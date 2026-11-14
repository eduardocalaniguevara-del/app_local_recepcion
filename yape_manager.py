#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor y Conciliador Mensual de Yapes (Google Drive + Celular -> SQLite `yape_conciliacion_hostal`)
Reemplazo directo en SQLite de la hoja `CONCILIACION PAGOS YAPE`.
Hotel El Óvalo Mariátegui — C:\\ovalo_mariategui_prueba\\yape_manager.py

REGLA ESTRICTA DE SEGURIDAD:
- ESTE MÓDULO ES 100% NO INTRUSIVO (SOLO LECTURA SOBRE LA WEBAPP LOCAL).
- JAMÁS edita, modifica ni autocompleta `estancias`, `pagos`, `ventas_tienda` ni los formularios
  de las chicas de recepción (`CheckInRapido`, `PagosPosteriores`, `VentaTienda`).
- Extrae con continuidad total los reportes `.xlsx` desde la carpeta de Google Drive
  (`1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe`), los guarda en `yape_conciliacion_hostal` en SQLite,
  ejecuta el motor multipasada de conciliación contra lo anotado por recepción, y expone la vista
  mensual tanto para Recepción (50% últimos sueltos / 50% recibidos diarios) como para Administrador
  (Cruzados/Enlazados, Sueltos del mes, Extraídos del Reporte de Transacciones y Hoja de Conciliación).
"""

from __future__ import annotations

import html as _html
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import (
    BRAZO_WSP_SCRIPT,
    DATA_DIR,
    EJECUTAR_REMOTO_SCRIPT,
    MODO_PRUEBA,
    obtener_ruta_credenciales,
)
from database import db_transaction, get_connection

# Misma carpeta de Google Drive donde llegan los reportes de Yape (usada por auditor_yape.py)
FOLDER_ID_YAPE_DRIVE = "1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe"
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]

CACHE_CORREO_MAELEN_PATH = DATA_DIR / "cache_correo_maelen_bcp.json"
ESTADO_BANCARIZACION_PATH = Path(
    r"c:\Users\josec\Downloads\trabajo\Sistema de agentes IA\Finanzas\facturas_auto\estado_bancarizacion.json"
)
CUENTAS_CORREO_MAELEN = ["eduardocalaniguevara@gmail.com", "hostal.ovalo@gmail.com"]
TEL_ALERTA_RECEPCION = "964139749"

_MESES_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "setiembre": 9,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_drive_sync_lock = threading.Lock()
_maelen_sync_lock = threading.Lock()
_ultimo_drive_check_ts: float = 0.0
_ultimo_maelen_check_ts: float = 0.0
_ultimo_archivo_drive_id: str = ""
_ultimo_archivo_drive_mtime: str = ""
_ultimo_archivo_drive_nombre: str = ""
_ULTIMO_PING_CELULAR_ISO: str = ""
_ultimo_maelen_resumen: Dict[str, Any] = {}


def _migrar_tabla_yape_antigua(conn) -> None:
    """
    Si la tabla yape_conciliacion_hostal existe con el esquema ANTIGUO (sin fecha_operacion),
    la renombra como respaldo (con todo su contenido) para que se cree la nueva sin perder datos.
    """
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(yape_conciliacion_hostal)").fetchall()}
    if cols and "fecha_operacion" not in cols:
        sufijo = datetime.now().strftime("%Y%m%d_%H%M%S")
        conn.execute(f"ALTER TABLE yape_conciliacion_hostal RENAME TO yape_conciliacion_hostal_antigua_{sufijo}")
        conn.commit()


def init_yape_tables() -> None:
    conn = get_connection()
    try:
        _migrar_tabla_yape_antigua(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS yape_conciliacion_hostal (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_operacion     TEXT NOT NULL DEFAULT '',
                hora_operacion      TEXT NOT NULL DEFAULT '',
                remitente_bcp       TEXT NOT NULL DEFAULT '',
                monto_bcp           REAL NOT NULL DEFAULT 0,
                nro_operacion       TEXT NOT NULL DEFAULT '',
                id_estancia_calzada TEXT DEFAULT '',
                hab_calzada         TEXT DEFAULT '',
                huesped_calzado     TEXT DEFAULT '',
                estado_auditoria    TEXT DEFAULT 'PENDIENTE',
                observacion         TEXT DEFAULT '',
                updated_at          TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_yape_fecha_estado
                ON yape_conciliacion_hostal (fecha_operacion, estado_auditoria);
            CREATE INDEX IF NOT EXISTS idx_yape_updated
                ON yape_conciliacion_hostal (id DESC);

            CREATE TABLE IF NOT EXISTS maelen_transferencias_correo (
                msg_id               TEXT PRIMARY KEY,
                cuenta_correo        TEXT DEFAULT '',
                fecha_operacion      TEXT NOT NULL DEFAULT '',
                hora_operacion       TEXT DEFAULT '',
                tipo_movimiento      TEXT DEFAULT '',
                remitente_o_destino  TEXT NOT NULL DEFAULT '',
                monto                REAL DEFAULT 0,
                nro_operacion        TEXT DEFAULT '',
                cuenta_origen_destino TEXT DEFAULT 'BCP CTA CTE 8023 MAELEN',
                asunto               TEXT DEFAULT '',
                estado_cruce         TEXT DEFAULT 'PENDIENTE',
                id_cruce_recepcion   TEXT DEFAULT '',
                detalle_cruce        TEXT DEFAULT '',
                created_at           TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_maelen_fecha
                ON maelen_transferencias_correo (fecha_operacion, tipo_movimiento);

            CREATE TABLE IF NOT EXISTS yape_alertas_wsp_5min (
                id_alerta             INTEGER PRIMARY KEY AUTOINCREMENT,
                id_yape               INTEGER UNIQUE,
                fecha_operacion       TEXT NOT NULL DEFAULT '',
                hora_operacion        TEXT NOT NULL DEFAULT '',
                remitente_bcp         TEXT NOT NULL DEFAULT '',
                monto_bcp             REAL DEFAULT 0,
                minutos_transcurridos REAL DEFAULT 0,
                mensaje_wsp           TEXT NOT NULL DEFAULT '',
                telefono_destino      TEXT DEFAULT '964139749',
                estado_envio          TEXT DEFAULT 'EN_COLA',
                id_cola_wsp           TEXT DEFAULT '',
                creado_en             TEXT DEFAULT '',
                enviado_en            TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_yape_wsp_5min_fecha
                ON yape_alertas_wsp_5min (fecha_operacion, estado_envio);

            CREATE TABLE IF NOT EXISTS yape_revisor_830_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_ejecucion   TEXT NOT NULL DEFAULT '',
                mes_evaluado      TEXT NOT NULL DEFAULT '',
                tipo_accion       TEXT NOT NULL DEFAULT '',
                id_pago           TEXT DEFAULT '',
                id_yape           INTEGER DEFAULT 0,
                habitacion        TEXT DEFAULT '',
                antes_json        TEXT DEFAULT '',
                despues_json      TEXT DEFAULT '',
                explicacion       TEXT DEFAULT '',
                estado_resolucion TEXT DEFAULT '',
                wsp_enviado       INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_yape_revisor_830_fecha
                ON yape_revisor_830_log (fecha_ejecucion DESC, id DESC);

            CREATE TABLE IF NOT EXISTS aprendizaje_compartido_hostal (
                id                TEXT PRIMARY KEY,
                fecha_hora        TEXT NOT NULL DEFAULT '',
                origen_equipo     TEXT NOT NULL DEFAULT 'PRUEBA_8082',
                actor             TEXT NOT NULL DEFAULT 'REVISOR_YAPE_830',
                categoria         TEXT NOT NULL DEFAULT 'YAPE',
                pregunta_o_evento TEXT NOT NULL DEFAULT '',
                patron_detectado  TEXT NOT NULL DEFAULT '',
                regla_aprendida   TEXT NOT NULL DEFAULT '',
                updated_at        TEXT DEFAULT ''
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _normalizar_fecha_dmy(fecha_raw: str) -> str:
    """Normaliza 'D/M/YYYY', 'DD/MM/YYYY' o 'YYYY-MM-DD' a 'DD/MM/YYYY'."""
    s = str(fecha_raw or "").strip()
    if not s:
        return ""
    s_date = s.split(" ")[0].strip()
    m_dmy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s_date)
    if m_dmy:
        return f"{int(m_dmy.group(1)):02d}/{int(m_dmy.group(2)):02d}/{m_dmy.group(3)}"
    m_ymd = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s_date)
    if m_ymd:
        return f"{int(m_ymd.group(3)):02d}/{int(m_ymd.group(2)):02d}/{m_ymd.group(1)}"
    return s_date


def _extraer_mes_mmyyyy(fecha_dmy: str) -> str:
    """Devuelve 'MM/YYYY' a partir de 'DD/MM/YYYY'."""
    f = _normalizar_fecha_dmy(fecha_dmy)
    p = f.split("/")
    if len(p) == 3 and len(p[2]) == 4:
        return f"{p[1]}/{p[2]}"
    return ""


def _dmy_a_ymd(fecha_dmy: str) -> str:
    f = _normalizar_fecha_dmy(fecha_dmy)
    p = f.split("/")
    if len(p) == 3:
        return f"{p[2]}-{p[1]}-{p[0]}"
    return ""


def _dmy_a_date(fecha_dmy: str) -> Optional[datetime]:
    f = _normalizar_fecha_dmy(fecha_dmy)
    try:
        return datetime.strptime(f, "%d/%m/%Y")
    except Exception:
        return None


def _hora_str_a_minutos(hora_str: str) -> Optional[int]:
    if not hora_str:
        return None
    s = str(hora_str).strip().lower()
    # Si empieza con un monto decimal como '7.50 8:58 pm' o '58.5 09:49', quitar el monto inicial antes de leer la hora
    s_sin_monto = re.sub(r"^[sS]?\/?\.?\s*\d+(?:[\.,]\d+)?\s+", "", s)
    for cand in (s_sin_monto, s):
        m = re.search(r"(?:^|\s)(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?|am|pm|aam|ppm)?", cand)
        if not m:
            m = re.search(r"(?:^|\s)(\d{1,2})\.(\d{2})\s*(a\.?\s*m\.?|p\.?\s*m\.?|am|pm|aam|ppm)", cand)
            if m:
                h, minute, ampm_raw = int(m.group(1)), int(m.group(2)), m.group(3) or ""
            else:
                continue
        else:
            h, minute, ampm_raw = int(m.group(1)), int(m.group(2)), m.group(4) or ""
        if h > 23 or minute > 59:
            continue
        ampm = re.sub(r"[^ap]", "", ampm_raw)
        if ampm:
            if "p" in ampm and h < 12:
                h += 12
            elif "a" in ampm and h == 12:
                h = 0
        return h * 60 + minute
    return None


def _hora_str_a_12h_corta(hora_str: str) -> str:
    mins = _hora_str_a_minutos(hora_str)
    if mins is None:
        return str(hora_str or "").strip()
    h = mins // 60
    m = mins % 60
    suf = "pm" if h >= 12 else "am"
    h12 = h - 12 if h > 12 else (12 if h == 0 else h)
    return f"{h12}:{m:02d}{suf}"


def _parse_monto_glosa(glosa: str) -> Optional[float]:
    if not glosa:
        return None
    parts = str(glosa).strip().split()
    if not parts:
        return None
    try:
        val = float(re.sub(r"^[Ss]/\.?", "", parts[0]).replace(",", "."))
        return round(val, 2) if val > 0 else None
    except Exception:
        return None


def _normalizar_glosa_grupo(glosa: str) -> str:
    """Normaliza una glosa como '130 20:18' o '130 8:18pm' a clave común para agrupar pagos compartidos."""
    if not glosa:
        return ""
    s = str(glosa).strip()
    if not s:
        return ""
    m_monto = _parse_monto_glosa(s)
    mins = _hora_str_a_minutos(s)
    if m_monto is not None and mins is not None:
        # Redondear minutos o usar hora en formato 12h sin distinción AM/PM para agrupar vouchers idénticos
        return f"M{m_monto:.2f}_T{mins % 720}"
    if mins is not None:
        return f"T{mins % 720}"
    return s.lower()


def _similitud_nombres(a: str, b: str) -> float:
    na = re.sub(r"[^A-Z0-9ÑÁÉÍÓÚ\s]", "", str(a or "").upper()).strip()
    nb = re.sub(r"[^A-Z0-9ÑÁÉÍÓÚ\s]", "", str(b or "").upper()).strip()
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.92
    ta = [t for t in na.split() if len(t) >= 2]
    tb = [t for t in nb.split() if len(t) >= 2]
    if ta and tb:
        # Comparar también prefijos (ej. 'HUA*' o 'HUA' en 'HUACAN', 'AGU*' en 'AGUILAR', 'MAM*' en 'MAMANI')
        matches_tok = 0
        for tok_a in ta:
            for tok_b in tb:
                if tok_a == tok_b:
                    matches_tok += 1.0
                    break
                if (len(tok_a) >= 3 and tok_b.startswith(tok_a[:3])) or (len(tok_b) >= 3 and tok_a.startswith(tok_b[:3])):
                    matches_tok += 0.85
                    break
        if matches_tok >= 1.8:
            return 0.90
        if matches_tok >= 1.0:
            return 0.72
        if matches_tok >= 0.8:
            return 0.58
    return SequenceMatcher(None, na, nb).ratio()


def _cargar_anotaciones_recepcion(conn) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Carga en memoria TODOS los pagos Yape/Plin/Transferencia Maelen, ventas de tienda Yape,
    vueltos y gastos Yape/BCP sin LIMIT por rowid para conciliación completa.
    """
    pagos_rows = conn.execute(
        """
        SELECT p.pago_id, p.estancia_uuid, p.id_estancia, p.fecha_iso, p.hab, p.huesped,
               p.monto, p.medio_pago, p.concepto, p.nro_operacion,
               COALESCE(e.empresa, '') AS empresa, COALESCE(e.ruc, '') AS ruc
        FROM pagos p
        LEFT JOIN estancias e ON e.estancia_uuid = p.estancia_uuid
        WHERE upper(p.medio_pago) LIKE '%YAPE%' OR upper(p.medio_pago) LIKE '%PLIN%'
           OR upper(p.medio_pago) LIKE '%TRANS%' OR upper(p.medio_pago) LIKE '%BCP%'
           OR upper(p.concepto) LIKE '%VUELTO%'
        """
    ).fetchall()

    pagos_chicas: List[Dict[str, Any]] = []
    vueltos_chicas: List[Dict[str, Any]] = []

    for r in pagos_rows:
        d = dict(r)
        f_iso = str(d.get("fecha_iso") or "").strip()
        f_dmy = _normalizar_fecha_dmy(f_iso.split(" ")[0] if " " in f_iso else f_iso[:10])
        hora_reg = f_iso.split(" ")[1].strip() if " " in f_iso else ""
        d["fecha_dmy"] = f_dmy
        d["hora_reg"] = hora_reg
        monto_val = round(float(d.get("monto") or 0), 2)
        concepto_up = str(d.get("concepto") or "").upper()
        medio_up = str(d.get("medio_pago") or "").upper()
        if monto_val < 0 or "VUELTO" in concepto_up:
            d["monto_abs"] = abs(monto_val)
            vueltos_chicas.append(d)
        elif monto_val > 0 and any(k in medio_up for k in ("YAPE", "PLIN", "TRANS", "BCP")):
            pagos_chicas.append(d)

    ventas_rows = conn.execute(
        """
        SELECT venta_uuid, fecha_dmy, fecha_iso, producto, cantidad, hab, personal, subtotal, metodo_pago, nro_op_yape
        FROM ventas_tienda
        WHERE upper(metodo_pago) LIKE '%YAPE%' OR upper(metodo_pago) LIKE '%PLIN%'
        """
    ).fetchall()
    ventas_chicas: List[Dict[str, Any]] = []
    for vr in ventas_rows:
        dv = dict(vr)
        f_iso_v = str(dv.get("fecha_iso") or "").strip()
        f_dmy_v = _normalizar_fecha_dmy(dv.get("fecha_dmy") or (f_iso_v.split(" ")[0] if " " in f_iso_v else f_iso_v[:10]))
        dv["fecha_dmy_norm"] = f_dmy_v
        dv["hora_reg"] = f_iso_v.split(" ")[1].strip() if " " in f_iso_v else ""
        if round(float(dv.get("subtotal") or 0), 2) > 0:
            ventas_chicas.append(dv)

    gastos_chicas: List[Dict[str, Any]] = []
    try:
        gastos_rows = conn.execute(
            """
            SELECT gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                   descripcion, monto, categoria, metodo_pago, responsable, personal
            FROM gastos
            WHERE upper(metodo_pago) LIKE '%YAPE%'
               OR upper(metodo_pago) LIKE '%TRANS%'
               OR upper(metodo_pago) LIKE '%BCP%'
               OR upper(descripcion) LIKE '%YAPE%'
            """
        ).fetchall()
        for gr in gastos_rows:
            dg = dict(gr)
            dg["id_gasto"] = dg.get("id_ts_str") or dg.get("gasto_uuid")
            dg["fecha_dmy_norm"] = _normalizar_fecha_dmy(dg.get("fecha_gasto_dmy") or dg.get("fecha_gasto_iso") or "")
            if round(float(dg.get("monto") or 0), 2) > 0:
                gastos_chicas.append(dg)
    except Exception:
        pass

    return pagos_chicas, ventas_chicas, vueltos_chicas, gastos_chicas


