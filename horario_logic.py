#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo ⏰ Horario y Pagos de Personal (offline-first, SQLite).

- Marcación abierta (sin contraseña) + edición libre de horas con rastro de auditoría inborrable.
- Liquidación de pagos protegida por contraseña (sueldo, faltas, descansos, feriados, horas extras,
  AFP, adelantos de sueldo desde Gastos y consumos de tienda 'Cargar a la Cuenta').
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from database import db_transaction, get_connection
import domain_logic as dl

# Hash SHA-256 de la clave de Cuentas/Pagos (la clave nunca se guarda ni se muestra en claro).
_CLAVE_CUENTAS_SHA256 = "947ebb53c00a42907060aea69f503600eec08ef75da83109f2958865a4d8f934"

CAMPOS_HORA = ("e1", "s1", "e2", "s2")
CAMPOS_ADMIN: Tuple[str, ...] = ()  # Estado (F) y Horas Extras (G) libres sin clave (auditados siempre)
CAMPOS_EDITABLES = CAMPOS_HORA + ("estado", "extras", "extra_noche", "nota")
ETIQUETA_CAMPO = {
    "e1": "Entrada 1", "s1": "Salida 1", "e2": "Entrada 2", "s2": "Salida 2",
    "estado": "Estado (F)", "extras": "Horas extras (G)", "extra_noche": "Extra nocturno", "nota": "Nota",
}
ESTADOS_VALIDOS = ("", "FALTÓ", "MEDIO TIEMPO", "MEDIA FALTA", "DESCANSO", "MEDIO DESCANSO", "LIBRE", "FERIADO")
DOW_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DOW_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


# ==============================================================================
# UTILIDADES
# ==============================================================================

def validar_clave_cuentas(datos: Dict[str, Any]) -> None:
    clave = str((datos or {}).get("clave") or "").strip().lower()
    if hashlib.sha256(clave.encode("utf-8")).hexdigest() != _CLAVE_CUENTAS_SHA256:
        raise ValueError("Contraseña incorrecta.")


def verificar_clave_cuentas_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    return {"ok": True}


def _norm_hora(v: Any) -> str:
    """'8' -> '08:00', '8:5' -> '08:05', '0805' -> '08:05', '8.30' -> '08:30', '8:30 pm' -> '20:30'. Vacío -> ''."""
    s = str(v or "").strip().lower().replace(".", ":").replace("h", ":").replace(" ", "")
    if not s:
        return ""
    pm = s.endswith("pm") or s.endswith("p.m.")
    am = s.endswith("am") or s.endswith("a.m.")
    s = re.sub(r"(a\.?m\.?|p\.?m\.?)$", "", s).rstrip(":")
    if re.fullmatch(r"\d{3,4}", s):
        s = s[:-2] + ":" + s[-2:]
    m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", s)
    if not m:
        raise ValueError(f"Hora no válida: '{v}'. Usa formato 8:30 o 20:00.")
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if h > 23 or mi > 59:
        raise ValueError(f"Hora no válida: '{v}'.")
    return f"{h:02d}:{mi:02d}"


def _minutos(hm: str) -> int:
    p = hm.split(":")
    return int(p[0]) * 60 + int(p[1])


def _horas_pares(r: Dict[str, Any]) -> float:
    total = 0
    for a, b in (("e1", "s1"), ("e2", "s2")):
        if r.get(a) and r.get(b):
            d = _minutos(r[b]) - _minutos(r[a])
            if d <= 0:
                d += 24 * 60  # turno que cruza medianoche
            total += d
    return round(total / 60.0, 2)


def parse_extras(txt: Any) -> float:
    """'4' -> 4 h, '3/8' -> 3 h (numerador sobre jornada de 8), '1.5' -> 1.5 h."""
    t = str(txt or "").strip().replace(",", ".")
    if not t:
        return 0.0
    if "/" in t:
        a, b = t.split("/", 1)
        num, den = float(a), float(b)
        if den <= 0:
            raise ValueError("Horas extras no válidas.")
        return round(num / den * 8.0, 4)
    return round(float(t), 4)


