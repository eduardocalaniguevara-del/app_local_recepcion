# -*- coding: utf-8 -*-
"""
main.py — Punto de Entrada Único de C:\\Ovalo_Sistema_v2 (Puerto :8080)
Sirve la Interfaz Operativa Exacta desde 05_GIT_web_mostrador/PanelDisponibilidad.html
conectada a 03_DB_sqlite_local/ovalo.db y 04_GIT_app_motor/estancias.py.
"""
from contextlib import asynccontextmanager
from pathlib import Path
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
MOTOR_DIR = BASE_DIR / "03_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

import config as _cfg_v2
import db as _db_v2
sys.modules["config"] = _cfg_v2
sys.modules["database"] = _db_v2

import estancias as dl
import yape_bcp as ym
import sunat_motor_playwright as smp
import sunat as sws
import whatsapp as wi
sys.modules["domain_logic"] = dl
sys.modules["yape_manager"] = ym
sys.modules["sunat_motor_playwright"] = smp
sys.modules["sunat_wsp_service"] = sws
sys.modules["wsp_inteligencia"] = wi

from config import APP_HOST, APP_PORT, DATA_DIR, MODO_PRUEBA, NOMBRE_HOTEL, ROL_NODO, STATIC_DIR, WEB_DIR
from db import get_connection, init_db
from salud import publicar_seguidor_carpetas_supabase, reporte_salud
from sync_nube import (
    instalar_triggers_outbox,
    pull_from_supabase,
    push_to_supabase,
    sincronizar_espejo_sheets,
)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

__ovalo_v2_rpc__ = True
_sync_wakeup_event = threading.Event()
_sheets_wakeup_event = threading.Event()
_sync_stop_event = threading.Event()


