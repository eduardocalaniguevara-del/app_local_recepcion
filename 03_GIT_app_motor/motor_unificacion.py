import sys as _sys
from pathlib import Path as _Path
_MDIR = str(_Path(__file__).resolve().parent)
if _MDIR not in _sys.path:
    _sys.path.insert(0, _MDIR)
import config as _cfg_v2
import db as _db_v2
_sys.modules["config"] = _cfg_v2
_sys.modules["database"] = _db_v2

"""
motor_unificacion.py
Motor de Sugerencia de Unificación y Conciliación Inexacta (Ovalo_Mariategui_PRUEBA)

Implementa:
- Constantes de negocio y rangos plausibles por tipo de habitación.
- Regla aprobada: Habitación doble con baño compartido ocupada por 1 persona (n_acomp en ('', '0'))
  aplica el rango de simple con baño privado (Boleta 45–60, Factura máx 70).
- Cálculo de colchón fiscal (Efectivo Maelen = monto_comp - yape_banco).
- Detección de comprobantes inexactos (< rango mínimo o < S/ 35).
- Búsqueda de receptoras del mismo día o día anterior con prioridad fecha_out == donante fecha.
- Búsqueda de Yapes de tienda disponibles.
- Sugerencia clasificada de opciones B, T, C, A.
- Aplicación transaccional y persistencia de decisiones en SQLite.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import database

# ==============================================================================
# CONSTANTES DE NEGOCIO (PLAN v2)
# ==============================================================================

MINIMO_COMPROBANTE = 35.0
MONTO_OPCION_C = 50.0
HORAS_EXTRAS_MIN = 10.0
HORAS_EXTRAS_MAX = 50.0
UMBRAL_TIENDA = 10.0

# Rangos plausibles por tipo de habitación (Tabla 1.1)
RANGOS_COMPROBANTE: Dict[str, Dict[str, float]] = {
    "Simple con baño compartido": {"boleta_min": 35.0, "boleta_max": 45.0, "factura_max": 50.0},
    "Simple con baño privado": {"boleta_min": 45.0, "boleta_max": 60.0, "factura_max": 70.0},
    "Matrimonial baño compartido": {"boleta_min": 40.0, "boleta_max": 65.0, "factura_max": 70.0},
    "Matrimonial": {"boleta_min": 60.0, "boleta_max": 90.0, "factura_max": 110.0},
    "Matrimonial con Tina": {"boleta_min": 65.0, "boleta_max": 95.0, "factura_max": 110.0},
    "Doble con baño privado": {"boleta_min": 70.0, "boleta_max": 100.0, "factura_max": 120.0},
    "Doble familiar con baño privado": {"boleta_min": 80.0, "boleta_max": 120.0, "factura_max": 140.0},
    "Doble con baño compartido": {"boleta_min": 45.0, "boleta_max": 65.0, "factura_max": 75.0},
    "Doble Familiar con baño compartido": {"boleta_min": 50.0, "boleta_max": 75.0, "factura_max": 85.0},
}


# ==============================================================================
# FUNCIONES AUXILIARES DE RANGO Y FECHA
# ==============================================================================

def rango_por_tipo(tipo_hab: str, n_acomp: str = "") -> Dict[str, float]:
    """
    Retorna los rangos plausibles para un tipo de habitación.
    REGLA ESPECIAL APROBADA (Tabla 1.1):
    Habitación doble con baño compartido ocupada por 1 persona (n_acomp en ('', '0'))
    se mapea al rango de Simple con baño privado: Boleta 45–60, Factura máx 70.
    """
    tipo_clean = str(tipo_hab or "").strip()
    n_acomp_str = str(n_acomp or "").strip()

    if tipo_clean in ("Doble con baño compartido", "Doble Familiar con baño compartido") and n_acomp_str in ("", "0"):
        return {"boleta_min": 45.0, "boleta_max": 60.0, "factura_max": 70.0}

    for k, v in RANGOS_COMPROBANTE.items():
        if k.lower() == tipo_clean.lower():
            return dict(v)

    return {"boleta_min": 35.0, "boleta_max": 60.0, "factura_max": 70.0}


def calcular_colchon(monto_comp: float, yape_banco: float) -> float:
    """Calcula el colchón fiscal (Efectivo Maelen): max(0, monto_comp - yape_banco)."""
    return max(0.0, round(float(monto_comp or 0.0) - float(yape_banco or 0.0), 2))


def calcular_fecha_ayer(fecha_dmy: str) -> str:
    """Calcula el día calendario inmediatamente anterior en formato DD/MM/YYYY."""
    try:
        dt = datetime.strptime(fecha_dmy.strip(), "%d/%m/%Y")
        return (dt - timedelta(days=1)).strftime("%d/%m/%Y")
    except Exception:
        return ""


def es_pendiente_inexacto(estancia: Dict[str, Any]) -> bool:
    """
    Determina si una estancia es una Boleta pendiente con monto inexacto (< rango mínimo o < 35).
    Excluye Facturas (con RUC o empresa), estancias ya emitidas o unificadas, y Yapes < 10 (Nivel 2).
    """
    estado_comp = str(estancia.get("estado_comp") or "").strip().lower()
    nro_comp = str(estancia.get("nro_comp") or "").strip()
    unificado_en = str(estancia.get("unificado_en_uuid") or "").strip()
    if estado_comp in ("emitido", "unificado") or nro_comp or unificado_en:
        return False

    tipo_comp = str(estancia.get("tipo_comp") or "").strip().lower()
    ruc = str(estancia.get("ruc") or "").strip()
    empresa = str(estancia.get("empresa") or "").strip()
    if "fact" in tipo_comp or (ruc and ruc != "-") or (empresa and empresa != "-"):
        return False

    yape_banco = round(float(estancia.get("pago_ini_yape") or 0.0) + float(estancia.get("pago_post_yape") or 0.0), 2)
    if yape_banco < UMBRAL_TIENDA:
        # Menor a S/ 10 lo absorbe Bolsa Nivel 2
        return False

    rango = rango_por_tipo(estancia.get("tipo_hab", ""), estancia.get("n_acomp", ""))
    return yape_banco < rango["boleta_min"]


# ==============================================================================
# BÚSQUEDA DE RECEPTORAS Y YAPES DE TIENDA
# ==============================================================================

def buscar_receptoras(
    conn: sqlite3.Connection,
    fecha_ref_dmy: str,
    horas_extras: float,
    exclude_uuid: str = "",
) -> List[Dict[str, Any]]:
    """
    Busca estancias candidatas a receptoras para un monto de horas extras dado.
    Reglas de elegibilidad:
    - Misma fecha de ingreso o día anterior, o fecha_out igual a fecha_ref_dmy.
    - Boleta NO emitida, no unificada, sin RUC, sin empresa, celular sin WhatsApp (< 7 dígitos).
    - Sin horas extras previas ya asignadas.
    - Horas extras entre HORAS_EXTRAS_MIN (10) y HORAS_EXTRAS_MAX (50).
    - Total (alojamiento + horas extras) dentro del rango plausible de la receptora.
    Prioridad de ordenamiento:
    1. fecha_out == fecha_ref_dmy (el huésped se quedó horas extras al salir).
    2. fecha_in == fecha_ref_dmy (mismo día).
    3. fecha_in == fecha_ayer (día anterior).
    """
    if not (HORAS_EXTRAS_MIN <= horas_extras <= HORAS_EXTRAS_MAX):
        return []

    fecha_ref_dmy = str(fecha_ref_dmy or "").strip()
    fecha_ayer = calcular_fecha_ayer(fecha_ref_dmy)
    fechas_in = [f for f in (fecha_ref_dmy, fecha_ayer) if f]

    cur = conn.cursor()
    placeholders = ",".join("?" for _ in fechas_in)
    params: List[Any] = list(fechas_in) + [fecha_ref_dmy]

    sql = f"""
        SELECT * FROM estancias
        WHERE (fecha_in_dmy IN ({placeholders}) OR fecha_out_dmy = ?)
          AND (ruc IS NULL OR trim(ruc) IN ('', '-'))
          AND (empresa IS NULL OR trim(empresa) IN ('', '-'))
          AND lower(coalesce(tipo_comp, '')) NOT LIKE '%fact%'
          AND (nro_comp IS NULL OR trim(nro_comp) = '')
          AND lower(coalesce(estado_comp, '')) NOT IN ('emitido', 'unificado')
          AND (unificado_en_uuid IS NULL OR trim(unificado_en_uuid) = '')
          AND lower(coalesce(tipo_adicional, '')) NOT LIKE '%horas extras%'
          AND coalesce(adic_comp, 0) = 0
    """
    rows = cur.execute(sql, params).fetchall()

    candidatas = []
    for r in rows:
        d = dict(r)
        uuid_rec = d.get("estancia_uuid", "")
        if uuid_rec == exclude_uuid:
            continue

        # Validar celular: si tiene >= 7 dígitos numéricos, tiene WSP y no es apta receptora ciega
        cel = str(d.get("celular") or "")
        digitos_cel = sum(1 for ch in cel if ch.isdigit())
        if digitos_cel >= 7:
            continue

        yape_rec = round(float(d.get("pago_ini_yape") or 0.0) + float(d.get("pago_post_yape") or 0.0), 2)
        total_con_extras = round(yape_rec + horas_extras, 2)

        rango_rec = rango_por_tipo(d.get("tipo_hab", ""), d.get("n_acomp", ""))
        if not (rango_rec["boleta_min"] <= total_con_extras <= rango_rec["boleta_max"]):
            continue

        # Determinar peso de orden según fecha
        f_in = str(d.get("fecha_in_dmy") or "").strip()
        f_out = str(d.get("fecha_out_dmy") or "").strip()
        if f_out == fecha_ref_dmy:
            peso_prioridad = 0  # Máxima prioridad: checkout en la fecha del donante
        elif f_in == fecha_ref_dmy:
            peso_prioridad = 1  # Mismo día de ingreso
        else:
            peso_prioridad = 2  # Día anterior

        d["_yape_propio"] = yape_rec
        d["_total_con_extras"] = total_con_extras
        d["_peso_prioridad"] = peso_prioridad
        candidatas.append(d)

    # Ordenar por prioridad de fecha y luego por hab
    candidatas.sort(key=lambda x: (x["_peso_prioridad"], str(x.get("hab", ""))))
    return candidatas


def buscar_yapes_tienda_libres(conn: sqlite3.Connection, fecha_ref_dmy: str) -> List[Dict[str, Any]]:
    """
    Busca transacciones Yape en ventas_tienda disponibles (no asociadas a ninguna boleta receptora).
    Filtra por la fecha de referencia y el día anterior.
    """
    fecha_ref_dmy = str(fecha_ref_dmy or "").strip()
    fecha_ayer = calcular_fecha_ayer(fecha_ref_dmy)
    fechas = [f for f in (fecha_ref_dmy, fecha_ayer) if f]

    cur = conn.cursor()
    placeholders = ",".join("?" for _ in fechas)
    sql = f"""
        SELECT venta_uuid, fecha_dmy, producto, cantidad, subtotal, nro_op_yape, hab
        FROM ventas_tienda
        WHERE lower(coalesce(metodo_pago, '')) LIKE '%yape%'
          AND (yape_tienda_usado_en IS NULL OR trim(yape_tienda_usado_en) = '')
          AND fecha_dmy IN ({placeholders})
        ORDER BY fecha_dmy DESC, rowid DESC
    """
    rows = cur.execute(sql, fechas).fetchall()
    return [dict(r) for r in rows]


# ==============================================================================
# MOTOR DE DECISIÓN Y SUGERENCIAS (OPCIONES B, T, C, A)
# ==============================================================================

def sugerir_unificacion(
    estancia_uuid: str,
    modo_mas_boletas: Optional[bool] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Genera las opciones de unificación y emisión para una estancia con Yape inexacto.
    Opciones generadas:
    - 🥇 B: Unificar donante como horas extras en receptora (Colchón 0.00).
    - 🥇 T: Unificar Yapes de tienda como horas extras (Colchón 0.00).
    - 🥈 C: Boleta simple con baño privado por S/ 50.00 (Colchón moderado).
    - 🥉 A: Boleta completa por tarifa pactada (Colchón completo).
    """
    should_close = False
    if conn is None:
        conn = database.get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (estancia_uuid,)).fetchone()
        if not row:
            return {"ok": False, "error": f"Estancia no encontrada: {estancia_uuid}"}

        estancia = dict(row)
        fecha_in = str(estancia.get("fecha_in_dmy") or "").strip()
        yape_banco = round(float(estancia.get("pago_ini_yape") or 0.0) + float(estancia.get("pago_post_yape") or 0.0), 2)
        tarifa_estancia = float(estancia.get("tarifa") or 0.0)
        tipo_hab = str(estancia.get("tipo_hab") or "").strip()
        n_acomp = str(estancia.get("n_acomp") or "").strip()
        rango = rango_por_tipo(tipo_hab, n_acomp)

        # Determinar modo_mas_boletas persistido si no fue pasado explícitamente
        if modo_mas_boletas is None:
            modo_mas_boletas = obtener_modo_mas_boletas(conn)

        # Comprobar si califica como inexacto
        inexacto = es_pendiente_inexacto(estancia)
        if not inexacto:
            # Caso 1: Yape menor a 10
            if yape_banco < UMBRAL_TIENDA:
                return {
                    "ok": True,
                    "estancia_uuid": estancia_uuid,
                    "es_inexacto": False,
                    "mensaje": "Yape menor a S/ 10.00; lo absorbe la Bolsa Nivel 2",
                    "opciones": [],
                    "sugerida": "",
                }
            # Caso 2: Yape dentro de rango
            if rango["boleta_min"] <= yape_banco <= rango["boleta_max"]:
                return {
                    "ok": True,
                    "estancia_uuid": estancia_uuid,
                    "es_inexacto": False,
                    "mensaje": "Yape dentro del rango; se emite exacto sin sugerencia",
                    "opciones": [],
                    "sugerida": "",
                }
            # Caso 3: Ya emitido o unificado
            if estancia.get("estado_comp") in ("Emitido", "Unificado") or estancia.get("nro_comp"):
                return {
                    "ok": True,
                    "estancia_uuid": estancia_uuid,
                    "es_inexacto": False,
                    "mensaje": "Estancia ya emitida o unificada",
                    "opciones": [],
                    "sugerida": "",
                }

        opciones: List[Dict[str, Any]] = []

        # ----------------------------------------------------------------------
        # 1. OPCIÓN B: Horas Extras en Receptora
        # ----------------------------------------------------------------------
        receptoras = buscar_receptoras(conn, fecha_in, horas_extras=yape_banco, exclude_uuid=estancia_uuid)
        receptora_b = receptoras[0] if receptoras else None
        if receptora_b:
            rec_yape = receptora_b["_yape_propio"]
            total_b = receptora_b["_total_con_extras"]
            opciones.append({
                "codigo": "B",
                "titulo": "Yape de otra habitación como horas extras",
                "medalla": "🥇 Opción B",
                "colchon": 0.0,
                "monto_total": total_b,
                "receptora_uuid": receptora_b["estancia_uuid"],
                "receptora_hab": receptora_b["hab"],
                "receptora_huesped": receptora_b["huesped"],
                "receptora_fecha_in": receptora_b["fecha_in_dmy"],
                "receptora_fecha_out": receptora_b["fecha_out_dmy"],
                "items_preview": [
                    {
                        "descripcion": f"DIA DE ALOJAMIENTO DEL {receptora_b['fecha_in_dmy']}",
                        "monto": rec_yape,
                    },
                    {
                        "descripcion": f"ADICIONAL POR HORAS EXTRAS DEL {fecha_in}",
                        "monto": yape_banco,
                    },
                ],
                "descripcion": (
                    f"En la Boleta de Hab {receptora_b['hab']} ({receptora_b['huesped']}): "
                    f"alojamiento S/ {rec_yape:.2f} + horas extras S/ {yape_banco:.2f}. "
                    f"Total S/ {total_b:.2f} (Colchón S/ 0.00)"
                ),
            })

        # ----------------------------------------------------------------------
        # 2. OPCIÓN T: Yapes de Tienda como Horas Extras
        # ----------------------------------------------------------------------
        yapes_tienda = buscar_yapes_tienda_libres(conn, fecha_in)
        # Combinar Yapes de tienda si su suma entra en rango (10 a 50)
        suma_tienda = round(sum(float(yt.get("subtotal") or 0.0) for yt in yapes_tienda[:3]), 2)
        if HORAS_EXTRAS_MIN <= suma_tienda <= HORAS_EXTRAS_MAX and receptoras:
            # Buscar receptora para la tienda
            rec_t = receptoras[0]
            rec_t_yape = rec_t["_yape_propio"]
            total_t = round(rec_t_yape + suma_tienda, 2)
            rango_t = rango_por_tipo(rec_t.get("tipo_hab", ""), rec_t.get("n_acomp", ""))
            if rango_t["boleta_min"] <= total_t <= rango_t["boleta_max"]:
                tienda_uuids = [yt["venta_uuid"] for yt in yapes_tienda[:3]]
                opciones.append({
                    "codigo": "T",
                    "titulo": "Yapes de tienda como horas extras",
                    "medalla": "🥇 Opción T",
                    "colchon": 0.0,
                    "monto_total": total_t,
                    "receptora_uuid": rec_t["estancia_uuid"],
                    "receptora_hab": rec_t["hab"],
                    "receptora_huesped": rec_t["huesped"],
                    "receptora_fecha_in": rec_t["fecha_in_dmy"],
                    "receptora_fecha_out": rec_t["fecha_out_dmy"],
                    "tienda_uuids": tienda_uuids,
                    "items_preview": [
                        {
                            "descripcion": f"DIA DE ALOJAMIENTO DEL {rec_t['fecha_in_dmy']}",
                            "monto": rec_t_yape,
                        },
                        {
                            "descripcion": f"ADICIONAL POR HORAS EXTRAS (TIENDA)",
                            "monto": suma_tienda,
                        },
                    ],
                    "descripcion": (
                        f"Sumar S/ {suma_tienda:.2f} de Yapes de tienda a la Boleta de Hab {rec_t['hab']} "
                        f"({rec_t['huesped']}). Total S/ {total_t:.2f} (Colchón S/ 0.00)"
                    ),
                })

        # ----------------------------------------------------------------------
        # 3. OPCIÓN C: Boleta Simple con Baño Privado S/ 50.00
        # ----------------------------------------------------------------------
        colchon_c = calcular_colchon(MONTO_OPCION_C, yape_banco)
        opciones.append({
            "codigo": "C",
            "titulo": "Boleta simple con baño privado",
            "medalla": "🥈 Opción C",
            "colchon": colchon_c,
            "monto_total": MONTO_OPCION_C,
            "items_preview": [
                {
                    "descripcion": f"DIA DE ALOJAMIENTO DEL {fecha_in}",
                    "monto": MONTO_OPCION_C,
                }
            ],
            "descripcion": f"1 ítem de alojamiento simple con baño privado por S/ {MONTO_OPCION_C:.2f} (Colchón S/ {colchon_c:.2f})",
        })

        # ----------------------------------------------------------------------
        # 4. OPCIÓN A: Boleta Completa (Tarifa Pactada)
        # ----------------------------------------------------------------------
        monto_a = max(MINIMO_COMPROBANTE, tarifa_estancia)
        colchon_a = calcular_colchon(monto_a, yape_banco)
        opciones.append({
            "codigo": "A",
            "titulo": "Boleta completa",
            "medalla": "🥉 Opción A",
            "colchon": colchon_a,
            "monto_total": monto_a,
            "items_preview": [
                {
                    "descripcion": f"DIA DE ALOJAMIENTO DEL {fecha_in}",
                    "monto": monto_a,
                }
            ],
            "descripcion": f"1 ítem de alojamiento por la tarifa completa S/ {monto_a:.2f} (Colchón S/ {colchon_a:.2f})",
        })

        # ----------------------------------------------------------------------
        # ORDENAMIENTO SEGÚN MODO Y APRENDIZAJE PROGRESIVO
        # ----------------------------------------------------------------------
        escenario = f"{tipo_hab}|{int(yape_banco)}|{'mas' if modo_mas_boletas else 'std'}"

        # Consultar historial en decisiones_boleta (Aprendizaje progresivo)
        hist = cur.execute(
            """
            SELECT elegida, count(*) as cnt
            FROM decisiones_boleta
            WHERE escenario = ?
            GROUP BY elegida
            ORDER BY cnt DESC
            """,
            (escenario,),
        ).fetchall()

        total_decisiones = sum(r[1] for r in hist)
        opcion_habitual = ""
        if total_decisiones >= 3 and hist:
            top_opcion, top_cnt = hist[0][0], hist[0][1]
            if top_cnt / total_decisiones >= 0.60:
                opcion_habitual = top_opcion

        # Etiquetar medalla habitual si corresponde preservando la identidad de la opción
        for opt in opciones:
            if opt["codigo"] == opcion_habitual:
                opt["habitual"] = True
                opt["es_habitual"] = True
                medalla_base = opt.get("medalla") or f"Opción {opt['codigo']}"
                opt["medalla"] = f"{medalla_base} · ⭐ Tu elección habitual"

        if modo_mas_boletas:
            # Modo "Necesito más boletas": Priorizar montos altos para cubrir el déficit BCP
            # Opción A (tarifa alta) > Opción C > Opciones B/T
            def rank_mas(opt: Dict[str, Any]) -> Tuple[int, float, float]:
                is_habitual = 0 if opt.get("habitual") else 1
                # Queremos mayor monto_total primero, luego mayor colchón
                return (is_habitual, -opt["monto_total"], -opt["colchon"])

            opciones.sort(key=rank_mas)
        else:
            # Modo Estándar: Priorizar colchón 0 (B, T), luego menor colchón (C sobre A)
            def rank_std(opt: Dict[str, Any]) -> Tuple[int, int, float, float]:
                is_habitual = 0 if opt.get("habitual") else 1
                has_zero_colchon = 0 if opt["colchon"] == 0.0 else 1
                return (is_habitual, has_zero_colchon, opt["colchon"], -opt["monto_total"])

            opciones.sort(key=rank_std)

        sugerida = opciones[0]["codigo"] if opciones else ""

        return {
            "ok": True,
            "estancia_uuid": estancia_uuid,
            "estancia": estancia,
            "yape_banco": yape_banco,
            "es_inexacto": inexacto,
            "opciones": opciones,
            "sugerida": sugerida,
            "escenario": escenario,
            "modo_mas_boletas": modo_mas_boletas,
            "opcion_habitual": opcion_habitual,
        }
    finally:
        if should_close:
            conn.close()