def _norm_extras(v: Any) -> str:
    t = str(v or "").strip().replace(",", ".")
    if not t:
        return ""
    try:
        parse_extras(t)
    except Exception:
        raise ValueError("Horas extras no válidas. Ejemplos: 4, 2.5 o 4/8.")
    return t


def _norm_estado(v: Any) -> str:
    t = str(v or "").strip().upper().replace("FALTO", "FALTÓ").replace("MEDIO TIMEPO", "MEDIO TIEMPO")
    if t not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado no válido: '{v}'.")
    return t


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, mes, dia)


def feriados_peru(year: int) -> Dict[str, str]:
    """Feriados nacionales (clave dd/mm/yyyy)."""
    fijos = [
        (1, 1, "Año Nuevo"), (1, 5, "Día del Trabajo"), (7, 6, "Batalla de Arica"),
        (29, 6, "San Pedro y San Pablo"), (23, 7, "Día de la Fuerza Aérea"),
        (28, 7, "Fiestas Patrias"), (29, 7, "Fiestas Patrias"), (6, 8, "Batalla de Junín"),
        (30, 8, "Santa Rosa de Lima"), (8, 10, "Combate de Angamos"), (1, 11, "Todos los Santos"),
        (8, 12, "Inmaculada Concepción"), (9, 12, "Batalla de Ayacucho"), (25, 12, "Navidad"),
    ]
    res = {f"{d:02d}/{m:02d}/{year}": n for d, m, n in fijos}
    pascua = _easter(year)
    jueves = pascua - timedelta(days=3)
    viernes = pascua - timedelta(days=2)
    res[jueves.strftime("%d/%m/%Y")] = "Jueves Santo"
    res[viernes.strftime("%d/%m/%Y")] = "Viernes Santo"
    return res


def _fecha(dmy: str) -> date:
    return datetime.strptime(dl.normalizar_fecha_dmy(dmy), "%d/%m/%Y").date()


def _dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _ymd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _cfg_empleado(conn, empleado: str) -> Dict[str, Any]:
    r = conn.execute("SELECT * FROM personal_config WHERE empleado = ? COLLATE NOCASE", (str(empleado or "").strip(),)).fetchone()
    if not r:
        raise ValueError(f"Empleado no encontrado: {empleado}")
    return dict(r)


def _lista_empleados(conn, solo_activos: bool = True) -> List[Dict[str, Any]]:
    q = "SELECT * FROM personal_config" + (" WHERE activo = 1" if solo_activos else "") + " ORDER BY orden, empleado"
    return [dict(r) for r in conn.execute(q).fetchall()]


def _fila_vacia(emp: str, fecha_dmy: str) -> Dict[str, Any]:
    return {"empleado": emp, "fecha_dmy": fecha_dmy, "ymd": dl.fecha_dmy_a_ymd(fecha_dmy), "e1": "", "s1": "", "e2": "", "s2": "",
            "estado": "", "extras": "", "extra_noche": 0, "nota": "", "ult_marca_ts": "", "updated_at": "", "equipo": "", "synced": 1}


def _leer_fila(conn, emp: str, fecha_dmy: str) -> Dict[str, Any]:
    r = conn.execute("SELECT * FROM horario_registros WHERE empleado = ? AND fecha_dmy = ?", (emp, fecha_dmy)).fetchone()
    return dict(r) if r else _fila_vacia(emp, fecha_dmy)


