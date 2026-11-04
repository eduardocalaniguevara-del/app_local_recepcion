# -*- coding: utf-8 -*-
"""
pases_logic.py — Control Interno de Visitas / Pases por Día (Corte 12:00 m.)
============================================================================
- El día operativo de pases corta cada mediodía (12:00 m. a 11:59 a.m. del día siguiente).
- Todo pase se registra primero como ACEPTADO (+S/ 10 cargado a la cuenta como Servicio Adicional = 'Pase').
- Una vez registrado en la tabla, puede marcarse como RECHAZADO:
  * NO se borra del registro del día (queda visible en rojo como 🚫 RECHAZADO).
  * Descuenta automáticamente los S/ 10.00 de la cuenta de la habitación.
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

DOW_CORTA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def fecha_corte_mediodia_actual(ahora: Optional[datetime] = None) -> str:
    """El corte diario es a las 12:00 m. (mediodía):
    - Antes de las 12:00 m. pertenece al ciclo iniciado ayer a las 12:00 m.
    - Desde las 12:00 m. en adelante pertenece al ciclo de hoy (12:00 m. -> mañana 11:59 a.m.)."""
    dt = ahora or datetime.now()
    d_base = dt.date() - timedelta(days=1) if dt.hour < 12 else dt.date()
    return d_base.strftime("%d/%m/%Y")


def _fecha_a_dmy(valor: Optional[str] = None) -> str:
    s = str(valor or "").strip()
    if not s:
        return fecha_corte_mediodia_actual()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        y, m, d = s[:10].split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    return dl.normalizar_fecha_dmy(s)


def _dmy_a_date(dmy: str) -> date:
    return datetime.strptime(dl.normalizar_fecha_dmy(dmy), "%d/%m/%Y").date()


def _dmy_a_iso(dmy: str) -> str:
    return _dmy_a_date(dmy).isoformat()


def _rango_corte_txt(dmy: str) -> str:
    d0 = _dmy_a_date(dmy)
    d1 = d0 + timedelta(days=1)
    return f"{d0.strftime('%d/%m')} 12:00 m. → {d1.strftime('%d/%m')} 11:59 a.m."


def _habitaciones_ocupadas_activas(fecha_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """Devuelve las habitaciones ocupadas del Panel de Disponibilidad."""
    panel = dl.obtener_datos_panel_local(fecha_iso)
    habs_out: List[Dict[str, Any]] = []
    for h in panel.get("habitaciones") or []:
        est = str(h.get("estado") or "").strip().lower()
        hab_num = str(h.get("hab") or "").strip()
        huesped = str(h.get("huesped") or "").strip()
        if est == "libre" or not hab_num or huesped in ("", "-"):
            continue
        habs_out.append({
            "hab": hab_num,
            "huesped": huesped,
            "estanciaUuid": str(h.get("estanciaUuid") or "").strip(),
            "idEstancia": str(h.get("idEstancia") or h.get("id") or "").strip(),
        })
    return habs_out


def consultar_dni_visita_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    q_raw = str(datos.get("dni") or datos.get("q") or "").strip()
    solo_dig = "".join(ch for ch in q_raw if ch.isdigit())
    dni = solo_dig if len(solo_dig) >= 7 else "".join(ch for ch in q_raw if ch.isalnum())
    if len(dni) == 7 and dni.isdigit():
        dni = dni.zfill(8)
    if not dni and not q_raw:
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
    hoy_corte_dmy = fecha_corte_mediodia_actual()
    fecha_dmy = _fecha_a_dmy(datos.get("fecha") or hoy_corte_dmy)
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)

    conn = get_connection()
    try:
        # Para elegir habitación ocupada, usamos siempre las habitaciones ocupadas actuales (o de la fecha si es pasada)
        iso_panel = None if fecha_dmy == hoy_corte_dmy else _dmy_a_iso(fecha_dmy)
        habs_activas = _habitaciones_ocupadas_activas(iso_panel)
        rows = conn.execute(
            """
            SELECT * FROM control_pases
            WHERE ymd = ?
            ORDER BY created_at DESC, hora DESC
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

        # Resumen de pases por día (últimos días con corte 12:00 m.)
        rows_dias = conn.execute(
            """
            SELECT fecha_dmy, ymd,
                   SUM(CASE WHEN estado = 'ACEPTADO' THEN 1 ELSE 0 END) AS aceptados,
                   SUM(CASE WHEN estado = 'RECHAZADO' THEN 1 ELSE 0 END) AS rechazados,
                   SUM(CASE WHEN estado = 'ACEPTADO' THEN monto ELSE 0 END) AS monto_total
            FROM control_pases
            GROUP BY ymd, fecha_dmy
            ORDER BY ymd DESC
            LIMIT 10
            """
        ).fetchall()
        mapa_dias = {
            str(rd["fecha_dmy"]): {
                "fechaDmy": str(rd["fecha_dmy"]),
                "fechaIso": _dmy_a_iso(str(rd["fecha_dmy"])),
                "dow": DOW_CORTA[_dmy_a_date(str(rd["fecha_dmy"])).weekday()],
                "aceptados": int(rd["aceptados"] or 0),
                "rechazados": int(rd["rechazados"] or 0),
                "totalCargo": round(float(rd["monto_total"] or 0.0), 2),
                "rangoCorte": _rango_corte_txt(str(rd["fecha_dmy"])),
            }
            for rd in rows_dias if rd["fecha_dmy"]
        }
        # Asegurar que el día de corte actual y la fecha seleccionada siempre aparezcan en la tira por día
        for f_aseg in (hoy_corte_dmy, fecha_dmy):
            if f_aseg not in mapa_dias:
                dt_a = _dmy_a_date(f_aseg)
                mapa_dias[f_aseg] = {
                    "fechaDmy": f_aseg,
                    "fechaIso": dt_a.isoformat(),
                    "dow": DOW_CORTA[dt_a.weekday()],
                    "aceptados": 0,
                    "rechazados": 0,
                    "totalCargo": 0.0,
                    "rangoCorte": _rango_corte_txt(f_aseg),
                }
        historial_por_dia = sorted(
            mapa_dias.values(),
            key=lambda x: dl.fecha_dmy_a_ymd(x["fechaDmy"]),
            reverse=True,
        )[:8]

        dt_sel = _dmy_a_date(fecha_dmy)
        return {
            "fechaDmy": fecha_dmy,
            "fechaIso": dt_sel.isoformat(),
            "dowNombre": DOW_CORTA[dt_sel.weekday()],
            "rangoCorteTxt": _rango_corte_txt(fecha_dmy),
            "hoyCorteDmy": hoy_corte_dmy,
            "hoyCorteIso": _dmy_a_iso(hoy_corte_dmy),
            "esHoy": fecha_dmy == hoy_corte_dmy,
            "ahoraHM": datetime.now().strftime("%H:%M"),
            "habitacionesOcupadas": habs_activas,
            "registros": registros,
            "historialPorDia": historial_por_dia,
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
    """Registra el pase inicialmente como ACEPTADO (+S/ 10 a la cuenta de la habitación).
    El día asignado usa el corte de mediodía (12:00 m.)."""
    datos = datos or {}
    hab = str(datos.get("hab") or "").strip()
    dni_raw = str(datos.get("dni") or "").strip()
    nombre_vis = str(datos.get("nombreVisita") or "").strip()
    motivo = str(datos.get("motivo") or "").strip()

    hoy_corte_dmy = fecha_corte_mediodia_actual()
    fecha_req = str(datos.get("fecha") or "").strip()
    fecha_dmy = _fecha_a_dmy(fecha_req) if fecha_req else hoy_corte_dmy
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)
    hora = str(datos.get("hora") or "").strip() or datetime.now().strftime("%H:%M")

    if not hab or hab == "-":
        raise ValueError("Selecciona primero la habitación ocupada.")
    if not dni_raw:
        raise ValueError("Ingresa el DNI de la visita.")

    monto = dl.parse_moneda(datos.get("monto") if datos.get("monto") not in (None, "") else 10.0)
    if monto <= 0:
        monto = 10.0

    dni_limpio = "".join(ch for ch in dni_raw if ch.isalnum() or ch in (",", " ", "-")).strip()
    primer_dni = "".join(ch for ch in dni_limpio.split(",")[0].split()[0] if ch.isalnum()) if dni_limpio else ""
    if not nombre_vis and len(primer_dni) == 8 and primer_dni.isdigit():
        try:
            r_dni = consultar_dni_local_o_reniec(primer_dni)
            if isinstance(r_dni, dict) and r_dni.get("nombre"):
                nombre_vis = str(r_dni["nombre"]).strip()
        except Exception:
            nombre_vis = ""

    conn_chk = get_connection()
    try:
        st = dl._buscar_estancia_local(conn_chk, hab=hab)
        est_uuid = str(st["estancia_uuid"] or "")
        id_estancia = str(st["id_timestamp_str"] or "")
        huesped_titular = str(st["huesped"] or "")
    finally:
        conn_chk.close()

    # Cargar S/ 10 a la cuenta de la habitación como Servicio Adicional ('Pase') sin exponer DNI
    dl.guardar_pago_posterior_local({
        "estanciaUuid": est_uuid,
        "id": id_estancia,
        "hab": hab,
        "monto": monto,
        "concepto": "Servicio Adicional",
        "metodo": "Cargar a la Cuenta",
        "tipoAdicional": "Pase",
    })

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
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'ACEPTADO',?,?,?,?,0)
            """,
            (
                pase_uuid, fecha_dmy, ymd, hora, hab, est_uuid, id_estancia,
                huesped_titular, dni_limpio, nombre_vis, monto,
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
                    huesped_titular, dni_limpio, nombre_vis, "ACEPTADO",
                    monto, motivo, equipo, ahora_iso,
                ],
            },
        )

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = f"✅ Pase registrado en Hab. {hab} (+S/ {monto:.2f} cargado a la cuenta)"
    return vista


def cambiar_estado_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Marca un pase YA registrado como RECHAZADO (o lo reactiva a ACEPTADO) SIN borrarlo de la lista.
    - Al pasar de ACEPTADO -> RECHAZADO: descuenta automáticamente el monto (S/ 10) de adic_servicios de la habitación.
    - Al pasar de RECHAZADO -> ACEPTADO: vuelve a sumar el monto (S/ 10) a adic_servicios de la habitación."""
    datos = datos or {}
    pase_uuid = str(datos.get("paseUuid") or "").strip()
    nuevo_estado = str(datos.get("estado") or "RECHAZADO").strip().upper()
    motivo = str(datos.get("motivo") or "").strip()
    if not pase_uuid:
        raise ValueError("Falta identificador del pase.")
    if nuevo_estado not in ("RECHAZADO", "ACEPTADO"):
        raise ValueError("Estado no válido.")

    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM control_pases WHERE pase_uuid = ?", (pase_uuid,)).fetchone()
        if not row:
            raise ValueError("El registro de pase no existe.")

        estado_ant = str(row["estado"] or "ACEPTADO").upper()
        fecha_dmy = str(row["fecha_dmy"] or fecha_corte_mediodia_actual())
        hab = str(row["hab"] or "")
        est_uuid = str(row["estancia_uuid"] or "").strip()
        monto_reg = float(row["monto"] or 10.0)
        if monto_reg <= 0:
            monto_reg = 10.0

        if estado_ant != nuevo_estado and est_uuid:
            st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_uuid,)).fetchone()
            if st:
                adic_actual = float(st["adic_servicios"] or 0.0)
                if nuevo_estado == "RECHAZADO":
                    nuevo_adic = max(0.0, round(adic_actual - monto_reg, 2))
                else:
                    nuevo_adic = round(adic_actual + monto_reg, 2)

                otros_activos = conn.execute(
                    "SELECT COUNT(*) AS c FROM control_pases WHERE estancia_uuid = ? AND estado = 'ACEPTADO' AND pase_uuid != ?",
                    (est_uuid, pase_uuid),
                ).fetchone()
                tipo_adic_actual = str(st["tipo_adicional"] or "").strip()
                if nuevo_estado == "RECHAZADO" and (not otros_activos or int(otros_activos["c"] or 0) == 0) and nuevo_adic == 0:
                    tags = [t.strip() for t in tipo_adic_actual.split(",") if t.strip() and t.strip().lower() != "pase"]
                    tipo_adic_actual = ", ".join(tags)
                elif nuevo_estado == "ACEPTADO":
                    tags = [t.strip() for t in tipo_adic_actual.split(",") if t.strip()]
                    if "pase" not in [x.lower() for x in tags]:
                        tags.append("Pase")
                    tipo_adic_actual = ", ".join(tags)

                conn.execute(
                    "UPDATE estancias SET adic_servicios = ?, tipo_adicional = ?, dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
                    (nuevo_adic, tipo_adic_actual, datetime.now().isoformat(), est_uuid),
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
                        "adic_servicios": nuevo_adic,
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

        motivo_final = motivo if nuevo_estado == "RECHAZADO" else ""
        if nuevo_estado == "RECHAZADO" and not motivo_final:
            motivo_final = str(row["motivo_rechazo"] or "").strip() or "Rechazado"

        conn.execute(
            "UPDATE control_pases SET estado = ?, monto = ?, motivo_rechazo = ?, synced = 0 WHERE pase_uuid = ?",
            (nuevo_estado, monto_reg, motivo_final, pase_uuid),
        )
        # Actualizar o insertar en import_Control_Pases
        dl._encolar_operacion(
            conn,
            "PASE_UPSERT",
            pase_uuid,
            {
                "pase_uuid": pase_uuid,
                "fila": [
                    pase_uuid,
                    fecha_dmy,
                    str(row["hora"] or ""),
                    hab,
                    est_uuid,
                    str(row["id_estancia"] or ""),
                    str(row["huesped_titular"] or ""),
                    str(row["dni_visita"] or ""),
                    str(row["nombre_visita"] or ""),
                    nuevo_estado,
                    0.0 if nuevo_estado == "RECHAZADO" else monto_reg,
                    motivo_final,
                    str(row["equipo"] or ""),
                    str(row["created_at"] or ""),
                ],
            },
        )

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = (
        f"🚫 Pase en Hab. {hab} marcado como RECHAZADO (queda en el registro y se descontó S/ {monto_reg:.2f} de la cuenta)."
        if nuevo_estado == "RECHAZADO"
        else f"✅ Pase en Hab. {hab} reactivado (+S/ {monto_reg:.2f} cargado a la cuenta)."
    )
    return vista


def eliminar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Por compatibilidad si algún llamado invoca eliminarVisitaPase, lo marca como RECHAZADO sin borrarlo."""
    datos = dict(datos or {})
    datos["estado"] = "RECHAZADO"
    return cambiar_estado_visita_pase_local(datos)
