#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_motor_unificacion.py
Suite de Pruebas Automatizadas para el Motor de Sugerencia de Unificación y Cruce Tricolor.
Ambiente: Ovalo_Mariategui_PRUEBA (:8082).
Valida el 100% de los 6 Casos Dorados definidos en implementation_plan_unificacion.md v2:

1. Caso 1: Lourdes 02/11 Hab 105 -> OK_COLCHON con colchón 70.00 (de 62.50 + 7.50).
2. Caso 2: Susan 04/11 Hab 302 -> B/T solo con receptora válida; si no, C 50 (colchón 25)
   por encima de A 75 (colchón 50). En modo booster (modo_mas_boletas=True), A sobre C.
3. Caso 3: Fiorela 04/11 Hab 304 (emitida con EB01-6816) -> nunca es candidata receptora.
4. Caso 4: Receptora del día anterior con fechas concordantes: ítem 1 fecha de estancia (03/11)
   + ítem 2 horas extras con fecha del Yape donante (04/11).
5. Caso 5: Yapes de tienda 15 + 15 -> horas extras 30 dentro del rango de la receptora.
6. Caso 6: Mínimo S/ 35, Yape < 10 absorbido por Nivel 2, Yape dentro de rango sin sugerencia.
   Regla de Tabla 1.1: Habitación doble con baño compartido ocupada por 1 persona
   (n_acomp vacío o '0') aplica rango de simple con baño privado (45–60 Boleta, máx 70 Factura).