def _guardar_fila(conn, fila: Dict[str, Any]) -> None:
    ahora = datetime.now().isoformat(timespec="seconds")
    fila["updated_at"] = ahora
    fila["equipo"] = dl._nombre_equipo()
    fila["synced"] = 0
    fila["ymd"] = dl.fecha_dmy_a_ymd(fila["fecha_dmy"])
    conn.execute(
        """
        INSERT OR REPLACE INTO horario_registros
            (empleado, fecha_dmy, ymd, e1, s1, e2, s2, estado, extras, extra_noche, nota, ult_marca_ts, updated_at, equipo, synced)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        """,
        (fila["empleado"], fila["fecha_dmy"], fila["ymd"], fila["e1"], fila["s1"], fila["e2"], fila["s2"], fila["estado"],
         fila["extras"], int(fila["extra_noche"] or 0), fila["nota"], fila.get("ult_marca_ts", ""), ahora, fila["equipo"]),
    )
    dl._encolar_operacion(
        conn, "HORARIO_UPSERT", f"{fila['empleado']}|{fila['fecha_dmy']}",
        {"empleado": fila["empleado"], "fecha_dmy": fila["fecha_dmy"], "updated_at": ahora,
         "fila": [fila["empleado"], fila["fecha_dmy"], fila["e1"], fila["s1"], fila["e2"], fila["s2"], fila["estado"],
                  fila["extras"], int(fila["extra_noche"] or 0), fila["nota"], ahora, fila["equipo"]]},
    )


def _auditar(conn, emp: str, fecha_dmy: str, campo: str, antes: str, nuevo: str) -> None:
    audit_uuid = str(uuid.uuid4())
    ts = datetime.now().isoformat(timespec="seconds")
    equipo = dl._nombre_equipo()
    conn.execute(
        "INSERT INTO horario_auditoria (audit_uuid, empleado, fecha_dmy, campo, valor_anterior, valor_nuevo, editado_ts, equipo, synced) "
        "VALUES (?,?,?,?,?,?,?,?,0)",
        (audit_uuid, emp, fecha_dmy, campo, str(antes), str(nuevo), ts, equipo),
    )
    dl._encolar_operacion(
        conn, "HORARIO_AUDIT", audit_uuid,
        {"audit_uuid": audit_uuid, "fila": [audit_uuid, emp, fecha_dmy, ETIQUETA_CAMPO.get(campo, campo), str(antes), str(nuevo), ts, equipo]},
    )


def _fila_publica(r: Dict[str, Any], cfg: Dict[str, Any], hoy: date, feriados: Dict[str, str]) -> Dict[str, Any]:
    d = _fecha(r["fecha_dmy"])
    return {
        "fecha": r["fecha_dmy"], "fechaIso": d.isoformat(), "dia": d.day, "dow": d.weekday(), "dowNombre": DOW_ES[d.weekday()],
        "esDescanso": d.weekday() == int(cfg["descanso_dow"]),
        "feriado": feriados.get(r["fecha_dmy"], ""),
        "e1": r["e1"], "s1": r["s1"], "e2": r["e2"], "s2": r["s2"],
        "estado": r["estado"], "extras": r["extras"], "extraNoche": int(r["extra_noche"] or 0), "nota": r["nota"],
        "horas": _horas_pares(r), "esHoy": d == hoy, "futuro": d > hoy,
    }


# ==============================================================================
# VISTA ABIERTA: HORARIO MENSUAL + MARCACIÓN
# ==============================================================================

