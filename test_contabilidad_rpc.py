#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de verificación integral de Contabilidad Logic y RPCs en Sandbox (puerto 8082).
Verifica identidades contables con error S/ 0.00 y las 3 rutas RPC.
"""

import sys
from pathlib import Path

SANDBOX_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SANDBOX_DIR))

from fastapi.testclient import TestClient
import main
import contabilidad_logic as cl


def test_contabilidad_directo():
    print("--- Probando contabilidad_logic.py directamente ---")
    datos = cl.obtener_datos_contabilidad_local("2026-11")
    assert datos["status"] == "ok", "Status no es ok"

    pv = datos["puente_visual"]
    print(f"Banco BCP Total   : S/ {pv['banco_total']:,.2f}")
    print(f"Cola Viva Post-Corte: S/ {pv['cola_viva']:,.2f}")
    print(f"SUNAT Bruto       : S/ {pv['sunat_bruto']:,.2f}")
    print(f"SUNAT PDT Neto    : S/ {pv['sunat_pdt']:,.2f}")
    print(f"Balance Error     : S/ {pv['balance_error']:.2f}")

    # Identidades matemáticas
    assert abs(pv["banco_total"] - 11767.10) < 0.01, f"Banco total erróneo: {pv['banco_total']}"
    assert abs(pv["cola_viva"] - 760.00) < 0.01, f"Cola viva errónea: {pv['cola_viva']}"
    assert abs(pv["banco_calzado"] - 9285.50) < 0.01, f"Banco calzado erróneo: {pv['banco_calzado']}"
    assert abs(pv["banco_absorbido_n2"] - 172.80) < 0.01, f"Absorbido N2 erróneo: {pv['banco_absorbido_n2']}"
    assert abs(pv["banco_lotes_3ab"] - 1973.80) < 0.01, f"Lotes 3AB erróneo: {pv['banco_lotes_3ab']}"
    assert abs(pv["banco_no_solicita"] - 335.00) < 0.01, f"Banco no solicita erróneo: {pv['banco_no_solicita']}"

    assert abs(pv["sunat_bruto"] - 11500.50) < 0.01, f"SUNAT bruto erróneo: {pv['sunat_bruto']}"
    assert abs(pv["sunat_pdt"] - 11300.50) < 0.01, f"SUNAT pdt erróneo: {pv['sunat_pdt']}"
    assert abs(pv["sunat_facturas_monto"] - 5673.00) < 0.01, f"Facturas erróneo: {pv['sunat_facturas_monto']}"
    assert abs(pv["sunat_boletas_monto"] - 5827.50) < 0.01, f"Boletas erróneo: {pv['sunat_boletas_monto']}"
    assert abs(pv["sunat_nc_oct_monto"] - 200.00) < 0.01, f"NCs erróneo: {pv['sunat_nc_oct_monto']}"

    assert abs(pv["cierre_A"]["sunat_proyectado"] - 13893.30) < 0.01
    assert abs(pv["cierre_A"]["diferencia_banco"] - 2126.20) < 0.01
    assert abs(pv["cierre_B"]["sunat_proyectado"] - 13558.30) < 0.01
    assert abs(pv["cierre_B"]["diferencia_banco"] - 1791.20) < 0.01
    assert abs(pv["cierre_C"]["sunat_proyectado"] - 14653.30) < 0.01
    assert abs(pv["cierre_C"]["meta_banco"] - 12527.10) < 0.01

    assert abs(pv["colchon_fiscal"] - 2215.00) < 0.01
    assert abs(pv["balance_error"]) < 0.01, f"Balance error distinto de 0: {pv['balance_error']}"

    # Vasijas Nivel 2
    vas = datos["vasijas_nivel_2"]
    res_vas = datos["vasijas_resumen"]
    print(f"Vasijas Nivel 2   : {len(vas)} bolsas activas")
    print(f"Total absorbido   : S/ {res_vas['total_tienda_pura_y_sueltos']:.2f}")
    assert len(vas) == 8, f"Se esperaban 8 vasijas, hay {len(vas)}"
    assert abs(res_vas["total_tienda_pura_y_sueltos"] - 172.80) < 0.01
    assert res_vas["remanentes_sin_cubrir"] == 0
    assert res_vas["boletas_adicionales_requeridas"] == 0

    # CPEs pendientes BD
    cpes = datos["cpe_pendientes_bd"]
    print(f"CPEs pendientes   : {len(cpes)} registrados (8 CPEs + RENIEC)")
    assert len(cpes) >= 8

    # Lotes emisión
    lotes = datos["lotes_emision"]
    tot_l = lotes["totales_lotes"]
    print(f"Lote 3A Yape      : S/ {tot_l['lote_3A_yape']:.2f} -> Emitir: S/ {tot_l['lote_3A_emitir']:.2f}")
    print(f"Lote 3B.1 Yape    : S/ {tot_l['lote_3B1_yape']:.2f} -> Emitir: S/ {tot_l['lote_3B1_emitir']:.2f}")
    print(f"Lote 3B.2 Yape    : S/ {tot_l['lote_3B2_yape']:.2f}")
    print(f"Cola Viva 3B.3    : S/ {tot_l['lote_3B3_cola_viva']:.2f}")
    assert abs(tot_l["lote_3A_yape"] - 408.80) < 0.01
    assert abs(tot_l["lote_3A_emitir"] - 471.80) < 0.01
    assert abs(tot_l["lote_3B1_yape"] - 1565.00) < 0.01
    assert abs(tot_l["lote_3B1_emitir"] - 1786.00) < 0.01
    assert abs(tot_l["lote_3B2_yape"] - 335.00) < 0.01
    assert abs(tot_l["lote_3B3_cola_viva"] - 760.00) < 0.01

    # Calzado rows, SUNAT rows, Extracto rows
    print(f"Calzado rows      : {len(datos['calzado_rows'])} filas")
    print(f"SUNAT rows        : {len(datos['sunat_rows'])} comprobantes")
    print(f"Extracto rows     : {len(datos['extracto_rows'])} movimientos")
    assert len(datos["calzado_rows"]) > 0
    assert len(datos["sunat_rows"]) == 108
    assert len(datos["extracto_rows"]) == 180

    print("✅ Prueba directa de contabilidad_logic.py: PASÓ CON ÉXITO")


def test_rpcs_fastapi():
    print("\n--- Probando llamadas RPC vía FastAPI TestClient ---")
    client = TestClient(main.app, client=("127.0.0.1", 50000))

    # 1. obtenerDatosContabilidad
    print("1. POST /api/rpc/obtenerDatosContabilidad...")
    r1 = client.post("/api/rpc/obtenerDatosContabilidad", json={"args": ["2026-11"]})
    assert r1.status_code == 200, f"Error HTTP {r1.status_code}: {r1.text}"
    body1 = r1.json()
    assert "result" in body1
    res1 = body1["result"]
    assert res1["status"] == "ok"
    assert abs(res1["puente_visual"]["balance_error"]) < 0.01
    print("   -> OK 200: Puente visual y datos completos recibidos.")

    # 2. recalcularAuditoriaContabilidad
    print("2. POST /api/rpc/recalcularAuditoriaContabilidad...")
    r2 = client.post("/api/rpc/recalcularAuditoriaContabilidad", json={"args": []})
    assert r2.status_code == 200, f"Error HTTP {r2.status_code}: {r2.text}"
    body2 = r2.json()
    assert body2["result"]["status"] == "ok"
    print("   -> OK 200: Auditoría recalculada exitosamente.")

    # 3. sincronizarComprobantesFaltantesBD
    print("3. POST /api/rpc/sincronizarComprobantesFaltantesBD...")
    r3 = client.post("/api/rpc/sincronizarComprobantesFaltantesBD", json={"args": []})
    assert r3.status_code == 200, f"Error HTTP {r3.status_code}: {r3.text}"
    body3 = r3.json()
    assert body3["result"]["status"] == "ok"
    print(f"   -> OK 200: Sincronización exitosa ({body3['result']['total_modificados']} estancias actualizadas).")

    print("\n✅ TODAS LAS PRUEBAS RPC PASARON SATISFACTORIAMENTE (Error S/ 0.00).")


if __name__ == "__main__":
    test_contabilidad_directo()
    test_rpcs_fastapi()
