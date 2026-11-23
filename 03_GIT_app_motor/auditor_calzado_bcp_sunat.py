#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditor de Calzado BCP/Yape <-> SUNAT (Arnés² — Ley 2 / GR-10).
Ejecución exclusiva en Sandbox Ovalo_Mariategui_PRUEBA (GR-05).

Funciones principales:
1. Sincroniza raw JSONs de SUNAT Noviembre 2026 hacia SQLite Sandbox (`sunat_ventas`
   y `sunat_lineas_cache`), corrigiendo el bug de estados `ANULADO (NCE)` (`EB01-6782`,
   `E001-88`).
2. Normaliza timestamps y corrige atribuciones mixtas/multi-link en
   `corroboracion_ingresos_bcp_nov2026.json` preservando al centavo `Meta_BCP = S/ 11,767.10`.
3. Ejecuta el motor de emparejamiento en 4 niveles (1:1, Unificación de Excedentes `Monto > Yape`
   absorbiendo tienda/sueltos, Lotes 3A/3B.1/3B.2/3B.3, y Respaldo Nivel 4 `"Adicional por horas extras"`).
4. Verifica identidades matemáticas exactas con `assert` (< 0.01) y exporta
   `C:\\ovalo_mariategui_prueba\\data\\plan_emparejamiento_noviembre_2026.json`.
"""

import json
import re
import sqlite3
import sys as _sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_MDIR = str(Path(__file__).resolve().parent)
if _MDIR not in _sys.path:
    _sys.path.insert(0, _MDIR)

from config import DB_PATH as SANDBOX_DB, DATA_DIR

# === Rutas Dinámicas (100% dentro de 02_DB_sqlite_local con fallback solo lectura) ===
SANDBOX_DIR = Path(__file__).resolve().parent
CORR_JSON = DATA_DIR / "corroboracion_ingresos_bcp_nov2026.json"
OUT_PLAN_JSON = DATA_DIR / "plan_emparejamiento_noviembre_2026.json"

SUNAT_DIR = DATA_DIR / "datos_sunat"
if not SUNAT_DIR.exists() and Path(r"C:\Hostal_Sistema\datos_sunat").exists():
    SUNAT_DIR = Path(r"C:\Hostal_Sistema\datos_sunat")
if not CORR_JSON.exists() and (SUNAT_DIR / CORR_JSON.name).exists():
    try:
        import shutil as _sh
        CORR_JSON.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(SUNAT_DIR / CORR_JSON.name, CORR_JSON)
    except Exception:
        pass
RAW_FAC_JSON = SUNAT_DIR / "sunat_facturas_noviembre_raw.json"
RAW_BOL_JSON = SUNAT_DIR / "sunat_boletas_noviembre_raw.json"
RAW_NC_JSON = SUNAT_DIR / "sunat_notas_credito_noviembre_raw.json"
LINEAS_NOV_JSON = SUNAT_DIR / "sunat_nov_lineas.json"

FACTOR_IGV = 1.105  # Ley 31556 / IGV hotelero 10.5%


def norm_cp(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").upper())


def norm_ts(s: str) -> str:
    s = str(s or "").strip()
    m = re.match(r"^0*(\d{1,2})/0*(\d{1,2})/(\d{4})\s+0*(\d{1,2}):(\d{2}):(\d{2})$", s)
    if m:
        d, mo, y, h, mi, se = m.groups()
        return f"{int(d)}/{int(mo)}/{y} {int(h)}:{mi}:{se}"
    return s


def parse_dmy(s: str):
    return datetime.strptime(s.strip(), "%d/%m/%Y")


def parse_dt_flexible(s: str):
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def is_anulado(estado: str) -> bool:
    return "ANULADO" in str(estado or "").upper()


def r2(val: float) -> float:
    return round(float(val or 0.0), 2)


def valor_sin_igv(total: float) -> float:
    return round(float(total) / FACTOR_IGV, 4)


def extraer_resumen_items(doc: dict) -> str:
    partes = []
    for it in doc.get("items") or []:
        tc = str(it.get("texto_completo") or "").strip()
        if not tc:
            continue
        first_line = tc.split("\n")[0]
        if "EL OVALO MARIATEGUI" in tc or first_line.startswith("Cantidad\t"):
            continue
        clean = re.sub(r"\s+", " ", tc).strip()
        if clean and clean not in partes:
            partes.append(clean)
    return " | ".join(partes)[:600]


def sincronizar_sunat_nov_sqlite(db_path: Path):
    """Sincroniza raw Facturas/Boletas/NCs y lineas de Nov 2026 a SQLite Sandbox."""
    with open(RAW_FAC_JSON, "r", encoding="utf-8") as f:
        raw_fac = json.load(f).get("facturas", [])
    with open(RAW_BOL_JSON, "r", encoding="utf-8") as f:
        raw_bol = json.load(f).get("boletas", [])
    with open(RAW_NC_JSON, "r", encoding="utf-8") as f:
        raw_nc = json.load(f).get("notas_credito", [])
    with open(LINEAS_NOV_JSON, "r", encoding="utf-8") as f:
        lineas_nov = json.load(f)

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    docs_canon = {}
    ventas_rows = []

    def push_doc(doc: dict, tipo_default: str):
        cp = norm_cp(doc.get("numero_cp"))
        tipo = str(doc.get("tipo") or tipo_default).upper()
        estado = str(doc.get("anulado") or "VIGENTE").strip() or "VIGENTE"
        monto = r2(doc.get("monto_total") or 0)
        entry = {
            "nro_cp": cp,
            "tipo": tipo,
            "periodo_mes": "2026-11",
            "fecha_emision": str(doc.get("fecha_emision") or "").strip(),
            "receptor_doc": str(doc.get("receptor_doc") or "").strip(),
            "receptor_nombre": str(doc.get("receptor_nombre") or "").strip(),
            "monto_total": monto,
            "estado": estado,
            "resumen_items": extraer_resumen_items(doc),
            "actualizado_en": ahora,
            "raw": doc,
        }
        docs_canon[cp] = entry
        ventas_rows.append(entry)

    for x in raw_fac:
        push_doc(x, "FACTURA")
    for x in raw_bol:
        push_doc(x, "BOLETA")
    for x in raw_nc:
        push_doc(x, "NOTA_CREDITO")

    con = sqlite3.connect(str(db_path), timeout=30)
    cur = con.cursor()
    cur.execute("DELETE FROM sunat_ventas WHERE periodo_mes = '2026-11'")
    for r in ventas_rows:
        cur.execute(
            """
            INSERT OR REPLACE INTO sunat_ventas
            (nro_cp, tipo, periodo_mes, fecha_emision, receptor_doc, receptor_nombre,
             monto_total, estado, resumen_items, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["nro_cp"],
                r["tipo"],
                r["periodo_mes"],
                r["fecha_emision"],
                r["receptor_doc"],
                r["receptor_nombre"],
                r["monto_total"],
                r["estado"],
                r["resumen_items"],
                r["actualizado_en"],
            ),
        )

    # Limpia líneas previas de Nov 2026 en sunat_lineas_cache e inserta limpias corrigiendo EB01-6782
    all_nov_cps = tuple(docs_canon.keys())
    if all_nov_cps:
        placeholders = ",".join("?" for _ in all_nov_cps)
        cur.execute(
            f"DELETE FROM sunat_lineas_cache WHERE fecha_emision LIKE '%/11/2026' OR nro_cp IN ({placeholders})",
            all_nov_cps,
        )
    else:
        cur.execute("DELETE FROM sunat_lineas_cache WHERE fecha_emision LIKE '%/11/2026'")

    inserted_cps = set()
    for ln in lineas_nov:
        cp = norm_cp(ln.get("nro_comprobante"))
        doc_ref = docs_canon.get(cp)
        estado_real = doc_ref["estado"] if doc_ref else str(ln.get("estado") or "VIGENTE")
        tipo_real = doc_ref["tipo"] if doc_ref else str(ln.get("tipo") or "BOLETA")
        if tipo_real.startswith("NC") or tipo_real == "NOTA_CREDITO":
            tipo_real = "NOTA_CREDITO"
        cur.execute(
            """
            INSERT INTO sunat_lineas_cache
            (nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp, cliente, doc,
             fecha_emision, estado, descripcion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cp,
                tipo_real,
                str(ln.get("noche") or ""),
                str(ln.get("habitacion") or ""),
                r2(ln.get("monto_linea") or 0),
                r2(ln.get("total_comprobante") or 0),
                str(ln.get("cliente") or ""),
                str(ln.get("doc") or ""),
                str(ln.get("fecha_emision") or ""),
                estado_real,
                str(ln.get("texto_linea") or ""),
            ),
        )
        inserted_cps.add(cp)

    # Asegura que NCs o documentos crudos que no generaron línea en sunat_nov_lineas también queden reflejados si faltan
    for cp, entry in docs_canon.items():
        if cp not in inserted_cps and entry["tipo"] == "NOTA_CREDITO":
            cur.execute(
                """
                INSERT INTO sunat_lineas_cache
                (nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp, cliente, doc,
                 fecha_emision, estado, descripcion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cp,
                    "NOTA_CREDITO",
                    entry["fecha_emision"],
                    "",
                    -entry["monto_total"],
                    -entry["monto_total"],
                    entry["receptor_nombre"],
                    entry["receptor_doc"],
                    entry["fecha_emision"],
                    entry["estado"],
                    entry["resumen_items"],
                ),
            )

    con.commit()
    con.close()
    return docs_canon, raw_fac, raw_bol, raw_nc, lineas_nov