def obtener_horario_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    hoy = datetime.now().date()
    mes = str(datos.get("mes") or hoy.strftime("%Y-%m"))
    y, m = int(mes[:4]), int(mes[5:7])
    conn = get_connection()
    try:
        emps = _lista_empleados(conn)
        if not emps:
            return {"empleados": [], "dias": []}
        nombre = str(datos.get("empleado") or emps[0]["empleado"])
        cfg = next((e for e in emps if e["empleado"].lower() == nombre.lower()), emps[0])
        feriados = feriados_peru(y)
        n_dias = calendar.monthrange(y, m)[1]
        ini, fin = _ymd(date(y, m, 1)), _ymd(date(y, m, n_dias))
        reg = {r["fecha_dmy"]: dict(r) for r in conn.execute(
            "SELECT * FROM horario_registros WHERE empleado = ? AND ymd BETWEEN ? AND ?", (cfg["empleado"], ini, fin)).fetchall()}
        dias = []
        for dd in range(1, n_dias + 1):
            f = _dmy(date(y, m, dd))
            dias.append(_fila_publica(reg.get(f) or _fila_vacia(cfg["empleado"], f), cfg, hoy, feriados))
        total_h = round(sum(x["horas"] for x in dias), 2)
        trab = sum(1 for x in dias if x["horas"] > 0 or x["e1"])
        return {
            "empleados": [{"id": e["empleado"], "rol": e["rol"], "turno": e["turno"], "descansoDow": e["descanso_dow"],
                           "descansoNombre": DOW_LARGO[int(e["descanso_dow"])]} for e in emps],
            "empleado": cfg["empleado"], "rol": cfg["rol"], "turno": cfg["turno"],
            "descansoDow": cfg["descanso_dow"], "descansoNombre": DOW_LARGO[int(cfg["descanso_dow"])],
            "mes": f"{y:04d}-{m:02d}", "hoyDmy": _dmy(hoy), "ahoraHM": datetime.now().strftime("%H:%M"),
            "dias": dias, "totalHoras": total_h, "diasTrabajados": trab,
            "proximaMarca": _proxima_marca_txt(conn, cfg),
        }
    finally:
        conn.close()


def _decidir_marca(conn, cfg: Dict[str, Any], ahora: datetime) -> Tuple[str, str]:
    emp = cfg["empleado"]
    hoy_dmy = _dmy(ahora.date())
    if str(cfg["turno"]).upper() == "NOCHE" and ahora.hour < 15:
        ayer = _dmy(ahora.date() - timedelta(days=1))
        fa = _leer_fila(conn, emp, ayer)
        if fa["e1"] and not fa["s1"]:
            return ayer, "s1"
    fh = _leer_fila(conn, emp, hoy_dmy)
    for c in CAMPOS_HORA:
        if not fh[c]:
            return hoy_dmy, c
    raise ValueError("Ya tienes las 4 casillas de hoy marcadas. Si necesitas corregir, edita la hora en la tabla.")


def _proxima_marca_txt(conn, cfg: Dict[str, Any]) -> str:
    try:
        f, c = _decidir_marca(conn, cfg, datetime.now())
        return f"{ETIQUETA_CAMPO[c]} ({f[:5]})"
    except ValueError:
        return "Día completo"


def marcar_asistencia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    emp = str((datos or {}).get("empleado") or "").strip()
    ahora = datetime.now()
    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
        fecha, campo = _decidir_marca(conn, cfg, ahora)
        fila = _leer_fila(conn, cfg["empleado"], fecha)
        prev = str(fila.get("ult_marca_ts") or "")
        if prev:
            try:
                if (ahora - datetime.fromisoformat(prev)).total_seconds() < 120:
                    raise ValueError("Ya marcaste hace menos de 2 minutos. Espera un momento si quieres marcar la siguiente casilla.")
            except ValueError as e:
                if "marcaste" in str(e):
                    raise
        hm = ahora.strftime("%H:%M")
        fila[campo] = hm
        fila["ult_marca_ts"] = ahora.isoformat(timespec="seconds")
        _guardar_fila(conn, fila)
    return {"ok": True, "empleado": cfg["empleado"], "fecha": fecha, "campo": campo, "etiqueta": ETIQUETA_CAMPO[campo], "hora": hm}


