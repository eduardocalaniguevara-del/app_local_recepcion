async function verificarSaludInicial() {
  try {
    const r = await fetch('/api/salud').then(x => x.json());
    document.getElementById('badgeSalud').textContent = r.ok ? '🟢 SALUD OK (' + r.nodo + ')' : '🟠 REVISAR';
    document.getElementById('outSalud').textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    document.getElementById('outSalud').textContent = 'Servidor web en modo archivo local.';
  }
}
verificarSaludInicial();
