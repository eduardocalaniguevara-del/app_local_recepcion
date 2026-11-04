# -*- coding: utf-8 -*-
"""
pases_logic.py — Control Interno de Visitas / Pases por Día
===========================================================
- Registra pases aceptados como Cargo a la Cuenta (Servicio Adicional = 'Pase', S/ 10 por defecto)
  SIN exponer el DNI ni el nombre del visitante en Registro_Estancias ni en Registro_Pagos.
- Registra también visitas rechazadas (monto = 0) con hora, habitación opcional y motivo.
- Mantiene conteo diario en vivo y sincronización privada entre equipos vía `import_Control_Pases`.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from database import db_transaction, get_connection
import domain_logic as dl
from sunat_wsp_service import consultar_dni_local_o_reniec

PASES_CABECERA = [
    "PASE_UUID", "FECHA", "HORA", "HAB", "ESTANCIA_UUID", "ID_ESTANCIA",
    "HUESPED_TITULAR", "DNI_VISITA", "NOMBRE_VISITA", "ESTADO",
    "MONTO", "MOTIVO", "EQUIPO", "CREATED_AT",
]


def _fecha_a_dmy(valor: Optional[str] = None) -> str:
    s = str(valor or "").strip()
    if not s:
        return datetime.now().strftime("%d/%m/%Y")
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        y, m, d = s[:10].split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    return dl.normalizar_fecha_dmy(s)


def _dmy_a_iso(dmy: str) -> str:
    dt = datetime.strptime(dl.normalizar_fecha_dmy(dmy), "%d/%m/%Y").date()
    return dt.isoformat()


def _habitaciones_ocupadas_activas(conn) -> List[Dict[str, Any]]:
    """Devuelve las habitaciones actualmente ocupadas para elegirlas en 1 click."""
    rows = conn.execute(
        """
        SELECT estancia_uuid, id_timestamp_str, hab, huesped, check_in_ymd, estado_estancia
        FROM estancias
        WHERE lower(coalesce(estado_estancia, '')) NOT LIKE '%liber%'
          AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
          AND lower(coalesce(estado_estancia, '')) NOT LIKE '%finaliz%'
          AND trim(coalesce(hab, '')) != ''
          AND trim(coalesce(huesped, '')) NOT IN ('', '-')
        ORDER BY CAST(hab AS INTEGER) ASC, check_in_ymd DESC, sheet_row DESC
        """
    ).fetchall()
    por_hab: Dict[str, Dict[str, Any]] = {}
    hoy_ymd = int(datetime.now().strftime("%Y%m%d"))
    min_ymd = int((datetime.now().date() - timedelta(days=25)).strftime("%Y%m%d"))
    for r in rows:
        hab = str(r["hab"] or "").strip()
        if not hab or hab in por_hab:
            continue
        ci_ymd = int(r["check_in_ymd"] or 0)
        if ci_ymd < min_ymd or ci_ymd > hoy_ymd + 1:
            continue
        por_hab[hab] = {
            "hab": hab,
            "huesped": str(r["huesped"] or "").strip(),
            "estanciaUuid": str(r["estancia_uuid"] or "").strip(),
            "idEstancia": str(r["id_timestamp_str"] or "").strip(),
        }
    return sorted(por_hab.values(), key=lambda x: (int(x["hab"]) if x["hab"].isdigit() else 9999, x["hab"]))


def consultar_dni_visita_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    dni = "".join(ch for ch in str(datos.get("dni") or "").strip() if ch.isalnum())
    if not dni:
        return {"encontrado": False, "dni": "", "nombre": "", "rechazosPrevios": [], "visitasPrevias": 0}

    nombre = ""
    if len(dni) == 8 and dni.isdigit():
        try:
            info = consultar_dni_local_o_reniec(dni)
            if isinstance(info, dict) and info.get("nombre"):
                nombre = str(info["nombre"]).strip()
        except Exception:
            nombre = ""

    conn = get_connection()
    try:
        if not nombre:
            prev = conn.execute(
                "SELECT nombre_visita FROM control_pases WHERE dni_visita = ? AND trim(nombre_visita) != '' ORDER BY ymd DESC LIMIT 1",
                (dni,),
            ).fetchone()
            if prev:
                nombre = str(prev["nombre_visita"] or "").strip()

        rechazos = [
            {
                "fecha": str(r["fecha_dmy"] or ""),
                "hora": str(r["hora"] or ""),
                "hab": str(r["hab"] or ""),
                "motivo": str(r["motivo_rechazo"] or ""),
            }
            for r in conn.execute(
                "SELECT fecha_dmy, hora, hab, motivo_rechazo FROM control_pases WHERE dni_visita = ? AND estado = 'RECHAZADO' ORDER BY ymd DESC, hora DESC LIMIT 5",
                (dni,),
            ).fetchall()
        ]
        tot_vis = conn.execute(
            "SELECT COUNT(*) AS c FROM control_pases WHERE dni_visita = ? AND estado = 'ACEPTADO'",
            (dni,),
        ).fetchone()
        return {
            "encontrado": bool(nombre),
            "dni": dni,
            "nombre": nombre,
            "rechazosPrevios": rechazos,
            "visitasPrevias": int(tot_vis["c"] or 0) if tot_vis else 0,
        }
    finally:
        conn.close()


def obtener_visitas_pases_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    fecha_dmy = _fecha_a_dmy(datos.get("fecha"))
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)
    hoy_dmy = datetime.now().strftime("%d/%m/%Y")

    conn = get_connection()
    try:
        habs_activas = _habitaciones_ocupadas_activas(conn)
        rows = conn.execute(
            """
            SELECT * FROM control_pases
            WHERE ymd = ?
            ORDER BY hora DESC, created_at DESC
            """,
            (ymd,),
        ).fetchall()

        conteo_hab: Dict[str, int] = {}
        aceptados = 0
        rechazados = 0
        total_cargo = 0.0
        registros: List[Dict[str, Any]] = []

        for r in rows:
            est = str(r["estado"] or "ACEPTADO").upper()
            hab_r = str(r["hab"] or "-").strip() or "-"
            monto_r = round(float(r["monto"] or 0.0), 2)
            if est == "RECHAZADO":
                rechazados += 1
            else:
                aceptados += 1
                total_cargo = round(total_cargo + monto_r, 2)
                if hab_r != "-":
                    conteo_hab[hab_r] = conteo_hab.get(hab_r, 0) + 1

            registros.append({
                "paseUuid": str(r["pase_uuid"]),
                "fecha": str(r["fecha_dmy"] or fecha_dmy),
                "hora": str(r["hora"] or ""),
                "hab": hab_r,
                "huespedTitular": str(r["huesped_titular"] or ""),
                "dni": str(r["dni_visita"] or ""),
                "nombreVisita": str(r["nombre_visita"] or ""),
                "estado": est,
                "monto": monto_r,
                "motivo": str(r["motivo_rechazo"] or ""),
            })

        for h in habs_activas:
            h["pasesHoy"] = conteo_hab.get(h["hab"], 0)

        por_hab_lista = [
            {"hab": k, "conteo": v}
            for k, v in sorted(conteo_hab.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]))
        ]

        return {
            "fechaDmy": fecha_dmy,
            "fechaIso": _dmy_a_iso(fecha_dmy),
            "esHoy": fecha_dmy == hoy_dmy,
            "ahoraHM": datetime.now().strftime("%H:%M"),
            "habitacionesOcupadas": habs_activas,
            "registros": registros,
            "resumen": {
                "aceptados": aceptados,
                "rechazados": rechazados,
                "totalRegistros": aceptados + rechazados,
                "totalCargo": total_cargo,
                "porHab": por_hab_lista,
            },
        }
    finally:
        conn.close()


def registrar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    estado = str(datos.get("estado") or "ACEPTADO").strip().upper()
    if estado not in ("ACEPTADO", "RECHAZADO"):
        estado = "ACEPTADO"

    hab = str(datos.get("hab") or "").strip()
    dni_raw = str(datos.get("dni") or "").strip()
    nombre_vis = str(datos.get("nombreVisita") or "").strip()
    motivo = str(datos.get("motivo") or "").strip()
    fecha_dmy = _fecha_a_dmy(datos.get("fecha"))
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)
    hora = str(datos.get("hora") or "").strip() or datetime.now().strftime("%H:%M")

    if estado == "ACEPTADO":
        if not hab or hab == "-":
            raise ValueError("Selecciona primero la habitación ocupada para cargar el pase a su cuenta.")
        if not dni_raw:
            raise ValueError("Ingresa el DNI de la visita para registrar el pase.")
        monto = dl.parse_moneda(datos.get("monto") if datos.get("monto") not in (None, "") else 10.0)
    else:
        hab = hab or "-"
        dni_raw = dni_raw or "SIN DNI"
        monto = 0.0
        if not motivo:
            motivo = "Ingreso rechazado en recepción"

    # Autocompletar nombre silenciosamente en el registro interno si es DNI de 8 dígitos
    dni_limpio = "".join(ch for ch in dni_raw if ch.isalnum() or ch in (",", " ", "-")).strip()
    primer_dni = "".join(ch for ch in dni_limpio.split(",")[0].split()[0] if ch.isalnum()) if dni_limpio else ""
    if not nombre_vis and len(primer_dni) == 8 and primer_dni.isdigit():
        try:
            r_dni = consultar_dni_local_o_reniec(primer_dni)
            if isinstance(r_dni, dict) and r_dni.get("nombre"):
                nombre_vis = str(r_dni["nombre"]).strip()
        except Exception:
            nombre_vis = ""

    est_uuid = ""
    id_estancia = ""
    huesped_titular = ""

    # 1. Si es ACEPTADO, cargar como Servicio Adicional ('Pase') a la cuenta de la habitación (sin exponer DNI)
    if estado == "ACEPTADO":
        conn_chk = get_connection()
        try:
            st = dl._buscar_estancia_local(conn_chk, hab=hab)
            est_uuid = str(st["estancia_uuid"] or "")
            id_estancia = str(st["id_timestamp_str"] or "")
            huesped_titular = str(st["huesped"] or "")
        finally:
            conn_chk.close()

        if monto > 0:
            dl.guardar_pago_posterior_local({
                "estanciaUuid": est_uuid,
                "id": id_estancia,
                "hab": hab,
                "monto": monto,
                "concepto": "Servicio Adicional",
                "metodo": "Cargar a la Cuenta",
                "tipoAdicional": "Pase",
            })
    elif hab and hab != "-":
        conn_chk = get_connection()
        try:
            try:
                st = dl._buscar_estancia_local(conn_chk, hab=hab)
                est_uuid = str(st["estancia_uuid"] or "")
                id_estancia = str(st["id_timestamp_str"] or "")
                huesped_titular = str(st["huesped"] or "")
            except Exception:
                pass
        finally:
            conn_chk.close()

    # 2. Guardar en tabla privada control_pases + encolar sync interno
    pase_uuid = str(uuid.uuid4())
    ahora_iso = datetime.now().isoformat(timespec="seconds")
    equipo = dl._nombre_equipo()

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO control_pases (
                pase_uuid, fecha_dmy, ymd, hora, hab, estancia_uuid, id_estancia,
                huesped_titular, dni_visita, nombre_visita, estado, monto,
                motivo_rechazo, equipo, created_at, synced
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                pase_uuid, fecha_dmy, ymd, hora, hab, est_uuid, id_estancia,
                huesped_titular, dni_limpio, nombre_vis, estado, monto,
                motivo, equipo, ahora_iso,
            ),
        )
        dl._encolar_operacion(
            conn,
            "PASE_UPSERT",
            pase_uuid,
            {
                "pase_uuid": pase_uuid,
                "fila": [
                    pase_uuid, fecha_dmy, hora, hab, est_uuid, id_estancia,
                    huesped_titular, dni_limpio, nombre_vis, estado,
                    monto, motivo, equipo, ahora_iso,
                ],
            },
        )

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = (
        f"✅ Pase registrado en Hab. {hab} (+S/ {monto:.2f} cargado a la cuenta)"
        if estado == "ACEPTADO"
        else f"🚫 Visita rechazada anotada ({hora})"
    )
    return vista


def eliminar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    pase_uuid = str(datos.get("paseUuid") or "").strip()
    if not pase_uuid:
        raise ValueError("Falta identificador del registro de pase.")

    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM control_pases WHERE pase_uuid = ?", (pase_uuid,)).fetchone()
        if not row:
            raise ValueError("El registro de visita ya no existe.")
        fecha_dmy = str(row["fecha_dmy"] or datetime.now().strftime("%d/%m/%Y"))
        estado = str(row["estado"] or "")
        monto = float(row["monto"] or 0.0)
        est_uuid = str(row["estancia_uuid"] or "").strip()

        # Si era un pase aceptado con cargo a la cuenta, descontar el adicional de la estancia en espejo
        if estado == "ACEPTADO" and monto > 0 and est_uuid:
            st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_uuid,)).fetchone()
            if st:
                nuevo_adic_serv = max(0.0, round(float(st["adic_servicios"] or 0.0) - monto, 2))
                otros_pases = conn.execute(
                    "SELECT COUNT(*) AS c FROM control_pases WHERE estancia_uuid = ? AND estado = 'ACEPTADO' AND pase_uuid != ?",
                    (est_uuid, pase_uuid),
                ).fetchone()
                tipo_adic_actual = str(st["tipo_adicional"] or "").strip()
                if (not otros_pases or int(otros_pases["c"] or 0) == 0) and nuevo_adic_serv == 0:
                    tags = [t.strip() for t in tipo_adic_actual.split(",") if t.strip() and t.strip().lower() != "pase"]
                    tipo_adic_actual = ", ".join(tags)

                conn.execute(
                    "UPDATE estancias SET adic_servicios = ?, tipo_adicional = ?, dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
                    (nuevo_adic_serv, tipo_adic_actual, datetime.now().isoformat(), est_uuid),
                )
                dl._encolar_operacion(
                    conn,
                    "PAGO",
                    est_uuid,
                    {
                        "estancia_uuid": est_uuid,
                        "id_timestamp_str": st["id_timestamp_str"],
                        "sheet_row": int(st["sheet_row"] or 0),
                        "hab": st["hab"],
                        "adic_servicios": nuevo_adic_serv,
                        "adic_comp": float(st["adic_comp"] or 0.0),
                        "deuda_anterior": float(st["deuda_anterior"] or 0.0),
                        "pago_post_efec": float(st["pago_post_efec"] or 0.0),
                        "pago_post_yape": float(st["pago_post_yape"] or 0.0),
                        "pago_visa": float(st["pago_visa"] or 0.0),
                        "nro_op_yape": str(st["nro_op_yape"] or ""),
                        "solicita_comp": str(st["solicita_comp"] or "No"),
                        "tipo_comp": str(st["tipo_comp"] or ""),
                        "estado_comp": str(st["estado_comp"] or ""),
                        "monto_factura": float(st["monto_factura"] or 0.0),
                        "tipo_adicional": tipo_adic_actual,
                        "cuenta_destino": str(st["cuenta_destino"] or ""),
                        "pago_row": None,
                    },
                )

        conn.execute("DELETE FROM control_pases WHERE pase_uuid = ?", (pase_uuid,))
        dl._encolar_operacion(conn, "PASE_DELETE", pase_uuid, {"pase_uuid": pase_uuid})

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = "🗑️ Registro de visita eliminado (y cargo revertido si correspondía)."
    return vista