# ==============================================================================
# APLICACIÓN Y PERSISTENCIA DE DECISIONES
# ==============================================================================

def aplicar_decision(payload: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Aplica y persiste la decisión seleccionada por el usuario (o por borrador).
    Actualiza la base de datos de manera transaccional:
    - Para B: Receptora recibe horas extras en adic_comp y monto_factura;
              Donante queda estado_comp='Unificado', unificado_en_uuid=receptora_uuid.
    - Para T: Receptora recibe suma de tienda; ventas_tienda marca yape_tienda_usado_en.
    - Para C/A: Estancia ajusta monto_factura y excedente_efec_sunat=colchon.
    - Inserta registro de auditoría en decisiones_boleta.
    """
    should_close = False
    if conn is None:
        conn = database.get_connection()
        should_close = True

    try:
        cur = conn.cursor()
        estancia_uuid = str(payload.get("estancia_uuid") or "").strip()
        opcion = str(payload.get("opcion") or "").strip().upper()
        receptora_uuid = str(payload.get("receptora_uuid") or "").strip()
        tienda_uuids = payload.get("tienda_uuids") or []
        colchon = float(payload.get("colchon") or 0.0)
        usuario = str(payload.get("usuario") or "Recepcionista").strip()
        escenario = str(payload.get("escenario") or "").strip()
        now_iso = datetime.now().isoformat()

        row_est = cur.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (estancia_uuid,)).fetchone()
        if not row_est:
            return {"ok": False, "error": f"Estancia donante no encontrada: {estancia_uuid}"}

        donante = dict(row_est)
        yape_donante = round(float(donante.get("pago_ini_yape") or 0.0) + float(donante.get("pago_post_yape") or 0.0), 2)
        items_borrador = []

        if opcion == "B":
            if not receptora_uuid:
                return {"ok": False, "error": "Opción B requiere receptora_uuid"}

            row_rec = cur.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (receptora_uuid,)).fetchone()
            if not row_rec:
                return {"ok": False, "error": f"Estancia receptora no encontrada: {receptora_uuid}"}

            receptora = dict(row_rec)
            rec_yape = round(float(receptora.get("pago_ini_yape") or 0.0) + float(receptora.get("pago_post_yape") or 0.0), 2)
            adic_actual = float(receptora.get("adic_comp") or 0.0)
            nuevo_adic = round(adic_actual + yape_donante, 2)
            nuevo_monto = round(rec_yape + yape_donante, 2)

            tipo_adic = f"HORAS EXTRAS (Hab {donante.get('hab', '')})"

            # Actualizar receptora
            cur.execute(
                """
                UPDATE estancias
                SET adic_comp = ?, monto_factura = ?, tipo_adicional = ?, dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (nuevo_adic, nuevo_monto, tipo_adic, now_iso, receptora_uuid),
            )

            # Actualizar donante como Unificado
            cur.execute(
                """
                UPDATE estancias
                SET estado_comp = 'Unificado', unificado_en_uuid = ?, opcion_decision = 'B', dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (receptora_uuid, now_iso, estancia_uuid),
            )

            items_borrador = [
                {"descripcion": f"DIA DE ALOJAMIENTO DEL {receptora.get('fecha_in_dmy')}", "monto": rec_yape},
                {"descripcion": f"ADICIONAL POR HORAS EXTRAS DEL {donante.get('fecha_in_dmy')}", "monto": yape_donante},
            ]

        elif opcion == "T":
            if not receptora_uuid:
                return {"ok": False, "error": "Opción T requiere receptora_uuid"}

            row_rec = cur.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (receptora_uuid,)).fetchone()
            if not row_rec:
                return {"ok": False, "error": f"Estancia receptora no encontrada: {receptora_uuid}"}

            receptora = dict(row_rec)
            rec_yape = round(float(receptora.get("pago_ini_yape") or 0.0) + float(receptora.get("pago_post_yape") or 0.0), 2)

            # Sumar subtotales de las ventas tienda indicadas
            suma_tienda = 0.0
            if tienda_uuids:
                ph = ",".join("?" for _ in tienda_uuids)
                t_rows = cur.execute(f"SELECT subtotal FROM ventas_tienda WHERE venta_uuid IN ({ph})", tienda_uuids).fetchall()
                suma_tienda = round(sum(float(r[0] or 0.0) for r in t_rows), 2)
                # Marcar ventas_tienda como usadas
                cur.execute(f"UPDATE ventas_tienda SET yape_tienda_usado_en = ? WHERE venta_uuid IN ({ph})", [receptora_uuid] + tienda_uuids)

            adic_actual = float(receptora.get("adic_comp") or 0.0)
            nuevo_adic = round(adic_actual + suma_tienda, 2)
            nuevo_monto = round(rec_yape + suma_tienda, 2)

            cur.execute(
                """
                UPDATE estancias
                SET adic_comp = ?, monto_factura = ?, tipo_adicional = 'HORAS EXTRAS (Tienda)', dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (nuevo_adic, nuevo_monto, now_iso, receptora_uuid),
            )

            items_borrador = [
                {"descripcion": f"DIA DE ALOJAMIENTO DEL {receptora.get('fecha_in_dmy')}", "monto": rec_yape},
                {"descripcion": "ADICIONAL POR HORAS EXTRAS (TIENDA)", "monto": suma_tienda},
            ]

        elif opcion == "C":
            cur.execute(
                """
                UPDATE estancias
                SET monto_factura = ?, excedente_efec_sunat = ?, opcion_decision = 'C', dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (MONTO_OPCION_C, colchon, now_iso, estancia_uuid),
            )
            items_borrador = [
                {"descripcion": f"DIA DE ALOJAMIENTO DEL {donante.get('fecha_in_dmy')}", "monto": MONTO_OPCION_C}
            ]

        elif opcion == "A":
            monto_a = max(MINIMO_COMPROBANTE, float(donante.get("tarifa") or 0.0))
            cur.execute(
                """
                UPDATE estancias
                SET monto_factura = ?, excedente_efec_sunat = ?, opcion_decision = 'A', dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (monto_a, colchon, now_iso, estancia_uuid),
            )
            items_borrador = [
                {"descripcion": f"DIA DE ALOJAMIENTO DEL {donante.get('fecha_in_dmy')}", "monto": monto_a}
            ]

        # Registrar en decisiones_boleta
        cur.execute(
            """
            INSERT INTO decisiones_boleta (
                fecha_iso, estancia_uuid, escenario, opciones_json, sugerida,
                elegida, receptora_uuid, origen_tienda_json, colchon, usuario, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso,
                estancia_uuid,
                escenario,
                json.dumps(payload.get("opciones") or []),
                str(payload.get("sugerida") or ""),
                opcion,
                receptora_uuid,
                json.dumps(tienda_uuids),
                colchon,
                usuario,
                now_iso,
            ),
        )

        conn.commit()
        return {
            "ok": True,
            "mensaje": f"Decisión {opcion} aplicada con éxito",
            "items_borrador": items_borrador,
            "colchon": colchon,
            "opcion": opcion,
            "receptora_uuid": receptora_uuid,
        }
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        if should_close:
            conn.close()


# ==============================================================================
# GESTIÓN DEL INTERRUPTOR "NECESITO MÁS BOLETAS"
# ==============================================================================

def guardar_modo_mas_boletas(activo: bool, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Persiste el estado del interruptor de modo más boletas en estado_sistema."""
    should_close = False
    if conn is None:
        conn = database.get_connection()
        should_close = True

    try:
        val_str = "1" if activo else "0"
        now_iso = datetime.now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('modo_mas_boletas', ?, ?)",
            (val_str, now_iso),
        )
        conn.commit()
        return {"ok": True, "activo": bool(activo)}
    finally:
        if should_close:
            conn.close()


def obtener_modo_mas_boletas(conn: Optional[sqlite3.Connection] = None) -> bool:
    """Lee el estado del interruptor de modo más boletas desde estado_sistema."""
    should_close = False
    if conn is None:
        conn = database.get_connection()
        should_close = True

    try:
        row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'modo_mas_boletas'").fetchone()
        if not row:
            return False
        return str(row[0]).strip() in ("1", "true", "True")
    finally:
        if should_close:
            conn.close()
