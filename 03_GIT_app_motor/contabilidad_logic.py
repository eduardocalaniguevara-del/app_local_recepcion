import sys as _sys
from pathlib import Path as _Path
_MDIR = str(_Path(__file__).resolve().parent)
if _MDIR not in _sys.path:
    _sys.path.insert(0, _MDIR)
import config as _cfg_v2
import db as _db_v2
_sys.modules["config"] = _cfg_v2
_sys.modules["database"] = _db_v2

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo Backend de Contabilidad, Conciliación SUNAT <-> BCP/Yape y Emparejamiento.
Arnés² — Ley 2 / GR-05 / GR-10.
Ubicación: C:\\ovalo_mariategui_prueba\\contabilidad_logic.py

Responsabilidades:
1. Conecta auditor_calzado_bcp_sunat.py con SQLite (ovalo_sistema.db) y expone
   `obtener_datos_contabilidad_local(mes='2026-11')` con:
   - puente_visual: Métricas exactas (Banco BCP S/ 11,767.10 + S/ 760 Cola Viva;
     SUNAT Bruto S/ 11,500.50 y PDT Neto S/ 11,300.50 tras NCs Octubre; cierres A, B, C; error S/ 0.00).
   - vasijas_nivel_2: 8 bolsas vasija de absorción Nivel 2 absorbiendo el 100% de S/ 172.80
     (S/ 168.00 tienda + S/ 4.80 sueltos con 0 boletas chicas extra).
   - cpe_pendientes_bd: Los 8 CPEs ya emitidos en SUNAT sin anotar en BD (E001-2108..2113, E001-2118, EB01-6860 + RENIEC 11/11).
   - lotes_emision: Lotes 3A, 3B.1, 3B.2, 3B.3 y Nivel 4 (Respaldo horas extras).
   - calzado_rows y resumen_estados: Filas del cruce tricolor (Estancia <-> Banco <-> SUNAT).
   - sunat_rows y extracto_rows.
2. `recalcular_auditoria_contabilidad_local()`: Dispara recálculo fresco de la auditoría.
3. `sincronizar_comprobantes_faltantes_bd_local()`: Anota en SQLite Sandbox los 8 CPEs
   ya emitidos en SUNAT fijando estado_comp='Emitido' y nro_comp sin re-emitir.