"""

import os
import sys
import json
import sqlite3
import unittest
from datetime import datetime

# Asegurar importación de módulos del proyecto C:\ovalo_mariategui_prueba
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import database
import motor_unificacion as mu
import contabilidad_logic as cl


def _crear_conexion_en_memoria() -> sqlite3.Connection:
    """
    Crea una conexión SQLite en memoria clonando el esquema DDL exacto
    de la base de datos real ovalo_sistema.db para pruebas aisladas.
    """
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row
    real_conn = database.get_connection()
    try:
        cur = real_conn.cursor()
        rows = cur.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table', 'index')"
        ).fetchall()
        for r in rows:
            try:
                mem_conn.execute(r["sql"])
            except Exception:
                pass
        mem_conn.commit()
    finally:
        real_conn.close()
    return mem_conn


def _insertar_estancia_mock(conn: sqlite3.Connection, datos: dict) -> str:
    """Inserta un registro en la tabla estancias respetando las columnas exactas del esquema."""
    defaults = {
        "estancia_uuid": "mock-" + str(abs(hash(str(datos)))),
        "id_timestamp_str": datetime.now().isoformat(),
        "sheet_row": 1,
        "parent_uuid": "",
        "fecha_in_dmy": "04/11/2026",
        "fecha_out_dmy": "05/11/2026",
        "ymd_in": "2026-11-04",
        "ymd_out": "2026-11-05",
        "hora_registro": "14:00",
        "huesped": "HUESPED DE PRUEBA",
        "dni": "00000000",
        "sexo": "M",
        "n_acomp": "0",
        "dni_acomp": "",
        "nac": "Peruano",
        "reg": "Tacna",
        "mot": "Vacaciones",
        "hab": "101",
        "tipo_hab": "Simple con baño privado",
        "visita": "Primera vez",
        "noches": 1,
        "tarifa": 55.0,
        "adic_snacks": 0.0,
        "adic_servicios": 0.0,
        "adic_comp": 0.0,
        "pago_ini_efec": 0.0,
        "pago_ini_yape": 0.0,
        "pago_post_efec": 0.0,
        "pago_post_yape": 0.0,
        "pago_visa": 0.0,
        "deuda_anterior": 0.0,
        "cuenta_destino": "Caja",
        "solicita_comp": "Boleta",
        "tipo_comp": "Boleta",
        "estado_comp": "Pendiente",
        "nro_comp": "",
        "monto_factura": 0.0,
        "estado_estancia": "Check-in",
        "empresa": "-",
        "proyecto": "",
        "triple1": "",
        "triple2": "",
        "hotel_apoyo": "",
        "observaciones": "",
        "nro_op_yape": "",
        "celular": "",
        "ruc": "-",
        "excedente_efec_sunat": 0.0,
        "dirty": 0,
        "updated_at": datetime.now().isoformat(),
        "tipo_adicional": "",
        "unificado_en_uuid": "",
        "opcion_decision": "",
    }
    defaults.update(datos)
    # Filtrar solo columnas existentes en estancias
    pragma_cols = [r[1] for r in conn.execute("PRAGMA table_info(estancias)").fetchall()]
    keys = [k for k in defaults.keys() if k in pragma_cols]
    placeholders = ",".join("?" for _ in keys)
    cols = ",".join(keys)
    sql = f"INSERT INTO estancias ({cols}) VALUES ({placeholders})"
    conn.execute(sql, [defaults[k] for k in keys])
    conn.commit()
    return defaults["estancia_uuid"]


class TestMotorUnificacionGoldenCases(unittest.TestCase):
    """Suite de validación de los 6 Casos Dorados de Unificación y Conciliación."""

    def test_caso_1_lourdes_ok_colchon(self):
        """
        Caso 1: Lourdes 02/11 Hab 105 -> OK_COLCHON con colchón 70.00.
        Verifica que en el Cruce Tricolor de Noviembre 2026 la fila de Lourdes
        quede clasificada como OK_COLCHON con colchón fiscal exacto de S/ 70.00.
        """
        # 1. Verificar contabilidad local de noviembre 2026
        datos_contab = cl.obtener_datos_contabilidad_local("2026-11")
        self.assertIn("calzado_rows", datos_contab, "Debe existir calzado_rows en contabilidad")

        lourdes_uuid = "7c462d30-b0aa-445c-831b-7474becec951"
        filas_lourdes = [r for r in datos_contab["calzado_rows"] if r.get("estancia_uuid") == lourdes_uuid]
        self.assertTrue(len(filas_lourdes) > 0, f"No se encontró a Lourdes con UUID {lourdes_uuid} en calzado_rows")

        fila = filas_lourdes[0]
        # Validar estado OK_COLCHON y colchón de S/ 70.00
        self.assertEqual(fila.get("estado_conc"), "OK_COLCHON", "El estado_conc de Lourdes debe ser OK_COLCHON")
        self.assertAlmostEqual(fila.get("colchon"), 70.00, places=2, msg="El colchón fiscal debe ser exactamente S/ 70.00")

        # 2. Verificar datos en base de datos física
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT estancia_uuid, huesped, hab, monto_factura, nro_comp, estado_comp, excedente_efec_sunat "
                "FROM estancias WHERE estancia_uuid = ?",
                (lourdes_uuid,),
            ).fetchone()
            self.assertIsNotNone(row, "Lourdes debe existir en la base de datos")
            d = dict(row)
            self.assertEqual(d["hab"], "105", "Habitación de Lourdes debe ser 105")
            self.assertEqual(d["nro_comp"], "E001-2102", "Comprobante emitido debe ser E001-2102")
            self.assertEqual(d["estado_comp"], "Emitido", "Estado del comprobante debe ser Emitido")
            self.assertAlmostEqual(float(d["monto_factura"]), 70.00, places=2)

            # Validar función calcular_colchon: monto_comp 70.00 - yape_banco 0.00 = 70.00
            self.assertAlmostEqual(mu.calcular_colchon(70.0, 0.0), 70.00, places=2)
        finally:
            conn.close()

    def test_caso_2_susan_orden_opciones_std_y_mas(self):
        """
        Caso 2: Susan 04/11 Hab 302 -> B/T solo si hay receptora válida; si no,
        Opción C 50.00 (colchón 25.00) por encima de Opción A 75.00 (colchón 50.00).
        En modo 'Necesito más boletas' (modo_mas_boletas=True), se invierte: A sobre C.
        """
        susan_uuid = "5b914cfd-5c18-4aed-916e-bf0479b5ffc5"
        conn = database.get_connection()
        try:
            # 1. Comprobar que en la base real no hay receptoras válidas para horas extras de S/ 25 el 04/11
            receptoras = mu.buscar_receptoras(conn, "04/11/2026", horas_extras=25.0, exclude_uuid=susan_uuid)
            self.assertEqual(len(receptoras), 0, "No debe haber receptoras para Susan en la base real el 04/11")

            # 2. Modo Estándar (modo_mas_boletas=False):
            # C (colchón 25) debe preceder a A (colchón 50)
            res_std = mu.sugerir_unificacion(susan_uuid, modo_mas_boletas=False, conn=conn)
            self.assertTrue(res_std["ok"])
            self.assertTrue(res_std["es_inexacto"])
            opciones_std = res_std["opciones"]
            codigos_std = [o["codigo"] for o in opciones_std]
            self.assertEqual(codigos_std, ["C", "A"], "En modo estándar Susan debe tener opciones ['C', 'A']")

            opt_c = opciones_std[0]
            self.assertEqual(opt_c["codigo"], "C")
            self.assertEqual(opt_c["monto_total"], 50.00)
            self.assertEqual(opt_c["colchon"], 25.00)

            opt_a = opciones_std[1]
            self.assertEqual(opt_a["codigo"], "A")
            self.assertEqual(opt_a["monto_total"], 75.00)
            self.assertEqual(opt_a["colchon"], 50.00)

            # 3. Modo 'Necesito más boletas' (modo_mas_boletas=True):
            # A (monto 75, colchón 50) debe preceder a C (monto 50, colchón 25) para recuperar déficit
            res_mas = mu.sugerir_unificacion(susan_uuid, modo_mas_boletas=True, conn=conn)
            self.assertTrue(res_mas["ok"])
            opciones_mas = res_mas["opciones"]
            codigos_mas = [o["codigo"] for o in opciones_mas]
            self.assertEqual(codigos_mas, ["A", "C"], "En modo más boletas Susan debe tener opciones ['A', 'C']")
            self.assertEqual(opciones_mas[0]["codigo"], "A")
            self.assertEqual(opciones_mas[1]["codigo"], "C")
        finally:
            conn.close()

    def test_caso_3_fiorela_nunca_receptora(self):
        """
        Caso 3: Fiorela 04/11 Hab 304 (emitida con EB01-6816) -> NUNCA debe ser receptora.
        Verifica que al buscar receptoras para cualquier monto de horas extras,
        una estancia emitida quede terminantemente excluida.
        """
        fiorela_uuid = "29ea9fb5-fabb-4cc7-ae36-04882cce077b"
        conn = database.get_connection()
        try:
            # 1. Verificar datos de Fiorela en base de datos
            row = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (fiorela_uuid,)).fetchone()
            self.assertIsNotNone(row, "Fiorela 04/11 Hab 304 debe existir en la BD")
            f_dict = dict(row)
            self.assertEqual(f_dict["nro_comp"], "EB01-6816", "Fiorela debe tener nro_comp EB01-6816")
            self.assertEqual(f_dict["estado_comp"], "Emitido", "Fiorela debe tener estado_comp Emitido")

            # 2. Probar buscar_receptoras para diferentes montos de horas extras
            for extras in (10.0, 15.0, 20.0, 25.0, 30.0, 45.0, 50.0):
                candidatas = mu.buscar_receptoras(conn, "04/11/2026", horas_extras=extras)
                uuids_candidatas = [c["estancia_uuid"] for c in candidatas]
                self.assertNotIn(
                    fiorela_uuid,
                    uuids_candidatas,
                    f"Fiorela {fiorela_uuid} (emitida con EB01-6816) no debe ser receptora con horas_extras={extras}",
                )

            # 3. Validar con conexión aislada que cualquier estancia emitida queda excluida
            mem_conn = _crear_conexion_en_memoria()
            _insertar_estancia_mock(
                mem_conn,
                {
                    "estancia_uuid": "candidata-emitida",
                    "fecha_in_dmy": "04/11/2026",
                    "nro_comp": "EB01-9999",
                    "estado_comp": "Emitido",
                    "pago_ini_yape": 35.0,
                    "hab": "102",
                    "tipo_hab": "Simple con baño privado",
                },
            )
            recs_mem = mu.buscar_receptoras(mem_conn, "04/11/2026", horas_extras=20.0)
            self.assertNotIn("candidata-emitida", [r["estancia_uuid"] for r in recs_mem])
        finally:
            conn.close()

    def test_caso_4_receptora_dia_anterior_fechas_concordantes(self):
        """
        Caso 4: Receptora del día anterior con fechas concordantes:
        - Receptora ingresó el 03/11 y sale el 04/11 (fecha_out coincide con el Yape donante).
        - Donante ingresa el 04/11 con Yape inexacto de S/ 20.00.
        - Generación de Opción B con:
          Ítem 1: DIA DE ALOJAMIENTO DEL 03/11/2026
          Ítem 2: ADICIONAL POR HORAS EXTRAS DEL 04/11/2026 (fecha del donante).
        - Al aplicar: Receptora actualiza adic_comp y monto_factura;
          Donante queda estado_comp='Unificado' y unificado_en_uuid=receptora.
        """
        mem_conn = _crear_conexion_en_memoria()

        # Receptora: Simple con baño privado (rango 45-60)
        rec_uuid = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "receptora-03",
                "huesped": "JUAN PEREZ RECEPTOR",
                "hab": "201",
                "fecha_in_dmy": "03/11/2026",
                "fecha_out_dmy": "04/11/2026",
                "tipo_hab": "Simple con baño privado",
                "tarifa": 55.0,
                "pago_ini_yape": 35.0,
                "pago_post_yape": 0.0,
                "estado_comp": "Pendiente",
                "nro_comp": "",
                "celular": "",
                "ruc": "-",
                "empresa": "-",
            },
        )

        # Donante: Simple con baño privado con Yape inexacto S/ 20 el 04/11
        donante_uuid = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "donante-04",
                "huesped": "CARLOS DONANTE",
                "hab": "205",
                "fecha_in_dmy": "04/11/2026",
                "fecha_out_dmy": "05/11/2026",
                "tipo_hab": "Simple con baño privado",
                "tarifa": 55.0,
                "pago_ini_yape": 20.0,
                "pago_post_yape": 0.0,
                "estado_comp": "Pendiente",
                "nro_comp": "",
                "celular": "",
                "ruc": "-",
                "empresa": "-",
            },
        )

        # 1. Búsqueda de receptoras para el 04/11 con horas extras S/ 20.00
        recs = mu.buscar_receptoras(mem_conn, "04/11/2026", horas_extras=20.0, exclude_uuid=donante_uuid)
        self.assertEqual(len(recs), 1, "Debe encontrar a la receptora del día anterior")
        self.assertEqual(recs[0]["estancia_uuid"], rec_uuid)
        self.assertEqual(recs[0]["_peso_prioridad"], 0, "Debe tener prioridad 0 por checkout el día del donante")

        # 2. Sugerencia de unificación
        sug = mu.sugerir_unificacion(donante_uuid, modo_mas_boletas=False, conn=mem_conn)
        self.assertTrue(sug["ok"])
        opciones = sug["opciones"]
        codigos = [o["codigo"] for o in opciones]
        self.assertIn("B", codigos, "Debe existir Opción B al haber receptora válida")

        opt_b = next(o for o in opciones if o["codigo"] == "B")
        self.assertEqual(opt_b["colchon"], 0.0)
        self.assertEqual(opt_b["monto_total"], 55.00)  # 35.00 + 20.00

        # Validar concordancia de fechas en ítems
        items = opt_b["items_preview"]
        self.assertEqual(len(items), 2)
        self.assertIn("03/11/2026", items[0]["descripcion"], "Ítem 1 debe tener la fecha de estancia de la receptora (03/11)")
        self.assertEqual(items[0]["monto"], 35.00)
        self.assertIn("04/11/2026", items[1]["descripcion"], "Ítem 2 debe tener la fecha del donante (04/11)")
        self.assertEqual(items[1]["monto"], 20.00)

        # 3. Aplicar decisión Opción B
        res_app = mu.aplicar_decision(
            {
                "estancia_uuid": donante_uuid,
                "opcion": "B",
                "receptora_uuid": rec_uuid,
                "colchon": 0.0,
                "usuario": "Auditor Test",
            },
            conn=mem_conn,
        )
        self.assertTrue(res_app["ok"])

        # Verificar BD receptora actualizada
        rec_row = mem_conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (rec_uuid,)).fetchone()
        self.assertEqual(float(rec_row["adic_comp"]), 20.00)
        self.assertEqual(float(rec_row["monto_factura"]), 55.00)
        self.assertIn("HORAS EXTRAS", rec_row["tipo_adicional"])
        self.assertIn("205", rec_row["tipo_adicional"])

        # Verificar BD donante actualizado
        don_row = mem_conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (donante_uuid,)).fetchone()
        self.assertEqual(don_row["estado_comp"], "Unificado")
        self.assertEqual(don_row["unificado_en_uuid"], rec_uuid)

        # Verificar auditoría en decisiones_boleta
        dec_row = mem_conn.execute("SELECT * FROM decisiones_boleta WHERE estancia_uuid = ?", (donante_uuid,)).fetchone()
        self.assertIsNotNone(dec_row)
        self.assertEqual(dec_row["elegida"], "B")
        self.assertEqual(dec_row["receptora_uuid"], rec_uuid)

    def test_caso_5_yapes_tienda_horas_extras(self):
        """
        Caso 5: Yapes de tienda 15 + 15 -> horas extras 30 dentro del rango de la receptora.
        - Se registran 2 ventas en tienda por S/ 15.00 con método Yape.
        - Receptora Matrimonial (rango 60-90) con Yape de S/ 45.00.
        - Suma de tienda = S/ 30.00; total = S/ 75.00 (dentro de 60-90).
        - Generación de Opción T con colchón 0.00 y aplicación que marca
          yape_tienda_usado_en para evitar doble gasto.
        """
        mem_conn = _crear_conexion_en_memoria()

        # Insertar 2 ventas de tienda
        v1_uuid = "tienda-v1-uuid"
        v2_uuid = "tienda-v2-uuid"
        mem_conn.execute(
            """
            INSERT INTO ventas_tienda (
                venta_uuid, lote_uuid, estancia_uuid, id_estancia, fecha_iso, fecha_dmy,
                producto, cantidad, metodo_pago, hab, personal, precio_unit, subtotal,
                nro_op_yape, sheet_row, synced, yape_tienda_usado_en
            ) VALUES (
                ?, '', '', '', ?, '04/11/2026',
                'Gaseosa', 3, 'Yape', '101', 'Recepción', 5.0, 15.0,
                '11111', 1, 0, ''
            )
            """,
            (v1_uuid, datetime.now().isoformat()),
        )
        mem_conn.execute(
            """
            INSERT INTO ventas_tienda (
                venta_uuid, lote_uuid, estancia_uuid, id_estancia, fecha_iso, fecha_dmy,
                producto, cantidad, metodo_pago, hab, personal, precio_unit, subtotal,
                nro_op_yape, sheet_row, synced, yape_tienda_usado_en
            ) VALUES (
                ?, '', '', '', ?, '04/11/2026',
                'Snack', 3, 'Yape', '102', 'Recepción', 5.0, 15.0,
                '22222', 2, 0, ''
            )
            """,
            (v2_uuid, datetime.now().isoformat()),
        )
        mem_conn.commit()

        # Receptora Matrimonial: rango Boleta 60–90
        rec_uuid = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "receptora-matr",
                "huesped": "MARIA LOPEZ",
                "hab": "301",
                "fecha_in_dmy": "04/11/2026",
                "fecha_out_dmy": "05/11/2026",
                "tipo_hab": "Matrimonial",
                "tarifa": 75.0,
                "pago_ini_yape": 45.0,
                "estado_comp": "Pendiente",
                "nro_comp": "",
                "celular": "",
                "ruc": "-",
                "empresa": "-",
            },
        )

        # Donante inexacto
        don_uuid = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "donante-tienda",
                "huesped": "PEDRO TIENDA",
                "hab": "202",
                "fecha_in_dmy": "04/11/2026",
                "fecha_out_dmy": "05/11/2026",
                "tipo_hab": "Simple con baño compartido",
                "tarifa": 40.0,
                "pago_ini_yape": 20.0,
                "estado_comp": "Pendiente",
                "nro_comp": "",
                "celular": "",
                "ruc": "-",
                "empresa": "-",
            },
        )

        # 1. Comprobar que buscar_yapes_tienda_libres encuentra las 2 ventas
        libres = mu.buscar_yapes_tienda_libres(mem_conn, "04/11/2026")
        self.assertEqual(len(libres), 2)
        total_tienda = sum(float(l["subtotal"]) for l in libres)
        self.assertEqual(total_tienda, 30.00)

        # 2. Sugerir unificación y verificar Opción T
        sug = mu.sugerir_unificacion(don_uuid, modo_mas_boletas=False, conn=mem_conn)
        self.assertTrue(sug["ok"])
        opciones = sug["opciones"]
        codigos = [o["codigo"] for o in opciones]
        self.assertIn("T", codigos, "Debe existir Opción T al haber Yapes de tienda disponibles")

        opt_t = next(o for o in opciones if o["codigo"] == "T")
        self.assertEqual(opt_t["colchon"], 0.0)
        self.assertEqual(opt_t["monto_total"], 75.00)  # 45.00 + 30.00
        self.assertEqual(len(opt_t["items_preview"]), 2)
        self.assertEqual(opt_t["items_preview"][0]["monto"], 45.00)
        self.assertEqual(opt_t["items_preview"][1]["monto"], 30.00)
        self.assertIn("TIENDA", opt_t["items_preview"][1]["descripcion"])

        # 3. Aplicar decisión Opción T
        res_app = mu.aplicar_decision(
            {
                "estancia_uuid": don_uuid,
                "opcion": "T",
                "receptora_uuid": rec_uuid,
                "tienda_uuids": [v1_uuid, v2_uuid],
                "colchon": 0.0,
                "usuario": "Auditor Tienda",
            },
            conn=mem_conn,
        )
        self.assertTrue(res_app["ok"])

        # 4. Verificar que las ventas quedaron marcadas como usadas
        vt_rows = mem_conn.execute(
            "SELECT venta_uuid, yape_tienda_usado_en FROM ventas_tienda WHERE venta_uuid IN (?, ?)",
            (v1_uuid, v2_uuid),
        ).fetchall()
        for r in vt_rows:
            self.assertEqual(r["yape_tienda_usado_en"], rec_uuid, "Debe marcar yape_tienda_usado_en con la receptora")

        # Verificar que buscar_yapes_tienda_libres ya no las retorna (cero doble gasto)
        libres_despues = mu.buscar_yapes_tienda_libres(mem_conn, "04/11/2026")
        self.assertEqual(len(libres_despues), 0, "No debe haber Yapes de tienda libres tras su aplicación")

    def test_caso_6_reglas_negocio_y_tabla_1_1(self):
        """
        Caso 6: Reglas de negocio universales:
        a) Mínimo absoluto de comprobante S/ 35.00.
        b) Yape < 10 -> absorbido por Bolsa Nivel 2 (no inexacto, sin sugerencia).
        c) Yape dentro del rango -> se emite exacto (no inexacto, sin sugerencia).
        d) Regla especial de Tabla 1.1: Doble con baño compartido ocupada por 1 persona
           (n_acomp vacío o '0') aplica rango de simple con baño privado (Boleta 45–60, Factura máx 70).
           Si n_acomp >= 1, aplica rango de doble compartido estándar (Boleta 45–65, Factura máx 75).
        """
        # a) Mínimo absoluto
        self.assertEqual(mu.MINIMO_COMPROBANTE, 35.0)

        # b) Yape < 10 absorbido por Nivel 2
        estancia_yape_chico = {
            "estancia_uuid": "yape-5",
            "pago_ini_yape": 5.0,
            "pago_post_yape": 0.0,
            "tipo_hab": "Simple con baño compartido",
            "tipo_comp": "Boleta",
            "nro_comp": "",
            "estado_comp": "Pendiente",
        }
        self.assertFalse(
            mu.es_pendiente_inexacto(estancia_yape_chico),
            "Yape < 10 no debe ser clasificado como inexacto para unificación",
        )

        mem_conn = _crear_conexion_en_memoria()
        u_chico = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "u-chico-5",
                "pago_ini_yape": 5.0,
                "pago_post_yape": 0.0,
                "tipo_hab": "Simple con baño privado",
            },
        )
        sug_chico = mu.sugerir_unificacion(u_chico, conn=mem_conn)
        self.assertFalse(sug_chico["es_inexacto"])
        self.assertIn("Nivel 2", sug_chico["mensaje"])
        self.assertEqual(len(sug_chico["opciones"]), 0)

        # c) Yape dentro del rango -> exacto sin sugerencia
        estancia_exacta = {
            "estancia_uuid": "yape-50",
            "pago_ini_yape": 50.0,
            "pago_post_yape": 0.0,
            "tipo_hab": "Simple con baño privado",
            "tipo_comp": "Boleta",
            "nro_comp": "",
            "estado_comp": "Pendiente",
        }
        self.assertFalse(
            mu.es_pendiente_inexacto(estancia_exacta),
            "Yape dentro de rango no debe ser inexacto",
        )

        u_exacto = _insertar_estancia_mock(
            mem_conn,
            {
                "estancia_uuid": "u-exacto-50",
                "pago_ini_yape": 50.0,
                "pago_post_yape": 0.0,
                "tipo_hab": "Simple con baño privado",
            },
        )
        sug_exacto = mu.sugerir_unificacion(u_exacto, conn=mem_conn)
        self.assertFalse(sug_exacto["es_inexacto"])
        self.assertIn("dentro del rango", sug_exacto["mensaje"])
        self.assertEqual(len(sug_exacto["opciones"]), 0)

        # d) Regla especial Tabla 1.1: Doble con baño compartido ocupada por 1 persona
        # Caso 1 persona (n_acomp = ''): rango Boleta 45–60, Factura máx 70
        r_vacio = mu.rango_por_tipo("Doble con baño compartido", n_acomp="")
        self.assertEqual(r_vacio["boleta_min"], 45.0)
        self.assertEqual(r_vacio["boleta_max"], 60.0)
        self.assertEqual(r_vacio["factura_max"], 70.0)

        # Caso 1 persona (n_acomp = '0'): rango Boleta 45–60, Factura máx 70
        r_cero = mu.rango_por_tipo("Doble con baño compartido", n_acomp="0")
        self.assertEqual(r_cero["boleta_min"], 45.0)
        self.assertEqual(r_cero["boleta_max"], 60.0)
        self.assertEqual(r_cero["factura_max"], 70.0)

        # Caso 1 persona en Doble Familiar con baño compartido
        r_fam_vacio = mu.rango_por_tipo("Doble Familiar con baño compartido", n_acomp="")
        self.assertEqual(r_fam_vacio["boleta_min"], 45.0)
        self.assertEqual(r_fam_vacio["boleta_max"], 60.0)
        self.assertEqual(r_fam_vacio["factura_max"], 70.0)

        # Caso 2 personas (n_acomp = '1'): rango estándar Boleta 45–65, Factura máx 75
        r_dos = mu.rango_por_tipo("Doble con baño compartido", n_acomp="1")
        self.assertEqual(r_dos["boleta_min"], 45.0)
        self.assertEqual(r_dos["boleta_max"], 65.0)
        self.assertEqual(r_dos["factura_max"], 75.0)


if __name__ == "__main__":
    # Ejecución directa con resultado detallado
    unittest.main(verbosity=2)
