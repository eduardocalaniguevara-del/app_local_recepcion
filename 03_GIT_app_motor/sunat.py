#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_GIT_app_motor/sunat.py — Orquestador Asíncrono de Facturación SUNAT (IGV 10.5% Multi-Ítem + Notas de Crédito),
Conversión de PDF Local a Datos en Supabase, Despacho WhatsApp Recepción y Cola Remota Multi-Laptop.

Arquitectura v2 (Cero dependencia de C:\\Hostal_Sistema):
1. Todo el código viaja en `03_GIT_app_motor/` por GitHub (`git push` desde Laptop 1 -> `git pull` en Laptop 2 Madre y Laptops 3 Terciarias).
2. Los PDFs emitidos (Boletas, Facturas y Notas de Crédito) y las capturas preliminares se guardan
   exclusivamente en la carpeta local `02_DB_sqlite_local/comprobantes_pdf/` (`boletas/`, `facturas/`, `notas_credito/`, `capturas/`).
3. Apenas se emite un comprobante, `convertir_comprobante_a_datos_y_subir_supabase()` extrae toda su información
   y la guarda en `sunat_ventas`, `sunat_lineas_cache` y `estancias`, subiéndola en vivo a Supabase.
4. Si una orden se dispara desde Laptop 3 (Terciaria) o Laptop 1, viaja por Supabase (`estado_sistema` -> `sunat_orden_remota_<job_id>`)
   para que la Laptop 2 Madre la ejecute y devuelva el resultado.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

_MDIR = str(Path(__file__).resolve().parent)
if _MDIR not in sys.path:
    sys.path.insert(0, _MDIR)

from config import (
    APISPERU_DNI_URL,
    APISPERU_RUC_URL,
    APISPERU_TOKEN,
    CAPTURAS_SUNAT_DIR,
    DATA_DIR,
    ES_NIVEL_1_INGENIERIA,
    ES_NIVEL_2_MADRE_HOTEL,
    MODO_PRUEBA,
    REGLAS_AUDITORIA_JSON,
    ROL_NODO,
    SUPABASE_CONFIGURADO,
    SUPABASE_URL,
    TIENE_QR_WSP_FISICO,
)
from db import db_transaction, get_connection
from estancias import _registrar_movimiento, actualizar_comprobante_desde_panel_local
from sunat_motor_playwright import (
    buscar_pdf_comprobante_local,
    calcular_valor_unitario_105,
    convertir_comprobante_a_datos_y_subir_supabase,
    despachar_wsp_comprobante_local,
    emitir_comprobante_sunat_local,
    emitir_nota_credito_sunat_local,
    revisar_y_descargar_faltantes_sunat_incremental,
)
from sync_nube import _headers, encolar_whatsapp_remoto, pull_from_supabase, push_to_supabase

FACTOR_IGV_HOTEL = 1.105

_sunat_emission_lock = threading.Lock()
_remote_consumer_lock = threading.Lock()
_jobs_lock = threading.Lock()
_SUNAT_JOBS: Dict[str, Dict[str, Any]] = {}
_sb_http_lock = threading.Lock()
_SB_FAST_CLIENT: Optional[httpx.Client] = None
_COLA_MADRE_GEN: int = int(time.time() * 1000)


def _get_sb_fast_client() -> httpx.Client:
    """Devuelve un cliente HTTP persistente (Keep-Alive TLS) hacia Supabase para latencia <60 ms."""
    global _SB_FAST_CLIENT
    with _sb_http_lock:
        if _SB_FAST_CLIENT is None or _SB_FAST_CLIENT.is_closed:
            _SB_FAST_CLIENT = httpx.Client(timeout=4.5, verify=False)
        return _SB_FAST_CLIENT


def es_laptop_recepcion_directa() -> bool:
    """Detecta si este nodo es el Nivel 2 (Laptop Madre del Hotel con único QR WSP y emisión directa)."""
    if ES_NIVEL_2_MADRE_HOTEL or TIENE_QR_WSP_FISICO or os.environ.get("HOSTAL_EJECUCION_DIRECTA") == "1":
        return True
    if os.path.exists(r"C:\Users\ovalo mariategui"):
        return True
    return False


# ==============================================================================
# 1. CONSULTA DNI (RENIEC) Y RUC (SUNAT) CON FALLBACK OFFLINE EN 0 MS
# ==============================================================================

