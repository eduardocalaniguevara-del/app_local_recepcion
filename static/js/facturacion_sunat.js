/**
 * Constructor Multi-Ítem IGV 10.5% (Ley MYPE Hotelería/Turismo) + Emisión SUNAT y Envío por WhatsApp
 * Integrado con C:\Hostal_Sistema (emitir_comprobante_sunat.py y brazo_wsp.py)
 */
(function () {
  const FACTOR_IGV_HOTEL = 1.105;

  window.inicializarItemsFacturacionModal = function (h, montoSolicitado, subtotalAloj, tarifaNoche) {
    const tbody = document.getElementById("tbodyItemsFacturacion");
    if (!tbody) return;
    tbody.innerHTML = "";
    const defaultDesc = `DIA DE ALOJAMIENTO DEL ${h.checkIn || "OCTUBRE"}, HAB N ${h.hab}`;
    const defaultPrecio = montoSolicitado > 0 ? montoSolicitado : subtotalAloj || tarifaNoche || 0;
    window.agregarFilaItemSunat(1, defaultDesc, defaultPrecio);
  };

  window.agregarFilaItemSunat = function (cant, desc, precioUnit) {
    const tbody = document.getElementById("tbodyItemsFacturacion");
    if (!tbody) return;
    cant = Math.max(1, Number(cant) || 1);
    desc = desc || "";
    precioUnit = Number(precioUnit) || 0;
    const valorUnit = (precioUnit / FACTOR_IGV_HOTEL).toFixed(4);
    const subtotal = (cant * precioUnit).toFixed(2);

    const tr = document.createElement("tr");
    tr.className = "border-b border-slate-100 last:border-b-0";
    tr.innerHTML = `
      <td class="py-1 pr-1">
        <input type="number" min="1" value="${cant}" oninput="recalcularItemsFacturacionModal()"
               class="it-cant w-12 border border-slate-300 rounded px-1 py-0.5 font-bold text-center text-slate-900 bg-white" />
      </td>
      <td class="py-1 px-1">
        <input type="text" value="${desc}" placeholder="DIA DE ALOJAMIENTO HAB N..."
               class="it-desc w-full border border-slate-300 rounded px-1.5 py-0.5 text-slate-800 uppercase font-medium bg-white" />
      </td>
      <td class="py-1 px-1">
        <input type="number" step="0.01" min="0" value="${precioUnit > 0 ? precioUnit : ""}" oninput="recalcularItemsFacturacionModal()"
               placeholder="0.00"
               class="it-precio w-20 border border-slate-300 rounded px-1 py-0.5 font-black text-slate-900 bg-white text-right" />
      </td>
      <td class="py-1 px-1">
        <input type="text" readonly value="${valorUnit}"
               class="it-valor-unit w-20 border border-slate-200 rounded px-1 py-0.5 font-mono text-[10px] text-slate-500 bg-slate-100 text-right" />
      </td>
      <td class="py-1 px-1 text-right font-black text-slate-900 font-mono it-subtotal">
        S/ ${subtotal}
      </td>
      <td class="py-1 pl-1 text-center">
        <button type="button" onclick="eliminarFilaItemSunat(this)"
                title="Eliminar este ítem"
                class="text-red-500 hover:text-red-700 font-black px-1 text-xs cursor-pointer">✕</button>
      </td>
    `;
    tbody.appendChild(tr);
    window.recalcularItemsFacturacionModal();
  };

  window.eliminarFilaItemSunat = function (btn) {
    const tbody = document.getElementById("tbodyItemsFacturacion");
    if (!tbody) return;
    if (tbody.querySelectorAll("tr").length <= 1) {
      alert("⚠️ Debe haber al menos un ítem para emitir el comprobante.");
      return;
    }
    btn.closest("tr").remove();
    window.recalcularItemsFacturacionModal();
  };

  window.recalcularItemsFacturacionModal = function () {
    const tbody = document.getElementById("tbodyItemsFacturacion");
    if (!tbody) return;
    const filas = tbody.querySelectorAll("tr");
    let totalGeneral = 0;

    filas.forEach((tr) => {
      const inpCant = tr.querySelector(".it-cant");
      const inpPrecio = tr.querySelector(".it-precio");
      const inpValor = tr.querySelector(".it-valor-unit");
      const cellSub = tr.querySelector(".it-subtotal");

      const cant = Math.max(1, parseInt(inpCant.value, 10) || 1);
      const precioUnit = parseFloat(inpPrecio.value) || 0;
      const valorUnit = (precioUnit / FACTOR_IGV_HOTEL).toFixed(4);
      const subtotal = cant * precioUnit;

      if (inpValor) inpValor.value = valorUnit;
      if (cellSub) cellSub.textContent = `S/ ${subtotal.toFixed(2)}`;
      totalGeneral += subtotal;
    });

    const lblTot = document.getElementById("lblTotalItemsSunat");
    if (lblTot) lblTot.textContent = totalGeneral.toFixed(2);
    const inpMontoModal = document.getElementById("modalFact_monto");
    if (inpMontoModal && totalGeneral > 0) {
      inpMontoModal.value = totalGeneral.toFixed(2);
    }
  };

  window.recolectarItemsFacturacionModal = function () {
    const tbody = document.getElementById("tbodyItemsFacturacion");
    if (!tbody) return [];
    const items = [];
    tbody.querySelectorAll("tr").forEach((tr) => {
      const cant = Math.max(1, parseInt(tr.querySelector(".it-cant")?.value, 10) || 1);
      const desc = (tr.querySelector(".it-desc")?.value || "").trim().toUpperCase();
      const precioUnit = parseFloat(tr.querySelector(".it-precio")?.value) || 0;
      const valorUnit = parseFloat((precioUnit / FACTOR_IGV_HOTEL).toFixed(4));
      if (desc && precioUnit > 0) {
        items.push({
          cantidad: cant,
          descripcion: desc,
          precio_unitario: precioUnit,
          valor_unitario: valorUnit,
        });
      }
    });
    return items;
  };

  window.ejecutarEmisionDesdeApp = async function (accion, habNum, forzarAuditoria = false) {
    const h = (window.habitacionesData || []).find((x) => String(x.hab) === String(habNum));
    if (!h) return;
    const f = typeof window.obtenerFacturacionEfectiva === "function" ? window.obtenerFacturacionEfectiva(h) : h.facturacion || {};

    const tipoSel = (document.getElementById("modalFact_tipo")?.value || (f.esFactura ? "Factura" : "Boleta")).toUpperCase();
    const esFact = tipoSel === "FACTURA";
    const ruc = (document.getElementById("modalFact_ruc")?.value || f.ruc || "").replace(/\D/g, "");
    const empresa = (document.getElementById("modalFact_empresa")?.value || f.empresa || "").trim().toUpperCase();
    const celular = (document.getElementById("modalFact_celular")?.value || f.celular || h.celular || "").replace(/\D/g, "");
    const items = window.recolectarItemsFacturacionModal();

    if (esFact) {
      if (ruc.length !== 11 || !/^(10|15|17|20)/.test(ruc)) {
        alert("⚠️ Para emitir FACTURA se requiere un RUC válido de 11 dígitos (10 o 20).");
        document.getElementById("modalFact_ruc")?.focus();
        return;
      }
      if (empresa.length < 3) {
        alert("⚠️ Ingrese la Razón Social de la empresa para la Factura.");
        document.getElementById("modalFact_empresa")?.focus();
        return;
      }
    }

    if (items.length === 0) {
      alert("⚠️ Verifique que todos los ítems tengan descripción y Precio Unitario mayor a S/ 0.00.");
      return;
    }

    if (accion === "EMITIR_Y_WSP" && !celular) {
      alert("⚠️ Ingrese el número de Celular / WhatsApp del huésped para poder enviarlo por WhatsApp.");
      document.getElementById("modalFact_celular")?.focus();
      return;
    }

    const banner = document.getElementById("estadoProcesoSunat");
    const btnEmitir = document.getElementById("btnEmitirSunatApp");
    const btnEmitirWsp = document.getElementById("btnEmitirWspApp");

    if (banner) {
      banner.className = "mt-1.5 p-2 rounded-lg bg-blue-50 border border-blue-300 text-blue-900 font-bold text-[11px] flex items-center gap-2";
      banner.innerHTML = `⏳ Procesando en SUNAT SEE-SOL (${esFact ? "Factura E001" : "Boleta EB01"} con IGV 10.5%)... Por favor espere ~25s.`;
    }
    if (btnEmitir) btnEmitir.disabled = true;
    if (btnEmitirWsp) btnEmitirWsp.disabled = true;

    try {
      const resp = await fetch("/api/sunat/orden", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accion: accion,
          hab: h.hab,
          estanciaUuid: h.estanciaUuid || (f && f.estanciaUuid) || "",
          id_estancia: f.filaNum || h.filaNum || 0,
          fila_calzado: f.filaCalzado || null,
          tipo: esFact ? "FACTURA" : "BOLETA",
          documento: esFact ? ruc : f.dni || h.dni || "",
          cliente: esFact ? empresa : h.huesped || "CLIENTES VARIOS",
          telefono: celular,
          items: items,
          forzar: forzarAuditoria,
        }),
      });
      const res = await resp.json();

      if (res.status === "CONFIRM_REQUIRED") {
        if (btnEmitir) btnEmitir.disabled = false;
        if (btnEmitirWsp) btnEmitirWsp.disabled = false;
        const autorizar = confirm(
          `🚨 AUDITORÍA PREVENTIVA DE MONTO (S/ ${Number(res.monto || 0).toFixed(2)})\n\n` +
            `${res.mensajeAuditoria}\n\n` +
            `¿Confirma que el monto es CORRECTO (ej. estadía corporativa o varios días) y desea autorizar la emisión?`
        );
        if (autorizar) {
          return window.ejecutarEmisionDesdeApp(accion, habNum, true);
        }
        if (banner) banner.className = "hidden";
        return;
      }

      if (res.status === "OK") {
        const nro = res.nro_comprobante || "Emitido";
        const inpNro = document.getElementById("modalFact_nro");
        const selEst = document.getElementById("modalFact_estado");
        if (inpNro) inpNro.value = nro;
        if (selEst) selEst.value = "Emitido";

        if (banner) {
          banner.className = "mt-1.5 p-2 rounded-lg bg-emerald-100 border border-emerald-400 text-emerald-950 font-black text-[11px]";
          banner.innerHTML =
            `✅ Comprobante <strong>${nro}</strong> emitido con éxito.` +
            (res.wsp_status === "ENVIADO" ? ` 📲 Enviado por WhatsApp al ${celular}.` : "");
        }
        if (typeof window.recargarDatos === "function") {
          window.recargarDatos();
        }
      } else {
        throw new Error(res.error || res.stderr || "No se pudo completar la emisión en SUNAT.");
      }
    } catch (err) {
      if (banner) {
        banner.className = "mt-1.5 p-2 rounded-lg bg-red-100 border border-red-300 text-red-900 font-bold text-[11px]";
        banner.textContent = `❌ Error: ${err.message || err}`;
      }
    } finally {
      if (btnEmitir) btnEmitir.disabled = false;
      if (btnEmitirWsp) btnEmitirWsp.disabled = false;
    }
  };

  window.reenviarComprobanteWspModal = async function (habNum) {
    const h = (window.habitacionesData || []).find((x) => String(x.hab) === String(habNum));
    if (!h) return;
    const f = typeof window.obtenerFacturacionEfectiva === "function" ? window.obtenerFacturacionEfectiva(h) : h.facturacion || {};
    const celular = (document.getElementById("modalFact_celular")?.value || f.celular || h.celular || "").replace(/\D/g, "");
    const nro = (document.getElementById("modalFact_nro")?.value || f.comprobantesStr || "").trim();

    if (!celular) {
      alert("⚠️ Ingrese el número de Celular / WhatsApp en la casilla 'Celular / WSP' antes de enviar.");
      document.getElementById("modalFact_celular")?.focus();
      return;
    }

    const banner = document.getElementById("estadoProcesoSunat");
    const btn = document.getElementById("btnReenviarWspApp");
    if (btn) btn.disabled = true;
    if (banner) {
      banner.className = "mt-1.5 p-2 rounded-lg bg-emerald-50 border border-emerald-300 text-emerald-900 font-bold text-[11px]";
      banner.textContent = `📲 Enviando comprobante ${nro} al WhatsApp ${celular}...`;
    }

    try {
      const resp = await fetch("/api/sunat/orden", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accion: "SOLO_WSP",
          hab: h.hab,
          telefono: celular,
          nro_comprobante: nro,
          documento: f.ruc || f.dni || h.dni || "",
          cliente: f.empresa || h.huesped || "",
        }),
      });
      const res = await resp.json();
      if (res.status === "OK") {
        if (banner) {
          banner.className = "mt-1.5 p-2 rounded-lg bg-emerald-100 border border-emerald-400 text-emerald-950 font-black text-[11px]";
          banner.textContent = `✅ Comprobante ${nro} enviado por WhatsApp al ${celular}.`;
        }
      } else {
        throw new Error(res.error || "No se pudo enviar por WhatsApp.");
      }
    } catch (err) {
      if (banner) {
        banner.className = "mt-1.5 p-2 rounded-lg bg-red-100 border border-red-300 text-red-900 font-bold text-[11px]";
        banner.textContent = `❌ Error WSP: ${err.message || err}`;
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  };
})();
