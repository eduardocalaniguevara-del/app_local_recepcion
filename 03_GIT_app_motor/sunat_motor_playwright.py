#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_GIT_app_motor/sunat_motor_playwright.py — Motor Unificado SUNAT SEE-SOL + Conversor PDF->Supabase + Brazo WSP
Viaja 100% por GitHub (`git push` desde Laptop 1 -> `git pull` en Laptop 2 Madre y Laptops 3 Terciarias).

Responsabilidades:
1. Emisión en SUNAT SEE-SOL (Playwright + Microsoft Edge Headless Stealth) de:
   - Boletas de Venta Electrónicas (EB01) con IGV 10.5% (Ley 31556).
   - Facturas Electrónicas (E001) al Contado con IGV 10.5%.
   - Notas de Crédito Electrónicas (EB01 / E001) por Anulación de la Operación.
   - Modo Borrador (Vista Preliminar): toma captura PNG oficial sin hacer clic en Emitir.
2. Almacenamiento 100% local de PDFs y capturas en `02_DB_sqlite_local/comprobantes_pdf/`
   (`boletas/`, `facturas/`, `notas_credito/`, `capturas/`), bloqueado en `.gitignore` y sin depender de `C:\\Hostal_Sistema`.
3. Conversión inmediata del PDF emitido en datos estructurados (`sunat_ventas`, `sunat_lineas_cache`, `estancias`)
   que viajan automáticamente a Supabase para que Laptop 1 y Laptops Terciarias vean toda la información sin mover PDFs.
4. Despacho del PDF por el WhatsApp de Recepción en Laptop Madre (vía `wsp_daemon.js` Baileys 0-ventanas o fallback WhatsApp Desktop invisible).
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

_MDIR = str(Path(__file__).resolve().parent)
if _MDIR not in sys.path:
    sys.path.insert(0, _MDIR)

from config import (
    BOLETAS_PDF_DIR,
    BORRADORES_DIR,
    CAPTURAS_SUNAT_DIR,
    COMPROBANTES_PDF_DIR,
    DATA_DIR,
    ES_NIVEL_2_MADRE_HOTEL,
    FACTURAS_PDF_DIR,
    HOSTAL_SISTEMA_DIR,
    MODO_PRUEBA,
    NOTAS_CREDITO_PDF_DIR,
    ROL_NODO,
)
from db import db_transaction, get_connection

FACTOR_IGV_HOTEL = 1.105
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

MESES_CARPETA = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

MESES_ES_NUM = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}

# ==============================================================================
# 1. NAVEGADOR DISCRETO (MICROSOFT EDGE HEADLESS ANTI-WAF SUNAT)
# ==============================================================================

EDGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

ARGS_STEALTH_HEADLESS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling",
    "--mute-audio",
]


_pw_thread_lock = threading.RLock()


@contextmanager
def adquirir_candado_sunat_exclusivo(timeout_s: float = 95.0) -> Iterator[None]:
    """
    Exclusión mutua hilo + sistema operativo (`02_DB_sqlite_local/sunat_playwright.lock`)
    alrededor de toda sesión Playwright contra SUNAT SEE-SOL.
    """
    adq_hilo = _pw_thread_lock.acquire(timeout=timeout_s)
    if not adq_hilo:
        raise RuntimeError("Otra operación SUNAT está en curso en este nodo; intente en unos segundos.")

    lock_fh = None
    try:
        if os.name == "nt":
            import msvcrt
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            lock_path = DATA_DIR / "sunat_playwright.lock"
            t0 = time.time()
            while True:
                try:
                    lock_fh = open(lock_path, "a+b")
                    lock_fh.seek(0)
                    if lock_path.stat().st_size == 0:
                        lock_fh.write(b"0")
                        lock_fh.flush()
                        lock_fh.seek(0)
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if lock_fh is not None:
                        try:
                            lock_fh.close()
                        except Exception:
                            pass
                        lock_fh = None
                    if time.time() - t0 >= timeout_s:
                        raise RuntimeError("Otra operación SUNAT está en curso en este nodo; intente en unos segundos.")
                    time.sleep(0.35)
        yield
    finally:
        if lock_fh is not None:
            try:
                import msvcrt
                lock_fh.seek(0)
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                lock_fh.close()
            except Exception:
                pass
        _pw_thread_lock.release()


def hay_sesion_sunat_activa() -> bool:
    """Devuelve True si existe una sesión Playwright SUNAT en curso en este proceso o en otro proceso local."""
    if not _pw_thread_lock.acquire(blocking=False):
        return True
    try:
        if os.name == "nt":
            import msvcrt
            lock_path = DATA_DIR / "sunat_playwright.lock"
            if not lock_path.exists():
                return False
            fh = None
            try:
                fh = open(lock_path, "a+b")
                fh.seek(0)
                if lock_path.stat().st_size == 0:
                    fh.write(b"0")
                    fh.flush()
                    fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                return False
            except OSError:
                return True
            finally:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
        return False
    finally:
        _pw_thread_lock.release()


def lanzar_edge_sunat(p, oculto: bool = True, headless: Optional[bool] = None):
    """Lanza Microsoft Edge en modo 100% invisible (headless=True) con camuflaje anti-WAF SUNAT."""
    modo_oculto = oculto if headless is None else headless
    return p.chromium.launch(
        channel="msedge",
        headless=bool(modo_oculto),
        args=ARGS_STEALTH_HEADLESS,
    )


def crear_contexto_sunat(browser, viewport: Optional[Dict[str, int]] = None):
    """Crea BrowserContext con User-Agent real de Edge y oculta navigator.webdriver."""
    vp = viewport or {"width": 1600, "height": 1150}
    ctx = browser.new_context(
        viewport=vp,
        user_agent=EDGE_USER_AGENT,
        locale="es-PE",
    )
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    return ctx


# ==============================================================================
# 2. CREDENCIALES CLAVE SOL SUNAT Y LOGIN OAUTH2 BLINDADO
# ==============================================================================

LLAVE_FERNET_HOSTAL = b"tGc_ULh-iN0-TZAxRtFjVTb2g_cRn4jcuWZSOq98V-4="

AUTENTICA_SOL_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
STATE_SOL_DEFAULT = (
    "rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcAUH2sHDFmDRAwACRgAKbG9hZEZhY3RvckkACXRocmVzaG9sZHhwP0AAAAAAAAx3CAAAABAAAAADdAADZXhlcHQABnBhcmFtc3QA"
    "SyomKiYvY2wtdGktaXRtZW51L01lbnVJbnRlcm5ldC5odG0mYjY0ZDI2YThiNWFmMDkxOTIzYjIzYjY0MDdhMWMxZGI0MWU3MzNhNnQABGV4ZWNweA=="
)
URL_MENU_SOL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
ERRORES_RED = ("net::ERR_", "ERR_CONNECTION", "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED", "Timeout", "timeout")


def obtener_credenciales_sunat() -> Tuple[str, str, str]:
    """
    Obtiene (RUC, Usuario_SOL, Clave_SOL) EXCLUSIVAMENTE en la Laptop Madre (NIVEL_2_MADRE_HOTEL)
    o si se forzó prueba local de ingeniería (OVALO_PERMITIR_SUNAT_LOCAL_L1=1).
    """
    es_madre = (
        ROL_NODO in ("NIVEL_2_MADRE_HOTEL", "LAPTOP_2_RECEPCION")
        or os.path.exists(r"C:\Users\ovalo mariategui")
        or os.environ.get("OVALO_PERMITIR_SUNAT_LOCAL_L1") == "1"
    )
    if not es_madre:
        raise RuntimeError(
            f"Candado de arquitectura activo: este nodo ({ROL_NODO}) no es la Laptop Madre. "
            "Toda emisión SUNAT debe ejecutarse en NIVEL_2_MADRE_HOTEL vía cola Supabase."
        )

    env_ruc = os.environ.get("SUNAT_RUC", "").strip()
    env_usr = os.environ.get("SUNAT_USUARIO", "").strip()
    env_clv = os.environ.get("SUNAT_CLAVE", "").strip()
    if env_ruc and env_usr and env_clv:
        return env_ruc, env_usr, env_clv

    candidatos_boveda = [
        DATA_DIR / "credenciales_seguras.json",
        BORRADORES_DIR / "credenciales_seguras.json",
        HOSTAL_SISTEMA_DIR / "config" / "credenciales_seguras.json",
    ]
    for ruta in candidatos_boveda:
        try:
            if not ruta.exists():
                continue
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos.get("sunat"), dict):
                s = datos["sunat"]
                if s.get("ruc") and s.get("usuario") and s.get("clave"):
                    return str(s["ruc"]).strip(), str(s["usuario"]).strip(), str(s["clave"]).strip()
            if datos.get("RUC") and datos.get("USUARIO") and datos.get("CLAVE_CIFRADA"):
                from cryptography.fernet import Fernet
                clave_real = Fernet(LLAVE_FERNET_HOSTAL).decrypt(datos["CLAVE_CIFRADA"].encode()).decode()
                return str(datos["RUC"]).strip(), str(datos["USUARIO"]).strip(), clave_real
        except Exception:
            continue

    raise RuntimeError(
        "Este nodo no tiene la bóveda local de Clave SOL (credenciales_seguras.json / .env). "
        "La emisión en SUNAT SEE-SOL solo está autorizada en la Laptop Madre (NIVEL_2_MADRE_HOTEL)."
    )


def calcular_valor_unitario_105(precio_con_igv: float) -> float:
    """Ley N° 31556 Hotelería/Turismo (IGV 10.5%): Valor Unitario = Precio / 1.105 (4 decimales)."""
    return round(float(precio_con_igv) / FACTOR_IGV_HOTEL, 4)


def _es_error_red(msg: Any) -> bool:
    s = str(msg or "")
    return any(t in s for t in ERRORES_RED)


def _en_menu_internet_final(url_str: str) -> bool:
    u = str(url_str or "")
    return "/MenuInternet.htm" in u and "AutenticaMenuInternet.htm" not in u


def iniciar_sesion_sol_blindado(page, url_login: str, ruc_emisor: str, usuario: str, clave: str, intentos: int = 4) -> None:
    """Login OAuth2 SUNAT SEE-SOL blindado contra 503 nginx y redirecciones intermedias."""
    ultimo_error = None
    for i in range(1, intentos + 1):
        try:
            cargado_200 = False
            for _ in range(1, 9):
                resp = page.goto(url_login, wait_until="domcontentloaded", timeout=15000)
                st_code = resp.status if resp else 0
                titulo = (page.title() or "").lower()
                if st_code == 200 and "503" not in titulo and "unavailable" not in titulo:
                    cargado_200 = True
                    break
                time.sleep(0.6)

            if not cargado_200:
                raise RuntimeError("SUNAT devolvió 503 Service Temporarily Unavailable en múltiples nodos.")

            page.wait_for_selector("#txtRuc", state="visible", timeout=12000)
            time.sleep(0.35)

            page.evaluate(
                """([origUrl, stateVal]) => {
                    const ou = document.getElementById('originalUrl') || document.querySelector('input[name="originalUrl"]');
                    if (ou) ou.value = origUrl;
                    const st = document.querySelector('input[name="state"]');
                    if (st) st.value = stateVal;
                }""",
                [AUTENTICA_SOL_URL, STATE_SOL_DEFAULT],
            )

            page.fill("#txtRuc", ruc_emisor)
            page.fill("#txtUsuario", usuario)
            page.fill("#txtContrasena", clave)
            page.locator("#btnAceptar").click(no_wait_after=True, timeout=10000)

            t0 = time.time()
            rescatado = False
            while time.time() - t0 < 28.0:
                cur_url = page.url or ""
                if _en_menu_internet_final(cur_url):
                    return
                try:
                    tit_act = (page.title() or "").lower()
                except Exception:
                    tit_act = ""
                if ("503" in tit_act or "unavailable" in tit_act) and not rescatado:
                    rescatado = True
                    page.goto(URL_MENU_SOL, wait_until="domcontentloaded", timeout=15000)
                    if _en_menu_internet_final(page.url):
                        return
                if "api-seguridad.sunat.gob.pe" in cur_url and "code=" in cur_url and not rescatado:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(cur_url).query)
                    jwt_code = (qs.get("code") or [""])[0]
                    if jwt_code:
                        rescatado = True
                        salto = (
                            f"{AUTENTICA_SOL_URL}?state={urllib.parse.quote(STATE_SOL_DEFAULT, safe='')}"
                            f"&code={urllib.parse.quote(jwt_code, safe='')}"
                        )
                        page.goto(salto, wait_until="domcontentloaded", timeout=20000)
                        for _ in range(15):
                            if _en_menu_internet_final(page.url):
                                return
                            time.sleep(0.3)
                time.sleep(0.3)

            raise RuntimeError(f"Timeout esperando MenuInternet.htm (URL actual: {(page.url or '')[:120]})")
        except Exception as e:
            ultimo_error = e
            time.sleep(0.8)
    raise RuntimeError(f"No se pudo iniciar sesión en SUNAT tras {intentos} intentos: {ultimo_error}")