def editar_celda_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    emp = str(datos.get("empleado") or "").strip()
    campo = str(datos.get("campo") or "").strip()
    fecha = dl.normalizar_fecha_dmy(datos.get("fecha"))
    if campo not in CAMPOS_EDITABLES:
        raise ValueError("Campo no editable.")
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha or ""):
        raise ValueError("Fecha no válida.")
    if campo in CAMPOS_ADMIN:
        validar_clave_cuentas(datos)

    raw = datos.get("valor")
    if campo in CAMPOS_HORA:
        nuevo: Any = _norm_hora(raw)
    elif campo == "estado":
        nuevo = _norm_estado(raw)
    elif campo == "extras":
        nuevo = _norm_extras(raw)
    elif campo == "extra_noche":
        nuevo = 1 if str(raw).strip().lower() in ("1", "true", "si", "sí", "on") else 0
    else:
        nuevo = str(raw or "").strip()[:200]

    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
        fila = _leer_fila(conn, cfg["empleado"], fecha)
        antes = fila[campo]
        if str(antes) == str(nuevo):
            return {"ok": True, "sinCambios": True}
        fila[campo] = nuevo
        _guardar_fila(conn, fila)
        _auditar(conn, cfg["empleado"], fecha, campo, str(antes), str(nuevo))
    return {"ok": True, "valor": nuevo}


# ==============================================================================
# CUENTAS / PAGOS (con contraseña)
# ==============================================================================

def _match_personal(g_personal: str, desc: str, cfg: Dict[str, Any]) -> str:
    claves = {cfg["empleado"].lower(), str(cfg["categoria_tienda"]).lower()}
    try:
        alias = [a.lower() for a in json.loads(cfg.get("alias_json") or "[]")]
    except Exception:
        alias = []
    gp = str(g_personal or "").strip().lower()
    if gp:
        return "personal" if gp in claves or gp in alias else ""
    d = str(desc or "").lower()
    return "descripcion" if any(a and re.search(r"\b" + re.escape(a) + r"\b", d) for a in alias + [cfg["empleado"].lower()]) else ""