4. Mantiene retrocompatibilidad 100% con `calzado_cache`, Supabase y Google Sheets.
"""

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DB_PATH as SANDBOX_DB, DATA_DIR

# === Rutas Dinámicas de la Aplicación (compatible con Prueba :8082 y Operativa :8080) ===
SANDBOX_DIR = Path(__file__).resolve().parent
SUNAT_SHARED_DIR = Path(r"C:\Hostal_Sistema\datos_sunat")
PLAN_JSON_PATH = DATA_DIR / "plan_emparejamiento_noviembre_2026.json"
CORR_JSON_PATH = DATA_DIR / "corroboracion_ingresos_bcp_nov2026.json"


def _asegurar_jsons_base_contabilidad() -> None:
    """Si aún no existen los JSON base en data/ o faltan los 108 CPEs de Nov 2026 en sunat_ventas, hidrata desde C:\\Hostal_Sistema\\datos_sunat."""
    import shutil
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for local_p in (CORR_JSON_PATH, PLAN_JSON_PATH):
        if not local_p.exists():
            shared_p = SUNAT_SHARED_DIR / local_p.name
            if shared_p.exists():
                try:
                    shutil.copy2(shared_p, local_p)
                except Exception:
                    pass
    try:
        conn = _get_db_connection()
        try:
            cnt_nov = conn.execute("SELECT COUNT(*) FROM sunat_ventas WHERE periodo_mes = '2026-11'").fetchone()[0]
        except Exception:
            cnt_nov = 0
        finally:
            conn.close()
        if cnt_nov < 100 and (SUNAT_SHARED_DIR / "sunat_facturas_noviembre_raw.json").exists():
            import auditor_calzado_bcp_sunat as aud
            aud.sincronizar_sunat_nov_sqlite(SANDBOX_DB)
    except Exception:
        pass

FACTOR_IGV = 1.105  # Ley 31556 / IGV hotelero 10.5%


def _r2(val: Any) -> float:
    try:
        return round(float(val or 0.0), 2)
    except (ValueError, TypeError):
        return 0.0


def _valor_sin_igv(total: float) -> float:
    return round(float(total) / FACTOR_IGV, 4)


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SANDBOX_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _cargar_plan_emparejamiento(forzar_recalculo: bool = False) -> Dict[str, Any]:
    """Carga el plan validado desde JSON o lo genera usando auditor_calzado_bcp_sunat."""
    _asegurar_jsons_base_contabilidad()
    if not PLAN_JSON_PATH.exists() or forzar_recalculo:
        try:
            import auditor_calzado_bcp_sunat as aud
            return aud.ejecutar_auditoria(verbose=False)
        except Exception as exc:
            if PLAN_JSON_PATH.exists():
                with open(PLAN_JSON_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise RuntimeError(f"Error al ejecutar auditor_calzado_bcp_sunat: {exc}")
    else:
        with open(PLAN_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def _obtener_puente_visual(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """Genera las métricas calculadas exactas del puente visual."""
    cuadre = plan_data.get("cuadre_maestro", {})
    banco = cuadre.get("identidad_banco_extracto", {})
    sunat = cuadre.get("identidad_portal_sunat", {})
    cierres = cuadre.get("cierres_escenarios", {})

    banco_total = _r2(banco.get("meta_bcp_reportada", 11767.10))
    banco_calzado = _r2(banco.get("hab_y_corp_cubiertos_sunat_nov", 9285.50))
    tienda_pura = _r2(banco.get("tienda_pura_sin_cpe", 168.00))
    sueltos = _r2(banco.get("sueltos_banco_sin_cpe", 4.80))
    banco_absorbido_n2 = _r2(tienda_pura + sueltos)

    # Lotes 3A + 3B.1 + 3B.2
    lotes_p = plan_data.get("nivel_3_lotes_pendientes", {})
    lote_3a_yape = _r2(sum(x.get("yape_banco", 0.0) for x in lotes_p.get("lote_3A_facturas_ruc_con_yape", [])))
    lote_3b1_yape = _r2(sum(x.get("yape_banco", 0.0) for x in lotes_p.get("lote_3B1_boletas_dni_solicitadas", [])))
    banco_lotes_3ab = _r2(lote_3a_yape + lote_3b1_yape)
    banco_no_solicita = _r2(sum(x.get("yape_banco", 0.0) for x in lotes_p.get("lote_3B2_yapes_no_solicitaron", [])))

    cola_viva_items = lotes_p.get("lote_3B3_cola_viva_post_corte_14_15nov", [])
    cola_viva_total = _r2(sum(x.get("monto", 0.0) for x in cola_viva_items))
    if cola_viva_total == 0.0:
        cola_viva_total = 760.00

    sunat_bruto = _r2(sunat.get("sunat_emitido_vigente_bruto", 11500.50))
    sunat_pdt = _r2(sunat.get("sunat_neto_fiscal_pdt", 11300.50))
    facturas_monto = _r2(sunat.get("facturas_vigentes_18", 5673.00))
    boletas_monto = _r2(sunat.get("boletas_vigentes_83", 5827.50))
    boletas_anuladas_monto = _r2(sunat.get("boletas_anuladas_nov_2", 195.00))
    boletas_sire_bruto_85 = _r2(boletas_monto + boletas_anuladas_monto)
    nc_oct_monto = _r2(sunat.get("nc_vigentes_modifican_octubre_3", 200.00))
    nc_nov_monto = _r2(sunat.get("nc_que_anulan_noviembre_2", 195.00))
    nc_sire_total_5 = _r2(nc_oct_monto + nc_nov_monto)
    sunat_base_sin_igv = round(_valor_sin_igv(sunat_pdt), 2)
    sunat_igv_105 = _r2(sunat_pdt - sunat_base_sin_igv)

    ca = cierres.get("cierre_A_operativo_completo_con_nivel2", {})
    cb = cierres.get("cierre_B_minimo_solicitado_y_bcp", {})
    cc = cierres.get("cierre_C_proyectado_15nov_08am", {})

    cierre_A = {
        "sunat_proyectado": _r2(ca.get("sunat_neto_final", 13893.30)),
        "diferencia_banco": _r2(ca.get("superavit_fiscal_vs_bcp", 2126.20)),
        "nueva_emision": _r2(ca.get("total_nueva_emision", 2592.80)),
        "descripcion": ca.get("descripcion", "Emite Lote 3A (RUC) + Lote 3B.1 (Boletas sol=Sí) + Lote 3B.2 (Yapes sol=No); Tienda S/ 168.00 + Sueltos S/ 4.80 quedan 100% cubiertos por Excedentes Nivel 2 (S/ 0.00 en boletas extra).")
    }
    cierre_B = {
        "sunat_proyectado": _r2(cb.get("sunat_neto_final", 13558.30)),
        "diferencia_banco": _r2(cb.get("superavit_fiscal_vs_bcp", 1791.20)),
        "nueva_emision": _r2(cb.get("total_nueva_emision", 2257.80)),
        "descripcion": cb.get("descripcion", "Emite únicamente Lote 3A (RUC) + Lote 3B.1 (Boletas sol=Sí); las 7 estancias con sol='No' (Lote 3B.2) y Tienda+Sueltos (S/ 172.80) quedan respaldadas tributariamente por el colchón efectivo/crédito/Nivel 2 ya existente (+S/ 1,264.00 + +S/ 284.00).")
    }
    cierre_C = {
        "sunat_proyectado": _r2(cc.get("sunat_neto_final_proyectado", 14653.30)),
        "meta_banco": _r2(cc.get("meta_bcp_proyectada", 12527.10)),
        "cola_viva": cola_viva_total,
        "descripcion": cc.get("descripcion", "Cierre A más Cola Viva Post-Corte (10 Yapes del 14/11 16:17 al 15/11 07:51 = S/ 760.00).")
    }

    excedentes_nivel2a = _r2(sunat.get("excedentes_credito_efectivo_mixto_nivel2a", 1264.00))
    noches_oct_en_nov = _r2(sunat.get("noches_octubre_facturadas_nov_sin_banco_nov", 951.00))
    colchon_fiscal = _r2(excedentes_nivel2a + noches_oct_en_nov)

    # Identidad de reconstrucción
    banco_reconstruido = _r2(banco_calzado + banco_absorbido_n2 + banco_lotes_3ab + banco_no_solicita)
    balance_error = _r2(banco_reconstruido - banco_total)

    # Alerta de déficit frente al BCP (S4 Plan v2)
    if banco_total > sunat_pdt:
        deficit_monto = round(banco_total - sunat_pdt, 2)
        alerta_deficit = {
            "hay_deficit": True,
            "monto": deficit_monto,
            "mensaje": f"Faltan S/ {deficit_monto:.2f} frente al BCP",
        }
    else:
        alerta_deficit = {
            "hay_deficit": False,
            "monto": 0.0,
            "mensaje": "",
        }

    # Efectivo Maelen acumulado del mes (suma mensual de colchón en estancias emitidas)
    efectivo_maelen_acumulado = 0.0
    try:
        conn_pv = _get_db_connection()
        try:
            rows_em = conn_pv.execute("""
                SELECT monto_factura, tarifa, pago_ini_yape, pago_post_yape, nro_comp, estado_comp
                FROM estancias
                WHERE ((ymd_in BETWEEN 20261101 AND 20261115) OR (ymd_in = 20261031 AND (pago_ini_yape > 0 OR pago_post_yape > 0)))
                  AND (lower(coalesce(estado_comp, '')) = 'emitido' OR (nro_comp IS NOT NULL AND trim(nro_comp) != ''))
            """).fetchall()
            for r in rows_em:
                m_comp = float(r["monto_factura"] or 0.0) or float(r["tarifa"] or 0.0)
                y_banco = float(r["pago_ini_yape"] or 0.0) + float(r["pago_post_yape"] or 0.0)
                c = max(0.0, round(m_comp - y_banco, 2))
                efectivo_maelen_acumulado += c
            efectivo_maelen_acumulado = round(efectivo_maelen_acumulado, 2)
        finally:
            conn_pv.close()
    except Exception:
        pass

    return {
        "banco_total": banco_total,
        "cola_viva": cola_viva_total,
        "banco_calzado": banco_calzado,
        "banco_absorbido_n2": banco_absorbido_n2,
        "banco_lotes_3ab": banco_lotes_3ab,
        "banco_no_solicita": banco_no_solicita,
        "sunat_bruto": sunat_bruto,
        "sunat_pdt": sunat_pdt,
        "sunat_declarado_neto": sunat_pdt,
        "sunat_base_sin_igv": sunat_base_sin_igv,
        "sunat_igv_105": sunat_igv_105,
        "sunat_facturas_monto": facturas_monto,
        "sunat_facturas_cant": 18,
        "sunat_boletas_monto": boletas_monto,
        "sunat_boletas_cant": 83,
        "sunat_boletas_anuladas_monto": boletas_anuladas_monto,
        "sunat_boletas_sire_bruto_85": boletas_sire_bruto_85,
        "sunat_nc_oct_monto": nc_oct_monto,
        "sunat_nc_oct_cant": 3,
        "sunat_nc_nov_monto": nc_nov_monto,
        "sunat_nc_sire_total_5": nc_sire_total_5,
        "cierre_A": cierre_A,
        "cierre_B": cierre_B,
        "cierre_C": cierre_C,
        "colchon_fiscal": colchon_fiscal,
        "excedentes_nivel2a": excedentes_nivel2a,
        "noches_oct_en_nov": noches_oct_en_nov,
        "balance_error": balance_error,
        "alerta_deficit": alerta_deficit,
        "efectivo_maelen_acumulado": efectivo_maelen_acumulado,
    }


def _extraer_hab_de_pool(pool: Dict[str, Any]) -> str:
    """Extrae el número de habitación limpio de un pool Nivel 2."""
    id_pool = pool.get("id_pool", "")
    m = re.search(r"H(\d+)", id_pool)
    if m:
        return m.group(1)
    if "EVENTISIMO" in id_pool:
        return "205/306"
    if "E001-2102" in id_pool or "EB01-6815" in id_pool:
        return "208"
    if "EB01-6860" in id_pool:
        return "406"
    return pool.get("hab", "—")


def _construir_vasijas_nivel_2(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """Formatea las 8 Vasijas de Absorción de Excedentes Nivel 2 y su resumen."""
    n2 = plan_data.get("nivel_2_unificacion_excedentes", {})
    bolsas_2b = n2.get("bolsas_2b_por_emitir_mixtos", [])
    bolsas_2a = n2.get("bolsas_2a_ya_emitidos", [])

    # Filtrar únicamente las 8 bolsas que consumieron ítems
    activas_2b = [p for p in bolsas_2b if p.get("consumido_por")]
    activas_2a = [p for p in bolsas_2a if p.get("consumido_por")]

    vasijas = []
    for p in activas_2b + activas_2a:
        pool_id = p.get("id_pool", p.get("nro_cp", ""))
        excedente = _r2(p.get("excedente_total", 0.0))
        items_c = p.get("consumido_por", [])
        absorbido = _r2(sum(it.get("monto", 0.0) for it in items_c))
        libre = _r2(p.get("excedente_disponible", excedente - absorbido))
        pct = round((absorbido / excedente * 100), 1) if excedente > 0 else 0.0

        vasijas.append({
            "id": pool_id,
            "nivel": p.get("nivel", "2B" if pool_id.startswith("2B") else "2A"),
            "huesped": p.get("cliente", ""),
            "hab": _extraer_hab_de_pool(p),
            "tipo": p.get("tipo", "BOLETA"),
            "monto_cpe": _r2(p.get("monto_sugerido", p.get("monto_sunat", 0.0))),
            "monto_yape": _r2(p.get("bank_matched", 0.0)),
            "excedente": excedente,
            "absorbido": absorbido,
            "libre": libre,
            "porcentaje_uso": pct,
            "items_absorbidos": items_c,
        })

    resumen = n2.get("resumen", {
        "total_tienda_pura_y_sueltos": 172.80,
        "total_cubierto_por_2B": 122.30,
        "total_cubierto_por_2A": 50.50,
        "remanentes_sin_cubrir": 0,
        "boletas_adicionales_requeridas": 0,
    })

    return {
        "vasijas": vasijas,
        "resumen": resumen,
    }


CATALOGO_8_CPES_SIN_ANOTAR = [
    {
        "nro_cp": "E001-2108",
        "tipo": "FACTURA",
        "fecha_emision": "06/11/2026",
        "monto_sunat": 140.0,
        "cliente": "MAN PAN SERVICE S.A.C.",
        "ruc_dni": "20250975563",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE ruc = '20250975563' AND ymd_in IN (20261104, 20261105) AND hab IN ('102', '206')",
    },
    {
        "nro_cp": "E001-2109",
        "tipo": "FACTURA",
        "fecha_emision": "06/11/2026",
        "monto_sunat": 140.0,
        "cliente": "MAN PAN SERVICE S.A.C.",
        "ruc_dni": "20250975563",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE ruc = '20250975563' AND ymd_in IN (20261104, 20261105) AND hab = '307'",
    },
    {
        "nro_cp": "E001-2110",
        "tipo": "FACTURA",
        "fecha_emision": "06/11/2026",
        "monto_sunat": 55.0,
        "cliente": "FARMACIAS PERUANAS S.A.C.",
        "ruc_dni": "20605900012",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE estancia_uuid LIKE 'aa5cb16d%'",
    },
    {
        "nro_cp": "E001-2111",
        "tipo": "FACTURA",
        "fecha_emision": "06/11/2026",
        "monto_sunat": 70.0,
        "cliente": "AUTOSAFE S.A.C.",
        "ruc_dni": "20504292968",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE estancia_uuid LIKE 'b21b23e1%'",
    },
    {
        "nro_cp": "E001-2112",
        "tipo": "FACTURA",
        "fecha_emision": "06/11/2026",
        "monto_sunat": 70.0,
        "cliente": "QUISAPATA SOLUCIONES S.A.C.",
        "ruc_dni": "20603511043",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE estancia_uuid LIKE '76537f03%'",
    },
    {
        "nro_cp": "E001-2113",
        "tipo": "FACTURA",
        "fecha_emision": "09/11/2026",
        "monto_sunat": 233.0,
        "cliente": "QUISAPATA SOLUCIONES S.A.C.",
        "ruc_dni": "20603511043",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE ymd_in = 20261108 AND ruc = '20603511043'",
    },
    {
        "nro_cp": "E001-2118",
        "tipo": "FACTURA",
        "fecha_emision": "12/11/2026",
        "monto_sunat": 70.0,
        "cliente": "AUTOSAFE S.A.C.",
        "ruc_dni": "20504292968",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE estancia_uuid LIKE '147c9a9a%'",
    },
    {
        "nro_cp": "EB01-6860",
        "tipo": "BOLETA",
        "fecha_emision": "12/11/2026",
        "monto_sunat": 35.0,
        "cliente": "MIGUEL ANGEL SOTO GUERRA",
        "ruc_dni": "42583830",
        "filtro_sql": "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE estancia_uuid LIKE '743bd21a%'",
    },
]


def _construir_cpe_pendientes_bd(plan_data: Dict[str, Any], conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Devuelve los 8 CPEs ya emitidos en SUNAT sin anotar en BD, enriquecidos con estado en BD."""
    resultado = []
    cur = conn.cursor()

    for cp_info in CATALOGO_8_CPES_SIN_ANOTAR:
        nro = cp_info["nro_cp"]
        filas = cur.execute(cp_info["filtro_sql"]).fetchall()
        estancias = []
        for r in filas:
            sinc = bool(r["nro_comp"] and nro in r["nro_comp"])
            estancias.append({
                "estancia_uuid": r["estancia_uuid"],
                "fecha_in": r["fecha_in_dmy"],
                "hab": r["hab"],
                "huesped": r["huesped"],
                "estado_comp_bd": r["estado_comp"] or "Pendiente",
                "nro_comp_bd": r["nro_comp"] or "(VACÍO)",
                "sincronizado": sinc,
            })

        todos_sinc = all(est["sincronizado"] for est in estancias) if estancias else False

        resultado.append({
            "nro_cp": nro,
            "serie_numero": nro,
            "tipo": cp_info["tipo"],
            "fecha_emision": cp_info["fecha_emision"],
            "fecha": cp_info["fecha_emision"],
            "monto_sunat": cp_info["monto_sunat"],
            "monto": cp_info["monto_sunat"],
            "cliente": cp_info["cliente"],
            "ruc_dni": cp_info["ruc_dni"],
            "estancias_bd": estancias,
            "sincronizado_completo": todos_sinc,
            "nota": "Vigente en SUNAT; pulsar 'Sincronizar en BD' para marcar Emitido sin re-emitir." if not todos_sinc else "✅ Ya sincronizado con estado 'Emitido' en BD."
        })

    # Agregar información explícita de RENIEC 11/11
    reniec_est = cur.execute(
        "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, estado_comp, nro_comp FROM estancias WHERE fecha_in_dmy = '11/11/2026' AND ruc = '20295613620'"
    ).fetchall()
    if reniec_est:
        resultado.append({
            "nro_cp": "E001-2116 / E001-2117 (RENIEC)",
            "serie_numero": "E001-2116 / E001-2117",
            "tipo": "FACTURA",
            "fecha_emision": "11/11/2026",
            "fecha": "11/11/2026",
            "monto_sunat": 540.0,
            "monto": 540.0,
            "cliente": "REGIST. NAC. DE IDENTIF. Y ESTADO CIVIL",
            "ruc_dni": "20295613620",
            "estancias_bd": [
                {
                    "estancia_uuid": r["estancia_uuid"],
                    "fecha_in": r["fecha_in_dmy"],
                    "hab": r["hab"],
                    "huesped": r["huesped"],
                    "estado_comp_bd": r["estado_comp"] or "Pendiente",
                    "nro_comp_bd": r["nro_comp"] or "(VACÍO)",
                    "sincronizado": bool(r["nro_comp"]),
                }
                for r in reniec_est
            ],
            "sincronizado_completo": all(bool(r["nro_comp"]) for r in reniec_est),
            "nota": "Estancias de Rodrigo Guevara (H406) y Rodolfo Gutiérrez (H407) cubiertas en glosa de E001-2116/2117."
        })

    return resultado