# ==============================================================================
# 3. EMISIÓN DE BOLETAS (EB01) Y FACTURAS (E001) CON IGV 10.5% EN CARPETA LOCAL
# ==============================================================================

def _agregar_items_sunat_dojo(frame, items: List[Dict[str, Any]]) -> None:
    """Registra la lista de ítems en el formulario Dojo de SUNAT SEE-SOL con IGV 10.5%."""
    for idx, it in enumerate(items, 1):
        cant = max(1, int(it.get("cantidad", 1)))
        precio_unit = float(it.get("precio_unitario", it.get("monto", 0)))
        valor_unit = float(it.get("valor_unitario") or calcular_valor_unitario_105(precio_unit))
        valor_str = f"{valor_unit:.4f}"
        desc = str(it.get("descripcion", "")).strip().upper()

        frame.get_by_role("button", name="Adicionar").dispatch_event("click", timeout=5000)
        inp_desc = frame.locator('[id="item.descripcion"]')
        inp_desc.wait_for(state="visible", timeout=7000)
        time.sleep(0.2)

        # 1. Marcar Radio 'Servicio' (subTipoTI02)
        frame.evaluate("""() => {
            const el = document.getElementById('item.subTipoTI02');
            if (el) {
                el.checked = true;
                el.dispatchEvent(new Event('click', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (window.dijit && dijit.byId('item.subTipoTI02')) {
                dijit.byId('item.subTipoTI02').set('checked', true);
            }
        }""")
        time.sleep(0.35)

        # 2. Seleccionar IGV 10.5% antes del Valor Unitario
        def _marcar_igv_105():
            frame.evaluate("""() => {
                const radios = document.querySelectorAll('input[name="tipoIGV"]');
                if (radios.length > 1) {
                    const r105 = radios[1];
                    r105.checked = true;
                    r105.dispatchEvent(new Event('click', { bubbles: true }));
                    r105.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.dijit && r105.id && dijit.byId(r105.id)) {
                        dijit.byId(r105.id).set('checked', true);
                    }
                }
            }""")

        _marcar_igv_105()
        time.sleep(0.35)

        # 3. Cantidad y Descripción
        inp_cant = frame.locator('[id="item.cantidad"]')
        if inp_cant.count() > 0:
            inp_cant.fill(str(cant))

        inp_desc.fill(desc)
        time.sleep(0.15)

        # 4. Valor Unitario sin IGV (4 decimales)
        inp_precio = frame.locator('[id="item.precioUnitario"]')
        for _ in range(1, 4):
            _marcar_igv_105()
            time.sleep(0.15)
            inp_precio.click(force=True, timeout=3000)
            inp_precio.fill("")
            inp_precio.fill(valor_str)
            frame.evaluate(
                """(valStr) => {
                    const el = document.getElementById('item.precioUnitario');
                    if (el) {
                        el.value = valStr;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (window.dijit && dijit.byId('item.precioUnitario')) {
                        try { dijit.byId('item.precioUnitario').set('value', valStr); } catch (e) {}
                    }
                }""",
                valor_str,
            )
            inp_precio.press("Tab")
            time.sleep(0.45)

            estado_item = frame.evaluate("""() => {
                const el = document.getElementById('item.precioUnitario');
                const v = el ? String(el.value || '').trim() : '';
                const igvEl = document.getElementById('item.igv') || document.getElementById('item.mtoIgv');
                const totEl = document.getElementById('item.importeTotal') || document.getElementById('item.total');
                const txtModal = (
                    (igvEl ? igvEl.value : '') + ' ' +
                    (totEl ? totEl.value : '') + ' ' +
                    (el && el.closest('form, .dijitDialog') ? el.closest('form, .dijitDialog').innerText : '')
                ).toLowerCase();
                return {
                    val: v,
                    num: parseFloat(v.replace(',', '')) || 0,
                    hasUndefined: txtModal.includes('undefined') || txtModal.includes('nan')
                };
            }""")
            if estado_item.get("num", 0) > 0 and not estado_item.get("hasUndefined"):
                break
            time.sleep(0.3)

        # 5. Aceptar Ítem
        def _clic_aceptar_item():
            btn_aceptar = frame.locator('[id="item.botonAceptar"]')
            if btn_aceptar.count() > 0 and btn_aceptar.first.is_visible():
                btn_aceptar.first.click(force=True, timeout=3000)
            else:
                frame.get_by_role("button", name="Aceptar").first.click(force=True, timeout=3000)

        _clic_aceptar_item()
        try:
            inp_desc.wait_for(state="hidden", timeout=5500)
        except Exception:
            time.sleep(0.4)
            if inp_desc.is_visible():
                _clic_aceptar_item()
                try:
                    inp_desc.wait_for(state="hidden", timeout=4500)
                except Exception:
                    raise RuntimeError(
                        f"SUNAT no aceptó el ítem #{idx} ('{desc[:45]}') en el formulario Dojo. Verifique monto y descripción."
                    )


