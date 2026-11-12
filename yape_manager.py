#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor de Yapes desde Google Drive -> SQLite (`yape_conciliacion_hostal`)
y Comprobador de Solo Lectura contra lo que anotan las chicas en Recepción.
Hotel El Óvalo Mariátegui — C:\\Recepcion_Ovalo_Local\\yape_manager.py

REGLA ESTRICTA DE SEGURIDAD:
- ESTE MÓDULO ES 100% NO INTRUSIVO (SOLO LECTURA SOBRE LA WEBAPP LOCAL).
- JAMÁS edita, modifica ni autocompleta `estancias`, `pagos`, `ventas_tienda` ni los formularios
  de las chicas de recepción (`CheckInRapido`, `PagosPosteriores`, `VentaTienda`).
- Únicamente extrae los Yapes desde la carpeta de Google Drive (`1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe`,
  la misma que usa `auditor_yape.py`), los guarda en `yape_conciliacion_hostal` en SQLite,
  comprueba cada Yape contra lo que anotaron las chicas (detectando también los EXCEDENTES / SOBRANTES),
  y muestra el mensaje informativo de "Yape recibido / comprobado".
"""

from __future__ import annotations

import io
import os
import re
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import obtener_ruta_credenciales
from database import db_transaction, get_connection

# Misma carpeta de Google Drive donde llegan los reportes de Yape (usada por auditor_yape.py)
FOLDER_ID_YAPE_DRIVE = "1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe"
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]

_drive_sync_lock = threading.Lock()
_ultimo_drive_check_ts: float = 0.0
_ultimo_archivo_drive_id: str = ""
_ultimo_archivo_drive_mtime: str = ""
_ultimo_archivo_drive_nombre: str = ""


def init_yape_tables() -> None:
    conn = get_connection()
    try:
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
            """
        )
        conn.commit()
    finally:
        conn.close()


def _hora_str_a_minutos(hora_str: str) -> Optional[int]:
    if not hora_str:
        return None
    s = str(hora_str).strip().lower().replace(".", ":")
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm|a\s*m|p\s*m)?", s)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(4) or "").replace(" ", "")
    if ampm:
        if "p" in ampm and h < 12:
            h += 12
        elif "a" in ampm and h == 12:
            h = 0
    return h * 60 + minute


def _hora_str_a_12h_corta(hora_str: str) -> str:
    mins = _hora_str_a_minutos(hora_str)
    if mins is None:
        return str(hora_str or "").strip()
    h = mins // 60
    m = mins % 60
    suf = "pm" if h >= 12 else "am"
    h12 = h - 12 if h > 12 else (12 if h == 0 else h)
    return f"{h12}:{m:02d}{suf}"


def _similitud_nombres(a: str, b: str) -> float:
    na = re.sub(r"[^A-Z0-9\s]", "", str(a or "").upper()).strip()
    nb = re.sub(r"[^A-Z0-9\s]", "", str(b or "").upper()).strip()
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.92
    ta = {t for t in na.split() if len(t) > 2}
    tb = {t for t in nb.split() if len(t) > 2}
    if ta and tb:
        inter = len(ta.intersection(tb))
        if inter >= 2:
            return 0.90
        if inter == 1 and (len(ta) == 1 or len(tb) == 1):
            return 0.78
    return SequenceMatcher(None, na, nb).ratio()