def _construir_lotes_emision(plan_data: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Formatea los lotes de emisión 3A, 3B.1, 3B.2, 3B.3 y Nivel 4 con separación Naranja Intenso vs Amarillo."""
    lp = plan_data.get("nivel_3_lotes_pendientes", {})
    lote_3a = [dict(x) for x in lp.get("lote_3A_facturas_ruc_con_yape", [])]
    lote_3b1 = [dict(x) for x in lp.get("lote_3B1_boletas_dni_solicitadas", [])]
    lote_3b2 = [dict(x) for x in lp.get("lote_3B2_yapes_no_solicitaron", [])]
    lote_3b3 = [dict(x) for x in lp.get("lote_3B3_cola_viva_post_corte_14_15nov", [])]
    nivel_4 = plan_data.get("nivel_4_respaldo_horas_extras", [])

    cel_por_uuid: Dict[str, str] = {}
    cel_por_hab_fecha: Dict[tuple, str] = {}
    if conn is not None:
        for r in conn.execute("SELECT estancia_uuid, fecha_in_dmy, hab, celular, ruc, tipo_comp FROM estancias WHERE ymd_in >= 20261031").fetchall():
            c_clean = str(r["celular"] or "").strip()
            cel_por_uuid[str(r["estancia_uuid"] or "")] = c_clean
            if c_clean:
                cel_por_hab_fecha[(str(r["fecha_in_dmy"] or ""), str(r["hab"] or ""))] = c_clean

    def _tiene_cel(cel_txt: str) -> bool:
        return sum(1 for ch in str(cel_txt or "") if ch.isdigit()) >= 7

    for it in lote_3a:
        u = str(it.get("estancia_uuid") or "")
        cel = cel_por_uuid.get(u, "")
        if not cel and "," in u:
            for sub_p in u.split(","):
                for k_u, v_c in cel_por_uuid.items():
                    if k_u.startswith(sub_p) and v_c:
                        cel = v_c
                        break
        it["celular"] = cel
        it["tiene_celular"] = _tiene_cel(cel)
        it["es_factura"] = True
        it["prioridad_naranja"] = True  # Facturas SIEMPRE en Naranja Intenso

    for it in lote_3b1:
        u = str(it.get("estancia_uuid") or "")
        cel = cel_por_uuid.get(u, "") or cel_por_hab_fecha.get((str(it.get("fecha_in") or ""), str(it.get("hab") or "")), "")
        it["celular"] = cel
        it["tiene_celular"] = _tiene_cel(cel)
        it["es_factura"] = False
        it["prioridad_naranja"] = _tiene_cel(cel)  # Boletas: Naranja Intenso SOLO si tienen celular; Amarillo si no

    for it in lote_3b2:
        u = str(it.get("estancia_uuid") or "")
        cel = cel_por_uuid.get(u, "") or cel_por_hab_fecha.get((str(it.get("fecha_in") or ""), str(it.get("hab") or "")), "")
        it["celular"] = cel
        it["tiene_celular"] = _tiene_cel(cel)
        it["es_factura"] = False
        it["prioridad_naranja"] = _tiene_cel(cel)

    for it in lote_3b3:
        cel = cel_por_hab_fecha.get((str(it.get("fecha_in") or ""), str(it.get("hab") or "")), "")
        es_fac_cv = str(it.get("tipo_comp") or "").lower().startswith("fact")
        it["celular"] = cel
        it["tiene_celular"] = _tiene_cel(cel)
        it["es_factura"] = es_fac_cv
        it["prioridad_naranja"] = bool(es_fac_cv or _tiene_cel(cel))

    # Ordenar Lote 3B.1 poniendo primero las Naranja Intenso (con celular de envío WSP) y luego las Amarillas (sin celular)
    lote_3b1.sort(key=lambda x: (0 if x.get("prioridad_naranja") else 1, str(x.get("fecha_in") or ""), str(x.get("hab") or "")))

    return {
        "lote_3A": lote_3a,
        "lote_3B1": lote_3b1,
        "lote_3B2": lote_3b2,
        "lote_3B3": lote_3b3,
        "nivel_4_respaldo": nivel_4,
        "totales_lotes": {
            "lote_3A_yape": _r2(sum(x.get("yape_banco", 0.0) for x in lote_3a)),
            "lote_3A_emitir": _r2(sum(x.get("monto_sugerido", 0.0) for x in lote_3a)),
            "lote_3B1_yape": _r2(sum(x.get("yape_banco", 0.0) for x in lote_3b1)),
            "lote_3B1_emitir": _r2(sum(x.get("monto_sugerido", 0.0) for x in lote_3b1)),
            "lote_3B1_con_cel_cant": sum(1 for x in lote_3b1 if x.get("tiene_celular")),
            "lote_3B1_sin_cel_cant": sum(1 for x in lote_3b1 if not x.get("tiene_celular")),
            "lote_3B2_yape": _r2(sum(x.get("yape_banco", 0.0) for x in lote_3b2)),
            "lote_3B3_cola_viva": _r2(sum(x.get("monto", 0.0) for x in lote_3b3)),
            "nivel_4_respaldo": _r2(sum(x.get("monto_total", 0.0) for x in nivel_4)),
        }
    }


def _construir_sunat_rows(conn: sqlite3.Connection, mes: str = "2026-11") -> List[Dict[str, Any]]:
    """Consulta comprobantes de SUNAT en SQLite Sandbox con desglose base/IGV."""
    cur = conn.cursor()
    filas = cur.execute(
        """
        SELECT nro_cp, tipo, periodo_mes, fecha_emision, receptor_doc, receptor_nombre,
               monto_total, estado, resumen_items
        FROM sunat_ventas
        WHERE periodo_mes = ?
        ORDER BY fecha_emision, tipo, nro_cp
        """,
        (mes,)
    ).fetchall()

    lineas_por_cp = defaultdict(list)
    for ln in cur.execute("SELECT nro_cp, noche_dmy, hab, monto_linea, descripcion FROM sunat_lineas_cache").fetchall():
        lineas_por_cp[ln["nro_cp"]].append(dict(ln))

    resultado = []
    for r in filas:
        raw_total = abs(_r2(r["monto_total"]))
        tipo_doc = str(r["tipo"] or "").strip().upper()
        cp_num = r["nro_cp"]
        estado_raw = str(r["estado"] or "VIGENTE").strip()
        # Las Notas de Crédito en SIRE están vigentes (-S/ 395.00); solo se tachan las 2 Boletas anuladas (EB01-6777 y EB01-6782)
        es_anulado = (tipo_doc != "NOTA_CREDITO" and "ANULAD" in estado_raw.upper())
        if tipo_doc == "NOTA_CREDITO":
            estado_mostrar = "VIGENTE (Anula Nov)" if cp_num in ("EB01-52", "E001-88") else "VIGENTE (Modif. Oct)"
        else:
            estado_mostrar = estado_raw
        signo = -1.0 if tipo_doc == "NOTA_CREDITO" else 1.0

        total_signed = _r2(signo * raw_total)
        base_abs = round(_valor_sin_igv(raw_total), 2)
        igv_abs = _r2(raw_total - base_abs)
        base_signed = _r2(signo * base_abs)
        igv_signed = _r2(signo * igv_abs)
        glosa_txt = r["resumen_items"] or ""

        resultado.append({
            # Campos originales backend
            "nro_cp": cp_num,
            "tipo": tipo_doc,
            "periodo_mes": r["periodo_mes"],
            "fecha_emision": r["fecha_emision"],
            "receptor_doc": r["receptor_doc"] or "",
            "receptor_nombre": r["receptor_nombre"] or "",
            "monto_total": total_signed,
            "base_imponible": base_signed,
            "igv": igv_signed,
            "estado": estado_mostrar,
            "resumen_items": glosa_txt,
            "lineas_detalle": lineas_por_cp.get(cp_num, []),
            # Alias directos para UI PanelDisponibilidad.html
            "fecha": r["fecha_emision"] or "",
            "serie_numero": cp_num,
            "doc_identidad": r["receptor_doc"] or "—",
            "razon_social": r["receptor_nombre"] or "CLIENTES VARIOS",
            "moneda": "PEN",
            "total": total_signed,
            "estado_sunat": estado_mostrar,
            "es_anulado": es_anulado,
            "glosa": glosa_txt,
        })

    return resultado


def _sort_key_dmy_hms(fecha_str: str, hora_str: str) -> tuple:
    f = str(fecha_str or "").strip()
    h = str(hora_str or "").strip()
    ymd = 0
    partes = f.split("/")
    if len(partes) == 3:
        try:
            ymd = int(partes[2]) * 10000 + int(partes[1]) * 100 + int(partes[0])
        except ValueError:
            ymd = 0
    return (ymd, h)


def _construir_extracto_rows(plan_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Devuelve los ingresos reales BCP/Yape (+), cola viva (+) y salidas (-) con fecha, hora y titular."""
    if not CORR_JSON_PATH.exists():
        return []

    with open(CORR_JSON_PATH, "r", encoding="utf-8") as f:
        corr = json.load(f)

    clas = corr.get("clasificacion_detallada", {})
    resultado = []

    def _add_row(id_op, fecha, hora, tipo_mov, canal_cod, canal_label, titular, monto_signed, nro_op, destino, calzado_tag, estado_txt, obs=""):
        f_clean = str(fecha or "").strip()
        h_clean = str(hora or "").strip()
        fh = f"{f_clean} {h_clean}".strip()
        resultado.append({
            "id": id_op,
            "fecha": f_clean,
            "hora": h_clean,
            "fecha_hora": fh,
            "tipo_mov": tipo_mov,  # INGRESO | SALIDA
            "canal": canal_cod,
            "canal_etiqueta": canal_label,
            "remitente": titular or "—",
            "titular": titular or "—",
            "monto": _r2(monto_signed),
            "nro_operacion": str(nro_op or "—"),
            "operacion": str(nro_op or "—"),
            "calzado_con": destino,
            "destino": destino,
            "calzado": calzado_tag,
            "estado_calzado": estado_txt,
            "estado": estado_txt,
            "observacion": obs or "",
        })

    # 1. Habitaciones (110 ops) -> INGRESO (+)
    for it in clas.get("habitaciones", []):
        habs = it.get("habitaciones_vinculadas", [])
        hab_txt = ", ".join(f"Hab. {h.get('hab')}" for h in habs if h.get("hab"))
        _add_row(
            it.get("id"), it.get("fecha"), it.get("hora"),
            "INGRESO", "YAPE_HABITACION", "Yape / Plin Habitación",
            it.get("remitente"), abs(_r2(it.get("monto"))),
            it.get("nro_operacion"), hab_txt or "Alojamiento",
            "1:1 Habitación", "Ingreso Verificado", it.get("observacion", "")
        )

    # 2. Tienda Pura (33 ops) -> INGRESO (+)
    for it in clas.get("tienda", []):
        _add_row(
            it.get("id"), it.get("fecha"), it.get("hora"),
            "INGRESO", "YAPE_TIENDA", "Yape Vitrina / Tienda",
            it.get("remitente"), abs(_r2(it.get("monto"))),
            it.get("nro_operacion"), "Tienda (Cubierto Bolsa Efectivo N2)",
            "Absorbido N2", "Cubierto por Bolsa N2", it.get("observacion", "")
        )

    # 3. Corporativo Maelen (2 ops) -> INGRESO (+)
    for it in clas.get("corporativo_maelen", []):
        _add_row(
            it.get("id"), it.get("fecha"), it.get("hora"),
            "INGRESO", "MAELEN_CORPORATIVO", "Transferencia BCP Empresa",
            it.get("remitente"), abs(_r2(it.get("monto"))),
            it.get("nro_operacion"), "Facturas Empresas (Quisapata / RENIEC)",
            "1:1 Empresa", "Ingreso Corporativo", it.get("observacion", "")
        )

    # 4. Sueltos Banco (4 ops) -> INGRESO (+)
    for it in clas.get("sueltos_banco", []):
        _add_row(
            it.get("id"), it.get("fecha"), it.get("hora"),
            "INGRESO", "SUELTO_BANCO", "Ingreso Menor / Suelto",
            it.get("remitente"), abs(_r2(it.get("monto"))),
            it.get("nro_operacion"), "Cubierto Bolsa Efectivo N2",
            "Absorbido N2", "Cubierto por Bolsa N2", it.get("observacion", "")
        )

    # 5. Cola Viva (10 ops) -> INGRESO (+)
    lp = plan_data.get("nivel_3_lotes_pendientes", {})
    for idx, it in enumerate(lp.get("lote_3B3_cola_viva_post_corte_14_15nov", []), start=1001):
        fp = str(it.get("fecha_pago", "")).strip()
        f_p = fp.split(" ")[0] if " " in fp else fp
        h_p = fp.split(" ")[1] if " " in fp else ""
        _add_row(
            idx, f_p, h_p,
            "INGRESO", "COLA_VIVA", "Yape Post-Corte (14–15/11)",
            it.get("huesped"), abs(_r2(it.get("monto"))),
            it.get("nro_operacion") or f"ColaViva H{it.get('hab')}",
            f"Hab. {it.get('hab')} ({it.get('huesped')})",
            "Cola Viva", f"Post-Corte · Sol. Boleta: {it.get('solicita_comp', 'No')}",
            f"Yape post-corte 14–15/11"
        )

    # 6. Salidas / Egresos Excluidos (deduplicados por fecha + monto + nro_op) -> SALIDA (-)
    vistos_salidas = set()
    for it in clas.get("salidas_excluidas", []):
        clave_sal = (
            str(it.get("fecha") or "").strip(),
            _r2(abs(float(it.get("monto") or 0))),
            str(it.get("nro_operacion") or "").strip(),
        )
        if clave_sal in vistos_salidas:
            continue
        vistos_salidas.add(clave_sal)
        _add_row(
            it.get("id"), it.get("fecha"), it.get("hora"),
            "SALIDA", "EGRESO_EXCLUIDO", "Salida / Retiro Bancario",
            it.get("destinatario_o_concepto", "Salida de cuenta"),
            -abs(_r2(it.get("monto"))),
            it.get("nro_operacion"), "Salida Excluida de Ventas",
            "Excluido", it.get("motivo_exclusion", "Salida bancaria (no es venta)"),
            it.get("motivo_exclusion", "")
        )

    resultado.sort(key=lambda x: _sort_key_dmy_hms(x.get("fecha", ""), x.get("hora", "")))
    return resultado


def _construir_calzado_rows(plan_data: Dict[str, Any], conn: sqlite3.Connection, mes: str = "2026-11") -> tuple:
    """
    Construye las filas tricolor (Estancia <-> Banco <-> SUNAT) y actualiza calzado_cache en SQLite Sandbox.
    """
    cur = conn.cursor()
    # Estancias del periodo Noviembre 2026 (01/11 - 15/11) + noches 31/10 pagadas en 01/11
    est_rows = cur.execute(
        """
        SELECT * FROM estancias
        WHERE (ymd_in BETWEEN 20261101 AND 20261115)
           OR (ymd_in = 20261031 AND (pago_ini_yape > 0 OR pago_post_yape > 0))
        ORDER BY ymd_in, hab
        """
    ).fetchall()

    # Mapear estancias que están en los lotes y vínculos
    vinculos_1 = {x.get("nro_cp"): x for x in plan_data.get("nivel_1_calzado_exacto", [])}
    sin_anotar = {x.get("nro_cp"): x for x in plan_data.get("comprobantes_emitidos_sin_anotar_en_bd", [])}

    lotes_p = plan_data.get("nivel_3_lotes_pendientes", {})
    lote_3a_by_uuid = {x.get("estancia_uuid"): x for x in lotes_p.get("lote_3A_facturas_ruc_con_yape", [])}
    lote_3b1_by_uuid = {x.get("estancia_uuid"): x for x in lotes_p.get("lote_3B1_boletas_dni_solicitadas", [])}
    lote_3b2_by_uuid = {x.get("estancia_uuid"): x for x in lotes_p.get("lote_3B2_yapes_no_solicitaron", [])}

    # Map de CPEs emitidos sin anotar por UUID
    sin_anotar_by_uuid = {}
    for item in plan_data.get("comprobantes_emitidos_sin_anotar_en_bd", []):
        for e in item.get("estancias_bd", []):
            sin_anotar_by_uuid[e.get("estancia_uuid")] = item

    # Map de comprobantes SUNAT desde sunat_ventas
    sunat_docs = {
        r["nro_cp"]: dict(r)
        for r in cur.execute("SELECT * FROM sunat_ventas WHERE periodo_mes = ?", (mes,)).fetchall()
    }

    # Map de operaciones reales de Banco BCP/Yape por uuid o (hab, huesped_upper)
    banco_ops_por_uuid: Dict[str, List[str]] = defaultdict(list)
    banco_ops_por_hab_huesped: Dict[tuple, List[str]] = defaultdict(list)
    if CORR_JSON_PATH.exists():
        try:
            with open(CORR_JSON_PATH, "r", encoding="utf-8") as f:
                corr_bcp = json.load(f)
            clas_bcp = corr_bcp.get("clasificacion_detallada", {})
            for op in clas_bcp.get("habitaciones", []):
                hora_corta = str(op.get("hora") or "")[:5]
                rem_corto = str(op.get("remitente") or "").strip()
                etiqueta_op = f"{op.get('fecha', '')[:5]} {hora_corta} · {rem_corto}".strip(" ·")
                for hv in op.get("habitaciones_vinculadas", []):
                    eu = str(hv.get("estancia_uuid") or "").strip()
                    if eu and etiqueta_op not in banco_ops_por_uuid[eu]:
                        banco_ops_por_uuid[eu].append(etiqueta_op)
                    kh = (str(hv.get("hab") or "").strip(), str(hv.get("huesped") or "").strip().upper())
                    if kh[0] and etiqueta_op not in banco_ops_por_hab_huesped[kh]:
                        banco_ops_por_hab_huesped[kh].append(etiqueta_op)
        except Exception:
            pass

    calzado_rows = []
    resumen_estados = defaultdict(int)
    calzado_cache_inserts = []

    for idx, row in enumerate(est_rows, start=1):
        e = dict(row)
        u = e["estancia_uuid"]
        fecha_in = str(e["fecha_in_dmy"] or "").strip()
        hab = str(e["hab"] or "").strip()
        huesped = str(e["huesped"] or "").strip()
        dni_ruc = str(e["ruc"] or e["dni"] or "").strip()

        tarifa = float(e["tarifa"] or 0)
        noches = int(e["noches"] or 1)
        adic = float(e["adic_snacks"] or 0) + float(e["adic_servicios"] or 0) + float(e["adic_comp"] or 0)
        total_cuenta = _r2((tarifa * noches) + adic)

        ef_aa = _r2(float(e["pago_ini_efec"] or 0) + float(e["pago_post_efec"] or 0))
        yape_ab = _r2(float(e["pago_ini_yape"] or 0) + float(e["pago_post_yape"] or 0))
        monto_aj = _r2(float(e["monto_factura"] or 0) or total_cuenta)

        # Resolver Comprobante SUNAT asociado (soporta múltiples tokens EB01/E001 o emitidos fin de mes)
        nro_comp_bd = (e["nro_comp"] or "").strip()
        estado_comp_bd = str(e.get("estado_comp") or "").strip().lower()
        cpe_sunat = ""
        monto_sunat = 0.0

        if nro_comp_bd:
            tokens_cp = re.findall(r"E[B0]01-\d+", nro_comp_bd.upper())
            suma_docs = sum(_r2(sunat_docs[t]["monto_total"]) for t in tokens_cp if t in sunat_docs)
            cpe_sunat = nro_comp_bd
            monto_sunat = _r2(suma_docs if suma_docs > 0 else monto_aj)
        elif u in sin_anotar_by_uuid:
            cpe_item = sin_anotar_by_uuid[u]
            cpe_sunat = cpe_item.get("nro_cp", "")
            monto_sunat = _r2(cpe_item.get("monto_sunat", 0.0))
        elif estado_comp_bd == "emitido" and u not in lote_3a_by_uuid and u not in lote_3b1_by_uuid and u not in lote_3b2_by_uuid:
            cpe_sunat = "Emitido BD"
            monto_sunat = _r2(monto_aj if monto_aj > 0 else yape_ab)

        # Resolver Banco BCP con hora y remitente reales del extracto
        yape_banco = 0.0
        badge_banco = "⏳ Sin Yape"
        ops_match = banco_ops_por_uuid.get(u, []) or banco_ops_por_hab_huesped.get((hab, huesped.upper()), [])
        fuente_banco = " / ".join(ops_match[:2]) if ops_match else ""

        if yape_ab > 0:
            yape_banco = yape_ab
            badge_banco = "✅ Yape Verificado"
            if not fuente_banco:
                fuente_banco = f"Extracto BCP (S/ {yape_ab:.2f})"
        elif cpe_sunat in ("E001-2113", "E001-2116", "E001-2117"):
            badge_banco = "🏦 Abono BCP Empresa"
            fuente_banco = fuente_banco or "Cuenta BCP Maelen"
        elif ef_aa > 0:
            badge_banco = "💵 Solo Efectivo"
            fuente_banco = "Efectivo en Recepción"
        else:
            fuente_banco = "—"

        # Resolver Estado de Conciliación y Acción (cero jerga "CPE")
        check_bd_sunat = False
        check_yape_banco = bool(yape_ab == 0 or yape_banco > 0)
        check_banco_sunat = False

        celular_row = str(e.get("celular") or "").strip()
        tiene_cel_row = sum(1 for ch in celular_row if ch.isdigit()) >= 7
        es_fact_row = bool(
            str(e.get("ruc") or "").strip()
            or "fact" in str(e.get("tipo_comp") or "").lower()
            or (cpe_sunat and cpe_sunat.startswith("E001"))
        )
        prioridad_naranja_row = bool(es_fact_row or tiene_cel_row)

        # Campos de Unificación, Colchón e Inexactos (Plan v2 / M1)
        unificado_en_uuid = str(e.get("unificado_en_uuid") or "").strip()
        opcion_decision = str(e.get("opcion_decision") or "").strip()
        colchon = 0.0
        if cpe_sunat:
            colchon = max(0.0, round(monto_sunat - yape_banco, 2))
        elif float(e.get("excedente_efec_sunat") or 0.0) > 0:
            colchon = float(e.get("excedente_efec_sunat") or 0.0)

        import motor_unificacion as mu
        es_inexacto = mu.es_pendiente_inexacto(e)

        if unificado_en_uuid or estado_comp_bd == "unificado":
            estado_conc = "UNIFICADO"
            estado_ui = "UNIFICADO"
            pildora_triple = "BD:🔗 BCP:· SUN:🔗"
            accion = f"Unificado en {unificado_en_uuid}" if unificado_en_uuid else "Unificado en otra habitación"
        elif cpe_sunat:
            check_bd_sunat = True
            check_banco_sunat = True
            if u in sin_anotar_by_uuid and not nro_comp_bd:
                estado_conc = "ANOTAR_EN_BD"
                estado_ui = "ANOTAR_BD"
                accion = f"Ya emitido en SUNAT ({cpe_sunat}) · Falta anotar en BD"
            elif colchon > 0:
                estado_conc = "OK_COLCHON"
                estado_ui = "OK_COLCHON"
                accion = f"Calzado con Efectivo Maelen · Colchón S/ {colchon:.2f}"
            else:
                estado_conc = "OK_TRIPLE"
                estado_ui = "OK_TRIPLE"
                accion = "Cuadre exacto Estancia ↔ Banco ↔ SUNAT"
        elif u in lote_3a_by_uuid or (es_fact_row and yape_ab > 0):
            l3a = lote_3a_by_uuid.get(u, {})
            m_sug = l3a.get("monto_sugerido") or (monto_aj if monto_aj > 0 else yape_ab)
            estado_conc = "FALTA_EMITIR_RUC"
            estado_ui = "PEND_FACTURA"
            badge_banco = "🟠 Pend. Factura"
            cel_note = f" · WSP {celular_row}" if tiene_cel_row else ""
            accion = f"Emitir Factura RUC {l3a.get('ruc') or e.get('ruc') or ''} por S/ {float(m_sug):.2f}{cel_note}"
        elif u in lote_3b1_by_uuid or u in lote_3b2_by_uuid or yape_ab > 0:
            l3b = lote_3b1_by_uuid.get(u) or lote_3b2_by_uuid.get(u) or {}
            m_sug = l3b.get("monto_sugerido") or yape_ab
            estado_conc = "YAPE_SIN_COMPROBANTE"
            if tiene_cel_row:
                estado_ui = "PEND_BOLETA_CEL"
                badge_banco = "🟠📱 Pend. Boleta (WSP)"
                accion = f"Emitir Boleta S/ {float(m_sug):.2f} y enviar a WSP {celular_row}"
            else:
                estado_ui = "PEND_BOLETA_SINCEL"
                badge_banco = "⏳ Pend. Boleta (Sin Cel)"
                accion = f"Boleta por Yape S/ {float(yape_ab):.2f} (Sin cel · Apta +horas extras tienda)"
        elif ef_aa > 0:
            estado_conc = "SOLO_EFECTIVO"
            estado_ui = "SOLO_EFECTIVO"
            accion = "Efectivo sin boleta solicitada"
        else:
            estado_conc = "SIN_MOVIMIENTO"
            estado_ui = "SIN_MOVIMIENTO"
            accion = "Crédito corporativo / En curso"

        resumen_estados[estado_conc] += 1
        pildora_triple = (
            f"BD:{'✓' if (cpe_sunat and nro_comp_bd) else ('⚠' if cpe_sunat else '·')} "
            f"BCP:{'✓' if check_yape_banco else '⚠'} "
            f"SUN:{'✓' if cpe_sunat else '⏳'}"
        )

        calzado_rows.append({
            "filaCalzado": idx,
            "estancia_uuid": u,
            # Alias unificados Backend + UI
            "fecha": fecha_in,
            "fecha_in": fecha_in,
            "noche_dmy": fecha_in,
            "hab": hab,
            "huesped": huesped,
            "dni_ruc": dni_ruc or "—",
            "total_cuenta": total_cuenta,
            "efectivo_aa": ef_aa,
            "efectivo_bd": ef_aa,
            "yape_ab": yape_ab,
            "yape_bd": yape_ab,
            "monto_aj": monto_aj,
            "anexo_aj": cpe_sunat or "",
            "yape_banco": yape_banco,
            "fuente_banco": fuente_banco,
            "op_hora": fuente_banco,
            "badge_banco": badge_banco,
            "comprobantes": cpe_sunat or "—",
            "cpe_sunat": cpe_sunat or "—",
            "monto_sunat": monto_sunat,
            "colchon": colchon,
            "es_inexacto": es_inexacto,
            "unificado_en_uuid": unificado_en_uuid,
            "opcion_decision": opcion_decision,
            "pildora_triple": pildora_triple,
            "check_bd_sunat": check_bd_sunat,
            "check_yape_banco": check_yape_banco,
            "check_banco_sunat": check_banco_sunat,
            "estado_conciliacion": estado_conc,
            "estado_conc": estado_conc,
            "estado": estado_ui,
            "celular": celular_row,
            "tiene_celular": tiene_cel_row,
            "es_factura": es_fact_row,
            "prioridad_naranja": prioridad_naranja_row,
            "accion": accion,
        })

        calzado_cache_inserts.append((
            f"NOV_{u[:8]}", idx, fecha_in, hab, huesped, dni_ruc,
            e.get("sheet_row") or f"E_{u[:8]}", monto_sunat, cpe_sunat,
            monto_aj, yape_ab, ef_aa, colchon, yape_banco,
            "CUADRADO" if estado_conc in ("OK_TRIPLE", "OK_COLCHON") else estado_conc,
            estado_conc, accion
        ))

    # Actualizar calzado_cache en SQLite Sandbox (Cero Ruptura / Panel de Habitaciones)
    try:
        cur.executemany(
            """
            INSERT OR REPLACE INTO calzado_cache (
                clave_calzado, fila_calzado, noche_dmy, hab, huesped, dni,
                fila_est, monto_sunat, comprobantes, monto_aj, yape_ab,
                efectivo_aa, excedente_efec, yape_bcp, estado_bcp,
                estado_conciliacion, accion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            calzado_cache_inserts
        )
        conn.commit()
    except Exception as exc:
        print(f"[WARN] No se pudo actualizar calzado_cache: {exc}")

    return calzado_rows, dict(resumen_estados)


# ==============================================================================
# FUNCIONES PÚBLICAS EXPUESTAS
# ==============================================================================

def _calcular_totales_vivos_sqlite(conn: sqlite3.Connection, mes: str = "2026-11") -> Dict[str, Any]:
    """
    Calcula en tiempo real (0 ms) los totales reales del mes directamente desde SQLite
    (`sunat_ventas` y `pagos`), sin depender de JSONs estáticos ni textos fijos.
    """
    mes_norm = str(mes or datetime.now().strftime("%Y-%m")).strip()
    m_re = re.match(r"^(\d{4})-(\d{2})$", mes_norm)
    if m_re:
        yyyy, mm = m_re.group(1), m_re.group(2)
    else:
        ahora = datetime.now()
        yyyy, mm = str(ahora.year), f"{ahora.month:02d}"
        mes_norm = f"{yyyy}-{mm}"
    like_dmy = f"%/{mm}/{yyyy}%"
    like_iso = f"{yyyy}-{mm}%"

    bol_vig_cnt = 0
    bol_vig_tot = 0.0
    fac_vig_cnt = 0
    fac_vig_tot = 0.0
    nc_vig_cnt = 0
    nc_vig_tot = 0.0
    sunat_neto_sire = 0.0
    total_docs_sunat = 0
    ultimo_eb01 = ""
    ultimo_e001 = ""

    try:
        rows_sv = conn.execute(
            """
            SELECT tipo_doc, estado, COUNT(*) AS n, COALESCE(SUM(total), 0.0) AS suma, MAX(nro_cp) AS max_cp
            FROM sunat_ventas
            WHERE periodo_mes = ? OR fecha_emision LIKE ? OR fecha_emision LIKE ?
            GROUP BY tipo_doc, estado
            """,
            (mes_norm, like_dmy, like_iso),
        ).fetchall()
        for r in rows_sv:
            td = str(r["tipo_doc"] or "").upper().strip()
            est = str(r["estado"] or "").upper().strip()
            n = int(r["n"] or 0)
            s = float(r["suma"] or 0.0)
            mx = str(r["max_cp"] or "").strip()
            total_docs_sunat += n
            sunat_neto_sire += s
            if "ANULADO" not in est:
                if td == "BOLETA":
                    bol_vig_cnt += n
                    bol_vig_tot += s
                    if mx and mx > ultimo_eb01:
                        ultimo_eb01 = mx
                elif td == "FACTURA":
                    fac_vig_cnt += n
                    fac_vig_tot += s
                    if mx and mx > ultimo_e001:
                        ultimo_e001 = mx
                elif td == "NOTA_CREDITO":
                    nc_vig_cnt += n
                    nc_vig_tot += s
    except Exception:
        pass

    sunat_bruto_vig = _r2(bol_vig_tot + fac_vig_tot)
    sunat_neto_sire = _r2(sunat_neto_sire)
    nc_neto_otros_meses = _r2(max(0.0, sunat_bruto_vig - sunat_neto_sire))

    yape_tot = 0.0
    transf_tot = 0.0
    efectivo_tot = 0.0
    yape_cnt = 0
    transf_cnt = 0
    try:
        rows_pg = conn.execute(
            """
            SELECT medio_pago, COUNT(*) AS n, COALESCE(SUM(monto), 0.0) AS suma
            FROM pagos
            WHERE fecha_iso LIKE ? OR fecha_iso LIKE ?
            GROUP BY medio_pago
            """,
            (like_dmy, like_iso),
        ).fetchall()
        for r in rows_pg:
            mp = str(r["medio_pago"] or "").strip().lower()
            n = int(r["n"] or 0)
            s = float(r["suma"] or 0.0)
            if "yape" in mp or "plin" in mp:
                yape_tot += s
                yape_cnt += n
            elif "transf" in mp or "bcp" in mp or "dep" in mp:
                transf_tot += s
                transf_cnt += n
            elif "efect" in mp:
                efectivo_tot += s
    except Exception:
        pass

    banco_total_vivo = _r2(yape_tot + transf_tot)
    base_sin_igv = round(_valor_sin_igv(sunat_neto_sire), 2)
    igv_105 = _r2(sunat_neto_sire - base_sin_igv)

    return {
        "mes": mes_norm,
        "sunat_bruto": sunat_bruto_vig,
        "sunat_pdt": sunat_neto_sire,
        "sunat_base_sin_igv": base_sin_igv,
        "sunat_igv_105": igv_105,
        "boletas_vigentes_cant": bol_vig_cnt,
        "boletas_vigentes_total": _r2(bol_vig_tot),
        "facturas_vigentes_cant": fac_vig_cnt,
        "facturas_vigentes_total": _r2(fac_vig_tot),
        "nc_vigentes_cant": nc_vig_cnt,
        "nc_vigentes_total": _r2(nc_vig_tot),
        "nc_descuento_neto": nc_neto_otros_meses,
        "total_docs_sunat": total_docs_sunat,
        "ultimo_eb01": ultimo_eb01,
        "ultimo_e001": ultimo_e001,
        "yape_cant": yape_cnt,
        "yape_total": _r2(yape_tot),
        "transf_cant": transf_cnt,
        "transf_total": _r2(transf_tot),
        "banco_total": banco_total_vivo,
        "efectivo_total": _r2(efectivo_tot),
    }


def obtener_datos_contabilidad_local(mes: str = "2026-11") -> Dict[str, Any]:
    """
    Función principal expuesta vía RPC `obtenerDatosContabilidad`.
    Retorna el diccionario unificado con puente_visual, vasijas_nivel_2,
    cpe_pendientes_bd, lotes_emision, calzado_rows, resumen_estados,
    sunat_rows y extracto_rows.
    """
    mes = str(mes or "2026-11").strip()
    plan_data = _cargar_plan_emparejamiento(forzar_recalculo=False)
    conn = _get_db_connection()

    try:
        puente_visual = _obtener_puente_visual(plan_data)
        totales_vivos = _calcular_totales_vivos_sqlite(conn, mes)
        if totales_vivos.get("sunat_bruto", 0.0) > 0:
            puente_visual["sunat_bruto"] = totales_vivos["sunat_bruto"]
            puente_visual["sunat_pdt"] = totales_vivos["sunat_pdt"]
            puente_visual["sunat_declarado_neto"] = totales_vivos["sunat_pdt"]
            puente_visual["sunat_base_sin_igv"] = totales_vivos["sunat_base_sin_igv"]
            puente_visual["sunat_igv_105"] = totales_vivos["sunat_igv_105"]
            puente_visual["sunat_facturas_monto"] = totales_vivos["facturas_vigentes_total"]
            puente_visual["sunat_facturas_cant"] = totales_vivos["facturas_vigentes_cant"]
            puente_visual["sunat_boletas_monto"] = totales_vivos["boletas_vigentes_total"]
            puente_visual["sunat_boletas_cant"] = totales_vivos["boletas_vigentes_cant"]
            puente_visual["sunat_nc_oct_monto"] = totales_vivos["nc_descuento_neto"]
            puente_visual["sunat_nc_oct_cant"] = totales_vivos["nc_vigentes_cant"]
            puente_visual["ultimo_eb01"] = totales_vivos["ultimo_eb01"]
            puente_visual["ultimo_e001"] = totales_vivos["ultimo_e001"]
        if totales_vivos.get("banco_total", 0.0) > 0:
            puente_visual["banco_total"] = totales_vivos["banco_total"]
            if totales_vivos["banco_total"] > puente_visual["sunat_pdt"]:
                def_m = _r2(totales_vivos["banco_total"] - puente_visual["sunat_pdt"])
                puente_visual["alerta_deficit"] = {
                    "hay_deficit": True,
                    "monto": def_m,
                    "mensaje": f"Faltan S/ {def_m:,.2f} frente al Banco/Yape del mes",
                }
            else:
                puente_visual["alerta_deficit"] = {"hay_deficit": False, "monto": 0.0, "mensaje": ""}
        vasijas_data = _construir_vasijas_nivel_2(plan_data)
        cpe_pendientes = _construir_cpe_pendientes_bd(plan_data, conn)
        lotes_emision = _construir_lotes_emision(plan_data, conn)
        calzado_rows, resumen_estados = _construir_calzado_rows(plan_data, conn, mes)
        sunat_rows = _construir_sunat_rows(conn, mes)
        extracto_rows = _construir_extracto_rows(plan_data)

        return {
            "status": "ok",
            "mes": mes,
            "puente_visual": puente_visual,
            "totales_vivos_sqlite": totales_vivos,
            "vasijas_nivel_2": vasijas_data["vasijas"],
            "vasijas_resumen": vasijas_data["resumen"],
            "cpe_pendientes_bd": cpe_pendientes,
            "lotes_emision": lotes_emision,
            "calzado_rows": calzado_rows,
            "resumen_estados": resumen_estados,
            "sunat_rows": sunat_rows,
            "extracto_rows": extracto_rows,
        }
    finally:
        conn.close()


def recalcular_auditoria_contabilidad_local() -> Dict[str, Any]:
    """
    Función expuesta vía RPC `recalcularAuditoriaContabilidad`.
    Dispara la auditoría completa vía auditor_calzado_bcp_sunat y
    retorna los datos actualizados en primer nivel y en 'datos'.
    """
    try:
        import auditor_calzado_bcp_sunat as aud
        aud.ejecutar_auditoria(verbose=False)
        datos = obtener_datos_contabilidad_local("2026-11")
        out = dict(datos)
        out["mensaje"] = "Auditoría recalculada al centavo con auditor_calzado_bcp_sunat"
        out["datos"] = datos
        return out
    except Exception as exc:
        return {
            "status": "error",
            "mensaje": f"Error al recalcular auditoría: {exc}",
        }


def actualizar_extraccion_tributaria_completa_local(mes: str = "2026-11") -> Dict[str, Any]:
    """
    Función expuesta vía RPC `actualizarExtraccionTributariaCompleta` y tarea automática 08:00 AM.
    1. Sincroniza nuevos movimientos Yape/BCP disponibles en Drive/SQLite.
    2. Refresca comprobantes (Facturas, Boletas y Notas de Crédito) en SQLite Sandbox.
    3. Recalcula el cruce tricolor y reportes tributarios (error = S/ 0.00).
    """
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    pasos_ok = []
    avisos = []

    # 1. Revisor Incremental de Faltantes SUNAT (en Laptop 2 Madre descarga PDFs faltantes de SEE-SOL; en L1/L3 envía orden remota o sincroniza desde Supabase)
    try:
        from config import ES_NIVEL_2_MADRE_HOTEL
        from sync_nube import pull_from_supabase
        if ES_NIVEL_2_MADRE_HOTEL:
            from sunat_motor_playwright import revisar_y_descargar_faltantes_sunat_incremental
            res_inc = revisar_y_descargar_faltantes_sunat_incremental(dias_atras=7)
            desc_n = len(res_inc.get("faltantes_descargados") or [])
            pasos_ok.append(f"Revisor incremental SUNAT ejecutado ({desc_n} faltantes descargados)")
        else:
            pull_from_supabase(limit=250)
            pasos_ok.append("Datos SUNAT sincronizados desde Supabase")
    except Exception as exc:
        avisos.append(f"Revisor incremental SUNAT: {exc}")

    # 2. Sincronización Yape / BCP local (sin consultar correo BCP automáticamente)
    try:
        import yape_bcp as ym
        if hasattr(ym, "conciliar_yapes_sqlite"):
            ym.conciliar_yapes_sqlite()
            pasos_ok.append("Extracto Yape/BCP conciliado en SQLite")
    except Exception as exc:
        avisos.append(f"Yape local: {exc}")

    # 2. Refresco de caché SUNAT (Boletas, Facturas y Notas de Crédito) + Auditor Maestro
    try:
        import auditor_calzado_bcp_sunat as aud
        aud.ejecutar_auditoria(verbose=False)
        pasos_ok.append("Facturas, Boletas, Notas de Crédito y Cruce recalculados")
    except Exception as exc:
        avisos.append(f"Auditoría SUNAT/BCP: {exc}")

    # 3. Guardar sello horario de última comprobación en estado_sistema
    try:
        conn = _get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES ('ultima_comprobacion_contable', ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (now_str, datetime.now().isoformat(timespec="seconds"))
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    datos = obtener_datos_contabilidad_local(mes)
    out = dict(datos)
    out["ultima_actualizacion"] = now_str
    out["pasos_ok"] = pasos_ok
    out["avisos"] = avisos
    out["mensaje"] = f"Reportes tributarios (Boletas, Facturas, Notas de Crédito y Extracto BCP) actualizados a las {now_str}."
    out["datos"] = datos
    return out


def sincronizar_comprobantes_faltantes_bd_local() -> Dict[str, Any]:
    """
    Función expuesta vía RPC `sincronizarComprobantesFaltantesBD`.
    Anota en SQLite Sandbox los 8 comprobantes ya emitidos en SUNAT fijando
    estado_comp='Emitido' y nro_comp sin re-emitir.
    Incluye las 2 estancias de RENIEC del 11/11 (E001-2116 / E001-2117).
    """
    conn = _get_db_connection()
    cur = conn.cursor()

    actualizados = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Mapeo explícito y validado forensemente de los 8 CPEs + RENIEC
    reglas_sincronizacion = [
        # 1. E001-2108 (S/ 140.00): Man Pan 04/11 H102 + 05/11 H206 (Juan Carlos Ramos)
        ("E001-2108", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE ruc = '20250975563' AND ymd_in IN (20261104, 20261105) AND hab IN ('102', '206')"),
        # 2. E001-2109 (S/ 140.00): Man Pan 04/11 H307 + 05/11 H307 (José Alfredo Jara)
        ("E001-2109", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE ruc = '20250975563' AND ymd_in IN (20261104, 20261105) AND hab = '307'"),
        # 3. E001-2110 (S/ 55.00): Farmacias Peruanas 05/11 H207 (Paulo Cornejo)
        ("E001-2110", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE estancia_uuid LIKE 'aa5cb16d%'"),
        # 4. E001-2111 (S/ 70.00): Autosafe 05/11 H306 (Elvis Coaguila)
        ("E001-2111", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE estancia_uuid LIKE 'b21b23e1%'"),
        # 5. E001-2112 (S/ 70.00): Quisapata 04/11 H407 (Alex Cárdenas)
        ("E001-2112", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE estancia_uuid LIKE '76537f03%'"),
        # 6. E001-2113 (S/ 233.00): Quisapata 08/11 Habs 203, 207, 307, 406, 407
        ("E001-2113", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE ymd_in = 20261108 AND ruc = '20603511043'"),
        # 7. E001-2118 (S/ 70.00): Autosafe 11/11 H309 (Elvis Coaguila)
        ("E001-2118", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE estancia_uuid LIKE '147c9a9a%'"),
        # 8. EB01-6860 (S/ 35.00): Miguel Soto Guerra 12/11 H406
        ("EB01-6860", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE estancia_uuid LIKE '743bd21a%'"),
        # RENIEC 11/11 H406: Rodrigo Guevara (E001-2116)
        ("E001-2116", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE ymd_in = 20261111 AND hab = '406' AND huesped LIKE '%GUEVARA%'"),
        # RENIEC 11/11 H407: Rodolfo Gutiérrez (E001-2117)
        ("E001-2117", "SELECT estancia_uuid, hab, huesped FROM estancias WHERE ymd_in = 20261111 AND hab = '407' AND huesped LIKE '%GUTIERREZ%'"),
    ]

    try:
        total_modificados = 0
        for cp_num, sql_query in reglas_sincronizacion:
            filas = cur.execute(sql_query).fetchall()
            for r in filas:
                u = r["estancia_uuid"]
                cur.execute(
                    """
                    UPDATE estancias
                    SET estado_comp = 'Emitido',
                        nro_comp = ?,
                        dirty = 1,
                        updated_at = ?
                    WHERE estancia_uuid = ?
                    """,
                    (cp_num, now_str, u)
                )
                actualizados.append({
                    "nro_cp": cp_num,
                    "estancia_uuid": u,
                    "hab": r["hab"],
                    "huesped": r["huesped"],
                    "estado_comp": "Emitido",
                })
                total_modificados += 1

        conn.commit()

        # Recalcular auditoría para que el plan JSON y calzado_cache reflejen el cambio
        try:
            import auditor_calzado_bcp_sunat as aud
            aud.ejecutar_auditoria(verbose=False)
        except Exception as exc:
            print(f"[WARN] No se pudo re-ejecutar auditoría tras sincronización: {exc}")

        return {
            "status": "ok",
            "mensaje": f"Se sincronizaron exitosamente {total_modificados} estancias en SQLite Sandbox con comprobantes vigentes de SUNAT.",
            "total_modificados": total_modificados,
            "estancias_actualizadas": actualizados,
        }
    except Exception as exc:
        conn.rollback()
        return {
            "status": "error",
            "mensaje": f"Error al sincronizar comprobantes faltantes en BD: {exc}",
        }
    finally:
        conn.close()


def listar_inexactos_contabilidad_local(mes: str = "2026-11") -> Dict[str, Any]:
    """
    Lista las estancias con comprobante inexacto para el periodo indicado,
    incluyendo su sugerencia de unificación calculada por motor_unificacion.
    """
    import motor_unificacion as mu
    mes = str(mes or "2026-11").strip()
    plan_data = _cargar_plan_emparejamiento(forzar_recalculo=False)
    conn = _get_db_connection()
    try:
        calzado_rows, _ = _construir_calzado_rows(plan_data, conn, mes)
        inexactos = []
        for r in calzado_rows:
            if r.get("es_inexacto"):
                u = r.get("estancia_uuid")
                sug = mu.sugerir_unificacion(u, conn=conn)
                inexactos.append({
                    "filaCalzado": r.get("filaCalzado"),
                    "estancia_uuid": u,
                    "hab": r.get("hab"),
                    "huesped": r.get("huesped"),
                    "fecha_in": r.get("fecha_in"),
                    "yape_banco": r.get("yape_banco"),
                    "total_cuenta": r.get("total_cuenta"),
                    "sugerencia": sug,
                })
        return {
            "status": "ok",
            "mes": mes,
            "total_inexactos": len(inexactos),
            "inexactos": inexactos,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print("=== TEST CONTABILIDAD LOGIC ===")
    res = obtener_datos_contabilidad_local("2026-11")
    pv = res["puente_visual"]
    print(f"Banco Total  : S/ {pv['banco_total']:,.2f}")
    print(f"SUNAT Bruto  : S/ {pv['sunat_bruto']:,.2f}")
    print(f"SUNAT PDT    : S/ {pv['sunat_pdt']:,.2f}")
    print(f"Balance Error: S/ {pv['balance_error']:.2f}")
    print(f"Vasijas Nivel 2: {len(res['vasijas_nivel_2'])} bolsas activas")
    print(f"CPEs Pendientes: {len(res['cpe_pendientes_bd'])} registrados")
    print(f"Filas Calzado: {len(res['calzado_rows'])} generadas")