def emitir_comprobante_sunat_local(
    tipo: str,
    doc_num: str,
    cliente_nombre: str,
    items: List[Dict[str, Any]],
    emitir_final: bool = True,
    headless: bool = True,
    etapa_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Emite Boleta (EB01) o Factura (E001) en SUNAT SEE-SOL y guarda el PDF exclusivamente
    en la carpeta local `02_DB_sqlite_local/comprobantes_pdf/boletas/` o `facturas/`.
    Si `emitir_final=False` (Modo Borrador), toma captura de la Vista Preliminar en `capturas/` y no emite.
    """
    tipo_norm = str(tipo or "BOLETA").upper().strip()
    if MODO_PRUEBA:
        emitir_final = False

    with adquirir_candado_sunat_exclusivo():
        res: Dict[str, Any] = {"status": "ERROR", "tipo": tipo_norm, "error": "No ejecutado."}
        for intento in range(1, 3):
            estado = {"emitir_pulsado": False}
            res = _emitir_comprobante_intento(
                tipo_norm,
                str(doc_num or "").strip(),
                str(cliente_nombre or "CLIENTES VARIOS").strip().upper(),
                items,
                emitir_final,
                headless,
                estado,
                etapa_cb=etapa_cb,
            )
            if res.get("status") == "OK":
                return res
            if estado["emitir_pulsado"] or not _es_error_red(res.get("error")) or intento == 2:
                return res
            time.sleep(2.0)
        return res


def _emitir_comprobante_intento(
    tipo: str,
    doc_num: str,
    cliente_nombre: str,
    items: List[Dict[str, Any]],
    emitir_final: bool,
    headless: bool,
    estado: Dict[str, bool],
    etapa_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    ruc_emisor, usuario, clave = obtener_credenciales_sunat()
    tipo = tipo.upper().strip()
    for d in (BOLETAS_PDF_DIR, FACTURAS_PDF_DIR, CAPTURAS_SUNAT_DIR):
        d.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = lanzar_edge_sunat(p, oculto=bool(headless))
        context = crear_contexto_sunat(browser, viewport={"width": 1600, "height": 1150})
        page = context.new_page()

        try:
            url_login = (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
                "?lang=es-PE&showDni=true&showLanguages=false"
                f"&originalUrl={AUTENTICA_SOL_URL}"
                f"&state={STATE_SOL_DEFAULT}"
            )
            if etapa_cb:
                try:
                    etapa_cb(f"🔐 Laptop 2 Madre: Iniciando sesión en SUNAT SEE-SOL ({tipo.title()})...")
                except Exception:
                    pass
            iniciar_sesion_sol_blindado(page, url_login, ruc_emisor, usuario, clave)
            time.sleep(0.3)

            alerta_sunat = [False, ""]
            page.on("dialog", lambda d: (alerta_sunat.__setitem__(0, True), alerta_sunat.__setitem__(1, d.message or ""), d.accept()))

            for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
                try:
                    btn = page.get_by_role("button", name=btn_text)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        time.sleep(0.25)
                except Exception:
                    pass

            target_sub = "Factura Electrónica" if tipo == "FACTURA" else "Boleta de Venta Electrónica"
            target_leaf = "Emitir Factura" if tipo == "FACTURA" else "Emitir Boleta de Venta"
            page.get_by_role("heading", name="Empresas").first.wait_for(state="visible", timeout=25000)

            menu_ok = False
            for _ in range(1, 5):
                try:
                    page.get_by_role("heading", name="Empresas").first.click(timeout=10000)
                    time.sleep(0.75)
                    if not page.get_by_text(target_sub).first.is_visible():
                        if not page.get_by_text("SEE - SOL").first.is_visible():
                            page.locator("#nivel1_11 > .glyphicon").first.click(timeout=5000)
                            time.sleep(0.5)
                        if not page.get_by_text(target_sub).first.is_visible():
                            page.get_by_text("SEE - SOL").first.click(timeout=5000)
                            time.sleep(0.55)
                    page.get_by_text(target_sub).first.wait_for(state="visible", timeout=3500)
                    menu_ok = True
                    break
                except Exception:
                    time.sleep(0.6)

            if not menu_ok:
                page.get_by_text(target_sub).first.wait_for(state="visible", timeout=5000)

            page.get_by_text(target_sub).first.click(timeout=5000)
            time.sleep(0.45)
            page.get_by_text(target_leaf).first.click(timeout=5000)

            frame = None
            for _ in range(180):
                for f in page.frames:
                    if f.name == "iframeApplication" or "11.5.4.1.1" in f.url or "ww1" in f.url:
                        try:
                            if f.locator('[id="inicio.numeroDocumento"], [widgetid="inicio.tipoDocumento"]').count() > 0:
                                frame = f
                                break
                        except Exception:
                            pass
                if frame:
                    break
                time.sleep(0.25)

            if not frame:
                raise RuntimeError("No se pudo cargar el formulario dentro de iframeApplication en SUNAT.")

            if etapa_cb:
                try:
                    etapa_cb(f"📝 Laptop 2 Madre: Formulario SUNAT listo · Llenando {doc_num or cliente_nombre} e ítems...")
                except Exception:
                    pass

            doc_limpio = str(doc_num or "").strip()
            nombre_receptor_sunat = cliente_nombre

            def _esperar_autocompletado_cliente(max_s: float = 3.0) -> str:
                t_c = time.time()
                while time.time() - t_c < max_s:
                    if alerta_sunat[0]:
                        break
                    try:
                        val_rz = frame.evaluate("""() => {
                            const el = document.getElementById('inicio.razonSocial') || document.getElementById('inicio.descripcionTipoDocumento');
                            return el ? (el.value || '').trim() : '';
                        }""")
                        if val_rz and len(val_rz) >= 3:
                            time.sleep(0.15)
                            return str(val_rz).strip().upper()
                    except Exception:
                        pass
                    time.sleep(0.2)
                return ""

            if tipo == "FACTURA":
                if len(doc_limpio) != 11 or not doc_limpio.isdigit():
                    raise ValueError(f"Para FACTURA se requiere RUC de 11 dígitos válido (recibido: '{doc_limpio}').")

                inp_ruc = frame.locator('[id="inicio.numeroDocumento"]').first
                inp_ruc.click()
                inp_ruc.fill(doc_limpio)
                inp_ruc.press("Tab")
                rz_auto = _esperar_autocompletado_cliente(3.0)
                if rz_auto:
                    nombre_receptor_sunat = rz_auto
                if alerta_sunat[0]:
                    raise ValueError(f"SUNAT rechazó el RUC {doc_limpio}: {alerta_sunat[1] or 'RUC no válido'}.")

                try:
                    frame.locator("label:has-text('Contado')").first.click(timeout=2000)
                except Exception:
                    frame.evaluate("""() => {
                        const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                        for (const r of radios) {
                            if ((r.value || '').toUpperCase() === 'CONTADO' || (r.id || '').includes('contado')) {
                                r.checked = true;
                                r.dispatchEvent(new Event('click', { bubbles: true }));
                            }
                        }
                    }""")
            else:
                if len(doc_limpio) == 7 and doc_limpio.isdigit():
                    doc_limpio = doc_limpio.zfill(8)
                es_dni = len(doc_limpio) == 8 and doc_limpio.isdigit()

                if es_dni:
                    inp_doc = frame.locator('[id="inicio.numeroDocumento"]').first
                    inp_doc.click()
                    inp_doc.fill(doc_limpio)
                    time.sleep(0.2)
                    try:
                        frame.locator('[id="inicio.pagoAnticipado.show"]').get_by_role("cell", name="No", exact=True).click(timeout=2500)
                    except Exception:
                        inp_doc.press("Tab")
                    rz_auto = _esperar_autocompletado_cliente(2.8)
                    if rz_auto:
                        nombre_receptor_sunat = rz_auto
                    if alerta_sunat[0]:
                        raise ValueError(
                            f"SUNAT/RENIEC rechazó el DNI {doc_limpio}: {alerta_sunat[1] or 'DNI no encontrado en RENIEC'}. "
                            "Verifique y corrija el DNI del huésped antes de emitir."
                        )
                else:
                    nom_final = (cliente_nombre or "CLIENTES VARIOS").upper()
                    wid_btn = frame.locator("[widgetid='inicio.tipoDocumento'] .dijitArrowButtonContainer").first
                    if wid_btn.count() > 0:
                        wid_btn.click()
                        time.sleep(0.5)
                        frame.locator(".dijitMenuItem").filter(has_text="SIN DOCUMENTO").first.click()
                        time.sleep(0.6)
                    else:
                        frame.evaluate("""() => {
                            if (window.dijit && dijit.byId('inicio.tipoDocumento')) {
                                dijit.byId('inicio.tipoDocumento').set('value', '-');
                            }
                        }""")
                        time.sleep(0.6)

                    rz = frame.locator('[id="inicio.razonSocial"]')
                    rz.click()
                    rz.fill(nom_final)
                    time.sleep(0.25)

            frame.get_by_role("button", name="Continuar").dispatch_event("click")
            frame.get_by_role("button", name="Adicionar").wait_for(state="visible", timeout=10000)

            _agregar_items_sunat_dojo(frame, items)

            t_pre = time.time()
            ultimo_clic_cont = 0.0
            while time.time() - t_pre < 15.0:
                try:
                    if frame.get_by_role("button", name="Emitir").is_visible():
                        break
                except Exception:
                    pass
                if time.time() - ultimo_clic_cont >= 1.1:
                    try:
                        btn_c = frame.get_by_role("button", name="Continuar")
                        if btn_c.count() > 0 and btn_c.first.is_visible():
                            btn_c.first.dispatch_event("click")
                            ultimo_clic_cont = time.time()
                    except Exception:
                        pass
                time.sleep(0.25)

            if not emitir_final:
                nom_cap = f"borrador_{tipo.lower()}_{int(time.time())}.png"
                ruta_cap = str(CAPTURAS_SUNAT_DIR / nom_cap)
                png_bytes = page.screenshot(path=ruta_cap, full_page=True)
                if not frame.get_by_role("button", name="Emitir").is_visible():
                    raise RuntimeError("SUNAT no llegó a la Vista Preliminar (revise ítems/datos). Captura: " + nom_cap)
                return {
                    "status": "OK",
                    "modo": "borrador",
                    "tipo": tipo,
                    "nro_comprobante": "BORRADOR-PRELIMINAR",
                    "captura": ruta_cap,
                    "nombre_captura": nom_cap,
                    "captura_b64": base64.b64encode(png_bytes).decode("ascii") if png_bytes else "",
                    "receptor_nombre": nombre_receptor_sunat,
                    "receptor_doc": doc_limpio,
                    "mensaje": "Vista Preliminar alcanzada con éxito en SUNAT (sin emitir).",
                }

            # Emisión Definitiva en SUNAT
            if not frame.get_by_role("button", name="Emitir").first.is_visible():
                nom_cap_err = f"error_pre_emitir_{tipo.lower()}_{int(time.time())}.png"
                page.screenshot(path=str(CAPTURAS_SUNAT_DIR / nom_cap_err), full_page=True)
                raise RuntimeError(f"SUNAT no habilitó el botón Emitir en Vista Preliminar (captura: {nom_cap_err}).")

            if etapa_cb:
                try:
                    etapa_cb(f"🧾 Laptop 2 Madre: Confirmando emisión de {tipo.title()} en SUNAT y descargando PDF...")
                except Exception:
                    pass

            estado["emitir_pulsado"] = True
            time.sleep(0.4)
            btn_conf = frame.locator("#dlgBtnAceptarConfirm, #dlgBtnAceptar")
            for _ in range(3):
                try:
                    frame.get_by_role("button", name="Emitir").first.dispatch_event("click", timeout=3500)
                except Exception:
                    try:
                        frame.get_by_role("button", name="Emitir").first.click(force=True, timeout=3500)
                    except Exception:
                        pass
                try:
                    btn_conf.first.wait_for(state="visible", timeout=3500)
                    break
                except Exception:
                    if frame.get_by_role("dialog", name="Confirmar").count() > 0:
                        break
                    time.sleep(0.4)

            btn_dl = frame.locator(
                "button:has-text('Descargar PDF'), a:has-text('Descargar PDF'), span:has-text('Descargar PDF'), [widgetid*='pdf'], [widgetid*='PDF']"
            ).first

            # Disparar Aceptar en el diálogo Confirmar de Dojo con triple respaldo hasta que aparezca Descargar PDF
            t_conf = time.time()
            while time.time() - t_conf < 22.0:
                try:
                    if btn_dl.count() > 0 and btn_dl.is_visible():
                        break
                except Exception:
                    pass
                try:
                    if btn_conf.count() > 0 and btn_conf.first.is_visible():
                        btn_conf.first.dispatch_event("click", timeout=2000)
                except Exception:
                    pass
                try:
                    dlg_c = frame.get_by_role("dialog", name="Confirmar")
                    if dlg_c.count() > 0:
                        dlg_c.get_by_role("button", name="Aceptar").first.dispatch_event("click", timeout=2000)
                except Exception:
                    pass
                try:
                    frame.evaluate("""() => {
                        for (const id of ['dlgBtnAceptarConfirm', 'dlgBtnAceptar']) {
                            if (window.dijit && dijit.byId(id)) {
                                try { dijit.byId(id).onClick(); } catch (e) {}
                            }
                            const el = document.getElementById(id);
                            if (el) { try { el.click(); } catch (e) {} }
                        }
                        const btns = Array.from(document.querySelectorAll('.dijitDialogPaneActionBar .dijitButtonNode, .dijitDialog .dijitButtonContents'));
                        for (const b of btns) {
                            if ((b.textContent || '').includes('Aceptar')) {
                                try { b.click(); } catch (e) {}
                            }
                        }
                    }""")
                except Exception:
                    pass
                time.sleep(0.75)

            prefijo = "E001" if tipo == "FACTURA" else "EB01"
            try:
                btn_dl.wait_for(state="visible", timeout=6000)
                texto_constancia_dom = ""
                try:
                    texto_constancia_dom = frame.locator("body").inner_text()
                except Exception:
                    pass
                with page.expect_download(timeout=25000) as dl_info:
                    try:
                        btn_dl.dispatch_event("click", timeout=5000)
                    except Exception:
                        btn_dl.click(force=True, timeout=5000)
                download = dl_info.value
            except Exception as exc_dl:
                texto_rescate = ""
                try:
                    texto_rescate = frame.locator("body").inner_text()
                except Exception:
                    pass
                m_rescate = re.search(rf"\b({prefijo}\s*-\s*\d+)\b", texto_rescate) if texto_rescate else None
                if m_rescate:
                    nro_cp_rescate = m_rescate.group(1).replace(" ", "")
                    cap_rescate = str(CAPTURAS_SUNAT_DIR / f"constancia_{tipo.lower()}_{nro_cp_rescate}_{int(time.time())}.png")
                    try:
                        page.screenshot(path=cap_rescate, full_page=True)
                    except Exception:
                        pass
                    return {
                        "status": "OK",
                        "tipo": tipo,
                        "nro_comprobante": nro_cp_rescate,
                        "archivo_pdf": "",
                        "archivo_pdf_original": "",
                        "nombre_pdf": "",
                        "pdf_pendiente_descarga": True,
                        "receptor_nombre": nombre_receptor_sunat,
                        "receptor_doc": doc_limpio,
                        "fecha_emision": datetime.now().strftime("%d/%m/%Y"),
                        "texto_constancia_dom": texto_rescate,
                        "mensaje": f"{tipo} {nro_cp_rescate} emitida en SUNAT (PDF pendiente de descarga incremental).",
                    }
                raise exc_dl

            hoy_dt = datetime.now()
            carpeta_dia = f"{hoy_dt.day:02d}_{MESES_CARPETA.get(hoy_dt.month, 'Mes')}"
            dir_tipo_base = FACTURAS_PDF_DIR if tipo == "FACTURA" else BOLETAS_PDF_DIR
            dir_dia_dest = dir_tipo_base / carpeta_dia
            dir_dia_dest.mkdir(parents=True, exist_ok=True)

            nombre_pdf_orig = download.suggested_filename or f"{tipo}_{int(time.time())}.pdf"
            ruta_pdf_orig = str(dir_dia_dest / nombre_pdf_orig)
            download.save_as(ruta_pdf_orig)

            nro_cp = ""
            if texto_constancia_dom:
                m_cp = re.search(rf"\b({prefijo}\s*-\s*\d+)\b", texto_constancia_dom)
                if m_cp:
                    nro_cp = m_cp.group(1).replace(" ", "")

            if not nro_cp and nombre_pdf_orig:
                m_cp = re.search(r"([EFB][B0-9]{3})-?(\d{1,8})", nombre_pdf_orig)
                if m_cp:
                    nro_cp = f"{m_cp.group(1)}-{int(m_cp.group(2))}"

            if not nro_cp:
                txt_pdf_check = _extraer_texto_pdf_si_posible(ruta_pdf_orig)
                if txt_pdf_check:
                    m_cp_pdf = re.search(rf"\b({prefijo}\s*-\s*\d+)\b", txt_pdf_check)
                    if m_cp_pdf:
                        nro_cp = m_cp_pdf.group(1).replace(" ", "")

            if not nro_cp:
                cap_sin_num = str(CAPTURAS_SUNAT_DIR / f"sin_nro_{tipo.lower()}_{int(time.time())}.png")
                try:
                    page.screenshot(path=cap_sin_num, full_page=True)
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "tipo": tipo,
                    "emitir_pulsado": True,
                    "estado_incierto": True,
                    "archivo_pdf": ruta_pdf_orig,
                    "captura_error": cap_sin_num,
                    "error": (
                        "⚠️ ESTADO INCIERTO POST-EMISIÓN (se pulsó Emitir pero no se pudo leer el número de comprobante; "
                        "verifique en SUNAT antes de reintentar)."
                    ),
                }

            ruta_pdf_final = ruta_pdf_orig
            if nro_cp:
                cli_slug = re.sub(r"[^A-Z0-9]+", "_", str(nombre_receptor_sunat or doc_limpio or "CLIENTE").upper()).strip("_")[:36]
                pref_nom = "FACTURA" if tipo == "FACTURA" else "BOLETA"
                nombre_limpio = f"{pref_nom}_{nro_cp}_{cli_slug}.pdf"
                ruta_limpia = str(dir_dia_dest / nombre_limpio)
                try:
                    if os.path.abspath(ruta_limpia) != os.path.abspath(ruta_pdf_orig):
                        if os.path.exists(ruta_limpia):
                            try:
                                os.remove(ruta_limpia)
                            except Exception:
                                pass
                        shutil.move(ruta_pdf_orig, ruta_limpia)
                        ruta_pdf_final = ruta_limpia
                        ruta_pdf_orig = ruta_pdf_final
                except Exception:
                    pass

            return {
                "status": "OK",
                "tipo": tipo,
                "nro_comprobante": nro_cp,
                "archivo_pdf": ruta_pdf_final,
                "archivo_pdf_original": ruta_pdf_final,
                "nombre_pdf": os.path.basename(ruta_pdf_final),
                "receptor_nombre": nombre_receptor_sunat,
                "receptor_doc": doc_limpio,
                "fecha_emision": hoy_dt.strftime("%d/%m/%Y"),
                "texto_constancia_dom": texto_constancia_dom,
                "mensaje": f"{tipo} {nro_cp} emitida y guardada localmente en 02_DB_sqlite_local/comprobantes_pdf.",
            }

        except Exception as e:
            err_cap = str(CAPTURAS_SUNAT_DIR / f"error_emision_{tipo.lower()}_{int(time.time())}.png")
            try:
                page.screenshot(path=err_cap, full_page=True)
            except Exception:
                pass
            emitir_puls = bool(estado.get("emitir_pulsado"))
            msg_err = str(e)
            if emitir_puls:
                msg_err = (
                    "⚠️ ESTADO INCIERTO POST-EMISIÓN (se pulsó Emitir antes del fallo; verifique en SUNAT antes de reintentar): "
                    + msg_err
                )
            return {
                "status": "ERROR",
                "tipo": tipo,
                "emitir_pulsado": emitir_puls,
                "estado_incierto": emitir_puls,
                "error": msg_err,
                "captura_error": err_cap,
            }
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


# ==============================================================================
# 4. EMISIÓN DE NOTAS DE CRÉDITO ELECTRÓNICAS EN CARPETA LOCAL
# ==============================================================================

def generar_pdf_nota_credito_local(nc_data: Dict[str, Any], dir_destino: Path) -> str:
    """Genera PDF de respaldo de Nota de Crédito si el portal de SUNAT no entregó descarga directa."""
    from playwright.sync_api import sync_playwright

    dir_destino.mkdir(parents=True, exist_ok=True)
    nro_nc = str(nc_data.get("numero_cp") or "EB01-00").strip()
    fec_emision = str(nc_data.get("fecha_emision") or datetime.now().strftime("%d/%m/%Y"))
    cp_mod = str(nc_data.get("cp_modificado") or "")
    receptor_nom = str(nc_data.get("receptor_nombre") or "")
    receptor_doc = str(nc_data.get("receptor_doc") or "")
    motivo = str(nc_data.get("motivo_nc") or "ANULACION DE LA OPERACION")
    monto_total = float(nc_data.get("monto_total") or 0.0)
    op_gravada = round(monto_total / FACTOR_IGV_HOTEL, 2)
    igv = round(monto_total - op_gravada, 2)
    val_unit = f"{monto_total / FACTOR_IGV_HOTEL:.4f}"
    imp_vta = f"{monto_total:.2f}"
    desc = f"ANULACION DE COMPROBANTE {cp_mod}"

    html_content = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; color: #000; margin: 25px 35px; }}
  .header-table, .datos-table, .items-table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
  .ruc-box {{ width: 38%; border: 2px solid #000; text-align: center; padding: 10px 5px; }}
  .items-table th {{ border-top: 1px solid #000; border-bottom: 1px solid #000; padding: 5px 4px; text-align: left; }}
  .items-table td {{ padding: 5px 4px; }}
  .totales-table {{ width: 45%; margin-left: auto; border-collapse: collapse; margin-bottom: 15px; }}
  .totales-table td {{ padding: 2px 4px; text-align: right; }}
</style></head><body>
<table class="header-table"><tr>
  <td style="width:60%;vertical-align:top;">
    <div style="font-weight:bold;font-size:13px;">EL OVALO MARIATEGUI</div>
    <div style="font-weight:bold;">MAELEN HOGAR EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA</div>
    <div>AV. PAZ 507 INT. A URB. VILLA MILITAR MARISCAL NIETO - MOQUEGUA</div>
  </td>
  <td class="ruc-box">
    <h2 style="margin:0 0 5px 0;font-size:13px;">RUC: 20611986484</h2>
    <h3 style="margin:0 0 5px 0;font-size:11px;">REPRESENTACION INTERNA DE NOTA DE CREDITO ELECTRONICA (RESPALDO LOCAL)</h3>
    <h1 style="margin:0;font-size:14px;">{nro_nc}</h1>
  </td>
</tr></table>
<table class="datos-table">
  <tr><td style="width:25%;"><strong>Documento que modifica</strong></td><td>: {cp_mod}</td></tr>
  <tr><td><strong>Fecha de Emisión</strong></td><td>: {fec_emision}</td></tr>
  <tr><td><strong>Señor(es)</strong></td><td>: {receptor_nom}</td></tr>
  <tr><td><strong>DNI / RUC</strong></td><td>: {receptor_doc}</td></tr>
  <tr><td><strong>Motivo o Sustento</strong></td><td>: {motivo}</td></tr>
</table>
<table class="items-table">
  <thead><tr><th>Cantidad</th><th>Unidad</th><th>Descripción</th><th style="text-align:right;">Valor Unit.</th><th style="text-align:right;">Importe</th></tr></thead>
  <tbody><tr><td>1.00</td><td>UNIDAD</td><td>{desc}</td><td style="text-align:right;">{val_unit}</td><td style="text-align:right;">{imp_vta}</td></tr></tbody>
</table>
<table class="totales-table">
  <tr><td>Op. Gravada :</td><td>S/ {op_gravada:.2f}</td></tr>
  <tr><td>IGV (10.5%) :</td><td>S/ {igv:.2f}</td></tr>
  <tr style="border-top:1px solid #000;font-weight:bold;"><td>Importe Total :</td><td>S/ {monto_total:.2f}</td></tr>
</table>
</body></html>"""

    nom_cte = re.sub(r"[^A-Z0-9_]", "_", receptor_nom.upper())[:30] or "CLIENTE"
    nombre_archivo = f"NOTA_CREDITO_{nro_nc.replace(' ', '')}_{nom_cte}.pdf"
    ruta_pdf = str(dir_destino / nombre_archivo)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html_content, wait_until="load")
        page.pdf(path=ruta_pdf, format="A4", print_background=True)
        browser.close()
    return ruta_pdf


