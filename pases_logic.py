# -*- coding: utf-8 -*-
"""
pases_logic.py — Control Interno de Visitas (Corte 12:00 m.) + Cuentas Multi-Día por Habitación
===============================================================================================
1. El día operativo de visitas corta cada mediodía (12:00 m. a 11:59 a.m. del día siguiente).
2. Todo pase aceptado se carga a la cuenta como Servicio Adicional = 'Visita' (S/ 10 por defecto).
3. El estado RECHAZADO se aplica sobre un registro existente en la tabla (sin borrarlo) y descuenta
   automáticamente los S/ 10 de la cuenta.
4. `obtener_cuenta_detallada_hab_local`: reconstruye la cadena completa de días de cualquier habitación
   ocupada (huéspedes que van pasando varios días), detallando día por día hospedaje, adicionales
   (tienda, visitas, servicios, comprobante) y pagos realizados con su medio de pago.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

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
    """El corte diario es a las 12:00 m. (mediodía)."""
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
            "saldo": round(float(h.get("saldo") or 0.0), 2),
            "adicional": round(float(h.get("adicional") or 0.0), 2),
            "tipoAdicional": str(h.get("tipoAdicional") or ""),
        })
    return sorted(habs_out, key=lambda x: (int(x["hab"]) if x["hab"].isdigit() else 9999, x["hab"]))


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
    """Registra la visita inicialmente como ACEPTADO (+S/ 10 a la cuenta con tipoAdicional = 'Visita')."""
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
        raise ValueError("Selecciona o escribe primero el número de la habitación ocupada (ej. 206).")
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

    # Cargar S/ 10 a la cuenta de la habitación como Servicio Adicional ('Visita') sin exponer DNI
    dl.guardar_pago_posterior_local({
        "estanciaUuid": est_uuid,
        "id": id_estancia,
        "hab": hab,
        "monto": monto,
        "concepto": "Servicio Adicional",
        "metodo": "Cargar a la Cuenta",
        "tipoAdicional": "Visita",
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
    vista["mensaje"] = f"✅ Visita registrada en Hab. {hab} (+S/ {monto:.2f} cargado a la cuenta)"
    return vista


def cambiar_estado_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Marca un pase YA registrado como RECHAZADO (o lo reactiva a ACEPTADO) SIN borrarlo de la lista."""
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
            raise ValueError("El registro de visita no existe.")

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
                # Normalizar cualquier etiqueta 'Pase' previa a 'Visita'
                tags_base = [
                    ("Visita" if t.strip().lower() == "pase" else t.strip())
                    for t in tipo_adic_actual.split(",") if t.strip()
                ]
                tags_unicos = []
                for t in tags_base:
                    if t.lower() not in [x.lower() for x in tags_unicos]:
                        tags_unicos.append(t)

                if nuevo_estado == "RECHAZADO" and (not otros_activos or int(otros_activos["c"] or 0) == 0) and nuevo_adic == 0:
                    tags_unicos = [t for t in tags_unicos if t.lower() not in ("visita", "pase")]
                elif nuevo_estado == "ACEPTADO":
                    if "visita" not in [x.lower() for x in tags_unicos]:
                        tags_unicos.append("Visita")
                tipo_adic_actual = ", ".join(tags_unicos)

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
        f"🚫 Visita en Hab. {hab} marcada como RECHAZADA (permanece en la lista y se descontó S/ {monto_reg:.2f} de la cuenta)."
        if nuevo_estado == "RECHAZADO"
        else f"✅ Visita en Hab. {hab} reactivada (+S/ {monto_reg:.2f} cargado a la cuenta)."
    )
    return vista


def eliminar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = dict(datos or {})
    datos["estado"] = "RECHAZADO"
    return cambiar_estado_visita_pase_local(datos)


# ==============================================================================
# CUENTAS DETALLADAS MULTI-DÍA POR HABITACIÓN (HUÉSPEDES QUE VAN VARIOS DÍAS)
# ==============================================================================