def comprobar_yapes_con_anotaciones_chicas(conn=None) -> Dict[str, int]:
    """
    Comprueba en SOLO LECTURA cada Yape de `yape_conciliacion_hostal` contra lo que
    anotaron las chicas en `pagos`, `estancias` y `ventas_tienda`.
    - NUNCA modifica `pagos`, `estancias` ni `ventas_tienda`.
    - Solo actualiza el estado en `yape_conciliacion_hostal`:
      * 'COMPROBADO' (cuando coincide con lo anotado por las chicas en recepción o tienda)
      * 'EXCEDENTE' (cuando el Yape recibido supera el monto anotado por las chicas para esa operación)
      * 'SIN ANOTAR' (cuando el Yape entró al banco pero las chicas aún no lo han anotado)
    """
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True

    comprobados_cnt = 0
    excedentes_cnt = 0
    try:
        pendientes = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                WHERE estado_auditoria IN ('PENDIENTE', 'SIN ANOTAR') AND monto_bcp > 0
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
            WHERE id_estancia_calzada != '' AND estado_auditoria IN ('COMPROBADO', 'CALZADO', 'EXCEDENTE')
            """
        ).fetchall():
            for part_id in str(r["id_estancia_calzada"] or "").split("|"):
                if part_id.strip():
                    ya_usados.add(part_id.strip())

        pagos_chicas = [
            dict(r)
            for r in conn.execute(
                """
                SELECT pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, concepto, nro_operacion
                FROM pagos
                WHERE upper(medio_pago) LIKE '%YAPE%' OR upper(medio_pago) LIKE '%PLIN%'
                ORDER BY rowid DESC LIMIT 1000
                """
            ).fetchall()
        ]

        ventas_chicas = [
            dict(r)
            for r in conn.execute(
                """
                SELECT venta_uuid, fecha_dmy, fecha_iso, producto, cantidad, hab, personal, subtotal, nro_op_yape
                FROM ventas_tienda
                WHERE upper(metodo_pago) LIKE '%YAPE%'
                ORDER BY rowid DESC LIMIT 600
                """
            ).fetchall()
        ]

        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for y in pendientes:
            y_id = y["id"]
            y_fecha = str(y["fecha_operacion"] or "").strip()
            y_hora = str(y["hora_operacion"] or "").strip()
            y_min = _hora_str_a_minutos(y_hora)
            y_monto = round(float(y["monto_bcp"] or 0), 2)
            y_rem = str(y["remitente_bcp"] or "").strip().upper()
            y_cod = str(y["nro_operacion"] or "").strip()

            mejor_match = None
            mejor_score = -1.0

            # 1. Comparar contra pagos de habitaciones anotados por las chicas
            for p in pagos_chicas:
                pid = str(p["pago_id"] or "").strip()
                if not pid or pid in ya_usados:
                    continue

                f_iso = str(p["fecha_iso"] or "").strip()
                f_dmy_pago = f_iso.split(" ")[0].strip() if " " in f_iso else f_iso[:10]
                if f_dmy_pago != y_fecha:
                    continue

                p_monto = round(float(p["monto"] or 0), 2)
                nro_op_p = str(p["nro_operacion"] or "").strip()
                huesped_p = str(p["huesped"] or "").strip().upper()
                hab_p = str(p["hab"] or "").strip()

                p_min = _hora_str_a_minutos(nro_op_p) or _hora_str_a_minutos(f_iso)
                diff_m = abs(y_min - p_min) if (y_min is not None and p_min is not None) else 999
                sim_nom = _similitud_nombres(y_rem, huesped_p)

                # Caso A: Monto exacto igual al anotado por las chicas
                if abs(p_monto - y_monto) <= 0.05:
                    score = 50.0
                    if y_cod and len(y_cod) >= 3 and y_cod in nro_op_p:
                        score += 45.0
                    if diff_m <= 3:
                        score += 40.0
                    elif diff_m <= 15:
                        score += 28.0
                    elif diff_m <= 60:
                        score += 12.0
                    elif diff_m > 240:
                        score -= 25.0

                    if sim_nom >= 0.75:
                        score += 35.0
                    elif sim_nom >= 0.50:
                        score += 15.0

                    if score >= 60.0 and score > mejor_score:
                        mejor_score = score
                        mejor_match = {
                            "id_calzado": pid,
                            "hab": hab_p or "S/H",
                            "huesped": huesped_p,
                            "estado": "COMPROBADO",
                            "obs": f"✅ Comprobado con lo anotado por recepción: Hab. {hab_p} — {huesped_p} (S/ {p_monto:.2f}, glosa: '{nro_op_p or '-'}')",
                        }

                # Caso B: Yape con EXCEDENTE sobre lo anotado por las chicas
                elif (diff_m <= 5 and sim_nom >= 0.5) or (diff_m <= 2) or (sim_nom >= 0.80 and diff_m <= 30):
                    diff_monto = round(y_monto - p_monto, 2)
                    score = 45.0 + (30.0 if diff_m <= 3 else 10.0) + (25.0 if sim_nom >= 0.75 else 0.0)
                    if score >= 65.0 and score > mejor_score:
                        mejor_score = score
                        signo = f"+S/ {diff_monto:.2f} EXCEDENTE" if diff_monto > 0 else f"-S/ {abs(diff_monto):.2f} MENOS"
                        mejor_match = {
                            "id_calzado": pid,
                            "hab": hab_p or "S/H",
                            "huesped": huesped_p,
                            "estado": "EXCEDENTE",
                            "obs": f"⚠️ Diferencia/Excedente ({signo}): Yape real S/ {y_monto:.2f} vs Anotado por chicas S/ {p_monto:.2f} en Hab. {hab_p} ({huesped_p})",
                        }

            # 1b. Si no hubo match individual, verificar pagos grupales que comparten la misma glosa nro_operacion (ej. '130 20:18' o '3705 11:28')
            if not mejor_match:
                grupos_glosa: Dict[str, List[Dict[str, Any]]] = {}
                for p in pagos_chicas:
                    pid = str(p["pago_id"] or "").strip()
                    if not pid or pid in ya_usados:
                        continue
                    f_iso = str(p["fecha_iso"] or "").strip()
                    f_dmy_pago = f_iso.split(" ")[0].strip() if " " in f_iso else f_iso[:10]
                    if f_dmy_pago != y_fecha:
                        continue
                    glosa = str(p["nro_operacion"] or "").strip()
                    if glosa:
                        grupos_glosa.setdefault(glosa, []).append(p)
                for glosa, lista_g in grupos_glosa.items():
                    if len(lista_g) < 2:
                        continue
                    suma_g = round(sum(float(x["monto"] or 0) for x in lista_g), 2)
                    if abs(suma_g - y_monto) <= 0.1:
                        ids_joined = "|".join(str(x["pago_id"]).strip() for x in lista_g)
                        habs_joined = ", ".join(sorted({str(x["hab"] or "").strip() for x in lista_g if str(x["hab"] or "").strip()}))
                        huespedes_joined = " / ".join(sorted({str(x["huesped"] or "").strip().upper() for x in lista_g if str(x["huesped"] or "").strip()}))[:80]
                        mejor_match = {
                            "id_calzado": ids_joined,
                            "hab": habs_joined or "GRUPAL",
                            "huesped": huespedes_joined,
                            "estado": "COMPROBADO",
                            "obs": f"✅ Comprobado (Pago Grupal {len(lista_g)} habs: {habs_joined} — S/ {suma_g:.2f}, glosa: '{glosa}')",
                        }
                        break

            # 2. Comparar contra ventas de tienda anotadas por las chicas
            if not mejor_match:
                for vt in ventas_chicas:
                    vid = f"TIENDA-{vt['venta_uuid']}"
                    if vid in ya_usados:
                        continue
                    if str(vt["fecha_dmy"] or "").strip() != y_fecha:
                        continue
                    vt_monto = round(float(vt["subtotal"] or 0), 2)
                    vt_min = _hora_str_a_minutos(str(vt["nro_op_yape"] or "")) or _hora_str_a_minutos(str(vt["fecha_iso"] or ""))
                    diff_m = abs(y_min - vt_min) if (y_min is not None and vt_min is not None) else 999

                    if abs(vt_monto - y_monto) <= 0.05:
                        score = 55.0 + (35.0 if diff_m <= 20 else 0.0)
                        if score >= 60.0 and score > mejor_score:
                            mejor_score = score
                            mejor_match = {
                                "id_calzado": vid,
                                "hab": str(vt["hab"] or "TIENDA").strip() or "TIENDA",
                                "huesped": f"TIENDA ({vt['personal'] or 'Recepción'}): {vt['cantidad']}x {vt['producto']}",
                                "estado": "COMPROBADO",
                                "obs": f"✅ Comprobado con Tienda: {vt['cantidad']}x {vt['producto']} (S/ {vt_monto:.2f})",
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
                    if y_min is not None and e_min is not None and abs(y_min - e_min) <= 30:
                        sug_txt = f" | 💡 Coincide en hora/monto con Hab. {er['hab']} ({er['huesped']}, hora {er['hora_registro']}, tarifa S/ {float(er['tarifa'] or 0):.2f} sin pago Yape anotado)"
                        break
                if sug_txt and sug_txt not in str(y.get("observacion") or ""):
                    obs_base = str(y.get("observacion") or "").split(" | 💡")[0].strip()
                    conn.execute(
                        "UPDATE yape_conciliacion_hostal SET observacion = ?, updated_at = ? WHERE id = ?",
                        (obs_base + sug_txt, ahora_iso, y_id),
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
    donde llegan los reportes de Yape, extrae los movimientos nuevos hacia la tabla
    `yape_conciliacion_hostal` en SQLite, y los comprueba contra lo que anotaron las chicas.
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
                pageSize=6,
            )
            .execute()
        )
        items = results.get("files", [])
        if not items:
            return {"ok": True, "sinArchivosEnDrive": True, "nuevosYapes": 0}

        top_file = items[0]
        if (
            not forzar
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

        mejor_archivo = None
        mejor_fecha_max = None
        mejor_df = None

        for item in items[:2]:
            try:
                dl_url = f"https://www.googleapis.com/drive/v3/files/{item['id']}?alt=media"
                resp = requests.get(dl_url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=20)
                if resp.status_code == 200:
                    fh_temp = io.BytesIO(resp.content)
                    df_temp = pd.read_excel(fh_temp, skiprows=4)
                    if len(df_temp.columns) >= 6:
                        col_fec = df_temp.columns[5]
                        fechas_s = pd.to_datetime(df_temp[col_fec], format="%d/%m/%Y %H:%M:%S", errors="coerce").dropna()
                        if len(fechas_s) > 0:
                            max_f = fechas_s.max()
                            if mejor_fecha_max is None or max_f > mejor_fecha_max:
                                mejor_fecha_max = max_f
                                mejor_archivo = item
                                mejor_df = df_temp
            except Exception:
                continue

        if mejor_archivo is None or mejor_df is None:
            return {"ok": False, "error": "No se pudo leer el Excel de Yape desde Google Drive."}

        _ultimo_archivo_drive_id = top_file["id"]
        _ultimo_archivo_drive_mtime = top_file.get("modifiedTime") or ""
        _ultimo_archivo_drive_nombre = mejor_archivo["name"]

        cols = mejor_df.columns.tolist()
        df_banco = mejor_df.rename(
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

        if mejor_fecha_max is not None:
            corte_dt = mejor_fecha_max - pd.Timedelta(days=15)
            df_banco = df_banco[df_banco["Fecha_dt"] >= corte_dt].reset_index(drop=True)

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
                existentes.add((d_r["fecha_operacion"], d_r["hora_operacion"], d_r["remitente_bcp"], float(d_r["m"] or 0)))
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
                        if str(fc["fecha_operacion"] or "") != fecha_op:
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
                obs = f"Extraído de Drive ({mejor_archivo['name']})"

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

            res_comp = comprobar_yapes_con_anotaciones_chicas(conn)

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


def obtener_resumen_yapes_sqlite(fecha_dmy: str = "", limite: int = 100, forzar_drive: bool = False) -> Dict[str, Any]:
    """
    Sincroniza desde la carpeta de Google Drive si corresponde, comprueba en SOLO LECTURA
    contra lo que anotaron las chicas en SQLite y devuelve el listado + totales + alertas.
    """
    init_yape_tables()
    if forzar_drive:
        info_drive = sincronizar_yapes_desde_drive_a_sqlite(forzar=True)
    else:
        threading.Thread(target=sincronizar_yapes_desde_drive_a_sqlite, kwargs={"forzar": False}, daemon=True).start()
        info_drive = {"archivoDrive": _ultimo_archivo_drive_nombre}

    fecha_filtro = str(fecha_dmy or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", fecha_filtro):
        p = fecha_filtro.split("-")
        fecha_filtro = f"{p[2]}/{p[1]}/{p[0]}"

    conn = get_connection()
    try:
        comprobar_yapes_con_anotaciones_chicas(conn)
        conn.commit()

        ultima_fecha_row = conn.execute(
            "SELECT fecha_operacion FROM yape_conciliacion_hostal WHERE id > 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        ultima_fecha_bd = str(ultima_fecha_row["fecha_operacion"] or "") if ultima_fecha_row else ""

        es_todos = fecha_filtro.upper() in ("TODOS", "ALL", "RECIENTES")
        if es_todos:
            filas_raw = conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                ORDER BY id DESC LIMIT ?
                """,
                (limite,),
            ).fetchall()
        elif fecha_filtro:
            filas_raw = conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                WHERE fecha_operacion = ?
                ORDER BY id DESC LIMIT ?
                """,
                (fecha_filtro, limite),
            ).fetchall()
        else:
            fecha_filtro = ultima_fecha_bd or datetime.now().strftime("%d/%m/%Y")
            filas_raw = conn.execute(
                """
                SELECT * FROM yape_conciliacion_hostal
                WHERE fecha_operacion = ?
                ORDER BY id DESC LIMIT ?
                """,
                (fecha_filtro, limite),
            ).fetchall()

        # Conjunto global de IDs de pagos/ventas que ya están cruzados con algún Yape en SQLite
        ids_ya_cruzados: set = set()
        mapa_yape_por_id_calzado: Dict[str, Dict[str, Any]] = {}
        for r_calz in conn.execute(
            """
            SELECT id, fecha_operacion, hora_operacion, remitente_bcp, monto_bcp, id_estancia_calzada, estado_auditoria
            FROM yape_conciliacion_hostal
            WHERE id_estancia_calzada != '' AND estado_auditoria IN ('COMPROBADO', 'CALZADO', 'EXCEDENTE')
            """
        ).fetchall():
            d_calz = dict(r_calz)
            for pid_part in str(d_calz.get("id_estancia_calzada") or "").split("|"):
                pid_clean = pid_part.strip()
                if pid_clean:
                    ids_ya_cruzados.add(pid_clean)
                    mapa_yape_por_id_calzado[pid_clean] = d_calz

        # Consultar lo anotado por las chicas en `pagos` y `ventas_tienda`
        fechas_consulta = set()
        if not es_todos and fecha_filtro:
            fechas_consulta.add(fecha_filtro)
        else:
            for r_f in filas_raw:
                if r_f["fecha_operacion"]:
                    fechas_consulta.add(str(r_f["fecha_operacion"]).strip())
            fechas_consulta.add(datetime.now().strftime("%d/%m/%Y"))

        anotados_chicas: List[Dict[str, Any]] = []
        sueltos_anotados: List[Dict[str, Any]] = []

        pagos_rows = conn.execute(
            """
            SELECT pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, concepto, nro_operacion
            FROM pagos
            WHERE upper(medio_pago) LIKE '%YAPE%' OR upper(medio_pago) LIKE '%PLIN%'
            ORDER BY rowid DESC LIMIT 400
            """
        ).fetchall()
        for pr in pagos_rows:
            f_iso = str(pr["fecha_iso"] or "").strip()
            f_dmy_p = f_iso.split(" ")[0].strip() if " " in f_iso else f_iso[:10]
            if f_dmy_p not in fechas_consulta:
                continue
            pid = str(pr["pago_id"] or "").strip()
            hora_p = f_iso.split(" ")[1].strip() if " " in f_iso else ""
            cruzado_flag = pid in ids_ya_cruzados
            item_anot = {
                "id": pid,
                "origen": "Habitación",
                "fecha_operacion": f_dmy_p,
                "hora_operacion": hora_p,
                "hora12": _hora_str_a_12h_corta(str(pr["nro_operacion"] or "") or hora_p),
                "hab": str(pr["hab"] or "S/H").strip(),
                "huesped": str(pr["huesped"] or "").strip(),
                "concepto": str(pr["concepto"] or "Pago").strip(),
                "glosa": str(pr["nro_operacion"] or "").strip(),
                "monto": round(float(pr["monto"] or 0), 2),
                "cruzado": cruzado_flag,
            }
            anotados_chicas.append(item_anot)
            if not cruzado_flag:
                sueltos_anotados.append(item_anot)

        ventas_rows = conn.execute(
            """
            SELECT venta_uuid, fecha_dmy, fecha_iso, producto, cantidad, hab, personal, subtotal, nro_op_yape
            FROM ventas_tienda
            WHERE upper(metodo_pago) LIKE '%YAPE%'
            ORDER BY rowid DESC LIMIT 300
            """
        ).fetchall()
        for vr in ventas_rows:
            f_dmy_v = str(vr["fecha_dmy"] or "").strip()
            if f_dmy_v not in fechas_consulta:
                continue
            vid = f"TIENDA-{vr['venta_uuid']}"
            f_iso_v = str(vr["fecha_iso"] or "").strip()
            hora_v = f_iso_v.split(" ")[1].strip() if " " in f_iso_v else ""
            cruzado_flag = vid in ids_ya_cruzados
            item_vt = {
                "id": vid,
                "origen": "Tienda",
                "fecha_operacion": f_dmy_v,
                "hora_operacion": hora_v,
                "hora12": _hora_str_a_12h_corta(str(vr["nro_op_yape"] or "") or hora_v),
                "hab": str(vr["hab"] or "TIENDA").strip() or "TIENDA",
                "huesped": f"TIENDA ({vr['personal'] or 'Recepción'}): {vr['cantidad']}x {vr['producto']}",
                "concepto": f"{vr['cantidad']}x {vr['producto']}",
                "glosa": str(vr["nro_op_yape"] or "").strip(),
                "monto": round(float(vr["subtotal"] or 0), 2),
                "cruzado": cruzado_flag,
            }
            anotados_chicas.append(item_vt)
            if not cruzado_flag:
                sueltos_anotados.append(item_vt)

        def _serializar(r) -> Dict[str, Any]:
            d = dict(r)
            est = str(d.get("estado_auditoria") or "").upper()
            if est in ("CALZADO", "COMPROBADO"):
                d["estado_auditoria"] = "COMPROBADO"
            elif est == "PENDIENTE":
                d["estado_auditoria"] = "SIN ANOTAR"
            d["hora12"] = _hora_str_a_12h_corta(d.get("hora_operacion") or "")
            return d

        items = [_serializar(r) for r in filas_raw]
        items.sort(
            key=lambda x: (
                "-".join(reversed(str(x.get("fecha_operacion") or "").split("/"))),
                str(x.get("hora_operacion") or ""),
                int(x.get("id") or 0),
            ),
            reverse=True,
        )
        recibidos_list = []
        cruzados_list = []
        sin_anotar_list = []

        tot_banco = 0.0
        tot_comprobado = 0.0
        tot_sin_anotar = 0.0
        tot_excedente = 0.0
        tot_sueltos_anotados = round(sum(float(x["monto"] or 0) for x in sueltos_anotados), 2)

        for it in items:
            est = str(it.get("estado_auditoria") or "").upper()
            m = round(float(it.get("monto_bcp") or 0), 2)
            if est == "SALIDA":
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

        max_id_row = conn.execute("SELECT MAX(id) AS max_id FROM yape_conciliacion_hostal").fetchone()
        ultimo_id = int(max_id_row["max_id"] or 0) if max_id_row else 0
        ping_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'yape_ultimo_ping_celular'").fetchone()
        ultimo_ping_bd = str(ping_row["valor"] or "") if ping_row else globals().get("_ULTIMO_PING_CELULAR_ISO", "")
        rep_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'yape_ultimo_reporte_drive'").fetchone()
        ultimo_rep_bd = str(rep_row["valor"] or "") if rep_row else (_ultimo_archivo_drive_nombre or info_drive.get("archivoDrive") or "")

        return {
            "ok": True,
            "fechaMostrada": "TODOS" if es_todos else fecha_filtro,
            "ultimaFechaBD": ultima_fecha_bd,
            "archivoDrive": ultimo_rep_bd,
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
            "totales": {
                "totalBanco": tot_banco,
                "totalComprobado": tot_comprobado,
                "totalSinAnotar": tot_sin_anotar,
                "totalExcedente": tot_excedente,
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
        # Si viene un monto explícito (por ejemplo desde prueba o parámetros directos)
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


registrar_yape_entrante_sqlite = registrar_notificacion_celular_sqlite
conciliar_yapes_sqlite = comprobar_yapes_con_anotaciones_chicas


