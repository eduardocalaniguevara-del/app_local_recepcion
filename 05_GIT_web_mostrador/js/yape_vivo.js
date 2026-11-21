/**
 * ============================================================================
 * PANEL DE YAPES Y CONCILIACIÓN MENSUAL EN SQLITE (REEMPLAZO DE HOJA CONCILIACIÓN)
 * ============================================================================
 * Diseño integrado a PanelDisponibilidad.html (#F7EFEA / #FAF6F3 / #2D2623).
 * Dividido en 2 modos (ambos estructurados POR MES para continuidad total):
 *   1) MODO RECEPCIÓN (No Administrador - por defecto):
 *      - Navegación por MES (◀ | Noviembre 2026 | ▶) en lugar de cortar por día.
 *      - Pantalla dividida exactamente en 2 mitades verticales (50% arriba / 50% abajo):
 *        * Mitad Superior (50%): Últimos Yapes sueltos por anotar del mes (con scroll propio).
 *        * Mitad Inferior (50% hacia abajo): Yapes recibidos diarios del mes (con scroll propio).
 *   2) MODO ADMINISTRADOR (protegido con contraseña):
 *      - Cabecera con último Reporte de Drive + fecha/hora + continuidad diaria sin cortes.
 *      - Estructura mensual completa con scroll libre hasta el final del mes en 4 pestañas:
 *        * 🔗 Cruzados (Enlazados): Comparación lado a lado (Extraído del Banco <-> Anotado Recepción).
 *        * ⚠️ Sueltos (Mes): 1A. Sueltos en Banco sin anotar + 1B. Anotados sin respaldo Yape.
 *        * 📥 Extraídos del Reporte: Flujo continuo extraído del Reporte de Transacciones.
 *        * 📊 Conciliación (Formato Hoja): Reemplazo directo de `CONCILIACION PAGOS YAPE` en SQLite.
 * ============================================================================
 */
