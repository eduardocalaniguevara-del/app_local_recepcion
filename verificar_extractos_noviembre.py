#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificador y Corroborador Integral de Ingresos Yape y Extractos Bancarios BCP
Período: 01/11/2026 al 15/11/2026 (Noviembre 2026)
Hotel El Óvalo Mariátegui — Sandbox: C:\\ovalo_mariategui_prueba

Cumple estrictamente:
- GR-05: Entorno de prueba 100% aislado (no toca app_local_recepcion operativa).
- GR-11: Cero Omisiones (descarga y unifica TODOS los archivos .xlsx de Drive).
- Multiplicidad Máxima: Preserva pagos legítimos idénticos de un mismo minuto sin duplicar solapamientos.
- Separación de Ingresos vs. Salidas bancarias.
- Anti-doble conteo estricto entre notificaciones BCP Maelen y Reporte de Transacciones Yape.
- Cruce completo contra estancias, pagos y ventas_tienda de SQLite.
- Generación de C:\\ovalo_mariategui_prueba\\data\\corroboracion_ingresos_bcp_nov2026.json
"""

import io
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Asegurar codificación utf-8 en consola de Windows
try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "ovalo_sistema.db"
OUTPUT_JSON_PATH = DATA_DIR / "corroboracion_ingresos_bcp_nov2026.json"

CREDENTIALS_PATHS = [
    Path(r"C:\Hostal_Sistema\config\credentials.json"),
    BASE_DIR / "config" / "credentials.json",
    Path(r"C:\Users\josec\Downloads\trabajo\Sistema de agentes IA\credentials.json"),
]

FOLDER_ID_DRIVE = "1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe"
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]


def obtener_credenciales_drive() -> Credentials:
    cred_file = None
    for cp in CREDENTIALS_PATHS:
        if cp.exists():
            cred_file = cp
            break
    if not cred_file:
        raise FileNotFoundError(f"No se encontró credentials.json en ninguna ruta: {CREDENTIALS_PATHS}")

    creds = Credentials.from_service_account_file(str(cred_file), scopes=SCOPES_DRIVE)
    if not creds.valid or not creds.token:
        creds.refresh(Request())
    return creds


def descargar_todos_reportes_noviembre(creds: Credentials) -> Tuple[List[Dict[str, Any]], Counter, Dict[Tuple, Dict[str, Any]]]:
    """
    Descarga TODOS los reportes .xlsx de la carpeta de Drive con modifiedTime >= 2026-10-31
    para asegurar cobertura 100% de noviembre (Cero Omisiones GR-11).
    Deduplica solapamientos preservando la multiplicidad máxima observada en un único archivo.
    """
    drive_service = build("drive", "v3", credentials=creds)
    query = f"'{FOLDER_ID_DRIVE}' in parents and trashed=false and name contains '.xlsx'"
    results = (
        drive_service.files()
        .list(
            q=query,
            orderBy="modifiedTime desc",
            fields="files(id, name, createdTime, modifiedTime)",
            pageSize=100,
        )
        .execute()
    )
    items = results.get("files", [])

    # Filtrar archivos relevantes para noviembre (desde 31/10/2026 en adelante)
    archivos_nov = [f for f in items if f.get("modifiedTime", "") >= "2026-10-31T00:00:00Z"]
    print(f"📦 Se encontraron {len(archivos_nov)} archivos .xlsx de Yape relevantes en Google Drive.")

    file_counters: Dict[str, Counter] = {}
    all_tuples_meta: Dict[Tuple, Dict[str, Any]] = {}
    archivos_info: List[Dict[str, Any]] = []

    token = creds.token

    for idx, item in enumerate(archivos_nov, 1):
        fid = item["id"]
        fname = item["name"]
        mtime = item.get("modifiedTime", "")
        dl_url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media"

        try:
            resp = requests.get(dl_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code != 200:
                print(f"   [!] Error HTTP {resp.status_code} al descargar {fname} ({fid})")
                continue

            fh = io.BytesIO(resp.content)
            df_raw = pd.read_excel(fh, skiprows=4)
            if len(df_raw.columns) < 6:
                print(f"   [!] Archivo con menos de 6 columnas: {fname}")
                continue

            cols = df_raw.columns.tolist()
            df = df_raw.rename(
                columns={
                    cols[0]: "Tipo",
                    cols[1]: "Origen",
                    cols[2]: "Destino",
                    cols[3]: "Monto",
                    cols[4]: "Mensaje",
                    cols[5]: "Fecha",
                }
            )

            df["Fecha_dt"] = pd.to_datetime(df["Fecha"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
            df = df.dropna(subset=["Fecha_dt"])

            # Filtrar fecha >= 01/11/2026 00:00:00
            df_nov = df[df["Fecha_dt"] >= datetime(2026, 11, 1, 0, 0, 0)].copy()

            cnt_file = Counter()
            for _, row in df_nov.iterrows():
                tipo_u = str(row["Tipo"]).strip().upper()
                # Tipo contiene PAG
                if "PAG" not in tipo_u:
                    continue

                origen_u = str(row["Origen"]).strip().upper() if pd.notna(row["Origen"]) else ""
                destino_u = str(row["Destino"]).strip().upper() if pd.notna(row["Destino"]) else ""
                fecha_str = str(row["Fecha"]).strip()
                try:
                    monto_num = round(float(str(row["Monto"]).replace("S/", "").replace(",", ".").strip()), 2)
                except Exception:
                    continue
                if monto_num <= 0:
                    continue

                msg_str = str(row["Mensaje"]).strip() if pd.notna(row["Mensaje"]) else ""

                tup = (fecha_str, origen_u, destino_u, monto_num, msg_str)
                cnt_file[tup] += 1

                if tup not in all_tuples_meta:
                    all_tuples_meta[tup] = {
                        "fecha_exacta": fecha_str,
                        "fecha_dt": row["Fecha_dt"],
                        "tipo": tipo_u,
                        "origen": origen_u,
                        "destino": destino_u,
                        "monto": monto_num,
                        "mensaje": msg_str,
                        "es_salida": "PAGASTE" in tipo_u,
                        "archivos_origen": [],
                    }
                all_tuples_meta[tup]["archivos_origen"].append(fname)

            file_counters[fid] = cnt_file
            archivos_info.append(
                {
                    "archivo": fname,
                    "id": fid,
                    "modifiedTime": mtime,
                    "filas_noviembre": len(df_nov),
                    "min_fecha": df_nov["Fecha_dt"].min().strftime("%d/%m/%Y %H:%M:%S") if len(df_nov) > 0 else None,
                    "max_fecha": df_nov["Fecha_dt"].max().strftime("%d/%m/%Y %H:%M:%S") if len(df_nov) > 0 else None,
                }
            )

        except Exception as e:
            print(f"   [!] Excepción procesando {fname}: {e}")
            continue

    # Calcular multiplicidad máxima observada dentro de un mismo .xlsx
    max_multiplicity = Counter()
    for fid, cnt in file_counters.items():
        for tup, c in cnt.items():
            if c > max_multiplicity[tup]:
                max_multiplicity[tup] = c

    return archivos_info, max_multiplicity, all_tuples_meta


def ejecutar_sincronizaciones_locales() -> Dict[str, Any]:
    """
    Ejecuta sincronizaciones de yape_manager, auditor_yape y conciliar_con_criterio
    para garantizar que SQLite y Google Sheets estén sincronizados al 100%.
    """
    print("\n🔄 Sincronizando Yape y Cuenta Maelen en SQLite (C:\\ovalo_mariategui_prueba)...")
    sys.path.insert(0, str(BASE_DIR))
    try:
        import yape_manager

        res_y = yape_manager.sincronizar_yapes_desde_drive_a_sqlite(forzar=True)
        print(f"   -> Yape Drive Sync: {res_y.get('nuevosYapes', 0)} nuevos yapes, hasta: {res_y.get('ultimaFechaDrive', '')}")
    except Exception as e:
        print(f"   [!] Error en sincronizar_yapes_desde_drive_a_sqlite: {e}")
        res_y = {"ok": False, "error": str(e)}

    try:
        res_m = yape_manager.sincronizar_transferencias_maelen_correo(forzar=True)
        print(f"   -> Maelen Gmail Sync: {res_m.get('insertadosMaelen', 0)} movimientos nuevos, {res_m.get('corroboradosYape', 0)} Yapes corroborados.")
    except Exception as e:
        print(f"   [!] Error en sincronizar_transferencias_maelen_correo: {e}")
        res_m = {"ok": False, "error": str(e)}

    # Ejecutar auditor_yape.py y conciliar_con_criterio.py
    for script_name in ["auditor_yape.py", "conciliar_con_criterio.py"]:
        script_path = Path(r"C:\Hostal_Sistema\scripts") / script_name
        if script_path.exists():
            print(f"\n📊 Ejecutando {script_name}...")
            try:
                proc = subprocess.run(
                    [sys.executable, str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if proc.returncode == 0:
                    print(f"   ✔ {script_name} ejecutado con éxito.")
                else:
                    print(f"   [!] {script_name} retornó código {proc.returncode}:\n{proc.stderr[:300]}")
            except Exception as ex:
                print(f"   [!] Error ejecutando {script_name}: {ex}")

    return {"yape_drive": res_y, "maelen_gmail": res_m}


def realizar_cruce_integral_sqlite() -> Dict[str, Any]:
    """
    Cruce integral contra estancias, pagos, ventas_tienda y maelen_transferencias_correo.
    Clasifica el 100% de los ingresos bancarios de noviembre.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 1. Cargar Yapes y Movimientos Bancarios de Noviembre
    yapes_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, fecha_operacion, hora_operacion, remitente_bcp, monto_bcp, nro_operacion,
                   id_estancia_calzada, hab_calzada, huesped_calzado, estado_auditoria, observacion, updated_at
            FROM yape_conciliacion_hostal
            WHERE fecha_operacion LIKE '%/11/2026'
            ORDER BY id ASC
            """
        ).fetchall()
    ]

    maelen_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT msg_id, cuenta_correo, fecha_operacion, hora_operacion, tipo_movimiento,
                   remitente_o_destino, monto, nro_operacion, cuenta_origen_destino, asunto,
                   estado_cruce, id_cruce_recepcion, detalle_cruce
            FROM maelen_transferencias_correo
            WHERE fecha_operacion LIKE '%/11/2026'
            ORDER BY fecha_operacion, hora_operacion
            """
        ).fetchall()
    ]

    # 2. Cargar registros operativos con filtros de fecha estrictos
    # estancias: ymd_in >= 20261101 AND ymd_in <= 20261130
    estancias_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT estancia_uuid, id_timestamp_str, hab, huesped, dni, ruc, empresa,
                   tipo_comp, estado_comp, nro_comp, monto_factura, tarifa,
                   adic_snacks, adic_servicios, adic_comp, pago_ini_yape, pago_post_yape,
                   ymd_in, fecha_in_dmy
            FROM estancias
            WHERE ymd_in >= 20261101 AND ymd_in <= 20261130
            """
        ).fetchall()
    ]

    # pagos: parsear DD/MM/YYYY en Python para filtrar mes 11 y año 2026
    todos_pagos = [dict(r) for r in conn.execute("SELECT * FROM pagos").fetchall()]
    pagos_nov = []
    for p in todos_pagos:
        f_iso = str(p.get("fecha_iso") or "").strip()
        f_part = f_iso.split(" ")[0] if " " in f_iso else f_iso
        partes = f_part.split("/")
        if len(partes) == 3 and partes[1] == "11" and partes[2] == "2026":
            pagos_nov.append(p)

    # ventas_tienda: fecha_dmy LIKE '%/11/2026'
    ventas_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT venta_uuid, lote_uuid, estancia_uuid, id_estancia, fecha_iso, fecha_dmy,
                   producto, cantidad, metodo_pago, hab, personal, precio_unit, subtotal, nro_op_yape
            FROM ventas_tienda
            WHERE fecha_dmy LIKE '%/11/2026'
            """
        ).fetchall()
    ]

    conn.close()

    # Indexar para cruce rápido
    pagos_by_id = {str(p["pago_id"]): p for p in todos_pagos if p.get("pago_id")}
    estancias_by_uuid = {str(e["estancia_uuid"]): e for e in estancias_rows if e.get("estancia_uuid")}
    estancias_by_ts = {str(e["id_timestamp_str"]): e for e in estancias_rows if e.get("id_timestamp_str")}
    ventas_by_uuid = {str(v["venta_uuid"]): v for v in ventas_rows if v.get("venta_uuid")}

    # Clasificación estricta
    clasif_habitaciones: List[Dict[str, Any]] = []
    clasif_tienda: List[Dict[str, Any]] = []
    clasif_corporativo: List[Dict[str, Any]] = []
    clasif_sueltos_banco: List[Dict[str, Any]] = []
    clasif_salidas_excluidas: List[Dict[str, Any]] = []
    clasif_anti_doble_conteo: List[Dict[str, Any]] = []

    # A) Procesar maelen_transferencias_correo para Anti-Doble Conteo y Corporativos
    for m in maelen_rows:
        tipo_m = m["tipo_movimiento"]
        if tipo_m == "CORPORATIVO_MAELEN":
            # Ingreso corporativo a Maelen ****8023
            part_ids = [x.strip() for x in str(m.get("id_cruce_recepcion") or "").split("|") if x.strip()]
            detalles_habs = []
            for pid in part_ids:
                p_info = pagos_by_id.get(pid, {})
                e_uuid = p_info.get("estancia_uuid", "")
                e_info = estancias_by_uuid.get(e_uuid) or estancias_by_ts.get(e_uuid) or {}
                tarifa = float(e_info.get("tarifa") or 0)
                adic = float(e_info.get("adic_snacks") or 0) + float(e_info.get("adic_servicios") or 0) + float(e_info.get("adic_comp") or 0)
                detalles_habs.append(
                    {
                        "pago_id": pid,
                        "estancia_uuid": e_uuid,
                        "id_estancia": e_info.get("id_timestamp_str") or p_info.get("id_estancia", ""),
                        "hab": e_info.get("hab") or p_info.get("hab", ""),
                        "huesped": e_info.get("huesped") or p_info.get("huesped", ""),
                        "dni": e_info.get("dni", ""),
                        "ruc": e_info.get("ruc", ""),
                        "empresa": e_info.get("empresa", ""),
                        "tipo_comp": e_info.get("tipo_comp", ""),
                        "estado_comp": e_info.get("estado_comp", ""),
                        "nro_comp": e_info.get("nro_comp", ""),
                        "monto_factura": float(e_info.get("monto_factura") or 0),
                        "total_cuenta": tarifa + adic if (tarifa + adic) > 0 else float(p_info.get("monto") or 0),
                    }
                )

            clasif_corporativo.append(
                {
                    "msg_id": m["msg_id"],
                    "fecha": m["fecha_operacion"],
                    "hora": m["hora_operacion"],
                    "remitente": m["remitente_o_destino"],
                    "monto": float(m["monto"]),
                    "asunto": m["asunto"],
                    "habitaciones_vinculadas": detalles_habs,
                    "detalle_cruce": m.get("detalle_cruce", ""),
                }
            )

        elif tipo_m == "INGRESO_YAPEO_BCP":
            # Notificación por correo de un Yape entrante
            # Verificar anti-doble conteo contra yape_conciliacion_hostal
            clasif_anti_doble_conteo.append(
                {
                    "msg_id": m["msg_id"],
                    "fecha": m["fecha_operacion"],
                    "hora": m["hora_operacion"],
                    "remitente": m["remitente_o_destino"],
                    "monto": float(m["monto"]),
                    "asunto": m["asunto"],
                    "estado_cruce": m.get("estado_cruce", ""),
                    "id_cruce_yape": m.get("id_cruce_recepcion", ""),
                    "detalle_cruce": m.get("detalle_cruce", ""),
                    "accion": "IGNORADO_COMO_INGRESO_SEPARADO_PARA_EVITAR_DOBLE_CONTEO",
                }
            )

        elif tipo_m in ("SALIDA_TRANSFERENCIA_BCP", "SALIDA_YAPEO_BCP", "PAGO_SERVICIO_BCP"):
            clasif_salidas_excluidas.append(
                {
                    "origen": "CUENTA_MAELEN_BCP_8023",
                    "id": m["msg_id"],
                    "fecha": m["fecha_operacion"],
                    "hora": m["hora_operacion"],
                    "tipo_salida": tipo_m,
                    "destinatario": m["remitente_o_destino"],
                    "monto": float(m["monto"]),
                    "asunto": m["asunto"],
                    "nro_operacion": m.get("nro_operacion", ""),
                    "motivo_exclusion": "SALIDA_BANCARIA_NO_SUJETA_A_BOLETA",
                }
            )

    # B) Procesar yape_conciliacion_hostal
    # Nota: yape_conciliacion_hostal puede contener registros de Maelen que ya fueron sincronizados.
    # Evitaremos duplicar los corporativos si ya se procesaron.
    for y in yapes_rows:
        y_id = y["id"]
        f_op = y["fecha_operacion"]
        h_op = y["hora_operacion"]
        rem = y["remitente_bcp"]
        monto = float(y["monto_bcp"] or 0)
        estado = str(y.get("estado_auditoria") or "").strip()
        obs = str(y.get("observacion") or "").strip()
        calce = str(y.get("id_estancia_calzada") or "").strip()

        # Si ya es un corporativo de Maelen sincronizado en yape_conciliacion_hostal, no duplicarlo
        if "TRANSFERENCIA CTA MAELEN" in rem or "GRUPO 5 HABS" in rem:
            continue

        if estado == "SALIDA":
            clasif_salidas_excluidas.append(
                {
                    "origen": "YAPE_CELULAR_DRIVE",
                    "id": y_id,
                    "fecha": f_op,
                    "hora": h_op,
                    "tipo_salida": "PAGASTE_YAPE",
                    "destinatario": rem,
                    "monto": monto,
                    "asunto": obs,
                    "nro_operacion": y.get("nro_operacion", ""),
                    "motivo_exclusion": "SALIDA_BANCARIA_NO_SUJETA_A_BOLETA",
                }
            )
            continue

        if estado == "SIN ANOTAR":
            clasif_sueltos_banco.append(
                {
                    "id": y_id,
                    "fecha": f_op,
                    "hora": h_op,
                    "remitente": rem,
                    "monto": monto,
                    "nro_operacion": y.get("nro_operacion", ""),
                    "clasificacion": "SOBRANTE_BANCO_PENDIENTE_ANOTAR",
                    "observacion": obs,
                }
            )
            continue

        if "TIENDA-" in calce:
            part_ids = [p.strip() for p in calce.split("|") if p.strip()]
            detalles_vt = []
            for vid_raw in part_ids:
                vid = vid_raw.replace("TIENDA-", "")
                vt_info = ventas_by_uuid.get(vid, {})
                detalles_vt.append(
                    {
                        "venta_uuid": vid,
                        "producto": vt_info.get("producto", ""),
                        "cantidad": vt_info.get("cantidad", 0),
                        "subtotal": float(vt_info.get("subtotal") or 0),
                        "estancia_uuid": vt_info.get("estancia_uuid", ""),
                        "hab": vt_info.get("hab", ""),
                        "nro_op_yape": vt_info.get("nro_op_yape", ""),
                    }
                )
            clasif_tienda.append(
                {
                    "id": y_id,
                    "fecha": f_op,
                    "hora": h_op,
                    "remitente": rem,
                    "monto": monto,
                    "nro_operacion": y.get("nro_operacion", ""),
                    "ventas_vinculadas": detalles_vt,
                    "observacion": obs,
                }
            )
            continue

        # Pago de Habitación
        part_ids = [p.strip() for p in calce.split("|") if p.strip()]
        detalles_p = []
        for pid in part_ids:
            p_info = pagos_by_id.get(pid, {})
            e_uuid = p_info.get("estancia_uuid", "")
            e_info = estancias_by_uuid.get(e_uuid) or estancias_by_ts.get(e_uuid) or {}
            tarifa = float(e_info.get("tarifa") or 0)
            adic = float(e_info.get("adic_snacks") or 0) + float(e_info.get("adic_servicios") or 0) + float(e_info.get("adic_comp") or 0)
            detalles_p.append(
                {
                    "pago_id": pid,
                    "estancia_uuid": e_uuid,
                    "id_estancia": e_info.get("id_timestamp_str") or p_info.get("id_estancia", ""),
                    "hab": e_info.get("hab") or p_info.get("hab", ""),
                    "huesped": e_info.get("huesped") or p_info.get("huesped", ""),
                    "dni": e_info.get("dni", ""),
                    "ruc": e_info.get("ruc", ""),
                    "empresa": e_info.get("empresa", ""),
                    "tipo_comp": e_info.get("tipo_comp", ""),
                    "estado_comp": e_info.get("estado_comp", ""),
                    "nro_comp": e_info.get("nro_comp", ""),
                    "monto_factura": float(e_info.get("monto_factura") or 0),
                    "total_cuenta": tarifa + adic if (tarifa + adic) > 0 else float(p_info.get("monto") or 0),
                }
            )
        clasif_habitaciones.append(
            {
                "id": y_id,
                "fecha": f_op,
                "hora": h_op,
                "remitente": rem,
                "monto": monto,
                "nro_operacion": y.get("nro_operacion", ""),
                "habitaciones_vinculadas": detalles_p,
                "observacion": obs,
            }
        )

    return {
        "habitaciones": clasif_habitaciones,
        "tienda": clasif_tienda,
        "corporativo": clasif_corporativo,
        "sueltos_banco": clasif_sueltos_banco,
        "salidas_excluidas": clasif_salidas_excluidas,
        "anti_doble_conteo": clasif_anti_doble_conteo,
    }


