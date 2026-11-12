/**
 * ============================================================================
 * PANEL DE YAPES (CELULAR + DRIVE -> SQLITE) — 100% SOLO LECTURA
 * ============================================================================
 * Diseño integrado a la hoja de PanelDisponibilidad.html (#F7EFEA / #FAF6F3 / #2D2623).
 * Dividido en 2 modos:
 *   1) MODO SENCILLO (Recepcionista - por defecto):
 *      - Selector de fecha (◀ | DD/MM/YYYY | ▶) igual al panel principal para voltear al día anterior.
 *      - Sin montos totales ni auditoría compleja.
 *      - Muestra como máximo 3 Yapes sueltos recientes y la lista limpia de todos los Yapes recibidos del día.
 *   2) MODO ADMINISTRADOR (protegido con contraseña):
 *      - Al ingresar la clave del administrador (correo), muestra el detalle completo:
 *        Cruzados con lo anotado, Todos los sueltos en Yape, Sueltos anotados sin Yape, observaciones y totales.
 * ============================================================================
 */
(function () {
  if (window._yapeComprobadorIniciado) return;
  window._yapeComprobadorIniciado = true;

  let _ultimoIdConocido = null;
  let _estadosConocidos = {};
  let _ultimoPingConocido = null;
  let _ultimoResumen = null;
  let _fechaSeleccionada = ""; // 'DD/MM/YYYY'
  let _panelExpandido = false;
  let _busquedaTexto = "";

  // Estado de Modo Administrador
  let _modoAdminActivo = false;
  let _mostrarPromptClave = false;
  let _tabAdminActiva = "todo"; // 'todo' | 'cruzados' | 'sueltos' | 'recibidos'

  const _ADMIN_EMAIL_CLAVE = "eduardocalaniguevara@gmail.com";

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
  // UTILIDADES DE FECHA (DD/MM/YYYY)
  // =========================================================================
  function obtenerFechaHoyDMY() {
    const d = new Date();
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    return `${dd}/${mm}/${yyyy}`;
  }

  function obtenerFechaDelPanelPrincipalDMY() {
    const el = document.getElementById("lblFechaVisibleDMY");
    if (el && el.textContent && /\d{2}\/\d{2}\/\d{4}/.test(el.textContent.trim())) {
      return el.textContent.trim();
    }
    return obtenerFechaHoyDMY();
  }

  function dmyADate(dmy) {
    const p = String(dmy || "").split("/");
    if (p.length !== 3) return new Date();
    return new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0]), 12, 0, 0);
  }

  function dateADmy(d) {
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    return `${dd}/${mm}/${yyyy}`;
  }

  function sumarDiasDMY(dmyBase, deltaDias) {
    const dt = dmyADate(dmyBase || obtenerFechaHoyDMY());
    dt.setDate(dt.getDate() + Number(deltaDias || 0));
    return dateADmy(dt);
  }

  // =========================================================================
  // AVISOS DISCRETOS EN ESQUINA INFERIOR DERECHA (ACORDE A LA HOJA)
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
  // PANEL LATERAL DERECHO — DISEÑO ACORDE A LA HOJA (#F7EFEA / #FAF6F3 / #2D2623)
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
        "position:fixed; top:0; right:0; height:100vh; width:min(560px, 96vw); background:#F7EFEA; color:#1e293b; border-left:2px solid #D8C7B8; box-shadow:-8px 0 28px rgba(0,0,0,0.22); display:flex; flex-direction:column; z-index:99991; font-family:system-ui,-apple-system,sans-serif; transition:width 0.18s ease;";
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
                <span style="font-size:12.5px; font-weight:800; letter-spacing:-0.01em;">Yapes Recibidos</span>
                <span id="yapeBadgeModoActual" style="background:#433A36; color:#e2e8f0; font-size:9.5px; font-weight:700; padding:1px 6px; border-radius:4px;">
                  Recepción
                </span>
              </div>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:5px;">
            <button type="button" id="btnYapeToggleAdmin" style="background:#433A36; color:#f8fafc; border:1px solid #574b46; border-radius:5px; padding:3.5px 8px; font-size:10.5px; font-weight:700; cursor:pointer;" title="Desbloquear vista detallada de Administrador">
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

        <!-- Barra de Fecha (idéntica al selector de fecha del Panel Principal: ◀ | DD/MM/YYYY | ▶) + Buscador -->
        <div style="background:#FAF6F3; padding:7px 12px; display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; border-bottom:1px solid #DFD2C5; flex-shrink:0;">
          <div style="display:flex; align-items:center; gap:6px;">
            <!-- Selector de fecha igual al panel principal -->
            <div id="grupoFechaYapePanel" style="display:inline-flex; align-items:center; gap:3px; background:#16a34a; border:2px solid #15803d; padding:2px 5px; border-radius:7px;">
              <button type="button" id="btnYapeDiaAnterior" title="Voltear al día anterior"
                      style="padding:1px 6px; border-radius:4px; background:rgba(255,255,255,0.95); color:#0f172a; font-weight:900; font-size:11px; border:none; cursor:pointer;">
                ◀
              </button>
              <div style="position:relative; display:inline-flex; align-items:center; background:#fff; border-radius:4px; border:1px solid #D8C7B8; padding:1px 8px; cursor:pointer;"
                   onclick="const inp=document.getElementById('yapeInputFechaFiltro'); if(inp && inp.showPicker) inp.showPicker();"
                   title="Clic para elegir fecha">
                <span id="lblYapeFechaVisibleDMY" style="font-weight:900; color:#0f172a; font-size:11.5px; user-select:none;">--/--/----</span>
                <span style="margin-left:5px; font-size:10.5px; color:#64748b; user-select:none;">📆</span>
                <input type="date" id="yapeInputFechaFiltro" style="position:absolute; inset:0; opacity:0; width:100%; height:100%; cursor:pointer;" />
              </div>
              <button type="button" id="btnYapeDiaSiguiente" title="Día siguiente"
                      style="padding:1px 6px; border-radius:4px; background:rgba(255,255,255,0.95); color:#0f172a; font-weight:900; font-size:11px; border:none; cursor:pointer;">
                ▶
              </button>
            </div>

            <button type="button" id="btnYapeIrHoy" style="display:none; background:#fff; color:#1e293b; border:1px solid #D8C7B8; border-radius:5px; padding:3px 8px; font-size:10.5px; font-weight:800; cursor:pointer;">
              Ir a Hoy
            </button>
          </div>

          <div style="display:flex; align-items:center; gap:5px; margin-left:auto;">
            <input type="text" id="inpYapeBuscarTexto" placeholder="🔍 Buscar nombre o monto..."
                   style="background:#fff; color:#1e293b; border:1px solid #D8C7B8; border-radius:5px; padding:3px 7px; font-size:11px; width:145px;" />
            <button type="button" id="btnYapeRefrescarSimple" title="Actualizar lista"
                    style="background:#fff; color:#334155; border:1px solid #D8C7B8; border-radius:5px; padding:3px 7px; font-size:11px; font-weight:700; cursor:pointer;">
              🔄
            </button>
          </div>
        </div>

        <!-- Contenedor principal desplazable -->
        <div id="yapeContenidoPrincipal" style="flex:1; overflow-y:auto; padding:10px 12px; display:flex; flex-direction:column; gap:10px;"></div>
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
          // Salir de Modo Admin y volver a vista sencilla
          _modoAdminActivo = false;
          _mostrarPromptClave = false;
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

      // Navegación de fecha: ◀ Día anterior / ▶ Día siguiente
      panel.querySelector("#btnYapeDiaAnterior").onclick = function () {
        const base = _fechaSeleccionada && _fechaSeleccionada !== "TODOS" ? _fechaSeleccionada : obtenerFechaHoyDMY();
        _fechaSeleccionada = sumarDiasDMY(base, -1);
        cargarYapesDesdeServidor(_fechaSeleccionada, false);
      };

      panel.querySelector("#btnYapeDiaSiguiente").onclick = function () {
        const base = _fechaSeleccionada && _fechaSeleccionada !== "TODOS" ? _fechaSeleccionada : obtenerFechaHoyDMY();
        _fechaSeleccionada = sumarDiasDMY(base, 1);
        cargarYapesDesdeServidor(_fechaSeleccionada, false);
      };

      panel.querySelector("#btnYapeIrHoy").onclick = function () {
        _fechaSeleccionada = obtenerFechaHoyDMY();
        cargarYapesDesdeServidor(_fechaSeleccionada, false);
      };

      const inpFecha = panel.querySelector("#yapeInputFechaFiltro");
      inpFecha.addEventListener("change", () => {
        if (inpFecha.value) {
          const p = inpFecha.value.split("-");
          _fechaSeleccionada = `${p[2]}/${p[1]}/${p[0]}`;
          cargarYapesDesdeServidor(_fechaSeleccionada, false);
        }
      });

      const inpBuscar = panel.querySelector("#inpYapeBuscarTexto");
      inpBuscar.addEventListener("input", () => {
        _busquedaTexto = String(inpBuscar.value || "").trim().toLowerCase();
        if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
      });

      panel.querySelector("#btnYapeRefrescarSimple").onclick = function () {
        cargarYapesDesdeServidor(_fechaSeleccionada, false);
      };
    }
    return overlay;
  }

  function actualizarAnchoPanel() {
    const panel = document.getElementById("yapeRightDrawerPanel");
    if (!panel) return;
    if (_panelExpandido) {
      panel.style.width = "min(980px, 98vw)";
    } else {
      panel.style.width = _modoAdminActivo ? "min(760px, 96vw)" : "min(540px, 96vw)";
    }
  }

  function actualizarEstadoVisualModoAdmin() {
    const badge = document.getElementById("yapeBadgeModoActual");
    const btnAdmin = document.getElementById("btnYapeToggleAdmin");
    if (badge) {
      if (_modoAdminActivo) {
        badge.textContent = "🔓 Administrador";
        badge.style.background = "#b45309";
        badge.style.color = "#fff";
      } else {
        badge.textContent = "Recepción";
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
    actualizarAnchoPanel();
  }

  function cerrarPanelDerechoYape() {
    const overlay = document.getElementById("yapeRightDrawerOverlay");
    if (overlay) overlay.style.display = "none";
    // Por seguridad, al cerrar el panel se bloquea el modo Admin para que recepción siempre vea el modo sencillo
    if (_modoAdminActivo || _mostrarPromptClave) {
      _modoAdminActivo = false;
      _mostrarPromptClave = false;
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
      btnEl.textContent = "⏳ Extrayendo de Drive...";
    }
    await cargarYapesDesdeServidor(_fechaSeleccionada, true);
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "🔄 Extraer reporte de Drive";
    }
  };

  // =========================================================================
  // RENDERIZADO DEL PANEL (SENCILLO PARA RECEPCIÓN vs DETALLADO PARA ADMIN)
  // =========================================================================
  function renderizarPanelDerecho(data) {
    const overlay = document.getElementById("yapeRightDrawerOverlay");
    if (!overlay || !data) return;

    const hoyStr = obtenerFechaHoyDMY();
    const fechaMostrada = String(data.fechaMostrada || _fechaSeleccionada || hoyStr);

    // Actualizar selector visual de fecha (Verde si es Hoy, Naranja si es otra fecha, igual al Panel Principal)
    const lblFecha = overlay.querySelector("#lblYapeFechaVisibleDMY");
    if (lblFecha) {
      lblFecha.textContent = fechaMostrada === "TODOS" ? "Recientes" : fechaMostrada;
    }
    const grupoFecha = overlay.querySelector("#grupoFechaYapePanel");
    const btnIrHoy = overlay.querySelector("#btnYapeIrHoy");
    const esHoy = fechaMostrada === hoyStr;
    if (grupoFecha) {
      grupoFecha.style.backgroundColor = esHoy ? "#16a34a" : "#d97706";
      grupoFecha.style.borderColor = esHoy ? "#15803d" : "#b45309";
    }
    if (btnIrHoy) {
      btnIrHoy.style.display = esHoy ? "none" : "inline-block";
    }
    const inpFecha = overlay.querySelector("#yapeInputFechaFiltro");
    if (inpFecha && fechaMostrada && fechaMostrada !== "TODOS") {
      const p = fechaMostrada.split("/");
      if (p.length === 3) inpFecha.value = `${p[2]}-${p[1]}-${p[0]}`;
    }

    const filtro = _busquedaTexto;
    const filtrarItem = (it) => {
      if (!filtro) return true;
      const texto = `${it.remitente_bcp || ""} ${it.monto_bcp || ""} ${it.nro_operacion || ""} ${it.hora12 || ""} ${it.huesped_calzado || ""} ${it.hab_calzada || ""}`.toLowerCase();
      return texto.includes(filtro);
    };

    const recibidos = (data.recibidos || []).filter(filtrarItem);
    const cruzados = (data.cruzados || []).filter(filtrarItem);
    const sueltosRecibidos = (data.sueltosRecibidos || []).filter(filtrarItem);
    const sueltosAnotados = (data.sueltosAnotados || []).filter((an) => {
      if (!filtro) return true;
      const t = `${an.hab || ""} ${an.huesped || ""} ${an.monto || ""} ${an.glosa || ""}`.toLowerCase();
      return t.includes(filtro);
    });

    const cont = overlay.querySelector("#yapeContenidoPrincipal");
    if (!cont) return;

    // =======================================================================
    // VISTA 1: MODO SENCILLO (RECEPCIONISTA)
    // - Sin montos totales ni sueltos anotados complejos.
    // - Máximo 3 Yapes sueltos recientes arriba.
    // - Lista limpia de todos los Yapes recibidos en la fecha seleccionada.
    // =======================================================================
    if (!_modoAdminActivo) {
      const max3Sueltos = (data.sueltosRecibidos || []).slice(0, 3);
      let bloqueTresSueltosHtml = "";

      if (max3Sueltos.length > 0) {
        let itemsSueltosHtml = "";
        max3Sueltos.forEach((it) => {
          itemsSueltosHtml += `
            <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; padding:5px 8px; background:#fff; border:1px solid #FDE68A; border-radius:5px; font-size:11px;">
              <div style="display:flex; align-items:center; gap:6px; min-width:0;">
                <span style="font-weight:800; color:#92400e; white-space:nowrap;">${escHtml(it.hora12 || it.hora_operacion || "")}</span>
                <span style="font-weight:700; color:#1e293b; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escHtml(it.remitente_bcp || "")}</span>
              </div>
              <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
                <span style="font-size:10px; color:#64748b;">Cód: ${escHtml(it.nro_operacion || "-")}</span>
                <span style="font-weight:900; color:#b45309; font-size:11.5px;">S/ ${Number(it.monto_bcp || 0).toFixed(2)}</span>
              </div>
            </div>
          `;
        });

        bloqueTresSueltosHtml = `
          <div style="background:#FFFBEB; border:1px solid #FCD34D; border-radius:7px; padding:8px 10px;">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
              <span style="font-size:11px; font-weight:800; color:#92400e;">
                ⏳ Últimos Yapes sueltos por anotar (${max3Sueltos.length})
              </span>
              <span style="font-size:10px; color:#b45309; font-weight:600;">Recientes del día</span>
            </div>
            <div style="display:flex; flex-direction:column; gap:4px;">
              ${itemsSueltosHtml}
            </div>
          </div>
        `;
      } else {
        bloqueTresSueltosHtml = `
          <div style="background:#ECFDF5; border:1px solid #A7F3D0; border-radius:6px; padding:6px 10px; font-size:11px; color:#065F46; font-weight:700; display:flex; align-items:center; gap:6px;">
            <span>✓</span>
            <span>No hay Yapes sueltos pendientes en esta fecha.</span>
          </div>
        `;
      }

      // Tabla sencilla de todos los Yapes recibidos del día
      let filasRecibidosHtml = "";
      if (recibidos.length === 0) {
        filasRecibidosHtml = `
          <tr>
            <td colspan="4" style="padding:18px 10px; text-align:center; color:#64748b; font-size:11.5px;">
              No hay Yapes registrados en la fecha <b>${escHtml(fechaMostrada)}</b>.
            </td>
          </tr>
        `;
      } else {
        recibidos.forEach((it, idx) => {
          const est = String(it.estado_auditoria || "SIN ANOTAR").toUpperCase();
          const anotado = est === "COMPROBADO" || est === "EXCEDENTE";
          const bgRow = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const estadoBadge = anotado
            ? `<span style="background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; font-weight:800; font-size:9.5px; padding:1px 6px; border-radius:4px;">✓ Anotado</span>`
            : `<span style="background:#FFFBEB; color:#92400E; border:1px solid #FCD34D; font-weight:800; font-size:9.5px; padding:1px 6px; border-radius:4px;">Suelto</span>`;

          filasRecibidosHtml += `
            <tr style="background:${bgRow}; border-bottom:1px solid #E5DCD3;">
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
        ${bloqueTresSueltosHtml}

        <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
          <div style="background:#2D2623; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>📋 Yapes Recibidos del Día (${escHtml(fechaMostrada)})</span>
            <span style="color:#cbd5e1; font-size:10.5px; font-weight:700;">${recibidos.length} registros</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; text-align:left; font-size:11px;">
              <thead>
                <tr style="background:#3A312D; color:#f8fafc; font-size:10px; font-weight:800; text-transform:uppercase;">
                  <th style="padding:5px 8px; border-right:1px solid #4E433E; width:78px;">Hora</th>
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
    // VISTA 2: MODO ADMINISTRADOR (DESBLOQUEADO CON CLAVE)
    // - Muestra observaciones detalladas, Cruzados, Todos los Sueltos en Yape,
    //   Sueltos anotados por las chicas sin Yape, totales y botón de Drive.
    // =======================================================================
    const tot = data.totales || {};
    const totalSueltosCount = sueltosRecibidos.length + sueltosAnotados.length;

    const mkTabBtn = (id, label, count) => {
      const act = _tabAdminActiva === id;
      return `
        <button type="button" onclick="window.cambiarTabYapeAdmin('${id}')"
                style="background:${act ? "#2D2623" : "#fff"}; color:${act ? "#fff" : "#334155"}; border:1px solid #D8C7B8; border-radius:5px; padding:4px 9px; font-size:10.5px; font-weight:800; cursor:pointer; white-space:nowrap;">
          ${label} (${count})
        </button>
      `;
    };

    let adminHtml = `
      <!-- Barra Admin: Drive + Totales discretos -->
      <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; padding:8px 10px; display:flex; flex-direction:column; gap:8px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
          <div style="font-size:10.5px; color:#475569;">
            <b>📁 Reporte Drive:</b> ${escHtml(data.archivoDrive || "Sincronizado en SQLite")}
          </div>
          <div style="display:flex; align-items:center; gap:5px;">
            <button type="button" onclick="window._yapeVerTodosDiasAdmin()" style="background:#fff; color:#334155; border:1px solid #D8C7B8; border-radius:5px; padding:3.5px 8px; font-size:10.5px; font-weight:700; cursor:pointer;">
              📅 Ver últimos días
            </button>
            <button type="button" onclick="window.forzarExtraccionDriveAdmin(this)" style="background:#2D2623; color:#fff; border:none; border-radius:5px; padding:4px 9px; font-size:10.5px; font-weight:800; cursor:pointer;">
              🔄 Extraer reporte de Drive
            </button>
          </div>
        </div>

        <!-- 4 tarjetas compactas acorde a la hoja -->
        <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:6px;">
          <div onclick="window.cambiarTabYapeAdmin('recibidos')" style="cursor:pointer; background:#fff; border:1px solid #D8C7B8; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#64748b; font-weight:800;">RECIBIDOS (${recibidos.length})</div>
            <div style="font-size:13px; font-weight:900; color:#0f172a;">S/ ${Number(tot.totalBanco || 0).toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('cruzados')" style="cursor:pointer; background:#ECFDF5; border:1px solid #A7F3D0; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#065F46; font-weight:800;">CRUZADOS (${cruzados.length})</div>
            <div style="font-size:13px; font-weight:900; color:#047857;">S/ ${Number((tot.totalComprobado || 0) + (tot.totalExcedente || 0)).toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('sueltos')" style="cursor:pointer; background:#FFFBEB; border:1px solid #FCD34D; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#92400E; font-weight:800;">SUELTOS YAPE (${sueltosRecibidos.length})</div>
            <div style="font-size:13px; font-weight:900; color:#B45309;">S/ ${Number(tot.totalSinAnotar || 0).toFixed(2)}</div>
          </div>
          <div onclick="window.cambiarTabYapeAdmin('sueltos')" style="cursor:pointer; background:#FEF2F2; border:1px solid #FECACA; border-radius:6px; padding:6px 8px;">
            <div style="font-size:9.5px; color:#991B1B; font-weight:800;">ANOTADOS SIN YAPE (${sueltosAnotados.length})</div>
            <div style="font-size:13px; font-weight:900; color:#B91C1C;">S/ ${Number(tot.totalSueltosAnotados || 0).toFixed(2)}</div>
          </div>
        </div>

        <!-- Pestañas Admin -->
        <div style="display:flex; gap:5px; overflow-x:auto; padding-top:2px;">
          ${mkTabBtn("todo", "📋 Todo", recibidos.length + sueltosAnotados.length)}
          ${mkTabBtn("cruzados", "✓ Cruzados", cruzados.length)}
          ${mkTabBtn("sueltos", "⚠️ Sueltos", totalSueltosCount)}
          ${mkTabBtn("recibidos", "📱 Recibidos", recibidos.length)}
        </div>
      </div>
    `;

    // BLOQUE ADMIN 1: SUELTOS (Yapes sin anotar + Anotados sin Yape)
    if (_tabAdminActiva === "todo" || _tabAdminActiva === "sueltos") {
      let filasSY = "";
      if (sueltosRecibidos.length === 0) {
        filasSY = `<tr><td colspan="4" style="padding:10px; text-align:center; color:#64748b; font-size:11px;">✓ Ningún Yape recibido está suelto sin anotar.</td></tr>`;
      } else {
        sueltosRecibidos.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          filasSY += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(it.fecha_operacion || "")} <b>${escHtml(it.hora12 || it.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(it.remitente_bcp || "")}
                <div style="font-size:10px; color:#64748b;">Cód: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#b45309; text-align:right; white-space:nowrap;">
                S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#78350f;">
                ${escHtml(it.observacion || "Sin anotar en recepción")}
              </td>
            </tr>
          `;
        });
      }

      let filasSA = "";
      if (sueltosAnotados.length === 0) {
        filasSA = `<tr><td colspan="4" style="padding:10px; text-align:center; color:#64748b; font-size:11px;">✓ Todos los pagos Yape anotados por recepción tienen respaldo en Celular/Drive.</td></tr>`;
      } else {
        sueltosAnotados.forEach((an, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          filasSA += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(an.fecha_operacion || "")} <b>${escHtml(an.hora12 || an.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#7f1d1d;">
                ${an.hab === "TIENDA" ? "🛒 Tienda" : "🏨 Hab. " + escHtml(an.hab)} — ${escHtml(an.huesped || "")}
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#b91c1c; text-align:right; white-space:nowrap;">
                S/ ${Number(an.monto || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#475569;">
                Glosa: <b>${escHtml(an.glosa || "—")}</b> (${escHtml(an.concepto || "")})
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #FCD34D; border-radius:7px; overflow:hidden;">
          <div style="background:#92400E; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>⏳ 1A. Yapes Sueltos en Celular/Drive (Falta anotar en recepción) (${sueltosRecibidos.length})</span>
            <span>S/ ${Number(tot.totalSinAnotar || 0).toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#FEF3C7; color:#78350F; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px;">Fecha / Hora</th>
                  <th style="padding:5px 8px;">Remitente Yape</th>
                  <th style="padding:5px 8px; text-align:right;">Monto</th>
                  <th style="padding:5px 8px;">Observación / Sugerencia</th>
                </tr>
              </thead>
              <tbody>${filasSY}</tbody>
            </table>
          </div>
        </div>

        <div style="background:#FAF6F3; border:1px solid #FECACA; border-radius:7px; overflow:hidden;">
          <div style="background:#991B1B; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>⚠️ 1B. Anotados por Recepción SIN Yape en Celular/Drive (${sueltosAnotados.length})</span>
            <span>S/ ${Number(tot.totalSueltosAnotados || 0).toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#FEE2E2; color:#7F1D1D; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px;">Fecha / Hora</th>
                  <th style="padding:5px 8px;">Habitación / Tienda</th>
                  <th style="padding:5px 8px; text-align:right;">Monto Anotado</th>
                  <th style="padding:5px 8px;">Glosa / Concepto</th>
                </tr>
              </thead>
              <tbody>${filasSA}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // BLOQUE ADMIN 2: CRUZADOS CON LOS ANOTADOS
    if (_tabAdminActiva === "todo" || _tabAdminActiva === "cruzados") {
      let filasCruz = "";
      if (cruzados.length === 0) {
        filasCruz = `<tr><td colspan="4" style="padding:10px; text-align:center; color:#64748b; font-size:11px;">No hay Yapes cruzados en esta fecha.</td></tr>`;
      } else {
        cruzados.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const esExc = String(it.estado_auditoria || "").toUpperCase() === "EXCEDENTE";
          filasCruz += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(it.fecha_operacion || "")} <b>${escHtml(it.hora12 || it.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(it.remitente_bcp || "")}
                <div style="font-size:10px; color:#64748b;">Cód: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:${esExc ? "#b45309" : "#047857"}; text-align:right; white-space:nowrap;">
                S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#1e293b;">
                <div style="font-weight:800; color:#065f46;">
                  ${it.hab_calzada ? (it.hab_calzada === "TIENDA" ? "🛒 Tienda" : "🏨 Hab. " + escHtml(it.hab_calzada)) : ""} — ${escHtml(it.huesped_calzado || "")}
                </div>
                <div style="color:#475569; font-size:10px;">${escHtml(it.observacion || "")}</div>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #A7F3D0; border-radius:7px; overflow:hidden;">
          <div style="background:#065F46; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>✓ 2. Cruzados con lo Anotado por las Chicas (${cruzados.length})</span>
            <span>S/ ${Number((tot.totalComprobado || 0) + (tot.totalExcedente || 0)).toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#D1FAE5; color:#065F46; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px;">Fecha / Hora</th>
                  <th style="padding:5px 8px;">Remitente Yape</th>
                  <th style="padding:5px 8px; text-align:right;">Monto</th>
                  <th style="padding:5px 8px;">Cruzado con Anotación / Observación</th>
                </tr>
              </thead>
              <tbody>${filasCruz}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // BLOQUE ADMIN 3: TODOS LOS RECIBIDOS
    if (_tabAdminActiva === "todo" || _tabAdminActiva === "recibidos") {
      let filasRec = "";
      if (recibidos.length === 0) {
        filasRec = `<tr><td colspan="4" style="padding:10px; text-align:center; color:#64748b; font-size:11px;">No hay Yapes recibidos en esta fecha.</td></tr>`;
      } else {
        recibidos.forEach((it, idx) => {
          const bg = idx % 2 === 0 ? "#FAF6F3" : "#FFFFFF";
          const est = String(it.estado_auditoria || "SIN ANOTAR").toUpperCase();
          const badge =
            est === "COMPROBADO"
              ? `<span style="background:#ECFDF5;color:#065F46;border:1px solid #A7F3D0;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">✓ Cruzado</span>`
              : est === "EXCEDENTE"
              ? `<span style="background:#FFFBEB;color:#B45309;border:1px solid #FCD34D;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">⚠️ Diferencia</span>`
              : `<span style="background:#FFFBEB;color:#92400E;border:1px solid #FCD34D;font-size:9.5px;font-weight:800;padding:1px 5px;border-radius:4px;">⏳ Suelto</span>`;
          filasRec += `
            <tr style="background:${bg}; border-bottom:1px solid #E5DCD3;">
              <td style="padding:6px 8px; white-space:nowrap; font-weight:700; color:#334155;">
                ${escHtml(it.fecha_operacion || "")} <b>${escHtml(it.hora12 || it.hora_operacion || "")}</b>
              </td>
              <td style="padding:6px 8px; font-weight:700; color:#0f172a;">
                ${escHtml(it.remitente_bcp || "")}
                <div style="font-size:10px; color:#64748b;">Cód: ${escHtml(it.nro_operacion || "-")}</div>
              </td>
              <td style="padding:6px 8px; font-weight:900; color:#0f172a; text-align:right; white-space:nowrap;">
                S/ ${Number(it.monto_bcp || 0).toFixed(2)}
              </td>
              <td style="padding:6px 8px; font-size:10.5px; color:#475569;">
                ${badge} <span style="margin-left:4px;">${escHtml(it.observacion || "")}</span>
              </td>
            </tr>
          `;
        });
      }

      adminHtml += `
        <div style="background:#FAF6F3; border:1px solid #DFD2C5; border-radius:7px; overflow:hidden;">
          <div style="background:#2D2623; color:#fff; padding:6px 10px; display:flex; align-items:center; justify-content:space-between; font-size:11px; font-weight:800;">
            <span>📱 3. Todos los Yapes Recibidos (${recibidos.length})</span>
            <span>Total: S/ ${Number(tot.totalBanco || 0).toFixed(2)}</span>
          </div>
          <div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:11px; text-align:left;">
              <thead>
                <tr style="background:#3A312D; color:#f8fafc; font-size:10px; font-weight:800;">
                  <th style="padding:5px 8px;">Fecha / Hora</th>
                  <th style="padding:5px 8px;">Remitente</th>
                  <th style="padding:5px 8px; text-align:right;">Monto</th>
                  <th style="padding:5px 8px;">Estado / Detalle</th>
                </tr>
              </thead>
              <tbody>${filasRec}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    cont.innerHTML = adminHtml;
  }

  window._yapeVerTodosDiasAdmin = function () {
    _fechaSeleccionada = "TODOS";
    cargarYapesDesdeServidor("TODOS", false);
  };

  window.abrirModalYapeComprobador = function () {
    const overlay = crearPanelDerechoYape();
    overlay.style.display = "block";
    if (!_fechaSeleccionada || _fechaSeleccionada === "TODOS") {
      _fechaSeleccionada = obtenerFechaDelPanelPrincipalDMY();
    }
    actualizarEstadoVisualModoAdmin();
    if (_ultimoResumen) renderizarPanelDerecho(_ultimoResumen);
    cargarYapesDesdeServidor(_fechaSeleccionada, false);
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
    const mostrarCount = Math.min(sinAnotar, 3);
    btn.innerHTML = `
      <span>📱 Yapes</span>
      ${
        mostrarCount > 0
          ? `<span style="background:#FEF3C7;color:#92400E;border:1px solid #FCD34D;font-weight:900;font-size:10px;padding:0px 5px;border-radius:4px;" title="Yapes recientes por anotar">${mostrarCount}</span>`
          : `<span style="background:#ECFDF5;color:#065F46;border:1px solid #A7F3D0;font-weight:800;font-size:9.5px;padding:0px 4px;border-radius:4px;">✓</span>`
      }
    `;
  }

  async function cargarYapesDesdeServidor(fechaFiltro = "", forzarDrive = false) {
    try {
      const fConsultar = fechaFiltro || _fechaSeleccionada || obtenerFechaDelPanelPrincipalDMY();
      const qs = new URLSearchParams({
        limite: "150",
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
  setInterval(() => cargarYapesDesdeServidor("", false), 5000);
})();