def comprobar_yapes_con_anotaciones_chicas(conn=None, recalcular_todo: bool = False) -> Dict[str, int]:
    """
    Motor de conciliación multipasada en SQLite (equivalente completo de `CONCILIACION PAGOS YAPE`):
    - Pasada 0: Salidas (`PAGASTE`) contra Vueltos a cliente y Gastos en Yape.
    - Pasada 1: Hora exacta/cercana de voucher o registro (+/- 15 min) + Monto exacto.
    - Pasada 1b: Pagos Grupales Compartidos (misma glosa/voucher o mismo huésped en el día, en habitaciones o tienda).
    - Pasada 2: Similitud de nombres (+/- 60 min).
    - Pasada 3: Error AM/PM (+/- 12 horas) o Registro tardío (+/- 1 día con hora de voucher o nombre coincidente).
    - Pasada 4: Monto único en el día con coherencia horaria/nombre.
    - Pasada 5: Diferencia / Excedente sobre lo anotado.
    """
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True

    comprobados_cnt = 0
    excedentes_cnt = 0
    try:
        if recalcular_todo:
            conn.execute(
                """
                UPDATE yape_conciliacion_hostal
                SET id_estancia_calzada = '',
                    hab_calzada = '',
                    huesped_calzado = '',
                    estado_auditoria = CASE WHEN remitente_bcp LIKE 'SALIDA%' THEN 'SALIDA' ELSE 'SIN ANOTAR' END
                WHERE estado_auditoria IN ('PENDIENTE', 'SIN ANOTAR', 'COMPROBADO', 'CALZADO', 'EXCEDENTE', 'SALIDA')
                """
            )

        pendientes = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                WHERE estado_auditoria IN ('PENDIENTE', 'SIN ANOTAR', 'SALIDA') AND monto_bcp > 0
                ORDER BY id ASC
                """
            ).fetchall()
        ]
        if not pendientes:
            return {"comprobados": 0, "excedentes": 0}

        ya_usados: set = set()
        for r in conn.execute(
            """
            SELECT id_estancia_calzada FROM yape_conciliacion_hostal
            WHERE id_estancia_calzada != '' AND estado_auditoria IN ('COMPROBADO', 'CALZADO', 'EXCEDENTE', 'SALIDA_CONCILIADA')
            """
        ).fetchall():
            for part_id in str(r["id_estancia_calzada"] or "").split("|"):
                if part_id.strip():
                    ya_usados.add(part_id.strip())

        pagos_chicas, ventas_chicas, vueltos_chicas, gastos_chicas = _cargar_anotaciones_recepcion(conn)
        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Unificar candidatos de ingresos (Habitaciones + Tienda) para soportar también pagos grupales mixtos
        candidatos_ingresos: List[Dict[str, Any]] = []
        for p in pagos_chicas:
            pid = str(p["pago_id"] or "").strip()
            if not pid:
                continue
            glosa = str(p.get("nro_operacion") or "").strip()
            f_iso = str(p.get("fecha_iso") or "").strip()
            min_p = _hora_str_a_minutos(glosa)
            if min_p is None:
                min_p = _hora_str_a_minutos(f_iso)
            candidatos_ingresos.append(
                {
                    "id": pid,
                    "origen": "Habitación",
                    "fecha_dmy": p["fecha_dmy"],
                    "fecha_dt": _dmy_a_date(p["fecha_dmy"]),
                    "minutos": min_p,
                    "monto": round(float(p.get("monto") or 0), 2),
                    "monto_nota": _parse_monto_glosa(glosa),
                    "hab": str(p.get("hab") or "S/H").strip() or "S/H",
                    "huesped": str(p.get("huesped") or "").strip().upper(),
                    "glosa": glosa,
                    "glosa_norm": _normalizar_glosa_grupo(glosa),
                    "concepto": str(p.get("concepto") or "Pago").strip(),
                }
            )

        for vt in ventas_chicas:
            vid = f"TIENDA-{vt['venta_uuid']}"
            glosa_v = str(vt.get("nro_op_yape") or "").strip()
            f_iso_v = str(vt.get("fecha_iso") or "").strip()
            min_v = _hora_str_a_minutos(glosa_v)
            if min_v is None:
                min_v = _hora_str_a_minutos(f_iso_v)
            candidatos_ingresos.append(
                {
                    "id": vid,
                    "origen": "Tienda",
                    "fecha_dmy": vt["fecha_dmy_norm"],
                    "fecha_dt": _dmy_a_date(vt["fecha_dmy_norm"]),
                    "minutos": min_v,
                    "monto": round(float(vt.get("subtotal") or 0), 2),
                    "monto_nota": _parse_monto_glosa(glosa_v),
                    "hab": str(vt.get("hab") or "TIENDA").strip() or "TIENDA",
                    "huesped": f"TIENDA ({vt.get('personal') or 'Recepción'}): {vt.get('cantidad')}x {vt.get('producto')}".upper(),
                    "glosa": glosa_v,
                    "glosa_norm": _normalizar_glosa_grupo(glosa_v),
                    "concepto": f"{vt.get('cantidad')}x {vt.get('producto')}",
                }
            )

        for y in pendientes:
            y_id = y["id"]
            y_fecha = _normalizar_fecha_dmy(y.get("fecha_operacion") or "")
            y_fecha_dt = _dmy_a_date(y_fecha)
            y_hora = str(y.get("hora_operacion") or "").strip()
            y_min = _hora_str_a_minutos(y_hora)
            y_monto = round(float(y.get("monto_bcp") or 0), 2)
            y_rem = str(y.get("remitente_bcp") or "").strip().upper()
            y_cod = str(y.get("nro_operacion") or "").strip()
            es_salida = str(y.get("estado_auditoria") or "").upper() == "SALIDA" or y_rem.startswith("SALIDA")

            # --- PASADA 0: SALIDAS (Vueltos y Gastos en Yape / Transferencia Maelen BCP) ---
            if es_salida:
                if str(y.get("id_estancia_calzada") or "").strip():
                    continue
                match_sal = None
                for v in vueltos_chicas:
                    vid = f"VUELTO-{v['pago_id']}"
                    if vid in ya_usados:
                        continue
                    if abs(float(v["monto_abs"]) - y_monto) <= 0.05 and v["fecha_dmy"] == y_fecha:
                        match_sal = {
                            "id_calzado": vid,
                            "hab": str(v.get("hab") or "S/H"),
                            "huesped": f"Vuelto: {v.get('huesped') or ''}",
                            "obs": f"✅ CUADRADO (VUELTO A CLIENTE JUSTIFICADO: Hab. {v.get('hab')} — S/ {y_monto:.2f})",
                        }
                        break
                if not match_sal:
                    mejor_gasto_score = -1.0
                    for g in gastos_chicas:
                        gid = f"GASTO-{g.get('gasto_uuid') or g.get('id_gasto')}"
                        if gid in ya_usados:
                            continue
                        if abs(float(g.get("monto") or 0) - y_monto) > 0.05:
                            continue
                        g_dt = _dmy_a_date(g.get("fecha_dmy_norm") or "")
                        diff_g_dias = abs((g_dt - y_fecha_dt).days) if (g_dt and y_fecha_dt) else 999
                        if diff_g_dias > 2:
                            continue
                        sim_g = _similitud_nombres(y_rem, str(g.get("descripcion") or ""))
                        # Coincidencia por fecha exacta o (+/- 2 días con similitud o monto alto único)
                        if diff_g_dias == 0 or sim_g >= 0.35 or y_monto >= 200:
                            sc_g = (100.0 - diff_g_dias * 20.0) + (sim_g * 30.0)
                            if sc_g > mejor_gasto_score:
                                mejor_gasto_score = sc_g
                                match_sal = {
                                    "id_calzado": gid,
                                    "hab": "GASTO",
                                    "huesped": f"Gasto ({g.get('fecha_dmy_norm')}): {g.get('descripcion') or ''}",
                                    "obs": f"✅ CUADRADO (GASTO HOSTAL: {g.get('descripcion') or ''} [{g.get('fecha_dmy_norm')}] — S/ {y_monto:.2f})",
                                }
                if match_sal:
                    ya_usados.add(match_sal["id_calzado"])
                    conn.execute(
                        """
                        UPDATE yape_conciliacion_hostal
                        SET id_estancia_calzada = ?, hab_calzada = ?, huesped_calzado = ?, observacion = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (match_sal["id_calzado"], match_sal["hab"], match_sal["huesped"], match_sal["obs"], ahora_iso, y_id),
                    )
                continue

            mejor_match = None
            mejor_score = -1.0

            # --- PASADA 1, 2 y 3: Cruce individual contra Habitaciones y Tienda ---
            for c in candidatos_ingresos:
                cid = c["id"]
                if cid in ya_usados:
                    continue

                mismo_dia = c["fecha_dmy"] == y_fecha
                diff_dias = abs((c["fecha_dt"] - y_fecha_dt).days) if (c["fecha_dt"] and y_fecha_dt) else 999
                if not mismo_dia and diff_dias > 1:
                    continue

                c_monto = c["monto"]
                c_min = c["minutos"]
                diff_m = abs(y_min - c_min) if (y_min is not None and c_min is not None) else 999
                diff_12h = abs(diff_m - 720) if diff_m != 999 else 999
                sim_nom = _similitud_nombres(y_rem, c["huesped"])

                # A) Monto exacto (o monto en nota de voucher exacto)
                es_monto_exacto = abs(c_monto - y_monto) <= 0.05
                if es_monto_exacto:
                    score = 0.0
                    etiqueta_cruce = "CUADRADO"

                    if mismo_dia:
                        if diff_m <= 5:
                            score = 95.0 + (sim_nom * 25.0)
                            if sim_nom < 0.30 and c["origen"] == "Habitación":
                                etiqueta_cruce = f"CUADRADO (HORA EXACTA VOUCHER / TERCERO: {y_rem})"
                        elif diff_m <= 18:
                            score = 84.0 + (sim_nom * 25.0)
                            if sim_nom < 0.30:
                                etiqueta_cruce = "CUADRADO (TIEMPO CERCANO VOUCHER)"
                        elif diff_12h <= 18:
                            # Error AM/PM clásico (ej. anotó 9:56pm en vez de 9:56am)
                            score = 82.0 + (sim_nom * 25.0)
                            etiqueta_cruce = "CUADRADO (DESFASE AM/PM CORREGIDO)"
                        elif sim_nom >= 0.45 and diff_m <= 90:
                            score = 78.0 + (sim_nom * 20.0)
                            etiqueta_cruce = "CUADRADO (SIMILITUD NOMBRE)"
                        elif sim_nom >= 0.55 and (diff_m <= 600 or diff_12h <= 60):
                            score = 70.0 + (sim_nom * 20.0)
                            etiqueta_cruce = "CUADRADO (MISMO HUÉSPED EN EL DÍA)"
                        elif y_cod and len(y_cod) >= 3 and y_cod in c["glosa"]:
                            score = 88.0 + (sim_nom * 15.0)
                    elif diff_dias == 1:
                        # Registro tardío (+/- 1 día) con voucher o nombre coincidente
                        if (diff_m <= 15 or diff_12h <= 15) and (sim_nom >= 0.40 or (c["monto_nota"] and abs(c["monto_nota"] - y_monto) <= 0.05)):
                            score = 74.0 + (sim_nom * 20.0)
                            etiqueta_cruce = f"CUADRADO (REGISTRO TARDÍO {c['fecha_dmy']})"

                    if score >= 65.0 and score > mejor_score:
                        mejor_score = score
                        pref = "🛒 Tienda" if c["origen"] == "Tienda" else f"🏨 Hab. {c['hab']}"
                        mejor_match = {
                            "id_calzado": cid,
                            "hab": c["hab"],
                            "huesped": c["huesped"],
                            "estado": "COMPROBADO",
                            "obs": f"✅ {etiqueta_cruce}: {pref} — {c['huesped']} (S/ {c_monto:.2f}, glosa: '{c['glosa'] or '-'}')",
                        }

            # --- PASADA 1B: Pagos Grupales Compartidos (misma glosa/hora o misma habitación o mismo huésped o ventas simultáneas en tienda) ---
            if not mejor_match:
                cands_dia = [c for c in candidatos_ingresos if c["id"] not in ya_usados and c["fecha_dmy"] == y_fecha]

                grupos_clave: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for c in cands_dia:
                    if c["glosa_norm"]:
                        grupos_clave[f"G_{c['glosa_norm']}"].append(c)
                    if c["minutos"] is not None:
                        grupos_clave[f"M_{c['minutos'] // 3}"].append(c)
                    if c["hab"] and c["hab"] not in ("S/H", "TIENDA"):
                        grupos_clave[f"HAB_{c['hab']}"].append(c)
                    if c["huesped"] and not c["huesped"].startswith("TIENDA"):
                        grupos_clave[f"H_{c['huesped']}"].append(c)

                for _, lista_g in grupos_clave.items():
                    vistos_g = []
                    ids_v = set()
                    for item_g in lista_g:
                        if item_g["id"] not in ids_v:
                            ids_v.add(item_g["id"])
                            vistos_g.append(item_g)
                    if len(vistos_g) < 2:
                        continue
                    suma_g = round(sum(x["monto"] for x in vistos_g), 2)
                    nota_m = vistos_g[0].get("monto_nota")
                    coincide_suma = abs(suma_g - y_monto) <= 0.10 or (nota_m is not None and abs(nota_m - y_monto) <= 0.10)
                    if not coincide_suma:
                        continue
                    min_diffs = [
                        min(abs(y_min - x["minutos"]), abs(abs(y_min - x["minutos"]) - 720))
                        for x in vistos_g
                        if y_min is not None and x["minutos"] is not None
                    ]
                    best_d = min(min_diffs) if min_diffs else 999
                    best_sim = max((_similitud_nombres(y_rem, x["huesped"]) for x in vistos_g), default=0.0)
                    es_glosa_maelen = bool(y_cod and any(y_cod.lower() == str(x.get("glosa") or "").strip().lower() for x in vistos_g if x.get("glosa")))
                    if best_d <= 35 or best_sim >= 0.38 or es_glosa_maelen:
                        ids_joined = "|".join(x["id"] for x in vistos_g)
                        habs_joined = ", ".join(sorted({x["hab"] for x in vistos_g if x["hab"]}))
                        huespedes_joined = " / ".join(sorted({x["huesped"] for x in vistos_g if x["huesped"]}))[:90]
                        glosas_joined = " / ".join(sorted({x["glosa"] for x in vistos_g if x["glosa"]}))
                        mejor_match = {
                            "id_calzado": ids_joined,
                            "hab": habs_joined or "GRUPAL",
                            "huesped": huespedes_joined,
                            "estado": "COMPROBADO",
                            "obs": f"✅ CUADRADO (PAGO GRUPAL COMPARTIDO {len(vistos_g)} ítems: {habs_joined} — S/ {suma_g:.2f}, glosa: '{glosas_joined or '-'}')",
                        }
                        break

            # --- PASADA 4: Monto Único en el Día con coherencia horaria/nombre ---
            if not mejor_match:
                cands_mismo_monto = [
                    c for c in candidatos_ingresos if c["id"] not in ya_usados and c["fecha_dmy"] == y_fecha and abs(c["monto"] - y_monto) <= 0.05
                ]
                if len(cands_mismo_monto) == 1:
                    c = cands_mismo_monto[0]
                    c_min = c["minutos"]
                    diff_m = abs(y_min - c_min) if (y_min is not None and c_min is not None) else 999
                    diff_12h = abs(diff_m - 720) if diff_m != 999 else 999
                    sim_nom = _similitud_nombres(y_rem, c["huesped"])
                    if sim_nom >= 0.32 or diff_m <= 180 or diff_12h <= 30:
                        pref = "🛒 Tienda" if c["origen"] == "Tienda" else f"🏨 Hab. {c['hab']}"
                        mejor_match = {
                            "id_calzado": c["id"],
                            "hab": c["hab"],
                            "huesped": c["huesped"],
                            "estado": "COMPROBADO",
                            "obs": f"✅ CUADRADO (MONTO ÚNICO DÍA): {pref} — {c['huesped']} (S/ {c['monto']:.2f}, glosa: '{c['glosa'] or '-'}')",
                        }

            # --- PASADA 5: Diferencia / Excedente (solo si no cuadró individual ni como pago grupal exacto) ---
            if not mejor_match:
                for c in candidatos_ingresos:
                    cid = c["id"]
                    if cid in ya_usados or c["fecha_dmy"] != y_fecha:
                        continue
                    c_monto = c["monto"]
                    c_min = c["minutos"]
                    diff_m = abs(y_min - c_min) if (y_min is not None and c_min is not None) else 999
                    diff_12h = abs(diff_m - 720) if diff_m != 999 else 999
                    sim_nom = _similitud_nombres(y_rem, c["huesped"])
                    if (diff_m <= 4 and sim_nom >= 0.50) or (sim_nom >= 0.78 and (diff_m <= 25 or diff_12h <= 15)):
                        diff_monto = round(y_monto - c_monto, 2)
                        score = 62.0 + (15.0 if (diff_m <= 4 or diff_12h <= 4) else 0.0) + (sim_nom * 15.0)
                        if score > mejor_score:
                            mejor_score = score
                            signo = f"+S/ {diff_monto:.2f} EXCEDENTE" if diff_monto > 0 else f"-S/ {abs(diff_monto):.2f} MENOS"
                            mejor_match = {
                                "id_calzado": cid,
                                "hab": c["hab"],
                                "huesped": c["huesped"],
                                "estado": "EXCEDENTE",
                                "obs": f"⚠️ DIFERENCIA ({signo}): Banco S/ {y_monto:.2f} vs Anotado S/ {c_monto:.2f} en Hab. {c['hab']} ({c['huesped']})",
                            }

            if mejor_match:
                for part_id in str(mejor_match["id_calzado"]).split("|"):
                    if part_id.strip():
                        ya_usados.add(part_id.strip())
                conn.execute(
                    """
                    UPDATE yape_conciliacion_hostal
                    SET id_estancia_calzada = ?,
                        hab_calzada = ?,
                        huesped_calzado = ?,
                        estado_auditoria = ?,
                        observacion = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mejor_match["id_calzado"],
                        mejor_match["hab"],
                        mejor_match["huesped"],
                        mejor_match["estado"],
                        mejor_match["obs"],
                        ahora_iso,
                        y_id,
                    ),
                )
                if mejor_match["estado"] == "EXCEDENTE":
                    excedentes_cnt += 1
                else:
                    comprobados_cnt += 1
            else:
                # Diagnóstico de solo lectura: ver si hay una habitación en estancias registrada a esa misma hora y tarifa sin pago Yape anotado
                est_sug = conn.execute(
                    """
                    SELECT hab, huesped, hora_registro, tarifa
                    FROM estancias
                    WHERE fecha_in_dmy = ?
                      AND estado_estancia != 'Cancelada'
                      AND (pago_ini_yape + pago_post_yape) = 0
                      AND ABS(tarifa - ?) <= 0.5
                    """,
                    (y_fecha, y_monto),
                ).fetchall()
                sug_txt = ""
                for er in est_sug:
                    e_min = _hora_str_a_minutos(str(er["hora_registro"] or ""))
                    if y_min is not None and e_min is not None and (abs(y_min - e_min) <= 35 or abs(abs(y_min - e_min) - 720) <= 25):
                        sug_txt = f" | 💡 Coincide en hora/monto con Hab. {er['hab']} ({er['huesped']}, hora {er['hora_registro']}, tarifa S/ {float(er['tarifa'] or 0):.2f} anotada en Efectivo)"
                        break
                obs_base = str(y.get("observacion") or "").split(" | 💡")[0].strip()
                if not obs_base or obs_base.startswith("✅") or obs_base.startswith("⚠️"):
                    obs_base = "Extraído de Drive (ReporteTransacciones+51964139749.xlsx)"
                nueva_obs = obs_base + sug_txt if sug_txt else obs_base
                conn.execute(
                    "UPDATE yape_conciliacion_hostal SET estado_auditoria = 'SIN ANOTAR', observacion = ?, updated_at = ? WHERE id = ?",
                    (nueva_obs, ahora_iso, y_id),
                )

        if own_conn:
            conn.commit()

        return {"comprobados": comprobados_cnt, "excedentes": excedentes_cnt}
    finally:
        if own_conn:
            conn.close()


def sincronizar_yapes_desde_drive_a_sqlite(forzar: bool = False) -> Dict[str, Any]:
    """
    Lee automáticamente la carpeta de Google Drive (`1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe`)
    donde llegan los reportes de Yape (`ReporteTransacciones+51964139749.xlsx`).
    - Garantiza CONTINUIDAD MENSUAL COMPLETA (ya sea que el reporte traiga 7 días o 30 días hacia atrás):
      si detecta que meses anteriores tienen pocos registros o cuando `forzar=True`, consolida
      múltiples reportes históricos de la carpeta sin romper la continuidad ni duplicar filas.
    """
    global _ultimo_drive_check_ts, _ultimo_archivo_drive_id, _ultimo_archivo_drive_mtime, _ultimo_archivo_drive_nombre

    ahora_ts = time.time()
    if not forzar and (ahora_ts - _ultimo_drive_check_ts) < 25.0:
        return {
            "ok": True,
            "desdeCache": True,
            "archivoDrive": _ultimo_archivo_drive_nombre,
        }

    if not _drive_sync_lock.acquire(blocking=False):
        return {"ok": True, "ocupado": True, "archivoDrive": _ultimo_archivo_drive_nombre}

    try:
        _ultimo_drive_check_ts = ahora_ts
        init_yape_tables()

        cred_path = obtener_ruta_credenciales()
        if not os.path.exists(cred_path):
            return {"ok": False, "error": f"No se encontró credentials.json en {cred_path}"}

        creds = Credentials.from_service_account_file(cred_path, scopes=SCOPES_DRIVE)
        drive_service = build("drive", "v3", credentials=creds)

        query = f"'{FOLDER_ID_YAPE_DRIVE}' in parents and trashed=false and name contains '.xlsx'"
        results = (
            drive_service.files()
            .list(
                q=query,
                orderBy="modifiedTime desc",
                fields="files(id, name, createdTime, modifiedTime)",
                pageSize=40,
            )
            .execute()
        )
        items = results.get("files", [])
        if not items:
            return {"ok": True, "sinArchivosEnDrive": True, "nuevosYapes": 0}

        # Verificar si ya tenemos continuidad histórica de Octubre/Noviembre en SQLite
        conn_chk = get_connection()
        try:
            oct_cnt = conn_chk.execute(
                "SELECT COUNT(*) AS c FROM yape_conciliacion_hostal WHERE fecha_operacion LIKE '%/10/2026'"
            ).fetchone()["c"]
        finally:
            conn_chk.close()

        necesita_backfill_continuidad = oct_cnt < 40

        top_file = items[0]
        if (
            not forzar
            and not necesita_backfill_continuidad
            and top_file["id"] == _ultimo_archivo_drive_id
            and top_file.get("modifiedTime") == _ultimo_archivo_drive_mtime
        ):
            res_comp = comprobar_yapes_con_anotaciones_chicas()
            return {
                "ok": True,
                "archivoDrive": _ultimo_archivo_drive_nombre,
                "sinCambiosDrive": True,
                "comprobacion": res_comp,
            }

        if not creds.valid or not creds.token:
            creds.refresh(Request())

        import pandas as pd

        # Si necesita reconstruir continuidad mensual (ej. reportes de 7 días y 15 días hacia atrás),
        # tomamos una muestra escalonada de archivos de Drive (el más reciente + puntos intermedios hasta el más antiguo)
        if necesita_backfill_continuidad or forzar:
            indices_a_leer = sorted({0, 1, 3, 7, 14, 22, 30, len(items) - 1})
            archivos_a_procesar = [items[i] for i in indices_a_leer if 0 <= i < len(items)]
        else:
            archivos_a_procesar = items[:2]

        dfs_consolidados = []
        mejor_archivo = None
        mejor_fecha_max = None

        for item in archivos_a_procesar:
            try:
                dl_url = f"https://www.googleapis.com/drive/v3/files/{item['id']}?alt=media"
                resp = requests.get(dl_url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=20)
                if resp.status_code == 200:
                    fh_temp = io.BytesIO(resp.content)
                    df_temp = pd.read_excel(fh_temp, skiprows=4)
                    if len(df_temp.columns) >= 6:
                        cols = df_temp.columns.tolist()
                        df_b = df_temp.rename(
                            columns={
                                cols[0]: "Tipo",
                                cols[1]: "Origen",
                                cols[2]: "Destino",
                                cols[3]: "Monto",
                                cols[4]: "Mensaje",
                                cols[5]: "Fecha",
                            }
                        )
                        df_b["Fecha_dt"] = pd.to_datetime(df_b["Fecha"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
                        df_b = df_b.dropna(subset=["Fecha_dt"])
                        df_b = df_b[df_b["Tipo"].astype(str).str.contains("PAG", na=False, case=False)]
                        if len(df_b) > 0:
                            max_f = df_b["Fecha_dt"].max()
                            if mejor_fecha_max is None or max_f > mejor_fecha_max:
                                mejor_fecha_max = max_f
                                mejor_archivo = item
                            df_b["_archivo_nombre"] = item["name"]
                            dfs_consolidados.append(df_b)
            except Exception:
                continue

        if not dfs_consolidados or mejor_archivo is None:
            return {"ok": False, "error": "No se pudo leer el Excel de Yape desde Google Drive."}

        _ultimo_archivo_drive_id = top_file["id"]
        _ultimo_archivo_drive_mtime = top_file.get("modifiedTime") or ""
        _ultimo_archivo_drive_nombre = mejor_archivo["name"]

        # Unir todos los reportes leídos sin límite artificial de 15 días para mantener continuidad total
        df_banco = pd.concat(dfs_consolidados, ignore_index=True)
        df_banco = df_banco.sort_values("Fecha_dt").reset_index(drop=True)

        insertados = 0
        corroborados = 0
        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ultima_fecha_excel_str = mejor_fecha_max.strftime("%d/%m/%Y %H:%M") if mejor_fecha_max is not None else ""

        with db_transaction() as conn:
            if ultima_fecha_excel_str:
                conn.execute(
                    "INSERT INTO estado_sistema (clave, valor) VALUES ('yape_ultimo_reporte_drive', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (f"{mejor_archivo['name']} (hasta {ultima_fecha_excel_str})",),
                )

            existentes = set()
            filas_celular_pendientes = []
            for r in conn.execute(
                "SELECT id, fecha_operacion, hora_operacion, remitente_bcp, ROUND(monto_bcp, 2) AS m, observacion FROM yape_conciliacion_hostal"
            ).fetchall():
                d_r = dict(r)
                existentes.add((_normalizar_fecha_dmy(d_r["fecha_operacion"]), d_r["hora_operacion"], d_r["remitente_bcp"], float(d_r["m"] or 0)))
                if "Notificación celular" in str(d_r.get("observacion") or "") and "Corroborado con Drive" not in str(d_r.get("observacion") or ""):
                    filas_celular_pendientes.append(d_r)

            ids_celular_corroborados = set()

            for _, row in df_banco.iterrows():
                tipo_str = str(row["Tipo"] or "").upper().strip()
                es_salida = "PAGASTE" in tipo_str
                dt_val: datetime = row["Fecha_dt"].to_pydatetime()
                fecha_op = dt_val.strftime("%d/%m/%Y")
                hora_op = dt_val.strftime("%H:%M:%S")

                try:
                    monto_val = round(float(str(row["Monto"]).replace("S/", "").replace(",", ".").strip()), 2)
                except Exception:
                    continue
                if monto_val <= 0:
                    continue

                msg_str = str(row["Mensaje"]).strip() if pd.notna(row["Mensaje"]) and str(row["Mensaje"]).strip() else ""
                if es_salida:
                    dest_str = str(row["Destino"]).strip() if pd.notna(row["Destino"]) else "DESTINO"
                    remitente = f"SALIDA: {dest_str}".upper()
                    estado_aud = "SALIDA"
                else:
                    remitente = (str(row["Origen"]).strip() if pd.notna(row["Origen"]) else "CLIENTE YAPE").upper()
                    estado_aud = "SIN ANOTAR"

                clave = (fecha_op, hora_op, remitente, monto_val)
                if clave in existentes:
                    continue

                # Verificar si este movimiento del Excel corrobora un Yape que ya había entrado en vivo por el celular
                match_cel = None
                if not es_salida:
                    min_excel = _hora_str_a_minutos(hora_op)
                    for fc in filas_celular_pendientes:
                        if fc["id"] in ids_celular_corroborados:
                            continue
                        if _normalizar_fecha_dmy(str(fc["fecha_operacion"] or "")) != fecha_op:
                            continue
                        if abs(float(fc["m"] or 0) - monto_val) > 0.01:
                            continue
                        min_cel = _hora_str_a_minutos(str(fc["hora_operacion"] or ""))
                        diff_min = abs(min_excel - min_cel) if (min_excel is not None and min_cel is not None) else 999
                        sim_rem = _similitud_nombres(remitente, str(fc["remitente_bcp"] or ""))
                        if diff_min <= 10 and (sim_rem >= 0.45 or "CLIENTE YAPE" in str(fc["remitente_bcp"] or "")):
                            match_cel = fc
                            break

                if match_cel is not None:
                    ids_celular_corroborados.add(match_cel["id"])
                    existentes.add(clave)
                    obs_prev = str(match_cel.get("observacion") or "")
                    nueva_obs = obs_prev + " | ✔️ Corroborado con Drive" if "Corroborado con Drive" not in obs_prev else obs_prev
                    conn.execute(
                        """
                        UPDATE yape_conciliacion_hostal
                        SET hora_operacion = ?, remitente_bcp = ?, observacion = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (hora_op, remitente, nueva_obs, ahora_iso, match_cel["id"]),
                    )
                    corroborados += 1
                    continue

                existentes.add(clave)

                nro_op = msg_str if msg_str else f"{monto_val:g} {_hora_str_a_12h_corta(hora_op)}"
                arch_nom = str(row.get("_archivo_nombre") or mejor_archivo["name"])
                obs = f"Extraído de Drive ({arch_nom})"

                conn.execute(
                    """
                    INSERT INTO yape_conciliacion_hostal (
                        fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                        nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                        estado_auditoria, observacion, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', '', '', ?, ?, ?)
                    """,
                    (fecha_op, hora_op, remitente, monto_val, nro_op, estado_aud, obs, ahora_iso),
                )
                if not es_salida:
                    insertados += 1

            res_comp = comprobar_yapes_con_anotaciones_chicas(conn, recalcular_todo=bool(forzar or necesita_backfill_continuidad or insertados > 0))

        return {
            "ok": True,
            "archivoDrive": mejor_archivo["name"],
            "ultimaFechaDrive": ultima_fecha_excel_str,
            "nuevosYapes": insertados,
            "corroboradosCelular": corroborados,
            "comprobacion": res_comp,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        _drive_sync_lock.release()


def _limpiar_html_correo(raw_txt: str) -> str:
    txt = str(raw_txt or "")
    txt = re.sub(r"(?is)<style.*?</style>", " ", txt)
    txt = re.sub(r"(?is)<script.*?</script>", " ", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    txt = txt.replace("\u200b", "").replace("\xa0", " ").replace("*", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _parsear_fecha_hora_bcp_es(texto: str) -> Tuple[str, str]:
    """Convierte '09 de noviembre de 2026 - 09:00 PM' o 'Lunes, 02 Noviembre 2026 - 10:52 A. M.' -> ('09/11/2026', '21:00:00')."""
    s = str(texto or "")
    m = re.search(
        r"(\d{1,2})\s+(?:de\s+)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?(\d{4})"
        r"(?:\s*[-–]\s*(\d{1,2}):(\d{2})\s*([AP]\.?\s*M\.?))?",
        s,
        re.IGNORECASE,
    )
    if not m:
        m_num = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}):(\d{2})", s)
        if m_num:
            return m_num.group(1), f"{int(m_num.group(2)):02d}:{int(m_num.group(3)):02d}:00"
        return "", ""

    dia = int(m.group(1))
    mes = _MESES_ES.get(m.group(2).lower(), 1)
    anio = int(m.group(3))
    fecha_dmy = f"{dia:02d}/{mes:02d}/{anio}"

    hora_op = "00:00:00"
    if m.group(4) and m.group(5):
        hh = int(m.group(4))
        mm = int(m.group(5))
        ampm = re.sub(r"[^ap]", "", (m.group(6) or "").lower())
        if ampm == "p" and hh < 12:
            hh += 12
        elif ampm == "a" and hh == 12:
            hh = 0
        hora_op = f"{hh:02d}:{mm:02d}:00"

    return fecha_dmy, hora_op


def _parsear_correo_maelen_bcp(msg_id: str, raw_txt: str, cuenta_correo: str = "") -> Optional[Dict[str, Any]]:
    """Extrae movimientos BCP de la Cuenta Corriente Maelen Hogar (**** 8023) o recepciones de Yapeo a celular."""
    limpio = _limpiar_html_correo(raw_txt)
    if not limpio:
        return None

    m_subj = re.search(r"Subject:\s*(.+?)\s+(?:To:|Cc:|---)", limpio, re.IGNORECASE)
    asunto = m_subj.group(1).strip() if m_subj else "Notificación BCP Maelen"
    up = limpio.upper()

    es_maelen = ("8023" in up) or ("MAELEN HOGAR" in up)
    if not es_maelen:
        return None

    tipo_mov = ""
    if "RECIBISTE UN YAPEO" in up or "RECEPCIÓN DE YAPEO" in up or "RECEPCION DE YAPEO" in up:
        tipo_mov = "INGRESO_YAPEO_BCP"
    elif "RECIBISTE UNA TRANSFERENCIA" in up or "ABONO EN TU CUENTA" in up:
        tipo_mov = "INGRESO_TRANSFERENCIA_BCP"
    elif "TRANSFERENCIA A TERCEROS" in up or "REALIZASTE UNA TRANSFERENCIA" in up:
        tipo_mov = "SALIDA_TRANSFERENCIA_BCP"
    elif "YAPEO A CELULAR" in up or "YAPEAR A CELULAR" in up or "REALIZASTE UN YAPEO" in up or "YAPEASTE" in up:
        tipo_mov = "SALIDA_YAPEO_BCP"
    elif "PAGO DE SERVICIO" in up or "PAGO DE SERVICIOS" in up:
        tipo_mov = "PAGO_SERVICIO_BCP"
    else:
        return None

    monto = 0.0
    for pat in (
        r"Recibiste un yapeo de\s+S/\s*([\d,]+\.\d{2})",
        r"Monto recibido\s+S/\s*([\d,]+\.\d{2})",
        r"Realizaste un yapeo a celular de\s+S/\s*([\d,]+\.\d{2})",
        r"Monto enviado\s+S/\s*([\d,]+\.\d{2})",
        r"Realizaste una transferencia de\s+S/\s*([\d,]+\.\d{2})",
        r"Monto transferido\s+S/\s*([\d,]+\.\d{2})",
        r"Monto total:?\s*S/\s*([\d,]+\.\d{2})",
        r"S/\s*([\d,]+\.\d{2})",
    ):
        mm = re.search(pat, limpio, re.IGNORECASE)
        if mm:
            try:
                monto = round(float(mm.group(1).replace(",", "")), 2)
                if monto > 0:
                    break
            except Exception:
                pass
    if monto <= 0:
        return None

    fecha_dmy, hora_op = _parsear_fecha_hora_bcp_es(limpio)
    if not fecha_dmy:
        return None

    remitente = ""
    if tipo_mov == "INGRESO_YAPEO_BCP":
        m_rem = re.search(r"Enviado por\s+(.+?)\s+(?:¿No reconoces|Número de operación|Destino|Mensaje|$)", limpio, re.IGNORECASE)
        if not m_rem:
            m_rem = re.search(r"Recibiste un yapeo de\s+S/\s*[\d,]+\.\d{2}\s+de\s+([^\.]+)\.", limpio, re.IGNORECASE)
        remitente = (m_rem.group(1).strip() if m_rem else "CLIENTE YAPEO BCP")
    elif tipo_mov == "PAGO_SERVICIO_BCP":
        m_emp = re.search(r"Empresa:\s*(.+?)\s+Servicio:", limpio, re.IGNORECASE)
        remitente = (m_emp.group(1).strip() if m_emp else "PAGO SERVICIO BCP")
    else:
        m_des = re.search(
            r"Enviado a\s+(.+?)\s+(?:\*{2,}|Destino|Moneda|Desde|Mensaje|Canal|Número de operación|$)",
            limpio,
            re.IGNORECASE,
        )
        remitente = (m_des.group(1).strip() if m_des else "TERCERO BCP")

    remitente = re.sub(r"\*{2,}.*$", "", remitente).strip(" .:-").upper() or "MOVIMIENTO MAELEN BCP"

    m_op = re.search(r"N[úu]mero de operaci[óo]n:?\s*(\d{5,12})", limpio, re.IGNORECASE)
    nro_op = m_op.group(1).strip() if m_op else msg_id[-8:]

    m_msg = re.search(r"Mensaje\s+(.*?)\s+Canal", limpio, re.IGNORECASE)
    msg_nota = m_msg.group(1).strip() if m_msg else ""
    if msg_nota and msg_nota not in remitente and tipo_mov != "INGRESO_YAPEO_BCP":
        nro_op_full = f"{nro_op} ({msg_nota})"
    else:
        nro_op_full = nro_op

    return {
        "msg_id": msg_id,
        "cuenta_correo": cuenta_correo or "eduardocalaniguevara@gmail.com",
        "fecha_operacion": fecha_dmy,
        "hora_operacion": hora_op,
        "tipo_movimiento": tipo_mov,
        "remitente_o_destino": remitente,
        "monto": monto,
        "nro_operacion": nro_op_full,
        "cuenta_origen_destino": "BCP CTA CTE 8023 MAELEN",
        "asunto": asunto[:160],
    }


def _cargar_cache_correo_maelen() -> Dict[str, Any]:
    if CACHE_CORREO_MAELEN_PATH.exists():
        try:
            data = json.loads(CACHE_CORREO_MAELEN_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault("mensajes", {})
                return data
        except Exception:
            pass
    return {"mensajes": {}, "ultima_sync": ""}


def _guardar_cache_correo_maelen(cache: Dict[str, Any]) -> None:
    try:
        CACHE_CORREO_MAELEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache["ultima_sync"] = datetime.now().isoformat(timespec="seconds")
        CACHE_CORREO_MAELEN_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _ejecutar_gmail_cli(args: List[str], cuenta: str) -> Tuple[int, str, str]:
    env = os.environ.copy()
    if cuenta:
        env["AGY_ACCOUNT"] = cuenta
    cands = [
        shutil.which("gmail"),
        os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\bin\gmail.cmd"),
    ]
    for bin_path in cands:
        if not bin_path:
            continue
        try:
            proc = subprocess.run(
                [bin_path] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                env=env,
                timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            if proc.returncode == 0:
                return 0, proc.stdout or "", proc.stderr or ""
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except Exception:
            continue
    return 1, "", "gmail CLI no disponible"


def sincronizar_transferencias_maelen_correo(forzar: bool = False) -> Dict[str, Any]:
    """
    Extrae movimientos bancarios de la Cuenta Empresa Maelen Hogar (**** 8023) desde:
      1) Semilla cacheada `estado_bancarizacion.json` + caché local `cache_correo_maelen_bcp.json`.
      2) Consulta incremental segura vía `gmail` CLI (`from:notificacionesbcp.com.pe` en eduardocalaniguevara@gmail.com).
      3) Detección de Transferencias Corporativas/Grupales a Maelen (p.ej. QUISAPATA '3705 11:28', '233 15:22', '1175 08:25').
    Sincroniza ingresos/salidas faltantes hacia `maelen_transferencias_correo` y `yape_conciliacion_hostal`.
    """
    global _ultimo_maelen_check_ts, _ultimo_maelen_resumen

    ahora_ts = time.time()
    if not forzar and (ahora_ts - _ultimo_maelen_check_ts) < 120.0 and _ultimo_maelen_resumen:
        res_cached = dict(_ultimo_maelen_resumen)
        res_cached["desdeCache"] = True
        return res_cached

    if not _maelen_sync_lock.acquire(blocking=False):
        return dict(_ultimo_maelen_resumen or {"ok": True, "ocupado": True})

    try:
        _ultimo_maelen_check_ts = ahora_ts
        init_yape_tables()
        cache = _cargar_cache_correo_maelen()
        msgs_cache: Dict[str, Any] = cache.setdefault("mensajes", {})

        nuevos_leidos_gmail = 0
        error_gmail = ""

        # 1) Importar base ya extraída desde estado_bancarizacion.json sin tocar cuota Gmail
        if ESTADO_BANCARIZACION_PATH.exists():
            try:
                est_b = json.loads(ESTADO_BANCARIZACION_PATH.read_text(encoding="utf-8-sig"))
                for mid, info in (est_b.get("transferencias") or {}).items():
                    if mid not in msgs_cache:
                        raw_txt = str(info.get("texto_completo") or "")
                        parsed = _parsear_correo_maelen_bcp(mid, raw_txt, "eduardocalaniguevara@gmail.com")
                        if parsed:
                            msgs_cache[mid] = parsed
            except Exception:
                pass

        # 2) Búsqueda incremental liviana en Gmail (eduardocalaniguevara@gmail.com)
        rc_s, out_s, err_s = _ejecutar_gmail_cli(
            ["readonly", "search", "from:notificacionesbcp.com.pe", "--max", "30", "--json"],
            "eduardocalaniguevara@gmail.com",
        )
        if rc_s == 0 and out_s.strip():
            try:
                lista_ids = json.loads(out_s)
            except Exception:
                lista_ids = []
            pendientes_get = [
                str(it.get("id") or "")
                for it in (lista_ids if isinstance(lista_ids, list) else [])
                if it.get("id") and str(it.get("id")) not in msgs_cache
            ]
            # Descargar máximo 12 mensajes nuevos por ciclo para evitar rateLimitExceeded
            for mid in pendientes_get[:12]:
                rc_g, out_g, err_g = _ejecutar_gmail_cli(
                    ["readonly", "get", mid],
                    "eduardocalaniguevara@gmail.com",
                )
                if rc_g == 0 and out_g.strip():
                    parsed = _parsear_correo_maelen_bcp(mid, out_g, "eduardocalaniguevara@gmail.com")
                    if parsed:
                        msgs_cache[mid] = parsed
                    else:
                        msgs_cache[mid] = {"msg_id": mid, "ignorado": True}
                    nuevos_leidos_gmail += 1
                elif "rateLimitExceeded" in (err_g or out_g):
                    error_gmail = "rateLimitExceeded"
                    break
        elif err_s:
            error_gmail = err_s.strip()[:160]

        _guardar_cache_correo_maelen(cache)

        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insertados_maelen = 0
        insertados_yape_vivo = 0
        corroborados_yape = 0
        corp_detectados = 0

        with db_transaction() as conn:
            # 3) Guardar movimientos parseados en `maelen_transferencias_correo`
            for mid, mov in msgs_cache.items():
                if not isinstance(mov, dict) or mov.get("ignorado") or not mov.get("fecha_operacion"):
                    continue
                ya_m = conn.execute(
                    "SELECT msg_id FROM maelen_transferencias_correo WHERE msg_id = ?",
                    (mid,),
                ).fetchone()
                if not ya_m:
                    conn.execute(
                        """
                        INSERT INTO maelen_transferencias_correo (
                            msg_id, cuenta_correo, fecha_operacion, hora_operacion, tipo_movimiento,
                            remitente_o_destino, monto, nro_operacion, cuenta_origen_destino,
                            asunto, estado_cruce, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', ?)
                        """,
                        (
                            mid,
                            mov.get("cuenta_correo", "eduardocalaniguevara@gmail.com"),
                            mov["fecha_operacion"],
                            mov.get("hora_operacion", "00:00:00"),
                            mov["tipo_movimiento"],
                            mov["remitente_o_destino"],
                            float(mov["monto"]),
                            mov.get("nro_operacion", ""),
                            mov.get("cuenta_origen_destino", "BCP CTA CTE 8023 MAELEN"),
                            mov.get("asunto", ""),
                            ahora_iso,
                        ),
                    )
                    insertados_maelen += 1

            # 4) Detectar transferencias corporativas / grupales a Cuenta Maelen **** 8023 (ej. QUISAPATA)
            pagos_chicas, _, _, gastos_chicas = _cargar_anotaciones_recepcion(conn)
            grupos_corp: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
            for p in pagos_chicas:
                gl = str(p.get("nro_operacion") or "").strip()
                gl_n = _normalizar_glosa_grupo(gl)
                f_dmy = str(p.get("fecha_dmy") or "").strip()
                if f_dmy and gl_n and gl:
                    grupos_corp[(f_dmy, gl_n)].append(p)

            filas_yape_actuales = [
                dict(r)
                for r in conn.execute("SELECT id, fecha_operacion, hora_operacion, remitente_bcp, ROUND(monto_bcp, 2) AS m, nro_operacion, observacion FROM yape_conciliacion_hostal").fetchall()
            ]

            for (f_dmy, gl_n), lista_p in grupos_corp.items():
                if len(lista_p) < 2:
                    continue
                suma_g = round(sum(float(x.get("monto") or 0) for x in lista_p), 2)
                gl_orig = str(lista_p[0].get("nro_operacion") or "").strip()
                m_nota = _parse_monto_glosa(gl_orig)
                if m_nota is None or abs(m_nota - suma_g) > 0.10:
                    continue
                conceptos_txt = " ".join(str(x.get("concepto") or "") for x in lista_p).upper()
                empresas_txt = " ".join(str(x.get("empresa") or "") for x in lista_p).upper()
                es_corp = (
                    len(lista_p) >= 3
                    or "EMPRESA" in conceptos_txt
                    or "QUISAPATA" in conceptos_txt
                    or "LIQUIDACION" in conceptos_txt
                    or bool(empresas_txt.strip())
                    or suma_g >= 200.0
                )
                if not es_corp:
                    continue

                # Verificar si ya existe un Yape en banco ese día por el mismo monto (para no duplicar pagos grupales normales de Yape app)
                ya_en_yape = any(
                    _normalizar_fecha_dmy(fy["fecha_operacion"]) == f_dmy
                    and abs(float(fy["m"] or 0) - suma_g) <= 0.10
                    and "TRANSFERENCIA CTA MAELEN" not in str(fy["remitente_bcp"] or "").upper()
                    for fy in filas_yape_actuales
                )
                if ya_en_yape:
                    continue

                # Extraer etiqueta de empresa si figura en concepto
                m_emp_c = re.search(r"EMPRESA\s+([A-Z0-9ÑÁÉÍÓÚ\s]{3,35})", conceptos_txt)
                etiqueta_emp = (m_emp_c.group(1).strip() if m_emp_c else "") or empresas_txt.strip() or f"GRUPO {len(lista_p)} HABS ({gl_orig})"
                etiqueta_emp = re.sub(r"\s+", " ", etiqueta_emp).strip()[:45]
                hora_m = _hora_str_a_minutos(gl_orig)
                hora_op_c = f"{hora_m // 60:02d}:{hora_m % 60:02d}:00" if hora_m is not None else "12:00:00"
                msg_id_c = f"CORP-{_dmy_a_ymd(f_dmy)}-{re.sub(r'[^A-Za-z0-9_]', '', gl_n)}"

                if not conn.execute("SELECT 1 FROM maelen_transferencias_correo WHERE msg_id = ?", (msg_id_c,)).fetchone():
                    conn.execute(
                        """
                        INSERT INTO maelen_transferencias_correo (
                            msg_id, cuenta_correo, fecha_operacion, hora_operacion, tipo_movimiento,
                            remitente_o_destino, monto, nro_operacion, cuenta_origen_destino,
                            asunto, estado_cruce, id_cruce_recepcion, detalle_cruce, created_at
                        ) VALUES (?, 'SISTEMA_PAGOS', ?, ?, 'CORPORATIVO_MAELEN', ?, ?, ?, 'BCP CTA CTE 8023 MAELEN', ?, 'CRUZADO', ?, ?, ?)
                        """,
                        (
                            msg_id_c,
                            f_dmy,
                            hora_op_c,
                            f"TRANSFERENCIA EMPRESA: {etiqueta_emp}",
                            suma_g,
                            gl_orig,
                            f"Abono grupal cuenta Maelen ****8023 ({len(lista_p)} habitaciones)",
                            "|".join(str(x.get("pago_id") or "") for x in lista_p),
                            f"Habs: {', '.join(sorted({str(x.get('hab') or '') for x in lista_p}))}",
                            ahora_iso,
                        ),
                    )
                    insertados_maelen += 1

                # Asegurar entrada en `yape_conciliacion_hostal` para que el motor de conciliación la enlace como COMPROBADO
                ya_en_conc = any(
                    _normalizar_fecha_dmy(fy["fecha_operacion"]) == f_dmy
                    and abs(float(fy["m"] or 0) - suma_g) <= 0.10
                    and str(fy.get("nro_operacion") or "").strip().lower() == gl_orig.lower()
                    for fy in filas_yape_actuales
                )
                if not ya_en_conc:
                    rem_corp = f"TRANSFERENCIA CTA MAELEN ****8023 - {etiqueta_emp}"
                    conn.execute(
                        """
                        INSERT INTO yape_conciliacion_hostal (
                            fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                            nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                            estado_auditoria, observacion, updated_at
                        ) VALUES (?, ?, ?, ?, ?, '', '', '', 'SIN ANOTAR', ?, ?)
                        """,
                        (
                            f_dmy,
                            hora_op_c,
                            rem_corp,
                            suma_g,
                            gl_orig,
                            f"🏦 Transferencia Cuenta Empresa Maelen ****8023 ({len(lista_p)} habs) — Conciliación grupal",
                            ahora_iso,
                        ),
                    )
                    filas_yape_actuales.append(
                        {"id": -1, "fecha_operacion": f_dmy, "hora_operacion": hora_op_c, "remitente_bcp": rem_corp, "m": suma_g, "nro_operacion": gl_orig, "observacion": ""}
                    )
                    corp_detectados += 1

            # 5) Sincronizar registros de `maelen_transferencias_correo` hacia `yape_conciliacion_hostal` y cruzar contra gastos/yape
            todas_m = [dict(r) for r in conn.execute("SELECT * FROM maelen_transferencias_correo ORDER BY created_at DESC").fetchall()]
            for mv in todas_m:
                f_op = _normalizar_fecha_dmy(mv.get("fecha_operacion") or "")
                h_op = str(mv.get("hora_operacion") or "00:00:00")
                m_val = round(float(mv.get("monto") or 0), 2)
                tipo_m = str(mv.get("tipo_movimiento") or "")
                dest = str(mv.get("remitente_o_destino") or "").upper()
                min_mv = _hora_str_a_minutos(h_op)

                if tipo_m == "INGRESO_YAPEO_BCP":
                    # Buscar si ya llegó en Drive Excel o celular (misma fecha, mismo monto, mayor similitud de nombre / cercanía horaria)
                    match_y = None
                    mejor_sim_y = -1.0
                    for fy in filas_yape_actuales:
                        if _normalizar_fecha_dmy(fy["fecha_operacion"]) != f_op or abs(float(fy["m"] or 0) - m_val) > 0.05:
                            continue
                        if str(fy.get("remitente_bcp") or "").upper().startswith("SALIDA"):
                            continue
                        min_fy = _hora_str_a_minutos(str(fy.get("hora_operacion") or ""))
                        diff_m = abs(min_mv - min_fy) if (min_mv is not None and min_fy is not None) else 999
                        sim_y = _similitud_nombres(dest, str(fy.get("remitente_bcp") or ""))
                        if diff_m <= 5 or sim_y >= 0.45:
                            score_y = sim_y * 100.0 - (diff_m * 0.5 if diff_m < 999 else 20.0)
                            if score_y > mejor_sim_y:
                                mejor_sim_y = score_y
                                match_y = fy

                    if match_y and int(match_y.get("id") or 0) > 0:
                        obs_p = str(match_y.get("observacion") or "")
                        if "Correo BCP Maelen" not in obs_p:
                            n_obs = (obs_p + f" | ✉️ Correo BCP Maelen").strip(" |")
                            conn.execute("UPDATE yape_conciliacion_hostal SET observacion = ?, updated_at = ? WHERE id = ?", (n_obs, ahora_iso, match_y["id"]))
                            corroborados_yape += 1
                        conn.execute(
                            "UPDATE maelen_transferencias_correo SET estado_cruce = 'EN_REPORTE_YAPE', id_cruce_recepcion = ?, detalle_cruce = ? WHERE msg_id = ?",
                            (str(match_y["id"]), f"Corroborado con Yape #{match_y['id']} ({match_y['remitente_bcp']})", mv["msg_id"]),
                        )
                    elif not match_y:
                        # Solo insertar a `yape_conciliacion_hostal` si está dentro del rango de fechas operativo en BD (>= fecha mínima)
                        ymds_y = [_dmy_a_ymd(_normalizar_fecha_dmy(fy["fecha_operacion"])) for fy in filas_yape_actuales if fy.get("fecha_operacion")]
                        min_ymd = min((y for y in ymds_y if y), default="")
                        if not min_ymd or _dmy_a_ymd(f_op) >= min_ymd:
                            cur_ins = conn.execute(
                                """
                                INSERT INTO yape_conciliacion_hostal (
                                    fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                                    nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                                    estado_auditoria, observacion, updated_at
                                ) VALUES (?, ?, ?, ?, ?, '', '', '', 'SIN ANOTAR', ?, ?)
                                """,
                                (
                                    f_op,
                                    h_op,
                                    f"BCP - {dest}",
                                    m_val,
                                    f"{m_val:g} {_hora_str_a_12h_corta(h_op)}",
                                    f"✉️ Extraído en vivo de Correo BCP Maelen (#{mv['msg_id'][:8]})",
                                    ahora_iso,
                                ),
                            )
                            new_yid = int(cur_ins.lastrowid or 0)
                            filas_yape_actuales.append(
                                {"id": new_yid, "fecha_operacion": f_op, "hora_operacion": h_op, "remitente_bcp": f"BCP - {dest}", "m": m_val, "nro_operacion": "", "observacion": "Correo BCP Maelen"}
                            )
                            insertados_yape_vivo += 1
                            conn.execute(
                                "UPDATE maelen_transferencias_correo SET estado_cruce = 'INGRESO_CORREO_VIVO', id_cruce_recepcion = ?, detalle_cruce = 'Ingresado desde correo BCP a panel en vivo' WHERE msg_id = ?",
                                (str(new_yid), mv["msg_id"]),
                            )

                elif tipo_m in ("SALIDA_TRANSFERENCIA_BCP", "SALIDA_YAPEO_BCP", "PAGO_SERVICIO_BCP"):
                    # Cruzar salidas contra `gastos` y contra salidas existentes en `yape_conciliacion_hostal`
                    g_match = None
                    mv_dt = _dmy_a_date(f_op)
                    for g in gastos_chicas:
                        if abs(float(g.get("monto") or 0) - m_val) > 0.05:
                            continue
                        g_dt = _dmy_a_date(g.get("fecha_dmy_norm") or "")
                        dd = abs((g_dt - mv_dt).days) if (g_dt and mv_dt) else 999
                        if dd <= 2:
                            g_match = g
                            break

                    ya_sal_yape = any(
                        _normalizar_fecha_dmy(fy["fecha_operacion"]) == f_op
                        and abs(float(fy["m"] or 0) - m_val) <= 0.05
                        and str(fy.get("remitente_bcp") or "").upper().startswith("SALIDA")
                        for fy in filas_yape_actuales
                    )
                    es_traspaso_propio = "CALANI GUEVARA" in dest and not g_match

                    if g_match:
                        conn.execute(
                            "UPDATE maelen_transferencias_correo SET estado_cruce = 'CRUZADO_GASTO', id_cruce_recepcion = ?, detalle_cruce = ? WHERE msg_id = ?",
                            (str(g_match.get("gasto_uuid") or g_match.get("id_gasto") or ""), f"Gasto: {g_match.get('descripcion') or ''} ({g_match.get('fecha_dmy_norm')})", mv["msg_id"]),
                        )
                    elif ya_sal_yape:
                        conn.execute(
                            "UPDATE maelen_transferencias_correo SET estado_cruce = 'EN_REPORTE_YAPE', detalle_cruce = 'Incluido en Reporte Yape/SQLite como SALIDA' WHERE msg_id = ?",
                            (mv["msg_id"],),
                        )
                    elif es_traspaso_propio:
                        conn.execute(
                            "UPDATE maelen_transferencias_correo SET estado_cruce = 'TRASPASO_TITULAR', detalle_cruce = 'Transferencia interna Maelen -> Cuenta Gerencia' WHERE msg_id = ?",
                            (mv["msg_id"],),
                        )

                    # Si no figura aún en `yape_conciliacion_hostal` (ej. Transferencia a terceros BCP) y es >= Oct 2026 y no es traspaso interno sin gasto, agregarlo como SALIDA MAELEN BCP
                    if not ya_sal_yape and not es_traspaso_propio and _dmy_a_ymd(f_op) >= "2026-10-01":
                        rem_sal = f"SALIDA MAELEN BCP: {dest}"
                        conn.execute(
                            """
                            INSERT INTO yape_conciliacion_hostal (
                                fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                                nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                                estado_auditoria, observacion, updated_at
                            ) VALUES (?, ?, ?, ?, ?, '', '', '', 'SALIDA', ?, ?)
                            """,
                            (
                                f_op,
                                h_op,
                                rem_sal,
                                m_val,
                                str(mv.get("nro_operacion") or ""),
                                f"🏦 {tipo_m.replace('_', ' ')} Cta Empresa Maelen ****8023 (#{mv['msg_id'][:8]})",
                                ahora_iso,
                            ),
                        )
                        filas_yape_actuales.append(
                            {"id": -1, "fecha_operacion": f_op, "hora_operacion": h_op, "remitente_bcp": rem_sal, "m": m_val, "nro_operacion": str(mv.get("nro_operacion") or ""), "observacion": ""}
                        )

            res_comp = comprobar_yapes_con_anotaciones_chicas(conn, recalcular_todo=bool(forzar or insertados_yape_vivo > 0 or corp_detectados > 0))
            total_m_bd = conn.execute("SELECT COUNT(*) AS c FROM maelen_transferencias_correo").fetchone()["c"]

        resumen = {
            "ok": True,
            "nuevosLeidosGmail": nuevos_leidos_gmail,
            "insertadosMaelen": insertados_maelen,
            "insertadosYapeVivo": insertados_yape_vivo,
            "corroboradosYape": corroborados_yape,
            "corporativosMaelenDetectados": corp_detectados,
            "totalMaelenBD": int(total_m_bd or 0),
            "comprobacion": res_comp,
            "errorGmail": error_gmail,
        }
        _ultimo_maelen_resumen = resumen
        return resumen
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        _maelen_sync_lock.release()


def _despachar_alerta_wsp_fisico(telefono: str, mensaje: str) -> Dict[str, Any]:
    """Despacha un mensaje de WhatsApp usando brazo_wsp.py (o ejecutar_remoto.py si aplica)."""
    try:
        script_target = None
        cmd: List[str] = []
        if BRAZO_WSP_SCRIPT.exists():
            cmd = ["python", str(BRAZO_WSP_SCRIPT), "--telefono", str(telefono), "--mensaje", mensaje]
            script_target = str(BRAZO_WSP_SCRIPT)
        elif EJECUTAR_REMOTO_SCRIPT.exists():
            cmd = ["python", str(EJECUTAR_REMOTO_SCRIPT), "--script", "brazo_wsp.py", "--telefono", str(telefono), "--mensaje", mensaje]
            script_target = str(EJECUTAR_REMOTO_SCRIPT)
        if not cmd:
            return {"ok": False, "metodo": "NO_DISPONIBLE", "error": "No se encontró brazo_wsp.py"}
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        return {
            "ok": proc.returncode == 0,
            "metodo": Path(script_target).name,
            "stdout": (proc.stdout or "").strip()[:300],
            "stderr": (proc.stderr or "").strip()[:300],
        }
    except Exception as exc:
        return {"ok": False, "metodo": "EXCEPCION", "error": str(exc)}


def verificar_y_alertar_yapes_sin_anotar_5min(
    ventana_horas: int = 24,
    enviar_wsp_real: bool = False,
    incluir_reciente_supervision: bool = False,
    telefono_destino: str = TEL_ALERTA_RECEPCION,
) -> Dict[str, Any]:
    """
    Vigilante WhatsApp (+5 minutos):
    Si llega un Yape y pasados 5 minutos NO ha sido registrado por recepción (`SIN ANOTAR` / `PENDIENTE`),
    genera la alerta WhatsApp con remitente, monto, hora y día, la encola en `wsp_recepcion_cola_envio`
    y en producción (o con `enviar_wsp_real=True`) la envía por WhatsApp a Recepción.
    """
    init_yape_tables()
    ahora_dt = datetime.now()
    ahora_iso = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    tel_dest = str(telefono_destino or TEL_ALERTA_RECEPCION).strip()

    alertas_creadas: List[Dict[str, Any]] = []
    omitidos_recientes = 0
    omitidos_antiguos = 0

    with db_transaction() as conn:
        comprobar_yapes_con_anotaciones_chicas(conn, recalcular_todo=False)

        ya_alertados = {
            int(r["id_yape"])
            for r in conn.execute("SELECT id_yape FROM yape_alertas_wsp_5min WHERE id_yape IS NOT NULL").fetchall()
        }

        sin_anotar = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                WHERE estado_auditoria IN ('SIN ANOTAR', 'PENDIENTE')
                  AND monto_bcp > 0
                  AND upper(remitente_bcp) NOT LIKE 'SALIDA%'
                ORDER BY id DESC
                """
            ).fetchall()
        ]

        max_fecha_ymd = ""
        if sin_anotar:
            ymds = [_dmy_a_ymd(_normalizar_fecha_dmy(r.get("fecha_operacion") or "")) for r in sin_anotar]
            max_fecha_ymd = max((y for y in ymds if y), default="")

        max_mins = float(max(1, int(ventana_horas or 24))) * 60.0

        for y in sin_anotar:
            y_id = int(y["id"])
            if y_id in ya_alertados:
                continue

            f_op = _normalizar_fecha_dmy(y.get("fecha_operacion") or "")
            h_op = str(y.get("hora_operacion") or "00:00:00").strip()
            if len(h_op) == 5:
                h_op += ":00"
            try:
                dt_yape = datetime.strptime(f"{f_op} {h_op}", "%d/%m/%Y %H:%M:%S")
            except Exception:
                continue

            delta_min = round((ahora_dt - dt_yape).total_seconds() / 60.0, 1)
            if delta_min < 5.0:
                omitidos_recientes += 1
                continue

            en_ventana = delta_min <= max_mins
            es_supervision_ultimo_dia = bool(incluir_reciente_supervision and _dmy_a_ymd(f_op) == max_fecha_ymd)
            if not en_ventana and not es_supervision_ultimo_dia:
                omitidos_antiguos += 1
                continue

            h12 = _hora_str_a_12h_corta(h_op)
            rem = str(y.get("remitente_bcp") or "CLIENTE YAPE").strip()
            monto_v = round(float(y.get("monto_bcp") or 0), 2)

            msg_wsp = (
                f"⚠️ *ALERTA RECEPCIÓN: YAPE NO REGISTRADO (+5 MIN)*\n"
                f"📱 *Yape de:* {rem}\n"
                f"💰 *Monto:* S/ {monto_v:.2f}\n"
                f"🕒 *Hora:* {h12} ({h_op}) | *Día:* {f_op}\n"
                f"⏳ *Sin registrar hace:* {int(delta_min)} min\n"
                f"👉 Por favor anotar este pago en el sistema de Recepción."
            )

            id_cola = f"YAPE5M-{y_id}-{uuid.uuid4().hex[:6]}"
            estado_wsp = "EN_COLA"
            enviado_en = ""

            if enviar_wsp_real and not MODO_PRUEBA:
                res_env = _despachar_alerta_wsp_fisico(tel_dest, msg_wsp)
                estado_wsp = "ENVIADO_WSP" if res_env.get("ok") else "ERROR_ENVIO"
                enviado_en = ahora_iso if res_env.get("ok") else ""
            elif MODO_PRUEBA:
                estado_wsp = "EN_COLA_PRUEBA"

            conn.execute(
                """
                INSERT OR IGNORE INTO yape_alertas_wsp_5min (
                    id_yape, fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                    minutos_transcurridos, mensaje_wsp, telefono_destino, estado_envio,
                    id_cola_wsp, creado_en, enviado_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    y_id,
                    f_op,
                    h_op,
                    rem,
                    monto_v,
                    delta_min,
                    msg_wsp,
                    tel_dest,
                    estado_wsp,
                    id_cola,
                    ahora_iso,
                    enviado_en,
                ),
            )

            # Encolar también en `wsp_recepcion_cola_envio` si la tabla existe
            try:
                conn.execute(
                    """
                    INSERT INTO wsp_recepcion_cola_envio (
                        destino_telefono, cliente_nombre, nro_comprobante,
                        texto, archivo_ruta, estado, creado_en, enviado_en
                    ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
                    """,
                    (
                        tel_dest,
                        rem,
                        f"ALERTA-YAPE-{y_id}",
                        msg_wsp,
                        estado_wsp,
                        ahora_iso,
                        enviado_en,
                    ),
                )
            except Exception:
                pass

            alertas_creadas.append(
                {
                    "id_yape": y_id,
                    "fecha_operacion": f_op,
                    "hora_operacion": h_op,
                    "hora12": h12,
                    "remitente_bcp": rem,
                    "monto_bcp": monto_v,
                    "minutos_transcurridos": delta_min,
                    "estado_envio": estado_wsp,
                    "mensaje_wsp": msg_wsp,
                    "id_cola_wsp": id_cola,
                }
            )

        total_alertas_bd = conn.execute("SELECT COUNT(*) AS c FROM yape_alertas_wsp_5min").fetchone()["c"]

    return {
        "ok": True,
        "nuevasAlertas": len(alertas_creadas),
        "alertasCreadas": alertas_creadas,
        "omitidosMenos5Min": omitidos_recientes,
        "omitidosFueraVentana": omitidos_antiguos,
        "totalAlertasHistoricas": int(total_alertas_bd or 0),
        "modoPrueba": MODO_PRUEBA,
        "telefonoDestino": tel_dest,
    }


def obtener_resumen_yapes_sqlite(fecha_dmy: str = "", limite: int = 2000, forzar_drive: bool = False) -> Dict[str, Any]:
    """
    Sincroniza desde Google Drive + Correo BCP Maelen (**** 8023) si corresponde, comprueba en SOLO LECTURA
    contra lo que anotaron las chicas en SQLite y devuelve la estructura MENSUAL completa:
      - En Modo Recepción: últimos Yapes sueltos del mes (mitad superior) + Yapes recibidos diarios del mes (mitad inferior).
      - En Modo Administrador:
        1) Cruzados (Enlazados lado a lado: Banco <-> Recepción)
        2) Sueltos del mes (Sobrantes en Banco + Anotados sin Yape, sin recorte de scroll)
        3) Extraídos del Reporte de Transacciones (continuidad mensual completa)
        4) Tabla Conciliación (reemplazo directo de la hoja `CONCILIACION PAGOS YAPE`).
        5) Cuenta Empresa Maelen (****8023) & Alertas Mensajero WSP (+5 min).
    """
    init_yape_tables()
    if forzar_drive:
        info_drive = sincronizar_yapes_desde_drive_a_sqlite(forzar=True)
        sincronizar_transferencias_maelen_correo(forzar=True)
        verificar_y_alertar_yapes_sin_anotar_5min(ventana_horas=24)
    else:
        threading.Thread(target=sincronizar_yapes_desde_drive_a_sqlite, kwargs={"forzar": False}, daemon=True).start()
        threading.Thread(target=sincronizar_transferencias_maelen_correo, kwargs={"forzar": False}, daemon=True).start()
        info_drive = {"archivoDrive": _ultimo_archivo_drive_nombre}

    filtro_raw = str(fecha_dmy or "").strip()
    # Soportar formato YYYY-MM o YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}$", filtro_raw):
        p = filtro_raw.split("-")
        filtro_raw = f"{p[1]}/{p[0]}"
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", filtro_raw):
        p = filtro_raw.split("-")
        filtro_raw = f"{p[2]}/{p[1]}/{p[0]}"

    conn = get_connection()
    try:
        comprobar_yapes_con_anotaciones_chicas(conn, recalcular_todo=forzar_drive)
        conn.commit()

        # Obtener todos los meses disponibles en `yape_conciliacion_hostal` y `pagos`
        meses_set = set()
        mes_actual_sistema = datetime.now().strftime("%m/%Y")
        meses_set.add(mes_actual_sistema)

        todas_filas_yape = conn.execute("SELECT * FROM yape_conciliacion_hostal ORDER BY id DESC").fetchall()
        ultima_fecha_bd = ""
        for r_all in todas_filas_yape:
            f_op = _normalizar_fecha_dmy(r_all["fecha_operacion"] or "")
            if f_op and not ultima_fecha_bd:
                ultima_fecha_bd = f_op
            m_op = _extraer_mes_mmyyyy(f_op)
            if m_op:
                meses_set.add(m_op)

        meses_disponibles = sorted(
            meses_set,
            key=lambda m: (int(m.split("/")[1]), int(m.split("/")[0])) if "/" in m else (0, 0),
            reverse=True,
        )

        # Determinar si el filtro es un Mes ('MM/YYYY'), un Día específico ('DD/MM/YYYY') o 'TODOS'
        es_todos = filtro_raw.upper() in ("TODOS", "ALL", "RECIENTES")
        es_dia_especifico = bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", filtro_raw))
        es_mes_especifico = bool(re.match(r"^\d{1,2}/\d{4}$", filtro_raw))

        if es_mes_especifico:
            pm = filtro_raw.split("/")
            mes_mostrado = f"{int(pm[0]):02d}/{pm[1]}"
            dia_mostrado = ""
        elif es_dia_especifico:
            dia_mostrado = _normalizar_fecha_dmy(filtro_raw)
            mes_mostrado = _extraer_mes_mmyyyy(dia_mostrado)
        elif es_todos:
            mes_mostrado = "TODOS"
            dia_mostrado = ""
        else:
            # Por defecto: Mes más reciente con datos (ej. 11/2026)
            mes_mostrado = _extraer_mes_mmyyyy(ultima_fecha_bd) or mes_actual_sistema
            dia_mostrado = ""

        # Filtrar filas de `yape_conciliacion_hostal` según mes o día
        filas_raw = []
        for r in todas_filas_yape:
            f_op = _normalizar_fecha_dmy(r["fecha_operacion"] or "")
            m_op = _extraer_mes_mmyyyy(f_op)
            if es_todos:
                filas_raw.append(r)
            elif dia_mostrado:
                if f_op == dia_mostrado:
                    filas_raw.append(r)
            else:
                if m_op == mes_mostrado:
                    filas_raw.append(r)

        # Cargar todas las anotaciones de recepción y construir un mapa por ID para mostrar los ENLAZADOS lado a lado
        pagos_chicas, ventas_chicas, vueltos_chicas, gastos_chicas = _cargar_anotaciones_recepcion(conn)

        mapa_anotacion_por_id: Dict[str, Dict[str, Any]] = {}
        ids_ya_cruzados: set = set()
        mapa_yape_por_id_calzado: Dict[str, Dict[str, Any]] = {}

        for r_calz in todas_filas_yape:
            d_calz = dict(r_calz)
            est_c = str(d_calz.get("estado_auditoria") or "").upper()
            if d_calz.get("id_estancia_calzada") and est_c in ("COMPROBADO", "CALZADO", "EXCEDENTE", "SALIDA"):
                for pid_part in str(d_calz.get("id_estancia_calzada") or "").split("|"):
                    pid_clean = pid_part.strip()
                    if pid_clean:
                        ids_ya_cruzados.add(pid_clean)
                        mapa_yape_por_id_calzado[pid_clean] = d_calz

        # Determinar la fecha mínima cubierta por los reportes de Drive en SQLite para no marcar falsos faltantes antes del primer reporte
        ymds_banco = [_dmy_a_ymd(_normalizar_fecha_dmy(r["fecha_operacion"] or "")) for r in todas_filas_yape if r["fecha_operacion"]]
        min_ymd_banco = min((y for y in ymds_banco if y), default="")

        anotados_chicas: List[Dict[str, Any]] = []
        sueltos_anotados: List[Dict[str, Any]] = []

        for pr in pagos_chicas:
            f_dmy_p = pr["fecha_dmy"]
            ymd_p = _dmy_a_ymd(f_dmy_p)
            m_p = _extraer_mes_mmyyyy(f_dmy_p)
            pid = str(pr["pago_id"] or "").strip()
            hora_p = pr["hora_reg"]
            cruzado_flag = pid in ids_ya_cruzados
            item_anot = {
                "id": pid,
                "origen": "Habitación",
                "fecha_operacion": f_dmy_p,
                "mes_operacion": m_p,
                "hora_operacion": hora_p,
                "hora12": _hora_str_a_12h_corta(str(pr.get("nro_operacion") or "") or hora_p),
                "hab": str(pr.get("hab") or "S/H").strip() or "S/H",
                "huesped": str(pr.get("huesped") or "").strip(),
                "concepto": str(pr.get("concepto") or "Pago").strip(),
                "glosa": str(pr.get("nro_operacion") or "").strip(),
                "monto": round(float(pr.get("monto") or 0), 2),
                "cruzado": cruzado_flag,
            }
            mapa_anotacion_por_id[pid] = item_anot
            pasa_filtro = (
                True
                if es_todos
                else (f_dmy_p == dia_mostrado if dia_mostrado else m_p == mes_mostrado)
            )
            if pasa_filtro:
                anotados_chicas.append(item_anot)
                if not cruzado_flag and (not min_ymd_banco or ymd_p >= min_ymd_banco):
                    sueltos_anotados.append(item_anot)

        for vr in ventas_chicas:
            f_dmy_v = vr["fecha_dmy_norm"]
            ymd_v = _dmy_a_ymd(f_dmy_v)
            m_v = _extraer_mes_mmyyyy(f_dmy_v)
            vid = f"TIENDA-{vr['venta_uuid']}"
            hora_v = vr["hora_reg"]
            cruzado_flag = vid in ids_ya_cruzados
            item_vt = {
                "id": vid,
                "origen": "Tienda",
                "fecha_operacion": f_dmy_v,
                "mes_operacion": m_v,
                "hora_operacion": hora_v,
                "hora12": _hora_str_a_12h_corta(str(vr.get("nro_op_yape") or "") or hora_v),
                "hab": str(vr.get("hab") or "TIENDA").strip() or "TIENDA",
                "huesped": f"TIENDA ({vr.get('personal') or 'Recepción'}): {vr.get('cantidad')}x {vr.get('producto')}",
                "concepto": f"{vr.get('cantidad')}x {vr.get('producto')}",
                "glosa": str(vr.get("nro_op_yape") or "").strip(),
                "monto": round(float(vr.get("subtotal") or 0), 2),
                "cruzado": cruzado_flag,
            }
            mapa_anotacion_por_id[vid] = item_vt
            pasa_filtro = (
                True
                if es_todos
                else (f_dmy_v == dia_mostrado if dia_mostrado else m_v == mes_mostrado)
            )
            if pasa_filtro:
                anotados_chicas.append(item_vt)
                if not cruzado_flag and (not min_ymd_banco or ymd_v >= min_ymd_banco):
                    sueltos_anotados.append(item_vt)

        # Ordenar sueltos_anotados cronológicamente (del más reciente al más antiguo)
        sueltos_anotados.sort(
            key=lambda x: (_dmy_a_ymd(x.get("fecha_operacion") or ""), str(x.get("hora_operacion") or ""), str(x.get("id") or "")),
            reverse=True,
        )

        alertas_wsp_rows = [
            dict(ar)
            for ar in conn.execute("SELECT * FROM yape_alertas_wsp_5min ORDER BY id_alerta DESC LIMIT 150").fetchall()
        ]
        mapa_alerta_por_yape_id = {int(ar["id_yape"]): ar for ar in alertas_wsp_rows if ar.get("id_yape") is not None}

        maelen_raw = [
            dict(mr)
            for mr in conn.execute("SELECT * FROM maelen_transferencias_correo ORDER BY msg_id DESC").fetchall()
        ]
        transferencias_maelen_mes: List[Dict[str, Any]] = []
        tot_maelen_ing = 0.0
        tot_maelen_sal = 0.0
        corp_maelen_cnt = 0
        cruz_gasto_maelen_cnt = 0
        for mr in maelen_raw:
            f_m = _normalizar_fecha_dmy(mr.get("fecha_operacion") or "")
            m_m = _extraer_mes_mmyyyy(f_m)
            mr["fecha_operacion"] = f_m
            mr["mes_operacion"] = m_m
            mr["hora12"] = _hora_str_a_12h_corta(mr.get("hora_operacion") or "")
            pasa_m = (
                True
                if es_todos
                else (f_m == dia_mostrado if dia_mostrado else m_m == mes_mostrado)
            )
            if pasa_m:
                transferencias_maelen_mes.append(mr)
                tm = str(mr.get("tipo_movimiento") or "")
                mv = round(float(mr.get("monto") or 0), 2)
                if tm.startswith("INGRESO") or tm == "CORPORATIVO_MAELEN":
                    tot_maelen_ing = round(tot_maelen_ing + mv, 2)
                else:
                    tot_maelen_sal = round(tot_maelen_sal + mv, 2)
                if tm == "CORPORATIVO_MAELEN":
                    corp_maelen_cnt += 1
                if mr.get("estado_cruce") in ("CRUZADO_GASTO", "CRUZADO", "EN_REPORTE_YAPE", "INGRESO_CORREO_VIVO"):
                    cruz_gasto_maelen_cnt += 1

        transferencias_maelen_mes.sort(
            key=lambda x: (_dmy_a_ymd(x.get("fecha_operacion") or ""), str(x.get("hora_operacion") or "")),
            reverse=True,
        )

        ahora_local_dt = datetime.now()

        def _serializar(r) -> Dict[str, Any]:
            d = dict(r)
            d["fecha_operacion"] = _normalizar_fecha_dmy(d.get("fecha_operacion") or "")
            d["mes_operacion"] = _extraer_mes_mmyyyy(d["fecha_operacion"])
            est = str(d.get("estado_auditoria") or "").upper()
            if est in ("CALZADO", "COMPROBADO"):
                d["estado_auditoria"] = "COMPROBADO"
            elif est == "PENDIENTE":
                d["estado_auditoria"] = "SIN ANOTAR"
            d["hora12"] = _hora_str_a_12h_corta(d.get("hora_operacion") or "")
            alerta_info = mapa_alerta_por_yape_id.get(int(d.get("id") or 0))
            mins_sin_anotar = None
            if d["estado_auditoria"] == "SIN ANOTAR":
                try:
                    h_op_s = str(d.get("hora_operacion") or "00:00:00").strip()
                    if len(h_op_s) == 5:
                        h_op_s += ":00"
                    dt_y = datetime.strptime(f"{d['fecha_operacion']} {h_op_s}", "%d/%m/%Y %H:%M:%S")
                    mins_sin_anotar = round((ahora_local_dt - dt_y).total_seconds() / 60.0, 1)
                except Exception:
                    mins_sin_anotar = None
            d["minutos_sin_anotar"] = mins_sin_anotar
            d["alerta_wsp_5m"] = alerta_info if alerta_info else None

            # Enriquecer con los detalles exactos del registro de recepción enlazado (`enlace_recepcion`)
            ids_enl = [p.strip() for p in str(d.get("id_estancia_calzada") or "").split("|") if p.strip()]
            detalles_enl = [mapa_anotacion_por_id[i] for i in ids_enl if i in mapa_anotacion_por_id]
            if detalles_enl:
                monto_anot_sum = round(sum(float(x.get("monto") or 0) for x in detalles_enl), 2)
                glosas_enl = " / ".join(dict.fromkeys(x["glosa"] for x in detalles_enl if x.get("glosa")))
                fechas_enl = " / ".join(dict.fromkeys(x["fecha_operacion"] for x in detalles_enl if x.get("fecha_operacion")))
                horas_enl = " / ".join(dict.fromkeys(x["hora12"] for x in detalles_enl if x.get("hora12")))
                habs_enl = ", ".join(dict.fromkeys(x["hab"] for x in detalles_enl if x.get("hab")))
                huesp_enl = " / ".join(dict.fromkeys(x["huesped"] for x in detalles_enl if x.get("huesped")))
                d["enlace_recepcion"] = {
                    "enlazado": True,
                    "es_grupal": len(detalles_enl) > 1,
                    "cantidad_items": len(detalles_enl),
                    "ids": ids_enl,
                    "ids_str": " | ".join(ids_enl),
                    "hab": habs_enl or d.get("hab_calzada") or "S/H",
                    "huesped": huesp_enl or d.get("huesped_calzado") or "",
                    "monto_anotado": monto_anot_sum,
                    "glosa_anotada": glosas_enl or horas_enl or "—",
                    "fecha_anotada": fechas_enl,
                    "hora_anotada": horas_enl,
                    "items": detalles_enl,
                }
            elif d.get("id_estancia_calzada"):
                d["enlace_recepcion"] = {
                    "enlazado": True,
                    "es_grupal": "|" in str(d.get("id_estancia_calzada") or ""),
                    "cantidad_items": len(ids_enl),
                    "ids": ids_enl,
                    "ids_str": " | ".join(ids_enl),
                    "hab": str(d.get("hab_calzada") or "S/H"),
                    "huesped": str(d.get("huesped_calzado") or ""),
                    "monto_anotado": round(float(d.get("monto_bcp") or 0), 2),
                    "glosa_anotada": str(d.get("nro_operacion") or "—"),
                    "fecha_anotada": d["fecha_operacion"],
                    "hora_anotada": d["hora12"],
                    "items": [],
                }
            else:
                d["enlace_recepcion"] = None

            return d

        items = [_serializar(r) for r in filas_raw]
        items.sort(
            key=lambda x: (
                _dmy_a_ymd(str(x.get("fecha_operacion") or "")),
                str(x.get("hora_operacion") or ""),
                int(x.get("id") or 0),
            ),
            reverse=True,
        )

        recibidos_list = []
        cruzados_list = []
        sin_anotar_list = []
        salidas_list = []
        extraidos_reporte_list = []

        tot_banco = 0.0
        tot_comprobado = 0.0
        tot_sin_anotar = 0.0
        tot_excedente = 0.0
        tot_salidas = 0.0
        tot_sueltos_anotados = round(sum(float(x["monto"] or 0) for x in sueltos_anotados), 2)

        dias_cubiertos = set()

        for it in items:
            est = str(it.get("estado_auditoria") or "").upper()
            m = round(float(it.get("monto_bcp") or 0), 2)
            if it.get("fecha_operacion"):
                dias_cubiertos.add(it["fecha_operacion"])
            extraidos_reporte_list.append(it)

            if est == "SALIDA":
                tot_salidas = round(tot_salidas + m, 2)
                salidas_list.append(it)
                continue

            recibidos_list.append(it)
            tot_banco = round(tot_banco + m, 2)
            if est == "COMPROBADO":
                tot_comprobado = round(tot_comprobado + m, 2)
                cruzados_list.append(it)
            elif est == "EXCEDENTE":
                tot_excedente = round(tot_excedente + m, 2)
                cruzados_list.append(it)
            else:
                tot_sin_anotar = round(tot_sin_anotar + m, 2)
                sin_anotar_list.append(it)

        # Construir `tablaConciliacion`: reemplazo exacto de la hoja `CONCILIACION PAGOS YAPE` en SQLite
        tabla_conciliacion: List[Dict[str, Any]] = []
        for it in items:
            est = str(it.get("estado_auditoria") or "").upper()
            enl = it.get("enlace_recepcion")
            if est in ("COMPROBADO", "EXCEDENTE"):
                m_anot = enl["monto_anotado"] if enl else round(float(it.get("monto_bcp") or 0), 2)
                hab_huesp = f"{'🛒 Tienda' if (enl and enl.get('hab') == 'TIENDA') else ('🏨 Hab. ' + str(enl.get('hab') if enl else it.get('hab_calzada') or ''))} — {enl.get('huesped') if enl else (it.get('huesped_calzado') or '')}"
                glosa_a = enl["glosa_anotada"] if enl else (it.get("nro_operacion") or "")
                id_reg = enl["ids_str"] if enl else (it.get("id_estancia_calzada") or "")
                f_reg = enl["fecha_anotada"] if enl else it.get("fecha_operacion")
                estado_hoja = "CUADRADO" if est == "COMPROBADO" else "DIFERENCIA / EXCEDENTE"
                if enl and enl.get("es_grupal"):
                    estado_hoja = f"CUADRADO (PAGO GRUPAL {enl.get('cantidad_items')} ÍTEMS)"
                tabla_conciliacion.append(
                    {
                        "tipo_fila": "CRUZADO" if est == "COMPROBADO" else "EXCEDENTE",
                        "orden_ymd": _dmy_a_ymd(it.get("fecha_operacion") or ""),
                        "orden_hora": str(it.get("hora_operacion") or ""),
                        "fecha_hora_banco": f"{it.get('fecha_operacion') or ''} {it.get('hora12') or it.get('hora_operacion') or ''}".strip(),
                        "origen_banco": str(it.get("remitente_bcp") or ""),
                        "monto_banco": round(float(it.get("monto_bcp") or 0), 2),
                        "monto_anotado": m_anot,
                        "huesped_anotado": hab_huesp,
                        "hora_anotada": glosa_a,
                        "estado_conciliacion": estado_hoja,
                        "detalle_observacion": str(it.get("observacion") or ""),
                        "id_registro": id_reg,
                        "fecha_registro": f_reg,
                    }
                )
            elif est == "SALIDA":
                if it.get("id_estancia_calzada"):
                    tabla_conciliacion.append(
                        {
                            "tipo_fila": "SALIDA_CUADRADA",
                            "orden_ymd": _dmy_a_ymd(it.get("fecha_operacion") or ""),
                            "orden_hora": str(it.get("hora_operacion") or ""),
                            "fecha_hora_banco": f"{it.get('fecha_operacion') or ''} {it.get('hora12') or ''}".strip(),
                            "origen_banco": str(it.get("remitente_bcp") or ""),
                            "monto_banco": round(float(it.get("monto_bcp") or 0), 2),
                            "monto_anotado": round(float(it.get("monto_bcp") or 0), 2),
                            "huesped_anotado": str(it.get("huesped_calzado") or ""),
                            "hora_anotada": str(it.get("nro_operacion") or ""),
                            "estado_conciliacion": "CUADRADO (SALIDA / GASTO / VUELTO)",
                            "detalle_observacion": str(it.get("observacion") or ""),
                            "id_registro": str(it.get("id_estancia_calzada") or ""),
                            "fecha_registro": str(it.get("fecha_operacion") or ""),
                        }
                    )
                else:
                    tabla_conciliacion.append(
                        {
                            "tipo_fila": "SALIDA_SUELTA",
                            "orden_ymd": _dmy_a_ymd(it.get("fecha_operacion") or ""),
                            "orden_hora": str(it.get("hora_operacion") or ""),
                            "fecha_hora_banco": f"{it.get('fecha_operacion') or ''} {it.get('hora12') or ''}".strip(),
                            "origen_banco": str(it.get("remitente_bcp") or ""),
                            "monto_banco": round(float(it.get("monto_bcp") or 0), 2),
                            "monto_anotado": 0.0,
                            "huesped_anotado": "—",
                            "hora_anotada": "—",
                            "estado_conciliacion": "SALIDA BANCO (Sin gasto/vuelto anotado)",
                            "detalle_observacion": str(it.get("observacion") or ""),
                            "id_registro": "",
                            "fecha_registro": "",
                        }
                    )
            else:
                tabla_conciliacion.append(
                    {
                        "tipo_fila": "SOBRANTE_BANCO",
                        "orden_ymd": _dmy_a_ymd(it.get("fecha_operacion") or ""),
                        "orden_hora": str(it.get("hora_operacion") or ""),
                        "fecha_hora_banco": f"{it.get('fecha_operacion') or ''} {it.get('hora12') or it.get('hora_operacion') or ''}".strip(),
                        "origen_banco": str(it.get("remitente_bcp") or ""),
                        "monto_banco": round(float(it.get("monto_bcp") or 0), 2),
                        "monto_anotado": 0.0,
                        "huesped_anotado": "—",
                        "hora_anotada": "—",
                        "estado_conciliacion": "SOBRANTE BANCO (Falta anotar en recepción)",
                        "detalle_observacion": str(it.get("observacion") or ""),
                        "id_registro": "",
                        "fecha_registro": "",
                    }
                )

        for sa in sueltos_anotados:
            hab_txt = "🛒 Tienda" if sa["hab"] == "TIENDA" else f"🏨 Hab. {sa['hab']}"
            tabla_conciliacion.append(
                {
                    "tipo_fila": "FALTANTE_BANCO",
                    "orden_ymd": _dmy_a_ymd(sa.get("fecha_operacion") or ""),
                    "orden_hora": str(sa.get("hora_operacion") or ""),
                    "fecha_hora_banco": "—",
                    "origen_banco": "— (No figura en Yape/Banco)",
                    "monto_banco": 0.0,
                    "monto_anotado": sa["monto"],
                    "huesped_anotado": f"{hab_txt} — {sa['huesped']}",
                    "hora_anotada": sa["glosa"] or sa["hora12"] or "—",
                    "estado_conciliacion": "FALTANTE BANCO (Anotado sin respaldo Yape)",
                    "detalle_observacion": f"Concepto: {sa['concepto']} | Glosa: {sa['glosa'] or '-'}",
                    "id_registro": sa["id"],
                    "fecha_registro": sa["fecha_operacion"],
                }
            )

        tabla_conciliacion.sort(key=lambda r: (r["orden_ymd"], r["orden_hora"]), reverse=True)

        # Continuidad de días en el mes
        dias_sorted = sorted(dias_cubiertos, key=_dmy_a_ymd)
        rango_continuidad = f"{dias_sorted[0]} al {dias_sorted[-1]} ({len(dias_sorted)} días con movimientos)" if dias_sorted else "Sin movimientos en el periodo"

        total_operaciones_eval = len(cruzados_list) + len(sin_anotar_list) + len(sueltos_anotados)
        tasa_conciliacion = round((len(cruzados_list) / total_operaciones_eval) * 100.0, 1) if total_operaciones_eval > 0 else 100.0

        max_id_row = conn.execute("SELECT MAX(id) AS max_id FROM yape_conciliacion_hostal").fetchone()
        ultimo_id = int(max_id_row["max_id"] or 0) if max_id_row else 0
        ping_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'yape_ultimo_ping_celular'").fetchone()
        ultimo_ping_bd = str(ping_row["valor"] or "") if ping_row else globals().get("_ULTIMO_PING_CELULAR_ISO", "")
        rep_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'yape_ultimo_reporte_drive'").fetchone()
        ultimo_rep_bd = str(rep_row["valor"] or "") if rep_row else (_ultimo_archivo_drive_nombre or info_drive.get("archivoDrive") or "")

        revisor_830_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM yape_revisor_830_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]

        return {
            "ok": True,
            "mesMostrado": mes_mostrado,
            "diaMostrado": dia_mostrado,
            "fechaMostrada": dia_mostrado if dia_mostrado else mes_mostrado,
            "mesesDisponibles": meses_disponibles,
            "ultimaFechaBD": ultima_fecha_bd,
            "archivoDrive": ultimo_rep_bd,
            "rangoContinuidad": rango_continuidad,
            "diasCubiertosCount": len(dias_sorted),
            "tasaConciliacion": tasa_conciliacion,
            "ultimoId": ultimo_id,
            "ultimoPingCelular": ultimo_ping_bd,
            "sinAnotarCount": len(sin_anotar_list),
            "sueltosAnotadosCount": len(sueltos_anotados),
            "items": items,
            "recibidos": recibidos_list,
            "cruzados": cruzados_list,
            "sueltosRecibidos": sin_anotar_list,
            "sueltosAnotados": sueltos_anotados,
            "anotadosChicas": anotados_chicas,
            "extraidosReporte": extraidos_reporte_list,
            "salidasBanco": salidas_list,
            "tablaConciliacion": tabla_conciliacion,
            "transferenciasMaelen": transferencias_maelen_mes,
            "resumenMaelen": {
                "totalMovimientos": len(transferencias_maelen_mes),
                "totalHistorico": len(maelen_raw),
                "ingresosMaelen": tot_maelen_ing,
                "salidasMaelen": tot_maelen_sal,
                "corporativosMaelen": corp_maelen_cnt,
                "cruzadosMaelen": cruz_gasto_maelen_cnt,
            },
            "alertasWsp5Min": alertas_wsp_rows,
            "alertasWsp5MinCount": len(alertas_wsp_rows),
            "revisor830Log": revisor_830_rows,
            "totales": {
                "totalBanco": tot_banco,
                "totalComprobado": tot_comprobado,
                "totalSinAnotar": tot_sin_anotar,
                "totalExcedente": tot_excedente,
                "totalSalidas": tot_salidas,
                "totalSueltosAnotados": tot_sueltos_anotados,
            },
        }
    finally:
        conn.close()


def parsear_notificacion_yape(texto: str, titulo: str = "") -> Optional[Dict[str, Any]]:
    """
    Lee el texto de la notificación push de Yape en el celular de recepción:
    Ej: 'Yape! JUAN PEREZ te envió un pago por S/ 60.00. El cód. de seguridad es: 482'
    """
    full = re.sub(r"\s+", " ", f"{titulo or ''} {texto or ''}".strip())
    if not full:
        return None
    lower_f = full.lower()
    if any(p in lower_f for p in ("promoción", "promocion", "descuento", "crédito", "préstamo", "sorteo")) and "te envió" not in lower_f and "te envio" not in lower_f:
        return None

    m_monto = re.search(r"[Ss]\/\.?\s*(\d{1,6}(?:[\.,]\d{1,2})?)", full)
    if not m_monto:
        return None
    try:
        monto = round(float(m_monto.group(1).replace(",", ".")), 2)
    except Exception:
        return None
    if monto <= 0:
        return None

    cod_seg = ""
    m_cod = re.search(r"(?:seguridad\s*(?:es)?\s*[:\-]?\s*|op(?:eraci[oó]n)?\s*[:#\-]?\s*)(\d{3,10})", full, re.IGNORECASE)
    if m_cod:
        cod_seg = m_cod.group(1).strip()

    remitente = ""
    for pat in (
        r"(?:Yape!|Pago\s*de\s*Yape:?)\s*(.+?)\s+te\s+envi[oó]\s+(?:un\s+pago|un\s+yape|[Ss]\/)",
        r"([A-Za-zÁÉÍÓÚÑáéíóúñ\s\.\*]{3,60}?)\s+te\s+envi[oó]\s+(?:un\s+pago|un\s+yape|[Ss]\/)",
        r"([A-Za-zÁÉÍÓÚÑáéíóúñ\s\.\*]{3,60}?)\s+te\s+yape[oó]",
        r"(?:recibiste\s+(?:un\s+)?(?:yape|pago)\s+de|de)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ\s\.\*]{3,60}?)\s+por\s+[Ss]\/",
    ):
        m_nom = re.search(pat, full, re.IGNORECASE)
        if m_nom:
            cand = re.sub(r"^(?:Confirmaci[oó]n\s+de\s+Pago\s*:?\s*|Yape!\s*)", "", m_nom.group(1).strip(" :-!¡.,"), flags=re.IGNORECASE).strip()
            if len(cand) >= 2:
                remitente = cand.upper()
                break

    ahora = datetime.now()
    hora_op = ahora.strftime("%H:%M:%S")
    return {
        "fecha_operacion": ahora.strftime("%d/%m/%Y"),
        "hora_operacion": hora_op,
        "remitente_bcp": remitente or "CLIENTE YAPE (CELULAR)",
        "monto_bcp": monto,
        "nro_operacion": cod_seg or f"{monto:g} {_hora_str_a_12h_corta(hora_op)}",
        "observacion": f"📱 Notificación celular en vivo (Cód: {cod_seg or '-'})",
    }


def registrar_notificacion_celular_sqlite(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recibe la notificación leída por el celular de recepción, la guarda en SQLite (`yape_conciliacion_hostal`)
    y la comprueba en SOLO LECTURA contra lo que anotaron las chicas.
    """
    global _ULTIMO_PING_CELULAR_ISO
    init_yape_tables()
    ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _ULTIMO_PING_CELULAR_ISO = ahora_iso
    texto_notif = str(datos.get("texto") or datos.get("text") or datos.get("mensaje") or "").strip()
    titulo_notif = str(datos.get("titulo") or datos.get("title") or "").strip()

    try:
        with db_transaction() as conn_ping:
            conn_ping.execute(
                "INSERT INTO estado_sistema (clave, valor) VALUES ('yape_ultimo_ping_celular', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (ahora_iso,),
            )
    except Exception:
        pass

    try:
        log_path = Path(__file__).resolve().parent / "yape_webhook.log"
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{ahora_iso}] RAW={json.dumps(datos, ensure_ascii=False)}\n")
    except Exception:
        pass

    parsed = parsear_notificacion_yape(texto_notif, titulo_notif)
    if not parsed:
        monto_exp = 0.0
        try:
            monto_exp = round(float(str(datos.get("monto") or "0").replace(",", ".")), 2)
        except Exception:
            monto_exp = 0.0
        if monto_exp > 0:
            ahora = datetime.now()
            hora_op = ahora.strftime("%H:%M:%S")
            parsed = {
                "fecha_operacion": ahora.strftime("%d/%m/%Y"),
                "hora_operacion": hora_op,
                "remitente_bcp": str(datos.get("remitente") or "CLIENTE YAPE (CELULAR)").strip().upper(),
                "monto_bcp": monto_exp,
                "nro_operacion": str(datos.get("codigo") or f"{monto_exp:g} {_hora_str_a_12h_corta(hora_op)}"),
                "observacion": "📱 Notificación celular en vivo",
            }
        else:
            return {
                "ok": False,
                "ignorado": True,
                "motivo": f"No se detectó monto en texto='{texto_notif}' titulo='{titulo_notif}'",
            }

    with db_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO yape_conciliacion_hostal (
                fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                estado_auditoria, observacion, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', '', '', 'SIN ANOTAR', ?, ?)
            """,
            (
                parsed["fecha_operacion"],
                parsed["hora_operacion"],
                parsed["remitente_bcp"],
                parsed["monto_bcp"],
                parsed["nro_operacion"],
                parsed["observacion"],
                ahora_iso,
            ),
        )
        new_id = int(cur.lastrowid or 0)
        res_comp = comprobar_yapes_con_anotaciones_chicas(conn)
        fila = dict(conn.execute("SELECT * FROM yape_conciliacion_hostal WHERE id = ?", (new_id,)).fetchone())
        fila["hora12"] = _hora_str_a_12h_corta(fila.get("hora_operacion") or "")

    return {"ok": True, "id": new_id, "yape": fila, "comprobacion": res_comp}


def importar_excel_yape_bytes(file_bytes: bytes, filename: str = "Reporte_Yape.xlsx") -> Dict[str, Any]:
    """Permite subir manualmente un archivo Excel de reporte de transacciones Yape."""
    init_yape_tables()
    import pandas as pd

    fh = io.BytesIO(file_bytes)
    df_temp = pd.read_excel(fh, skiprows=4)
    if len(df_temp.columns) < 6:
        return {"ok": False, "error": "Formato de Excel de Yape no reconocido."}
    cols = df_temp.columns.tolist()
    df_banco = df_temp.rename(
        columns={
            cols[0]: "Tipo",
            cols[1]: "Origen",
            cols[2]: "Destino",
            cols[3]: "Monto",
            cols[4]: "Mensaje",
            cols[5]: "Fecha",
        }
    )
    df_banco["Fecha_dt"] = pd.to_datetime(df_banco["Fecha"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    df_banco = df_banco.dropna(subset=["Fecha_dt"])
    df_banco = df_banco[df_banco["Tipo"].astype(str).str.contains("PAG", na=False, case=False)]
    df_banco = df_banco.sort_values("Fecha_dt").reset_index(drop=True)

    insertados = 0
    ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_transaction() as conn:
        existentes = {
            (_normalizar_fecha_dmy(r["fecha_operacion"]), r["hora_operacion"], r["remitente_bcp"], float(r["m"] or 0))
            for r in conn.execute(
                "SELECT fecha_operacion, hora_operacion, remitente_bcp, ROUND(monto_bcp, 2) AS m FROM yape_conciliacion_hostal"
            ).fetchall()
        }
        for _, row in df_banco.iterrows():
            tipo_str = str(row["Tipo"] or "").upper().strip()
            es_salida = "PAGASTE" in tipo_str
            dt_val: datetime = row["Fecha_dt"].to_pydatetime()
            fecha_op = dt_val.strftime("%d/%m/%Y")
            hora_op = dt_val.strftime("%H:%M:%S")
            try:
                monto_val = round(float(str(row["Monto"]).replace("S/", "").replace(",", ".").strip()), 2)
            except Exception:
                continue
            if monto_val <= 0:
                continue
            msg_str = str(row["Mensaje"]).strip() if pd.notna(row["Mensaje"]) and str(row["Mensaje"]).strip() else ""
            if es_salida:
                dest_str = str(row["Destino"]).strip() if pd.notna(row["Destino"]) else "DESTINO"
                remitente = f"SALIDA: {dest_str}".upper()
                estado_aud = "SALIDA"
            else:
                remitente = (str(row["Origen"]).strip() if pd.notna(row["Origen"]) else "CLIENTE YAPE").upper()
                estado_aud = "SIN ANOTAR"
            clave = (fecha_op, hora_op, remitente, monto_val)
            if clave in existentes:
                continue
            existentes.add(clave)
            nro_op = msg_str if msg_str else f"{monto_val:g} {_hora_str_a_12h_corta(hora_op)}"
            conn.execute(
                """
                INSERT INTO yape_conciliacion_hostal (
                    fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                    nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                    estado_auditoria, observacion, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', '', '', ?, ?, ?)
                """,
                (fecha_op, hora_op, remitente, monto_val, nro_op, estado_aud, f"Importado de {filename}", ahora_iso),
            )
            insertados += 1
        res_comp = comprobar_yapes_con_anotaciones_chicas(conn, recalcular_todo=True)
    return {"ok": True, "insertados": insertados, "comprobacion": res_comp}


def _normalizar_glosa_yape_con_criterio(texto: str, monto_pago: Optional[float] = None) -> Dict[str, Any]:
    """
    Normaliza glosas mal tipeadas por recepción en Yape:
    - ';' o '.' como separador horario ('1;5pm' -> '1:05pm', '1.5pm' -> '1:05pm')
    - Minutos de 1 dígito ('1:5pm' -> '1:05pm')
    - Formato 24h con 'pm' ('13:45pm' -> '1:45pm')
    - Montos duplicados al inicio ('140 140 1:55pm' -> '140 1:55pm')
    """
    orig = str(texto or "").strip()
    if not orig:
        return {
            "original": orig,
            "normalizada": orig,
            "cambio": False,
            "motivo": "",
            "hora_12h": "",
            "minutos": None,
            "monto_glosa": None,
        }

    s = re.sub(r"\s+", " ", orig).strip()
    motivos: List[str] = []

    # 1. Duplicado de monto inicial (ej. '140 140 1:55pm' -> '140 1:55pm')
    m_dup = re.match(r"^(?:[Ss]/\.?\s*)?(\d+(?:[\.,]\d{1,2})?)\s+\1\b(.*)$", s)
    if m_dup:
        s = f"{m_dup.group(1)}{m_dup.group(2)}".strip()
        motivos.append("monto_duplicado")

    # 2. Separador ';' entre dígitos de hora ('1;5pm', '1;45pm', '13;45')
    if re.search(r"\b\d{1,2};\d{1,2}", s):
        s = re.sub(r"\b(\d{1,2});(\d{1,2})", r"\1:\2", s)
        motivos.append("separador_punto_y_coma")

    # 3. Separador '.' cuando va pegado a am/pm ('1.5pm', '1.45 pm')
    if re.search(r"\b\d{1,2}\.\d{1,2}\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm)\b", s, flags=re.IGNORECASE):
        s = re.sub(
            r"\b(\d{1,2})\.(\d{1,2})(\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm))\b",
            r"\1:\2\3",
            s,
            flags=re.IGNORECASE,
        )
        motivos.append("separador_punto_horario")

    # 4. Minuto de 1 dígito ('1:5pm' -> '1:05pm')
    if re.search(r"\b\d{1,2}:(\d)(?!\d)", s):
        s = re.sub(r"\b(\d{1,2}):(\d)(?!\d)", r"\1:0\2", s)
        motivos.append("minuto_un_digito")

    # 5. Hora 24h combinada con 'pm'/'am' ('13:45pm' -> '1:45pm', '0:45am' -> '12:45am')
    def _fix_24h_ampm(match: re.Match) -> str:
        hh = int(match.group(1))
        mm = int(match.group(2))
        suf = match.group(3).lower().replace(".", "").replace(" ", "")
        if "p" in suf and hh > 12 and hh <= 23:
            motivos.append("hora_24h_con_pm")
            return f"{hh - 12}:{mm:02d}pm"
        if "a" in suf and hh == 0:
            motivos.append("hora_0h_con_am")
            return f"12:{mm:02d}am"
        return f"{hh}:{mm:02d}{suf}"

    s = re.sub(r"\b(\d{1,2}):(\d{2})\s*(a\.?\s*m\.?|p\.?\s*m\.?|am|pm)\b", _fix_24h_ampm, s, flags=re.IGNORECASE)

    # Si no trae monto inicial pero tenemos monto_pago y solo hay hora, mantener limpio
    mins = _hora_str_a_minutos(s)
    hora_12h = _hora_str_a_12h_corta(s) if mins is not None else ""
    monto_glosa = _parse_monto_glosa(s)
    if monto_glosa is None and monto_pago and float(monto_pago) > 0 and hora_12h:
        m_val = float(monto_pago)
        m_txt = str(int(m_val)) if abs(m_val - int(m_val)) < 0.005 else f"{m_val:.2f}"
        # Solo anteponer monto si la glosa original era puramente horaria sin monto
        if re.match(r"^\d{1,2}[:;\.]\d{1,2}", orig):
            s = f"{m_txt} {hora_12h}"

    cambio = (s != orig) or bool(motivos)
    return {
        "original": orig,
        "normalizada": s,
        "cambio": cambio,
        "motivo": ", ".join(dict.fromkeys(motivos)) if motivos else ("formato_normalizado" if cambio else ""),
        "hora_12h": hora_12h,
        "minutos": mins,
        "monto_glosa": monto_glosa,
    }


def _registrar_aprendizaje_yape(
    conn,
    fecha_iso: str,
    categoria: str,
    pregunta_o_caso: str,
    solucion_aprendida: str,
    metadata: Dict[str, Any],
) -> None:
    try:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(aprendizaje_compartido_hostal)").fetchall()
        }
        if "fecha_hora" in cols:
            conn.execute(
                """
                INSERT INTO aprendizaje_compartido_hostal (
                    id, fecha_hora, origen_equipo, actor, categoria,
                    pregunta_o_evento, patron_detectado, regla_aprendida, updated_at
                ) VALUES (?, ?, ?, 'REVISOR_YAPE_830', ?, ?, ?, ?, ?)
                """,
                (
                    f"YAPE830-{uuid.uuid4().hex[:12]}",
                    fecha_iso,
                    "PRUEBA_8082" if MODO_PRUEBA else "LAPTOP_2_RECEPCION",
                    categoria,
                    pregunta_o_caso,
                    json.dumps(metadata, ensure_ascii=False),
                    solucion_aprendida,
                    fecha_iso,
                ),
            )
        elif "fecha_iso" in cols:
            conn.execute(
                """
                INSERT INTO aprendizaje_compartido_hostal (
                    fecha_iso, origen, categoria, pregunta_o_caso, solucion_aprendida, metadata_json
                ) VALUES (?, 'revisor_yape_830am', ?, ?, ?, ?)
                """,
                (
                    fecha_iso,
                    categoria,
                    pregunta_o_caso,
                    solucion_aprendida,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
    except Exception:
        pass


def _registrar_log_revisor_830(
    conn,
    fecha_ejecucion: str,
    mes_evaluado: str,
    tipo_accion: str,
    id_pago: str,
    id_yape: int,
    habitacion: str,
    antes: Dict[str, Any],
    despues: Dict[str, Any],
    explicacion: str,
    estado_resolucion: str,
    wsp_enviado: int = 0,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO yape_revisor_830_log (
            fecha_ejecucion, mes_evaluado, tipo_accion, id_pago, id_yape, habitacion,
            antes_json, despues_json, explicacion, estado_resolucion, wsp_enviado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fecha_ejecucion,
            mes_evaluado,
            tipo_accion,
            str(id_pago or ""),
            int(id_yape or 0),
            str(habitacion or ""),
            json.dumps(antes, ensure_ascii=False),
            json.dumps(despues, ensure_ascii=False),
            explicacion,
            estado_resolucion,
            int(wsp_enviado),
        ),
    )
    return int(cur.lastrowid or 0)


def ejecutar_revisor_criterio_yape_830am(
    fecha_filtro: Optional[str] = None,
    forzar_extraccion: bool = False,
    enviar_wsp_real: bool = False,
) -> Dict[str, Any]:
    """
    Agente Revisor Inteligente de Yape (08:30 AM):
    - Regla inquebrantable: CERO OMISIONES (100% Drive Excel + 100% Correo Maelen).
    - Paso 1: Sincronización (si forzar_extraccion=True) y conciliación estándar.
    - Paso 2: Motor de Criterio:
        * Caso A (Obvio): Glosas mal tipeadas ('1;5pm', '140 140 1:55pm', '13:45pm') -> corrige SQLite y enlaza.
        * Caso B (Obvio): Yape grupal multi-habitación (ej. 301+302 con 1 Yape S/ 140 y monto duplicado) -> corrige SQLite y enlaza.
        * Caso C (Duda): Faltantes, sumas inconsistentes o ambigüedades -> consulta por WhatsApp y guarda en aprendizaje_compartido_hostal.
    - Paso 3: Despacho / Cola en wsp_recepcion_cola_envio ('✅ YAPES OK' o '⚠️ REVISIÓN YAPE 08:30 AM').
    """
    init_yape_tables()
    ahora_dt = datetime.now()
    ahora_iso = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")

    info_drive: Dict[str, Any] = {}
    info_maelen: Dict[str, Any] = {}
    if forzar_extraccion:
        info_drive = sincronizar_yapes_desde_drive_a_sqlite(forzar=True)
        info_maelen = sincronizar_transferencias_maelen_correo(forzar=True)

    comprobacion_std = comprobar_yapes_con_anotaciones_chicas(recalcular_todo=False)

    # Normalizar fecha_filtro (acepta DD/MM/YYYY, YYYY-MM-DD, MM/YYYY, YYYY-MM o None)
    filtro_raw = str(fecha_filtro or "").strip()
    if re.match(r"^\d{4}-\d{2}$", filtro_raw):
        p_f = filtro_raw.split("-")
        filtro_raw = f"{p_f[1]}/{p_f[0]}"
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", filtro_raw):
        p_f = filtro_raw.split("-")
        filtro_raw = f"{p_f[2]}/{p_f[1]}/{p_f[0]}"

    es_dia_filtro = bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", filtro_raw))
    es_mes_filtro = bool(re.match(r"^\d{1,2}/\d{4}$", filtro_raw))

    correcciones_aplicadas: List[Dict[str, Any]] = []
    consultas_pendientes: List[Dict[str, Any]] = []
    log_ids_run: List[int] = []

    with db_transaction() as conn:
        todas_yapes = [dict(r) for r in conn.execute("SELECT * FROM yape_conciliacion_hostal ORDER BY id DESC").fetchall()]

        if es_dia_filtro:
            dia_evaluado = _normalizar_fecha_dmy(filtro_raw)
            mes_evaluado = _extraer_mes_mmyyyy(dia_evaluado)
        elif es_mes_filtro:
            pm = filtro_raw.split("/")
            mes_evaluado = f"{int(pm[0]):02d}/{pm[1]}"
            dia_evaluado = ""
        else:
            dia_evaluado = ""
            ultima_f = ""
            for ry in todas_yapes:
                f_op = _normalizar_fecha_dmy(ry.get("fecha_operacion") or "")
                if f_op:
                    ultima_f = f_op
                    break
            mes_evaluado = _extraer_mes_mmyyyy(ultima_f) or ahora_dt.strftime("%m/%Y")

        def _en_periodo(f_dmy: str) -> bool:
            f_n = _normalizar_fecha_dmy(f_dmy)
            if not f_n:
                return False
            if dia_evaluado:
                return f_n == dia_evaluado
            return _extraer_mes_mmyyyy(f_n) == mes_evaluado

        yapes_periodo = [y for y in todas_yapes if _en_periodo(y.get("fecha_operacion") or "")]
        ymds_banco_all = [_dmy_a_ymd(_normalizar_fecha_dmy(y.get("fecha_operacion") or "")) for y in todas_yapes if y.get("fecha_operacion")]
        min_ymd_banco = min((ymd for ymd in ymds_banco_all if ymd), default="")

        pagos_chicas, _, _, _ = _cargar_anotaciones_recepcion(conn)

        # Mapa de estancia para conocer tarifa y tipo_pago de cada pago
        est_map: Dict[str, Dict[str, Any]] = {}
        for er in conn.execute(
            "SELECT estancia_uuid, id_timestamp_str, hab, huesped, tarifa, noches, pago_ini_yape, pago_post_yape, nro_op_yape FROM estancias"
        ).fetchall():
            ed = dict(er)
            est_map[str(ed["estancia_uuid"])] = ed

        # IDs ya cruzados en yape_conciliacion_hostal
        ids_ya_cruzados: set = set()
        for ry in todas_yapes:
            est_a = str(ry.get("estado_auditoria") or "").upper()
            if ry.get("id_estancia_calzada") and est_a in ("COMPROBADO", "CALZADO", "EXCEDENTE", "SALIDA"):
                for pid_part in str(ry.get("id_estancia_calzada") or "").split("|"):
                    if pid_part.strip():
                        ids_ya_cruzados.add(pid_part.strip())

        pagos_periodo = [
            p for p in pagos_chicas
            if _en_periodo(p.get("fecha_dmy") or "")
            and (not min_ymd_banco or _dmy_a_ymd(p.get("fecha_dmy") or "") >= min_ymd_banco)
        ]

        # Enriquecer pagos con tarifa de estancia y normalización
        for p in pagos_periodo:
            est = est_map.get(str(p.get("estancia_uuid") or ""), {})
            p["_tarifa_estancia"] = round(float(est.get("tarifa") or 0), 2)
            p["_est_nro_op_yape"] = str(est.get("nro_op_yape") or "")
            p["_norm"] = _normalizar_glosa_yape_con_criterio(p.get("nro_operacion") or "", p.get("monto"))

        yapes_resueltos_ids: set = set()
        pagos_resueltos_ids: set = set()

        # ------------------------------------------------------------------
        # CASO OBVIO B (PRIMERO): Yape Grupal Multi-Habitación (ej. 301 + 302)
        # Se evalúa antes que Caso A para capturar grupos donde una hab tiene
        # '140 140 1:55pm' y la otra tiene '70 1:45pm' o ya se enlazó en Paso 1.
        # ------------------------------------------------------------------
        from itertools import combinations as _iter_combinations

        yapes_candidatos_grupo = [
            y for y in yapes_periodo
            if str(y.get("estado_auditoria") or "").upper() in ("SIN ANOTAR", "PENDIENTE", "EXCEDENTE", "COMPROBADO")
            and round(float(y.get("monto_bcp") or 0), 2) > 0
            and not str(y.get("remitente_bcp") or "").upper().startswith("SALIDA")
        ]

        for y in yapes_candidatos_grupo:
            y_id = int(y["id"])
            if y_id in yapes_resueltos_ids:
                continue
            m_yape = round(float(y.get("monto_bcp") or 0), 2)
            f_yape = _normalizar_fecha_dmy(y.get("fecha_operacion") or "")
            dt_y = _dmy_a_date(f_yape)
            min_y = _hora_str_a_minutos(y.get("hora_operacion") or "")
            if min_y is None or dt_y is None:
                continue

            linked_ids = {
                s.strip() for s in str(y.get("id_estancia_calzada") or "").split("|") if s.strip()
            }
            # Si ya está COMPROBADO con 2+ pagos, no volver a rearmar grupo
            if str(y.get("estado_auditoria") or "").upper() == "COMPROBADO" and len(linked_ids) >= 2:
                continue

            cands_grupo: List[Dict[str, Any]] = []
            for p in pagos_periodo:
                pid = str(p.get("pago_id") or "").strip()
                if pid in pagos_resueltos_ids:
                    continue
                if pid in ids_ya_cruzados and pid not in linked_ids:
                    continue
                dt_p = _dmy_a_date(p.get("fecha_dmy") or "")
                if dt_p is None or abs((dt_y - dt_p).days) > 1:
                    continue

                norm = p["_norm"]
                min_p = norm.get("minutos")
                monto_g = norm.get("monto_glosa")
                cerca_hora = False
                diff_min_best = 9999
                if min_p is not None:
                    diff_24 = abs(min_y - min_p)
                    diff_12 = abs((min_y % 720) - (min_p % 720))
                    diff_min_best = min(diff_24, diff_12)
                    cerca_hora = diff_min_best <= 30
                ref_monto_total = monto_g is not None and abs(monto_g - m_yape) <= 0.01

                if pid in linked_ids or (cerca_hora and (ref_monto_total or round(float(p.get("monto") or 0), 2) < m_yape)):
                    p["_tmp_diff_min"] = diff_min_best
                    p["_tmp_ref_total"] = 1 if (ref_monto_total or abs(round(float(p.get("monto") or 0), 2) - m_yape) <= 0.01) else 0
                    p["_tmp_linked"] = 1 if pid in linked_ids else 0
                    cands_grupo.append(p)

            # Ordenar por prioridad: ya enlazado > referencia al monto total > cercanía horaria
            cands_grupo.sort(
                key=lambda px: (-px.get("_tmp_linked", 0), -px.get("_tmp_ref_total", 0), px.get("_tmp_diff_min", 9999))
            )
            cands_top = cands_grupo[:12]

            # Buscar subconjunto válido de 2..4 habitaciones distintas cuya suma de tarifas == m_yape
            sub_elegido: Optional[List[Dict[str, Any]]] = None
            tarifas_elegidas: List[float] = []
            if len(cands_top) >= 2:
                for sz in range(2, min(5, len(cands_top) + 1)):
                    for comb in _iter_combinations(cands_top, sz):
                        habs_comb = {str(px.get("hab") or "").strip() for px in comb}
                        if len(habs_comb) < sz:
                            continue
                        # Si el Yape ya tenía un pago enlazado en Paso 1, el subconjunto debe incluirlo
                        if linked_ids and not any(str(px.get("pago_id") or "") in linked_ids for px in comb):
                            continue
                        tars = [
                            px["_tarifa_estancia"] if px["_tarifa_estancia"] > 0 else round(float(px.get("monto") or 0), 2)
                            for px in comb
                        ]
                        if abs(round(sum(tars), 2) - m_yape) > 0.01:
                            continue
                        alguno_err = any(
                            abs(round(float(px.get("monto") or 0), 2) - m_yape) <= 0.01
                            or px["_norm"]["cambio"]
                            or str(px.get("pago_id") or "") not in ids_ya_cruzados
                            for px in comb
                        )
                        if alguno_err:
                            sub_elegido = list(comb)
                            tarifas_elegidas = tars
                            break
                    if sub_elegido is not None:
                        break

            if sub_elegido is not None:
                cands_grupo_ok = sub_elegido
                tarifas_esperadas = tarifas_elegidas
                habs_ord = sorted({str(p.get("hab") or "S/H").strip() for p in cands_grupo_ok})
                hab_tag = "+".join(habs_ord)
                m_txt = str(int(m_yape)) if abs(m_yape - int(m_yape)) < 0.005 else f"{m_yape:.2f}"
                h12_y = _hora_str_a_12h_corta(y.get("hora_operacion") or "")
                glosa_unif = f"{m_txt} {h12_y} [GRUPAL {hab_tag}]"

                antes_det = [
                    {
                        "pago_id": p["pago_id"],
                        "hab": p["hab"],
                        "monto": p["monto"],
                        "nro_operacion": p["nro_operacion"],
                    }
                    for p in cands_grupo_ok
                ]
                despues_det = []
                pids_grupo = []
                huespedes_grupo = []

                for p, tar_esp in zip(cands_grupo_ok, tarifas_esperadas):
                    pid = str(p["pago_id"])
                    pids_grupo.append(pid)
                    if p.get("huesped"):
                        huespedes_grupo.append(str(p["huesped"]).strip())
                    conn.execute(
                        "UPDATE pagos SET monto = ?, nro_operacion = ? WHERE pago_id = ?",
                        (tar_esp, glosa_unif, pid),
                    )
                    est_uuid = str(p.get("estancia_uuid") or "").strip()
                    if est_uuid:
                        tipo_p = str(p.get("concepto") or "").upper()
                        if "POSTERIOR" in tipo_p:
                            conn.execute(
                                "UPDATE estancias SET pago_post_yape = ?, nro_op_yape = ? WHERE estancia_uuid = ?",
                                (tar_esp, glosa_unif, est_uuid),
                            )
                        else:
                            conn.execute(
                                "UPDATE estancias SET pago_ini_yape = ?, nro_op_yape = ? WHERE estancia_uuid = ?",
                                (tar_esp, glosa_unif, est_uuid),
                            )
                    p["monto"] = tar_esp
                    p["nro_operacion"] = glosa_unif
                    pagos_resueltos_ids.add(pid)
                    ids_ya_cruzados.add(pid)
                    despues_det.append(
                        {
                            "pago_id": pid,
                            "hab": p["hab"],
                            "monto": tar_esp,
                            "nro_operacion": glosa_unif,
                        }
                    )

                obs_txt = f"Revisor 8:30am: Corrección grupal automática {hab_tag} (S/ {m_yape:.2f} = {' + '.join(f'{t:.0f}' for t in tarifas_esperadas)})"
                conn.execute(
                    """
                    UPDATE yape_conciliacion_hostal
                    SET id_estancia_calzada = ?,
                        hab_calzada = ?,
                        huesped_calzado = ?,
                        estado_auditoria = 'COMPROBADO',
                        observacion = ?,
                        updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (
                        "|".join(pids_grupo),
                        hab_tag,
                        " / ".join(dict.fromkeys(huespedes_grupo))[:120],
                        obs_txt,
                        y_id,
                    ),
                )
                yapes_resueltos_ids.add(y_id)

                lid = _registrar_log_revisor_830(
                    conn,
                    ahora_iso,
                    mes_evaluado,
                    "CORRECCION_GRUPAL_AUTOMATICA",
                    "+".join(pids_grupo),
                    y_id,
                    hab_tag,
                    {"pagos": antes_det, "yape_estado_antes": y.get("estado_auditoria")},
                    {"pagos": despues_det, "yape_estado_despues": "COMPROBADO"},
                    obs_txt,
                    "APLICADA_AUTO",
                    wsp_enviado=0,
                )
                log_ids_run.append(lid)
                _registrar_aprendizaje_yape(
                    conn,
                    ahora_iso,
                    "YAPE_GRUPAL_CORREGIDO",
                    f"Yape grupal S/ {m_yape:.2f} compartido entre Hab {hab_tag} con monto/glosa errónea",
                    obs_txt,
                    {"yape_id": y_id, "habs": hab_tag, "pagos": pids_grupo},
                )
                correcciones_aplicadas.append(
                    {
                        "tipo": "CORRECCION_GRUPAL_AUTOMATICA",
                        "hab": hab_tag,
                        "resumen": f"Hab {hab_tag}: ajustado grupal S/ {m_yape:.2f} ({'+'.join(f'{t:.0f}' for t in tarifas_esperadas)})",
                        "id_yape": y_id,
                        "id_pago": "+".join(pids_grupo),
                    }
                )

        # ------------------------------------------------------------------
        # CASO OBVIO A: Glosa mal tipeada ('1;5pm', '140 140 1:55pm', '13:45pm')
        # Incluye también pagos ya emparejados en Paso 1 cuya glosa siga mal tipeada.
        # ------------------------------------------------------------------
        yapes_disponibles_a = [
            y for y in yapes_periodo
            if int(y["id"]) not in yapes_resueltos_ids
            and str(y.get("estado_auditoria") or "").upper() in ("SIN ANOTAR", "PENDIENTE")
            and round(float(y.get("monto_bcp") or 0), 2) > 0
            and not str(y.get("remitente_bcp") or "").upper().startswith("SALIDA")
        ]

        for p in pagos_periodo:
            pid = str(p.get("pago_id") or "").strip()
            if pid in pagos_resueltos_ids:
                continue
            norm = p["_norm"]
            if not norm["cambio"] or norm["minutos"] is None:
                continue

            # Si ya fue cruzado en Paso 1, igual normalizar la glosa mal tipeada
            if pid in ids_ya_cruzados:
                y_linked = next(
                    (
                        ry for ry in yapes_periodo
                        if pid in {s.strip() for s in str(ry.get("id_estancia_calzada") or "").split("|") if s.strip()}
                    ),
                    None,
                )
                y_id = int(y_linked["id"]) if y_linked else 0
                nueva_glosa = norm["normalizada"]
                antes_glosa = str(p.get("nro_operacion") or "")
                if nueva_glosa != antes_glosa:
                    conn.execute("UPDATE pagos SET nro_operacion = ? WHERE pago_id = ?", (nueva_glosa, pid))
                    est_uuid = str(p.get("estancia_uuid") or "").strip()
                    if est_uuid and (not p["_est_nro_op_yape"] or p["_est_nro_op_yape"] == antes_glosa):
                        conn.execute("UPDATE estancias SET nro_op_yape = ? WHERE estancia_uuid = ?", (nueva_glosa, est_uuid))
                    obs_a = f"Revisor 8:30am: Glosa corregida '{antes_glosa}' -> '{nueva_glosa}' ({norm['motivo']})"
                    if y_id > 0:
                        conn.execute(
                            "UPDATE yape_conciliacion_hostal SET observacion = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                            (obs_a, y_id),
                        )
                    p["nro_operacion"] = nueva_glosa
                    pagos_resueltos_ids.add(pid)
                    lid = _registrar_log_revisor_830(
                        conn,
                        ahora_iso,
                        mes_evaluado,
                        "CORRECCION_OBVIA_APLICADA",
                        pid,
                        y_id,
                        str(p.get("hab") or ""),
                        {"nro_operacion": antes_glosa},
                        {"nro_operacion": nueva_glosa},
                        obs_a,
                        "APLICADA_AUTO",
                        wsp_enviado=0,
                    )
                    log_ids_run.append(lid)
                    _registrar_aprendizaje_yape(
                        conn,
                        ahora_iso,
                        "GLOSA_YAPE_NORMALIZADA",
                        f"Glosa mal tipeada '{antes_glosa}' en Hab {p.get('hab')}",
                        f"Normalizada automáticamente a '{nueva_glosa}' (Yape #{y_id})",
                        {"id_pago": pid, "id_yape": y_id, "motivo": norm["motivo"]},
                    )
                    correcciones_aplicadas.append(
                        {
                            "tipo": "CORRECCION_OBVIA_APLICADA",
                            "hab": str(p.get("hab") or ""),
                            "resumen": f"Hab {p.get('hab')}: glosa '{antes_glosa}' -> '{nueva_glosa}'",
                            "id_yape": y_id,
                            "id_pago": pid,
                        }
                    )
                continue

            monto_p = round(float(p.get("monto") or 0), 2)
            dt_p = _dmy_a_date(p.get("fecha_dmy") or "")
            if dt_p is None:
                continue

            cands_y = []
            for y in yapes_disponibles_a:
                if int(y["id"]) in yapes_resueltos_ids:
                    continue
                if abs(round(float(y.get("monto_bcp") or 0), 2) - monto_p) > 0.01:
                    continue
                dt_y = _dmy_a_date(y.get("fecha_operacion") or "")
                min_y = _hora_str_a_minutos(y.get("hora_operacion") or "")
                if dt_y is None or min_y is None or abs((dt_y - dt_p).days) > 1:
                    continue
                if abs(min_y - norm["minutos"]) <= 30 or abs((min_y % 720) - (norm["minutos"] % 720)) <= 30:
                    cands_y.append(y)

            if len(cands_y) == 1:
                y_match = cands_y[0]
                y_id = int(y_match["id"])
                nueva_glosa = norm["normalizada"]
                antes_glosa = str(p.get("nro_operacion") or "")

                conn.execute("UPDATE pagos SET nro_operacion = ? WHERE pago_id = ?", (nueva_glosa, pid))
                est_uuid = str(p.get("estancia_uuid") or "").strip()
                if est_uuid and (not p["_est_nro_op_yape"] or p["_est_nro_op_yape"] == antes_glosa):
                    conn.execute("UPDATE estancias SET nro_op_yape = ? WHERE estancia_uuid = ?", (nueva_glosa, est_uuid))

                obs_a = f"Revisor 8:30am: Glosa corregida '{antes_glosa}' -> '{nueva_glosa}' ({norm['motivo']})"
                conn.execute(
                    """
                    UPDATE yape_conciliacion_hostal
                    SET id_estancia_calzada = ?,
                        hab_calzada = ?,
                        huesped_calzado = ?,
                        estado_auditoria = 'COMPROBADO',
                        observacion = ?,
                        updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (pid, str(p.get("hab") or ""), str(p.get("huesped") or ""), obs_a, y_id),
                )
                yapes_resueltos_ids.add(y_id)
                pagos_resueltos_ids.add(pid)
                ids_ya_cruzados.add(pid)

                lid = _registrar_log_revisor_830(
                    conn,
                    ahora_iso,
                    mes_evaluado,
                    "CORRECCION_OBVIA_APLICADA",
                    pid,
                    y_id,
                    str(p.get("hab") or ""),
                    {"nro_operacion": antes_glosa},
                    {"nro_operacion": nueva_glosa},
                    obs_a,
                    "APLICADA_AUTO",
                    wsp_enviado=0,
                )
                log_ids_run.append(lid)
                _registrar_aprendizaje_yape(
                    conn,
                    ahora_iso,
                    "GLOSA_YAPE_NORMALIZADA",
                    f"Glosa mal tipeada '{antes_glosa}' en Hab {p.get('hab')}",
                    f"Normalizada automáticamente a '{nueva_glosa}' y enlazada a Yape #{y_id}",
                    {"id_pago": pid, "id_yape": y_id, "motivo": norm["motivo"]},
                )
                correcciones_aplicadas.append(
                    {
                        "tipo": "CORRECCION_OBVIA_APLICADA",
                        "hab": str(p.get("hab") or ""),
                        "resumen": f"Hab {p.get('hab')}: glosa '{antes_glosa}' -> '{nueva_glosa}'",
                        "id_yape": y_id,
                        "id_pago": pid,
                    }
                )

        # ------------------------------------------------------------------
        # CASO C (DUDA / AMBIGUO): Registrar consultas humanas + aprendizaje
        # ------------------------------------------------------------------
        yapes_final_periodo = [
            dict(r) for r in conn.execute("SELECT * FROM yape_conciliacion_hostal ORDER BY id DESC").fetchall()
            if _en_periodo(r["fecha_operacion"] or "")
        ]

        # Reconstruir cruzados tras correcciones
        ids_cruzados_final: set = set()
        for ry in yapes_final_periodo:
            if ry.get("id_estancia_calzada") and str(ry.get("estado_auditoria") or "").upper() in ("COMPROBADO", "CALZADO", "EXCEDENTE", "SALIDA"):
                for pid_part in str(ry.get("id_estancia_calzada") or "").split("|"):
                    if pid_part.strip():
                        ids_cruzados_final.add(pid_part.strip())

        sueltos_banco_final = [
            y for y in yapes_final_periodo
            if str(y.get("estado_auditoria") or "").upper() in ("SIN ANOTAR", "PENDIENTE", "EXCEDENTE")
            and round(float(y.get("monto_bcp") or 0), 2) > 0
            and not str(y.get("remitente_bcp") or "").upper().startswith("SALIDA")
        ]
        faltantes_banco_final = [
            p for p in pagos_periodo
            if str(p.get("pago_id") or "").strip() not in ids_cruzados_final
        ]

        for y in sueltos_banco_final:
            y_id = int(y["id"])
            h12 = _hora_str_a_12h_corta(y.get("hora_operacion") or "")
            preg = (
                f"Yape en banco sin cuadrar: S/ {float(y.get('monto_bcp') or 0):.2f} de "
                f"{y.get('remitente_bcp') or 'CLIENTE'} el {y.get('fecha_operacion')} a las {h12} "
                f"(estado: {y.get('estado_auditoria')}). ¿A qué habitación o consumo corresponde?"
            )
            lid = _registrar_log_revisor_830(
                conn,
                ahora_iso,
                mes_evaluado,
                "CONSULTA_HUMANA_WSP",
                str(y.get("id_estancia_calzada") or ""),
                y_id,
                str(y.get("hab_calzada") or ""),
                {"estado_auditoria": y.get("estado_auditoria"), "monto_bcp": y.get("monto_bcp")},
                {},
                preg,
                "PENDIENTE_RESPUESTA",
                wsp_enviado=0,
            )
            log_ids_run.append(lid)
            _registrar_aprendizaje_yape(
                conn,
                ahora_iso,
                "CONSULTA_YAPE_SUELTO",
                preg,
                "Pendiente confirmación humana vía WhatsApp",
                {"id_yape": y_id, "monto": y.get("monto_bcp"), "fecha": y.get("fecha_operacion")},
            )
            consultas_pendientes.append({"tipo": "BANCO_SUELTO", "id_yape": y_id, "pregunta": preg})

        for p in faltantes_banco_final:
            pid = str(p.get("pago_id") or "")
            preg = (
                f"Faltante en banco: Hab. {p.get('hab') or 'S/H'} ({p.get('huesped') or ''}) "
                f"anotó S/ {float(p.get('monto') or 0):.2f} el {p.get('fecha_dmy')} "
                f"(glosa '{p.get('nro_operacion') or '-'}') pero no figura Yape equivalente."
            )
            lid = _registrar_log_revisor_830(
                conn,
                ahora_iso,
                mes_evaluado,
                "CONSULTA_HUMANA_WSP",
                pid,
                0,
                str(p.get("hab") or ""),
                {"monto": p.get("monto"), "nro_operacion": p.get("nro_operacion")},
                {},
                preg,
                "PENDIENTE_RESPUESTA",
                wsp_enviado=0,
            )
            log_ids_run.append(lid)
            _registrar_aprendizaje_yape(
                conn,
                ahora_iso,
                "CONSULTA_FALTANTE_BANCO",
                preg,
                "Pendiente confirmación humana vía WhatsApp",
                {"id_pago": pid, "hab": p.get("hab"), "monto": p.get("monto")},
            )
            consultas_pendientes.append({"tipo": "FALTANTE_BANCO", "id_pago": pid, "pregunta": preg})

        # ------------------------------------------------------------------
        # VERIFICACIÓN DE INTEGRIDAD: CERO OMISIONES
        # ------------------------------------------------------------------
        total_extraidos = len(yapes_final_periodo)
        cruzados_cnt = sum(
            1 for y in yapes_final_periodo
            if str(y.get("estado_auditoria") or "").upper() in ("COMPROBADO", "CALZADO")
        )
        sueltos_cnt = sum(
            1 for y in yapes_final_periodo
            if str(y.get("estado_auditoria") or "").upper() in ("SIN ANOTAR", "PENDIENTE", "EXCEDENTE")
            and not str(y.get("remitente_bcp") or "").upper().startswith("SALIDA")
        )
        salidas_cnt = sum(
            1 for y in yapes_final_periodo
            if str(y.get("estado_auditoria") or "").upper() == "SALIDA"
            or str(y.get("remitente_bcp") or "").upper().startswith("SALIDA")
        )
        integridad_sin_omisiones = (total_extraidos == (cruzados_cnt + sueltos_cnt + salidas_cnt))

        maelen_all = [dict(r) for r in conn.execute("SELECT * FROM maelen_transferencias_correo").fetchall()]
        maelen_periodo = [m for m in maelen_all if _en_periodo(m.get("fecha_operacion") or "")]

        # ------------------------------------------------------------------
        # PASO 3: MENSAJE WHATSAPP (wsp_recepcion_cola_envio)
        # ------------------------------------------------------------------
        periodo_lbl = dia_evaluado if dia_evaluado else mes_evaluado
        if len(consultas_pendientes) == 0 and sueltos_cnt == 0:
            lineas_corr = "\n".join(f"🔧 {c['resumen']}" for c in correcciones_aplicadas)
            msg_wsp = (
                f"✅ *YAPES OK (08:30 AM)* — {periodo_lbl}\n"
                f"📥 Reporte Yape: {total_extraidos} movs (0 omitidos)\n"
                f"🏦 Correo Maelen: {len(maelen_periodo)} movs (0 omitidos)\n"
                f"🔗 Cruzados: {cruzados_cnt} | Salidas: {salidas_cnt}"
            )
            if lineas_corr:
                msg_wsp += f"\n\n*Correcciones automáticas aplicadas:*\n{lineas_corr}"
            tipo_wsp = "YAPES_OK"
        else:
            lineas_corr = "\n".join(f"🔧 {c['resumen']}" for c in correcciones_aplicadas[:5])
            lineas_cons = "\n".join(f"❓ {c['pregunta']}" for c in consultas_pendientes[:8])
            if len(consultas_pendientes) > 8:
                lineas_cons += f"\n... y {len(consultas_pendientes) - 8} caso(s) más en el panel."
            msg_wsp = (
                f"⚠️ *REVISIÓN YAPE 08:30 AM — CONSULTA / AYUDA* ({periodo_lbl})\n"
                f"📥 Reporte: {total_extraidos} (0 omitidos) | 🏦 Maelen: {len(maelen_periodo)}\n"
            )
            if lineas_corr:
                msg_wsp += f"\n*Correcciones obvias ya aplicadas:*\n{lineas_corr}\n"
            msg_wsp += f"\n*Casos con duda para confirmar:*\n{lineas_cons}"
            tipo_wsp = "CONSULTA_AYUDA"

        id_cola = ""
        estado_wsp = "EN_COLA_PRUEBA" if MODO_PRUEBA else "EN_COLA"
        enviado_en = ""
        if enviar_wsp_real and not MODO_PRUEBA:
            res_env = _despachar_alerta_wsp_fisico(TEL_ALERTA_RECEPCION, msg_wsp)
            estado_wsp = "ENVIADO_WSP" if res_env.get("ok") else "ERROR_ENVIO"
            enviado_en = ahora_iso if res_env.get("ok") else ""

        wsp_ok = 0
        try:
            cur_wsp = conn.execute(
                """
                INSERT INTO wsp_recepcion_cola_envio (
                    destino_telefono, cliente_nombre, nro_comprobante,
                    texto, archivo_ruta, estado, creado_en, enviado_en
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    TEL_ALERTA_RECEPCION,
                    "REVISOR YAPE 08:30 AM",
                    f"REV-830-{periodo_lbl}",
                    msg_wsp,
                    estado_wsp,
                    ahora_iso,
                    enviado_en,
                ),
            )
            id_cola = str(cur_wsp.lastrowid or "")
            wsp_ok = 1
        except Exception:
            wsp_ok = 0

        if wsp_ok and log_ids_run:
            placeholders = ",".join("?" for _ in log_ids_run)
            conn.execute(f"UPDATE yape_revisor_830_log SET wsp_enviado = 1 WHERE id IN ({placeholders})", log_ids_run)
        elif wsp_ok and not log_ids_run:
            _registrar_log_revisor_830(
                conn,
                ahora_iso,
                mes_evaluado,
                "REVISION_OK_SIN_NOVEDADES",
                "",
                0,
                "",
                {},
                {"cruzados": cruzados_cnt, "totalExtraidos": total_extraidos},
                msg_wsp,
                "OK_VERIFICADO",
                wsp_enviado=1,
            )

    return {
        "ok": True,
        "fechaEjecucion": ahora_iso,
        "periodoEvaluado": dia_evaluado if dia_evaluado else mes_evaluado,
        "mesEvaluado": mes_evaluado,
        "integridadSinOmisiones": integridad_sin_omisiones,
        "totalExtraidosReporte": total_extraidos,
        "cruzados": cruzados_cnt,
        "sueltos": sueltos_cnt,
        "salidas": salidas_cnt,
        "totalMaelenCorreo": len(maelen_periodo),
        "correccionesAplicadas": correcciones_aplicadas,
        "consultasPendientes": consultas_pendientes,
        "tipoMensajeWsp": tipo_wsp,
        "estadoWsp": estado_wsp,
        "idColaWsp": id_cola,
        "mensajeWsp": msg_wsp,
        "driveSync": info_drive,
        "maelenSync": info_maelen,
        "comprobacionEstandar": comprobacion_std,
    }


def gestionar_accion_yape_sqlite(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Permite forzar recálculo o sincronización (Drive, Correo Maelen, Mensajero WSP 5m o Revisor 8:30am) desde el frontend."""
    accion = str(payload.get("accion") or "recalcular").lower()
    if accion == "sincronizar_drive":
        res_d = sincronizar_yapes_desde_drive_a_sqlite(forzar=True)
        res_m = sincronizar_transferencias_maelen_correo(forzar=True)
        return {"ok": True, "drive": res_d, "maelen": res_m}
    if accion == "sincronizar_maelen":
        return sincronizar_transferencias_maelen_correo(forzar=True)
    if accion == "verificar_alertas_5min":
        return verificar_y_alertar_yapes_sin_anotar_5min(
            ventana_horas=int(payload.get("ventana_horas") or 72),
            enviar_wsp_real=bool(payload.get("enviar_wsp_real", False)),
            incluir_reciente_supervision=bool(payload.get("incluir_reciente_supervision", False)),
        )
    if accion == "revisor_830am":
        return ejecutar_revisor_criterio_yape_830am(
            fecha_filtro=payload.get("fecha_filtro"),
            forzar_extraccion=bool(payload.get("forzar_extraccion", False)),
            enviar_wsp_real=bool(payload.get("enviar_wsp_real", False)),
        )
    res = comprobar_yapes_con_anotaciones_chicas(recalcular_todo=True)
    return {"ok": True, "comprobacion": res}


def generar_macro_macrodroid() -> Dict[str, Any]:
    return {"ok": True, "endpoint": "/api/yape/webhook"}


registrar_yape_entrante_sqlite = registrar_notificacion_celular_sqlite
conciliar_yapes_sqlite = comprobar_yapes_con_anotaciones_chicas