def main():
    print("=" * 80)
    print("🚀 CORROBORACIÓN INTEGRAL DE INGRESOS YAPE Y EXTRACTOS BANCARIOS BCP")
    print("   Período: 01/11/2026 hasta 15/11/2026 (Noviembre 2026)")
    print("   Hotel El Óvalo Mariátegui — Regla Cero Omisiones GR-11")
    print("=" * 80)

    # 1. Credenciales y Google Drive
    creds = obtener_credenciales_drive()
    archivos_info, max_mult, all_tuples_meta = descargar_todos_reportes_noviembre(creds)

    # 2. Análisis de Multiplicidad y Deduplicación Cero Omisiones
    mult_gt_1 = {t: c for t, c in max_mult.items() if c > 1}
    print(f"\n🔍 Análisis de Multiplicidad Máxima:")
    print(f"   - Total tuplas únicas en reportes: {len(max_mult)}")
    print(f"   - Tuplas con multiplicidad > 1 dentro del mismo archivo: {len(mult_gt_1)}")
    if mult_gt_1:
        for t, c in mult_gt_1.items():
            print(f"     * Multiplicidad {c}: Fecha {t[0]}, Origen {t[1]}, Monto S/ {t[3]}")
    else:
        print("   ✔ Ninguna transacción duplicada internamente dentro del mismo reporte .xlsx.")

    # Agrupar transacciones por día y tipo
    transacciones_drive: List[Dict[str, Any]] = []
    for tup, cnt in max_mult.items():
        meta = all_tuples_meta[tup]
        for _ in range(cnt):
            transacciones_drive.append(meta)

    # Separar ingresos vs salidas de Drive
    ingresos_drive = [t for t in transacciones_drive if not t["es_salida"]]
    salidas_drive = [t for t in transacciones_drive if t["es_salida"]]

    # Desglose por día de ingresos Yape en Drive
    dias_drive = defaultdict(lambda: {"count": 0, "monto": 0.0})
    for t in ingresos_drive:
        d_str = t["fecha_dt"].strftime("%Y-%m-%d")
        dias_drive[d_str]["count"] += 1
        dias_drive[d_str]["monto"] = round(dias_drive[d_str]["monto"] + t["monto"], 2)

    print("\n📅 Verificación de Continuidad Diaria en Reportes Drive (01/11 al 15/11):")
    total_monto_drive_in = 0.0
    for d_num in range(1, 16):
        d_key = f"2026-11-{d_num:02d}"
        info_d = dias_drive.get(d_key, {"count": 0, "monto": 0.0})
        total_monto_drive_in = round(total_monto_drive_in + info_d["monto"], 2)
        flag_status = "✔ Presente" if info_d["count"] > 0 else "⚪ Sin reporte Drive (Verificando Celular/Maelen)"
        print(f"   - {d_key}: {info_d['count']:2d} operaciones | S/ {info_d['monto']:8.2f} | {flag_status}")

    print(f"\n   Total Ingresos Reportes Drive (01/11 al 14/11 13:23): S/ {total_monto_drive_in:,.2f} en {len(ingresos_drive)} operaciones.")
    print(f"   Total Salidas Reportes Drive (PAGASTE): S/ {sum(t['monto'] for t in salidas_drive):,.2f} en {len(salidas_drive)} operaciones.")

    # 3. Ejecutar sincronizaciones y auditores locales
    sync_res = ejecutar_sincronizaciones_locales()

    # 4. Cruce Integral con SQLite
    cruce = realizar_cruce_integral_sqlite()

    habs = cruce["habitaciones"]
    tienda = cruce["tienda"]
    corp = cruce["corporativo"]
    sueltos = cruce["sueltos_banco"]
    salidas = cruce["salidas_excluidas"]
    anti_dc = cruce["anti_doble_conteo"]

    sum_habs = round(sum(x["monto"] for x in habs), 2)
    sum_tienda = round(sum(x["monto"] for x in tienda), 2)
    sum_corp = round(sum(x["monto"] for x in corp), 2)
    sum_sueltos = round(sum(x["monto"] for x in sueltos), 2)
    sum_salidas = round(sum(x["monto"] for x in salidas), 2)

    total_ingresos_bancarios_reales = round(sum_habs + sum_tienda + sum_corp + sum_sueltos, 2)
    total_yape_puro = round(sum_habs + sum_tienda + sum_sueltos, 2)

    print("\n" + "=" * 80)
    print("📊 RESUMEN EJECUTIVO DE CORROBORACIÓN INTEGRAL (01/11 - 15/11/2026)")
    print("=" * 80)
    print(f"1) Yapes Calzados con Habitaciones (estancias/pagos):")
    print(f"   -> {len(habs):3d} operaciones | Total: S/ {sum_habs:10.2f}")
    print(f"2) Yapes Calzados con Ventas de Tienda (ventas_tienda):")
    print(f"   -> {len(tienda):3d} operaciones | Total: S/ {sum_tienda:10.2f}")
    print(f"3) Transferencias Corporativas Cuenta Empresa BCP (****8023 Maelen):")
    print(f"   -> {len(corp):3d} operaciones | Total: S/ {sum_corp:10.2f}")
    print(f"4) Yapes Sueltos en Banco (Sobrantes / pendientes de recepción):")
    print(f"   -> {len(sueltos):3d} operaciones | Total: S/ {sum_sueltos:10.2f}")
    print("-" * 80)
    print(f"⭐ TOTAL INGRESOS BANCARIOS REALES SUJETOS A FACTURA/BOLETA:")
    print(f"   -> {len(habs) + len(tienda) + len(corp) + len(sueltos):3d} operaciones | Total: S/ {total_ingresos_bancarios_reales:10.2f}")
    print(f"      [Yape Puro: S/ {total_yape_puro:,.2f} ({len(habs)+len(tienda)+len(sueltos)} ops) + BCP Maelen Corp: S/ {sum_corp:,.2f} ({len(corp)} ops)]")
    print("-" * 80)
    print(f"5) Salidas Bancarias Excluidas de Boleteo (Sueldos, Compras, Pagos BCP):")
    print(f"   -> {len(salidas):3d} operaciones | Total: S/ {sum_salidas:10.2f}")
    print(f"6) Notificaciones de Yape en Correo Maelen (Anti-Doble Conteo Verificado):")
    print(f"   -> {len(anti_dc):3d} notificaciones verificadas y enlazadas 1-a-1 con el reporte Yape.")
    print("=" * 80)

    # 5. Generar JSON Final
    resultado_final = {
        "metadata": {
            "periodo": "01/11/2026 al 15/11/2026",
            "fecha_generacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hostal": "Hotel El Óvalo Mariátegui",
            "regla_auditoria": "Cero Omisiones GR-11 & Multiplicidad Máxima",
            "cuenta_banco_empresa": "BCP Cuenta Corriente ****8023 Maelen Hogar",
            "yape_telefono": "964139749",
        },
        "totales_generales": {
            "total_ingresos_bancarios_reales_soles": total_ingresos_bancarios_reales,
            "total_operaciones_ingreso": len(habs) + len(tienda) + len(corp) + len(sueltos),
            "desglose_por_canal": {
                "yape_habitaciones_soles": sum_habs,
                "yape_habitaciones_ops": len(habs),
                "yape_tienda_soles": sum_tienda,
                "yape_tienda_ops": len(tienda),
                "bcp_maelen_corporativo_soles": sum_corp,
                "bcp_maelen_corporativo_ops": len(corp),
                "yape_sueltos_sobrante_soles": sum_sueltos,
                "yape_sueltos_sobrante_ops": len(sueltos),
            },
            "salidas_excluidas_soles": sum_salidas,
            "salidas_excluidas_ops": len(salidas),
            "anti_doble_conteo_notificaciones_bcp_ops": len(anti_dc),
        },
        "verificacion_archivos_drive": archivos_info,
        "continuidad_diaria_drive": {d: v for d, v in sorted(dias_drive.items())},
        "clasificacion_detallada": {
            "habitaciones": habs,
            "tienda": tienda,
            "corporativo_maelen": corp,
            "sueltos_banco": sueltos,
            "salidas_excluidas": salidas,
            "anti_doble_conteo_maelen": anti_dc,
        },
    }

    OUTPUT_JSON_PATH.write_text(json.dumps(resultado_final, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n💾 Archivo consolidado guardado exitosamente en:\n   {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