def emitir_nota_credito_sunat_local(
    tipo_cp: str,
    numero_comprobante: str,
    motivo: str = "ANULACION DE LA OPERACION",
    modo_borrador: bool = True,
    headless: bool = True,
) -> Dict[str, Any]:
    """
    Emite Nota de Crédito Electrónica en SUNAT SEE-SOL para anular Boleta (EB01) o Factura (E001).
    Guarda el PDF y las capturas exclusivamente en `02_DB_sqlite_local/comprobantes_pdf/notas_credito/` y `capturas/`.
    """
    from playwright.sync_api import sync_playwright

    if MODO_PRUEBA:
        modo_borrador = True
    emitir_final = not modo_borrador
    tipo_norm = "FACTURA" if str(tipo_cp or "").upper().startswith("F") else "BOLETA"

    m_num = re.search(r"(\d+)$", str(numero_comprobante or "").strip())
    if not m_num:
        return {"status": "ERROR", "error": f"Número de comprobante inválido: {numero_comprobante}"}
    correlativo_str = str(int(m_num.group(1)))

    ruc_emisor, usuario, clave = obtener_credenciales_sunat()
    NOTAS_CREDITO_PDF_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURAS_SUNAT_DIR.mkdir(parents=True, exist_ok=True)

    nom_cap = f"nc_{'borrador' if modo_borrador else 'emitida'}_{tipo_norm.lower()}_{int(time.time())}.png"
    ruta_cap = str(CAPTURAS_SUNAT_DIR / nom_cap)
    datos_cp_origen: Dict[str, Any] = {}

    with adquirir_candado_sunat_exclusivo():
        with sync_playwright() as p:
            browser = lanzar_edge_sunat(p, oculto=bool(headless))
            context = crear_contexto_sunat(browser, viewport={"width": 1600, "height": 950})
            page = context.new_page()

            try:
                url_login = (
                    "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
                    "?lang=es-PE&showDni=true&showLanguages=false"
                    f"&originalUrl={AUTENTICA_SOL_URL}&state={STATE_SOL_DEFAULT}"
                )
                iniciar_sesion_sol_blindado(page, url_login, ruc_emisor, usuario, clave)
                time.sleep(1.0)

                for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click()
                            time.sleep(0.4)
                    except Exception:
                        pass

                target_sub = "Boleta de Venta Electrónica" if tipo_norm == "BOLETA" else "Factura Electrónica"
                page.get_by_role("heading", name="Empresas").first.wait_for(state="visible", timeout=25000)

                for _ in range(1, 5):
                    try:
                        page.get_by_role("heading", name="Empresas").first.click(timeout=10000)
                        time.sleep(0.75)
                        if not page.get_by_text(target_sub).first.is_visible():
                            if not page.get_by_text("SEE - SOL").first.is_visible():
                                page.locator("#nivel1_11 > .glyphicon").first.click(timeout=5000)
                                time.sleep(0.5)
                            if not page.get_by_text(target_sub).first.is_visible():
                                page.get_by_text("SEE - SOL").first.click(timeout=5000)
                                time.sleep(0.55)
                        page.get_by_text(target_sub).first.wait_for(state="visible", timeout=3500)
                        break
                    except Exception:
                        time.sleep(0.6)

                page.get_by_text(target_sub).first.click(timeout=5000)
                time.sleep(0.6)

                clicked_nc = False
                for loc in page.get_by_text("Emitir Nota de Crédito", exact=True).all():
                    try:
                        if loc.is_visible():
                            loc.click(timeout=5000)
                            clicked_nc = True
                            break
                    except Exception:
                        pass
                if not clicked_nc:
                    page.get_by_text("Emitir Nota de Crédito").first.click(timeout=5000)

                time.sleep(2.0)
                frame = None
                campo_id = "pantallaInicial.numeroBVE" if tipo_norm == "BOLETA" else "pantallaInicial.numeroFE"
                for _ in range(35):
                    for f in page.frames:
                        if f.name == "iframeApplication" or "11.5.4.1.2" in f.url or "11.5.3" in f.url or "ww1" in f.url:
                            try:
                                if f.locator(f'[id="{campo_id}"]').is_visible():
                                    frame = f
                                    break
                            except Exception:
                                pass
                    if frame:
                        break
                    time.sleep(0.8)

                if not frame:
                    raise RuntimeError("No se pudo abrir el formulario de Nota de Crédito en SUNAT.")

                try:
                    input_tipo = frame.locator("input[type='text']").nth(3)
                    input_tipo.click()
                    time.sleep(0.5)
                    try:
                        frame.get_by_role("option", name=re.compile(r"Anulaci[oó]n\s+de\s+la\s+Operaci[oó]n", re.I)).click(timeout=3000)
                    except Exception:
                        frame.locator(".dijitMenuItem:has-text('Anulaci')").first.click(timeout=3000)
                except Exception:
                    frame.evaluate("""() => {
                        const opts = Array.from(document.querySelectorAll('tr, div, li, span'));
                        for (const o of opts) {
                            if (o.textContent && o.textContent.toLowerCase().includes('anulacion de la operacion')) {
                                o.click();
                                break;
                            }
                        }
                    }""")
                time.sleep(0.7)

                campo_num = frame.locator(f'[id="{campo_id}"]')
                campo_num.click()
                campo_num.fill(correlativo_str)
                time.sleep(0.3)

                campo_motivo = frame.locator('[id="pantallaInicial.motivoEmisionNC"]')
                campo_motivo.click()
                campo_motivo.fill(motivo)
                time.sleep(0.3)

                try:
                    frame.get_by_role("button", name="Continuar").click(timeout=5000)
                except Exception:
                    frame.get_by_role("button", name="Continuar").dispatch_event("click")
                time.sleep(2.8)

                try:
                    texto_prelim = frame.locator("body").inner_text()
                    m_nom = re.search(r"Se[ñn]or\(es\)\s*:\s*([^\n\r\t]+)", texto_prelim, re.I)
                    if m_nom:
                        datos_cp_origen["cliente"] = m_nom.group(1).strip()
                    m_monto = re.search(r"Importe\s+Total\s*:\s*S/?\s*([\d\.,]+)", texto_prelim, re.I)
                    if m_monto:
                        datos_cp_origen["monto"] = float(m_monto.group(1).replace(",", ""))
                except Exception:
                    pass

                if not emitir_final:
                    png_bytes = page.screenshot(path=ruta_cap, full_page=True)
                    return {
                        "status": "BORRADOR_GENERADO",
                        "modo": "borrador",
                        "tipo_cp": tipo_norm,
                        "cp_origen": numero_comprobante,
                        "nro_nota_credito": "BORRADOR-NC-PRELIMINAR",
                        "captura_pantalla": ruta_cap,
                        "nombre_captura": nom_cap,
                        "captura_b64": base64.b64encode(png_bytes).decode("ascii") if png_bytes else "",
                        "datos_cp_origen": datos_cp_origen,
                    }

                # Emisión Definitiva de Nota de Crédito
                try:
                    btn_emitir = frame.locator("button:has-text('Emitir'), [value='Emitir'], input[value='Emitir']").first
                    if btn_emitir.count() > 0:
                        btn_emitir.click(timeout=10000)
                    else:
                        frame.get_by_role("button", name="Emitir").click(timeout=10000)
                except Exception:
                    frame.get_by_role("button", name="Emitir").dispatch_event("click")
                time.sleep(1.8)

                dialog_confirmar = frame.get_by_role("dialog", name="Confirmar")
                if dialog_confirmar.count() > 0:
                    try:
                        dialog_confirmar.get_by_role("button", name="Aceptar").dispatch_event("click", timeout=4000)
                    except Exception:
                        dialog_confirmar.get_by_role("button", name="Aceptar").click(force=True, timeout=4000)
                else:
                    try:
                        frame.get_by_role("button", name="Aceptar").dispatch_event("click", timeout=4000)
                    except Exception:
                        frame.get_by_role("button", name="Aceptar").click(force=True, timeout=4000)

                time.sleep(3.2)
                page.screenshot(path=ruta_cap, full_page=True)

                cp_mod_norm = f"{'EB01' if tipo_norm == 'BOLETA' else 'E001'}-{correlativo_str}"
                texto_exito = frame.locator("body").inner_text()
                cands_nc = [
                    re.sub(r"\s+", "", c.upper())
                    for c in re.findall(r"((?:FC01|BC01|E001|EB01)\s*-\s*\d+)", texto_exito, re.I)
                ]
                cands_nc_filt = [c for c in cands_nc if c != cp_mod_norm]
                nro_nc = cands_nc_filt[0] if cands_nc_filt else f"NC-{tipo_norm}-{correlativo_str}"

                hoy_dt = datetime.now()
                carpeta_dia = f"{hoy_dt.day:02d}_{MESES_CARPETA.get(hoy_dt.month, 'Mes')}"
                dir_destino = NOTAS_CREDITO_PDF_DIR / ("nc_boletas" if tipo_norm == "BOLETA" else "nc_facturas") / carpeta_dia
                dir_destino.mkdir(parents=True, exist_ok=True)

                pdf_descargado = ""
                try:
                    btn_descargar = frame.locator(
                        "text='Descargar PDF', [widgetid*='pdf'], [widgetid*='PDF'], span:has-text('Descargar PDF'), button:has-text('Descargar PDF'), a:has-text('Descargar PDF')"
                    ).first
                    if btn_descargar.count() > 0:
                        with page.expect_download(timeout=20000) as download_info:
                            try:
                                btn_descargar.dispatch_event("click")
                            except Exception:
                                btn_descargar.click(force=True)
                        download = download_info.value
                        nom_cte = re.sub(r"[^A-Z0-9]+", "_", str(datos_cp_origen.get("cliente") or "CLIENTE").upper()).strip("_")[:36] or "CLIENTE"
                        nombre_pdf_limpio = f"NOTA_CREDITO_{nro_nc}_{nom_cte}.pdf"
                        ruta_pdf_limpia = str(dir_destino / nombre_pdf_limpio)
                        download.save_as(ruta_pdf_limpia)
                        pdf_descargado = ruta_pdf_limpia
                except Exception:
                    pass

                if not pdf_descargado:
                    try:
                        pdf_descargado = generar_pdf_nota_credito_local(
                            {
                                "numero_cp": nro_nc,
                                "fecha_emision": hoy_dt.strftime("%d/%m/%Y"),
                                "cp_modificado": cp_mod_norm,
                                "receptor_nombre": datos_cp_origen.get("cliente", ""),
                                "receptor_doc": datos_cp_origen.get("doc", ""),
                                "monto_total": datos_cp_origen.get("monto", 0.0),
                                "motivo_nc": motivo,
                            },
                            dir_destino,
                        )
                    except Exception:
                        pass

                return {
                    "status": "EMITIDO",
                    "modo": "definitivo",
                    "tipo_cp": tipo_norm,
                    "cp_origen": cp_mod_norm,
                    "nro_nota_credito": nro_nc,
                    "motivo": motivo,
                    "pdf_descargado": pdf_descargado,
                    "captura_pantalla": ruta_cap,
                    "nombre_captura": nom_cap,
                    "captura_b64": "",
                    "datos_cp_origen": datos_cp_origen,
                }

            except Exception as e_gral:
                try:
                    page.screenshot(path=ruta_cap, full_page=True)
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "tipo_cp": tipo_norm,
                    "cp_origen": numero_comprobante,
                    "error": str(e_gral),
                }
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass


# ==============================================================================
# 5. CONVERSOR DE PDF A DATOS ESTRUCTURADOS (SQLite -> SUPABASE)
# ==============================================================================

def _extraer_texto_pdf_si_posible(ruta_pdf: str) -> str:
    """Extrae el texto del PDF local (si pypdf/pymupdf está disponible) como verificación adicional."""
    if not ruta_pdf or not os.path.isfile(ruta_pdf):
        return ""
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            reader = mod.PdfReader(ruta_pdf)
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            continue
    try:
        import fitz  # type: ignore
        doc = fitz.open(ruta_pdf)
        txt = "\n".join(p.get_text() for p in doc)
        doc.close()
        return txt
    except Exception:
        return ""


def extraer_hab_y_fecha_de_descripcion(desc: str, fecha_emision_dmy: str = "", hab_fallback: str = "") -> Tuple[str, str]:
    """
    Extrae (hab, noche_dmy) de una glosa SUNAT tipo:
    'DIA DE ALOJAMIENTO DEL 22 DE NOVIEMBRE, HAB N 204, IDENTIFICADO CON DNI...'
    """
    texto = str(desc or "").upper()
    hab = str(hab_fallback or "").strip()
    if not hab:
        m_hab = re.search(r"HAB(?:ITACI[OÓ]N)?\.?\s*(?:N[°º\.]?)?\s*([A-Z]?\d{3})", texto)
        if m_hab:
            hab = m_hab.group(1)

    f_em = str(fecha_emision_dmy or datetime.now().strftime("%d/%m/%Y")).strip()
    partes_em = f_em.split("/")
    anio_def = int(partes_em[2]) if len(partes_em) == 3 and partes_em[2].isdigit() else datetime.now().year
    mes_def = int(partes_em[1]) if len(partes_em) == 3 and partes_em[1].isdigit() else datetime.now().month

    m_dmy = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", texto)
    if m_dmy:
        dd = int(m_dmy.group(1))
        mm = int(m_dmy.group(2))
        yy = int(m_dmy.group(3)) if m_dmy.group(3) else anio_def
        if yy < 100:
            yy += 2000
        if 1 <= dd <= 31 and 1 <= mm <= 12:
            return hab, f"{dd:02d}/{mm:02d}/{yy:04d}"

    m_mes = re.search(
        r"(?:DEL|DIA|D[IÍ]AS)\s+(\d{1,2})\s+DE\s+(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|SETIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)",
        texto,
    )
    if m_mes:
        dd = int(m_mes.group(1))
        mm = MESES_ES_NUM.get(m_mes.group(2), mes_def)
        anio_calc = anio_def - 1 if (mes_def == 1 and mm == 12) else anio_def
        if 1 <= dd <= 31:
            return hab, f"{dd:02d}/{mm:02d}/{anio_calc:04d}"

    return hab, f_em


def convertir_comprobante_a_datos_y_subir_supabase(
    res_emision: Dict[str, Any],
    data_norm: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convierte el comprobante emitido (y su PDF local en `02_DB_sqlite_local/comprobantes_pdf/`)
    en información estructurada dentro de `sunat_ventas` y `sunat_lineas_cache`, y lo empuja a Supabase.
    Así los PDFs quedan locales en Laptop Madre mientras toda la información viaja a todas las laptops.
    """
    from sync_nube import push_to_supabase

    accion = str(data_norm.get("accion") or "").upper()
    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hoy_dmy = datetime.now().strftime("%d/%m/%Y")

    # CASO 1: NOTA DE CRÉDITO -> Registrar NC en sunat_ventas y marcar comprobante origen como ANULADO (NCE)
    if accion == "NOTA_CREDITO":
        nro_nc = str(res_emision.get("nro_nota_credito") or "").strip().upper()
        cp_origen = str(res_emision.get("cp_origen") or data_norm.get("nro_comprobante") or "").strip().upper()
        motivo = str(data_norm.get("motivo") or "ANULACION DE LA OPERACION").strip().upper()
        datos_orig = res_emision.get("datos_cp_origen") or {}
        ruta_pdf_nc = str(res_emision.get("pdf_descargado") or "")
        txt_pdf = _extraer_texto_pdf_si_posible(ruta_pdf_nc)

        monto_nc = float(datos_orig.get("monto") or 0.0)
        cliente_nc = str(datos_orig.get("cliente") or data_norm.get("cliente") or "").strip().upper()
        doc_nc = str(datos_orig.get("doc") or "").strip()

        if not monto_nc and txt_pdf:
            m_t = re.search(r"Importe\s+Total\s*:\s*S/?\s*([\d\.,]+)", txt_pdf, re.I)
            if m_t:
                try:
                    monto_nc = float(m_t.group(1).replace(",", ""))
                except Exception:
                    pass

        partes_nc = nro_nc.split("-") if "-" in nro_nc else [nro_nc, "0"]
        serie_nc = partes_nc[0]
        num_nc = int(partes_nc[1]) if len(partes_nc) > 1 and partes_nc[1].isdigit() else 0
        partes_fec = hoy_dmy.split("/")
        periodo_mes = f"{partes_fec[2]}-{partes_fec[1]}" if len(partes_fec) == 3 else datetime.now().strftime("%Y-%m")

        with db_transaction() as conn:
            # Si no teníamos monto/doc del origen, leerlo de sunat_ventas
            if cp_origen:
                orig_row = conn.execute(
                    "SELECT receptor_doc, receptor_nombre, total, monto_total FROM sunat_ventas WHERE nro_cp = ? LIMIT 1",
                    (cp_origen,),
                ).fetchone()
                if orig_row:
                    if not monto_nc:
                        monto_nc = float(orig_row["monto_total"] or orig_row["total"] or 0.0)
                    if not doc_nc:
                        doc_nc = str(orig_row["receptor_doc"] or "")
                    if not cliente_nc:
                        cliente_nc = str(orig_row["receptor_nombre"] or "")
                conn.execute(
                    "UPDATE sunat_ventas SET estado = 'ANULADO (NCE)', updated_at = ?, actualizado_en = ? WHERE nro_cp = ? AND coalesce(tipo_doc, tipo, '') != 'NOTA_CREDITO'",
                    (ahora_str, ahora_str, cp_origen),
                )
                conn.execute(
                    "UPDATE sunat_lineas_cache SET estado = 'ANULADO (NCE)' WHERE nro_cp = ? AND coalesce(tipo, '') != 'NOTA_CREDITO'",
                    (cp_origen,),
                )

            if nro_nc and nro_nc != "BORRADOR-NC-PRELIMINAR":
                resumen_nc = f"NOTA DE CREDITO {nro_nc} ANULA {cp_origen} ({motivo})"
                origen_meta = json.dumps(
                    {
                        "cp_modificado": cp_origen,
                        "motivo": motivo,
                        "hab": data_norm.get("hab", ""),
                        "estancia_uuid": data_norm.get("estanciaUuid", ""),
                        "pdf_local_madre": os.path.basename(ruta_pdf_nc) if ruta_pdf_nc else "",
                        "nodo_emisor": ROL_NODO,
                    },
                    ensure_ascii=False,
                )
                conn.execute(
                    """
                    INSERT INTO sunat_ventas (
                        periodo_mes, tipo_doc, tipo, nro_cp, serie, numero,
                        fecha_emision, receptor_doc_tipo, receptor_doc, receptor_nombre,
                        moneda, total, monto_total, estado, resumen_items, items_json,
                        origen_json, updated_at, actualizado_en
                    ) VALUES (?, 'NOTA_CREDITO', 'NOTA_CREDITO', ?, ?, ?, ?, ?, ?, ?, 'PEN', ?, ?, 'VIGENTE', ?, ?, ?, ?, ?)
                    ON CONFLICT(tipo_doc, nro_cp) DO UPDATE SET
                        total = excluded.total,
                        monto_total = excluded.monto_total,
                        resumen_items = excluded.resumen_items,
                        origen_json = excluded.origen_json,
                        updated_at = excluded.updated_at,
                        actualizado_en = excluded.actualizado_en
                    """,
                    (
                        periodo_mes,
                        nro_nc,
                        serie_nc,
                        num_nc,
                        hoy_dmy,
                        "RUC" if len(doc_nc) == 11 else ("DNI" if len(doc_nc) == 8 else "-"),
                        doc_nc,
                        cliente_nc or "CLIENTES VARIOS",
                        -abs(round(monto_nc, 2)),
                        -abs(round(monto_nc, 2)),
                        resumen_nc,
                        json.dumps([{"cantidad": 1, "descripcion": resumen_nc, "precio_unitario": -abs(round(monto_nc, 2))}], ensure_ascii=False),
                        origen_meta,
                        ahora_str,
                        ahora_str,
                    ),
                )

        push_to_supabase(limit=40)
        return {"ok": True, "nro_cp": nro_nc, "tabla": "sunat_ventas"}

    # CASO 2: BOLETA (EB01) O FACTURA (E001)
    nro_cp = str(res_emision.get("nro_comprobante") or "").strip().upper()
    if not nro_cp or nro_cp == "BORRADOR-PRELIMINAR":
        return {"ok": False, "motivo": "BORRADOR_NO_PERSISTE_EN_SUNAT_VENTAS"}

    tipo_doc = "FACTURA" if str(data_norm.get("tipo") or "").upper() == "FACTURA" else "BOLETA"
    fecha_emision = str(res_emision.get("fecha_emision") or hoy_dmy).strip()
    partes_f = fecha_emision.split("/")
    periodo_mes = f"{partes_f[2]}-{partes_f[1]}" if len(partes_f) == 3 else datetime.now().strftime("%Y-%m")

    partes_cp = nro_cp.split("-") if "-" in nro_cp else [nro_cp, "0"]
    serie = partes_cp[0]
    numero = int(partes_cp[1]) if len(partes_cp) > 1 and partes_cp[1].isdigit() else 0

    receptor_doc = str(res_emision.get("receptor_doc") or data_norm.get("documento") or "").strip()
    receptor_nombre = str(res_emision.get("receptor_nombre") or data_norm.get("cliente") or "CLIENTES VARIOS").strip().upper()
    receptor_doc_tipo = "RUC" if len(receptor_doc) == 11 else ("DNI" if len(receptor_doc) == 8 else "SIN_DOC")

    ruta_pdf = str(res_emision.get("archivo_pdf") or "")
    txt_pdf = _extraer_texto_pdf_si_posible(ruta_pdf)
    if txt_pdf:
        m_rz = re.search(r"Se[ñn]or\(es\)\s*:\s*([^\n\r]+)", txt_pdf, re.I)
        if m_rz and len(m_rz.group(1).strip()) >= 3 and receptor_nombre in ("", "CLIENTES VARIOS"):
            receptor_nombre = m_rz.group(1).strip().upper()

    items: List[Dict[str, Any]] = list(data_norm.get("items") or [])
    total_esperado = round(float(data_norm.get("total_monto") or sum(float(i.get("precio_unitario", 0)) * int(i.get("cantidad", 1)) for i in items)), 2)
    total_monto = total_esperado
    texto_fuente_tot = "\n".join(x for x in (txt_pdf, str(res_emision.get("texto_constancia_dom") or "")) if x)
    if texto_fuente_tot:
        m_tot_sunat = re.search(r"Importe\s+Total\s*:\s*(?:S/?|\$)?\s*([0-9]+(?:\.[0-9]{2}))", texto_fuente_tot, re.I)
        if m_tot_sunat:
            try:
                tot_sunat = round(float(m_tot_sunat.group(1)), 2)
                if tot_sunat > 0 and (total_esperado <= 0 or abs(tot_sunat - total_esperado) <= 0.10):
                    total_monto = tot_sunat
            except Exception:
                pass
    op_gravada = round(total_monto / FACTOR_IGV_HOTEL, 2)
    igv_105 = round(total_monto - op_gravada, 2)

    hab_default = str(data_norm.get("hab") or "").strip()
    items_enriquecidos = []
    resumen_partes = []
    for it in items:
        cant = max(1, int(it.get("cantidad") or 1))
        desc = str(it.get("descripcion") or "").strip().upper()
        p_unit = round(float(it.get("precio_unitario") or 0.0), 2)
        v_unit = round(float(it.get("valor_unitario") or calcular_valor_unitario_105(p_unit)), 4)
        subtotal = round(cant * p_unit, 2)
        hab_it, noche_it = extraer_hab_y_fecha_de_descripcion(desc, fecha_emision, hab_default)
        items_enriquecidos.append({
            "cantidad": cant,
            "unidad": "NIU",
            "descripcion": desc,
            "precio_unitario": p_unit,
            "valor_unitario": v_unit,
            "subtotal": subtotal,
            "hab": hab_it,
            "noche_dmy": noche_it,
        })
        if desc:
            resumen_partes.append(desc)

    resumen_items = " | ".join(resumen_partes)[:600]
    origen_meta = {
        "estancia_uuid": data_norm.get("estanciaUuid", ""),
        "hab": hab_default,
        "dni_huesped": data_norm.get("dni_huesped", ""),
        "telefono": data_norm.get("telefono", ""),
        "op_gravada": op_gravada,
        "igv_105": igv_105,
        "total": total_monto,
        "total_esperado_panel": total_esperado,
        "nombre_pdf_local": os.path.basename(ruta_pdf) if ruta_pdf else "",
        "carpeta_local_madre": str(Path(ruta_pdf).parent.name) if ruta_pdf else "",
        "nodo_emisor": ROL_NODO,
        "emitido_ts": ahora_str,
    }

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO sunat_ventas (
                periodo_mes, tipo_doc, tipo, nro_cp, serie, numero,
                fecha_emision, receptor_doc_tipo, receptor_doc, receptor_nombre,
                moneda, total, monto_total, estado, resumen_items, items_json,
                origen_json, updated_at, actualizado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PEN', ?, ?, 'VIGENTE', ?, ?, ?, ?, ?)
            ON CONFLICT(tipo_doc, nro_cp) DO UPDATE SET
                periodo_mes = excluded.periodo_mes,
                tipo = excluded.tipo,
                fecha_emision = excluded.fecha_emision,
                receptor_doc = excluded.receptor_doc,
                receptor_nombre = excluded.receptor_nombre,
                total = excluded.total,
                monto_total = excluded.monto_total,
                estado = excluded.estado,
                resumen_items = excluded.resumen_items,
                items_json = excluded.items_json,
                origen_json = excluded.origen_json,
                updated_at = excluded.updated_at,
                actualizado_en = excluded.actualizado_en
            """,
            (
                periodo_mes,
                tipo_doc,
                tipo_doc,
                nro_cp,
                serie,
                numero,
                fecha_emision,
                receptor_doc_tipo,
                receptor_doc,
                receptor_nombre,
                total_monto,
                total_monto,
                resumen_items,
                json.dumps(items_enriquecidos, ensure_ascii=False),
                json.dumps(origen_meta, ensure_ascii=False),
                ahora_str,
                ahora_str,
            ),
        )

        # Reemplazar líneas en sunat_lineas_cache para que Contabilidad y Cruce Tricolor las vean al instante
        conn.execute("DELETE FROM sunat_lineas_cache WHERE nro_cp = ?", (nro_cp,))
        for it_e in items_enriquecidos:
            conn.execute(
                """
                INSERT INTO sunat_lineas_cache (
                    nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                    cliente, doc, fecha_emision, estado, descripcion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VIGENTE', ?)
                """,
                (
                    nro_cp,
                    tipo_doc,
                    it_e["noche_dmy"],
                    it_e["hab"],
                    it_e["subtotal"],
                    total_monto,
                    receptor_nombre,
                    receptor_doc,
                    fecha_emision,
                    it_e["descripcion"],
                ),
            )

    push_to_supabase(limit=50)
    return {
        "ok": True,
        "nro_cp": nro_cp,
        "tipo": tipo_doc,
        "total": total_monto,
        "op_gravada": op_gravada,
        "igv_105": igv_105,
        "lineas_registradas": len(items_enriquecidos),
    }