(function () {
  if (window._yapeComprobadorIniciado) return;
  window._yapeComprobadorIniciado = true;

  let _ultimoIdConocido = null;
  let _estadosConocidos = {};
  let _ultimoPingConocido = null;
  let _ultimoResumen = null;
  let _mesSeleccionado = ""; // 'MM/YYYY' (ej. '11/2026')
  let _diaFiltroAdmin = ""; // '' = todo el mes, o 'DD/MM/YYYY' opcional en Admin
  let _panelExpandido = false;
  let _busquedaTexto = "";

  // Estado de Modo Administrador
  let _modoAdminActivo = false;
  let _mostrarPromptClave = false;
  let _tabAdminActiva = "cruzados"; // 'cruzados' | 'sueltos' | 'extraidos' | 'conciliacion'

  const _ADMIN_EMAIL_CLAVE = "eduardocalaniguevara@gmail.com";

  const _NOMBRES_MESES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
  ];

  function escHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function sonarAvisoYape() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const notas = [
        { f: 587.33, t: 0.0, d: 0.1 },
        { f: 880.0, t: 0.11, d: 0.18 },
      ];
      notas.forEach((n) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(n.f, ctx.currentTime + n.t);
        gain.gain.setValueAtTime(0.12, ctx.currentTime + n.t);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + n.t + n.d);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(ctx.currentTime + n.t);
        osc.stop(ctx.currentTime + n.t + n.d);
      });
    } catch (e) {}
  }

  // =========================================================================
  // UTILIDADES DE MES (MM/YYYY)
  // =========================================================================
  function obtenerMesHoyMMYYYY() {
    const d = new Date();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    return `${mm}/${yyyy}`;
  }

  function obtenerMesDelPanelPrincipalMMYYYY() {
    const el = document.getElementById("lblFechaVisibleDMY");
    if (el && el.textContent && /\d{2}\/\d{2}\/\d{4}/.test(el.textContent.trim())) {
      const p = el.textContent.trim().split("/");
      return `${p[1]}/${p[2]}`;
    }
    return obtenerMesHoyMMYYYY();
  }

  function formatearEtiquetaMes(mmyyyy) {
    if (!mmyyyy || mmyyyy === "TODOS") return "Todos los meses";
    const p = String(mmyyyy).split("/");
    if (p.length !== 2) return mmyyyy;
    const mIdx = parseInt(p[0], 10) - 1;
    const nom = _NOMBRES_MESES[mIdx] || p[0];
    return `${nom} ${p[1]}`;
  }

  function sumarMesesMMYYYY(mmyyyyBase, deltaMeses) {
    const base = mmyyyyBase && mmyyyyBase.includes("/") ? mmyyyyBase : obtenerMesHoyMMYYYY();
    const p = base.split("/");
    let m = parseInt(p[0], 10) - 1;
    let y = parseInt(p[1], 10);
    const dt = new Date(y, m + Number(deltaMeses || 0), 1, 12, 0, 0);
    const mm = String(dt.getMonth() + 1).padStart(2, "0");
    return `${mm}/${dt.getFullYear()}`;
  }

  // =========================================================================
  // AVISOS DISCRETOS EN ESQUINA INFERIOR DERECHA
  // =========================================================================
  function obtenerContenedorAvisos() {
    let cont = document.getElementById("yapeCasillaAvisosContainer");
    if (!cont) {
      cont = document.createElement("div");
      cont.id = "yapeCasillaAvisosContainer";
      cont.style.cssText =
        "position:fixed; bottom:16px; right:16px; z-index:99998; display:flex; flex-direction:column; gap:8px; max-width:340px; width:calc(100vw - 32px); pointer-events:none;";
      document.body.appendChild(cont);
    }
    return cont;
  }

  window.mostrarCasillaConexionCelular = function (horaIso) {
    const cont = obtenerContenedorAvisos();
    const card = document.createElement("div");
    card.style.cssText =
      "pointer-events:auto; background:#FAF6F3; color:#1e293b; border:1px solid #D8C7B8; border-left:4px solid #16a34a; border-radius:8px; padding:10px 12px; box-shadow:0 8px 20px rgba(0,0,0,0.15); font-family:system-ui,-apple-system,sans-serif; font-size:11.5px;";
    card.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
        <span style="font-weight:800;color:#15803d;font-size:11px;">📱 Celular conectado (${escHtml(horaIso || "Ahora")})</span>
        <button type="button" onclick="this.closest('div').parentElement.remove()" style="background:transparent;border:none;color:#64748b;font-size:13px;font-weight:800;cursor:pointer;line-height:1;">✕</button>
      </div>
      <div style="color:#334155;font-size:11px;">
        Conexión activa con el celular de recepción.
      </div>
    `;
    cont.appendChild(card);
    setTimeout(() => {
      if (card && card.parentElement) card.remove();
    }, 10000);
  };

  window.mostrarMensajeYapeRecibido = function (yape) {
    if (!yape) return;
    const cont = obtenerContenedorAvisos();
    const monto = Number(yape.monto_bcp || 0).toFixed(2);
    const remitente = String(yape.remitente_bcp || "Cliente Yape");
    const hora = String(yape.hora12 || yape.hora_operacion || "");
    const cod = String(yape.nro_operacion || "");

    const card = document.createElement("div");
    card.style.cssText =
      "pointer-events:auto; background:#FAF6F3; color:#1e293b; border:1px solid #D8C7B8; border-left:4px solid #2D2623; border-radius:8px; padding:10px 12px; box-shadow:0 10px 24px rgba(0,0,0,0.18); font-family:system-ui,-apple-system,sans-serif;";
    card.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="background:#2D2623;color:#fff;font-weight:800;font-size:10px;padding:1.5px 6px;border-radius:4px;">
            📱 Nuevo Yape
          </span>
          <span style="font-size:11px;color:#475569;font-weight:700;">${escHtml(hora)}</span>
        </div>
        <button type="button" onclick="this.closest('div').parentElement.remove()" style="background:transparent;border:none;color:#64748b;font-size:13px;font-weight:800;cursor:pointer;line-height:1;">✕</button>
      </div>
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:4px;">
        <div style="font-size:12px;font-weight:800;color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          ${escHtml(remitente)}
        </div>
        <div style="font-size:13px;font-weight:900;color:#15803d;white-space:nowrap;">
          S/ ${monto}
        </div>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:10.5px;color:#64748b;">
        <span>Cód/Op: <b>${escHtml(cod || "-")}</b></span>
        <button type="button" onclick="window.abrirModalYapeComprobador()" style="background:#fff;color:#1e293b;border:1px solid #D8C7B8;border-radius:5px;padding:2px 8px;font-size:10.5px;font-weight:800;cursor:pointer;">
          Ver lista →
        </button>
      </div>
    `;
    cont.appendChild(card);
    setTimeout(() => {
      if (card && card.parentElement) card.remove();
    }, 16000);
  };

  // =========================================================================
  // VERIFICACIÓN DE CONTRASEÑA DE ADMINISTRADOR
  // =========================================================================
  async function verificarClaveAdmin(claveRaw) {
    const limpia = String(claveRaw || "").trim().toLowerCase();
    if (!limpia) return false;
    if (limpia === _ADMIN_EMAIL_CLAVE) return true;
    try {
      const r = await fetch("/api/rpc/verificarClaveCuentasHorario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ args: [{ clave: claveRaw }] }),
      });
      if (r.ok) {
        const j = await r.json();
        if (j && j.ok && j.result && j.result.ok) return true;
      }
    } catch (e) {}
    return false;
  }

  // =========================================================================
  // PANEL LATERAL DERECHO — ESTRUCTURA MENSUAL
  // =========================================================================
  function crearPanelDerechoYape() {
    let overlay = document.getElementById("yapeRightDrawerOverlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "yapeRightDrawerOverlay";
      overlay.style.cssText =
        "position:fixed; inset:0; background:rgba(15,23,42,0.28); z-index:99990; display:none;";
      overlay.onclick = function (e) {
        if (e.target === overlay) cerrarPanelDerechoYape();
      };

      const panel = document.createElement("div");
      panel.id = "yapeRightDrawerPanel";
      panel.style.cssText =
        "position:fixed; top:0; right:0; height:100vh; width:min(620px, 96vw); background:#F7EFEA; color:#1e293b; border-left:2px solid #D8C7B8; box-shadow:-8px 0 28px rgba(0,0,0,0.22); display:flex; flex-direction:column; z-index:99991; font-family:system-ui,-apple-system,sans-serif; transition:width 0.18s ease;";
      panel.onclick = function (e) {
        e.stopPropagation();
      };

      panel.innerHTML = `
        <!-- Cabecera sobria acorde a la tabla principal (#2D2623) -->
        <div style="background:#2D2623; color:#fff; padding:9px 12px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #433A36; flex-shrink:0;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:14px;">📱</span>
            <div>
              <div style="display:flex; align-items:center; gap:6px;">
                <span style="font-size:12.5px; font-weight:800; letter-spacing:-0.01em;">Conciliación y Yapes del Mes</span>
                <span id="yapeBadgeModoActual" style="background:#433A36; color:#e2e8f0; font-size:9.5px; font-weight:700; padding:1px 6px; border-radius:4px;">
                  Recepción (Vista Mensual)
                </span>
              </div>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:5px;">
            <button type="button" id="btnYapeToggleAdmin" style="background:#433A36; color:#f8fafc; border:1px solid #574b46; border-radius:5px; padding:3.5px 8px; font-size:10.5px; font-weight:700; cursor:pointer;" title="Desbloquear Conciliación de Administrador">
              🔒 Admin
            </button>
            <button type="button" id="btnToggleAnchoYape" style="background:#433A36; color:#e2e8f0; border:1px solid #574b46; border-radius:5px; padding:3.5px 7px; font-size:10.5px; font-weight:700; cursor:pointer;" title="Ampliar o reducir ancho del panel">
              ⤢
            </button>
            <button type="button" id="btnCerrarPanelDerechoYape" style="background:#dc2626; color:#fff; border:none; border-radius:5px; padding:3.5px 8px; font-size:11px; font-weight:800; cursor:pointer;">
              ✕
            </button>
          </div>
        </div>

        <!-- Barra de contraseña de Administrador (oculta por defecto) -->
        <div id="yapeBarraPasswordAdmin" style="display:none; background:#FAF6F3; border-bottom:1px solid #D8C7B8; padding:8px 12px; flex-shrink:0;">
          <form id="formYapeClaveAdmin" style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:0;">
            <span style="font-size:11px; font-weight:800; color:#334155;">🔑 Clave Admin:</span>
            <input type="password" id="inpYapeClaveAdmin" placeholder="Ingresa tu clave o correo..." autocomplete="off"
                   style="flex:1; min-width:150px; background:#fff; color:#0f172a; border:1px solid #D8C7B8; border-radius:5px; padding:4px 8px; font-size:11px;" />
            <button type="submit" style="background:#1e293b; color:#fff; border:none; border-radius:5px; padding:4px 10px; font-size:11px; font-weight:800; cursor:pointer;">
              Entrar
            </button>
            <button type="button" id="btnCancelarClaveAdmin" style="background:#fff; color:#475569; border:1px solid #D8C7B8; border-radius:5px; padding:4px 8px; font-size:11px; font-weight:700; cursor:pointer;">
              Cancelar
            </button>
          </form>
          <div id="lblErrorClaveAdmin" style="display:none; color:#dc2626; font-size:10.5px; font-weight:700; margin-top:4px;"></div>
        </div>

        <!-- Barra de Mes (◀ | Noviembre 2026 | ▶) + Filtro de Día opcional en Admin + Buscador -->
        <div style="background:#FAF6F3; padding:7px 12px; display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; border-bottom:1px solid #DFD2C5; flex-shrink:0;">
          <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
            <!-- Selector de MES -->
            <div id="grupoMesYapePanel" style="display:inline-flex; align-items:center; gap:3px; background:#16a34a; border:2px solid #15803d; padding:2px 5px; border-radius:7px;">
              <button type="button" id="btnYapeMesAnterior" title="Mes anterior"
                      style="padding:1px 7px; border-radius:4px; background:rgba(255,255,255,0.95); color:#0f172a; font-weight:900; font-size:11px; border:none; cursor:pointer;">
                ◀
              </button>
              <div style="position:relative; display:inline-flex; align-items:center; background:#fff; border-radius:4px; border:1px solid #D8C7B8; padding:1px 8px; cursor:pointer;"
                   title="Seleccionar mes">
                <span id="lblYapeMesVisible" style="font-weight:900; color:#0f172a; font-size:11.5px; user-select:none;">Noviembre 2026</span>
                <span style="margin-left:5px; font-size:10.5px; color:#64748b; user-select:none;">🗓️</span>
                <select id="selYapeMesRapido" style="position:absolute; inset:0; opacity:0; width:100%; height:100%; cursor:pointer;"></select>
              </div>
              <button type="button" id="btnYapeMesSiguiente" title="Mes siguiente"
                      style="padding:1px 7px; border-radius:4px; background:rgba(255,255,255,0.95); color:#0f172a; font-weight:900; font-size:11px; border:none; cursor:pointer;">
                ▶
              </button>
            </div>

            <button type="button" id="btnYapeIrMesActual" style="display:none; background:#fff; color:#1e293b; border:1px solid #D8C7B8; border-radius:5px; padding:3px 8px; font-size:10.5px; font-weight:800; cursor:pointer;">
              Mes Actual
            </button>

            <!-- Selector rápido de Día dentro del mes (visible en Modo Admin si quiere filtrar un día o ver todo el mes) -->
            <select id="selYapeDiaDentroDelMes" style="display:none; background:#fff; color:#1e293b; border:1px solid #D8C7B8; border-radius:5px; padding:3px 6px; font-size:10.5px; font-weight:700; cursor:pointer;">
              <option value="">📅 Todo el mes</option>
            </select>
          </div>

          <div style="display:flex; align-items:center; gap:5px; margin-left:auto;">
            <input type="text" id="inpYapeBuscarTexto" placeholder="🔍 Buscar nombre, monto, hab, fecha..."
                   style="background:#fff; color:#1e293b; border:1px solid #D8C7B8; border-radius:5px; padding:3px 7px; font-size:11px; width:175px;" />
            <button type="button" id="btnYapeRefrescarSimple" title="Actualizar mes"
                    style="background:#fff; color:#334155; border:1px solid #D8C7B8; border-radius:5px; padding:3px 7px; font-size:11px; font-weight:700; cursor:pointer;">
              🔄
            </button>
          </div>
        </div>

        <!-- Contenedor principal -->
        <div id="yapeContenidoPrincipal" style="flex:1; min-height:0; overflow:hidden; padding:10px 12px; display:flex; flex-direction:column; gap:10px;"></div>
      `;

      overlay.appendChild(panel);
      document.body.appendChild(overlay);

      // Eventos principales
      panel.querySelector("#btnCerrarPanelDerechoYape").onclick = cerrarPanelDerechoYape;

      panel.querySelector("#btnToggleAnchoYape").onclick = function () {
        _panelExpandido = !_panelExpandido;
        actualizarAnchoPanel();
      };

      // Botón Modo Admin
      panel.querySelector("#btnYapeToggleAdmin").onclick = function () {
        if (_modoAdminActivo) {
          _modoAdminActivo = false;
          _mostrarPromptClave = false;
          _diaFiltroAdmin = "";
          actualizarEstadoVisualModoAdmin();
          if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
        } else {
          _mostrarPromptClave = !_mostrarPromptClave;
          const barraPass = panel.querySelector("#yapeBarraPasswordAdmin");
          const errEl = panel.querySelector("#lblErrorClaveAdmin");
          if (errEl) errEl.style.display = "none";
          if (barraPass) {
            barraPass.style.display = _mostrarPromptClave ? "block" : "none";
            if (_mostrarPromptClave) {
              const inp = panel.querySelector("#inpYapeClaveAdmin");
              if (inp) {
                inp.value = "";
                setTimeout(() => inp.focus(), 40);
              }
            }
          }
        }
      };

      panel.querySelector("#btnCancelarClaveAdmin").onclick = function () {
        _mostrarPromptClave = false;
        const barraPass = panel.querySelector("#yapeBarraPasswordAdmin");
        if (barraPass) barraPass.style.display = "none";
      };

      panel.querySelector("#formYapeClaveAdmin").onsubmit = async function (e) {
        e.preventDefault();
        const inp = panel.querySelector("#inpYapeClaveAdmin");
        const errEl = panel.querySelector("#lblErrorClaveAdmin");
        const val = inp ? inp.value : "";
        const ok = await verificarClaveAdmin(val);
        if (ok) {
          _modoAdminActivo = true;
          _mostrarPromptClave = false;
          if (inp) inp.value = "";
          if (errEl) errEl.style.display = "none";
          const barraPass = panel.querySelector("#yapeBarraPasswordAdmin");
          if (barraPass) barraPass.style.display = "none";
          actualizarEstadoVisualModoAdmin();
          if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
        } else {
          if (errEl) {
            errEl.textContent = "❌ Contraseña incorrecta.";
            errEl.style.display = "block";
          }
        }
      };

      // Navegación por MES: ◀ Mes anterior / ▶ Mes siguiente
      panel.querySelector("#btnYapeMesAnterior").onclick = function () {
        const base = _mesSeleccionado && _mesSeleccionado !== "TODOS" ? _mesSeleccionado : obtenerMesHoyMMYYYY();
        _mesSeleccionado = sumarMesesMMYYYY(base, -1);
        _diaFiltroAdmin = "";
        cargarYapesDesdeServidor(_mesSeleccionado, false);
      };

      panel.querySelector("#btnYapeMesSiguiente").onclick = function () {
        const base = _mesSeleccionado && _mesSeleccionado !== "TODOS" ? _mesSeleccionado : obtenerMesHoyMMYYYY();
        _mesSeleccionado = sumarMesesMMYYYY(base, 1);
        _diaFiltroAdmin = "";
        cargarYapesDesdeServidor(_mesSeleccionado, false);
      };

      panel.querySelector("#btnYapeIrMesActual").onclick = function () {
        _mesSeleccionado = obtenerMesHoyMMYYYY();
        _diaFiltroAdmin = "";
        cargarYapesDesdeServidor(_mesSeleccionado, false);
      };

      const selMes = panel.querySelector("#selYapeMesRapido");
      selMes.addEventListener("change", () => {
        if (selMes.value) {
          _mesSeleccionado = selMes.value;
          _diaFiltroAdmin = "";
          cargarYapesDesdeServidor(_mesSeleccionado, false);
        }
      });

      const selDia = panel.querySelector("#selYapeDiaDentroDelMes");
      selDia.addEventListener("change", () => {
        _diaFiltroAdmin = selDia.value || "";
        if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
      });

      const inpBuscar = panel.querySelector("#inpYapeBuscarTexto");
      inpBuscar.addEventListener("input", () => {
        _busquedaTexto = String(inpBuscar.value || "").trim().toLowerCase();
        if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
      });

      panel.querySelector("#btnYapeRefrescarSimple").onclick = function () {
        cargarYapesDesdeServidor(_mesSeleccionado, false);
      };
    }
    return overlay;
  }

  function actualizarAnchoPanel() {
    const panel = document.getElementById("yapeRightDrawerPanel");
    if (!panel) return;
    if (_panelExpandido) {
      panel.style.width = "min(1140px, 99vw)";
    } else {
      panel.style.width = _modoAdminActivo ? "min(960px, 97vw)" : "min(600px, 96vw)";
    }
  }

  function actualizarEstadoVisualModoAdmin() {
    const badge = document.getElementById("yapeBadgeModoActual");
    const btnAdmin = document.getElementById("btnYapeToggleAdmin");
    const selDia = document.getElementById("selYapeDiaDentroDelMes");
    if (badge) {
      if (_modoAdminActivo) {
        badge.textContent = "🔓 Administrador (Conciliación SQLite)";
        badge.style.background = "#b45309";
        badge.style.color = "#fff";
      } else {
        badge.textContent = "Recepción (Vista Mensual)";
        badge.style.background = "#433A36";
        badge.style.color = "#e2e8f0";
      }
    }
    if (btnAdmin) {
      if (_modoAdminActivo) {
        btnAdmin.textContent = "🔒 Salir Admin";
        btnAdmin.style.background = "#b45309";
        btnAdmin.style.borderColor = "#d97706";
      } else {
        btnAdmin.textContent = "🔒 Admin";
        btnAdmin.style.background = "#433A36";
        btnAdmin.style.borderColor = "#574b46";
      }
    }
    if (selDia) {
      selDia.style.display = _modoAdminActivo ? "inline-block" : "none";
    }
    actualizarAnchoPanel();
  }

  function cerrarPanelDerechoYape() {
    const overlay = document.getElementById("yapeRightDrawerOverlay");
    if (overlay) overlay.style.display = "none";
    if (_modoAdminActivo || _mostrarPromptClave) {
      _modoAdminActivo = false;
      _mostrarPromptClave = false;
      _diaFiltroAdmin = "";
      const barraPass = document.getElementById("yapeBarraPasswordAdmin");
      if (barraPass) barraPass.style.display = "none";
      actualizarEstadoVisualModoAdmin();
    }
  }

  window.cambiarTabYapeAdmin = function (nuevaTab) {
    _tabAdminActiva = nuevaTab;
    if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
  };

  window.forzarExtraccionDriveAdmin = async function (btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "⏳ Extrayendo y conciliando...";
    }
    await cargarYapesDesdeServidor(_mesSeleccionado, true);
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "🔄 Extraer reporte de Drive";
    }
  };

  window.sincronizarCorreoMaelenAdmin = async function (btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "⏳ Leyendo Correo BCP...";
    }
    try {
      await fetch("/api/yape/sincronizar-maelen", { method: "POST", cache: "no-store" });
      await cargarYapesDesdeServidor(_mesSeleccionado, false);
      _tabAdminActiva = "maelen";
      if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
    } catch (e) {}
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "✉️ Correo Maelen";
    }
  };

  window.probarMensajeroWsp5MinAdmin = async function (btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "⏳ Verificando +5 min...";
    }
    try {
      await fetch("/api/yape/verificar-alertas-5min", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ventana_horas: 72, incluir_reciente_supervision: true, enviar_wsp_real: false }),
        cache: "no-store",
      });
      await cargarYapesDesdeServidor(_mesSeleccionado, false);
      _tabAdminActiva = "maelen";
      if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
    } catch (e) {}
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "📲 Mensajero WSP (5m)";
    }
  };

  window.ejecutarRevisorYape830 = async function (btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "⏳ Revisando 8:30am...";
    }
    try {
      await fetch("/api/yape/revisor-830am", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fecha_filtro: _mesSeleccionado || null,
          forzar_extraccion: false,
          enviar_wsp_real: false,
        }),
        cache: "no-store",
      });
      await cargarYapesDesdeServidor(_mesSeleccionado, false);
      _tabAdminActiva = "maelen";
      if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
    } catch (e) {}
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "🧠 Revisor 8:30am";
    }
  };

  // =========================================================================
  // RENDERIZADO DEL PANEL
  //   1) MODO RECEPCIÓN: Por Mes, 50% arriba (Últimos sueltos) / 50% abajo (Recibidos diarios)
  //   2) MODO ADMIN: Por Mes, con scroll completo en Cruzados, Sueltos, Extraídos y Conciliación
  // =========================================================================
  function renderizarPanelDerecho(data) {
    const overlay = document.getElementById("yapeRightDrawerOverlay");
    if (!overlay || !data) return;

    const mesHoy = obtenerMesHoyMMYYYY();
    const mesMostrado = String(data.mesMostrado || _mesSeleccionado || mesHoy);
    _mesSeleccionado = mesMostrado;

    const lblMes = overlay.querySelector("#lblYapeMesVisible");
    if (lblMes) {
      lblMes.textContent = formatearEtiquetaMes(mesMostrado);
    }

    // Poblar selector rápido de meses disponibles
    const selMes = overlay.querySelector("#selYapeMesRapido");
    if (selMes) {
      const listaMeses = Array.isArray(data.mesesDisponibles) && data.mesesDisponibles.length > 0 ? data.mesesDisponibles : [mesMostrado];
      let optsMes = "";
      listaMeses.forEach((m) => {
        const selAttr = m === mesMostrado ? "selected" : "";
        optsMes += `<option value="${escHtml(m)}" ${selAttr}>${escHtml(formatearEtiquetaMes(m))} (${escHtml(m)})</option>`;
      });
      optsMes += `<option value="TODOS" ${mesMostrado === "TODOS" ? "selected" : ""}>📚 Todos los meses</option>`;
      selMes.innerHTML = optsMes;
    }

    const grupoMes = overlay.querySelector("#grupoMesYapePanel");
    const btnMesActual = overlay.querySelector("#btnYapeIrMesActual");
    const esMesActual = mesMostrado === mesHoy;
    if (grupoMes) {
      grupoMes.style.backgroundColor = esMesActual ? "#16a34a" : "#d97706";
      grupoMes.style.borderColor = esMesActual ? "#15803d" : "#b45309";
    }
    if (btnMesActual) {
      btnMesActual.style.display = esMesActual ? "none" : "inline-block";
    }

    // Poblar selector opcional de día dentro del mes (para Modo Admin)
    const selDia = overlay.querySelector("#selYapeDiaDentroDelMes");
    if (selDia) {
      const diasSet = new Set();
      (data.extraidosReporte || []).forEach((it) => {
        if (it.fecha_operacion) diasSet.add(it.fecha_operacion);
      });
      (data.sueltosAnotados || []).forEach((an) => {
        if (an.fecha_operacion) diasSet.add(an.fecha_operacion);
      });
      const diasArr = Array.from(diasSet).sort((a, b) => {
        const pa = a.split("/").reverse().join("-");
        const pb = b.split("/").reverse().join("-");
        return pb.localeCompare(pa);
      });
      let htmlDias = `<option value="">📅 Todo ${escHtml(formatearEtiquetaMes(mesMostrado))} (${diasArr.length} días)</option>`;
      diasArr.forEach((d) => {
        htmlDias += `<option value="${escHtml(d)}" ${_diaFiltroAdmin === d ? "selected" : ""}>📆 Día ${escHtml(d)}</option>`;
      });
      selDia.innerHTML = htmlDias;
      selDia.style.display = _modoAdminActivo ? "inline-block" : "none";
    }

    const filtro = _busquedaTexto;
    const pasaDiaAdmin = (fechaOp) => {
      if (!_modoAdminActivo || !_diaFiltroAdmin) return true;
      return String(fechaOp || "") === _diaFiltroAdmin;
    };

    const filtrarItemBanco = (it) => {
      if (!pasaDiaAdmin(it.fecha_operacion)) return false;
      if (!filtro) return true;
      const enl = it.enlace_recepcion || {};
      const texto = `${it.fecha_operacion || ""} ${it.remitente_bcp || ""} ${it.monto_bcp || ""} ${it.nro_operacion || ""} ${it.hora12 || ""} ${it.huesped_calzado || ""} ${it.hab_calzada || ""} ${enl.hab || ""} ${enl.huesped || ""} ${enl.glosa_anotada || ""}`.toLowerCase();
      return texto.includes(filtro);
    };

    const recibidos = (data.recibidos || []).filter(filtrarItemBanco);
    const cruzados = (data.cruzados || []).filter(filtrarItemBanco);
    const sueltosRecibidos = (data.sueltosRecibidos || []).filter(filtrarItemBanco);
    const extraidosReporte = (data.extraidosReporte || []).filter(filtrarItemBanco);
    const sueltosAnotados = (data.sueltosAnotados || []).filter((an) => {
      if (!pasaDiaAdmin(an.fecha_operacion)) return false;
      if (!filtro) return true;
      const t = `${an.fecha_operacion || ""} ${an.hab || ""} ${an.huesped || ""} ${an.monto || ""} ${an.glosa || ""} ${an.concepto || ""}`.toLowerCase();
      return t.includes(filtro);
    });
    const tablaConciliacion = (data.tablaConciliacion || []).filter((row) => {
      if (_modoAdminActivo && _diaFiltroAdmin) {
        const fB = String(row.fecha_hora_banco || "").split(" ")[0];
        const fR = String(row.fecha_registro || "").split(" ")[0];
        if (fB !== _diaFiltroAdmin && fR !== _diaFiltroAdmin) return false;
      }
      if (!filtro) return true;
      const t = `${row.fecha_hora_banco || ""} ${row.origen_banco || ""} ${row.monto_banco || ""} ${row.monto_anotado || ""} ${row.huesped_anotado || ""} ${row.hora_anotada || ""} ${row.estado_conciliacion || ""} ${row.id_registro || ""}`.toLowerCase();
      return t.includes(filtro);
    });
    const transferenciasMaelen = (data.transferenciasMaelen || []).filter((mv) => {
      if (!pasaDiaAdmin(mv.fecha_operacion)) return false;
      if (!filtro) return true;
      const t = `${mv.fecha_operacion || ""} ${mv.remitente_o_destino || ""} ${mv.monto || ""} ${mv.tipo_movimiento || ""} ${mv.nro_operacion || ""} ${mv.detalle_cruce || ""}`.toLowerCase();
      return t.includes(filtro);
    });
    const alertasWsp5Min = (data.alertasWsp5Min || []).filter((al) => {
      if (!filtro) return true;
      const t = `${al.fecha_operacion || ""} ${al.remitente_bcp || ""} ${al.monto_bcp || ""} ${al.mensaje_wsp || ""} ${al.estado_envio || ""}`.toLowerCase();
      return t.includes(filtro);
    });

    const cont = overlay.querySelector("#yapeContenidoPrincipal");
    if (!cont) return;

    const etiquetaPeriodo = _modoAdminActivo && _diaFiltroAdmin ? _diaFiltroAdmin : formatearEtiquetaMes(mesMostrado);

    // =======================================================================
    // VISTA 1: MODO RECEPCIÓN (NO ADMINISTRADOR)
    // - Filtrado por MES (no por día).
    // - Pantalla dividida 50% arriba (Últimos Yapes sueltos del mes)
    //   y 50% hacia abajo (Yapes recibidos diarios del mes).
    // =======================================================================
    if (!_modoAdminActivo) {
      cont.style.overflow = "hidden";

      let itemsSueltosHtml = "";
      if (sueltosRecibidos.length === 0) {
        itemsSueltosHtml = `
          <div style="padding:18px 12px; text-align:center; color:#065F46; background:#ECFDF5; border:1px solid #A7F3D0; border-radius:6px; font-size:11.5px; font-weight:700;">
            ✓ No hay Yapes sueltos pendientes por anotar en <b>${escHtml(etiquetaPeriodo)}</b>.
          </div>
        `;
      } else {
        sueltosRecibidos.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FFFFFF" : "#FFFDF9";
          const tieneAlertaWsp = Boolean(it.alerta_wsp_5m) || (it.minutos_sin_anotar != null && Number(it.minutos_sin_anotar) >= 5);
          const badgeWsp5m = tieneAlertaWsp
            ? `<span style="background:#DCFCE7; color:#166534; border:1px solid #86EFAC; font-size:9px; font-weight:800; padding:1px 5px; border-radius:4px; white-space:nowrap;" title="Alerta WhatsApp (+5 min sin registrar)">📲 WSP +5m</span>`
            : "";
          itemsSueltosHtml += `
            <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; padding:6px 9px; background:${bg}; border:1px solid #FDE68A; border-radius:5px; font-size:11px; flex-shrink:0;">
              <div style="display:flex; align-items:center; gap:7px; min-width:0;">
                <span style="background:#FEF3C7; color:#92400e; border:1px solid #FCD34D; font-weight:800; font-size:10px; padding:1px 5px; border-radius:4px; white-space:nowrap;">
                  ${escHtml(it.fecha_operacion || "")} • ${escHtml(it.hora12 || it.hora_operacion || "")}
                </span>
                <span style="font-weight:700; color:#1e293b; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                  ${escHtml(it.remitente_bcp || "")}
                </span>
              </div>
              <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
                ${badgeWsp5m}
                <span style="font-size:10px; color:#64748b;">Cód: <b>${escHtml(it.nro_operacion || "-")}</b></span>
                <span style="font-weight:900; color:#b45309; font-size:12px; min-width:68px; text-align:right;">
                  S/ ${Number(it.monto_bcp || 0).toFixed(2)}
                </span>
              </div>
            </div>
          `;
        });
      }

      // Tabla mitad inferior: Yapes recibidos diarios del mes
      let filasRecibidosHtml = "";
      if (recibidos.length === 0) {
        filasRecibidosHtml = `
          <tr>
            <td colspan="5" style="padding:20px 10px; text-align:center; color:#64748b; font-size:11.5px;">
              No hay Yapes registrados en <b>${escHtml(etiquetaPeriodo)}</b>.
            </td>
          </tr>
        `;
      } else {
        let diaAnterior = "";
        recibidos.forEach((it, idx) => {
          const fOp = String(it.fecha_operacion || "");
          if (fOp && fOp !== diaAnterior) {
            diaAnterior = fOp;
            filasRecibidosHtml += `
              <tr style="background:#EAE0D5; border-bottom:1px solid #D8C7B8;">
                <td colspan="5" style="padding:4px 8px; font-size:10px; font-weight:900; color:#2D2623; letter-spacing:0.02em;">
                  📅 DÍA ${escHtml(fOp)}
                </td>
              </tr>
            `;
          }
          const est = String(it.estado_auditoria || "SIN ANOTAR").toUpperCase();
          const anotado = est === "COMPROBADO" || est === "EXCEDENTE";
          const bgRow = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const estadoBadge = anotado
            ? `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-weight:800; font-size:9.5px; padding:1px 6px; border-radius:4px;">✓ Anotado</span>`
            : `<span style="background:#FFFBEB; color:#92400E; border:1px solid #FCD34D; font-weight:800; font-size:9.5px; padding:1px 6px; border-radius:4px;">⏳ Suelto</span>`;

          filasRecibidosHtml += `
            <tr style="background:${bgRow}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#475569; font-size:10.5px; border-right:1px solid #EFE7DF;">
                ${escHtml(fOp)}
              </td>
              <td style="padding:6px 8px; white-space:nowrap; font-weight:800; color:#334155; font-size:11px; border-right:1px solid #EFE7DF;">
                ${escHtml(it.hora12 || it.hora_operacion || "")}
              </td>
              <td style="padding:6px 8px; font-size:11px; color:#0f172a; font-weight:700; border-right:1px solid #EFE7DF;">
                <div>${escHtml(it.remitente_bcp || "")}</div>
                <div style="font-size:10px; color:#64748b; font-weight:600;">Cód/Op: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; white-space:nowrap; font-weight:900; color:#0f172a; font-size:12px; text-align:right; border-right:1px solid #EFE7DF;">
                S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; white-space:nowrap; text-align:center;">
                ${estadoBadge}
              </td>
            </tr>
          `;
        });
      }

      cont.innerHTML = `
        <!-- MITAD SUPERIOR (50%): Últimos Yapes Sueltos por Anotar del Mes -->
        <div style="flex:1 1 50%; min-height:0; display:flex; flex-direction:column; background:#FFFBEB; border:1px solid #FCD34D; border-radius:7px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
          <div style="background:#92400E; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800; flex-shrink:0;">
            <span>⏳ Últimos Yapes Sueltos por Anotar — ${escHtml(etiquetaPeriodo)}</span>
            <span style="background:rgba(255,255,255,0.2); padding:1px 7px; border-radius:4px; font-size:10.5px;">
              ${sueltosRecibidos.length} sueltos
            </span>
          </div>
          <div style="flex:1; min-height:0; overflow-y:auto; padding:8px; display:flex; flex-direction:column; gap:5px;">
            ${itemsSueltosHtml}
          </div>
        </div>

        <!-- MITAD INFERIOR (50% HACIA ABAJO): Yapes Recibidos Diarios del Mes -->
        <div style="flex:1 1 50%; min-height:0; display:flex; flex-direction:column; background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
          <div style="background:#2D2623; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800; flex-shrink:0;">
            <span>📋 Yapes Recibidos Diarios — ${escHtml(etiquetaPeriodo)}</span>
            <span style="color:#cbd5e1; font-size:10.5px; font-weight:700;">${recibidos.length} recibidos</span>
          </div>
          <div style="flex:1; min-height:0; overflow-y:auto; overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; text-align:left; font-size:11px;">
              <thead style="position:sticky; top:0; z-index:2;">
                <tr style="background:#3A312D; color:#f8fafc; font-size:10px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px; border-right:1px solid #4E433E; width:78px;">Fecha</th>
                  <th style="padding:5px 8px; border-right:1px solid #4E433E; width:68px;">Hora</th>
                  <th style="padding:5px 8px; border-right:1px solid #4E433E;">Nombre / Remitente</th>
                  <th style="padding:5px 8px; border-right:1px solid #4E433E; text-align:right; width:82px;">Monto</th>
                  <th style="padding:5px 8px; text-align:center; width:76px;">Estado</th>
                </tr>
              </thead>
              <tbody>
                ${filasRecibidosHtml}
              </tbody>
            </table>
          </div>
        </div>
      `;
      return;
    }

    // =======================================================================
    // VISTA 2: MODO ADMINISTRADOR (REEMPLAZO DE `CONCILIACION PAGOS YAPE` EN SQLITE)
    // - Estructurado por MES con scroll libre hasta el último registro del mes.
    // - Pestañas:
    //   1) 🔗 Cruzados (Enlazados lado a lado)
    //   2) ⚠️ Sueltos (Mes) (1A Sueltos en Banco + 1B Anotados sin Yape)
    //   3) 📥 Extraídos del Reporte (continuidad del reporte de transacciones)
    //   4) 📊 Conciliación (Hoja) (tabla completa idéntica a CONCILIACION PAGOS YAPE)
    // =======================================================================
    cont.style.overflowY = "auto";

    const sumMonto = (arr, field) => arr.reduce((acc, x) => acc + Number(x[field] || 0), 0);
    const totBancoMes = sumMonto(recibidos, "monto_bcp");
    const totCruzadosMes = sumMonto(cruzados, "monto_bcp");
    const totSueltosYapeMes = sumMonto(sueltosRecibidos, "monto_bcp");
    const totSueltosAnotMes = sumMonto(sueltosAnotados, "monto");
    const totalSueltosCount = sueltosRecibidos.length + sueltosAnotados.length;

    const mkTabBtn = (id, label, count) => {
      const act = _tabAdminActiva === id;
      return `
        <button type="button" onclick="window.cambiarTabYapeAdmin('${id}')"
                style="background:${act ? "#2D2623" : "#fff"}; color:${act ? "#fff" : "#334155"}; border:1px solid ${act ? "#2D2623" : "#D8C7B8"}; border-radius:5px; padding:4.5px 10px; font-size:10.5px; font-weight:800; cursor:pointer; white-space:nowrap;">
          ${label} (${count})
        </button>
      `;
    };

    let adminHtml = `
      <!-- Cabecera Admin: Último Reporte de Drive con Hora + Continuidad Mensual + Correo Maelen / WSP 5m -->
      <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; padding:8px 10px; display:flex; flex-direction:column; gap:8px; flex-shrink:0;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
          <div style="display:flex; flex-direction:column; gap:2px;">
            <div style="font-size:11px; color:#1e293b;">
              <b>📁 Reporte Drive:</b> <span style="color:#0f172a; font-weight:800;">${escHtml(data.archivoDrive || "Sincronizado en SQLite")}</span>
              <span style="margin-left:8px; background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE; padding:1px 6px; border-radius:4px; font-size:9.5px; font-weight:800;">
                🏦 Cta Maelen (****8023): ${transferenciasMaelen.length} movs
              </span>
              <span style="margin-left:4px; background:#DCFCE7; color:#166534; border:1px solid #86EFAC; padding:1px 6px; border-radius:4px; font-size:9.5px; font-weight:800;">
                📲 Alertas WSP (+5m): ${alertasWsp5Min.length}
              </span>
            </div>
            <div style="font-size:10px; color:#047857; font-weight:700;">
              🔗 Continuidad en SQLite (${escHtml(etiquetaPeriodo)}): ${escHtml(data.rangoContinuidad || "Activa")} • Tasa Conciliación: <b>${Number(data.tasaConciliacion || 100).toFixed(1)}%</b>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap;">
            <button type="button" onclick="window.forzarExtraccionDriveAdmin(this)" style="background:#2D2623; color:#fff; border:none; border-radius:5px; padding:4.5px 9px; font-size:10px; font-weight:800; cursor:pointer;">
              🔄 Extraer reporte de Drive
            </button>
            <button type="button" onclick="window.sincronizarCorreoMaelenAdmin(this)" style="background:#1D4ED8; color:#fff; border:none; border-radius:5px; padding:4.5px 9px; font-size:10px; font-weight:800; cursor:pointer;">
              ✉️ Correo Maelen
            </button>
            <button type="button" onclick="window.probarMensajeroWsp5MinAdmin(this)" style="background:#15803D; color:#fff; border:none; border-radius:5px; padding:4.5px 9px; font-size:10px; font-weight:800; cursor:pointer;">
              📲 Mensajero WSP (5m)
            </button>
            <button type="button" onclick="window.ejecutarRevisorYape830(this)" style="background:#6D28D9; color:#fff; border:none; border-radius:5px; padding:4.5px 9px; font-size:10px; font-weight:800; cursor:pointer;">
              🧠 Revisor 8:30am
            </button>
          </div>
        </div>

        <!-- 4 tarjetas resumen del mes -->
        <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:6px;">
          <div onclick="window.cambiarTabYapeAdmin('cruzados')" style="cursor:pointer; background:#ECFDF5; border:1px solid #A7F3D0; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#065F46; font-weight:800;">🔗 CRUZADOS / ENLAZADOS (${cruzados.length})</div>
            <div style="font-size:13px; font-weight:900; color:#047857;">S/ ${totCruzadosMes.toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('sueltos')" style="cursor:pointer; background:#FFFBEB; border:1px solid #FCD34D; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#92400E; font-weight:800;">⏳ SUELTOS BANCO (${sueltosRecibidos.length})</div>
            <div style="font-size:13px; font-weight:900; color:#B45309;">S/ ${totSueltosYapeMes.toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('sueltos')" style="cursor:pointer; background:#FEF2F2; border:1px solid #FECACA; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#991B1B; font-weight:800;">⚠️ ANOTADOS SIN YAPE (${sueltosAnotados.length})</div>
            <div style="font-size:13px; font-weight:900; color:#B91C1C;">S/ ${totSueltosAnotMes.toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('extraidos')" style="cursor:pointer; background:#fff; border:1px solid #D8C7B8; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#475569; font-weight:800;">📥 EXTRAÍDOS REPORTE (${extraidosReporte.length})</div>
            <div style="font-size:13px; font-weight:900; color:#0f172a;">S/ ${totBancoMes.toFixed(2)}</div>
          </div>
        </div>

        <!-- Barra de Pestañas Admin -->
        <div style="display:flex; gap:5px; overflow-x:auto; padding-top:2px;">
          ${mkTabBtn("cruzados", "🔗 Cruzados (Enlazados)", cruzados.length)}
          ${mkTabBtn("sueltos", "⚠️ Sueltos del Mes", totalSueltosCount)}
          ${mkTabBtn("extraidos", "📥 Extraídos del Reporte", extraidosReporte.length)}
          ${mkTabBtn("conciliacion", "📊 Hoja Conciliación Yape", tablaConciliacion.length)}
          ${mkTabBtn("maelen", "🏦 Cuenta Maelen & WSP 5m", transferenciasMaelen.length + alertasWsp5Min.length)}
        </div>
      </div>
    `;

    // =====================================================================
    // PESTAÑA 1: 🔗 CRUZADOS (ENLAZADOS LADO A LADO: BANCO <-> RECEPCIÓN)
    // =====================================================================
    if (_tabAdminActiva === "cruzados") {
      let filasEnlazados = "";
      if (cruzados.length === 0) {
        filasEnlazados = `<tr><td colspan="7" style="padding:18px; text-align:center; color:#64748b; font-size:11.5px;">No hay Yapes cruzados/enlazados en ${escHtml(etiquetaPeriodo)}.</td></tr>`;
      } else {
        cruzados.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const esExc = String(it.estado_auditoria || "").toUpperCase() === "EXCEDENTE";
          const enl = it.enlace_recepcion || {};
          const mBanco = Number(it.monto_bcp || 0).toFixed(2);
          const mAnot = Number(enl.monto_anotado != null ? enl.monto_anotado : it.monto_bcp || 0).toFixed(2);
          const habStr = enl.hab || it.hab_calzada || "S/H";
          const huespStr = enl.huesped || it.huesped_calzado || "";
          const glosaStr = enl.glosa_anotada || it.nro_operacion || "—";
          const idRegStr = enl.ids_str || it.id_estancia_calzada || "—";

          let badgeEnlace = `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">🔗 CUADRADO</span>`;
          if (esExc) {
            badgeEnlace = `<span style="background:#FFFBEB; color:#B45309; border:1px solid #FCD34D; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">⚠️ DIFERENCIA</span>`;
          } else if (enl.es_grupal) {
            badgeEnlace = `<span style="background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">🔗 GRUPAL (${enl.cantidad_items})</span>`;
          }

          filasEnlazados += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <!-- Lado Extraído del Banco -->
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155; border-right:1px solid #EFE7DF;">
                <div>${escHtml(it.fecha_operacion || "")}</div>
                <div style="font-weight:900; color:#0f172a;">${escHtml(it.hora12 || it.hora_operacion || "")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a; border-right:1px solid #EFE7DF;">
                <div>${escHtml(it.remitente_bcp || "")}</div>
                <div style="font-size:10px; color:#64748b;">Cód Banco: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#047857; text-align:right; white-space:nowrap; border-right:2px solid #10B981; background:rgba(16,185,129,0.04);">
                S/ ${mBanco}
              </td>

              <!-- Puente de Enlace -->
              <td style="padding:6px 6px; text-align:center; white-space:nowrap; border-right:2px solid #10B981; background:rgba(16,185,129,0.08); font-weight:900; color:#065F46;">
                ⇄
              </td>

              <!-- Lado Anotado por Recepción -->
              <td style="padding:6px 8px; font-weight:900; color:${esExc ? "#b45309" : "#0f172a"}; text-align:right; white-space:nowrap; border-right:1px solid #EFE7DF;">
                S/ ${mAnot}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#1e293b; border-right:1px solid #EFE7DF;">
                <div style="font-weight:800; color:#065f46;">
                  ${habStr === "TIENDA" ? "🛒 Tienda" : "🏨 Hab. " + escHtml(habStr)} — ${escHtml(huespStr)}
                </div>
                <div style="color:#475569; font-size:10px;">
                  Hora/Glosa anotada: <b>${escHtml(glosaStr)}</b> • ID: <span style="font-family:monospace;">${escHtml(idRegStr)}</span>
                </div>
              </td>
              <td style="padding:6px 8px; font-size:10px; color:#334155;">
                <div style="margin-bottom:2px;">${badgeEnlace}</div>
                <div style="color:#64748b; font-size:9.5px;">${escHtml(it.observacion || "")}</div>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #A7F3D0; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#065F46; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>🔗 Yapes Extraídos del Banco ENLAZADOS con Anotaciones de Recepción — ${escHtml(etiquetaPeriodo)} (${cruzados.length})</span>
            <span>Total Enlazado: S/ ${totCruzadosMes.toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#D1FAE5; color:#065F46; font-size:10px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:6px 8px; border-right:1px solid #A7F3D0;">Fecha/Hora Banco</th>
                  <th style="padding:6px 8px; border-right:1px solid #A7F3D0;">Origen / Remitente Banco</th>
                  <th style="padding:6px 8px; text-align:right; border-right:2px solid #10B981;">Monto Banco</th>
                  <th style="padding:6px 5px; text-align:center; border-right:2px solid #10B981;">Enlace</th>
                  <th style="padding:6px 8px; text-align:right; border-right:1px solid #A7F3D0;">Monto Anotado</th>
                  <th style="padding:6px 8px; border-right:1px solid #A7F3D0;">Habitación / Huésped / Glosa Recepción</th>
                  <th style="padding:6px 8px;">Estado / Detalle del Enlace</th>
                </tr>
              </thead>
              <tbody>${filasEnlazados}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // =====================================================================
    // PESTAÑA 2: ⚠️ SUELTOS DEL MES (CON SCROLL COMPLETO HASTA EL FINAL DEL MES)
    // =====================================================================
    if (_tabAdminActiva === "sueltos") {
      let filasSY = "";
      if (sueltosRecibidos.length === 0) {
        filasSY = `<tr><td colspan="5" style="padding:14px; text-align:center; color:#065F46; font-size:11px; font-weight:700;">✓ Ningún Yape recibido en el banco está suelto sin anotar en ${escHtml(etiquetaPeriodo)}.</td></tr>`;
      } else {
        sueltosRecibidos.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          filasSY += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; font-weight:800; color:#64748b; font-size:10px; text-align:center;">
                ${idx + 1}
              </td>
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(it.fecha_operacion || "")} <b>${escHtml(it.hora12 || it.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(it.remitente_bcp || "")}
                <div style="font-size:10px; color:#64748b;">Cód/Glosa: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#b45309; text-align:right; white-space:nowrap;">
                S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#78350f;">
                ${escHtml(it.observacion || "SOBRANTE BANCO — Falta anotar en recepción")}
              </td>
            </tr>
          `;
        });
      }

      let filasSA = "";
      if (sueltosAnotados.length === 0) {
        filasSA = `<tr><td colspan="5" style="padding:14px; text-align:center; color:#065F46; font-size:11px; font-weight:700;">✓ Todos los pagos Yape anotados por recepción en ${escHtml(etiquetaPeriodo)} tienen su respaldo en el Banco/Drive.</td></tr>`;
      } else {
        sueltosAnotados.forEach((an, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          filasSA += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; font-weight:800; color:#64748b; font-size:10px; text-align:center;">
                ${idx + 1}
              </td>
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(an.fecha_operacion || "")} <b>${escHtml(an.hora12 || an.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#7f1d1d;">
                ${an.hab === "TIENDA" ? "🛒 Tienda" : "🏨 Hab. " + escHtml(an.hab)} — ${escHtml(an.huesped || "")}
                <div style="font-size:9.5px; color:#64748b; font-family:monospace;">ID: ${escHtml(an.id || "")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#b91c1c; text-align:right; white-space:nowrap;">
                S/ ${Number(an.monto || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#475569;">
                Glosa anotada: <b>${escHtml(an.glosa || "—")}</b> (${escHtml(an.concepto || "")})
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #FCD34D; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#92400E; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>⏳ 1A. SOBRANTES EN BANCO — Yapes en Reporte/Celular SIN anotar en Recepción (${sueltosRecibidos.length} en ${escHtml(etiquetaPeriodo)})</span>
            <span>Total: S/ ${totSueltosYapeMes.toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#FEF3C7; color:#78350F; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px; width:36px; text-align:center;">#</th>
                  <th style="padding:5px 8px;">Fecha / Hora Banco</th>
                  <th style="padding:5px 8px;">Remitente Yape</th>
                  <th style="padding:5px 8px; text-align:right;">Monto Banco</th>
                  <th style="padding:5px 8px;">Diagnóstico / Sugerencia de Habitación</th>
                </tr>
              </thead>
              <tbody>${filasSY}</tbody>
            </table>
          </div>
        </div>

        <div style="background:#FAF6F3; border:1px solid #FECACA; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#991B1B; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>⚠️ 1B. FALTANTES EN BANCO — Anotados por Recepción SIN respaldo en Reporte Yape (${sueltosAnotados.length} en ${escHtml(etiquetaPeriodo)})</span>
            <span>Total: S/ ${totSueltosAnotMes.toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#FEE2E2; color:#7F1D1D; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px; width:36px; text-align:center;">#</th>
                  <th style="padding:5px 8px;">Fecha / Hora Registro</th>
                  <th style="padding:5px 8px;">Habitación / Tienda / Huésped</th>
                  <th style="padding:5px 8px; text-align:right;">Monto Anotado</th>
                  <th style="padding:5px 8px;">Glosa / Concepto Anotado</th>
                </tr>
              </thead>
              <tbody>${filasSA}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // =====================================================================
    // PESTAÑA 3: 📥 EXTRAÍDOS DEL REPORTE DE TRANSACCIONES (CONTINUIDAD MENSUAL)
    // =====================================================================
    if (_tabAdminActiva === "extraidos") {
      let filasExt = "";
      if (extraidosReporte.length === 0) {
        filasExt = `<tr><td colspan="6" style="padding:18px; text-align:center; color:#64748b; font-size:11.5px;">No hay transacciones extraídas en ${escHtml(etiquetaPeriodo)}.</td></tr>`;
      } else {
        let diaAnt = "";
        extraidosReporte.forEach((it, idx) => {
          const fOp = String(it.fecha_operacion || "");
          if (fOp && fOp !== diaAnt) {
            diaAnt = fOp;
            filasExt += `
              <tr style="background:#EAE0D5; border-bottom:1px solid #D8C7B8;">
                <td colspan="6" style="padding:4px 8px; font-size:10px; font-weight:900; color:#2D2623;">
                  📅 ${escHtml(fOp)}
                </td>
              </tr>
            `;
          }
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const est = String(it.estado_auditoria || "SIN ANOTAR").toUpperCase();
          const esSal = est === "SALIDA";
          const tipoBadge = esSal
            ? `<span style="background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; font-size:9.5px; font-weight:800; padding:1px 5px; border-radius:4px;">📤 PAGASTE (Salida)</span>`
            : `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-size:9.5px; font-weight:800; padding:1px 5px; border-radius:4px;">📥 TE PAGÓ (Ingreso)</span>`;

          const estadoCruceBadge =
            est === "COMPROBADO"
              ? `<span style="background:#ECFDF5;color:#065F46;border:1px solid #A7F3D0;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">🔗 Enlazado</span>`
              : est === "EXCEDENTE"
              ? `<span style="background:#FFFBEB;color:#B45309;border:1px solid #FCD34D;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">⚠️ Diferencia</span>`
              : esSal
              ? `<span style="background:#F1F5F9;color:#475569;border:1px solid #CBD5E1;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">Salida</span>`
              : `<span style="background:#FFFBEB;color:#92400E;border:1px solid #FCD34D;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">⏳ Suelto</span>`;

          filasExt += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(fOp)} <b>${escHtml(it.hora12 || it.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; white-space:nowrap;">
                ${tipoBadge}
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(it.remitente_bcp || "")}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#475569;">
                ${escHtml(it.nro_operacion || "-")}
              </td>
              <td style="padding:6px 8px; font-weight:900; color:${esSal ? "#b91c1c" : "#0f172a"}; text-align:right; white-space:nowrap;">
                ${esSal ? "-" : ""}S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10px; color:#475569;">
                ${estadoCruceBadge}
                <span style="margin-left:4px;">${escHtml(it.observacion || "")}</span>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#2D2623; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>📥 Extraídos del Reporte de Transacciones (Continuidad Diaria en SQLite) — ${escHtml(etiquetaPeriodo)} (${extraidosReporte.length})</span>
            <span>Ingresos: S/ ${totBancoMes.toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#3A312D; color:#f8fafc; font-size:10px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px;">Fecha y Hora</th>
                  <th style="padding:5px 8px;">Tipo Transacción</th>
                  <th style="padding:5px 8px;">Origen / Destino Banco</th>
                  <th style="padding:5px 8px;">Mensaje / Cód.</th>
                  <th style="padding:5px 8px; text-align:right;">Monto Banco</th>
                  <th style="padding:5px 8px;">Estado en Conciliación</th>
                </tr>
              </thead>
              <tbody>${filasExt}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // =====================================================================
    // PESTAÑA 4: 📊 HOJA CONCILIACIÓN YAPE (REEMPLAZO DIRECTO DE `CONCILIACION PAGOS YAPE`)
    // =====================================================================
    if (_tabAdminActiva === "conciliacion") {
      let filasHoja = "";
      if (tablaConciliacion.length === 0) {
        filasHoja = `<tr><td colspan="8" style="padding:18px; text-align:center; color:#64748b; font-size:11.5px;">No hay registros de conciliación en ${escHtml(etiquetaPeriodo)}.</td></tr>`;
      } else {
        tablaConciliacion.forEach((r, idx) => {
          const tf = String(r.tipo_fila || "");
          let bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          let badgeEst = `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">${escHtml(r.estado_conciliacion)}</span>`;

          if (tf === "SOBRANTE_BANCO") {
            bg = "#FFFBEB";
            badgeEst = `<span style="background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">⏳ ${escHtml(r.estado_conciliacion)}</span>`;
          } else if (tf === "FALTANTE_BANCO") {
            bg = "#FEF2F2";
            badgeEst = `<span style="background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">⚠️ ${escHtml(r.estado_conciliacion)}</span>`;
          } else if (tf === "EXCEDENTE") {
            bg = "#FFFDF2";
            badgeEst = `<span style="background:#FEF3C7; color:#B45309; border:1px solid #FCD34D; font-size:9.5px; font-weight:800; padding:1.5px 6px; border-radius:4px;">⚠️ ${escHtml(r.estado_conciliacion)}</span>`;
          }

          filasHoja += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 7px; white-space:nowrap; font-weight:700; color:#334155; border-right:1px solid #E5DCD3;">
                ${escHtml(r.fecha_hora_banco || "—")}
              </td>
              <td style="padding:6px 7px; font-weight:700; color:#0f172a; border-right:1px solid #E5DCD3;">
                ${escHtml(r.origen_banco || "—")}
              </td>
              <td style="padding:6px 7px; font-weight:900; color:#047857; text-align:right; white-space:nowrap; border-right:2px solid #94A3B8;">
                ${Number(r.monto_banco || 0) > 0 ? "S/ " + Number(r.monto_banco).toFixed(2) : "—"}
              </td>
              <td style="padding:6px 7px; font-weight:900; color:#1e293b; text-align:right; white-space:nowrap; border-right:1px solid #E5DCD3;">
                ${Number(r.monto_anotado || 0) > 0 ? "S/ " + Number(r.monto_anotado).toFixed(2) : "—"}
              </td>
              <td style="padding:6px 7px; font-weight:700; color:#1e293b; border-right:1px solid #E5DCD3;">
                ${escHtml(r.huesped_anotado || "—")}
              </td>
              <td style="padding:6px 7px; font-family:monospace; font-size:10.5px; color:#334155; border-right:1px solid #E5DCD3;">
                ${escHtml(r.hora_anotada || "—")}
              </td>
              <td style="padding:6px 7px; border-right:1px solid #E5DCD3;">
                <div>${badgeEst}</div>
                <div style="font-size:9.5px; color:#64748b; margin-top:2px;">${escHtml(r.detalle_observacion || "")}</div>
              </td>
              <td style="padding:6px 7px; font-size:10px; color:#475569; white-space:nowrap;">
                <div style="font-family:monospace;">${escHtml(r.id_registro || "—")}</div>
                <div>${escHtml(r.fecha_registro || "")}</div>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#2D2623; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>📊 CONCILIACIÓN PAGOS YAPE EN SQLITE — ${escHtml(etiquetaPeriodo)} (${tablaConciliacion.length} filas)</span>
            <span>Vista Unificada (Banco vs Recepción)</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:10.5px; text-align:left;">
              <thead>
                <tr style="background:#3A312D; color:#f8fafc; font-size:9.5px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:6px 7px; border-right:1px solid #4E433E;">Fecha y Hora Banco</th>
                  <th style="padding:6px 7px; border-right:1px solid #4E433E;">Origen / Detalle Banco</th>
                  <th style="padding:6px 7px; text-align:right; border-right:2px solid #94A3B8;">Monto Banco</th>
                  <th style="padding:6px 7px; text-align:right; border-right:1px solid #4E433E;">Monto Recepción</th>
                  <th style="padding:6px 7px; border-right:1px solid #4E433E;">Habitación / Huésped / Tienda</th>
                  <th style="padding:6px 7px; border-right:1px solid #4E433E;">Hora / Glosa Anotada</th>
                  <th style="padding:6px 7px; border-right:1px solid #4E433E;">Estado de Conciliación</th>
                  <th style="padding:6px 7px;">ID / Fecha Registro</th>
                </tr>
              </thead>
              <tbody>${filasHoja}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // =====================================================================
    // PESTAÑA 5: 🏦 CUENTA MAELEN (CORREO BCP ****8023) & MENSAJERO WSP (+5 MIN)
    // =====================================================================
    if (_tabAdminActiva === "maelen") {
      const revisor830Log = Array.isArray(data.revisor830Log) ? data.revisor830Log : [];
      let filasRev830 = "";
      if (revisor830Log.length === 0) {
        filasRev830 = `<tr><td colspan="5" style="padding:14px; text-align:center; color:#5B21B6; font-size:11px;">Aún no hay ejecuciones registradas del Revisor 08:30 AM. Presiona <b>🧠 Revisor 8:30am</b> para ejecutar.</td></tr>`;
      } else {
        revisor830Log.slice(0, 25).forEach((rv, idx) => {
          const tipo = String(rv.tipo_accion || "");
          const esAuto = tipo.includes("CORRECCION");
          const esOk = tipo.includes("SIN_NOVEDADES");
          const badgeClr = esAuto
            ? "background:#DCFCE7; color:#166534; border:1px solid #86EFAC;"
            : esOk
              ? "background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE;"
              : "background:#FEF3C7; color:#92400E; border:1px solid #FCD34D;";
          filasRev830 += `
            <tr style="border-bottom:1px solid #EDE9FE; background:${idx % 2 === 0 ? "#fff" : "#F5F3FF"};">
              <td style="padding:6px 8px; white-space:nowrap; font-size:10px;">
                <b>${escHtml(rv.fecha_ejecucion || "")}</b>
                <div style="color:#64748b;">Mes: ${escHtml(rv.mes_evaluado || "")}</div>
              </td>
              <td style="padding:6px 8px;">
                <span style="${badgeClr} padding:2px 6px; border-radius:4px; font-size:9.5px; font-weight:800;">${escHtml(tipo)}</span>
              </td>
              <td style="padding:6px 8px; font-size:10px; font-weight:700;">
                ${rv.habitacion ? `Hab. ${escHtml(rv.habitacion)}` : "—"}
                ${rv.id_pago ? `<div style="font-size:9px; color:#64748b;">${escHtml(rv.id_pago)}</div>` : ""}
              </td>
              <td style="padding:6px 8px; font-size:10px; max-width:340px; white-space:normal; line-height:1.3;">${escHtml(rv.explicacion || "")}</td>
              <td style="padding:6px 8px; text-align:center; font-size:9.5px; font-weight:800;">
                ${escHtml(rv.estado_resolucion || "")}
                <div style="color:${Number(rv.wsp_enviado) === 1 ? "#15803D" : "#64748b"};">${Number(rv.wsp_enviado) === 1 ? "📲 WSP OK" : "Sin WSP"}</div>
              </td>
            </tr>
          `;
        });
      }

      let filasAlertasWsp = "";
      if (alertasWsp5Min.length === 0) {
        filasAlertasWsp = `<tr><td colspan="5" style="padding:14px; text-align:center; color:#065F46; font-size:11px; font-weight:700;">✓ Sin alertas WSP (+5 min) registradas aún en esta cola.</td></tr>`;
      } else {
        alertasWsp5Min.forEach((al, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const st = String(al.estado_envio || "EN_COLA");
          const badgeSt = st.includes("ENVIADO")
            ? `<span style="background:#DCFCE7; color:#166534; border:1px solid #86EFAC; font-size:9.5px; font-weight:800; padding:1px 6px; border-radius:4px;">✓ ${escHtml(st)}</span>`
            : `<span style="background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; font-size:9.5px; font-weight:800; padding:1px 6px; border-radius:4px;">📲 ${escHtml(st)}</span>`;
          filasAlertasWsp += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(al.fecha_operacion || "")} <b>${escHtml(al.hora_operacion || "")}</b>
                <div style="font-size:9.5px; color:#64748b;">Dest: ${escHtml(al.telefono_destino || "964139749")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(al.remitente_bcp || "")}
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#b45309; text-align:right; white-space:nowrap;">
                S/ ${Number(al.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10px; color:#334155; white-space:pre-wrap; max-width:340px;">
                ${escHtml(al.mensaje_wsp || "")}
              </td>
              <td style="padding:6px 8px; white-space:nowrap; text-align:center;">
                ${badgeSt}
                <div style="font-size:9.5px; color:#64748b; margin-top:2px;">${escHtml(al.creado_en || "")}</div>
              </td>
            </tr>
          `;
        });
      }

      let filasMaelen = "";
      if (transferenciasMaelen.length === 0) {
        filasMaelen = `<tr><td colspan="6" style="padding:16px; text-align:center; color:#64748b; font-size:11px;">No hay transferencias de Cuenta Maelen registradas en ${escHtml(etiquetaPeriodo)}. Presiona "✉️ Correo Maelen".</td></tr>`;
      } else {
        transferenciasMaelen.forEach((mv, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const tm = String(mv.tipo_movimiento || "");
          const esIng = tm.startsWith("INGRESO") || tm === "CORPORATIVO_MAELEN";
          const badgeTipo = esIng
            ? `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-size:9.5px; font-weight:800; padding:1px 5px; border-radius:4px;">📥 ${escHtml(tm)}</span>`
            : `<span style="background:#EFF6FF; color:#1D4ED8; border:1px solid #BFDBFE; font-size:9.5px; font-weight:800; padding:1px 5px; border-radius:4px;">📤 ${escHtml(tm)}</span>`;
          filasMaelen += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(mv.fecha_operacion || "")} <b>${escHtml(mv.hora12 || mv.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; white-space:nowrap;">${badgeTipo}</td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(mv.remitente_o_destino || "")}
                <div style="font-size:9.5px; color:#64748b;">${escHtml(mv.asunto || "")}</div>
              </td>
              <td style="padding:6px 8px; font-size:10px; color:#475569; font-family:monospace;">
                ${escHtml(mv.nro_operacion || "-")}
              </td>
              <td style="padding:6px 8px; font-weight:900; color:${esIng ? "#047857" : "#1D4ED8"}; text-align:right; white-space:nowrap;">
                S/ ${Number(mv.monto || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10px; color:#334155;">
                <b>${escHtml(mv.estado_cruce || "PENDIENTE")}</b>
                <div style="font-size:9.5px; color:#64748b;">${escHtml(mv.detalle_cruce || "")}</div>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #DDD6FE; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#5B21B6; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>🧠 Revisor Inteligente Yape (08:30 AM) — Autocorrecciones, Consultas WSP & Cero Omisiones</span>
            <span>${revisor830Log.length} registros</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:10.5px; text-align:left;">
              <thead>
                <tr style="background:#EDE9FE; color:#5B21B6; font-size:9.5px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px;">Ejecución</th>
                  <th style="padding:5px 8px;">Acción</th>
                  <th style="padding:5px 8px;">Hab / Pago</th>
                  <th style="padding:5px 8px;">Detalle / Criterio</th>
                  <th style="padding:5px 8px; text-align:center;">Estado & WSP</th>
                </tr>
              </thead>
              <tbody>${filasRev830}</tbody>
            </table>
          </div>
        </div>

        <div style="background:#FAF6F3; border:1px solid #86EFAC; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#166534; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>📲 Mensajero WhatsApp (+5 Minutos sin Anotar en Recepción) — Destino: 964139749</span>
            <span>${alertasWsp5Min.length} alertas registradas</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:10.5px; text-align:left;">
              <thead>
                <tr style="background:#DCFCE7; color:#166534; font-size:9.5px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px;">Día / Hora Yape</th>
                  <th style="padding:5px 8px;">Remitente</th>
                  <th style="padding:5px 8px; text-align:right;">Monto</th>
                  <th style="padding:5px 8px;">Mensaje WhatsApp</th>
                  <th style="padding:5px 8px; text-align:center;">Estado</th>
                </tr>
              </thead>
              <tbody>${filasAlertasWsp}</tbody>
            </table>
          </div>
        </div>

        <div style="background:#FAF6F3; border:1px solid #BFDBFE; border-radius:7px; overflow:hidden; flex-shrink:0;">
          <div style="background:#1E3A8A; color:#fff; padding:7px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>🏦 Cuenta Empresa Maelen Hogar (**** 8023) — Correo BCP & Transferencias Corporativas (${escHtml(etiquetaPeriodo)})</span>
            <span>${transferenciasMaelen.length} movimientos</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:10.5px; text-align:left;">
              <thead>
                <tr style="background:#DBEAFE; color:#1E3A8A; font-size:9.5px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px;">Fecha y Hora</th>
                  <th style="padding:5px 8px;">Tipo Movimiento</th>
                  <th style="padding:5px 8px;">Origen / Destino</th>
                  <th style="padding:5px 8px;">Nro. Operación</th>
                  <th style="padding:5px 8px; text-align:right;">Monto</th>
                  <th style="padding:5px 8px;">Cruce Recepción / Gastos</th>
                </tr>
              </thead>
              <tbody>${filasMaelen}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    cont.innerHTML = adminHtml;
  }

  window.abrirModalYapeComprobador = function () {
    const overlay = crearPanelDerechoYape();
    overlay.style.display = "block";
    if (!_mesSeleccionado) {
      _mesSeleccionado = obtenerMesDelPanelPrincipalMMYYYY();
    }
    actualizarEstadoVisualModoAdmin();
    if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
    cargarYapesDesdeServidor(_mesSeleccionado, false);
  };

  function actualizarBotonBarraSuperior(data) {
    let btn = document.getElementById("btnTopBarYapeComprobador");
    if (!btn) {
      const refBtn = document.getElementById("btnTabletS8") || document.getElementById("btnAbrirModalTablet");
      if (!refBtn) return;
      btn = document.createElement("button");
      btn.id = "btnTopBarYapeComprobador";
      btn.type = "button";
      btn.className =
        "px-2.5 py-1 text-xs font-black bg-white text-slate-800 hover:bg-amber-50 border-l border-[#D8C7B8] cursor-pointer transition-colors inline-flex items-center gap-1.5";
      if (refBtn && refBtn.parentElement) {
        refBtn.parentElement.insertBefore(btn, refBtn.nextSibling);
      }
    }
    btn.onclick = window.abrirModalYapeComprobador;

    const sinAnotar = Number((data && data.sinAnotarCount) || 0);
    btn.innerHTML = `
      <span>📱 Yapes</span>
      ${
        sinAnotar > 0
          ? `<span style="background:#FEF3C7;color:#92400E;border:1px solid #FCD34D;font-weight:900;font-size:10px;padding:0px 5px;border-radius:4px;" title="Yapes del mes por anotar">${sinAnotar}</span>`
          : `<span style="background:#ECFDF5;color:#065F46;border:1px solid #A7F3D0;font-weight:800;font-size:9.5px;padding:0px 4px;border-radius:4px;">✓</span>`
      }
    `;
  }

  async function cargarYapesDesdeServidor(mesOFiltro = "", forzarDrive = false) {
    try {
      const fConsultar = mesOFiltro || _mesSeleccionado || obtenerMesDelPanelPrincipalMMYYYY();
      const qs = new URLSearchParams({
        limite: "2500",
        fecha: fConsultar,
        _t: String(Date.now()),
      });
      if (forzarDrive) qs.set("forzar_drive", "1");

      const r = await fetch("/api/yape/recientes?" + qs.toString(), { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      if (!data || !data.ok) return;

      _ultimoResumen = data;
      actualizarBotonBarraSuperior(data);

      const pingActual = String(data.ultimoPingCelular || "");
      if (_ultimoPingConocido === null) {
        _ultimoPingConocido = pingActual;
      } else if (pingActual && pingActual !== _ultimoPingConocido) {
        _ultimoPingConocido = pingActual;
        sonarAvisoYape();
        window.mostrarCasillaConexionCelular(pingActual);
      }

      const items = (data.items || []).filter((x) => String(x.estado_auditoria || "").toUpperCase() !== "SALIDA");
      const maxId = Number(data.ultimoId || 0);

      if (_ultimoIdConocido === null) {
        _ultimoIdConocido = maxId;
        items.forEach((it) => {
          _estadosConocidos[it.id] = it.estado_auditoria;
        });
      } else {
        const nuevos = items.filter((it) => Number(it.id) > _ultimoIdConocido);
        if (nuevos.length > 0) {
          _ultimoIdConocido = maxId;
          sonarAvisoYape();
          nuevos.slice(0, 2).forEach((it) => {
            _estadosConocidos[it.id] = it.estado_auditoria;
            window.mostrarMensajeYapeRecibido(it);
          });
        } else {
          items.forEach((it) => {
            _estadosConocidos[it.id] = it.estado_auditoria;
          });
        }
      }

      const overlay = document.getElementById("yapeRightDrawerOverlay");
      if (overlay && overlay.style.display === "block") {
        renderizarPanelDerecho(data);
      }
    } catch (e) {}
  }

  setTimeout(() => cargarYapesDesdeServidor("", false), 800);
  setInterval(() => cargarYapesDesdeServidor("", false), 6000);
})();