def _calcular_liquidacion(conn, cfg: Dict[str, Any], desde: date, hasta: date) -> Dict[str, Any]:
    hoy = datetime.now().date()
    valor_dia = float(cfg["diario_noche"] if str(cfg["turno"]).upper() == "NOCHE" else cfg["diario_manana"])
    reg = {r["fecha_dmy"]: dict(r) for r in conn.execute(
        "SELECT * FROM horario_registros WHERE empleado = ? AND ymd BETWEEN ? AND ?",
        (cfg["empleado"], _ymd(desde), _ymd(hasta))).fetchall()}
    feriados: Dict[str, str] = {}
    for yy in range(desde.year, hasta.year + 1):
        feriados.update(feriados_peru(yy))

    faltas = descansos = feriados_trab = 0.0
    d_falta: List[str] = []
    d_desc: List[str] = []
    d_fer: List[str] = []
    h_m = h_n = 0.0
    extras_det: List[Dict[str, Any]] = []
    sin_marcar: List[str] = []
    horas_totales = 0.0
    cur = desde
    while cur <= hasta:
        f = _dmy(cur)
        r = reg.get(f) or _fila_vacia(cfg["empleado"], f)
        trabajo = any(r[c] for c in CAMPOS_HORA)
        h_dia = _horas_pares(r)
        horas_totales += h_dia
        es_desc = cur.weekday() == int(cfg["descanso_dow"])
        est = r["estado"]
        if est == "FALTÓ":
            faltas += 1
            d_falta.append(f)
        elif est == "MEDIA FALTA" or (est == "MEDIO TIEMPO" and not es_desc):
            faltas += 0.5
            d_falta.append(f + " (½ medio tiempo)")

        if est in ("MEDIO DESCANSO",) or (est == "MEDIO TIEMPO" and es_desc):
            descansos += 0.5
            d_desc.append(f + " (½ medio tiempo)")
        elif est == "DESCANSO":
            descansos += 1
            d_desc.append(f)
        elif es_desc and trabajo and est not in ("LIBRE", "FALTÓ"):
            # Si en su día de descanso solo trabajó un turno / medio tiempo (<= 5.5 h), cuenta como ½ descanso
            if 0 < h_dia <= 5.5:
                descansos += 0.5
                d_desc.append(f + " (½ medio tiempo)")
            else:
                descansos += 1
                d_desc.append(f)

        if f in feriados and (trabajo or est == "FERIADO"):
            feriados_trab += 1
            d_fer.append(f"{f} {feriados[f]}")
        elif est == "FERIADO":
            feriados_trab += 1
            d_fer.append(f)
        hx = parse_extras(r["extras"]) if r["extras"] else 0.0
        if hx:
            # Todas las horas extras se calculan sobre la base de mañana (S/ 50 / 8h)
            h_m += hx
            extras_det.append({"fecha": f, "horas": hx, "raw": r["extras"]})
        if cur < hoy and not trabajo and not est and not es_desc and f not in feriados:
            sin_marcar.append(f)
        cur += timedelta(days=1)

    # Adelantos de sueldo (Registro_Gastos) y consumos de tienda (Cargar a la Cuenta)
    ini_y, fin_y = _ymd(desde), _ymd(hasta)
    adelantos, asignados = [], set()
    todos_cfg = _lista_empleados(conn, solo_activos=False)
    sin_asignar = []
    for g in conn.execute("SELECT * FROM gastos WHERE lower(categoria) LIKE 'adelanto%' ORDER BY rowid").fetchall():
        ymd = dl.fecha_dmy_a_ymd(g["fecha_gasto_dmy"])
        if not (ini_y <= ymd <= fin_y):
            continue
        item = {"fecha": g["fecha_gasto_dmy"], "descripcion": g["descripcion"], "monto": float(g["monto"] or 0), "personal": g["personal"] or ""}
        how = _match_personal(g["personal"], g["descripcion"], cfg)
        if how:
            item["via"] = how
            adelantos.append(item)
            continue
        if not any(_match_personal(g["personal"], g["descripcion"], c) for c in todos_cfg):
            sin_asignar.append(item)
    tienda = []
    for v in conn.execute(
        "SELECT * FROM ventas_tienda WHERE metodo_pago = 'Cargar a la Cuenta' AND personal != '' ORDER BY rowid"
    ).fetchall():
        ymd = dl.fecha_dmy_a_ymd(v["fecha_dmy"])
        if not (ini_y <= ymd <= fin_y):
            continue
        if str(v["personal"]).strip().lower() in (cfg["empleado"].lower(), str(cfg["categoria_tienda"]).lower()):
            tienda.append({"fecha": v["fecha_dmy"], "producto": v["producto"], "cantidad": v["cantidad"], "subtotal": float(v["subtotal"] or 0)})

    n_dias = (hasta - desde).days + 1
    mes_completo = desde.day == 1 and hasta.day == calendar.monthrange(hasta.year, hasta.month)[1] and desde.month == hasta.month and desde.year == hasta.year
    return {
        "empleado": cfg["empleado"], "rol": cfg["rol"], "turno": cfg["turno"], "desde": _dmy(desde), "hasta": _dmy(hasta),
        "sueldo": float(cfg["sueldo"]), "diarioManana": float(cfg["diario_manana"]), "diarioNoche": float(cfg["diario_noche"]),
        "valorDia": valor_dia, "afp": float(cfg["afp"]),
        "diasBase": 30 if mes_completo else min(30, n_dias), "diasPeriodo": n_dias,
        "faltas": faltas, "faltasDias": d_falta, "descansos": descansos, "descansosDias": d_desc,
        "feriados": feriados_trab, "feriadosDias": d_fer,
        "horasExtraM": round(h_m, 2), "horasExtraN": round(h_n, 2), "extrasDetalle": extras_det,
        "adelantos": adelantos, "adelantosTotal": round(sum(a["monto"] for a in adelantos), 2),
        "adelantosSinAsignar": sin_asignar,
        "tienda": tienda, "tiendaTotal": round(sum(t["subtotal"] for t in tienda), 2),
        "sinMarcar": sin_marcar, "horasTotales": round(horas_totales, 2),
    }