# ==============================================================================
# 6. BÚSQUEDA DE PDF LOCAL Y ENVÍO POR WHATSAPP DE RECEPCIÓN (BAILEYS + FALLBACK WIN32)
# ==============================================================================

def normalizar_telefono_peru(telefono: str) -> str:
    tel = "".join(ch for ch in str(telefono or "") if ch.isdigit())
    if len(tel) == 9:
        tel = "51" + tel
    return tel


def _es_ruta_pdf_autorizada(ruta: str) -> bool:
    """Garantiza que solo se adjunten/sirvan archivos .pdf dentro de las carpetas de comprobantes autorizadas."""
    if not ruta:
        return False
    try:
        p = Path(str(ruta)).resolve()
        if p.suffix.lower() != ".pdf" or not p.is_file():
            return False
        bases_ok = (
            COMPROBANTES_PDF_DIR.resolve(),
            (DATA_DIR / "descargas_recepcion").resolve(),
            HOSTAL_SISTEMA_DIR.resolve(),
        )
        return any(p.is_relative_to(b) for b in bases_ok)
    except Exception:
        return False


def buscar_pdf_comprobante_local(nro_cp: str = "", ruc_dni: str = "", cliente: str = "", pista_ruta: str = "") -> Optional[str]:
    """
    Busca el PDF del comprobante priorizando la carpeta local `02_DB_sqlite_local/comprobantes_pdf/`
    y dejando `C:\\Hostal_Sistema` únicamente como lectura histórica de comprobantes antiguos.
    """
    if pista_ruta and _es_ruta_pdf_autorizada(pista_ruta):
        return str(Path(pista_ruta).resolve())

    patrones = [
        str(BOLETAS_PDF_DIR / "**" / "*.pdf"),
        str(FACTURAS_PDF_DIR / "**" / "*.pdf"),
        str(NOTAS_CREDITO_PDF_DIR / "**" / "*.pdf"),
        str(COMPROBANTES_PDF_DIR / "*.pdf"),
        # Fallback histórico solo lectura
        str(HOSTAL_SISTEMA_DIR / "boletas_sunat" / "**" / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "facturas_sunat" / "**" / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "descargas_recepcion" / "*.pdf"),
    ]
    archivos: List[str] = []
    for pat in patrones:
        archivos.extend(glob.glob(pat, recursive=True))
    if not archivos:
        return None
    archivos.sort(key=os.path.getmtime, reverse=True)

    nro_clean = str(nro_cp or "").strip().upper()
    if nro_clean:
        m_cp = re.match(r"^([A-Z0-9]+)-0*(\d+)$", nro_clean)
        if m_cp:
            serie, num = m_cp.group(1), int(m_cp.group(2))
            pat_cp = re.compile(rf"(?<![A-Z0-9]){re.escape(serie)}-?0*{num}(?![0-9])", re.I)
        else:
            pat_cp = re.compile(rf"(?<![A-Z0-9]){re.escape(nro_clean)}(?![A-Z0-9])", re.I)
        for ruta in archivos[:500]:
            nom = os.path.basename(ruta)
            if pat_cp.search(nom) and _es_ruta_pdf_autorizada(ruta):
                return str(Path(ruta).resolve())
        return None

    terminos = []
    if pista_ruta and str(pista_ruta).lower().endswith(".pdf"):
        terminos.append(os.path.basename(pista_ruta).lower())
    if ruc_dni:
        dig = re.sub(r"\D", "", str(ruc_dni).strip())
        if len(dig) >= 8:
            terminos.append(dig.lower())

    terminos_limpios = [t for t in terminos if t]
    if not terminos_limpios:
        return None
    for ruta in archivos[:200]:
        nom = os.path.basename(ruta).lower()
        if any(t in nom for t in terminos_limpios) and _es_ruta_pdf_autorizada(ruta):
            return str(Path(ruta).resolve())
    return None


def _wsp_daemon_baileys_conectado() -> bool:
    conn = get_connection()
    try:
        r = conn.execute("SELECT valor FROM wsp_recepcion_estado WHERE clave = 'conexion'").fetchone()
        if r and str(r[0] or "").strip().upper().startswith("CONECT"):
            return True
        r2 = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'wsp_recepcion_conexion'").fetchone()
        if r2 and str(r2[0] or "").strip().upper().startswith("CONECT"):
            return True
    except Exception:
        pass
    finally:
        conn.close()
    return False