async def _leer_json_opcional(request: Request) -> Dict[str, Any]:
    """Lee un body JSON opcional sin lanzar 422 cuando el cliente envía POST vacío."""
    try:
        raw = await request.body()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _git_head_actual() -> str:
    try:
        import subprocess as _sp
        flags = getattr(_sp, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return _sp.run(
            ["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=4, creationflags=flags
        ).stdout.strip()
    except Exception:
        return ""


# Commit con el que ESTE proceso cargó su código. Si el disco avanza (pull propio o del motor_24x7),
# el proceso se reinicia completo: importlib.reload no basta y fallaba en silencio (incidente 24-26/11:
# la Madre siguió emitiendo con sunat_motor_playwright viejo 2 días y ningún WhatsApp salió).
_HEAD_AL_ARRANCAR = _git_head_actual()
_reinicio_por_codigo_lanzado = False


def _reiniciar_si_codigo_cambio_desde_arranque() -> bool:
    global _reinicio_por_codigo_lanzado
    if _reinicio_por_codigo_lanzado or not _HEAD_AL_ARRANCAR:
        return False
    actual = _git_head_actual()
    if not actual or actual == _HEAD_AL_ARRANCAR:
        return False
    import subprocess as _sp
    flags = getattr(_sp, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        cambiados = _sp.run(
            ["git", "diff", "--name-only", _HEAD_AL_ARRANCAR, actual],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=4, creationflags=flags,
        ).stdout or ""
    except Exception:
        cambiados = ""
    if not any(ln.strip().endswith(".py") for ln in cambiados.splitlines()):
        return False
    _reinicio_por_codigo_lanzado = True
    print(f"[auto-update] Código cambió {_HEAD_AL_ARRANCAR[:7]} -> {actual[:7]}; reiniciando servidor completo.", flush=True)
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    _sp.Popen(
        [exe, str(BASE_DIR / "lanzador_escritorio.py"), "--no-browser", "--restart"],
        cwd=str(BASE_DIR),
        creationflags=flags,
    )
    return True


def _auto_git_pull_y_recargar() -> bool:
    """
    En Laptop 2 (Recepción) y Laptop 3 (Terciaria): descarga automáticamente en segundo plano
    cualquier mejora publicada con `git push` desde Laptop 1 y, si cambió algún .py desde que este
    proceso arrancó, reinicia el servidor completo para que el código nuevo quede realmente cargado.
    """
    if ROL_NODO == "NIVEL_1_INGENIERIA" or not (BASE_DIR / ".git").exists():
        return False
    try:
        if hasattr(sws, "hay_trabajo_sunat_en_curso") and sws.hay_trabajo_sunat_en_curso():
            return False
    except Exception:
        pass
    import importlib
    import subprocess

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=4, creationflags=flags
        ).stdout.strip()
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=flags,
        )
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=4, creationflags=flags
        ).stdout.strip()
        # Compara contra el código CARGADO al arrancar (no solo contra este pull: el motor_24x7 puede haber hecho el pull antes)
        if _reiniciar_si_codigo_cambio_desde_arranque():
            return True
        if before and after and before != after:
            for mod in (_cfg_v2, _db_v2, dl, ym, smp, sws, wi):
                try:
                    importlib.reload(mod)
                except Exception:
                    pass
            try:
                diff_files = subprocess.run(
                    ["git", "diff", "--name-only", before, after],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=4,
                    creationflags=flags,
                ).stdout
                if "main.py" in (diff_files or ""):
                    pyw = Path(sys.executable).with_name("pythonw.exe")
                    exe = str(pyw if pyw.exists() else sys.executable)
                    subprocess.Popen(
                        [exe, str(BASE_DIR / "lanzador_escritorio.py"), "--no-browser", "--restart"],
                        cwd=str(BASE_DIR),
                        creationflags=flags,
                    )
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _autolimpiar_y_conciliar_yapes() -> None:
    try:
        conn = get_connection()
        try:
            if hasattr(ym, "_deduplicar_yapes_celular_vs_drive"):
                ym._deduplicar_yapes_celular_vs_drive(conn)
            ym.comprobar_yapes_con_anotaciones_chicas(conn=conn)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _background_supabase_worker() -> None:
    try:
        _auto_git_pull_y_recargar()
        instalar_triggers_outbox()
        publicar_seguidor_carpetas_supabase()
        push_to_supabase()
        pull_from_supabase()
        _autolimpiar_y_conciliar_yapes()
        sincronizar_espejo_sheets()
    except Exception as exc:
        print(f"[OvaloV2-Sync] Inicio: {exc}")
    ciclo_n = 0
    while not _sync_stop_event.is_set():
        triggered = _sync_wakeup_event.wait(timeout=25)
        if _sync_stop_event.is_set():
            break
        ciclo_n += 1
        if triggered:
            _sync_wakeup_event.clear()
            time.sleep(0.25)
        try:
            if ciclo_n % 6 == 0:  # Cada ~2.5 min busca mejoras de código en GitHub automáticamente
                if _auto_git_pull_y_recargar():
                    _autolimpiar_y_conciliar_yapes()
            publicar_seguidor_carpetas_supabase()
            push_to_supabase()
            pull_from_supabase()
            try:
                sws.procesar_cola_ordenes_sunat_madre(wakeup_cb=lambda: _sheets_wakeup_event.set())
            except Exception as exc_sunat_q:
                print(f"[OvaloV2-SUNAT-ColaMadre] {exc_sunat_q}")
            if _sheets_wakeup_event.is_set() or (ciclo_n % 2 == 0):
                _sheets_wakeup_event.clear()
                sincronizar_espejo_sheets()
                push_to_supabase()
        except Exception as exc:
            print(f"[OvaloV2-Sync] Ciclo: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    instalar_triggers_outbox()
    try:
        import yape_manager as ym
        ym.init_yape_tables()
    except Exception:
        pass
    try:
        import tablet_manager as tm
        tm.init_tablet_tables()
    except Exception:
        pass
    t = threading.Thread(target=_background_supabase_worker, name="OvaloV2SupabaseSync", daemon=True)
    t.start()
    yield
    _sync_stop_event.set()
    _sync_wakeup_event.set()


app = FastAPI(
    title="Hostal Óvalo Mariátegui — Sistema v2 (Arquitectura 3 Niveles)",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class RpcPayload(BaseModel):
    args: List[Any] = []


BRIDGE_BODY_LOCAL = """
<script>
  (function() {
    var hookBanner = function() {
      if (typeof window.mostrarBanner === 'function' && !window.mostrarBanner._hooked) {
        var origBanner = window.mostrarBanner;
        window.mostrarBanner = function(tipo, msg) {
          origBanner(tipo, msg);
          if (tipo === 'success' && window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'REFRESH_PANEL' }, '*');
          }
        };
        window.mostrarBanner._hooked = true;
      }
    };
    hookBanner();
    setTimeout(hookBanner, 500);
    window.addEventListener('message', function(e) {
      if (e.data && e.data.type === 'SET_CONTEXT' && typeof window.aplicarContextoDesdePanel === 'function') {
        window.aplicarContextoDesdePanel(e.data.context);
      }
    });
  })();
</script>"""


def _obtener_vista_formulario_local(nombre_vista: str) -> str:
    vistas_permitidas = {
        "CheckInRapido": "CheckInRapido.html",
        "PagosPosteriores": "PagosPosteriores.html",
        "VentaTienda": "VentaTienda.html",
        "GastosRegistro": "GastosRegistro.html",
    }
    fname = vistas_permitidas.get(str(nombre_vista or "").strip())
    if not fname:
        raise ValueError(f"Vista no reconocida: {nombre_vista}")
    fpath = WEB_DIR / fname
    if not fpath.exists():
        raise FileNotFoundError(f"Archivo de vista no encontrado en 05_GIT_web_mostrador: {fname}")
    html = fpath.read_text(encoding="utf-8").replace("{{NOMBRE_HOTEL}}", NOMBRE_HOTEL)
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(r"</body>", lambda _m: BRIDGE_BODY_LOCAL + "\n</body>", html, count=1, flags=re.IGNORECASE)
    else:
        html += BRIDGE_BODY_LOCAL
    return html


@app.get("/", response_class=HTMLResponse)
def index():
    panel_path = WEB_DIR / "PanelDisponibilidad.html"
    js_sunat_path = WEB_DIR / "js" / "facturacion_sunat.js"
    js_yape_path = WEB_DIR / "js" / "yape_vivo.js"
    html = panel_path.read_text(encoding="utf-8")
    mtime_ui = int(
        max(
            panel_path.stat().st_mtime,
            js_sunat_path.stat().st_mtime if js_sunat_path.exists() else 0,
            js_yape_path.stat().st_mtime if js_yape_path.exists() else 0,
        )
    )
    html = html.replace("{{NOMBRE_HOTEL}}", NOMBRE_HOTEL)
    html = re.sub(r'/static/js/api_bridge\.js(?:\?[^"]*)?"', f'/static/js/api_bridge.js?v={mtime_ui}"', html)
    html = re.sub(r'/static/js/facturacion_sunat\.js(?:\?[^"]*)?"', f'/static/js/facturacion_sunat.js?v={mtime_ui}"', html)
    if "yape_vivo.js" in html:
        html = re.sub(r'/static/js/yape_vivo\.js(?:\?[^"]*)?"', f'/static/js/yape_vivo.js?v={mtime_ui}"', html)
    else:
        html = html.replace("</head>", f'  <script src="/static/js/yape_vivo.js?v={mtime_ui}"></script>\n</head>', 1)
    return HTMLResponse(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/salud")
def api_salud():
    return JSONResponse(content=reporte_salud())


@app.get("/api/health")
def api_health():
    return JSONResponse(content=reporte_salud())


@app.get("/api/supabase/status")
def api_supabase_status():
    _sync_wakeup_event.set()
    return JSONResponse(
        content={"ok": True, "status": "OK", "syncStatus": dl.obtener_estado_red_cola()},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/api/system/restart")
def api_system_restart():
    updated = _auto_git_pull_y_recargar()
    _sync_wakeup_event.set()
    _sheets_wakeup_event.set()
    return JSONResponse(
        content={"ok": True, "status": "UPDATED" if updated else "OK", "updated": bool(updated), "syncStatus": dl.obtener_estado_red_cola()}
    )


@app.get("/api/cuentas/pdf")
def api_cuentas_pdf(hab: str = "", modo: str = "ingreso", fecha: str = "", estanciaUuid: str = ""):
    try:
        import pases_logic as pl
        pdf_path = pl.generar_pdf_cuenta_hab_local(
            {"hab": hab, "modo": modo, "fecha": fecha, "estanciaUuid": estanciaUuid}
        )
        p_obj = Path(pdf_path)
        if not p_obj.is_file():
            return JSONResponse(status_code=404, content={"ok": False, "error": "PDF de cuenta no generado."})
        return FileResponse(
            str(p_obj),
            media_type="application/pdf",
            filename=p_obj.name,
            headers={"Cache-Control": "no-store"},
        )
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/sync/status")
def api_sync_status():
    st = dl.obtener_estado_red_cola()
    try:
        mtime = int((WEB_DIR / "PanelDisponibilidad.html").stat().st_mtime)
        st["uiVersion"] = f"v2.0-{ROL_NODO}-{mtime}"
    except Exception:
        st["uiVersion"] = f"v2.0-{ROL_NODO}"
    return JSONResponse(content=st, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.post("/api/sync/trigger")
def api_sync_trigger():
    if _auto_git_pull_y_recargar():
        _autolimpiar_y_conciliar_yapes()
    _sheets_wakeup_event.set()
    _sync_wakeup_event.set()
    return {"status": "TRIGGERED", "syncStatus": dl.obtener_estado_red_cola()}


@app.get("/api/yape/recientes")
def api_yape_recientes(fecha: str = "", limite: int = 80, forzar_drive: int = 0):
    try:
        if forzar_drive:
            _auto_git_pull_y_recargar()
            pull_from_supabase()
            _autolimpiar_y_conciliar_yapes()
        import yape_manager as ym
        res = ym.obtener_resumen_yapes_sqlite(fecha_dmy=fecha, limite=limite, forzar_drive=bool(forzar_drive))
        return JSONResponse(content=res, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    except Exception as exc:
        return JSONResponse(content={"ok": False, "items": [], "error": str(exc)})


@app.post("/api/yape/accion")
def api_yape_accion(payload: Dict[str, Any]):
    import yape_manager as ym
    res = ym.gestionar_accion_yape_sqlite(payload)
    if res.get("ok"):
        _sync_wakeup_event.set()
    return JSONResponse(content=res)


@app.post("/api/yape/sincronizar-maelen")
async def api_yape_sincronizar_maelen(request: Request):
    body = await _leer_json_opcional(request)
    try:
        import yape_manager as ym
        res = ym.sincronizar_transferencias_maelen_correo(forzar=bool(body.get("forzar", True)))
        if res.get("ok") or res.get("insertados_maelen") or res.get("insertados_yape_vivo"):
            _sync_wakeup_event.set()
        return JSONResponse(content=res, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/yape/verificar-alertas-5min")
async def api_yape_verificar_alertas_5min(request: Request):
    body = await _leer_json_opcional(request)
    try:
        import yape_manager as ym
        res = ym.verificar_y_alertar_yapes_sin_anotar_5min(
            ventana_horas=int(body.get("ventana_horas") or 24),
            enviar_wsp_real=bool(body.get("enviar_wsp_real", False)),
            incluir_reciente_supervision=bool(body.get("incluir_reciente_supervision", False)),
        )
        if res.get("ok") or res.get("alertas_creadas"):
            _sync_wakeup_event.set()
        return JSONResponse(content=res, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/yape/revisor-830am")
async def api_yape_revisor_830am(request: Request):
    body = await _leer_json_opcional(request)
    try:
        import yape_manager as ym
        res = ym.ejecutar_revisor_criterio_yape_830am(
            fecha_filtro=body.get("fecha_filtro"),
            forzar_extraccion=bool(body.get("forzar_extraccion", False)),
            enviar_wsp_real=bool(body.get("enviar_wsp_real", False)),
        )
        if res.get("ok"):
            _sync_wakeup_event.set()
        return JSONResponse(content=res, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/tablet", response_class=HTMLResponse)
def api_tablet_page():
    fpath = WEB_DIR / "TabletKiosco.html"
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="TabletKiosco.html no encontrado.")
    html = fpath.read_text(encoding="utf-8").replace("{{NOMBRE_HOTEL}}", NOMBRE_HOTEL)
    return HTMLResponse(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/tablet/manifest.json")
def api_tablet_manifest():
    manifest = {
        "name": f"{NOMBRE_HOTEL} — Lobby Digital",
        "short_name": "Óvalo Lobby",
        "start_url": "/tablet",
        "scope": "/",
        "display": "standalone",
        "orientation": "landscape",
        "background_color": "#16110E",
        "theme_color": "#16110E",
        "icons": [
            {"src": "/api/tablet/foto/logo_app_192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/api/tablet/foto/logo_app_512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return JSONResponse(content=manifest, media_type="application/manifest+json")


@app.get("/api/tablet/sw.js")
def api_tablet_sw():
    sw_code = (
        "self.addEventListener('install', function(e) { self.skipWaiting(); });\n"
        "self.addEventListener('activate', function(e) { e.waitUntil(self.clients.claim()); });\n"
        "self.addEventListener('fetch', function(e) { /* pass-through local */ });\n"
    )
    return Response(
        content=sw_code,
        media_type="application/javascript; charset=utf-8",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/tablet/estado")
def api_tablet_estado(request: Request, from_tablet: int = 0):
    import tablet_manager as tm
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    data = tm.obtener_datos_tablet_publico(client_ip=ip, user_agent=ua)
    return JSONResponse(content=data, headers={"Cache-Control": "no-store"})


@app.get("/api/tablet/control")
def api_tablet_control_get():
    import tablet_manager as tm
    return JSONResponse(content=tm.obtener_panel_control_tablet(), headers={"Cache-Control": "no-store"})


@app.post("/api/tablet/control")
async def api_tablet_control_post(request: Request):
    import tablet_manager as tm
    body = await _leer_json_opcional(request)
    return JSONResponse(content=tm.actualizar_control_remoto_tablet(body))


@app.post("/api/tablet/seleccionar-hab")
def api_tablet_seleccionar_hab(payload: Dict[str, Any]):
    import tablet_manager as tm
    try:
        return JSONResponse(content=tm.seleccionar_habitacion_desde_tablet(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tablet/consultar-doc")
def api_tablet_consultar_doc(payload: Dict[str, Any]):
    import tablet_manager as tm
    return JSONResponse(content=tm.consultar_documento_desde_tablet(payload))


@app.post("/api/tablet/checkin-datos")
def api_tablet_checkin_datos(payload: Dict[str, Any]):
    import tablet_manager as tm
    try:
        return JSONResponse(content=tm.enviar_datos_checkin_desde_tablet(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tablet/pedido-tienda")
def api_tablet_pedido_tienda(payload: Dict[str, Any]):
    import tablet_manager as tm
    try:
        return JSONResponse(content=tm.enviar_pedido_vitrina_desde_tablet(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tablet/limpieza")
def api_tablet_limpieza(payload: Dict[str, Any]):
    import tablet_manager as tm
    try:
        res = tm.cambiar_estado_limpieza_desde_tablet(payload)
        _sync_wakeup_event.set()
        return JSONResponse(content=res)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tablet/firma")
def api_tablet_firma(payload: Dict[str, Any]):
    import tablet_manager as tm
    try:
        res = tm.guardar_firma_conformidad_tablet(payload)
        _sync_wakeup_event.set()
        return JSONResponse(content=res)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tablet/sugerencia")
def api_tablet_sugerencia(payload: Dict[str, Any]):
    import tablet_manager as tm
    res = tm.guardar_sugerencia_tablet(payload)
    _sync_wakeup_event.set()
    return JSONResponse(content=res)


@app.get("/api/tablet/foto/{filename}")
def api_tablet_foto(filename: str, w: str = ""):
    import tablet_manager as tm
    safe = Path(str(filename or "")).name
    if not safe:
        raise HTTPException(status_code=404, detail="Archivo no especificado.")
    ruta = tm.obtener_ruta_foto_tablet(safe, w=w)
    if ruta and ruta.is_file():
        return FileResponse(str(ruta), headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail=f"Foto no encontrada: {safe}")


@app.get("/api/tablet/producto-foto/{filename}")
def api_tablet_producto_foto(filename: str, w: str = ""):
    import tablet_manager as tm
    safe = Path(str(filename or "")).name
    if not safe:
        raise HTTPException(status_code=404, detail="Archivo no especificado.")
    fotos_prods_dir = getattr(tm, "FOTOS_PRODS_DIR", DATA_DIR / "fotos_productos")
    ruta = tm.obtener_ruta_foto_tablet(safe, w=w, carpeta=fotos_prods_dir)
    if ruta and ruta.is_file():
        return FileResponse(str(ruta), headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail=f"Foto de producto no encontrada: {safe}")


@app.get("/api/sunat/jobs")
def api_sunat_jobs():
    try:
        from sunat_wsp_service import listar_jobs_sunat_recientes
        return JSONResponse(content={"jobs": listar_jobs_sunat_recientes()})
    except Exception:
        return JSONResponse(content={"jobs": []})


@app.post("/api/sunat/orden")
def api_sunat_orden(payload: Dict[str, Any]):
    from sunat_wsp_service import ejecutar_orden_sunat_wsp
    res = ejecutar_orden_sunat_wsp(
        payload,
        wakeup_cb=lambda: (_sheets_wakeup_event.set(), _sync_wakeup_event.set()),
    )
    res["syncStatus"] = dl.obtener_estado_red_cola()
    return JSONResponse(content=res)


@app.get("/api/sunat/captura/{nombre_captura}")
def api_sunat_captura(nombre_captura: str):
    from config import CAPTURAS_SUNAT_DIR, HOSTAL_SISTEMA_DIR
    safe_name = Path(str(nombre_captura or "")).name
    if not safe_name:
        raise HTTPException(status_code=404, detail="Captura no especificada.")
    for cand in (
        CAPTURAS_SUNAT_DIR / safe_name,
        HOSTAL_SISTEMA_DIR / "capturas" / safe_name,
    ):
        if cand.is_file():
            return FileResponse(str(cand), media_type="image/png", headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail=f"Captura no encontrada: {safe_name}")


@app.get("/api/sunat/pdf/{nro_cp}")
def api_sunat_pdf(nro_cp: str):
    from sunat_wsp_service import buscar_pdf_comprobante
    ruta = buscar_pdf_comprobante(nro_cp=str(nro_cp or "").strip())
    if ruta and os.path.isfile(ruta) and (not hasattr(smp, "_es_ruta_pdf_autorizada") or smp._es_ruta_pdf_autorizada(ruta)):
        return FileResponse(ruta, media_type="application/pdf", filename=os.path.basename(ruta))
    raise HTTPException(status_code=404, detail=f"PDF local no encontrado para {nro_cp} (los PDFs residen en la Laptop Madre).")


@app.get("/api/sunat/pendientes")
def api_sunat_pendientes(desde: int = 20261101, hasta: int = 20261231):
    from sunat_wsp_service import listar_pendientes_facturacion_para_aprobacion
    return JSONResponse(content=listar_pendientes_facturacion_para_aprobacion(desde, hasta))


@app.post("/api/rpc/{method_name}")
def api_rpc_handler(method_name: str, body: RpcPayload):
    args = body.args or []
    try:
        if method_name in ("obtenerDatosPanelDisponibilidad", "cambiarFechaPanelDisponibilidad"):
            fecha_iso = args[0] if len(args) > 0 and args[0] else None
            res = dl.obtener_datos_panel_local(fecha_iso)
        elif method_name == "obtenerVistaFormulario":
            res = _obtener_vista_formulario_local(args[0] if args else "")
        elif method_name == "obtenerDatosInicialesCheckIn":
            res = dl.obtener_datos_iniciales_checkin_local()
        elif method_name in ("consultarDniReniec", "consultarDocumentoPadron"):
            from sunat_wsp_service import consultar_dni_local_o_reniec
            res = consultar_dni_local_o_reniec(args[0] if args else "")
        elif method_name in ("consultarRucSunat", "consultarRucPadron"):
            from sunat_wsp_service import consultar_ruc_local_o_sunat
            res = consultar_ruc_local_o_sunat(args[0] if args else "")
        elif method_name in ("guardarCheckInDesdeApp", "procesarCheckInApp"):
            res = dl.guardar_checkin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name in ("obtenerDatosPagosPosteriores", "obtenerEstanciasConDeuda"):
            res = dl.obtener_datos_pagos_posteriores_local()
        elif method_name in ("guardarPagoPosteriorDesdeApp", "procesarPagoPosteriorApp"):
            res = dl.guardar_pago_posterior_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerDatosTienda":
            res = dl.obtener_datos_tienda_local()
        elif method_name in ("guardarVentaTiendaDesdeApp", "procesarVentaTiendaApp"):
            res = dl.guardar_venta_tienda_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerDatosGastos":
            res = dl.obtener_datos_gastos_local()
        elif method_name in ("guardarGastoDesdeApp", "procesarGastoApp"):
            res = dl.guardar_gasto_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name in ("continuarEstanciaDesdeApp", "continuarEstanciaRapida"):
            res = dl.continuar_estancia_local(args[0] if args else "")
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "liberarHabitacionDesdeApp":
            res = dl.liberar_habitacion_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "cancelarEstanciaDesdeApp":
            res = dl.cancelar_estancia_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "restaurarEstanciaDesdeApp":
            res = dl.restaurar_estancia_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciaParaModificar":
            hab = args[0] if len(args) > 0 else ""
            fila = args[1] if len(args) > 1 else None
            res = dl.obtener_estancia_para_modificar_local(hab, fila)
        elif method_name in ("modificarEstanciaDesdeApp", "guardarModificacionEstancia"):
            res = dl.modificar_estancia_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "actualizarEstadoComprobanteDesdePanel":
            res = dl.actualizar_comprobante_desde_panel_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroPagos":
            res = dl.obtener_registro_pagos_local(args[0] if args else {})
        elif method_name == "obtenerMovimientos":
            res = dl.obtener_movimientos_local(args[0] if args else {})
        elif method_name == "guardarColorFondoHabitacion":
            hab = args[0] if len(args) > 0 else ""
            color = args[1] if len(args) > 1 else ""
            fecha_ref = args[2] if len(args) > 2 else None
            nota = args[3] if len(args) > 3 else ""
            res = dl.guardar_color_habitacion_local(hab, color, fecha_ref, nota)
            _sync_wakeup_event.set()
        elif method_name == "obtenerDatosVitrina":
            res = dl.obtener_datos_vitrina_local()
        elif method_name == "guardarConteoVitrina":
            res = dl.guardar_conteo_vitrina_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "verificarClaveGestionTienda":
            res = dl.verificar_clave_gestion_tienda_local(args[0] if args else {})
        elif method_name == "registrarCompraTienda":
            res = dl.registrar_compra_tienda_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "cerrarCompraPendienteTienda":
            res = dl.cerrar_compra_pendiente_tienda_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "registrarMovimientoTienda":
            res = dl.registrar_movimiento_tienda_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "anularMovimientoAlmacen":
            res = dl.anular_movimiento_almacen_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroEstanciasBD":
            res = dl.obtener_registro_estancias_bd_local(args[0] if args else {})
        elif method_name == "editarEstanciaBDAdmin":
            res = dl.editar_estancia_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "eliminarEstanciaBDAdmin":
            res = dl.eliminar_estancia_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroGastosBD":
            res = dl.obtener_registro_gastos_bd_local(args[0] if args else {})
        elif method_name == "editarGastoBDAdmin":
            res = dl.editar_gasto_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "eliminarGastoBDAdmin":
            res = dl.eliminar_gasto_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroVentasTiendaBD":
            res = dl.obtener_registro_ventas_tienda_bd_local(args[0] if args else {})
        elif method_name == "editarVentaTiendaBDAdmin":
            res = dl.editar_venta_tienda_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "eliminarVentaTiendaBDAdmin":
            res = dl.eliminar_venta_tienda_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroPagosBD":
            res = dl.obtener_registro_pagos_bd_local(args[0] if args else {})
        elif method_name == "editarPagoBDAdmin":
            res = dl.editar_pago_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "eliminarPagoBDAdmin":
            res = dl.eliminar_pago_bd_admin_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "guardarCheckInGrupalEmpresa":
            res = dl.guardar_checkin_grupal_empresa_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciasPagoMasivo":
            res = dl.obtener_estancias_pago_masivo_local(args[0] if args else {})
        elif method_name == "guardarPagoMasivo":
            res = dl.guardar_pago_masivo_local(args[0] if args else {})
            _sheets_wakeup_event.set()
            _sync_wakeup_event.set()
        elif method_name == "sincronizarCopiaSheets":
            res = sincronizar_espejo_sheets(forzar=True)
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciaFacturacionPorUuid":
            a0 = args[0] if args else ""
            u_id = a0.get("uuid", "") if isinstance(a0, dict) else str(a0 or "")
            res = dl.obtener_estancia_facturacion_por_uuid(u_id)
        elif method_name == "listarPendientesFacturacionAprobacion":
            from sunat_wsp_service import listar_pendientes_facturacion_para_aprobacion
            a0 = args[0] if args and isinstance(args[0], dict) else {}
            d_ini = int(a0.get("desde", 20261115))
            d_fin = int(a0.get("hasta", 20261231))
            res = listar_pendientes_facturacion_para_aprobacion(d_ini, d_fin)
        elif method_name == "ejecutarEstanciaPendientePorUuid":
            from sunat_wsp_service import ejecutar_estancia_pendiente_por_uuid
            a0 = args[0] if args and isinstance(args[0], dict) else {}
            res = ejecutar_estancia_pendiente_por_uuid(
                estancia_uuid=str(a0.get("estancia_uuid") or a0.get("uuid") or ""),
                modo_borrador=bool(a0.get("modo_borrador", True)),
                enviar_wsp=bool(a0.get("enviar_wsp", True)),
                forzar_local=bool(a0.get("forzar_local", False)),
                sincrono=bool(a0.get("sincrono", False)),
            )
        elif method_name == "obtenerHorario":
            import horario_logic as hl
            res = hl.obtener_horario_local(args[0] if args else {})
        elif method_name == "marcarAsistenciaHorario":
            import horario_logic as hl
            res = hl.marcar_asistencia_local(args[0] if args else {})
        elif method_name == "editarCeldaHorario":
            import horario_logic as hl
            res = hl.editar_celda_horario_local(args[0] if args else {})
        elif method_name == "verificarClaveCuentasHorario":
            import horario_logic as hl
            res = hl.verificar_clave_cuentas_horario_local(args[0] if args else {})
        elif method_name == "obtenerLiquidacionHorario":
            import horario_logic as hl
            res = hl.obtener_liquidacion_horario_local(args[0] if args else {})
        elif method_name == "obtenerAuditoriaHorario":
            import horario_logic as hl
            res = hl.obtener_auditoria_horario_local(args[0] if args else {})
        elif method_name == "obtenerPersonalConfig":
            import horario_logic as hl
            res = hl.obtener_personal_config_local(args[0] if args else {})
        elif method_name == "guardarPersonalConfig":
            import horario_logic as hl
            res = hl.guardar_personal_config_local(args[0] if args else {})
        elif method_name == "guardarLiquidacionHorario":
            import horario_logic as hl
            res = hl.guardar_liquidacion_horario_local(args[0] if args else {})
        elif method_name == "obtenerPanelControlTablet":
            import tablet_manager as tm
            res = tm.obtener_panel_control_tablet()
        elif method_name == "actualizarControlRemotoTablet":
            import tablet_manager as tm
            res = tm.actualizar_control_remoto_tablet(args[0] if args else {})
        elif method_name == "obtenerVisitasPases":
            import pases_logic as pl
            res = pl.obtener_visitas_pases_local(args[0] if args else {})
        elif method_name == "registrarVisitaPase":
            import pases_logic as pl
            res = pl.registrar_visita_pase_local(args[0] if args else {})
        elif method_name == "cambiarEstadoVisitaPase":
            import pases_logic as pl
            res = pl.cambiar_estado_visita_pase_local(args[0] if args else {})
        elif method_name == "eliminarVisitaPase":
            import pases_logic as pl
            res = pl.eliminar_visita_pase_local(args[0] if args else {})
        elif method_name == "consultarDniVisita":
            import pases_logic as pl
            res = pl.consultar_dni_visita_local(args[0] if args else {})
        elif method_name == "obtenerCuentaDetalladaHab":
            import pases_logic as pl
            res = pl.obtener_cuenta_detallada_hab_local(args[0] if args else {})
        elif method_name == "enviarCuentaHabWsp":
            import pases_logic as pl
            res = pl.enviar_wsp_cuenta_hab_local(args[0] if args else {})
        elif method_name == "obtenerDatosContabilidad":
            import contabilidad_logic as cl
            a0 = args[0] if args else {}
            mes = a0.get("mes", "2026-11") if isinstance(a0, dict) else str(a0 or "2026-11")
            res = cl.obtener_datos_contabilidad_local(mes)
        elif method_name == "recalcularAuditoriaContabilidad":
            import contabilidad_logic as cl
            res = cl.recalcular_auditoria_contabilidad_local()
        elif method_name == "actualizarExtraccionTributariaCompleta":
            import contabilidad_logic as cl
            a0 = args[0] if args else {}
            mes = a0.get("mes", "2026-11") if isinstance(a0, dict) else str(a0 or "2026-11")
            res = cl.actualizar_extraccion_tributaria_completa_local(mes)
        elif method_name == "sincronizarComprobantesFaltantesBD":
            import contabilidad_logic as cl
            res = cl.sincronizar_comprobantes_faltantes_bd_local()
        elif method_name == "obtenerSugerenciaUnificacion":
            import motor_unificacion as mu
            if args and isinstance(args[0], dict):
                u_id = str(args[0].get("estancia_uuid") or args[0].get("uuid") or "")
                m_bol = args[0].get("modo_mas_boletas")
            else:
                u_id = str(args[0] if len(args) > 0 else "")
                m_bol = bool(args[1]) if len(args) > 1 and args[1] is not None else None
            res = mu.sugerir_unificacion(u_id, modo_mas_boletas=m_bol)
        elif method_name == "aplicarDecisionUnificacion":
            import motor_unificacion as mu
            res = mu.aplicar_decision(args[0] if args else {})
        elif method_name == "guardarModoMasBoletas":
            import motor_unificacion as mu
            a0 = args[0] if args else False
            activo = bool(a0.get("activo") if isinstance(a0, dict) else a0)
            res = mu.guardar_modo_mas_boletas(activo)
        elif method_name == "listarInexactosContabilidad":
            import contabilidad_logic as cl
            datos_c = cl.obtener_datos_contabilidad_local("2026-11")
            res = [r for r in datos_c.get("calzado_rows", []) if r.get("es_inexacto")]
        elif method_name == "obtenerAlertasWsp":
            import wsp_inteligencia as wi
            res = wi.obtener_alertas_wsp()
        elif method_name == "obtenerBandejaWsp":
            import wsp_inteligencia as wi
            res = wi.obtener_bandeja_wsp()
        elif method_name == "actualizarResumenWspAhora":
            import wsp_inteligencia as wi
            res = wi.ejecutar_analisis_agente_wsp(forzar=True)
        else:
            raise HTTPException(status_code=404, detail=f"Método RPC no soportado: {method_name}")

        sync_st = dl.obtener_estado_red_cola()
        if isinstance(res, dict) and "syncStatus" not in res:
            res["syncStatus"] = sync_st
        return {"result": res, "syncStatus": sync_st}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    print(f"[Ovalo_Sistema_v2] Mostrador Operativo activo en http://127.0.0.1:{APP_PORT} (Rol: {ROL_NODO})")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_config=None)