def obtener_liquidacion_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    hoy = datetime.now().date()
    desde = _fecha(datos["desde"]) if datos.get("desde") else date(hoy.year, hoy.month, 1)
    hasta = _fecha(datos["hasta"]) if datos.get("hasta") else date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    if hasta < desde:
        raise ValueError("La fecha 'hasta' no puede ser menor que 'desde'.")
    conn = get_connection()
    try:
        cfg = _cfg_empleado(conn, datos.get("empleado"))
        liq = _calcular_liquidacion(conn, cfg, desde, hasta)
        liq["empleados"] = [e["empleado"] for e in _lista_empleados(conn)]
        liq["historial"] = [
            {"liqUuid": r["liq_uuid"], "desde": r["desde_dmy"], "hasta": r["hasta_dmy"], "neto": r["neto"], "yape": r["yape"],
             "efectivo": r["efectivo"], "creado": r["created_at"]}
            for r in conn.execute("SELECT * FROM horario_liquidaciones WHERE empleado = ? ORDER BY created_at DESC LIMIT 12", (cfg["empleado"],)).fetchall()
        ]
        return liq
    finally:
        conn.close()


def obtener_personal_config_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    conn = get_connection()
    try:
        return {"personal": [
            {"empleado": e["empleado"], "rol": e["rol"], "categoriaTienda": e["categoria_tienda"], "sueldo": e["sueldo"],
             "diarioManana": e["diario_manana"], "diarioNoche": e["diario_noche"], "afp": e["afp"],
             "descansoDow": e["descanso_dow"], "turno": e["turno"], "activo": e["activo"]}
            for e in _lista_empleados(conn, solo_activos=False)]}
    finally:
        conn.close()


def guardar_personal_config_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    emp = str(datos.get("empleado") or "").strip()
    if not emp:
        raise ValueError("Falta el nombre del empleado.")
    turno = "NOCHE" if str(datos.get("turno") or "").upper() == "NOCHE" else "DIA"
    vals = {
        "rol": str(datos.get("rol") or "").strip(),
        "categoria_tienda": str(datos.get("categoriaTienda") or "").strip(),
        "sueldo": dl.parse_moneda(datos.get("sueldo")),
        "diario_manana": dl.parse_moneda(datos.get("diarioManana")),
        "diario_noche": dl.parse_moneda(datos.get("diarioNoche")),
        "afp": dl.parse_moneda(datos.get("afp")),
        "descanso_dow": max(0, min(6, int(datos.get("descansoDow") or 0))),
        "turno": turno,
        "activo": 1 if datos.get("activo", 1) in (1, True, "1", "true") else 0,
    }
    with db_transaction() as conn:
        ex = conn.execute("SELECT empleado FROM personal_config WHERE empleado = ? COLLATE NOCASE", (emp,)).fetchone()
        if ex:
            conn.execute(
                "UPDATE personal_config SET rol=?, categoria_tienda=?, sueldo=?, diario_manana=?, diario_noche=?, afp=?, "
                "descanso_dow=?, turno=?, activo=? WHERE empleado = ?",
                (vals["rol"], vals["categoria_tienda"], vals["sueldo"], vals["diario_manana"], vals["diario_noche"], vals["afp"],
                 vals["descanso_dow"], vals["turno"], vals["activo"], ex["empleado"]))
        else:
            orden = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM personal_config").fetchone()[0]
            conn.execute(
                "INSERT INTO personal_config (empleado, rol, categoria_tienda, alias_json, sueldo, diario_manana, diario_noche, afp, "
                "descanso_dow, turno, orden, activo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp, vals["rol"], vals["categoria_tienda"], json.dumps([emp.lower()]), vals["sueldo"], vals["diario_manana"],
                 vals["diario_noche"], vals["afp"], vals["descanso_dow"], vals["turno"], orden, vals["activo"]))
    return {"ok": True}