def _encolar_y_esperar_wsp_daemon(
    telefono_norm: str,
    mensaje: str,
    ruta_pdf: str,
    nro_cp: str = "",
    cliente: str = "",
    timeout_s: float = 18.0,
) -> Tuple[bool, str]:
    """
    Inserta el envío en `wsp_recepcion_cola_envio` con las columnas exactas que consume `wsp_daemon.js`
    (Baileys en Laptop 2) y espera hasta `timeout_s` a que marque `ENVIADO` (0 ventanas, 100% silencioso).
    Si vence `timeout_s`, deja la fila en `PENDIENTE` sin cambiarla ni duplicarla con Desktop fallback.
    """
    nombre_adj = os.path.basename(ruta_pdf) if ruta_pdf else ""
    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO wsp_recepcion_cola_envio (
                telefono_o_jid, destino_telefono, mensaje, texto,
                ruta_adjunto, archivo_ruta, nombre_adjunto,
                nro_comprobante, cliente_nombre, estado, creado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', ?)
            """,
            (
                telefono_norm,
                telefono_norm,
                mensaje,
                mensaje,
                ruta_pdf,
                ruta_pdf,
                nombre_adj,
                nro_cp,
                cliente,
                ahora_str,
            ),
        )
        conn.commit()
        cola_id = cur.lastrowid
    finally:
        conn.close()

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(0.45)
        c_chk = get_connection()
        try:
            row = c_chk.execute(
                "SELECT estado, detalle_error FROM wsp_recepcion_cola_envio WHERE id = ?",
                (cola_id,),
            ).fetchone()
            if row:
                st = str(row["estado"] or "").upper()
                if st == "ENVIADO":
                    return True, f"Enviado silenciosamente por Baileys (cola #{cola_id})."
                if st == "ERROR":
                    return False, str(row["detalle_error"] or "Error en wsp_daemon.js")
        finally:
            c_chk.close()

    return True, f"Encolado en Baileys Recepción (cola #{cola_id}, envío en curso)."


# --- Fallback WhatsApp Desktop Invisible (absorbido de brazo_wsp.py) ---

def _poner_archivo_en_portapapeles(ruta_archivo: str) -> bool:
    ruta_archivo = os.path.abspath(ruta_archivo)
    try:
        import struct
        import win32clipboard  # type: ignore
        import win32con  # type: ignore

        offset = 20
        dropfiles = struct.pack("IIIIi", offset, 0, 0, 0, 1)
        data = dropfiles + (ruta_archivo + "\0\0").encode("utf-16le")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    cmd = ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -LiteralPath '{ruta_archivo}'"]
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return res.returncode == 0


def _enviar_via_whatsapp_desktop_invisible(telefono_norm: str, mensaje: str, ruta_archivo: Optional[str] = None) -> bool:
    """Fallback de envío por WhatsApp Desktop en Laptop 2 cuando wsp_daemon.js no está activo."""
    url_encoded = urllib.parse.quote(mensaje) if mensaje else ""
    uri = f"whatsapp://send?phone={telefono_norm}&text={url_encoded}"

    try:
        res_tl = subprocess.run(
            ["tasklist", "/NH", "/FI", "IMAGENAME eq WhatsApp*"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=4,
        )
        ya_abierto = "whatsapp" in (res_tl.stdout or "").lower()
    except Exception:
        ya_abierto = False

    try:
        os.startfile(uri)
    except Exception:
        subprocess.run(["cmd", "/c", "start", "", uri], creationflags=CREATE_NO_WINDOW)

    time.sleep(1.8 if ya_abierto else 5.0)

    def _press_enter():
        try:
            import pyautogui  # type: ignore
            pyautogui.FAILSAFE = False
            pyautogui.press("enter")
            return
        except Exception:
            pass
        try:
            import win32com.client  # type: ignore
            win32com.client.Dispatch("WScript.Shell").SendKeys("{ENTER}")
        except Exception:
            pass

    def _press_ctrl_v():
        try:
            import pyautogui  # type: ignore
            pyautogui.FAILSAFE = False
            pyautogui.hotkey("ctrl", "v")
            return
        except Exception:
            pass
        try:
            import win32com.client  # type: ignore
            win32com.client.Dispatch("WScript.Shell").SendKeys("^v")
        except Exception:
            pass

    if mensaje:
        _press_enter()
        time.sleep(0.9)

    if ruta_archivo and os.path.isfile(ruta_archivo):
        if not _poner_archivo_en_portapapeles(ruta_archivo):
            raise RuntimeError("No se pudo copiar el PDF al portapapeles de Windows.")
        time.sleep(0.35)
        _press_ctrl_v()
        time.sleep(3.0)
        _press_enter()
        time.sleep(1.5)

    return True


def despachar_wsp_comprobante_local(
    telefono: str,
    pdf_path: str,
    mensaje: str,
    nro_cp: str = "",
    cliente: str = "",
    ruc_dni: str = "",
) -> Tuple[bool, str, str]:
    """
    Despacha el mensaje + PDF del comprobante desde la Laptop Madre (L2):
    1. Localiza el PDF en `02_DB_sqlite_local/comprobantes_pdf/`.
    2. Si `wsp_daemon.js` (Baileys) está conectado, envía el PDF en segundo plano (0 ventanas).
    3. Si no, usa el fallback de WhatsApp Desktop local.
    """
    if MODO_PRUEBA:
        return False, "🧪 MODO PRUEBA: envío WhatsApp deshabilitado.", ""

    tel_norm = normalizar_telefono_peru(telefono)
    if not tel_norm:
        return False, "", "Número de teléfono inválido."

    ruta_pdf_real = buscar_pdf_comprobante_local(nro_cp=nro_cp, ruc_dni=ruc_dni, cliente=cliente, pista_ruta=pdf_path) or ""
    if (pdf_path or nro_cp) and not ruta_pdf_real:
        return False, "", f"No se encontró el PDF local del comprobante {nro_cp or pdf_path} en 02_DB_sqlite_local/comprobantes_pdf."

    # Vía 1: Baileys wsp_daemon.js (100% silencioso, sin abrir ventanas)
    if _wsp_daemon_baileys_conectado():
        ok_b, msg_b = _encolar_y_esperar_wsp_daemon(
            telefono_norm=tel_norm,
            mensaje=mensaje,
            ruta_pdf=ruta_pdf_real,
            nro_cp=nro_cp,
            cliente=cliente,
            timeout_s=18.0,
        )
        if ok_b:
            return True, msg_b, ""
        return False, "", msg_b

    # Vía 2: Solo usar Desktop si está explícitamente habilitado en .env; por defecto encolar en Baileys sin robar foco
    if os.environ.get("OVALO_PERMITIR_WSP_DESKTOP_FALLBACK") == "1":
        try:
            _enviar_via_whatsapp_desktop_invisible(tel_norm, mensaje, ruta_pdf_real or None)
            return True, "Enviado por WhatsApp Desktop.", ""
        except Exception as exc:
            return False, "", str(exc)

    _encolar_y_esperar_wsp_daemon(
        telefono_norm=tel_norm,
        mensaje=mensaje,
        ruta_pdf=ruta_pdf_real,
        nro_cp=nro_cp,
        cliente=cliente,
        timeout_s=0.0,
    )
    return (
        False,
        "",
        "WhatsApp Recepción (Baileys) está reconectando; el comprobante quedó en cola y puede reenviarse con 'Reenviar WSP'.",
    )


# ==============================================================================
# 7. REVISOR INCREMENTAL DIARIO DE FALTANTES SUNAT (100% HEADLESS, 0 VENTANAS)
# ==============================================================================

def _buscar_pdf_solo_en_carpeta_local_v2(nro_cp: str) -> Optional[str]:
    """Verifica si ya existe un PDF para `nro_cp` dentro de `02_DB_sqlite_local/comprobantes_pdf/`."""
    if not nro_cp:
        return None
    cp_clean = nro_cp.strip().upper()
    cp_nodash = cp_clean.replace("-", "")
    for pat in (
        str(BOLETAS_PDF_DIR / "**" / "*.pdf"),
        str(FACTURAS_PDF_DIR / "**" / "*.pdf"),
        str(NOTAS_CREDITO_PDF_DIR / "**" / "*.pdf"),
        str(COMPROBANTES_PDF_DIR / "*.pdf"),
    ):
        for ruta in glob.glob(pat, recursive=True):
            nom = os.path.basename(ruta).upper()
            if cp_clean in nom or cp_nodash in nom:
                return os.path.abspath(ruta)
    return None


def _limpiar_pdfs_raw_duplicados_locales() -> int:
    """
    Elimina archivos raw duplicados (`PDF-BOLETA*`, `PDF-DOC*`) en `02_DB_sqlite_local/comprobantes_pdf/`
    cuando en la misma carpeta ya existe el archivo con nombre limpio (`BOLETA_*`, `FACTURA_*`, `NOTA_CREDITO_*`).
    """
    eliminados = 0
    for base_d in (BOLETAS_PDF_DIR, FACTURAS_PDF_DIR, NOTAS_CREDITO_PDF_DIR):
        for ruta in glob.glob(str(base_d / "**" / "PDF-*.pdf"), recursive=True):
            try:
                carpeta = Path(ruta).parent
                limpios = [
                    p for p in carpeta.glob("*.pdf")
                    if not p.name.upper().startswith("PDF-")
                ]
                if limpios:
                    os.remove(ruta)
                    eliminados += 1
            except Exception:
                pass
    return eliminados


def _extraer_fila_consulta_see_sol(row_locator, tipo_esperado: str) -> Dict[str, Any]:
    """
    Extrae Fecha, Nro Comprobante (`r"(E001|EB01)\\s*-\\s*(\\d+)"`), Receptor, Monto y Estado
    desde una fila de `Consultar Factura y Nota` o `Consultar Boleta de Venta y Nota`.
    """
    info: Dict[str, Any] = {
        "tipo_doc": tipo_esperado,
        "fecha_emision": "",
        "nro_cp": "",
        "receptor_doc": "",
        "receptor_nombre": "",
        "monto_total": 0.0,
        "estado": "VIGENTE",
    }
    try:
        celdas = row_locator.locator("td, div.dojoxGridCell").all()
        textos = [c.inner_text().strip() for c in celdas]
        texto_fila = " | ".join(textos)

        m_cp = re.search(r"(E001|EB01)\s*-\s*(\d+)", texto_fila, re.I)
        if m_cp:
            info["nro_cp"] = f"{m_cp.group(1).upper()}-{int(m_cp.group(2))}"

        for c_txt in textos:
            if not info["fecha_emision"] and re.match(r"^\d{2}/\d{2}/\d{4}$", c_txt):
                info["fecha_emision"] = c_txt
            if " - " in c_txt and not re.search(r"(E001|EB01)\s*-\s*\d+", c_txt, re.I):
                doc_p, nom_p = c_txt.split(" - ", 1)
                doc_digits = re.sub(r"\D", "", doc_p)
                if len(doc_digits) in (8, 11):
                    info["receptor_doc"] = doc_digits
                    info["receptor_nombre"] = nom_p.strip().upper()
            if "S/" in c_txt or re.match(r"^\d+[\.,]\d{2}$", c_txt.replace("S/", "").strip()):
                try:
                    val_m = float(c_txt.replace("S/", "").replace(",", "").replace("\xa0", "").strip())
                    if val_m > 0:
                        info["monto_total"] = val_m
                except Exception:
                    pass

        row_html = row_locator.inner_html().lower()
        if any(k in row_html for k in ("anulado", "✓", "glyphicon-ok")):
            info["estado"] = "ANULADO (NCE)"
        for c_txt in textos:
            cu = c_txt.upper()
            if any(k in cu for k in ("ANULAD", "CANCELAD", "NCE")) and "ANULACION DE LA OPERACION" not in cu and "ANULACIÓN DE LA OPERACIÓN" not in cu:
                info["estado"] = "ANULADO (NCE)"
    except Exception:
        pass
    return info


def _procesar_y_descargar_popup_consulta_sunat(
    popup_page,
    info_base: Dict[str, Any],
    ruta_pdf_limpia: str,
) -> Dict[str, Any]:
    """
    Lee el emergente (`verImprimir`) de SUNAT SEE-SOL para extraer glosa, DNI/RUC, nombre y monto exacto,
    y guarda el PDF único directamente en `ruta_pdf_limpia` (`headless=True`).
    """
    out = dict(info_base)
    items_extraidos: List[Dict[str, Any]] = []
    try:
        popup_page.wait_for_load_state("domcontentloaded", timeout=12000)
        time.sleep(1.2)
        texto_pag = popup_page.inner_text("body")

        if not out.get("receptor_doc"):
            m_dni = re.search(r"\bDNI\s*:\s*(\d{8})\b", texto_pag, re.I)
            if m_dni:
                out["receptor_doc"] = m_dni.group(1)
            else:
                for r_cand in re.findall(r"\bRUC\s*:\s*(\d{11})\b", texto_pag, re.I):
                    if r_cand != "20611986484":
                        out["receptor_doc"] = r_cand
                        break

        m_nom = re.search(r"Se[ñn\xa0\?]+or\(es\)\s*:\s*([^\n\r\t]+)", texto_pag, re.I)
        if m_nom:
            nom_clean = re.sub(r"\s+", " ", m_nom.group(1).replace("\ufffd", "").replace("\xa0", " ")).strip().upper()
            if len(nom_clean) >= 3:
                out["receptor_nombre"] = nom_clean

        m_tot = re.search(r"Importe\s+Total\s*:\s*S/?\s*([0-9\.,]+)", texto_pag, re.I)
        if m_tot:
            try:
                out["monto_total"] = float(m_tot.group(1).replace(",", "").strip())
            except Exception:
                pass

        for tr in popup_page.locator("tr").all():
            try:
                t_row = re.sub(r"\s+", " ", tr.inner_text().strip()).upper()
                if any(k in t_row for k in ("ALOJAMIENTO", "HOSPEDAJE", "HAB")):
                    desc_limpia = re.sub(r"^\s*\d+(?:\.\d+)?\s*(?:UNIDAD|ZZ|NIU|SERV|EA|DOC)?\s*", "", t_row, flags=re.I).strip()
                    if len(desc_limpia) >= 8:
                        items_extraidos.append({
                            "cantidad": 1,
                            "descripcion": desc_limpia[:240],
                            "precio_unitario": float(out.get("monto_total") or 0.0),
                        })
                        break
            except Exception:
                pass

        if not items_extraidos:
            m_glosa = re.search(r"((?:DIA|D[IÍ]AS|POR|SERVICIO|ALOJAMIENTO|HOSPEDAJE)[^\n\r]{6,180})", texto_pag, re.I)
            glosa_txt = m_glosa.group(1).strip().upper() if m_glosa else f"ALOJAMIENTO SEGUN {out.get('nro_cp', '')}"
            items_extraidos.append({
                "cantidad": 1,
                "descripcion": glosa_txt,
                "precio_unitario": float(out.get("monto_total") or 0.0),
            })

        out["items"] = items_extraidos

        # Guardar PDF único con nombre entendible (recalcular nombre si el popup trajo el nombre del receptor)
        cli_slug = re.sub(r"[^A-Z0-9]+", "_", str(out.get("receptor_nombre") or out.get("receptor_doc") or "CLIENTE").upper()).strip("_")[:36] or "CLIENTE"
        pref_nom = "FACTURA" if out.get("tipo_doc") == "FACTURA" else "BOLETA"
        dir_dest = Path(ruta_pdf_limpia).parent
        dir_dest.mkdir(parents=True, exist_ok=True)
        ruta_final = str(dir_dest / f"{pref_nom}_{out['nro_cp']}_{cli_slug}.pdf")

        descargado = False
        try:
            btn_pdf = popup_page.locator("text='Descargar PDF', button:has-text('Descargar PDF'), a:has-text('Descargar PDF'), [widgetid*='pdf']").first
            if btn_pdf.count() > 0:
                with popup_page.expect_download(timeout=10000) as dl_info:
                    btn_pdf.click(force=True)
                dl_info.value.save_as(ruta_final)
                descargado = os.path.isfile(ruta_final)
        except Exception:
            pass

        if not descargado:
            try:
                popup_page.pdf(path=ruta_final, format="A4", print_background=True)
                descargado = os.path.isfile(ruta_final)
            except Exception:
                pass

        if descargado:
            out["archivo_pdf"] = ruta_final
    except Exception:
        pass
    finally:
        try:
            popup_page.close()
        except Exception:
            pass
    return out


def revisar_y_descargar_faltantes_sunat_incremental(dias_atras: int = 7) -> Dict[str, Any]:
    """
    Revisor Incremental Diario de Faltantes SUNAT (100% Headless, 0 ventanas CMD):
    - Bloqueado estrictamente para correr solo en `ES_NIVEL_2_MADRE_HOTEL` (Laptop 2 Recepción).
    - Entra a SUNAT SEE-SOL (`Consultar Factura y Nota` y `Consultar Boleta de Venta y Nota`)
      para los últimos `dias_atras` días usando regex `r"(E001|EB01)\\s*-\\s*(\\d+)"`.
    - Compara contra `sunat_ventas` en SQLite y contra `02_DB_sqlite_local/comprobantes_pdf/`.
    - Solo si falta algún comprobante (en BD o en PDF), descarga su PDF con nombre entendible único
      (`FACTURA_E001-XXXX_EMPRESA.pdf` / `BOLETA_EB01-XXXX_NOMBRE.pdf`), extrae sus datos a
      `sunat_ventas` + `sunat_lineas_cache` y los sube a Supabase.
    """
    if not ES_NIVEL_2_MADRE_HOTEL:
        return {
            "ok": False,
            "status": "SKIPPED_NO_MADRE",
            "nodo": ROL_NODO,
            "mensaje": f"Revisor incremental SUNAT solo se ejecuta en NIVEL_2_MADRE_HOTEL (nodo actual: {ROL_NODO}).",
        }

    from playwright.sync_api import sync_playwright
    from sync_nube import push_to_supabase

    _limpiar_pdfs_raw_duplicados_locales()

    hoy_dt = datetime.now()
    dias_eff = max(1, min(int(dias_atras or 7), 30))
    ini_dt = hoy_dt - timedelta(days=dias_eff)
    fecha_inicio = ini_dt.strftime("%d/%m/%Y")
    fecha_fin = hoy_dt.strftime("%d/%m/%Y")

    conn = get_connection()
    existentes_db: Dict[str, Dict[str, Any]] = {}
    try:
        for r in conn.execute("SELECT nro_cp, tipo_doc, estado, receptor_nombre, total FROM sunat_ventas").fetchall():
            cp_k = str(r["nro_cp"] or "").strip().upper()
            if cp_k:
                existentes_db[cp_k] = dict(r)
    finally:
        conn.close()

    ruc_sol, usr_sol, clv_sol = obtener_credenciales_sunat()
    descargados: List[str] = []
    actualizados_estado: List[str] = []
    revisados_total = 0
    errores: List[str] = []

    with adquirir_candado_sunat_exclusivo():
        with sync_playwright() as pw:
            browser = lanzar_edge_sunat(pw, oculto=True)
            context = crear_contexto_sunat(browser)
            page = context.new_page()
            try:
                url_login = (
                    "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
                    "?lang=es-PE&showDni=true&showLanguages=false"
                    f"&originalUrl={AUTENTICA_SOL_URL}&state={STATE_SOL_DEFAULT}"
                )
                iniciar_sesion_sol_blindado(page, url_login, ruc_sol, usr_sol, clv_sol)
                time.sleep(0.5)

                for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click()
                            time.sleep(0.25)
                    except Exception:
                        pass

                modulos = [
                    ("FACTURA", "#nivel3_11_5_3", "Consultar Factura y Nota", "FE Emitidas"),
                    ("BOLETA", "#nivel3_11_5_4", "Consultar Boleta de Venta y", "BVE"),
                ]

                for tipo_mod, sel_n3, texto_menu, texto_opt in modulos:
                    try:
                        try:
                            page.get_by_role("heading", name="Empresas").click(timeout=3000)
                            time.sleep(0.6)
                        except Exception:
                            pass

                        for sel_nav in ("#nivel1_11 > .glyphicon", "#nivel2_11_5 > .glyphicon", f"{sel_n3} > .glyphicon"):
                            try:
                                loc_n = page.locator(sel_nav).first
                                if loc_n.count() > 0 and loc_n.is_visible():
                                    loc_n.click(timeout=4000)
                                    time.sleep(0.7)
                            except Exception:
                                pass

                        try:
                            page.get_by_text(texto_menu).first.click(timeout=7000)
                        except Exception:
                            page.get_by_text(texto_menu).first.dispatch_event("click")

                        time.sleep(3.2)
                        frame = page.locator('iframe[name="iframeApplication"]').content_frame

                        # Llenar rango de fechas y consultar
                        llenado_ok = False
                        for _ in range(12):
                            time.sleep(0.8)
                            try:
                                tbs = frame.get_by_role("textbox").all()
                                if len(tbs) >= 2:
                                    tbs[0].click()
                                    tbs[0].fill(fecha_inicio)
                                    time.sleep(0.25)
                                    tbs[1].click()
                                    tbs[1].fill(fecha_fin)
                                    time.sleep(0.25)
                                    try:
                                        arrow = frame.locator(".dijitReset.dijitRight").first
                                        if arrow.count() > 0:
                                            arrow.click()
                                            time.sleep(0.4)
                                            opt = frame.get_by_role("option", name=re.compile(texto_opt, re.I))
                                            if opt.count() > 0:
                                                opt.first.click()
                                                time.sleep(0.3)
                                    except Exception:
                                        pass
                                    btn_ac = frame.get_by_role("button", name="Aceptar")
                                    if btn_ac.count() > 0:
                                        btn_ac.first.click()
                                        llenado_ok = True
                                        break
                            except Exception:
                                pass

                        if not llenado_ok:
                            continue

                        time.sleep(4.2)
                        try:
                            scrollbox = frame.locator(".dojoxGridScrollbox, div[dojoattachpoint='scrollbox']").first
                            if scrollbox.count() > 0:
                                for _ in range(10):
                                    scrollbox.evaluate("el => el.scrollTop += 550")
                                    time.sleep(0.2)
                                scrollbox.evaluate("el => el.scrollTop = 0")
                                time.sleep(0.6)
                        except Exception:
                            pass

                        filas = frame.locator(".dojoxGridRow, tr.dojoxGridRow, tr[dojoxgridrowindex]").all()
                        for fila in filas:
                            info_f = _extraer_fila_consulta_see_sol(fila, tipo_mod)
                            nro_cp = info_f.get("nro_cp") or ""
                            if not nro_cp:
                                continue
                            revisados_total += 1

                            en_db = existentes_db.get(nro_cp)
                            pdf_local = _buscar_pdf_solo_en_carpeta_local_v2(nro_cp)

                            # Si fue anulado en SUNAT y en DB seguía VIGENTE, actualizar estado
                            if info_f.get("estado") == "ANULADO (NCE)" and en_db and en_db.get("estado") != "ANULADO (NCE)":
                                ahora_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                with db_transaction() as c_an:
                                    c_an.execute(
                                        "UPDATE sunat_ventas SET estado = 'ANULADO (NCE)', updated_at = ?, actualizado_en = ? WHERE nro_cp = ? AND coalesce(tipo_doc, tipo, '') != 'NOTA_CREDITO'",
                                        (ahora_s, ahora_s, nro_cp),
                                    )
                                    c_an.execute(
                                        "UPDATE sunat_lineas_cache SET estado = 'ANULADO (NCE)' WHERE nro_cp = ? AND coalesce(tipo, '') != 'NOTA_CREDITO'",
                                        (nro_cp,),
                                    )
                                actualizados_estado.append(nro_cp)

                            if en_db:
                                continue

                            # Falta en sunat_ventas o falta su PDF en 02_DB_sqlite_local/comprobantes_pdf/
                            f_em = info_f.get("fecha_emision") or hoy_dt.strftime("%d/%m/%Y")
                            partes_dmy = f_em.split("/")
                            if len(partes_dmy) == 3 and partes_dmy[0].isdigit() and partes_dmy[1].isdigit():
                                carpeta_dia = f"{int(partes_dmy[0]):02d}_{MESES_CARPETA.get(int(partes_dmy[1]), 'Mes')}"
                            else:
                                carpeta_dia = f"{hoy_dt.day:02d}_{MESES_CARPETA.get(hoy_dt.month, 'Mes')}"

                            dir_base = FACTURAS_PDF_DIR if tipo_mod == "FACTURA" else BOLETAS_PDF_DIR
                            dir_dia = dir_base / carpeta_dia
                            dir_dia.mkdir(parents=True, exist_ok=True)

                            cli_slug = re.sub(r"[^A-Z0-9]+", "_", str(info_f.get("receptor_nombre") or info_f.get("receptor_doc") or "CLIENTE").upper()).strip("_")[:36] or "CLIENTE"
                            pref_nom = "FACTURA" if tipo_mod == "FACTURA" else "BOLETA"
                            ruta_pdf_tentativa = str(dir_dia / f"{pref_nom}_{nro_cp}_{cli_slug}.pdf")

                            detalle_cp = dict(info_f)
                            link_cp = fila.locator("a:has(img), a[href*='verimprimir'], a[href*='verImprimir'], a[href*='consultar.do'], a").first
                            if link_cp.count() > 0:
                                try:
                                    link_cp.scroll_into_view_if_needed(timeout=2000)
                                    with page.expect_popup(timeout=9000) as pop_info:
                                        link_cp.click(force=True)
                                    detalle_cp = _procesar_y_descargar_popup_consulta_sunat(
                                        pop_info.value,
                                        info_f,
                                        ruta_pdf_tentativa,
                                    )
                                except Exception as exc_pop:
                                    errores.append(f"{nro_cp}: {exc_pop}")

                            res_em_sim = {
                                "status": "OK",
                                "tipo": tipo_mod,
                                "nro_comprobante": nro_cp,
                                "archivo_pdf": detalle_cp.get("archivo_pdf") or pdf_local or "",
                                "receptor_nombre": detalle_cp.get("receptor_nombre") or info_f.get("receptor_nombre") or "CLIENTES VARIOS",
                                "receptor_doc": detalle_cp.get("receptor_doc") or info_f.get("receptor_doc") or "",
                                "fecha_emision": f_em,
                            }
                            data_norm_sim = {
                                "accion": "EMITIR",
                                "tipo": tipo_mod,
                                "documento": res_em_sim["receptor_doc"],
                                "cliente": res_em_sim["receptor_nombre"],
                                "items": detalle_cp.get("items") or [
                                    {
                                        "cantidad": 1,
                                        "descripcion": f"ALOJAMIENTO SEGUN {nro_cp}",
                                        "precio_unitario": float(detalle_cp.get("monto_total") or info_f.get("monto_total") or 0.0),
                                    }
                                ],
                                "total_monto": float(detalle_cp.get("monto_total") or info_f.get("monto_total") or 0.0),
                            }
                            convertir_comprobante_a_datos_y_subir_supabase(res_em_sim, data_norm_sim)
                            descargados.append(nro_cp)
                            existentes_db[nro_cp] = {"nro_cp": nro_cp, "tipo_doc": tipo_mod, "estado": info_f.get("estado", "VIGENTE")}

                        try:
                            page.once("dialog", lambda d: d.dismiss())
                            frame.get_by_role("button", name="Cerrar").click(timeout=2500)
                            time.sleep(1.0)
                        except Exception:
                            pass
                    except Exception as exc_mod:
                        errores.append(f"{tipo_mod}: {exc_mod}")
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

    ahora_iso = datetime.now().isoformat(timespec="seconds")
    hoy_ymd = hoy_dt.strftime("%Y-%m-%d")
    resumen = {
        "ok": True,
        "status": "OK",
        "nodo": ROL_NODO,
        "rango_fechas": f"{fecha_inicio} al {fecha_fin}",
        "comprobantes_revisados": revisados_total,
        "faltantes_descargados": descargados,
        "anulaciones_sincronizadas": actualizados_estado,
        "errores": errores[:10],
        "ejecutado_en": ahora_iso,
    }
    try:
        with db_transaction() as conn_st:
            conn_st.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES ('sunat_revisor_incremental_ult_fecha', ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (hoy_ymd, ahora_iso),
            )
            conn_st.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES ('sunat_revisor_incremental_ult_resumen', ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (json.dumps(resumen, ensure_ascii=False), ahora_iso),
            )
        push_to_supabase(limit=40)
    except Exception:
        pass

    return resumen

