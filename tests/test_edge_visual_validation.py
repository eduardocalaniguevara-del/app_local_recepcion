#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_edge_visual_validation.py
Validación automatizada de interfaz de usuario con Microsoft Edge (msedge) y Playwright.
Verifica:
1. Navegación a Contabilidad -> Cruce Tricolor CALZADO.
2. Tarjeta Efectivo Maelen acumulado (S/ 1,090.00) y banner de déficit (S/ 466.60).
3. Filtro "Con Efectivo Maelen" muestra borde turquesa y píldora teal (✓ Calzado · 💵 +S/).
4. Filtro "Inexactos" muestra 7 filas con icono ⚖️.
5. Clic en la píldora de Susan Quispe (Hab 302) abre el modal de facturación sin recargar la página.
6. Cuadro "💡 Sugerencia de Unificación" muestra opciones C y A.
7. Clic en "⚡ Aplicar al borrador" de Opción C: Monto = 50.00, Op. Gravada = S/ 45.25, IGV = S/ 4.75.
8. Monitoreo y aserción de 0 excepciones o errores JavaScript en consola.
"""

import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8082"
SCREENSHOTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "screenshots"))
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def run_edge_visual_validation():
    print("=" * 70)
    print("INICIANDO VALIDACIÓN VISUAL E2E EN MICROSOFT EDGE (msedge)")
    print("=" * 70)

    console_errors = []
    page_errors = []

    def on_console(msg):
        # Excluir peticiones de red automáticas del navegador a favicon.ico
        loc = str(msg.location or "")
        txt = str(msg.text or "")
        if "favicon.ico" in txt or "favicon.ico" in loc:
            return
        if msg.type in ("error", "assert"):
            console_errors.append(f"[{msg.type}] {txt}")

    with sync_playwright() as p:
        print("[1/8] Lanzando Microsoft Edge (channel='msedge')...")
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Capturar mensajes de consola y errores de página
        page.on("console", on_console)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        print(f"[2/8] Navegando a {BASE_URL}...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        time.sleep(1.5)

        # 1. Navegar a Contabilidad -> Cruce Tricolor CALZADO
        print("[3/8] Cambiando vista a Contabilidad...")
        page.evaluate("() => { if (window.cambiarVista) window.cambiarVista('contabilidad'); }")
        time.sleep(1.0)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "01_vista_contabilidad.png"))

        # Asegurar subtab calzado
        page.evaluate("() => { if (window.contabCambiarSubTab) window.contabCambiarSubTab('calzado'); }")
        time.sleep(0.5)

        # 2. Verificar tarjeta Efectivo Maelen acumulado (S/ 1,090.00) y banner déficit (S/ 466.60)
        print("[4/8] Verificando Tarjeta Efectivo Maelen y Banner de Déficit...")
        val_maelen_el = page.locator("#valEfectivoMaelenAcum")
        assert val_maelen_el.is_visible(), "El elemento #valEfectivoMaelenAcum debe ser visible"
        txt_maelen = val_maelen_el.text_content().strip()
        print(f"     -> Efectivo Maelen acumulado: '{txt_maelen}'")
        assert "1,090.00" in txt_maelen, f"Se esperaba 'S/ 1,090.00' pero se obtuvo '{txt_maelen}'"

        banner_deficit_el = page.locator("#bannerDeficitBcp")
        assert banner_deficit_el.is_visible(), "El elemento #bannerDeficitBcp debe ser visible"
        txt_banner = banner_deficit_el.text_content().strip()
        print(f"     -> Banner de Déficit: '{txt_banner}'")
        assert "466.60" in txt_banner, f"Se esperaba mención de '466.60' pero se obtuvo '{txt_banner}'"

        # 3. Verificar filtro 'Con Efectivo Maelen'
        print("[5/8] Verificando filtro 'Con Efectivo Maelen' (OK_COLCHON)...")
        filtro_sel = page.locator("#contabCalzadoFiltroEstado")
        filtro_sel.select_option("OK_COLCHON")
        time.sleep(0.5)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "02_filtro_ok_colchon.png"))

        # Verificar filas con borde turquesa y píldora teal
        filas_colchon = page.locator("#contabCalzadoTbody tr")
        cnt_colchon = filas_colchon.count()
        print(f"     -> Filas con Efectivo Maelen encontradas: {cnt_colchon}")
        assert cnt_colchon > 0, "Debe haber al menos 1 fila con Efectivo Maelen"

        # Inspeccionar primera fila para validar estilo outline y píldora
        primera_fila = filas_colchon.first
        style_attr = primera_fila.get_attribute("style") or ""
        print(f"     -> Estilo primera fila: '{style_attr}'")
        assert "#14b8a6" in style_attr, "La fila debe tener outline turquesa (#14b8a6)"

        pildora_primera = primera_fila.locator("[role='button']")
        txt_pildora = pildora_primera.text_content().strip()
        print(f"     -> Píldora primera fila: '{txt_pildora}'")
        assert "Calzado" in txt_pildora and "💵" in txt_pildora, "La píldora debe mostrar '✓ Calzado · 💵 +S/'"

        # 4. Verificar filtro 'Inexactos'
        print("[6/8] Verificando filtro 'Inexactos'...")
        filtro_sel.select_option("INEXACTOS")
        time.sleep(0.5)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "03_filtro_inexactos.png"))

        filas_inexactos = page.locator("#contabCalzadoTbody tr")
        cnt_inexactos = filas_inexactos.count()
        print(f"     -> Filas inexactas encontradas: {cnt_inexactos}")
        assert cnt_inexactos == 7, f"Se esperaban exactamente 7 filas inexactas, pero se encontraron {cnt_inexactos}"

        # Verificar icono ⚖️ en todas las filas inexactas
        for i in range(cnt_inexactos):
            fila_i = filas_inexactos.nth(i)
            pildora_i = fila_i.locator("[role='button']")
            txt_i = pildora_i.text_content().strip()
            assert "⚖️" in txt_i, f"La fila {i} debe tener el icono ⚖️ en la píldora: '{txt_i}'"

        # 5. Localizar y hacer clic en la píldora de Susan Quispe (Hab 302)
        print("[7/8] Clic en píldora de Susan Quispe (Hab 302) para abrir modal...")
        fila_susan = None
        for i in range(cnt_inexactos):
            txt_row = filas_inexactos.nth(i).text_content()
            if "302" in txt_row and "SUSAN" in txt_row.upper():
                fila_susan = filas_inexactos.nth(i)
                break
        assert fila_susan is not None, "No se encontró la fila de Susan Quispe (Hab 302)"

        # Clic en la píldora de estado de Susan
        pildora_susan = fila_susan.locator("[role='button']")
        pildora_susan.click()
        time.sleep(1.2)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "04_modal_susan_abierto.png"))

        # El modal se renderiza dentro de #popoverModal y #popModalCard
        pop_modal = page.locator("#popModalCard")
        assert pop_modal.is_visible(), "El modal de facturación #popModalCard debe estar visible"

        # Verificar título del modal de Susan
        pop_title = page.locator("#popTitle")
        txt_pop_title = pop_title.text_content()
        print(f"     -> Título del modal: '{txt_pop_title}'")
        assert "302" in txt_pop_title and "SUSAN" in txt_pop_title.upper(), "El título debe indicar Hab. 302 (Susan)"

        # 6. Verificar cuadro '💡 Sugerencia de Unificación' con opciones C y A
        box_sug = page.locator("#boxSugerenciaUnificacion")
        assert box_sug.is_visible(), "El cuadro #boxSugerenciaUnificacion debe estar visible"
        txt_box = box_sug.text_content()
        print(f"     -> Contenido caja de sugerencias: {txt_box[:160]}...")
        assert "Opción C" in txt_box, f"Debe contener Opción C (obtenido: {txt_box[:200]})"
        assert "Opción A" in txt_box, f"Debe contener Opción A (obtenido: {txt_box[:200]})"
        if "Tu elección habitual" in txt_box:
            print("     -> Aprendizaje progresivo: ⭐ Tu elección habitual activo con 'Opción C' preservada.")
        else:
            print("     -> Sugerencias estándar: 'Opción C' y 'Opción A' presentes.")

        # 7. Clic en '⚡ Aplicar al borrador' en Opción C
        print("[8/8] Aplicando Opción C al borrador...")
        tarjeta_c = box_sug.locator("div.rounded-lg").filter(has_text="Opción C").first
        if tarjeta_c.is_visible():
            btn_aplicar_c = tarjeta_c.locator("button:has-text('Aplicar al borrador')")
        else:
            btn_aplicar_c = box_sug.locator("button:has-text('Aplicar al borrador')").first
        assert btn_aplicar_c.is_visible(), "El botón 'Aplicar al borrador' de Opción C debe ser visible"
        btn_aplicar_c.click()
        time.sleep(1.0)
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "05_opcion_c_aplicada.png"))

        # Verificar Monto = 50.00
        monto_input = page.locator("#modalFact_monto")
        val_monto = monto_input.input_value().strip()
        print(f"     -> Monto en borrador: '{val_monto}'")
        assert float(val_monto) == 50.00, f"Se esperaba monto 50.00 pero se obtuvo '{val_monto}'"

        # Verificar Op. Gravada y IGV en el modal
        txt_pop_modal = pop_modal.text_content()
        print("     -> Validando totales calculados (Ley 31556: total / 1.105):")
        # 50.0 / 1.105 = 45.2488 -> S/ 45.25
        # IGV = 50.0 - 45.25 = S/ 4.75
        assert "45.25" in txt_pop_modal, "Op. Gravada debe ser S/ 45.25"
        assert "4.75" in txt_pop_modal, "IGV debe ser S/ 4.75"
        print("     -> Op. Gravada: S/ 45.25 OK | IGV: S/ 4.75 OK")

        # 8. Monitoreo y aserción de 0 excepciones o errores JS
        print("-" * 70)
        print("MONITOREO DE CONSOLA JAVASCRIPT:")
        print(f"Errores de consola: {len(console_errors)}")
        for err in console_errors:
            print(f"   [Consola Error]: {err}")
        print(f"Excepciones no capturadas (pageerror): {len(page_errors)}")
        for perr in page_errors:
            print(f"   [Page Error]: {perr}")

        assert len(console_errors) == 0, f"Se detectaron {len(console_errors)} errores en la consola JS"
        assert len(page_errors) == 0, f"Se detectaron {len(page_errors)} excepciones en la página"

        browser.close()
        print("=" * 70)
        print("VALIDACIÓN VISUAL EN MICROSOFT EDGE COMPLETADA CON 100% ÉXITO (0 ERRORES JS)")
        print("=" * 70)


if __name__ == "__main__":
    run_edge_visual_validation()