def _saldo_fin_estancia(r: Dict[str, Any]) -> float:
    noches = max(1, int(r.get("noches") or 1))
    aloj = round(noches * float(r.get("tarifa") or 0.0), 2)
    cargos = (
        aloj
        + float(r.get("adic_snacks") or 0.0)
        + float(r.get("adic_servicios") or 0.0)
        + float(r.get("adic_comp") or 0.0)
        + float(r.get("deuda_anterior") or 0.0)
    )
    pagos = (
        float(r.get("pago_ini_efec") or 0.0)
        + float(r.get("pago_ini_yape") or 0.0)
        + float(r.get("pago_post_efec") or 0.0)
        + float(r.get("pago_post_yape") or 0.0)
        + float(r.get("pago_visa") or 0.0)
    )
    return round(cargos - pagos, 2)


def _reconstruir_cadena_estancia(
    conn,
    st_actual: Dict[str, Any],
    solo_ciclo_actual: bool = True,
) -> List[Dict[str, Any]]:
    """Sigue hacia atrás los días consecutivos de esa estancia.
    - Si `solo_ciclo_actual=True` (por defecto), se detiene en el Día 1 de la cuenta abierta actual
      (cuando `deuda_anterior == 0` y el día previo cerró con `saldo == 0`), de modo que los totales
      de Hospedaje + Adicionales − Pagos cuadren exactamente con el Saldo Pendiente Actual.
    - Si `solo_ciclo_actual=False`, reconstruye todos los días continuos incluidos ciclos ya saldados.
    """
    dni = str(st_actual.get("dni") or "").strip()
    huesped = str(st_actual.get("huesped") or "").strip()

    if dni and dni not in ("-", "0"):
        cand_rows = conn.execute(
            """
            SELECT * FROM estancias
            WHERE (dni = ? OR lower(trim(huesped)) = lower(trim(?)))
              AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
            ORDER BY ymd_in DESC, sheet_row DESC
            """,
            (dni, huesped),
        ).fetchall()
    else:
        cand_rows = conn.execute(
            """
            SELECT * FROM estancias
            WHERE lower(trim(huesped)) = lower(trim(?))
              AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
            ORDER BY ymd_in DESC, sheet_row DESC
            """,
            (huesped,),
        ).fetchall()

    por_uuid = {str(r["estancia_uuid"]): dict(r) for r in cand_rows}
    todos = [dict(r) for r in cand_rows]

    cadena = [dict(st_actual)]
    usados: Set[str] = {str(st_actual["estancia_uuid"])}
    cur = dict(st_actual)

    for paso in range(60):
        cur_in_d = _dmy_a_date(cur["fecha_in_dmy"])
        deuda_arr = round(float(cur.get("deuda_anterior") or 0.0), 2)
        cur_hab = str(cur.get("hab") or "").strip()
        cur_tar = round(float(cur.get("tarifa") or 0.0), 2)

        # Si estamos en modo "cuenta actual" y este día ya empezó con deuda_anterior == 0:
        # - Si ya retrocedimos al menos 1 día con deuda arrastrada (paso >= 1), este día es el Día 1 del ciclo actual -> detener aquí.
        if solo_ciclo_actual and paso >= 1 and abs(deuda_arr) <= 0.01:
            break

        posibles = []
        for cand in todos:
            cu = str(cand["estancia_uuid"])
            if cu in usados:
                continue
            c_in = _dmy_a_date(cand["fecha_in_dmy"])
            c_out = _dmy_a_date(cand["fecha_out_dmy"]) if cand.get("fecha_out_dmy") else (c_in + timedelta(days=1))
            if c_out != cur_in_d and (cur_in_d - c_in).days != 1:
                continue

            s_fin = _saldo_fin_estancia(cand)
            hab_c = str(cand.get("hab") or "").strip()
            tar_c = round(float(cand.get("tarifa") or 0.0), 2)
            est_c = str(cand.get("estado_estancia") or "").lower()

            score = 0
            if abs(s_fin - deuda_arr) <= 0.1:
                score += 120
            if hab_c == cur_hab:
                score += 50
            if abs(tar_c - cur_tar) <= 0.1:
                score += 35
            if p_uuid := str(cur.get("parent_uuid") or "").strip():
                if cu == p_uuid:
                    score += 25
            if "contin" in est_c:
                score += 15

            if score < 55:
                continue
            # Si estamos en el día actual con deuda_anterior == 0 (ej. pagó ayer y hoy aún debe),
            # en modo cuenta actual solo incluir el día de ayer si fue en la misma habitación.
            if solo_ciclo_actual and paso == 0 and abs(deuda_arr) <= 0.01 and hab_c != cur_hab:
                continue

            posibles.append((score, int(cand.get("sheet_row") or 0), cand))

        if not posibles:
            break
        posibles.sort(key=lambda x: (x[0], x[1]), reverse=True)
        elegido = posibles[0][2]

        usados.add(str(elegido["estancia_uuid"]))
        cadena.append(elegido)
        cur = elegido

    cadena.reverse()  # Orden cronológico: Día 1 -> Hoy
    return cadena


