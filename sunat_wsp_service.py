#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Servicio Local de Integración con SUNAT (IGV 10.5% Multi-Ítem), WhatsApp y Padrón DNI/RUC
Reutiliza los motores probados de C:\\Hostal_Sistema:
- scripts/emitir_comprobante_sunat.py
- scripts/brazo_wsp.py
- ejecutar_remoto.py (cuando se opera desde Laptop 1 hacia Laptop 2)
- reglas_auditoria.json (de emitir_y_enviar.py)
"""

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import (
    APISPERU_DNI_URL,
    APISPERU_RUC_URL,
    APISPERU_TOKEN,
    BRAZO_WSP_SCRIPT,
    DESCARGAS_RECEPCION_DIR,
    EJECUTAR_REMOTO_SCRIPT,
    EMITIR_COMPROBANTE_SCRIPT,
    HOSTAL_SISTEMA_DIR,
    REGLAS_AUDITORIA_JSON,
    SCRIPTS_HOSTAL_DIR,
)
from database import db_transaction, get_connection
from domain_logic import actualizar_comprobante_desde_panel_local, normalizar_dni

FACTOR_IGV_HOTEL = 1.105


def calcular_valor_unitario_105(precio_con_igv: float) -> float:
    """Ley MYPE Hotelería/Turismo (IGV 10.5%): Valor Unitario = Precio / 1.105 (4 decimales)"""
    return round(float(precio_con_igv) / FACTOR_IGV_HOTEL, 4)


def _python_cli_bin() -> str:
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-11] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def es_laptop_recepcion_directa() -> bool:
    """
    Detecta si el servidor está corriendo directamente en Laptop 2 (Recepción)
    o en Laptop 1 (Controlador).
    """
    if os.environ.get("HOSTAL_EJECUCION_DIRECTA") == "1":
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

    # 1) Buscar primero en padrón local SQLite (0 ms, funciona 100% sin internet)
    conn = get_connection()
    try:
        pad = conn.execute("SELECT * FROM huespedes_padron WHERE dni = ?", (dni,)).fetchone()
        if pad and pad["nombre"]:
            return {
                "ok": True,
                "dni": dni,
                "nombre": pad["nombre"],
                "nombreCompleto": pad["nombre"],
                "sexo": pad["sexo"] or "M",
                "nac": pad["nac"] or "Perú",
                "reg": pad["reg"] or "Lima",
                "mot": pad["mot"] or "Vacaciones, recreo u ocio",
                "celular": pad["celular"] or "",
                "ruc": pad["ruc"] or "",
                "empresa": pad["empresa"] or "",
                "visitas": int(pad["visitas"] or 1),
                "fuente": "PADRON_LOCAL",
            }
    finally:
        conn.close()

    # 2) Si no está en padrón local y hay internet, consultar APIsPERU (RENIEC)
    try:
        url = f"{APISPERU_DNI_URL}/{dni}?token={APISPERU_TOKEN}"
        with httpx.Client(timeout=4.5) as client:
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

    return {"ok": False, "dni": dni, "error": "Sin conexión a RENIEC o DNI no encontrado (ingrese nombre manualmente)."}


def consultar_ruc_local_o_sunat(ruc_buscado: str) -> Dict[str, Any]:
    ruc = re.sub(r"\D", "", str(ruc_buscado or "").strip())
    if len(ruc) != 11 or not re.match(r"^(10|15|17|20)", ruc):
        return {"ok": False, "error": "RUC inválido (11 dígitos, inicia con 10 o 20)."}

    # 1) Buscar primero en padrón local SQLite (0 ms, funciona 100% sin internet)
    conn = get_connection()
    try:
        emp = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
        if emp and emp["razon_social"]:
            return {
                "ok": True,
                "ruc": ruc,
                "razonSocial": emp["razon_social"],
                "empresa": emp["razon_social"],
                "nombre": emp["razon_social"],
                "estado": "ACTIVO",
                "condicion": "HABIDO",
                "fuente": "HISTORIAL_LOCAL",
            }
    finally:
        conn.close()

    # 2) Consultar APIsPERU (SUNAT) si hay internet
    try:
        url = f"{APISPERU_RUC_URL}/{ruc}?token={APISPERU_TOKEN}"
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                razon = str(data.get("razonSocial") or "").strip().upper()
                if razon:
                    with db_transaction() as c_save:
                        c_save.execute(
                            """
                            INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                            """,
                            (ruc, razon, datetime.now().isoformat()),
                        )
                    return {
                        "ok": True,
                        "ruc": ruc,
                        "razonSocial": razon,
                        "empresa": razon,
                        "nombre": razon,
                        "estado": str(data.get("estado") or "ACTIVO"),
                        "condicion": str(data.get("condicion") or "HABIDO"),
                        "fuente": "SUNAT",
                    }
    except Exception:
        pass

    return {"ok": False, "ruc": ruc, "error": "Sin conexión o RUC no encontrado (escriba la Razón Social manualmente)."}


# ==============================================================================
# 2. AUDITORÍA PREVENTIVA Y BÚSQUEDA DE PDF (REUTILIZA emitir_y_enviar.py)
# ==============================================================================

def cargar_reglas_auditoria() -> Dict[str, Any]:
    if REGLAS_AUDITORIA_JSON.exists():
        try:
            with open(REGLAS_AUDITORIA_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
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


def buscar_pdf_comprobante(nro_cp: str = "", ruc_dni: str = "", cliente: str = "") -> Optional[str]:
    patrones = [
        str(DESCARGAS_RECEPCION_DIR / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "boletas_sunat" / "**" / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "facturas_sunat" / "**" / "*.pdf"),
    ]
    archivos: List[str] = []
    for pat in patrones:
        archivos.extend(glob.glob(pat, recursive=True))
    if not archivos:
        return None
    archivos.sort(key=os.path.getmtime, reverse=True)

    terminos = []
    if nro_cp:
        terminos.append(nro_cp.replace("-", "").lower())
        terminos.append(nro_cp.lower())
    if ruc_dni:
        terminos.append(str(ruc_dni).strip().lower())
    if cliente:
        palabras = [p.lower() for p in str(cliente).split() if len(p) > 3]
        if palabras:
            terminos.append(palabras[0])

    for ruta in archivos[:40]:
        nom = os.path.basename(ruta).lower()
        if any(t in nom for t in terminos if t):
            return ruta
    return archivos[0]


# ==============================================================================
# 3. ORQUESTADOR DE EMISIÓN SUNAT (10.5% IGV MULTI-ÍTEM) Y DESPACHO WHATSAPP
# ==============================================================================

def ejecutar_orden_sunat_wsp(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maneja las órdenes desde el Modal de Facturación:
    - accion = 'EMITIR'       -> Emite en SUNAT (EB01 / E001) con IGV 10.5%, guarda en SQLite y Sheets (CERO envío WSP).
    - accion = 'EMITIR_Y_WSP' -> Emite en SUNAT y despacha el PDF por WhatsApp al teléfono indicado.
    - accion = 'SOLO_WSP'     -> Envía o reenvía un comprobante ya emitido al WhatsApp indicado.
    """
    accion = str(data.get("accion") or "").upper().strip()
    hab = str(data.get("hab") or "").strip()
    estancia_uuid = str(data.get("estanciaUuid") or "").strip()
    id_estancia = data.get("id_estancia") or data.get("fila") or 0
    fila_calzado = data.get("fila_calzado")
    directo_local = es_laptop_recepcion_directa()

    if accion == "SOLO_WSP":
        tel = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
        if not tel:
            return {"status": "ERROR", "error": "Ingrese un número de celular válido para enviar por WhatsApp."}
        nro_cp = str(data.get("nro_comprobante") or "").strip()
        doc = str(data.get("documento") or "").strip()
        cli = str(data.get("cliente") or "").strip()
        pdf_path = str(data.get("archivo") or "").strip()
        if not pdf_path or not os.path.isfile(pdf_path):
            pdf_path = buscar_pdf_comprobante(nro_cp=nro_cp, ruc_dni=doc, cliente=cli) or ""
        msg = str(
            data.get("mensaje")
            or f"Estimado(a) {cli or 'cliente'}, adjuntamos su comprobante electrónico {nro_cp} del Hostal El Óvalo Mariátegui. ¡Gracias por su preferencia!"
        ).strip()

        py_bin = _python_cli_bin()
        if directo_local and BRAZO_WSP_SCRIPT.exists():
            cmd = [py_bin, str(BRAZO_WSP_SCRIPT), "--telefono", tel, "--archivo", pdf_path, "--mensaje", msg]
        else:
            cmd = [
                py_bin,
                str(EJECUTAR_REMOTO_SCRIPT),
                "brazo_wsp.py",
                "--telefono", tel,
                "--archivo", pdf_path,
                "--mensaje", msg,
                "--timeout-remoto", "120",
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=140)
        return {
            "status": "OK" if proc.returncode == 0 else "ERROR",
            "wsp_status": "ENVIADO" if proc.returncode == 0 else "ERROR_WSP",
            "archivo_pdf": pdf_path,
            "stdout": (proc.stdout or "").strip(),
            "error": (proc.stderr or proc.stdout or "").strip() if proc.returncode != 0 else "",
        }

    if accion not in ("EMITIR", "EMITIR_Y_WSP"):
        return {"status": "ERROR", "error": f"Acción '{accion}' no soportada."}

    tipo = str(data.get("tipo") or "BOLETA").upper().strip()
    doc_num = re.sub(r"\D", "", str(data.get("documento") or "").strip())
    cliente = str(data.get("cliente") or "CLIENTES VARIOS").strip().upper()
    telefono = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
    forzar = bool(data.get("forzar", False))
    raw_items = data.get("items") or []

    if tipo == "FACTURA" and (len(doc_num) != 11 or not re.match(r"^(10|15|17|20)", doc_num)):
        return {"status": "ERROR", "error": "Para emitir FACTURA se requiere un RUC válido de 11 dígitos (10 o 20)."}
    if not raw_items:
        return {"status": "ERROR", "error": "Debe incluir al menos un ítem para emitir en SUNAT."}
    if accion == "EMITIR_Y_WSP" and not telefono:
        return {"status": "ERROR", "error": "Para 'Emitir y Enviar a WSP' debe ingresar el número de WhatsApp del cliente."}

    # Normalizar ítems con fórmula Ley MYPE Hotelería IGV 10.5%
    items_normalizados = []
    total_monto = 0.0
    for it in raw_items:
        cant = max(1, int(it.get("cantidad") or 1))
        precio_u = round(float(it.get("precio_unitario") or it.get("monto") or 0), 2)
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

    # Validación de auditoría preventiva (regla de cero extra y umbral de monto)
    ok_audit, msg_audit = validar_monto_preventivo(total_monto, forzar=forzar)
    if not ok_audit:
        return {
            "status": "CONFIRM_REQUIRED",
            "requiereConfirmacion": True,
            "monto": total_monto,
            "mensajeAuditoria": msg_audit,
        }

    payload_sunat = {
        "accion": accion,
        "tipo": tipo,
        "documento": doc_num,
        "cliente": cliente,
        "items": items_normalizados,
        "telefono": telefono,
        "enviar_wsp": (accion == "EMITIR_Y_WSP"),
        "emitir": True,
        "id_estancia": id_estancia,
        "fila_calzado": fila_calzado,
    }

    py_bin = _python_cli_bin()
    if directo_local and EMITIR_COMPROBANTE_SCRIPT.exists():
        cmd = [
            py_bin,
            str(EMITIR_COMPROBANTE_SCRIPT),
            "--payload-json",
            json.dumps(payload_sunat, ensure_ascii=False),
        ]
    else:
        cmd = [
            py_bin,
            str(EJECUTAR_REMOTO_SCRIPT),
            "emitir_comprobante_sunat.py",
            "--payload-json",
            json.dumps(payload_sunat, ensure_ascii=False),
            "--timeout-remoto",
            "240",
        ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=260,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""

    if "JSON_RESULT_START" in out and "JSON_RESULT_END" in out:
        bloque = out.split("JSON_RESULT_START")[1].split("JSON_RESULT_END")[0].strip()
        payload_resp = json.loads(bloque)
    else:
        try:
            payload_resp = json.loads(out.strip())
        except Exception:
            payload_resp = {
                "status": "OK" if proc.returncode == 0 else "ERROR",
                "returncode": proc.returncode,
                "stdout": out.strip(),
                "error": (err or out).strip(),
            }

    # Si la emisión fue exitosa, actualizar SQLite en 0 ms y encolar respaldo hacia Google Sheets
    if payload_resp.get("status") == "OK":
        nro_cp = str(payload_resp.get("nro_comprobante") or "").strip()
        tipo_label = "Factura" if tipo == "FACTURA" else "Boleta"
        try:
            actualizar_comprobante_desde_panel_local({
                "hab": hab,
                "estanciaUuid": estancia_uuid,
                "fila": id_estancia if str(id_estancia).isdigit() else 0,
                "estadoComp": "Emitido",
                "tipoComp": tipo_label,
                "nroComp": nro_cp,
                "montoFact": total_monto,
                "celular": telefono,
                "ruc": doc_num if tipo == "FACTURA" else "",
                "empresa": cliente if tipo == "FACTURA" else "",
            })
            # Insertar en sunat_lineas_cache local para que aparezca de inmediato en la tarjeta verde
            with db_transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO sunat_lineas_cache (
                        nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                        cliente, doc, fecha_emision, estado, descripcion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VIGENTE', ?)
                    """,
                    (
                        nro_cp,
                        tipo,
                        datetime.now().strftime("%d/%m/%Y"),
                        hab,
                        total_monto,
                        total_monto,
                        cliente,
                        doc_num,
                        datetime.now().strftime("%d/%m/%Y"),
                        items_normalizados[0]["descripcion"],
                    ),
                )
        except Exception as e_loc:
            payload_resp["local_update_warn"] = str(e_loc)

    return payload_resp