def consultar_dni_local_o_reniec(dni_buscado: str) -> Dict[str, Any]:
    dni = re.sub(r"\D", "", str(dni_buscado or "").strip())
    if len(dni) == 7:
        dni = dni.zfill(8)
    if len(dni) < 8:
        return {"ok": False, "error": "DNI debe tener 8 dígitos."}

    conn = get_connection()
    try:
        pad = conn.execute("SELECT * FROM huespedes_padron WHERE dni = ?", (dni,)).fetchone()
        est_rows = conn.execute(
            """
            SELECT huesped, sexo, nac, reg, mot, hab, tarifa, empresa, celular, ruc
            FROM estancias
            WHERE dni = ?
            ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC
            """,
            (dni,),
        ).fetchall()

        if (pad and pad["nombre"]) or est_rows:
            nombre = (pad["nombre"] if pad and pad["nombre"] else "") or (est_rows[0]["huesped"] if est_rows else "")
            sexo = (pad["sexo"] if pad and pad["sexo"] else "") or "M"
            nac = (pad["nac"] if pad and pad["nac"] else "") or "Perú"
            reg = (pad["reg"] if pad and pad["reg"] else "") or ""
            mot = (pad["mot"] if pad and pad["mot"] else "") or "Vacaciones, recreo u ocio"
            celular = (pad["celular"] if pad and pad["celular"] else "") or ""
            ruc = (pad["ruc"] if pad and pad["ruc"] else "") or ""
            empresa = (pad["empresa"] if pad and pad["empresa"] else "") or ""
            hab_frec = (pad["hab_frec"] if pad and pad["hab_frec"] else "") or ""
            tarifa_frec = float(pad["tarifa_frec"] or 0) if pad else 0.0
            visitas = int(pad["visitas"] or 0) if pad else len(est_rows)

            for er in est_rows:
                if not reg and (er["reg"] or "").strip():
                    reg = str(er["reg"]).strip()
                if not ruc and (er["ruc"] or "").strip():
                    ruc = re.sub(r"\D", "", str(er["ruc"]).strip())
                if not empresa and (er["empresa"] or "").strip():
                    empresa = str(er["empresa"]).strip().upper()
                if not celular and (er["celular"] or "").strip():
                    celular = str(er["celular"]).strip()
                if not hab_frec and (er["hab"] or "").strip():
                    hab_frec = str(er["hab"]).strip()
                if tarifa_frec <= 0 and float(er["tarifa"] or 0) > 0:
                    tarifa_frec = float(er["tarifa"] or 0)

            if ruc and not empresa:
                emp_r = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
                if emp_r and emp_r["razon_social"]:
                    empresa = str(emp_r["razon_social"]).strip().upper()
            elif empresa and not ruc:
                emp_r = conn.execute(
                    "SELECT ruc FROM empresas_padron WHERE UPPER(TRIM(razon_social)) = ? LIMIT 1",
                    (empresa.strip().upper(),),
                ).fetchone()
                if emp_r and emp_r["ruc"]:
                    ruc = str(emp_r["ruc"]).strip()

            if nombre:
                return {
                    "ok": True,
                    "dni": dni,
                    "nombre": nombre,
                    "nombreCompleto": nombre,
                    "sexo": sexo,
                    "nac": nac,
                    "reg": reg,
                    "mot": mot,
                    "celular": celular,
                    "ruc": ruc,
                    "empresa": empresa,
                    "hab": hab_frec,
                    "tarifa": tarifa_frec if tarifa_frec > 0 else "",
                    "visitas": max(visitas, 1),
                    "fuente": "PADRON_LOCAL",
                }
    finally:
        conn.close()

    try:
        url = f"{APISPERU_DNI_URL}/{dni}?token={APISPERU_TOKEN}"
        with httpx.Client(timeout=4.5, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                nombres = str(data.get("nombres") or "").strip()
                ap_pat = str(data.get("apellidoPaterno") or "").strip()
                ap_mat = str(data.get("apellidoMaterno") or "").strip()
                nombre_completo = " ".join(x for x in [nombres, ap_pat, ap_mat] if x).strip().upper()
                if nombre_completo:
                    with db_transaction() as c_save:
                        c_save.execute(
                            """
                            INSERT INTO huespedes_padron (dni, nombre, visitas, updated_at)
                            VALUES (?, ?, 0, ?)
                            ON CONFLICT(dni) DO UPDATE SET nombre = excluded.nombre, updated_at = excluded.updated_at
                            """,
                            (dni, nombre_completo, datetime.now().isoformat()),
                        )
                    return {
                        "ok": True,
                        "dni": dni,
                        "nombre": nombre_completo,
                        "nombreCompleto": nombre_completo,
                        "visitas": 0,
                        "fuente": "RENIEC",
                    }
    except Exception:
        pass

    return {"ok": False, "dni": dni, "error": "Sin conexión o DNI no encontrado (ingrese nombre manualmente)."}


def _guardar_empresa_padron_seguro(ruc: str, razon: str) -> None:
    if not ruc or not razon:
        return
    try:
        with db_transaction() as c_save:
            c_save.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET
                    razon_social = excluded.razon_social,
                    updated_at = excluded.updated_at
                """,
                (ruc, razon, datetime.now().isoformat()),
            )
    except Exception:
        pass


def consultar_ruc_local_o_sunat(ruc_buscado: str) -> Dict[str, Any]:
    ruc = re.sub(r"\D", "", str(ruc_buscado or "").strip())
    if len(ruc) != 11 or not re.match(r"^(10|15|17|20)", ruc):
        return {"ok": False, "error": "RUC inválido (debe tener 11 dígitos e iniciar con 10 o 20)."}

    razon_local = ""
    conn = get_connection()
    try:
        emp = conn.execute("SELECT ruc, razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
        if emp and (emp["razon_social"] or "").strip():
            razon_local = str(emp["razon_social"]).strip().upper()
        if not razon_local:
            est_emp = conn.execute(
                "SELECT empresa FROM estancias WHERE ruc = ? AND trim(empresa) != '' ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC LIMIT 1",
                (ruc,),
            ).fetchone()
            if est_emp and (est_emp["empresa"] or "").strip():
                razon_local = str(est_emp["empresa"]).strip().upper()
        if not razon_local:
            hp_emp = conn.execute(
                "SELECT empresa FROM huespedes_padron WHERE ruc = ? AND trim(empresa) != '' LIMIT 1",
                (ruc,),
            ).fetchone()
            if hp_emp and (hp_emp["empresa"] or "").strip():
                razon_local = str(hp_emp["empresa"]).strip().upper()
    except Exception:
        pass
    finally:
        conn.close()

    urls_consulta = [
        ("APISPERU", f"{APISPERU_RUC_URL}/{ruc}?token={APISPERU_TOKEN}"),
        ("APIS_NET_PE", f"https://api.apis.net.pe/v1/ruc?numero={ruc}"),
    ]
    for _, url in urls_consulta:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        razon = str(
                            data.get("razonSocial")
                            or data.get("nombre")
                            or data.get("nombre_o_razon_social")
                            or ""
                        ).strip().upper()
                        if razon:
                            estado = str(data.get("estado") or "ACTIVO").strip().upper()
                            condicion = str(data.get("condicion") or "HABIDO").strip().upper()
                            direccion = str(data.get("direccion") or "").strip()
                            _guardar_empresa_padron_seguro(ruc, razon)
                            return {
                                "ok": True,
                                "ruc": ruc,
                                "razonSocial": razon,
                                "empresa": razon,
                                "nombre": razon,
                                "estado": estado,
                                "condicion": condicion,
                                "direccion": direccion,
                                "fuente": "SUNAT",
                            }
        except Exception:
            continue

    if razon_local:
        _guardar_empresa_padron_seguro(ruc, razon_local)
        return {
            "ok": True,
            "ruc": ruc,
            "razonSocial": razon_local,
            "empresa": razon_local,
            "nombre": razon_local,
            "estado": "ACTIVO",
            "condicion": "HABIDO",
            "direccion": "",
            "fuente": "PADRON_LOCAL",
        }

    return {"ok": False, "ruc": ruc, "error": "Sin conexión o RUC no encontrado (escriba la Razón Social manualmente)."}


# ==============================================================================
# 2. AUDITORÍA PREVENTIVA Y BÚSQUEDA DE PDF LOCAL
# ==============================================================================

def cargar_reglas_auditoria() -> Dict[str, Any]:
    for cand in (DATA_DIR / "reglas_auditoria.json", REGLAS_AUDITORIA_JSON):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {
        "umbrales_auditoria": {"umbral_alerta_monto": 250.0, "umbral_alerta_critico": 500.0},
        "regla_cero_extra": {"activo": True, "valores_sospechosos_cero_extra": [300.0, 350.0, 400.0, 500.0, 600.0, 700.0]},
    }


def validar_monto_preventivo(monto: float, forzar: bool = False) -> Tuple[bool, str]:
    if forzar:
        return True, "Autorizado por operador."
    reglas = cargar_reglas_auditoria()
    umbrales = reglas.get("umbrales_auditoria", {})
    umbral_alerta = float(umbrales.get("umbral_alerta_monto", 250.0))
    cero_extra = reglas.get("regla_cero_extra", {})
    valores_sospechosos = [float(x) for x in cero_extra.get("valores_sospechosos_cero_extra", [])]

    motivos = []
    if monto in valores_sospechosos:
        motivos.append(f"El monto S/ {monto:.2f} coincide con posible error de 'Cero Extra' (S/ {monto/10:.2f} con un cero de más).")
    if monto > umbral_alerta:
        motivos.append(f"El monto S/ {monto:.2f} supera el umbral habitual de una noche (S/ {umbral_alerta:.2f}).")

    if motivos:
        return False, " | ".join(motivos)
    return True, "OK"


def normalizar_y_validar_dni_servicio(
    raw_dni: Any,
    telefono: str = "",
    exigir_por_wsp: bool = False,
) -> Tuple[bool, str, str]:
    """
    Normaliza DNI peruano (7 dígitos -> 8 dígitos con cero inicial) y aplica regla estricta:
    - Si se ingresó un DNI no vacío y no tiene 7 u 8 dígitos, bloquea la emisión/envío.
    - Si el huésped tiene número de celular registrado o solicita envío WSP, es OBLIGATORIO tener DNI válido.
    """
    raw_str = str(raw_dni or "").strip()
    primer_token = re.split(r"[/,;]+", raw_str)[0].strip() if raw_str else ""
    es_vacio = primer_token in ("", "-", "SIN DOCUMENTO", "S/D")
    solo_dig = "" if es_vacio else re.sub(r"\D", "", primer_token)
    if len(solo_dig) == 7:
        solo_dig = solo_dig.zfill(8)

    tel_dig = re.sub(r"\D", "", str(telefono or "").strip())

    if not es_vacio and len(solo_dig) != 8:
        return (
            False,
            "",
            f"El DNI registrado ('{primer_token}' — {len(solo_dig)} dígitos) es incorrecto. "
            "Corríjalo con 7 u 8 dígitos válidos antes de emitir o enviar.",
        )

    if (tel_dig or exigir_por_wsp) and len(solo_dig) != 8:
        return (
            False,
            "",
            f"El huésped tiene celular registrado ({tel_dig or 'WhatsApp'}), por lo que es OBLIGATORIO "
            "que tenga su DNI correcto (7 u 8 dígitos) antes de emitir o enviar el comprobante.",
        )

    return True, solo_dig, ""


def buscar_pdf_comprobante(nro_cp: str = "", ruc_dni: str = "", cliente: str = "") -> Optional[str]:
    return buscar_pdf_comprobante_local(nro_cp=nro_cp, ruc_dni=ruc_dni, cliente=cliente)


# ==============================================================================
# 3. GESTOR DE TRABAJOS EN SEGUNDO PLANO + EJECUCIÓN LOCAL Y COLA REMOTA SUPABASE
# ==============================================================================

def _prune_old_jobs() -> None:
    now_ts = time.time()
    to_del = [
        jid for jid, j in _SUNAT_JOBS.items()
        if j.get("state") in ("DONE", "ERROR") and (now_ts - float(j.get("finished_ts") or now_ts)) > 180
    ]
    for jid in to_del:
        _SUNAT_JOBS.pop(jid, None)


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if job_id in _SUNAT_JOBS:
            _SUNAT_JOBS[job_id].update(kwargs)
            _SUNAT_JOBS[job_id]["updated_ts"] = time.time()


def obtener_estado_job_sunat(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        _prune_old_jobs()
        j = _SUNAT_JOBS.get(job_id)
        return dict(j) if j else None


def listar_jobs_sunat_recientes() -> List[Dict[str, Any]]:
    with _jobs_lock:
        _prune_old_jobs()
        jobs = [dict(j) for j in _SUNAT_JOBS.values()]
    jobs.sort(key=lambda x: float(x.get("created_ts") or 0), reverse=True)
    return jobs


def _guardar_estado_orden_remota_supabase(job_id: str, orden_dict: Dict[str, Any]) -> None:
    """
    Envío Directo Express (<60 ms):
    1. Publica `sunat_orden_remota_<job_id>` directamente en Supabase vía túnel Keep-Alive `httpx.Client`
       sin esperar la cola general `_supabase_outbox` (que procesa otras tablas primero).
    2. Guarda la copia local en `estado_sistema` con `pulling=1` (o con `pulling=0` como fallback si no hubo red).
    """
    clave = f"sunat_orden_remota_{job_id}"
    ahora_iso = datetime.now().isoformat(timespec="seconds")
    orden_dict["updated_ts"] = time.time()
    val_json = json.dumps(orden_dict, ensure_ascii=False)

    enviado_directo = False
    if SUPABASE_CONFIGURADO:
        try:
            url = f"{SUPABASE_URL}/rest/v1/estado_sistema?on_conflict=clave"
            hdrs = _headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
            payload = [{"clave": clave, "valor": val_json, "updated_at": ahora_iso}]
            resp = _get_sb_fast_client().post(url, headers=hdrs, content=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if 200 <= resp.status_code < 300:
                enviado_directo = True
        except Exception:
            enviado_directo = False

    conn = get_connection()
    try:
        if enviado_directo:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1")
        conn.execute(
            """
            INSERT INTO estado_sistema (clave, valor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
            """,
            (clave, val_json, ahora_iso),
        )
        if enviado_directo:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
        conn.commit()
    finally:
        try:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
            conn.commit()
        except Exception:
            pass
        conn.close()

    if not enviado_directo:
        push_to_supabase(limit=20)


def _leer_orden_remota_local(job_id: str) -> Optional[Dict[str, Any]]:
    """
    Lee el estado de `sunat_orden_remota_{job_id}` directamente desde `estado_sistema`
    en Supabase usando conexión Keep-Alive (<50 ms) y lo sincroniza en `estado_sistema` local.
    """
    clave = f"sunat_orden_remota_{job_id}"
    if SUPABASE_CONFIGURADO:
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/estado_sistema"
                f"?clave=eq.{urllib.parse.quote(clave)}&select=valor,updated_at&limit=1"
            )
            resp = _get_sb_fast_client().get(url, headers=_headers())
            if resp.status_code == 200:
                rows = resp.json()
                if isinstance(rows, list) and rows and rows[0].get("valor"):
                    val_str = str(rows[0]["valor"])
                    upd_at = str(rows[0].get("updated_at") or datetime.now().isoformat(timespec="seconds"))
                    c_w = get_connection()
                    try:
                        c_w.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1")
                        c_w.execute(
                            """
                            INSERT INTO estado_sistema (clave, valor, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                            """,
                            (clave, val_str, upd_at),
                        )
                        c_w.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
                        c_w.commit()
                    finally:
                        try:
                            c_w.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
                            c_w.commit()
                        except Exception:
                            pass
                        c_w.close()
                    return json.loads(val_str)
        except Exception:
            pass

    conn = get_connection()
    try:
        row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = ?", (clave,)).fetchone()
        if row and row["valor"]:
            return json.loads(row["valor"])
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _sincronizar_ordenes_remotas_desde_supabase_en_madre() -> int:
    """
    Descarga directamente de Supabase por Keep-Alive (<50 ms) todas las filas `clave=like.sunat_orden_remota*`
    de `estado_sistema` y las sincroniza en `estado_sistema` local de Laptop 2 Madre antes de procesarlas.
    """
    if not SUPABASE_CONFIGURADO:
        return 0
    sincronizadas = 0
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/estado_sistema"
            f"?clave=like.sunat_orden_remota*&select=clave,valor,updated_at"
        )
        resp = _get_sb_fast_client().get(url, headers=_headers())
        if resp.status_code != 200:
            return 0
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return 0

        conn = get_connection()
        try:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                cl = str(r.get("clave") or "").strip()
                vl = str(r.get("valor") or "").strip()
                up = str(r.get("updated_at") or datetime.now().isoformat(timespec="seconds"))
                if not cl or not vl:
                    continue
                conn.execute(
                    """
                    INSERT INTO estado_sistema (clave, valor, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                    """,
                    (cl, vl, up),
                )
                sincronizadas += 1
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
            conn.commit()
        finally:
            try:
                conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
                conn.commit()
            except Exception:
                pass
            conn.close()
    except Exception:
        pass
    return sincronizadas


def _ejecutar_trabajo_sunat_wsp_local(
    job_id: Optional[str],
    data_norm: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
    etapa_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Ejecuta el trabajo de SUNAT + Conversión PDF->Supabase + WhatsApp usando los motores de
    `03_GIT_app_motor/sunat_motor_playwright.py` y guardando los PDFs en `02_DB_sqlite_local/comprobantes_pdf/`.
    """
    def _notificar_etapa(txt: str) -> None:
        if job_id:
            _update_job(job_id, state="RUNNING", etapa=txt)
        if etapa_cb:
            try:
                etapa_cb(txt)
            except Exception:
                pass

    if job_id:
        _update_job(job_id, etapa="⏳ En cola esperando turno del motor SUNAT...")

    with _sunat_emission_lock:
        accion = data_norm["accion"]
        hab = data_norm.get("hab", "")
        estancia_uuid = data_norm.get("estanciaUuid", "")
        id_estancia = data_norm.get("id_estancia", 0)
        modo_borrador = bool(data_norm.get("modoBorrador", False))
        tipo = data_norm.get("tipo", "BOLETA")
        en_recepcion = es_laptop_recepcion_directa()
        CAPTURAS_SUNAT_DIR.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # CASO 0: REVISOR INCREMENTAL DE FALTANTES SUNAT (SOLO EN MADRE L2)
        # ------------------------------------------------------------------
        if accion == "REVISAR_FALTANTES_SUNAT":
            dias_atras = int(data_norm.get("dias_atras") or 7)
            if job_id:
                _update_job(job_id, state="RUNNING", etapa=f"🔍 Revisando comprobantes faltantes en SUNAT ({dias_atras} días)...")
            res_inc = revisar_y_descargar_faltantes_sunat_incremental(dias_atras=dias_atras)
            res_inc["accion"] = "REVISAR_FALTANTES_SUNAT"
            return res_inc

        # ------------------------------------------------------------------
        # CASO A: REENVÍO SOLO WHATSAPP
        # ------------------------------------------------------------------
        if accion == "SOLO_WSP":
            tel = data_norm["telefono"]
            nro_cp = data_norm["nro_comprobante"]
            doc = data_norm["documento"]
            cli = data_norm["cliente"]
            pdf_path = data_norm.get("archivo") or ""
            if not pdf_path or not os.path.isfile(pdf_path):
                pdf_path = buscar_pdf_comprobante_local(nro_cp=nro_cp, ruc_dni=doc, cliente=cli) or ""
            msg = (
                data_norm.get("mensaje")
                or f"Estimado(a) {cli or 'cliente'}, adjuntamos su comprobante electrónico {nro_cp} del Hostal El Óvalo Mariátegui. ¡Gracias por su preferencia!"
            )
            if job_id:
                _update_job(job_id, state="RUNNING", etapa=f"📲 Enviando {nro_cp or 'comprobante'} por WhatsApp Recepción a {tel}...")

            if en_recepcion:
                ok_wsp, out_wsp, err_wsp = despachar_wsp_comprobante_local(
                    telefono=tel,
                    pdf_path=pdf_path,
                    mensaje=msg,
                    nro_cp=nro_cp,
                    cliente=cli,
                    ruc_dni=doc,
                )
            else:
                res_q = encolar_whatsapp_remoto(
                    destino_celular=tel,
                    mensaje_texto=msg,
                    nro_comprobante=nro_cp,
                    adjunto_pdf_path=pdf_path,
                    cliente_nombre=cli,
                )
                ok_wsp = bool(res_q.get("ok"))
                out_wsp = f"Encolado en Supabase hacia Laptop Madre (Cola #{res_q.get('cola_id')})"
                err_wsp = "" if ok_wsp else "No se pudo encolar en wsp_recepcion_cola_envio"

            return {
                "status": "OK" if ok_wsp else "ERROR",
                "accion": "SOLO_WSP",
                "hab": hab,
                "wsp_status": "ENVIADO" if ok_wsp else "ERROR_WSP",
                "archivo_pdf": pdf_path,
                "mensaje": f"Comprobante enviado por WhatsApp al {tel}." if ok_wsp else f"Falló envío WhatsApp: {err_wsp}",
                "error": err_wsp,
                "stdout": out_wsp,
            }

        # ------------------------------------------------------------------
        # CASO B: NOTA DE CRÉDITO ELECTRÓNICA (ANULACIÓN EN SUNAT)
        # ------------------------------------------------------------------
        if accion == "NOTA_CREDITO":
            nro_origen = data_norm["nro_comprobante"]
            motivo = data_norm["motivo"]

            if job_id:
                _update_job(
                    job_id,
                    state="RUNNING",
                    etapa=(
                        f"🧪 Generando Vista Preliminar de Nota de Crédito ({nro_origen})..."
                        if modo_borrador
                        else f"🧾↩️ Emitiendo Nota de Crédito en SUNAT para anular {nro_origen}..."
                    ),
                )

            res_nc = emitir_nota_credito_sunat_local(
                tipo_cp=tipo,
                numero_comprobante=nro_origen,
                motivo=motivo,
                modo_borrador=modo_borrador,
                headless=True,
            )
            st_nc = str(res_nc.get("status") or "").upper()

            if modo_borrador and st_nc == "BORRADOR_GENERADO":
                return {
                    "status": "OK",
                    "modo": "borrador",
                    "accion": "NOTA_CREDITO",
                    "hab": hab,
                    "tipo": tipo,
                    "cp_origen": nro_origen,
                    "captura": res_nc.get("captura_pantalla", ""),
                    "nombre_captura": res_nc.get("nombre_captura", ""),
                    "captura_b64": res_nc.get("captura_b64", ""),
                    "datos_cp_origen": res_nc.get("datos_cp_origen") or {},
                    "mensaje": f"Vista Preliminar de Nota de Crédito para {nro_origen} lista (sin emitir).",
                }

            if not modo_borrador and st_nc == "EMITIDO":
                nro_nc = str(res_nc.get("nro_nota_credito") or f"NC-{nro_origen}").strip()
                try:
                    # 1. Convertir PDF de Nota de Crédito en datos para sunat_ventas -> Supabase
                    convertir_comprobante_a_datos_y_subir_supabase(res_nc, data_norm)

                    # 2. Revertir habitación a Pendiente para permitir re-emitir
                    actualizar_comprobante_desde_panel_local({
                        "hab": hab,
                        "estanciaUuid": estancia_uuid,
                        "fila": id_estancia,
                        "estadoComp": "Pendiente",
                        "tipoComp": "Factura" if tipo == "FACTURA" else "Boleta",
                        "nroComp": "",
                        "forzarNroComp": True,
                    })
                    with db_transaction() as conn_m:
                        _registrar_movimiento(
                            conn_m,
                            "Nota de Crédito SUNAT",
                            hab=hab,
                            huesped=data_norm.get("cliente", ""),
                            resumen=f"Hab. {hab} · {nro_nc} anuló {nro_origen} ({motivo}) · Estado vuelve a Pendiente",
                            detalle=[
                                {"campo": "Comprobante Anulado", "antes": nro_origen, "despues": "Anulado"},
                                {"campo": "Nota de Crédito", "antes": "-", "despues": nro_nc},
                                {"campo": "Motivo", "antes": "-", "despues": motivo},
                            ],
                            monto=float((res_nc.get("datos_cp_origen") or {}).get("monto") or 0),
                        )
                    push_to_supabase(limit=50)
                    if wakeup_cb:
                        wakeup_cb()
                except Exception as exc_db:
                    return {
                        "status": "OK",
                        "accion": "NOTA_CREDITO",
                        "hab": hab,
                        "nro_nota_credito": nro_nc,
                        "cp_origen": nro_origen,
                        "warn_sqlite": str(exc_db),
                        "mensaje": f"Nota de Crédito {nro_nc} emitida en SUNAT, pero revise actualización local: {exc_db}",
                    }

                return {
                    "status": "OK",
                    "accion": "NOTA_CREDITO",
                    "hab": hab,
                    "tipo": tipo,
                    "cp_origen": nro_origen,
                    "nro_nota_credito": nro_nc,
                    "archivo_pdf": res_nc.get("pdf_descargado") or "",
                    "captura": res_nc.get("captura_pantalla") or "",
                    "nombre_captura": res_nc.get("nombre_captura") or "",
                    "captura_b64": res_nc.get("captura_b64") or "",
                    "mensaje": f"Nota de Crédito {nro_nc} emitida en SUNAT (anuló {nro_origen}). Hab. {hab} volvió a Pendiente.",
                }

            return {
                "status": "ERROR",
                "accion": "NOTA_CREDITO",
                "hab": hab,
                "error": res_nc.get("error") or "No se pudo emitir la Nota de Crédito en SUNAT.",
            }

        # ------------------------------------------------------------------
        # CASO C: EMISIÓN DE BOLETA / FACTURA + CONVERSIÓN PDF->SUPABASE + WSP
        # ------------------------------------------------------------------
        emitir_en_firme = not modo_borrador
        items_normalizados = data_norm["items"]
        total_monto = data_norm["total_monto"]
        doc_num = data_norm["documento"]
        dni_huesped = data_norm.get("dni_huesped", "")
        cliente = data_norm["cliente"]
        telefono = data_norm["telefono"]

        _notificar_etapa(
            f"🧪 Laptop 2 Madre: Abriendo Vista Preliminar de {tipo.title()} en SUNAT SEE-SOL..."
            if modo_borrador
            else f"⚡ Laptop 2 Madre: Emitiendo {tipo.title()} S/ {total_monto:.2f} en SUNAT SEE-SOL (IGV 10.5%)..."
        )

        res_sunat = emitir_comprobante_sunat_local(
            tipo=tipo,
            doc_num=doc_num,
            cliente_nombre=cliente,
            items=items_normalizados,
            emitir_final=emitir_en_firme,
            headless=True,
            etapa_cb=_notificar_etapa,
        )

        if res_sunat.get("status") != "OK":
            return {
                "status": "ERROR",
                "accion": accion,
                "hab": hab,
                "error": res_sunat.get("error") or "Error desconocido en emisión SUNAT.",
            }

        # Si fue Modo Borrador, devolver captura y base64 sin persistir en sunat_ventas
        if modo_borrador:
            return {
                "status": "OK",
                "modo": "borrador",
                "accion": accion,
                "hab": hab,
                "tipo": tipo,
                "nro_comprobante": "BORRADOR-PRELIMINAR",
                "captura": res_sunat.get("captura", ""),
                "nombre_captura": res_sunat.get("nombre_captura", ""),
                "captura_b64": res_sunat.get("captura_b64", ""),
                "monto": total_monto,
                "mensaje": f"Vista Preliminar de {tipo.title()} generada en SUNAT (sin emitir).",
            }

        nro_cp = str(res_sunat.get("nro_comprobante") or "").strip()
        archivo_pdf = str(res_sunat.get("archivo_pdf") or "").strip()
        receptor_nombre_real = str(res_sunat.get("receptor_nombre") or cliente).strip().upper()

        # Etapa 2: Convertir PDF local en Datos (`sunat_ventas` + `sunat_lineas_cache`) y subir a Supabase
        _notificar_etapa(f"📊 Laptop 2 Madre: {nro_cp} descargada · Extrayendo datos a sunat_ventas y Supabase...")
        datos_conv = convertir_comprobante_a_datos_y_subir_supabase(res_sunat, data_norm)

        # Etapa 3: Enviar por WhatsApp de Recepción si se pidió EMITIR_Y_WSP
        wsp_status = "NO_SOLICITADO"
        wsp_err = ""
        if accion == "EMITIR_Y_WSP" and telefono:
            _notificar_etapa(f"📲 Laptop 2 Madre: {nro_cp} registrada · Enviando PDF por WhatsApp Recepción al {telefono}...")
            tipo_lbl = "Factura" if tipo == "FACTURA" else "Boleta"
            msg_wsp = (
                f"Estimado(a) {receptor_nombre_real}, le adjuntamos su {tipo_lbl} Electrónica {nro_cp}. "
                "¡Muchas gracias por su preferencia en Hostal El Óvalo Mariátegui!"
            )
            if en_recepcion:
                ok_wsp, _, err_wsp = despachar_wsp_comprobante_local(
                    telefono=telefono,
                    pdf_path=archivo_pdf,
                    mensaje=msg_wsp,
                    nro_cp=nro_cp,
                    cliente=receptor_nombre_real,
                    ruc_dni=doc_num,
                )
                wsp_status = "ENVIADO" if ok_wsp else "ERROR_WSP"
                wsp_err = err_wsp
            else:
                res_q = encolar_whatsapp_remoto(
                    destino_celular=telefono,
                    mensaje_texto=msg_wsp,
                    nro_comprobante=nro_cp,
                    adjunto_pdf_path=archivo_pdf,
                    cliente_nombre=receptor_nombre_real,
                )
                wsp_status = "ENVIADO" if res_q.get("ok") else "ERROR_WSP"
                wsp_err = "" if res_q.get("ok") else "No se pudo encolar envío WhatsApp en Supabase."

        # Etapa 4: Actualizar estancia a Emitido (✅) en SQLite, Supabase y espejo Sheets
        if hab or estancia_uuid or id_estancia:
            try:
                actualizar_comprobante_desde_panel_local({
                    "hab": hab,
                    "estanciaUuid": estancia_uuid,
                    "fila": id_estancia,
                    "dni": dni_huesped if dni_huesped else None,
                    "estadoComp": "Emitido",
                    "tipoComp": "Factura" if tipo == "FACTURA" else "Boleta",
                    "nroComp": nro_cp,
                    "montoFact": total_monto,
                    "celular": telefono,
                    "ruc": doc_num if tipo == "FACTURA" else "",
                    "empresa": receptor_nombre_real if tipo == "FACTURA" else "",
                    "forzarNroComp": True,
                })
                push_to_supabase(limit=50)
                if wakeup_cb:
                    wakeup_cb()
            except Exception as exc_upd:
                res_sunat["warn_sqlite"] = str(exc_upd)

        msg_final = f"{'Factura' if tipo == 'FACTURA' else 'Boleta'} {nro_cp} emitida y sincronizada con Supabase."
        if accion == "EMITIR_Y_WSP":
            if wsp_status == "ENVIADO":
                msg_final = f"✅ {nro_cp} emitida en SUNAT, convertida a datos en Supabase y enviada por WhatsApp al {telefono}."
            else:
                msg_final = f"⚠️ {nro_cp} emitida en SUNAT, pero WhatsApp reportó: {wsp_err or wsp_status}. Puede usar 'Reenviar WSP'."

        return {
            "status": "OK",
            "accion": accion,
            "hab": hab,
            "tipo": tipo,
            "nro_comprobante": nro_cp,
            "monto": total_monto,
            "archivo_pdf": archivo_pdf,
            "datos_supabase": datos_conv,
            "wsp_status": wsp_status,
            "wsp_error": wsp_err,
            "mensaje": msg_final,
        }


def _verificar_estancia_ya_emitida_local(data_norm: Dict[str, Any]) -> Optional[str]:
    """
    Si una orden EMITIR / EMITIR_Y_WSP (modoBorrador=False) corresponde a una estancia que ya
    fue marcada como 'Emitido' con `nro_comp != ''` en SQLite local `estancias`, devuelve ese `nro_comp`
    para evitar doble emisión en SUNAT.
    """
    accion = str(data_norm.get("accion") or "").upper()
    if accion not in ("EMITIR", "EMITIR_Y_WSP") or bool(data_norm.get("modoBorrador")):
        return None
    est_uuid = str(data_norm.get("estanciaUuid") or "").strip()
    id_est = int(data_norm.get("id_estancia") or 0)
    if not est_uuid and not id_est:
        return None
    conn = get_connection()
    try:
        if est_uuid:
            row = conn.execute(
                "SELECT estado_comp, nro_comp FROM estancias WHERE estancia_uuid = ? LIMIT 1",
                (est_uuid,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT estado_comp, nro_comp FROM estancias WHERE id = ? LIMIT 1",
                (id_est,),
            ).fetchone()
        if row:
            est_c = str(row["estado_comp"] or "").strip().lower()
            nro_c = str(row["nro_comp"] or "").strip()
            if est_c == "emitido" and nro_c:
                return nro_c
    except Exception:
        pass
    finally:
        conn.close()
    return None


def procesar_cola_ordenes_sunat_madre(wakeup_cb: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """
    Corre en la Laptop Madre (NIVEL_2_MADRE_HOTEL):
    1. Adquiere candado de hilo (`_remote_consumer_lock`) y candado inter-proceso en disco
       (`02_DB_sqlite_local/sunat_cola_madre.lock`) para que NUNCA dos procesos (servidor `:8080` y `motor_24x7.py`)
       consuman ni abran Playwright en SUNAT al mismo tiempo.
    2. Revisa si alguna otra laptop (Laptop 3 Terciaria o Laptop 1) envió una orden en
       `estado_sistema` (`clave LIKE 'sunat_orden_remota_%'`) con `state == 'PENDIENTE'`.
    3. La reclama (`state = 'RUNNING'`), ejecuta la emisión SUNAT + guardado de PDF local en L2 +
       conversión a `sunat_ventas` + envío por WhatsApp de Recepción, y publica `state = 'DONE'` en Supabase.
    """
    if not es_laptop_recepcion_directa():
        return {"ok": True, "procesadas": 0, "motivo": "NO_ES_LAPTOP_MADRE"}
    if not _remote_consumer_lock.acquire(blocking=False):
        return {"ok": True, "procesadas": 0, "motivo": "YA_EN_EJECUCION_HILO"}

    lock_file_handle = None
    try:
        if os.name == "nt":
            try:
                import msvcrt
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                lock_path = DATA_DIR / "sunat_cola_madre.lock"
                lock_file_handle = open(lock_path, "a+b")
                lock_file_handle.seek(0)
                if lock_path.stat().st_size == 0:
                    lock_file_handle.write(b"0")
                    lock_file_handle.flush()
                    lock_file_handle.seek(0)
                msvcrt.locking(lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                if lock_file_handle is not None:
                    try:
                        lock_file_handle.close()
                    except Exception:
                        pass
                return {"ok": True, "procesadas": 0, "motivo": "YA_EN_EJECUCION_PROCESO"}

        procesadas = 0
        _sincronizar_ordenes_remotas_desde_supabase_en_madre()
        conn = get_connection()
        try:
            filas = conn.execute(
                "SELECT clave, valor FROM estado_sistema WHERE clave LIKE 'sunat_orden_remota_%'"
            ).fetchall()
        finally:
            conn.close()

        ahora_ts = time.time()
        claves_limpiar: List[str] = []

        for r in filas:
            clave = str(r["clave"])
            try:
                orden = json.loads(r["valor"] or "{}")
            except Exception:
                continue

            st = str(orden.get("state") or "").upper()
            upd_ts = float(orden.get("updated_ts") or orden.get("created_ts") or 0)

            # Purgar órdenes finalizadas hace más de 10 minutos
            if st in ("DONE", "ERROR") and (ahora_ts - upd_ts) > 600:
                claves_limpiar.append(clave)
                continue

            if st != "PENDIENTE":
                continue

            job_id = str(orden.get("job_id") or clave.replace("sunat_orden_remota_", ""))
            data_norm = orden.get("data_norm") or {}
            if not data_norm:
                continue

            # Re-lectura fresca de Supabase por si otro proceso ya la reclamó o completó
            ord_fresca = _leer_orden_remota_local(job_id)
            if ord_fresca and str(ord_fresca.get("state") or "").upper() != "PENDIENTE":
                continue

            # Guardia de idempotencia: si la estancia ya fue emitida en SQLite local, devolver DONE sin re-abrir SUNAT
            nro_ya_emitido = _verificar_estancia_ya_emitida_local(data_norm)
            if nro_ya_emitido:
                res_idemp = {
                    "status": "OK",
                    "accion": data_norm.get("accion"),
                    "hab": data_norm.get("hab", ""),
                    "tipo": data_norm.get("tipo", "BOLETA"),
                    "nro_comprobante": nro_ya_emitido,
                    "monto": data_norm.get("total_monto", 0),
                    "mensaje": f"✅ {nro_ya_emitido} ya se encuentra emitido y sincronizado en Supabase.",
                }
                orden["state"] = "DONE"
                orden["atendido_por"] = ROL_NODO
                orden["etapa"] = res_idemp["mensaje"]
                orden["finished_ts"] = time.time()
                orden["result"] = res_idemp
                _guardar_estado_orden_remota_supabase(job_id, orden)
                procesadas += 1
                continue

            # 1. Reclamar la orden en Supabase
            orden["state"] = "RUNNING"
            orden["atendido_por"] = ROL_NODO
            orden["etapa"] = f"🏢 Laptop 2 Madre recibió la orden ({data_norm.get('accion', 'SUNAT')} Hab. {data_norm.get('hab', '')})..."
            _guardar_estado_orden_remota_supabase(job_id, orden)

            def _reportar_etapa_remota(txt_etapa: str, _jid=job_id, _ord=orden) -> None:
                _ord["etapa"] = txt_etapa
                _guardar_estado_orden_remota_supabase(_jid, _ord)

            # 2. Ejecutar la orden en Laptop Madre reportando cada fase en vivo a Supabase
            res = _ejecutar_trabajo_sunat_wsp_local(None, data_norm, wakeup_cb=wakeup_cb, etapa_cb=_reportar_etapa_remota)

            # 3. Guardia anti-sobreescritura: nunca pisar un DONE exitoso con un ERROR tardío
            if res.get("status") != "OK":
                ord_post = _leer_orden_remota_local(job_id)
                if ord_post and str(ord_post.get("state") or "").upper() == "DONE":
                    procesadas += 1
                    continue
                nro_post = _verificar_estancia_ya_emitida_local(data_norm)
                if nro_post:
                    res = {
                        "status": "OK",
                        "accion": data_norm.get("accion"),
                        "hab": data_norm.get("hab", ""),
                        "tipo": data_norm.get("tipo", "BOLETA"),
                        "nro_comprobante": nro_post,
                        "monto": data_norm.get("total_monto", 0),
                        "mensaje": f"✅ {nro_post} emitido y sincronizado con Supabase.",
                    }

            # 4. Publicar resultado final en Supabase
            orden["state"] = "DONE" if res.get("status") == "OK" else "ERROR"
            orden["etapa"] = res.get("mensaje") or res.get("error") or "Completado en Laptop Madre"
            orden["finished_ts"] = time.time()
            orden["result"] = res
            _guardar_estado_orden_remota_supabase(job_id, orden)
            procesadas += 1

        if claves_limpiar:
            c_del = get_connection()
            try:
                for cl in claves_limpiar:
                    c_del.execute("DELETE FROM estado_sistema WHERE clave = ?", (cl,))
                c_del.commit()
            finally:
                c_del.close()
            push_to_supabase(limit=30)

        return {"ok": True, "procesadas": procesadas}
    finally:
        if lock_file_handle is not None:
            try:
                import msvcrt
                lock_file_handle.seek(0)
                msvcrt.locking(lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                lock_file_handle.close()
            except Exception:
                pass
        _remote_consumer_lock.release()


def _materializar_captura_b64_local(res: Dict[str, Any]) -> None:
    """Si la respuesta remota de Laptop Madre trae una captura preliminar en Base64, la guarda en disco local para mostrarla en la UI."""
    nom_cap = str(res.get("nombre_captura") or "").strip()
    cap_b64 = str(res.get("captura_b64") or "").strip()
    if nom_cap and cap_b64:
        try:
            CAPTURAS_SUNAT_DIR.mkdir(parents=True, exist_ok=True)
            local_cap = CAPTURAS_SUNAT_DIR / Path(nom_cap).name
            if not local_cap.exists():
                local_cap.write_bytes(base64.b64decode(cap_b64))
            res["captura"] = str(local_cap)
        except Exception:
            pass


def _ejecutar_trabajo_sunat_wsp(
    job_id: Optional[str],
    data_norm: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Candado Arquitectónico por Rol (`ROL_NODO` en `.env`):
    - Aunque TODAS las laptops descargan el mismo código `.py` desde GitHub:
      1) Si el `.env` local identifica a la PC como `NIVEL_2_MADRE_HOTEL` (`es_laptop_recepcion_directa() == True`),
         el motor SUNAT + PDF local + WhatsApp se ACTIVA y ejecuta la orden directamente.
      2) Si el `.env` local identifica a la PC como `NIVEL_3_TERCIARIA` o `NIVEL_1_INGENIERIA`,
         el motor local queda INACTIVO: la laptop NO abre SUNAT ni WhatsApp, sino que envía la orden
         por Supabase (`sunat_orden_remota_<job_id>`) para que la Laptop Madre (L2) la ejecute y devuelva el resultado.
         (En Laptop 1 solo se permite ejecución local explícita con `--forzar-local` para pruebas de ingeniería).
    """
    if es_laptop_recepcion_directa() or (ES_NIVEL_1_INGENIERIA and bool(data_norm.get("forzar_local"))):
        return _ejecutar_trabajo_sunat_wsp_local(job_id, data_norm, wakeup_cb=wakeup_cb)

    jid_eff = job_id or f"sunat_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    orden_remota = {
        "job_id": jid_eff,
        "state": "PENDIENTE",
        "solicitado_por": ROL_NODO,
        "etapa": f"📡 [{ROL_NODO} → Laptop Madre] Orden enviada por Supabase...",
        "created_ts": time.time(),
        "updated_ts": time.time(),
        "data_norm": data_norm,
        "result": None,
    }
    if job_id:
        _update_job(job_id, state="RUNNING", etapa=orden_remota["etapa"])

    _guardar_estado_orden_remota_supabase(jid_eff, orden_remota)

    t0 = time.time()
    timeout_total = 240.0

    while time.time() - t0 < timeout_total:
        time.sleep(0.65)
        ord_act = _leer_orden_remota_local(jid_eff)
        if not ord_act:
            continue

        st_rem = str(ord_act.get("state") or "").upper()
        etapa_rem = str(ord_act.get("etapa") or "")
        if job_id and etapa_rem:
            _update_job(job_id, etapa=etapa_rem)

        if st_rem in ("DONE", "ERROR"):
            res_rem = ord_act.get("result") or {"status": "ERROR", "error": etapa_rem or "Error en Laptop Madre."}
            _materializar_captura_b64_local(res_rem)
            try:
                pull_from_supabase(limit=200)
            except Exception:
                pass
            if wakeup_cb:
                wakeup_cb()
            return res_rem

    return {
        "status": "ERROR",
        "accion": data_norm.get("accion"),
        "hab": data_norm.get("hab"),
        "error": "Tiempo de espera agotado aguardando respuesta de la Laptop Madre en Recepción vía Supabase.",
    }


def _thread_runner(
    job_id: str,
    data_norm: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> None:
    try:
        res = _ejecutar_trabajo_sunat_wsp(job_id, data_norm, wakeup_cb=wakeup_cb)
        st = "DONE" if res.get("status") == "OK" else "ERROR"
        _update_job(
            job_id,
            state=st,
            etapa=res.get("mensaje") or (res.get("error") if st == "ERROR" else "Completado"),
            result=res,
            finished_ts=time.time(),
        )
    except Exception as exc:
        _update_job(
            job_id,
            state="ERROR",
            etapa=f"Error: {exc}",
            result={"status": "ERROR", "error": str(exc), "hab": data_norm.get("hab", "")},
            finished_ts=time.time(),
        )


def ejecutar_orden_sunat_wsp(
    data: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Valida instantáneamente (<5 ms) los datos de la orden:
    - Si hay error de validación o requiere confirmación de auditoría, responde de inmediato.
    - Por defecto lanza la ejecución en segundo plano (async=True) y devuelve job_id en 0 ms.
    - Si data['sincrono'] == True, espera y devuelve el resultado directo.
    """
    accion_raw = str(data.get("accion") or "").upper().strip()
    if MODO_PRUEBA:
        _msg_p = "🧪 MODO PRUEBA: la emisión SUNAT está deshabilitada en el sistema de prueba."
        return {"status": "ERROR", "exito": False, "error": _msg_p, "mensaje": _msg_p}
    modo_borrador = bool(data.get("modoBorrador", False))
    if accion_raw == "BORRADOR":
        accion = "EMITIR"
        modo_borrador = True
    else:
        accion = accion_raw

    hab = str(data.get("hab") or data.get("habNum") or "").strip()
    estancia_uuid = str(data.get("estanciaUuid") or "").strip()
    id_estancia = data.get("id_estancia") or data.get("fila") or 0
    fila_calzado = data.get("fila_calzado")
    sincrono = bool(data.get("sincrono", False))
    forzar_local = bool(data.get("forzar_local", False))

    if accion not in ("EMITIR", "EMITIR_Y_WSP", "SOLO_WSP", "NOTA_CREDITO", "REVISAR_FALTANTES_SUNAT"):
        return {"status": "ERROR", "error": f"Acción '{accion_raw}' no soportada."}

    # 0. Revisor Incremental de Faltantes SUNAT
    if accion == "REVISAR_FALTANTES_SUNAT":
        data_norm = {
            "accion": "REVISAR_FALTANTES_SUNAT",
            "hab": "",
            "estanciaUuid": "",
            "id_estancia": 0,
            "fila_calzado": None,
            "modoBorrador": False,
            "forzar_local": False,
            "tipo": "SUNAT",
            "dias_atras": int(data.get("dias_atras") or 7),
        }

    # 1. Validación rápida SOLO_WSP
    elif accion == "SOLO_WSP":
        tel = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
        if not tel:
            return {"status": "ERROR", "error": "Ingrese un número de celular válido para enviar por WhatsApp."}
        dni_wsp_in = data.get("dni_huesped") if data.get("dni_huesped") is not None else data.get("dni")
        if dni_wsp_in is not None:
            ok_d, _, err_d = normalizar_y_validar_dni_servicio(dni_wsp_in, telefono=tel, exigir_por_wsp=True)
            if not ok_d:
                return {"status": "ERROR", "error": err_d}
        data_norm = {
            "accion": "SOLO_WSP",
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": False,
            "forzar_local": forzar_local,
            "tipo": str(data.get("tipo") or "BOLETA").upper().strip(),
            "telefono": tel,
            "nro_comprobante": str(data.get("nro_comprobante") or "").strip(),
            "documento": str(data.get("documento") or "").strip(),
            "cliente": str(data.get("cliente") or "").strip(),
            "archivo": str(data.get("archivo") or "").strip(),
            "mensaje": str(data.get("mensaje") or "").strip(),
        }

    # 2. Validación rápida NOTA_CREDITO
    elif accion == "NOTA_CREDITO":
        nro_cp = str(data.get("nro_comprobante") or "").strip().upper()
        if not re.search(r"(\d+)$", nro_cp):
            return {
                "status": "ERROR",
                "error": "Para emitir Nota de Crédito debe figurar el N° de comprobante emitido (ej. EB01-2069 o E001-355).",
            }
        tipo_nc = str(data.get("tipo") or "").upper().strip()
        if nro_cp.startswith("E001") or nro_cp.startswith("F"):
            tipo_nc = "FACTURA"
        elif nro_cp.startswith("EB01") or nro_cp.startswith("B"):
            tipo_nc = "BOLETA"
        elif tipo_nc not in ("BOLETA", "FACTURA"):
            tipo_nc = "BOLETA"

        motivo = str(data.get("motivo") or "ANULACION DE LA OPERACION").strip().upper()
        if len(motivo) < 5:
            return {"status": "ERROR", "error": "Ingrese un motivo válido para la Nota de Crédito (mínimo 5 caracteres)."}

        data_norm = {
            "accion": "NOTA_CREDITO",
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": modo_borrador,
            "forzar_local": forzar_local,
            "tipo": tipo_nc,
            "nro_comprobante": nro_cp,
            "motivo": motivo,
            "cliente": str(data.get("cliente") or "").strip().upper(),
        }

    # 3. Validación rápida EMITIR / EMITIR_Y_WSP
    else:
        tipo = str(data.get("tipo") or data.get("tipoDoc") or "BOLETA").upper().strip()
        doc_raw = str(data.get("documento") or data.get("docReceptor") or "").strip()
        dni_huesped_raw = str(data.get("dni_huesped") if data.get("dni_huesped") is not None else (data.get("dni") or "")).strip()
        cliente = str(data.get("cliente") or data.get("nombreReceptor") or "CLIENTES VARIOS").strip().upper()
        telefono = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
        forzar = bool(data.get("forzar", False))
        raw_items = data.get("items") or []
        exigir_wsp = bool(accion == "EMITIR_Y_WSP" and not modo_borrador)

        if exigir_wsp and not telefono:
            return {"status": "ERROR", "error": "Para 'Emitir y Enviar WSP' debe ingresar el número de WhatsApp del cliente."}

        if tipo == "FACTURA":
            doc_num = re.sub(r"\D", "", doc_raw)
            if len(doc_num) != 11 or not re.match(r"^(10|15|17|20)", doc_num):
                return {"status": "ERROR", "error": "Para emitir FACTURA se requiere un RUC válido de 11 dígitos (10 o 20)."}
            dni_para_validar = dni_huesped_raw
            if not dni_para_validar and doc_num.startswith("10"):
                dni_para_validar = doc_num[2:10]
            ok_dni, dni_huesped_norm, err_dni = normalizar_y_validar_dni_servicio(
                dni_para_validar,
                telefono=telefono,
                exigir_por_wsp=exigir_wsp,
            )
            if not ok_dni:
                return {"status": "ERROR", "error": err_dni}
        else:
            dni_fuente = doc_raw if doc_raw else dni_huesped_raw
            ok_dni, doc_num, err_dni = normalizar_y_validar_dni_servicio(
                dni_fuente,
                telefono=telefono,
                exigir_por_wsp=exigir_wsp,
            )
            if not ok_dni:
                return {"status": "ERROR", "error": err_dni}
            dni_huesped_norm = doc_num

        if not raw_items:
            return {"status": "ERROR", "error": "Debe incluir al menos un ítem para emitir en SUNAT."}

        items_normalizados = []
        total_monto = 0.0
        for it in raw_items:
            cant = max(1, int(it.get("cantidad") or 1))
            precio_u = round(float(it.get("precio_unitario") or it.get("precioUnitario") or it.get("monto") or 0), 2)
            if precio_u <= 0:
                return {"status": "ERROR", "error": "Todos los ítems deben tener Precio Unitario mayor a S/ 0.00."}
            valor_u = calcular_valor_unitario_105(precio_u)
            desc = str(it.get("descripcion") or f"DIA DE ALOJAMIENTO HAB N {hab}").strip().upper()
            items_normalizados.append({
                "cantidad": cant,
                "descripcion": desc,
                "precio_unitario": precio_u,
                "valor_unitario": valor_u,
            })
            total_monto = round(total_monto + (cant * precio_u), 2)

        if not modo_borrador:
            ok_audit, msg_audit = validar_monto_preventivo(total_monto, forzar=forzar)
            if not ok_audit:
                return {
                    "status": "CONFIRM_REQUIRED",
                    "requiereConfirmacion": True,
                    "monto": total_monto,
                    "mensajeAuditoria": msg_audit,
                }

        data_norm = {
            "accion": accion,
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": modo_borrador,
            "forzar_local": forzar_local,
            "tipo": tipo,
            "documento": doc_num,
            "dni_huesped": dni_huesped_norm,
            "cliente": cliente,
            "telefono": telefono,
            "items": items_normalizados,
            "total_monto": total_monto,
        }

    if sincrono:
        return _ejecutar_trabajo_sunat_wsp(None, data_norm, wakeup_cb=wakeup_cb)

    job_id = f"sunat_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    etapa_ini = (
        "🧪 Iniciando captura de Vista Preliminar en SUNAT..."
        if modo_borrador
        else (
            f"📲 Preparando envío WhatsApp (Hab. {hab})..."
            if accion == "SOLO_WSP"
            else (
                f"🧾↩️ Iniciando Nota de Crédito en SUNAT (Hab. {hab})..."
                if accion == "NOTA_CREDITO"
                else f"⚡ Iniciando emisión en SUNAT{' + WSP' if accion == 'EMITIR_Y_WSP' else ''} (Hab. {hab})..."
            )
        )
    )
    job_info = {
        "job_id": job_id,
        "hab": hab,
        "estanciaUuid": estancia_uuid,
        "accion": accion,
        "tipo": data_norm["tipo"],
        "modoBorrador": modo_borrador,
        "state": "QUEUED",
        "etapa": etapa_ini,
        "created_ts": time.time(),
        "updated_ts": time.time(),
        "finished_ts": None,
        "result": None,
    }
    with _jobs_lock:
        _prune_old_jobs()
        _SUNAT_JOBS[job_id] = job_info

    th = threading.Thread(
        target=_thread_runner,
        args=(job_id, data_norm, wakeup_cb),
        daemon=True,
    )
    th.start()

    return {
        "status": "PROCESSING",
        "async": True,
        "job_id": job_id,
        "hab": hab,
        "accion": accion,
        "tipo": data_norm["tipo"],
        "modoBorrador": modo_borrador,
        "etapa": etapa_ini,
    }


# ==============================================================================
# 4. OBJETIVO 2: PRE-APROBACIÓN Y AUTOMATIZACIÓN DE PENDIENTES (FACTURAS Y BOLETAS)
# ==============================================================================

MESES_TEXTO_GLOSA = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}


def _formatear_fecha_glosa(fecha_dmy: str) -> str:
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", str(fecha_dmy or "").strip())
    if m:
        d = int(m.group(1))
        mes = int(m.group(2))
        if 1 <= d <= 31 and 1 <= mes <= 12:
            return f"{d:02d} DE {MESES_TEXTO_GLOSA[mes]}"
    return "HOY"


def listar_pendientes_facturacion_para_aprobacion(
    fecha_desde_ymd: int = 20261101,
    fecha_hasta_ymd: int = 20261231,
    solo_con_yape_o_solicita: bool = True,
) -> Dict[str, Any]:
    """
    Escanea las estancias con comprobante pendiente y genera la tabla de Pre-Aprobación (Semáforo Pre-Vuelo SUNAT):
    - Separa en:
      1) `facturas_ruc_pendientes` (Prioridad Naranja: Facturas con RUC)
      2) `boletas_wsp_pendientes` (Prioridad Naranja: Boletas con celular para emitir + enviar por WhatsApp)
      3) `boletas_sin_wsp_pendientes` (Amarillo: Boletas con Yape sin celular)
      4) `observados_corregir` (Rojo: DNI incompleto, RUC inválido o sin monto)
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT estancia_uuid, sheet_row, fecha_in_dmy, fecha_out_dmy, ymd_in,
                   hab, tipo_hab, huesped, dni, celular, ruc, empresa,
                   tarifa, noches, adic_snacks, adic_servicios, adic_comp,
                   pago_ini_efec, pago_ini_yape, pago_post_efec, pago_post_yape,
                   solicita_comp, tipo_comp, estado_comp, nro_comp, monto_factura,
                   unificado_en_uuid
            FROM estancias
            WHERE ymd_in BETWEEN ? AND ?
              AND lower(coalesce(estado_estancia, '')) != 'cancelada'
              AND (nro_comp IS NULL OR trim(nro_comp) = '')
              AND lower(coalesce(estado_comp, '')) NOT IN ('emitido', 'unificado')
              AND (unificado_en_uuid IS NULL OR trim(unificado_en_uuid) = '')
            ORDER BY ymd_in DESC, hab ASC
            """,
            (fecha_desde_ymd, fecha_hasta_ymd),
        ).fetchall()
        mapa_emp_ruc: Dict[str, Tuple[str, str]] = {}
        for er_p in conn.execute("SELECT ruc, razon_social FROM empresas_padron WHERE length(trim(ruc)) = 11").fetchall():
            rz_p = str(er_p["razon_social"] or "").strip().upper()
            rc_p = str(er_p["ruc"] or "").strip()
            if rz_p and rc_p:
                mapa_emp_ruc[rz_p] = (rc_p, rz_p)
                primera_pal = rz_p.split()[0]
                if len(primera_pal) >= 5 and primera_pal not in mapa_emp_ruc:
                    mapa_emp_ruc[primera_pal] = (rc_p, rz_p)
        for er_e in conn.execute("SELECT ruc, empresa FROM estancias WHERE length(trim(ruc)) = 11 AND trim(empresa) != ''").fetchall():
            rz_e = str(er_e["empresa"] or "").strip().upper()
            rc_e = re.sub(r"\D", "", str(er_e["ruc"] or ""))
            if len(rc_e) == 11 and rz_e:
                if rz_e not in mapa_emp_ruc:
                    mapa_emp_ruc[rz_e] = (rc_e, rz_e)
                primera_pal = rz_e.split()[0]
                if len(primera_pal) >= 5 and primera_pal not in mapa_emp_ruc:
                    mapa_emp_ruc[primera_pal] = (rc_e, rz_e)
    finally:
        conn.close()

    facturas_ruc: List[Dict[str, Any]] = []
    boletas_wsp: List[Dict[str, Any]] = []
    boletas_sin_wsp: List[Dict[str, Any]] = []
    observados: List[Dict[str, Any]] = []

    for r in rows:
        yape_tot = round(float(r["pago_ini_yape"] or 0) + float(r["pago_post_yape"] or 0), 2)
        efec_tot = round(float(r["pago_ini_efec"] or 0) + float(r["pago_post_efec"] or 0), 2)
        tarifa = float(r["tarifa"] or 0)
        noches = max(1, int(r["noches"] or 1))
        adic = float(r["adic_snacks"] or 0) + float(r["adic_servicios"] or 0) + float(r["adic_comp"] or 0)
        total_cuenta = round(tarifa * noches + adic, 2)

        ruc_clean = re.sub(r"\D", "", str(r["ruc"] or "").strip())
        empresa_clean = str(r["empresa"] or "").strip().upper()
        if not ruc_clean and empresa_clean and empresa_clean != "-":
            match_emp = mapa_emp_ruc.get(empresa_clean) or mapa_emp_ruc.get(empresa_clean.split()[0])
            if match_emp:
                ruc_clean, empresa_clean = match_emp
        elif len(ruc_clean) == 11 and (not empresa_clean or len(empresa_clean) <= 12):
            for _, (rc_m, rz_m) in mapa_emp_ruc.items():
                if rc_m == ruc_clean and len(rz_m) > len(empresa_clean):
                    empresa_clean = rz_m
                    break
        es_fact = bool(
            len(ruc_clean) == 11
            or "fact" in str(r["tipo_comp"] or "").lower()
            or (empresa_clean and empresa_clean != "-")
        )
        solicita = str(r["solicita_comp"] or "").strip().lower() in ("sí", "si", "yes", "1", "true") or str(r["estado_comp"] or "").strip().lower() == "pendiente"

        if solo_con_yape_o_solicita and not (yape_tot > 0 or solicita or es_fact):
            continue

        monto_emitir = round(float(r["monto_factura"] or 0) or (yape_tot if yape_tot > 0 else total_cuenta), 2)
        tel_clean = re.sub(r"\D", "", str(r["celular"] or "").strip())
        tiene_wsp = len(tel_clean) >= 9
        hab = str(r["hab"] or "").strip()
        fecha_in = str(r["fecha_in_dmy"] or "").strip()
        huesped = str(r["huesped"] or "CLIENTES VARIOS").strip().upper()
        ok_dni, dni_norm, err_dni = normalizar_y_validar_dni_servicio(r["dni"], telefono=tel_clean if tiene_wsp else "", exigir_por_wsp=tiene_wsp)

        glosa_base = f"DIA DE ALOJAMIENTO DEL {_formatear_fecha_glosa(fecha_in)}, HAB N {hab}"
        if es_fact and dni_norm and huesped:
            glosa_base += f", IDENTIFICADO CON DNI {dni_norm} Y NOMBRE {huesped}"

        item_cand = {
            "estancia_uuid": r["estancia_uuid"],
            "id_estancia": r["sheet_row"],
            "fecha_in": fecha_in,
            "hab": hab,
            "tipo": "FACTURA" if es_fact else "BOLETA",
            "huesped": huesped,
            "dni": dni_norm or str(r["dni"] or "").strip(),
            "ruc": ruc_clean,
            "empresa": empresa_clean,
            "celular": tel_clean,
            "tiene_wsp": tiene_wsp,
            "yape_pagado": yape_tot,
            "efectivo_pagado": efec_tot,
            "monto_emitir": monto_emitir,
            "valor_sin_igv": calcular_valor_unitario_105(monto_emitir) if monto_emitir > 0 else 0.0,
            "glosa": glosa_base,
            "accion_sugerida": "EMITIR_Y_WSP" if tiene_wsp else "EMITIR",
        }

        if monto_emitir <= 0:
            item_cand["motivo_observacion"] = "Monto a emitir es S/ 0.00"
            observados.append(item_cand)
            continue

        if es_fact:
            if len(ruc_clean) != 11 or not re.match(r"^(10|15|17|20)", ruc_clean):
                item_cand["motivo_observacion"] = f"RUC inválido o faltante ('{r['ruc'] or ''}')"
                observados.append(item_cand)
                continue
            if not ok_dni:
                item_cand["motivo_observacion"] = err_dni
                observados.append(item_cand)
                continue
            facturas_ruc.append(item_cand)
        else:
            if not ok_dni:
                item_cand["motivo_observacion"] = err_dni
                observados.append(item_cand)
                continue
            if tiene_wsp:
                boletas_wsp.append(item_cand)
            else:
                boletas_sin_wsp.append(item_cand)

    return {
        "ok": True,
        "resumen": {
            "facturas_ruc_cant": len(facturas_ruc),
            "facturas_ruc_monto": round(sum(x["monto_emitir"] for x in facturas_ruc), 2),
            "boletas_wsp_cant": len(boletas_wsp),
            "boletas_wsp_monto": round(sum(x["monto_emitir"] for x in boletas_wsp), 2),
            "boletas_sin_wsp_cant": len(boletas_sin_wsp),
            "boletas_sin_wsp_monto": round(sum(x["monto_emitir"] for x in boletas_sin_wsp), 2),
            "observados_cant": len(observados),
        },
        "facturas_ruc_pendientes": facturas_ruc,
        "boletas_wsp_pendientes": boletas_wsp,
        "boletas_sin_wsp_pendientes": boletas_sin_wsp,
        "observados_corregir": observados,
    }


def ejecutar_estancia_pendiente_por_uuid(
    estancia_uuid: str,
    modo_borrador: bool = True,
    enviar_wsp: bool = True,
    forzar_local: bool = False,
    sincrono: bool = True,
) -> Dict[str, Any]:
    """
    Construye la orden de facturación para una estancia pendiente por su `estancia_uuid` y la ejecuta:
    - `modo_borrador=True`: Entra a SUNAT SEE-SOL y toma la captura oficial de la Vista Preliminar sin emitir.
    - `modo_borrador=False`: Emite el comprobante en SUNAT, guarda el PDF en `02_DB_sqlite_local/comprobantes_pdf/`,
      convierte el PDF a datos en `sunat_ventas` + `sunat_lineas_cache` + `estancias` -> Supabase,
      y si tiene celular y `enviar_wsp=True`, lo envía por el WhatsApp de Recepción.
    """
    pend = listar_pendientes_facturacion_para_aprobacion(20260101, 20261231, solo_con_yape_o_solicita=False)
    todos = (
        pend["facturas_ruc_pendientes"]
        + pend["boletas_wsp_pendientes"]
        + pend["boletas_sin_wsp_pendientes"]
        + pend["observados_corregir"]
    )
    cand = next((x for x in todos if x["estancia_uuid"] == estancia_uuid or str(x["estancia_uuid"]).startswith(estancia_uuid)), None)
    if not cand:
        return {"status": "ERROR", "error": f"No se encontró estancia pendiente con UUID '{estancia_uuid}'."}
    if cand.get("motivo_observacion"):
        return {"status": "ERROR", "error": f"Estancia observada (corregir antes de emitir): {cand['motivo_observacion']}"}

    es_fact = cand["tipo"] == "FACTURA"
    tiene_cel = bool(cand.get("tiene_wsp") and cand.get("celular"))
    accion = "BORRADOR" if modo_borrador else ("EMITIR_Y_WSP" if (enviar_wsp and tiene_cel) else "EMITIR")

    orden = {
        "accion": accion,
        "modoBorrador": bool(modo_borrador),
        "hab": cand["hab"],
        "estanciaUuid": cand["estancia_uuid"],
        "id_estancia": cand["id_estancia"],
        "tipo": cand["tipo"],
        "documento": cand["ruc"] if es_fact else cand["dni"],
        "dni_huesped": cand["dni"],
        "cliente": (cand["empresa"] if es_fact else cand["huesped"]) or "CLIENTES VARIOS",
        "telefono": cand["celular"] if tiene_cel else "",
        "items": [
            {
                "cantidad": 1,
                "descripcion": cand["glosa"],
                "precio_unitario": cand["monto_emitir"],
                "valor_unitario": cand["valor_sin_igv"],
            }
        ],
        "forzar": True,
        "forzar_local": bool(forzar_local),
        "sincrono": bool(sincrono),
    }
    return ejecutar_orden_sunat_wsp(orden)


def _loop_consumidor_cola_madre_fast(my_gen: int) -> None:
    """
    Hilo demonio exclusivo de Laptop 2 Madre (NIVEL_2_MADRE_HOTEL):
    Escucha directamente el buzón `sunat_orden_remota_*` en Supabase cada 0.8s por Keep-Alive
    (sin bloquearse con `pull_from_supabase` de otras tablas), logrando tiempo de reacción <0.8s.
    """
    while True:
        time.sleep(0.8)
        if my_gen != _COLA_MADRE_GEN or not es_laptop_recepcion_directa():
            break
        try:
            procesar_cola_ordenes_sunat_madre()
        except Exception:
            pass


def _asegurar_hilo_consumidor_madre() -> None:
    global _COLA_MADRE_GEN
    if not es_laptop_recepcion_directa():
        return
    # Si este módulo es importado dentro de motor_24x7.py, NO arrancar un segundo hilo consumidor cada 0.8s;
    # el servidor web principal (:8080) es el único dueño del hilo consumidor rápido.
    if os.environ.get("OVALO_PROCESO_ROL") == "MOTOR_24X7":
        return
    _COLA_MADRE_GEN = int(time.time() * 1000)
    t = threading.Thread(
        target=_loop_consumidor_cola_madre_fast,
        args=(_COLA_MADRE_GEN,),
        name=f"OvaloV2SunatColaMadreFast_{_COLA_MADRE_GEN}",
        daemon=True,
    )
    t.start()


_asegurar_hilo_consumidor_madre()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Orquestador SUNAT + PDF->Supabase + WhatsApp Recepción (Sistema v2)")
    parser.add_argument("--listar-pendientes", action="store_true", help="Lista facturas y boletas pendientes para pre-aprobación")
    parser.add_argument("--desde", type=int, default=20261115, help="Fecha inicio YYYYMMDD")
    parser.add_argument("--hasta", type=int, default=20261130, help="Fecha fin YYYYMMDD")
    parser.add_argument("--borrador-uuid", type=str, default="", help="Genera captura Borrador SUNAT (sin emitir) para estancia_uuid")
    parser.add_argument("--emitir-uuid", type=str, default="", help="Emite en SUNAT (y envía WSP si tiene celular) para estancia_uuid")
    parser.add_argument("--sin-wsp", action="store_true", help="Omitir envío por WhatsApp al emitir")
    parser.add_argument("--forzar-local", action="store_true", help="Ejecutar motor Playwright SUNAT en esta PC directamente")
    parser.add_argument("--procesar-cola-madre", action="store_true", help="Procesar órdenes remotas pendientes en Laptop Madre")
    args = parser.parse_args()

    if args.procesar_cola_madre:
        print(json.dumps(procesar_cola_ordenes_sunat_madre(), indent=2, ensure_ascii=False))
    elif args.borrador_uuid:
        res_b = ejecutar_estancia_pendiente_por_uuid(
            args.borrador_uuid,
            modo_borrador=True,
            enviar_wsp=False,
            forzar_local=args.forzar_local,
            sincrono=True,
        )
        res_print = {k: v for k, v in res_b.items() if k != "captura_b64"}
        print(json.dumps(res_print, indent=2, ensure_ascii=False))
    elif args.emitir_uuid:
        res_e = ejecutar_estancia_pendiente_por_uuid(
            args.emitir_uuid,
            modo_borrador=False,
            enviar_wsp=not args.sin_wsp,
            forzar_local=args.forzar_local,
            sincrono=True,
        )
        res_print = {k: v for k, v in res_e.items() if k != "captura_b64"}
        print(json.dumps(res_print, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(listar_pendientes_facturacion_para_aprobacion(args.desde, args.hasta), indent=2, ensure_ascii=False))