def cargar_bd_sandbox(db_path: Path):
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    est_all = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM estancias WHERE ymd_in >= 20261001 OR ymd_out >= 20261101 ORDER BY ymd_in, hab"
        ).fetchall()
    ]
    pag_nov = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM pagos WHERE fecha_iso LIKE '%/11/2026%' ORDER BY fecha_iso"
        ).fetchall()
    ]
    vt_nov = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM ventas_tienda WHERE fecha_dmy LIKE '%/11/2026%' ORDER BY fecha_dmy, fecha_iso"
        ).fetchall()
    ]
    con.close()
    return est_all, pag_nov, vt_nov


def atribuir_ingresos_bancarios(corr: dict, est_by_idstr: dict, est_by_uuid: dict):
    """Atribuye los 149 ingresos reales (S/ 11,767.10) a estancias y tienda pura."""
    clas = corr["clasificacion_detallada"]
    hab_ops = clas["habitaciones"]
    tie_ops = clas["tienda"]
    corp_ops = clas["corporativo_maelen"]
    sue_ops = clas["sueltos_banco"]

    est_bank_amt = defaultdict(float)
    est_bank_ops = defaultdict(list)
    matched_pago_ids = set()
    corrections_applied = []

    for op in hab_ops:
        op_id = op["id"]
        m_op = r2(op["monto"])
        links = op.get("habitaciones_vinculadas") or []

        # Corrección 3: op 406 PLIN - JUAN CARLOS RAMOS S/ 83.80 -> 100% 13/11 H206 Ramos Mamani (55 + 28.80)
        if op_id == 406 or (abs(m_op - 83.80) < 0.01 and "JUAN CARLOS RAMOS" in op["remitente"].upper()):
            for l in links:
                if l.get("pago_id") and "RAMOS" in str(l.get("huesped") or "").upper():
                    matched_pago_ids.add(l["pago_id"])
            target = est_by_idstr.get(norm_ts("13/11/2026 17:48:53"))
            assert target is not None, "No se encontró estancia 13/11/2026 17:48:53 para op 406"
            est_bank_amt[target["estancia_uuid"]] += m_op
            est_bank_ops[target["estancia_uuid"]].append(
                {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": m_op, "remitente": op["remitente"], "canal": "YAPE_CORP_MIX"}
            )
            corrections_applied.append(
                f"op {op_id} ({op['fecha']} {op['remitente']} S/ {m_op:.2f}): 100% asignado a 13/11 H206 JUAN CARLOS RAMOS MAMANI (55.00 + 28.80 Servicio por Comprobante), desacoplando a Esteban Asencio."
            )
            continue

        # Corrección 4: op 408 PLIN - ESTEBAN ASENCIO S/ 55.00 -> 14/11 H206 ESTEBAN ASENCIO GALDOS
        if op_id == 408 or (abs(m_op - 55.0) < 0.01 and "ESTEBAN ASENCIO" in op["remitente"].upper()):
            target = est_by_idstr.get(norm_ts("14/11/2026 13:28:24"))
            assert target is not None, "No se encontró estancia 14/11/2026 13:28:24 para op 408"
            est_bank_amt[target["estancia_uuid"]] += m_op
            est_bank_ops[target["estancia_uuid"]].append(
                {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": m_op, "remitente": op["remitente"], "canal": "YAPE_CORREGIDO"}
            )
            corrections_applied.append(
                f"op {op_id} ({op['fecha']} {op['remitente']} S/ {m_op:.2f}): reasignado a 14/11 H206 ESTEBAN ASENCIO GALDOS (enlazado erróneamente a Oblitas Tito 15/11 post-corte)."
            )
            continue

        for l in links:
            if l.get("pago_id"):
                matched_pago_ids.add(l["pago_id"])

        seen_uuids = {}
        for l in links:
            e = est_by_idstr.get(norm_ts(l.get("id_estancia")))
            if e:
                seen_uuids[e["estancia_uuid"]] = e

        assert len(seen_uuids) >= 1, f"Operación habitación sin estancia SQLite: {op}"
        if len(seen_uuids) == 1:
            u = next(iter(seen_uuids))
            est_bank_amt[u] += m_op
            est_bank_ops[u].append(
                {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": m_op, "remitente": op["remitente"], "canal": "YAPE_1A1"}
            )
        else:
            # Multi-stay legítimos (Lucero 02/11 = 47+93, Luis Mor* 08/11 = 85+85, Cieza 10/11 = 65+65)
            weights = {}
            for u, e in seen_uuids.items():
                ey = r2((e.get("pago_ini_yape") or 0) + (e.get("pago_post_yape") or 0))
                weights[u] = ey
            sum_w = r2(sum(weights.values()))
            assert abs(sum_w - m_op) < 0.05, f"Multi-stay suma {sum_w} != {m_op} en op {op_id}"
            for u, part in weights.items():
                est_bank_amt[u] += part
                est_bank_ops[u].append(
                    {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": part, "remitente": op["remitente"], "canal": "YAPE_MULTI_ESTANCIA"}
                )

    for op in corp_ops:
        for l in op.get("habitaciones_vinculadas") or []:
            if l.get("pago_id"):
                matched_pago_ids.add(l["pago_id"])
            e = est_by_idstr.get(norm_ts(l.get("id_estancia")))
            tc = r2(l.get("total_cuenta") or 0)
            assert e is not None, f"Corp link sin estancia: {l}"
            est_bank_amt[e["estancia_uuid"]] += tc
            est_bank_ops[e["estancia_uuid"]].append(
                {"op_id": op["msg_id"], "fecha": op["fecha"], "hora": op["hora"], "monto": tc, "remitente": op["remitente"], "canal": "BCP_MAELEN_CORP"}
            )

    # Procesa TIENDA (33 ops = S/ 308.00) separando los 2 pagos mixtos Hab + Tienda (T03=75+3, T17=65+7)
    tienda_pura_items = []
    for idx, op in enumerate(tie_ops, 1):
        op_id = op["id"]
        m_op = r2(op["monto"])
        vts = op.get("ventas_vinculadas") or []
        for v in vts:
            if v.get("venta_uuid") and "-" in str(v["venta_uuid"]) and not str(v["venta_uuid"]).startswith("SH-"):
                matched_pago_ids.add(v["venta_uuid"])

        # Deduplicación de productos listados
        uniq_prods = []
        seen_k = set()
        for v in vts:
            prod = str(v.get("producto") or "").strip()
            sub = r2(v.get("subtotal") or 0)
            if not prod or sub <= 0:
                continue
            k = (v.get("venta_uuid") or prod, sub)
            # Evitar doble fila por uuid local + SH-T
            sig = (prod.lower(), int(v.get("cantidad") or 1), sub)
            if sig not in seen_k:
                seen_k.add(sig)
                uniq_prods.append({"producto": prod, "cantidad": int(v.get("cantidad") or 1), "subtotal": sub, "hab": str(v.get("hab") or "")})

        # Corrección 1: op 48 / T03 (01/11 LUIS JUL* S/ 78.00 -> S/ 75 Hab 305 EB01-6802 + S/ 3 Agua 1L)
        if op_id == 48 or (abs(m_op - 78.0) < 0.01 and "LUIS JUL" in op["remitente"].upper()):
            hab_target = est_by_idstr.get(norm_ts("01/11/2026 19:47:01"))
            if not hab_target:
                for e in est_by_uuid.values():
                    if e.get("nro_comp") == "EB01-6802":
                        hab_target = e
                        break
            assert hab_target is not None, "No se encontró estancia para EB01-6802 (op 48)"
            est_bank_amt[hab_target["estancia_uuid"]] += 75.0
            est_bank_ops[hab_target["estancia_uuid"]].append(
                {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": 75.0, "remitente": op["remitente"], "canal": "YAPE_MIX_HAB_TIENDA"}
            )
            tienda_pura_items.append({
                "id": f"T-{op_id}",
                "op_id": op_id,
                "fecha": op["fecha"],
                "hora": op["hora"],
                "remitente": op["remitente"],
                "monto": 3.0,
                "monto_original_op": m_op,
                "tipo_item": "TIENDA_PURA_DESGLOSE_HAB",
                "hab_ref": "305",
                "detalle": "1x Agua 1L (desglose de Yape S/ 78.00 donde S/ 75.00 cubren Hab 305 EB01-6802)",
            })
            corrections_applied.append(
                f"op {op_id} / T03 (01/11 LUIS JUL* S/ 78.00): separado S/ 75.00 Hab 305 (EB01-6802 ya emitida) + S/ 3.00 Tienda pura."
            )
            continue

        # Corrección 2: op 120 / T17 (08/11 BCP - CIEZA DIAZ S/ 72.00 -> S/ 65 Hab 104 EB01-6845 + S/ 7 Tienda)
        if op_id == 120 or (abs(m_op - 72.0) < 0.01 and "CIEZA" in op["remitente"].upper()):
            hab_target = est_by_idstr.get(norm_ts("08/11/2026 16:55:25"))
            if not hab_target:
                for e in est_by_uuid.values():
                    if e.get("nro_comp") == "EB01-6845":
                        hab_target = e
                        break
            assert hab_target is not None, "No se encontró estancia para EB01-6845 (op 120)"
            est_bank_amt[hab_target["estancia_uuid"]] += 65.0
            est_bank_ops[hab_target["estancia_uuid"]].append(
                {"op_id": op_id, "fecha": op["fecha"], "hora": op["hora"], "monto": 65.0, "remitente": op["remitente"], "canal": "YAPE_MIX_HAB_TIENDA"}
            )
            tienda_pura_items.append({
                "id": f"T-{op_id}",
                "op_id": op_id,
                "fecha": op["fecha"],
                "hora": op["hora"],
                "remitente": op["remitente"],
                "monto": 7.0,
                "monto_original_op": m_op,
                "tipo_item": "TIENDA_PURA_DESGLOSE_HAB",
                "hab_ref": "104",
                "detalle": "1x Agua 1L + 1x Coca Cola y Gatorade (desglose de Yape S/ 72.00 donde S/ 65.00 cubren Hab 104 EB01-6845)",
            })
            corrections_applied.append(
                f"op {op_id} / T17 (08/11 BCP - CIEZA DIAZ S/ 72.00): separado S/ 65.00 Hab 104 (EB01-6845 ya emitida) + S/ 7.00 Tienda pura."
            )
            continue

        desc_prod = ", ".join(f"{p['cantidad']}x {p['producto']}" for p in uniq_prods) or str(op.get("observacion") or "Venta Tienda")
        hab_ref = next((p["hab"] for p in uniq_prods if p["hab"]), "")
        tienda_pura_items.append({
            "id": f"T-{op_id}",
            "op_id": op_id,
            "fecha": op["fecha"],
            "hora": op["hora"],
            "remitente": op["remitente"],
            "monto": m_op,
            "monto_original_op": m_op,
            "tipo_item": "TIENDA_PURA",
            "hab_ref": hab_ref,
            "detalle": desc_prod,
        })

    sueltos_items = []
    for op in sue_ops:
        sueltos_items.append({
            "id": f"S-{op['id']}",
            "op_id": op["id"],
            "fecha": op["fecha"],
            "hora": op["hora"],
            "remitente": op["remitente"],
            "monto": r2(op["monto"]),
            "monto_original_op": r2(op["monto"]),
            "tipo_item": "SUELTO_BANCO",
            "hab_ref": "",
            "detalle": f"Sueltos/Sobrante banco ({op.get('nro_operacion') or ''})",
        })

    return est_bank_amt, est_bank_ops, tienda_pura_items, sueltos_items, matched_pago_ids, corrections_applied


def vincular_sunat_y_estancias(docs_canon: dict, lineas_nov: list, est_all: list, est_bank_amt: dict):
    """Vincula comprobantes vigentes de SUNAT Nov 2026 con estancias y detecta los 8 sin anotar en BD."""
    cp_to_est = defaultdict(list)
    for e in est_all:
        for m in re.findall(r"(EB01\s*-\s*\d+|E001\s*-\s*\d+)", str(e.get("nro_comp") or "").upper()):
            cp_to_est[norm_cp(m)].append(e)

    cp_lineas = defaultdict(list)
    for ln in lineas_nov:
        cp_lineas[norm_cp(ln.get("nro_comprobante"))].append(ln)

    est_by_uuid = {e["estancia_uuid"]: e for e in est_all}
    # Vinculación explícita verificada para los 8 CPE emitidos en SUNAT con nro_comp vacío en BD
    fallback_lookup = {
        "E001-2108": [e for e in est_all if e.get("ruc") == "20250975563" and e["ymd_in"] in (20261104, 20261105) and e["hab"] in ("102", "206")],
        "E001-2109": [e for e in est_all if e.get("ruc") == "20250975563" and e["ymd_in"] in (20261104, 20261105) and e["hab"] == "307"],
        "E001-2110": [e for e in est_all if e["estancia_uuid"].startswith("aa5cb16d")],
        "E001-2111": [e for e in est_all if e["estancia_uuid"].startswith("b21b23e1")],
        "E001-2112": [e for e in est_all if e["estancia_uuid"].startswith("76537f03")],
        "E001-2113": [e for e in est_all if e["ymd_in"] == 20261108 and e.get("ruc") == "20603511043"],
        "E001-2118": [e for e in est_all if e["estancia_uuid"].startswith("147c9a9a")],
        "EB01-6860": [e for e in est_all if e["estancia_uuid"].startswith("743bd21a")],
    }

    sin_anotar_bd = []
    est_to_cps = defaultdict(list)
    nivel1_calzado = []
    nivel2a_excedentes = []
    oct_noches_en_nov = []
    cps_con_banco_cubierto = {}

    for cp, doc in sorted(docs_canon.items()):
        if doc["tipo"] == "NOTA_CREDITO" or is_anulado(doc["estado"]):
            continue
        monto = doc["monto_total"]
        fe = doc["fecha_emision"]
        cli = doc["receptor_nombre"]
        lns = cp_lineas.get(cp, [])
        meses = {ln.get("mes") for ln in lns}
        txt_all = doc["resumen_items"].upper()
        # Nota: E001-2110 dice "5 DE OCTUBRE" por error de tipeo en SUNAT pero corresponde al 05/11 H207 Farmacias Peruanas
        is_oct = (meses == {"10"}) or ("OCTUBRE" in txt_all and "NOVIEMBRE" not in txt_all and cp != "E001-2110")

        linked = list(cp_to_est.get(cp, []))
        via_fallback = False
        if not linked and cp in fallback_lookup and fallback_lookup[cp]:
            linked = fallback_lookup[cp]
            via_fallback = True
            sin_anotar_bd.append({
                "nro_cp": cp,
                "tipo": doc["tipo"],
                "fecha_emision": fe,
                "monto_sunat": monto,
                "cliente": cli,
                "ruc_dni": doc["receptor_doc"],
                "estancias_bd": [
                    {
                        "estancia_uuid": e["estancia_uuid"],
                        "fecha_in": e["fecha_in_dmy"],
                        "hab": e["hab"],
                        "huesped": e["huesped"],
                        "estado_comp_bd": e["estado_comp"],
                        "nro_comp_bd": e["nro_comp"] or "(VACÍO)",
                    }
                    for e in linked
                ],
            })

        by_sum = r2(sum(est_bank_amt.get(e["estancia_uuid"], 0.0) for e in linked))
        for e in linked:
            est_to_cps[e["estancia_uuid"]].append({"nro_cp": cp, "tipo": doc["tipo"], "monto_cp": monto})

        if is_oct:
            oct_noches_en_nov.append({
                "nro_cp": cp,
                "tipo": doc["tipo"],
                "fecha_emision": fe,
                "monto_sunat": monto,
                "bank_nov_matched": by_sum,
                "monto_sin_ingreso_nov": r2(monto - by_sum),
                "cliente": cli,
                "stays_31oct_01nov": [(e["fecha_in_dmy"], e["hab"], e["huesped"]) for e in linked],
            })
            if by_sum > 0:
                cps_con_banco_cubierto[cp] = by_sum
        else:
            diff = r2(monto - by_sum)
            if by_sum > 0:
                cps_con_banco_cubierto[cp] = by_sum
            if abs(diff) <= 0.01:
                nivel1_calzado.append({
                    "nro_cp": cp,
                    "tipo": doc["tipo"],
                    "fecha_emision": fe,
                    "monto_sunat": monto,
                    "bank_matched": by_sum,
                    "cliente": cli,
                    "vinculo": "BD_FALLBACK" if via_fallback else "BD_EXPLICITO",
                })
            elif diff > 0.01:
                ef_sum = r2(sum((e.get("pago_ini_efec") or 0) + (e.get("pago_post_efec") or 0) for e in linked))
                origen = "MIXTO_YAPE_RECARGO" if by_sum > 0 else ("EFECTIVO" if ef_sum > 0 else "CREDITO_CORPORATIVO")
                nivel2a_excedentes.append({
                    "id_pool": f"2A-{cp}",
                    "nivel": "2A_YA_EMITIDO",
                    "nro_cp": cp,
                    "tipo": doc["tipo"],
                    "fecha_emision": fe,
                    "fecha_ref_estancia": linked[0]["fecha_in_dmy"] if linked else fe,
                    "cliente": cli,
                    "ruc_dni": doc["receptor_doc"],
                    "monto_sunat": monto,
                    "bank_matched": by_sum,
                    "efectivo_bd": ef_sum,
                    "excedente_total": diff,
                    "excedente_disponible": diff,
                    "origen": origen,
                    "consumido_por": [],
                })

    return {
        "sin_anotar_bd": sin_anotar_bd,
        "est_to_cps": est_to_cps,
        "nivel1_calzado": nivel1_calzado,
        "nivel2a_excedentes": nivel2a_excedentes,
        "oct_noches_en_nov": oct_noches_en_nov,
    }


def detectar_cola_viva_post_corte(corr: dict, pag_nov: list, est_by_idstr: dict, matched_pago_ids: set):
    """Detecta los pagos Yape registrados en SQLite después del último Excel de Drive (14/11 13:23:34)."""
    cutoff_dt = datetime.strptime("14/11/2026 13:23:34", "%d/%m/%Y %H:%M:%S")
    # También verificar cualquier op viva del 15/11 ya incluida en corr (Belinda 50.00 op 436)
    live_in_corr = set()
    for op in corr["clasificacion_detallada"]["habitaciones"]:
        if "VIVO" in str(op.get("observacion") or "").upper() or op.get("fecha") == "15/11/2026":
            for l in op.get("habitaciones_vinculadas") or []:
                live_in_corr.add((norm_ts(l.get("id_estancia")), r2(op["monto"])))
        if op["id"] in (405, 407):
            for l in op.get("habitaciones_vinculadas") or []:
                live_in_corr.add((norm_ts(l.get("id_estancia")), r2(op["monto"])))
        elif op["id"] == 406:
            live_in_corr.add((norm_ts("13/11/2026 17:48:53"), 55.0))
            live_in_corr.add((norm_ts("13/11/2026 17:48:53"), 28.8))
        elif op["id"] == 408:
            live_in_corr.add((norm_ts("14/11/2026 13:28:24"), 55.0))

    cola_viva = []
    for p in pag_nov:
        if str(p.get("medio_pago") or "").strip().upper() != "YAPE":
            continue
        pid = p.get("pago_id")
        if pid in matched_pago_ids:
            continue
        dt_p = parse_dt_flexible(p.get("fecha_iso") or "")
        if not dt_p or dt_p <= cutoff_dt:
            continue
        id_s = norm_ts(p.get("id_estancia"))
        m = r2(p.get("monto") or 0)
        if (id_s, m) in live_in_corr:
            continue
        e = est_by_idstr.get(id_s)
        cola_viva.append({
            "pago_id": pid,
            "fecha_pago": p.get("fecha_iso"),
            "fecha_in": e["fecha_in_dmy"] if e else p.get("fecha_iso")[:10],
            "hab": e["hab"] if e else p.get("hab"),
            "huesped": e["huesped"] if e else p.get("huesped"),
            "dni": (e.get("dni") or "") if e else "",
            "monto": m,
            "nro_operacion": p.get("nro_operacion") or "",
            "solicita_comp": (e.get("solicita_comp") or "") if e else "",
            "tipo_comp": (e.get("tipo_comp") or "Boleta") if e else "Boleta",
            "valor_unitario_sin_igv": valor_sin_igv(m),
        })
    return cola_viva


def construir_lotes_pendientes(est_bank_amt: dict, est_bank_ops: dict, est_by_uuid: dict, est_to_cps: dict, est_all: list):
    """Clasifica los S/ 2,308.80 de estancias con banco sin CPE en Lote 3A, Lote 3B.1, Lote 3B.2 y pools Nivel 2B."""
    unbilled_bank_uuids = [u for u, amt in est_bank_amt.items() if amt > 0 and u not in est_to_cps]

    # Agrupación especial EVENTISIMO AGENCIA (RUC 20558597870, Tatiana Cano Soto 08-11/11)
    eventisimo_uuids = []
    lote_3a_facturas_yape = []
    lote_3b1_boletas_sol = []
    lote_3b2_boletas_nosol = []
    nivel2b_pools = []

    for u in sorted(unbilled_bank_uuids, key=lambda x: (est_by_uuid[x].get("ymd_in") or 0, str(est_by_uuid[x].get("hab") or ""))):
        e = est_by_uuid[u]
        ruc = str(e.get("ruc") or "").strip()
        tipo_comp = str(e.get("tipo_comp") or "").strip().upper()
        sol = str(e.get("solicita_comp") or "").strip().upper()
        bank_y = r2(est_bank_amt[u])
        ef = r2((e.get("pago_ini_efec") or 0) + (e.get("pago_post_efec") or 0))
        tot_cta = r2((e.get("noches") or 1) * (e.get("tarifa") or 0) + (e.get("adic_snacks") or 0) + (e.get("adic_servicios") or 0) + (e.get("adic_comp") or 0))
        ops = est_bank_ops[u]

        # Agrupar EVENTISIMO para consolidar sus 4 noches (2 con Yape + 2 en efectivo)
        if ruc == "20558597870" or "EVENTISIMO" in str(e.get("empresa") or "").upper():
            eventisimo_uuids.append(u)
            continue

        if tipo_comp == "FACTURA" or (ruc and len(ruc) == 11):
            mfac_db = r2(e.get("monto_factura") or 0)
            sugerido = mfac_db if mfac_db >= bank_y else bank_y
            item = {
                "estancia_uuid": u,
                "fecha_in": e["fecha_in_dmy"],
                "fecha_out": e["fecha_out_dmy"],
                "hab": e["hab"],
                "huesped": e["huesped"],
                "dni": str(e.get("dni") or ""),
                "ruc": ruc,
                "empresa": str(e.get("empresa") or "").strip(),
                "yape_banco": bank_y,
                "efectivo_bd": ef,
                "total_cuenta_bd": tot_cta,
                "monto_sugerido": sugerido,
                "excedente_sobre_yape": r2(sugerido - bank_y),
                "valor_unitario_sin_igv": valor_sin_igv(sugerido),
                "glosa_sunat": f"DIA DE ALOJAMIENTO DEL {e['fecha_in_dmy'][:2]} DE NOVIEMBRE, HAB N {e['hab']}",
                "ops_banco": ops,
                "observacion": "MAN PAN 13/11 incluye S/ 28.80 Servicio por Comprobante (Yape S/ 83.80); verificar si se factura aparte o en liquidación 10-13/11." if ruc == "20250975563" else "",
            }
            lote_3a_facturas_yape.append(item)
            continue

        # Boletas
        if sol in ("SI", "SÍ") or tipo_comp == "BOLETA":
            # Caso mixto Efectivo + Yape donde emiten por pagado total / total cuenta > Yape
            sugerido = bank_y
            excedente = 0.0
            nota = "Yape exacto"
            if ef > 0:
                if u == "90031021-":  # handled safely via prefix below
                    pass
                if e["fecha_in_dmy"] == "02/11/2026" and "LUCERO" in e["huesped"].upper() and e["hab"] == "306":
                    sugerido = r2(bank_y + ef)  # 93 + 10 = 103
                    excedente = ef
                    nota = "Mixto Lucero 02/11 (Yape S/ 93 + Efec S/ 10)"
                elif e["fecha_in_dmy"] == "04/11/2026" and "LUCERO" in e["huesped"].upper() and e["hab"] == "206":
                    sugerido = r2(bank_y + ef)  # 150 + 126 = 276
                    excedente = ef
                    nota = "Mixto Lucero 04/11 (Yape S/ 150 + Efec S/ 126)"
                elif e["fecha_in_dmy"] == "04/11/2026" and e["hab"] == "302" and "SUSAN" in e["huesped"].upper():
                    sugerido = r2(bank_y + ef)  # 25 + 50 = 75
                    excedente = ef
                    nota = "Mixto Susan Quispe 04/11 (Yape S/ 25 + Efec S/ 50)"
                elif e["fecha_in_dmy"] == "10/11/2026" and e["hab"] == "104" and "CIEZA" in e["huesped"].upper():
                    sugerido = r2(bank_y + ef)  # 65 + 20 = 85
                    excedente = ef
                    nota = "Mixto Cieza 10/11 (Yape S/ 65 + Efec S/ 20)"
                elif e["fecha_in_dmy"] == "11/11/2026" and e["hab"] == "204" and "JOAQUIN" in e["huesped"].upper():
                    sugerido = r2(bank_y + ef)  # 85 + 15 = 100
                    excedente = ef
                    nota = "Mixto Joaquín Leyva 11/11 (Yape S/ 85 + Efec S/ 15)"

            item = {
                "estancia_uuid": u,
                "fecha_in": e["fecha_in_dmy"],
                "fecha_out": e["fecha_out_dmy"],
                "hab": e["hab"],
                "huesped": e["huesped"],
                "dni": str(e.get("dni") or "").strip(),
                "yape_banco": bank_y,
                "efectivo_bd": ef,
                "total_cuenta_bd": tot_cta,
                "monto_sugerido": sugerido,
                "excedente_sobre_yape": excedente,
                "valor_unitario_sin_igv": valor_sin_igv(sugerido),
                "glosa_sunat": f"DIA DE ALOJAMIENTO DEL {e['fecha_in_dmy'][:2]} DE NOVIEMBRE, HAB N {e['hab']}",
                "ops_banco": ops,
                "nota": nota,
            }
            lote_3b1_boletas_sol.append(item)
            if excedente > 0.01:
                nivel2b_pools.append({
                    "id_pool": f"2B-{e['fecha_in_dmy'][:5]}-H{e['hab']}",
                    "nivel": "2B_POR_EMITIR_MIXTO",
                    "nro_cp": f"PENDIENTE_BOL_H{e['hab']}",
                    "tipo": "BOLETA",
                    "fecha_ref_estancia": e["fecha_in_dmy"],
                    "cliente": e["huesped"],
                    "ruc_dni": str(e.get("dni") or ""),
                    "monto_sugerido": sugerido,
                    "bank_matched": bank_y,
                    "efectivo_bd": ef,
                    "excedente_total": excedente,
                    "excedente_disponible": excedente,
                    "origen": "MIXTO_EFECTIVO_YAPE",
                    "consumido_por": [],
                })
        else:
            # No solicitó comprobante (sol No / Ninguno), pero tiene Yape en extracto
            item = {
                "estancia_uuid": u,
                "fecha_in": e["fecha_in_dmy"],
                "fecha_out": e["fecha_out_dmy"],
                "hab": e["hab"],
                "huesped": e["huesped"],
                "dni": str(e.get("dni") or "").strip(),
                "yape_banco": bank_y,
                "efectivo_bd": ef,
                "solicita_comp": e.get("solicita_comp") or "",
                "tipo_comp": e.get("tipo_comp") or "",
                "monto_sugerido": bank_y,
                "valor_unitario_sin_igv": valor_sin_igv(bank_y),
                "glosa_sunat": f"DIA DE ALOJAMIENTO DEL {e['fecha_in_dmy'][:2]} DE NOVIEMBRE, HAB N {e['hab']}",
                "ops_banco": ops,
            }
            lote_3b2_boletas_nosol.append(item)

    # Consolidado EVENTISIMO AGENCIA (08-11/11 todas sus estancias)
    ev_stays = [
        e for e in est_all
        if (e.get("ymd_in") or 0) >= 20261101
        and (str(e.get("ruc") or "").strip() == "20558597870" or "EVENTISIMO" in str(e.get("empresa") or "").upper())
    ]
    if ev_stays:
        ev_yape = r2(sum(est_bank_amt.get(e["estancia_uuid"], 0.0) for e in ev_stays))
        ev_efec = r2(sum((e.get("pago_ini_efec") or 0) + (e.get("pago_post_efec") or 0) for e in ev_stays))
        ev_tot_cta = r2(sum((e.get("noches") or 1)*(e.get("tarifa") or 0) + (e.get("adic_snacks") or 0) + (e.get("adic_servicios") or 0) + (e.get("adic_comp") or 0) for e in ev_stays))
        ev_pagado = r2(ev_yape + ev_efec)  # 195 + 63 = 258.00
        lote_3a_facturas_yape.append({
            "estancia_uuid": ",".join(e["estancia_uuid"][:8] for e in ev_stays),
            "fecha_in": "08/11/2026 - 11/11/2026",
            "fecha_out": "12/11/2026",
            "hab": "205 / 306",
            "huesped": "TATIANA CANO SOTO",
            "dni": "41608298",
            "ruc": "20558597870",
            "empresa": "EVENTISIMO AGENCIA",
            "yape_banco": ev_yape,
            "efectivo_bd": ev_efec,
            "total_cuenta_bd": ev_tot_cta,
            "monto_sugerido": ev_pagado,
            "monto_alternativo_estadia_completa": ev_tot_cta,
            "excedente_sobre_yape": r2(ev_pagado - ev_yape),
            "excedente_maximo_estadia": r2(ev_tot_cta - ev_yape),
            "valor_unitario_sin_igv": valor_sin_igv(ev_pagado),
            "glosa_sunat": "DIAS DE ALOJAMIENTO DEL 08 AL 12 DE NOVIEMBRE, HABS 205 Y 306",
            "detalle_estancias": [
                {"fecha": e["fecha_in_dmy"], "hab": e["hab"], "tot": (e.get("noches") or 1)*(e.get("tarifa") or 0), "ef": r2((e.get("pago_ini_efec") or 0)+(e.get("pago_post_efec") or 0)), "yape_banco": r2(est_bank_amt.get(e["estancia_uuid"], 0.0))}
                for e in ev_stays
            ],
        })
        nivel2b_pools.append({
            "id_pool": "2B-EVENTISIMO-RUC20558597870",
            "nivel": "2B_POR_EMITIR_MIXTO",
            "nro_cp": "PENDIENTE_FAC_EVENTISIMO",
            "tipo": "FACTURA",
            "fecha_ref_estancia": "10/11/2026",
            "cliente": "EVENTISIMO AGENCIA",
            "ruc_dni": "20558597870",
            "monto_sugerido": ev_pagado,
            "bank_matched": ev_yape,
            "efectivo_bd": ev_efec,
            "excedente_total": r2(ev_pagado - ev_yape),
            "excedente_disponible": r2(ev_pagado - ev_yape),
            "origen": "MIXTO_EFECTIVO_YAPE_CORPORATIVO",
            "consumido_por": [],
        })

    # Facturas pendientes sin Yape en extracto (solo efectivo o crédito por liquidar - informativo)
    facturas_solo_efectivo_credito = []
    covered_or_listed = {x["estancia_uuid"] for x in lote_3a_facturas_yape} | {e["estancia_uuid"] for e in ev_stays}
    for e in est_all:
        if (e.get("ymd_in") or 0) < 20261101:
            continue
        u = e["estancia_uuid"]
        if u in est_to_cps or u in covered_or_listed:
            continue
        if est_bank_amt.get(u, 0.0) > 0:
            continue
        if str(e.get("tipo_comp") or "").strip().upper() == "FACTURA":
            ef = r2((e.get("pago_ini_efec") or 0) + (e.get("pago_post_efec") or 0))
            tot_cta = r2((e.get("noches") or 1)*(e.get("tarifa") or 0) + (e.get("adic_snacks") or 0) + (e.get("adic_servicios") or 0) + (e.get("adic_comp") or 0))
            facturas_solo_efectivo_credito.append({
                "estancia_uuid": u,
                "fecha_in": e["fecha_in_dmy"],
                "hab": e["hab"],
                "huesped": e["huesped"],
                "ruc": str(e.get("ruc") or ""),
                "empresa": str(e.get("empresa") or ""),
                "efectivo_bd": ef,
                "total_cuenta_bd": tot_cta,
                "estado_comp_bd": e.get("estado_comp") or "",
            })

    return {
        "lote_3a_facturas_yape": lote_3a_facturas_yape,
        "lote_3b1_boletas_sol": lote_3b1_boletas_sol,
        "lote_3b2_boletas_nosol": lote_3b2_boletas_nosol,
        "facturas_solo_efectivo_credito": facturas_solo_efectivo_credito,
        "nivel2b_pools": nivel2b_pools,
    }


def absorber_tienda_con_excedentes(tienda_pura: list, sueltos: list, pools_2b: list, pools_2a: list):
    """Asigna S/ 168.00 Tienda pura + S/ 4.80 Sueltos contra excedentes 2B (prioridad) y 2A (±2 días)."""
    items = tienda_pura + sueltos
    all_pools = pools_2b + pools_2a
    asignaciones = []
    remanentes_no_absorbidos = []

    def try_allocate(item: dict, candidate_pools: list, max_days: int):
        d_item = parse_dmy(item["fecha"])
        monto = r2(item["monto"])
        # Ordena por: misma hab/nombre (CIEZA, etc.), luego 2B sobre 2A, luego cercanía en días, luego mayor excedente disponible
        ranked = []
        for p in candidate_pools:
            if p["excedente_disponible"] + 1e-9 < monto:
                continue
            d_pool = parse_dmy(p["fecha_ref_estancia"])
            delta_dias = abs((d_item - d_pool).days)
            if delta_dias > max_days:
                continue
            same_guest = 1 if ("CIEZA" in item["remitente"].upper() and "CIEZA" in p["cliente"].upper()) else 0
            prio_2b = 1 if p["nivel"].startswith("2B") else 0
            ranked.append((-same_guest, -prio_2b, delta_dias, -p["excedente_disponible"], p))
        if not ranked:
            return None
        ranked.sort(key=lambda x: x[:4])
        chosen = ranked[0][4]
        chosen["excedente_disponible"] = r2(chosen["excedente_disponible"] - monto)
        chosen["consumido_por"].append({"id_item": item["id"], "fecha": item["fecha"], "monto": monto, "detalle": item["detalle"]})
        return chosen

    for it in sorted(items, key=lambda x: (parse_dmy(x["fecha"]), -x["monto"])):
        pool = try_allocate(it, all_pools, max_days=2)
        window_used = "+/-2 días"
        if not pool:
            # Ampliar ventana en la quincena si no hubo bolsa en ±2 días (p.ej. 01/11 contra 02/11 o 04/11)
            pool = try_allocate(it, all_pools, max_days=5)
            window_used = "+/-5 días"
        if pool:
            asignaciones.append({
                **it,
                "estado_unificacion": "CUBIERTO_POR_EXCEDENTE",
                "bolsa_id": pool["id_pool"],
                "nivel_bolsa": pool["nivel"],
                "comprobante_o_ref": pool["nro_cp"],
                "cliente_bolsa": pool["cliente"],
                "fecha_bolsa": pool["fecha_ref_estancia"],
                "ventana": window_used,
            })
        else:
            remanentes_no_absorbidos.append(it)

    return asignaciones, remanentes_no_absorbidos


def construir_respaldo_nivel4_horas_extras(tienda_pura: list, sueltos: list):
    """Genera alternativa de agrupación diaria compacta 'ADICIONAL POR HORAS EXTRAS' (IGV 10.5%)."""
    por_dia = defaultdict(list)
    for it in tienda_pura + sueltos:
        por_dia[it["fecha"]].append(it)

    paquetes = []
    for fecha in sorted(por_dia.keys(), key=parse_dmy):
        arr = por_dia[fecha]
        tot_dia = r2(sum(x["monto"] for x in arr))
        dd = fecha[:2]
        paquetes.append({
            "fecha_emision_sugerida": fecha,
            "tipo": "BOLETA_AGRUPADA_NIVEL4",
            "receptor": "CLIENTES VARIOS / CONSUMO DIARIO RECEPCIÓN",
            "monto_total": tot_dia,
            "valor_unitario_sin_igv": valor_sin_igv(tot_dia),
            "igv_10_5": r2(tot_dia - tot_dia / FACTOR_IGV),
            "glosa_sunat": f"DIA DE ALOJAMIENTO DEL {dd} DE NOVIEMBRE - ADICIONAL POR HORAS EXTRAS",
            "cantidad_items": len(arr),
            "items_incluidos": [f"{x['id']} S/{x['monto']:.2f} ({x['detalle']})" for x in arr],
        })
    return paquetes


def ejecutar_auditoria(verbose: bool = True):
    docs_canon, raw_fac, raw_bol, raw_nc, lineas_nov = sincronizar_sunat_nov_sqlite(SANDBOX_DB)
    est_all, pag_nov, vt_nov = cargar_bd_sandbox(SANDBOX_DB)

    with open(CORR_JSON, "r", encoding="utf-8") as f:
        corr = json.load(f)

    est_by_idstr = {norm_ts(e.get("id_timestamp_str")): e for e in est_all if e.get("id_timestamp_str")}
    est_by_uuid = {e["estancia_uuid"]: e for e in est_all}

    est_bank_amt, est_bank_ops, tienda_pura, sueltos, matched_pago_ids, corrections_applied = atribuir_ingresos_bancarios(
        corr, est_by_idstr, est_by_uuid
    )
    vinculos = vincular_sunat_y_estancias(docs_canon, lineas_nov, est_all, est_bank_amt)
    cola_viva = detectar_cola_viva_post_corte(corr, pag_nov, est_by_idstr, matched_pago_ids)
    lotes = construir_lotes_pendientes(est_bank_amt, est_bank_ops, est_by_uuid, vinculos["est_to_cps"], est_all)

    # Identidad matemática 1: Banco Extracto == S/ 11,767.10
    meta_bcp_reportada = r2(corr["totales_generales"]["total_ingresos_bancarios_reales_soles"])
    hab_cubierto_sunat = r2(
        sum(
            amt
            for u, amt in est_bank_amt.items()
            if u in vinculos["est_to_cps"]
        )
    )
    hab_sin_cpe = r2(
        sum(
            amt
            for u, amt in est_bank_amt.items()
            if u not in vinculos["est_to_cps"]
        )
    )
    tienda_pura_sum = r2(sum(x["monto"] for x in tienda_pura))
    sueltos_sum = r2(sum(x["monto"] for x in sueltos))
    total_banco_reconstruido = r2(hab_cubierto_sunat + hab_sin_cpe + tienda_pura_sum + sueltos_sum)

    assert abs(meta_bcp_reportada - 11767.10) < 0.01, f"Meta BCP inesperada: {meta_bcp_reportada}"
    assert abs(total_banco_reconstruido - 11767.10) < 0.01, (
        f"Descuadre Identidad Banco: {total_banco_reconstruido} "
        f"({hab_cubierto_sunat} + {hab_sin_cpe} + {tienda_pura_sum} + {sueltos_sum})"
    )
    assert abs(tienda_pura_sum - 168.00) < 0.01, f"Tienda pura esperada 168.00, obtuvo {tienda_pura_sum}"
    assert abs(sueltos_sum - 4.80) < 0.01, f"Sueltos esperado 4.80, obtuvo {sueltos_sum}"
    assert abs(hab_cubierto_sunat - 9285.50) < 0.01, f"Hab cubierto esperado 9285.50, obtuvo {hab_cubierto_sunat}"
    assert abs(hab_sin_cpe - 2308.80) < 0.01, f"Hab sin CPE esperado 2308.80, obtuvo {hab_sin_cpe}"

    # Identidad matemática 2: Portal SUNAT Nov Vigente == S/ 11,500.50 (Neto fiscal S/ 11,300.50)
    fac_vig_sum = r2(sum(d["monto_total"] for d in docs_canon.values() if d["tipo"] == "FACTURA" and not is_anulado(d["estado"])))
    bol_vig_sum = r2(sum(d["monto_total"] for d in docs_canon.values() if d["tipo"] == "BOLETA" and not is_anulado(d["estado"])))
    bol_anul_sum = r2(sum(d["monto_total"] for d in docs_canon.values() if d["tipo"] == "BOLETA" and is_anulado(d["estado"])))
    nc_vig_oct_sum = r2(
        sum(
            d["monto_total"]
            for d in docs_canon.values()
            if d["tipo"] == "NOTA_CREDITO"
            and not is_anulado(d["estado"])
            and d["nro_cp"] != "EB01-52"  # EB01-52 ya anuló EB01-6777 en Nov
        )
    )
    sunat_vig_bruto = r2(fac_vig_sum + bol_vig_sum)
    sunat_neto_fiscal = r2(sunat_vig_bruto - nc_vig_oct_sum)

    excedentes_2a_sum = r2(sum(p["excedente_total"] for p in vinculos["nivel2a_excedentes"]))
    oct_sin_banco_nov_sum = r2(sum(x["monto_sin_ingreso_nov"] for x in vinculos["oct_noches_en_nov"]))
    oct_pagadas_01nov_sum = r2(sum(x["bank_nov_matched"] for x in vinculos["oct_noches_en_nov"]))
    sunat_reconstruido = r2(hab_cubierto_sunat + excedentes_2a_sum + oct_sin_banco_nov_sum)

    assert abs(fac_vig_sum - 5673.00) < 0.01, f"Facturas vig != 5673.00 ({fac_vig_sum})"
    assert abs(bol_vig_sum - 5827.50) < 0.01, f"Boletas vig != 5827.50 ({bol_vig_sum})"
    assert abs(sunat_vig_bruto - 11500.50) < 0.01, f"SUNAT vig != 11500.50 ({sunat_vig_bruto})"
    assert abs(nc_vig_oct_sum - 200.00) < 0.01, f"NCs vig Oct != 200.00 ({nc_vig_oct_sum})"
    assert abs(sunat_neto_fiscal - 11300.50) < 0.01, f"SUNAT neto fiscal != 11300.50 ({sunat_neto_fiscal})"
    assert abs(excedentes_2a_sum - 1264.00) < 0.01, f"Excedentes 2A != 1264.00 ({excedentes_2a_sum})"
    assert abs(oct_sin_banco_nov_sum - 951.00) < 0.01, f"Oct sin banco != 951.00 ({oct_sin_banco_nov_sum})"
    assert abs(sunat_reconstruido - 11500.50) < 0.01, f"SUNAT reconstruido != 11500.50 ({sunat_reconstruido})"

    # Asignación Nivel 2 sobre Tienda S/ 168.00 + Sueltos S/ 4.80 = S/ 172.80
    asig_tienda_sueltos, remanentes_tienda = absorber_tienda_con_excedentes(
        tienda_pura, sueltos, lotes["nivel2b_pools"], vinculos["nivel2a_excedentes"]
    )
    assert len(remanentes_tienda) == 0, f"Quedaron remanentes sin absorber: {remanentes_tienda}"
    assert abs(sum(x["monto"] for x in asig_tienda_sueltos) - 172.80) < 0.01

    nivel4_respaldo = construir_respaldo_nivel4_horas_extras(tienda_pura, sueltos)
    assert abs(sum(p["monto_total"] for p in nivel4_respaldo) - 172.80) < 0.01

    # Totales de Lotes 3A, 3B.1, 3B.2, 3B.3 y Cierres Financieros
    sum_3a_yape = r2(sum(x["yape_banco"] for x in lotes["lote_3a_facturas_yape"]))
    sum_3a_emitir = r2(sum(x["monto_sugerido"] for x in lotes["lote_3a_facturas_yape"]))
    sum_3b1_yape = r2(sum(x["yape_banco"] for x in lotes["lote_3b1_boletas_sol"]))
    sum_3b1_emitir = r2(sum(x["monto_sugerido"] for x in lotes["lote_3b1_boletas_sol"]))
    sum_3b2_yape = r2(sum(x["yape_banco"] for x in lotes["lote_3b2_boletas_nosol"]))
    sum_cola_viva = r2(sum(x["monto"] for x in cola_viva))

    assert abs(r2(sum_3a_yape + sum_3b1_yape + sum_3b2_yape) - 2308.80) < 0.01, (
        f"Suma Yapes lotes 3A+3B.1+3B.2 != 2308.80 ({sum_3a_yape}+{sum_3b1_yape}+{sum_3b2_yape})"
    )

    # Cierres financieros proyectados (0 nuevos NC propuestos porque no hay duplicados en Nov)
    nc_propuestas = 0.0
    # Cierre A: Operativo Completo (Lote 3A + Lote 3B.1 + Lote 3B.2; Tienda S/172.80 100% absorbida por Nivel 2)
    emision_nueva_operativa = r2(sum_3a_emitir + sum_3b1_emitir + sum_3b2_yape)
    sunat_final_cierre_a = r2(sunat_neto_fiscal - nc_propuestas + emision_nueva_operativa)
    superavit_cierre_a = r2(sunat_final_cierre_a - meta_bcp_reportada)

    # Cierre B: Mínimo Solicitado + Cierre BCP (Lote 3A + Lote 3B.1; omitiendo Lote 3B.2 donde sol="No")
    emision_solicitada = r2(sum_3a_emitir + sum_3b1_emitir)
    sunat_final_cierre_b = r2(sunat_neto_fiscal - nc_propuestas + emision_solicitada)
    superavit_cierre_b = r2(sunat_final_cierre_b - meta_bcp_reportada)

    # Cierre C: Proyectado al 15/11 08:00 AM (+ Cola Viva Post-Corte S/ 760.00 -> Meta 12,527.10)
    meta_proyectada_15nov = r2(meta_bcp_reportada + sum_cola_viva)
    sunat_final_cierre_c = r2(sunat_final_cierre_a + sum_cola_viva)
    superavit_cierre_c = r2(sunat_final_cierre_c - meta_proyectada_15nov)

    plan_json = {
        "metadata": {
            "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "periodo_auditado": "2026-11-01 a 2026-11-15",
            "entorno": "SANDBOX_OVALO_MARIATEGUI_PRUEBA",
            "db_path": str(SANDBOX_DB),
            "regla_igv": {"porcentaje": 10.5, "factor": FACTOR_IGV},
            "notas_credito_propuestas_nov": [],
            "duplicados_detectados_nov": 0,
        },
        "cuadre_maestro": {
            "identidad_banco_extracto": {
                "meta_bcp_reportada": meta_bcp_reportada,
                "hab_y_corp_cubiertos_sunat_nov": hab_cubierto_sunat,
                "incluye_boletas_31oct_pagadas_01nov": oct_pagadas_01nov_sum,
                "incluye_hab_desglosadas_de_tienda_T03_T17": 140.0,
                "hab_y_emp_yape_sin_cpe": hab_sin_cpe,
                "tienda_pura_sin_cpe": tienda_pura_sum,
                "sueltos_banco_sin_cpe": sueltos_sum,
                "total_reconstruido": total_banco_reconstruido,
                "error_centavos": r2(total_banco_reconstruido - meta_bcp_reportada),
            },
            "identidad_portal_sunat": {
                "facturas_vigentes_18": fac_vig_sum,
                "boletas_vigentes_83": bol_vig_sum,
                "boletas_anuladas_2_EB01_6777_6782": bol_anul_sum,
                "sunat_emitido_vigente_bruto": sunat_vig_bruto,
                "nc_vigentes_modifican_octubre_3": nc_vig_oct_sum,
                "nc_anulada_portal_E001_88_no_resta": 55.0,
                "sunat_neto_fiscal_pdt": sunat_neto_fiscal,
                "calzado_con_banco_nov": hab_cubierto_sunat,
                "excedentes_credito_efectivo_mixto_nivel2a": excedentes_2a_sum,
                "noches_octubre_facturadas_nov_sin_banco_nov": oct_sin_banco_nov_sum,
                "total_vigente_reconstruido": sunat_reconstruido,
                "error_centavos": r2(sunat_reconstruido - sunat_vig_bruto),
                "brecha_global_tributaria_actual_vs_pdt": r2(meta_bcp_reportada - sunat_neto_fiscal),
                "brecha_global_tributaria_actual_vs_bruto_vig": r2(meta_bcp_reportada - sunat_vig_bruto),
            },
            "cierres_escenarios": {
                "cierre_A_operativo_completo_con_nivel2": {
                    "descripcion": "Emite Lote 3A (RUC) + Lote 3B.1 (Boletas sol=Sí) + Lote 3B.2 (Yapes sol=No); Tienda S/ 168.00 + Sueltos S/ 4.80 quedan 100% cubiertos por Excedentes Nivel 2 (S/ 0.00 en boletas extra).",
                    "sunat_pdt_base": sunat_neto_fiscal,
                    "nueva_emision_lote_3A": sum_3a_emitir,
                    "nueva_emision_lote_3B1": sum_3b1_emitir,
                    "nueva_emision_lote_3B2": sum_3b2_yape,
                    "nueva_emision_nivel4_horas_extras": 0.0,
                    "total_nueva_emision": emision_nueva_operativa,
                    "sunat_neto_final": sunat_final_cierre_a,
                    "meta_bcp": meta_bcp_reportada,
                    "superavit_fiscal_vs_bcp": superavit_cierre_a,
                },
                "cierre_B_minimo_solicitado_y_bcp": {
                    "descripcion": "Emite únicamente Lote 3A (RUC) + Lote 3B.1 (Boletas sol=Sí); las 7 estancias con sol='No' (Lote 3B.2) y Tienda+Sueltos (S/ 172.80) quedan respaldadas tributariamente por el colchón efectivo/crédito/Nivel 2 ya existente (+S/ 1,264.00 + +S/ 284.00).",
                    "sunat_pdt_base": sunat_neto_fiscal,
                    "nueva_emision_lote_3A": sum_3a_emitir,
                    "nueva_emision_lote_3B1": sum_3b1_emitir,
                    "total_nueva_emision": emision_solicitada,
                    "sunat_neto_final": sunat_final_cierre_b,
                    "meta_bcp": meta_bcp_reportada,
                    "superavit_fiscal_vs_bcp": superavit_cierre_b,
                },
                "cierre_C_proyectado_15nov_08am": {
                    "descripcion": "Cierre A más Cola Viva Post-Corte (10 Yapes del 14/11 16:17 al 15/11 07:51 = S/ 760.00).",
                    "cola_viva_post_corte": sum_cola_viva,
                    "meta_bcp_proyectada": meta_proyectada_15nov,
                    "sunat_neto_final_proyectado": sunat_final_cierre_c,
                    "superavit_fiscal_proyectado": superavit_cierre_c,
                },
            },
        },
        "correcciones_forenses_aplicadas": corrections_applied,
        "comprobantes_emitidos_sin_anotar_en_bd": vinculos["sin_anotar_bd"],
        "nivel_1_calzado_exacto": vinculos["nivel1_calzado"],
        "nivel_2_unificacion_excedentes": {
            "bolsas_2b_por_emitir_mixtos": lotes["nivel2b_pools"],
            "bolsas_2a_ya_emitidos": vinculos["nivel2a_excedentes"],
            "asignacion_tienda_y_sueltos": asig_tienda_sueltos,
            "resumen": {
                "total_tienda_pura_y_sueltos": r2(tienda_pura_sum + sueltos_sum),
                "total_cubierto_por_2B": r2(sum(x["monto"] for x in asig_tienda_sueltos if x["nivel_bolsa"].startswith("2B"))),
                "total_cubierto_por_2A": r2(sum(x["monto"] for x in asig_tienda_sueltos if x["nivel_bolsa"].startswith("2A"))),
                "remanentes_sin_cubrir": len(remanentes_tienda),
                "boletas_adicionales_requeridas": 0,
            },
        },
        "nivel_3_lotes_pendientes": {
            "lote_3A_facturas_ruc_con_yape": lotes["lote_3a_facturas_yape"],
            "lote_3B1_boletas_dni_solicitadas": lotes["lote_3b1_boletas_sol"],
            "lote_3B2_yapes_no_solicitaron": lotes["lote_3b2_boletas_nosol"],
            "lote_3B3_cola_viva_post_corte_14_15nov": cola_viva,
            "informativo_facturas_pendientes_solo_efectivo_credito": lotes["facturas_solo_efectivo_credito"],
        },
        "nivel_4_respaldo_horas_extras": nivel4_respaldo,
        "noches_octubre_emitidas_noviembre": vinculos["oct_noches_en_nov"],
    }

    OUT_PLAN_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PLAN_JSON, "w", encoding="utf-8") as f:
        json.dump(plan_json, f, indent=2, ensure_ascii=False)

    if verbose:
        print("=== AUDITOR CALZADO BCP <-> SUNAT (SANDBOX GR-05) ===")
        print(f"SQLite Sandbox sincronizado -> {SANDBOX_DB}")
        print(f"Identidad Banco Extracto : S/ {total_banco_reconstruido:,.2f} == S/ {meta_bcp_reportada:,.2f} (err={total_banco_reconstruido-meta_bcp_reportada:.2f})")
        print(f"  - Hab/Corp cubiertos   : S/ {hab_cubierto_sunat:,.2f} (incl. S/ {oct_pagadas_01nov_sum:,.2f} Oct->01/11 + S/ 140.00 T03/T17)")
        print(f"  - Estancias sin CPE    : S/ {hab_sin_cpe:,.2f} (Lote 3A Yape S/ {sum_3a_yape:,.2f} + 3B.1 Yape S/ {sum_3b1_yape:,.2f} + 3B.2 Yape S/ {sum_3b2_yape:,.2f})")
        print(f"  - Tienda pura / Sueltos: S/ {tienda_pura_sum:,.2f} / S/ {sueltos_sum:,.2f} (Total S/ {tienda_pura_sum+sueltos_sum:,.2f} -> 100% absorbido Nivel 2)")
        print(f"Identidad Portal SUNAT   : S/ {sunat_reconstruido:,.2f} == S/ {sunat_vig_bruto:,.2f} (PDT neto S/ {sunat_neto_fiscal:,.2f} tras -S/ {nc_vig_oct_sum:,.2f} NCs Oct)")
        print(f"Brecha tributaria actual : S/ {meta_bcp_reportada - sunat_neto_fiscal:,.2f} (PDT) | S/ {meta_bcp_reportada - sunat_vig_bruto:,.2f} (Bruto vig.)")
        print(f"Emitidos sin anotar BD   : {len(vinculos['sin_anotar_bd'])} CPEs")
        print(f"Cola Viva Post-Corte     : {len(cola_viva)} Yapes = S/ {sum_cola_viva:,.2f} (Meta proy. 15/11 = S/ {meta_proyectada_15nov:,.2f})")
        print(f"Cierre A (Op. Completo)  : +S/ {emision_nueva_operativa:,.2f} -> SUNAT PDT S/ {sunat_final_cierre_a:,.2f} (superavit +S/ {superavit_cierre_a:,.2f})")
        print(f"Cierre B (Min Sol.+BCP)  : +S/ {emision_solicitada:,.2f} -> SUNAT PDT S/ {sunat_final_cierre_b:,.2f} (superavit +S/ {superavit_cierre_b:,.2f})")
        print(f"Plan exportado           : {OUT_PLAN_JSON}")

    return plan_json


if __name__ == "__main__":
    ejecutar_auditoria(verbose=True)