def obtener_cuenta_detallada_hab_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Devuelve la lista de habitaciones ocupadas y el estado de cuenta detallado día por día
    de la habitación seleccionada (hospedaje, adicionales y pagos con tipo de pago)."""
    datos = datos or {}
    hab_req = str(datos.get("hab") or "").strip()
    todo_historial = bool(datos.get("todoHistorial"))

    conn = get_connection()
    try:
        habs_activas = _habitaciones_ocupadas_activas(None)
        resumen_habs = []
        cadenas_por_hab: Dict[str, List[Dict[str, Any]]] = {}
        cadenas_full_por_hab: Dict[str, List[Dict[str, Any]]] = {}

        for h in habs_activas:
            try:
                st_h = dl._buscar_estancia_local(conn, estancia_uuid=h["estanciaUuid"], id_busq=h["idEstancia"], hab=h["hab"])
                cad_ciclo = _reconstruir_cadena_estancia(conn, dict(st_h), solo_ciclo_actual=True)
                cad_full = _reconstruir_cadena_estancia(conn, dict(st_h), solo_ciclo_actual=False)
            except Exception:
                cad_ciclo = []
                cad_full = []
            cadenas_por_hab[h["hab"]] = cad_full if todo_historial else cad_ciclo
            cadenas_full_por_hab[h["hab"]] = cad_full
            cad_ref = cadenas_por_hab[h["hab"]]
            dias_cnt = sum(max(1, int(x.get("noches") or 1)) for x in cad_ref) if cad_ref else 1
            resumen_habs.append({
                "hab": h["hab"],
                "huesped": h["huesped"],
                "dias": dias_cnt,
                "diasTotales": sum(max(1, int(x.get("noches") or 1)) for x in cad_full) if cad_full else dias_cnt,
                "registrosCadena": len(cad_ref),
                "desdeDmy": cad_ref[0]["fecha_in_dmy"] if cad_ref else "",
                "saldo": h["saldo"],
            })

        if not hab_req and resumen_habs:
            ordenadas = sorted(
                resumen_habs,
                key=lambda x: (1 if x["dias"] > 1 else 0, 1 if x["saldo"] > 0 else 0, x["dias"]),
                reverse=True,
            )
            hab_req = ordenadas[0]["hab"]

        cadena = cadenas_por_hab.get(hab_req) or []
        cadena_full = cadenas_full_por_hab.get(hab_req) or []
        if not cadena and hab_req:
            row_fb = conn.execute(
                """
                SELECT * FROM estancias
                WHERE hab = ? AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                ORDER BY ymd_in DESC, sheet_row DESC LIMIT 1
                """,
                (hab_req,),
            ).fetchone()
            if row_fb:
                cadena = _reconstruir_cadena_estancia(conn, dict(row_fb), solo_ciclo_actual=not todo_historial)
                cadena_full = _reconstruir_cadena_estancia(conn, dict(row_fb), solo_ciclo_actual=False)

        if not cadena:
            return {
                "encontrado": False,
                "mensaje": f"No se encontró una estancia activa en la Hab. {hab_req}. Elige una habitación ocupada.",
                "habitacionesOcupadas": resumen_habs,
                "hab": hab_req,
            }

        dias_detalle = []
        todos_pagos = []
        todos_adicionales = []

        tot_noches = 0
        tot_aloj = 0.0
        tot_tienda_cargada = 0.0
        tot_tienda_pagada = 0.0
        tot_serv = 0.0
        tot_comp = 0.0
        tot_pagado_efec = 0.0
        tot_pagado_yape = 0.0
        tot_pagado_visa = 0.0
        habs_recorridas: List[str] = []

        for idx_d, st in enumerate(cadena, start=1):
            est_uuid = str(st.get("estancia_uuid") or "")
            id_ts = str(st.get("id_timestamp") or "")
            hab_d = str(st.get("hab") or "")
            if hab_d and hab_d not in habs_recorridas:
                habs_recorridas.append(hab_d)

            f_in = str(st.get("fecha_in_dmy") or "")
            f_out = str(st.get("fecha_out_dmy") or "")
            noches_d = max(1, int(st.get("noches") or 1))
            tarifa_d = round(float(st.get("tarifa") or 0.0), 2)
            aloj_d = round(noches_d * tarifa_d, 2)

            snacks_d = round(float(st.get("adic_snacks") or 0.0), 2)
            serv_d = round(float(st.get("adic_servicios") or 0.0), 2)
            comp_d = round(float(st.get("adic_comp") or 0.0), 2)
            tipo_adic_d = str(st.get("tipo_adicional") or "").strip()
            deuda_ant_d = round(float(st.get("deuda_anterior") or 0.0), 2)

            ini_e = round(float(st.get("pago_ini_efec") or 0.0), 2)
            ini_y = round(float(st.get("pago_ini_yape") or 0.0), 2)
            post_e = round(float(st.get("pago_post_efec") or 0.0), 2)
            post_y = round(float(st.get("pago_post_yape") or 0.0), 2)
            visa_d = round(float(st.get("pago_visa") or 0.0), 2)
            pagado_dia_est = round(ini_e + ini_y + post_e + post_y + visa_d, 2)

            tot_noches += noches_d
            tot_aloj = round(tot_aloj + aloj_d, 2)
            tot_tienda_cargada = round(tot_tienda_cargada + snacks_d, 2)
            tot_serv = round(tot_serv + serv_d, 2)
            tot_comp = round(tot_comp + comp_d, 2)
            tot_pagado_efec = round(tot_pagado_efec + ini_e + post_e, 2)
            tot_pagado_yape = round(tot_pagado_yape + ini_y + post_y, 2)
            tot_pagado_visa = round(tot_pagado_visa + visa_d, 2)

            # 1. Pagos de ese día en Registro_Pagos
            rows_p = conn.execute(
                """
                SELECT fecha_iso, hab, monto, medio_pago, tipo_pago, concepto, nro_operacion
                FROM pagos
                WHERE (id_estancia = ? AND trim(id_estancia) != '')
                   OR (estancia_uuid = ? AND trim(estancia_uuid) != '')
                ORDER BY sheet_row ASC, fecha_iso ASC
                """,
                (id_ts, est_uuid),
            ).fetchall()

            pagos_dia_lista = []
            suma_rows_p = 0.0
            for rp in rows_p:
                m_val = round(float(rp["monto"] or 0.0), 2)
                suma_rows_p = round(suma_rows_p + m_val, 2)
                fh = str(rp["fecha_iso"] or "")
                hora_p = fh.split(" ")[1][:5] if " " in fh else ""
                fecha_p_raw = fh.split(" ")[0] if " " in fh else f_in
                fecha_p = _fecha_a_dmy(fecha_p_raw) if fecha_p_raw else f_in
                item_p = {
                    "fecha": fecha_p,
                    "hora": hora_p,
                    "hab": str(rp["hab"] or hab_d),
                    "monto": m_val,
                    "medioPago": str(rp["medio_pago"] or "Efectivo"),
                    "tipoPago": str(rp["tipo_pago"] or ""),
                    "concepto": str(rp["concepto"] or "Pago de estancia"),
                    "nroOperacion": str(rp["nro_operacion"] or ""),
                }
                pagos_dia_lista.append(item_p)
                todos_pagos.append(item_p)

            if not pagos_dia_lista and pagado_dia_est != 0:
                op_yape = str(st.get("nro_op_yape") or "")
                hora_reg = str(st.get("hora_registro") or "")[:5]
                for monto_col, med_col, tip_col in [
                    (ini_e, "Efectivo", "Inicial"),
                    (ini_y, "Yape", "Inicial"),
                    (post_e, "Efectivo", "Posterior"),
                    (post_y, "Yape", "Posterior"),
                    (visa_d, "VISA / Banco", "Posterior"),
                ]:
                    if monto_col != 0:
                        item_s = {
                            "fecha": f_in,
                            "hora": hora_reg,
                            "hab": hab_d,
                            "monto": monto_col,
                            "medioPago": med_col,
                            "tipoPago": tip_col,
                            "concepto": f"Pago {tip_col} ({med_col})",
                            "nroOperacion": op_yape if med_col == "Yape" else "",
                        }
                        pagos_dia_lista.append(item_s)
                        todos_pagos.append(item_s)

            # 2. Adicionales de Tienda y Servicios de ese día
            rows_t = conn.execute(
                """
                SELECT fecha_iso, fecha_dmy, producto, cantidad, metodo_pago, precio_unit, subtotal, nro_op_yape
                FROM ventas_tienda
                WHERE (id_estancia = ? AND trim(id_estancia) != '')
                   OR (estancia_uuid = ? AND trim(estancia_uuid) != '')
                ORDER BY sheet_row ASC, fecha_iso ASC
                """,
                (id_ts, est_uuid),
            ).fetchall()

            adicionales_dia_lista = []
            for rt in rows_t:
                sub_t = round(float(rt["subtotal"] or 0.0), 2)
                met_t = str(rt["metodo_pago"] or "")
                es_cargado_t = "cargar" in met_t.lower() or "cuenta" in met_t.lower() or "habitac" in met_t.lower()
                if not es_cargado_t:
                    tot_tienda_pagada = round(tot_tienda_pagada + sub_t, 2)
                f_iso_t = str(rt["fecha_iso"] or "")
                hora_t = f_iso_t.split(" ")[1][:5] if " " in f_iso_t else ""
                item_ad = {
                    "fecha": str(rt["fecha_dmy"] or f_in),
                    "hora": hora_t,
                    "hab": hab_d,
                    "categoria": "Tienda",
                    "descripcion": f"{int(rt['cantidad'] or 1)}x {rt['producto']}" + ("" if es_cargado_t else f" [Pagado {met_t}]"),
                    "monto": sub_t,
                    "modo": "Cargado a cuenta" if es_cargado_t else f"Pagado ({met_t})",
                    "cargadoACuenta": es_cargado_t,
                }
                adicionales_dia_lista.append(item_ad)
                todos_adicionales.append(item_ad)

            if serv_d > 0:
                vis_rows = conn.execute(
                    "SELECT hora, monto FROM control_pases WHERE estancia_uuid = ? AND estado = 'ACEPTADO' ORDER BY hora ASC",
                    (est_uuid,),
                ).fetchall()
                horas_vis = ", ".join(str(vr["hora"]) for vr in vis_rows if vr["hora"])
                etiqueta_tipo = tipo_adic_d if tipo_adic_d and tipo_adic_d.lower() != "pase" else "Visita"
                desc_serv = f"Servicio Adicional: {etiqueta_tipo}"
                if horas_vis:
                    desc_serv += f" ({len(vis_rows)} visita(s) a las {horas_vis})"
                item_sv = {
                    "fecha": f_in,
                    "hora": horas_vis or str(st.get("hora_registro") or "")[:5],
                    "hab": hab_d,
                    "categoria": etiqueta_tipo,
                    "descripcion": desc_serv,
                    "monto": serv_d,
                    "modo": "En cuenta de estancia",
                    "cargadoACuenta": True,
                }
                adicionales_dia_lista.append(item_sv)
                todos_adicionales.append(item_sv)

            if comp_d > 0:
                item_cp = {
                    "fecha": f_in,
                    "hora": "",
                    "hab": hab_d,
                    "categoria": "Comprobante",
                    "descripcion": f"Adicional por Comprobante ({st.get('tipo_comp') or 'Factura/Boleta'})",
                    "monto": comp_d,
                    "modo": "En cuenta de estancia",
                    "cargadoACuenta": True,
                }
                adicionales_dia_lista.append(item_cp)
                todos_adicionales.append(item_cp)

            saldo_cierre_dia = _saldo_fin_estancia(st)
            dt_in = _dmy_a_date(f_in)

            dias_detalle.append({
                "diaNum": idx_d,
                "fechaIn": f_in,
                "fechaOut": f_out,
                "horaIn": str(st.get("hora_registro") or "")[:5],
                "dow": DOW_CORTA[dt_in.weekday()],
                "hab": hab_d,
                "noches": noches_d,
                "tarifa": tarifa_d,
                "alojamiento": aloj_d,
                "adicSnacks": snacks_d,
                "adicServicios": serv_d,
                "adicComp": comp_d,
                "tipoAdicional": tipo_adic_d,
                "deudaAnterior": deuda_ant_d,
                "pagadoDia": pagado_dia_est,
                "saldoFinDia": saldo_cierre_dia,
                "adicionalesDetalle": adicionales_dia_lista,
                "pagosDetalle": pagos_dia_lista,
            })

        ult = cadena[-1]
        prim = cadena[0]
        deuda_inicial_cadena = round(float(prim.get("deuda_anterior") or 0.0), 2)
        tot_adicionales_cuenta = round(tot_tienda_cargada + tot_serv + tot_comp, 2)
        tot_cargos_cadena = round(deuda_inicial_cadena + tot_aloj + tot_adicionales_cuenta, 2)
        tot_pagado_cadena = round(tot_pagado_efec + tot_pagado_yape + tot_pagado_visa, 2)
        saldo_final_actual = _saldo_fin_estancia(ult)

        return {
            "encontrado": True,
            "hab": str(ult.get("hab") or hab_req),
            "huesped": str(ult.get("huesped") or ""),
            "dni": str(ult.get("dni") or ""),
            "empresa": str(ult.get("empresa") or ""),
            "todoHistorial": todo_historial,
            "tieneCiclosAnteriores": len(cadena_full) > len(cadena),
            "diasHistorialCompleto": len(cadena_full),
            "habitacionesOcupadas": resumen_habs,
            "resumen": {
                "diasCount": len(dias_detalle),
                "totalNoches": tot_noches,
                "habsRecorridas": habs_recorridas,
                "fechaInicio": str(prim.get("fecha_in_dmy") or ""),
                "fechaUltima": str(ult.get("fecha_in_dmy") or ""),
                "fechaSalidaUltima": str(ult.get("fecha_out_dmy") or ""),
                "deudaInicialArrastrada": deuda_inicial_cadena,
                "totalAlojamiento": tot_aloj,
                "totalTienda": tot_tienda_cargada,
                "totalTiendaPagadaDirecto": tot_tienda_pagada,
                "totalServicios": tot_serv,
                "totalComp": tot_comp,
                "totalAdicionales": tot_adicionales_cuenta,
                "granTotalCargos": tot_cargos_cadena,
                "totalPagado": tot_pagado_cadena,
                "pagadoEfectivo": tot_pagado_efec,
                "pagadoYape": tot_pagado_yape,
                "pagadoVisaBanco": tot_pagado_visa,
                "saldoActual": saldo_final_actual,
            },
            "todosLosPagos": todos_pagos,
            "todosLosAdicionales": todos_adicionales,
            "dias": dias_detalle,
        }
    finally:
        conn.close()
