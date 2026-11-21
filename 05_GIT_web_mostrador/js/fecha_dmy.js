/**
 * Fechas siempre en formato DD/MM/AAAA (25/10/2026), sin depender del idioma de Chrome.
 * Los <input type="date"> conservan su valor interno ISO (AAAA-MM-DD) para cálculos y para el servidor,
 * pero el texto visible se reemplaza por una etiqueta DD/MM/AAAA. Al hacer clic se abre el calendario.
 */
(function () {
  if (window.__dmyFechaInit) return;
  window.__dmyFechaInit = true;

  var estilo = document.createElement('style');
  estilo.setAttribute('data-dmy', '1');
  estilo.textContent =
    '.dmy-wrap{position:relative;display:block;}' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit,' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit-fields-wrapper,' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit-text,' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit-month-field,' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit-day-field,' +
    '.dmy-wrap input[type="date"]::-webkit-datetime-edit-year-field{color:transparent !important;background:transparent !important;}' +
    '.dmy-wrap input[type="date"]{cursor:pointer;}' +
    '.dmy-label{position:absolute;pointer-events:none;display:flex;align-items:center;white-space:nowrap;overflow:hidden;line-height:1;}';
  (document.head || document.documentElement).appendChild(estilo);

  function aDMY(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return m ? (m[3] + '/' + m[2] + '/' + m[1]) : '';
  }

  function envolver(inp) {
    if (inp.__dmy || inp.id === 'inputFecha' || (inp.closest && inp.closest('[data-dmy-skip]'))) return;
    inp.__dmy = true;

    var wrap = document.createElement('div');
    wrap.className = 'dmy-wrap';
    var lab = document.createElement('span');
    lab.className = 'dmy-label';
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    wrap.appendChild(lab);

    function actualizar() {
      var cs = window.getComputedStyle(inp);
      lab.style.left = ((parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.paddingLeft) || 0) + 1) + 'px';
      lab.style.top = cs.borderTopWidth;
      lab.style.bottom = cs.borderBottomWidth;
      lab.style.right = ((parseFloat(cs.borderRightWidth) || 0) + 30) + 'px';
      lab.style.fontFamily = cs.fontFamily;
      lab.style.fontSize = cs.fontSize;
      lab.style.fontWeight = cs.fontWeight;
      var txt = aDMY(inp.value);
      lab.textContent = txt || 'dd/mm/aaaa';
      lab.style.color = txt ? cs.color : '#94a3b8';
    }

    // Cambios programáticos (inp.value = '...') no disparan eventos: se intercepta el setter
    var desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(inp, 'value', {
      configurable: true,
      get: function () { return desc.get.call(this); },
      set: function (v) { desc.set.call(this, v); actualizar(); }
    });

    inp.addEventListener('input', actualizar);
    inp.addEventListener('change', actualizar);
    inp.addEventListener('focus', actualizar);
    if (inp.form) inp.form.addEventListener('reset', function () { setTimeout(actualizar, 0); });

    function abrir() { try { if (inp.showPicker) inp.showPicker(); } catch (e) {} }
    inp.addEventListener('click', abrir);
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Tab' || e.key === 'Escape' || e.key === 'Shift') return;
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') abrir();
      // Evita teclear segmentos en orden MM/DD/AAAA del navegador: la fecha se elige en el calendario
      e.preventDefault();
    });

    actualizar();
    // Si el campo estaba oculto al envolver (modales), se recalcula al mostrarse
    setTimeout(actualizar, 300);
  }

  function iniciar() {
    var lista = document.querySelectorAll('input[type="date"]');
    for (var i = 0; i < lista.length; i++) envolver(lista[i]);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