def obtener_auditoria_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    dias = int(datos.get("dias") or 60)
    emp = str(datos.get("empleado") or "").strip()
    desde_ts = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        turnos = {e["empleado"].lower(): str(e["turno"]).upper() for e in _lista_empleados(conn, solo_activos=False)}
        q = "SELECT * FROM horario_auditoria WHERE editado_ts >= ?"
        par: List[Any] = [desde_ts]
        if emp:
            q += " AND empleado = ? COLLATE NOCASE"
            par.append(emp)
        q += " ORDER BY editado_ts DESC LIMIT 400"
        out = []
        for r in conn.execute(q, par).fetchall():
            try:
                editado = datetime.fromisoformat(r["editado_ts"])
            except Exception:
                continue
            fecha = _fecha(r["fecha_dmy"])
            demora_min = None
            alerta = ""
            if r["campo"] in CAMPOS_HORA and r["valor_nuevo"]:
                h, mi = int(r["valor_nuevo"][:2]), int(r["valor_nuevo"][3:5])
                dia_real = fecha
                if r["campo"] == "s1" and turnos.get(r["empleado"].lower()) == "NOCHE" and h < 14:
                    dia_real = fecha + timedelta(days=1)
                momento = datetime(dia_real.year, dia_real.month, dia_real.day, h, mi)
                demora_min = int((editado - momento).total_seconds() // 60)
            if editado.date() > fecha:
                alerta = "rojo" if (editado.date() - fecha).days >= 1 and (demora_min is None or demora_min > 12 * 60) else "ambar"
            elif demora_min is not None and demora_min > 30:
                alerta = "ambar"
            out.append({
                "empleado": r["empleado"], "fecha": r["fecha_dmy"], "campo": ETIQUETA_CAMPO.get(r["campo"], r["campo"]),
                "antes": r["valor_anterior"], "nuevo": r["valor_nuevo"],
                "editadoFecha": editado.strftime("%d/%m/%Y"), "editadoHora": editado.strftime("%H:%M"),
                "demoraMin": demora_min, "alerta": alerta, "equipo": r["equipo"],
            })
        return {"ediciones": out}
    finally:
        conn.close()


def guardar_liquidacion_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    emp = str(datos.get("empleado") or "").strip()
    neto = round(float(datos.get("neto") or 0), 2)
    yape = round(float(datos.get("yape") or 0), 2)
    efectivo = round(float(datos.get("efectivo") or 0), 2)
    if abs((yape + efectivo) - neto) > 0.05:
        raise ValueError(f"Yape (S/ {yape:.2f}) + Efectivo (S/ {efectivo:.2f}) debe sumar el Total a pagar (S/ {neto:.2f}).")
    desde = dl.normalizar_fecha_dmy(datos.get("desde"))
    hasta = dl.normalizar_fecha_dmy(datos.get("hasta"))
    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
    gastos_creados = 0
    registrar_en_gastos = datos.get("registrarGastos", True)
    if registrar_en_gastos:
        periodo = f"{desde} al {hasta}"
        for monto, metodo in ((yape, "Yape"), (efectivo, "Efectivo")):
            if monto > 0:
                dl.guardar_gasto_local({
                    "fecha": dl.hoy_iso(), "descripcion": f"Pago sueldo {cfg['empleado']} ({periodo}) - {metodo}", "monto": monto,
                    "categoria": "Sueldos y Salarios", "metodo": metodo, "responsable": "Admin",
                    "personal": cfg["categoria_tienda"],
                })
                gastos_creados += 1
    liq_uuid = str(uuid.uuid4())
    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO horario_liquidaciones (liq_uuid, empleado, desde_dmy, hasta_dmy, neto, yape, efectivo, detalle_json, created_at, equipo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (liq_uuid, cfg["empleado"], desde, hasta, neto, yape, efectivo,
             json.dumps(datos.get("detalle") or {}, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), dl._nombre_equipo()))
    return {"ok": True, "liqUuid": liq_uuid, "gastosCreados": gastos_creados}
