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
MOTOR_DIR = BASE_DIR / "04_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

# Respaldo temporal de módulos de Fases 4 y 5 mientras se migran (forzados a usar ovalo.db de v2)
LEGACY_DIR = Path(r"C:\Recepcion_Ovalo_Local")
if LEGACY_DIR.exists() and str(LEGACY_DIR) not in sys.path:
    sys.path.append(str(LEGACY_DIR))

import config as _cfg_v2
import db as _db_v2
sys.modules["config"] = _cfg_v2
sys.modules["database"] = _db_v2

import estancias as dl
import yape_bcp as ym
import sunat as sws
import whatsapp as wi
sys.modules["domain_logic"] = dl
sys.modules["yape_manager"] = ym
sys.modules["sunat_wsp_service"] = sws
sys.modules["wsp_inteligencia"] = wi

from config import APP_HOST, APP_PORT, MODO_PRUEBA, NOMBRE_HOTEL, ROL_NODO, STATIC_DIR, WEB_DIR
from db import get_connection, init_db
from salud import reporte_salud
from sync_nube import instalar_triggers_outbox, pull_from_supabase, push_to_supabase

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_sync_wakeup_event = threading.Event()
_sync_stop_event = threading.Event()


def _background_supabase_worker() -> None:
    try:
        instalar_triggers_outbox()
        push_to_supabase()
        pull_from_supabase()
    except Exception as exc:
        print(f"[OvaloV2-Sync] Inicio: {exc}")
    while not _sync_stop_event.is_set():
        triggered = _sync_wakeup_event.wait(timeout=25)
        if _sync_stop_event.is_set():
            break
        if triggered:
            _sync_wakeup_event.clear()
            time.sleep(0.25)
        try:
            push_to_supabase()
            pull_from_supabase()
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
    _sync_wakeup_event.set()
    return {"status": "TRIGGERED", "syncStatus": dl.obtener_estado_red_cola()}


@app.get("/api/yape/recientes")
def api_yape_recientes(fecha: str = "", limite: int = 80, forzar_drive: int = 0):
    try:
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
    res = ejecutar_orden_sunat_wsp(payload, wakeup_cb=lambda: _sync_wakeup_event.set())
    res["syncStatus"] = dl.obtener_estado_red_cola()
    return JSONResponse(content=res)


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
            _sync_wakeup_event.set()
        elif method_name in ("obtenerDatosPagosPosteriores", "obtenerEstanciasConDeuda"):
            res = dl.obtener_datos_pagos_posteriores_local()
        elif method_name in ("guardarPagoPosteriorDesdeApp", "procesarPagoPosteriorApp"):
            res = dl.guardar_pago_posterior_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerDatosTienda":
            res = dl.obtener_datos_tienda_local()
        elif method_name in ("guardarVentaTiendaDesdeApp", "procesarVentaTiendaApp"):
            res = dl.guardar_venta_tienda_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerDatosGastos":
            res = dl.obtener_datos_gastos_local()
        elif method_name in ("guardarGastoDesdeApp", "procesarGastoApp"):
            res = dl.guardar_gasto_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name in ("continuarEstanciaDesdeApp", "continuarEstanciaRapida"):
            res = dl.continuar_estancia_local(args[0] if args else "")
            _sync_wakeup_event.set()
        elif method_name == "liberarHabitacionDesdeApp":
            res = dl.liberar_habitacion_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "cancelarEstanciaDesdeApp":
            res = dl.cancelar_estancia_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "restaurarEstanciaDesdeApp":
            res = dl.restaurar_estancia_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciaParaModificar":
            hab = args[0] if len(args) > 0 else ""
            fila = args[1] if len(args) > 1 else None
            res = dl.obtener_estancia_para_modificar_local(hab, fila)
        elif method_name in ("modificarEstanciaDesdeApp", "guardarModificacionEstancia"):
            res = dl.modificar_estancia_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "actualizarEstadoComprobanteDesdePanel":
            res = dl.actualizar_comprobante_desde_panel_local(args[0] if args else {})
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
            _sync_wakeup_event.set()
        elif method_name == "verificarClaveGestionTienda":
            res = dl.verificar_clave_gestion_tienda_local(args[0] if args else {})
        elif method_name == "registrarCompraTienda":
            res = dl.registrar_compra_tienda_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "cerrarCompraPendienteTienda":
            res = dl.cerrar_compra_pendiente_tienda_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "registrarMovimientoTienda":
            res = dl.registrar_movimiento_tienda_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "anularMovimientoAlmacen":
            res = dl.anular_movimiento_almacen_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroEstanciasBD":
            res = dl.obtener_registro_estancias_bd_local(args[0] if args else {})
        elif method_name == "editarEstanciaBDAdmin":
            res = dl.editar_estancia_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "eliminarEstanciaBDAdmin":
            res = dl.eliminar_estancia_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroGastosBD":
            res = dl.obtener_registro_gastos_bd_local(args[0] if args else {})
        elif method_name == "editarGastoBDAdmin":
            res = dl.editar_gasto_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "eliminarGastoBDAdmin":
            res = dl.eliminar_gasto_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroVentasTiendaBD":
            res = dl.obtener_registro_ventas_tienda_bd_local(args[0] if args else {})
        elif method_name == "editarVentaTiendaBDAdmin":
            res = dl.editar_venta_tienda_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "eliminarVentaTiendaBDAdmin":
            res = dl.eliminar_venta_tienda_bd_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerRegistroPagosBD":
            res = dl.obtener_registro_pagos_bd_local(args[0] if args else {})
        elif method_name == "editarPagoBDAdmin":
            res = dl.editar_pago_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "eliminarPagoBDAdmin":
            res = dl.eliminar_pago_bd_admin_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "guardarCheckInGrupalEmpresa":
            res = dl.guardar_checkin_grupal_empresa_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciasPagoMasivo":
            res = dl.obtener_estancias_pago_masivo_local(args[0] if args else {})
        elif method_name == "guardarPagoMasivo":
            res = dl.guardar_pago_masivo_local(args[0] if args else {})
            _sync_wakeup_event.set()
        elif method_name == "obtenerEstanciaFacturacionPorUuid":
            a0 = args[0] if args else ""
            u_id = a0.get("uuid", "") if isinstance(a0, dict) else str(a0 or "")
            res = dl.obtener_estancia_facturacion_por_uuid(u_id)
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
