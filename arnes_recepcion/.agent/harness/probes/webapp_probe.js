#!/usr/bin/env node
/** Sonda WebApp: 8080 Operativa, 8082 Prueba, 8788 Visor. */
const http = require('node:http');
function ping(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/', timeout: 1500 }, (res) => { res.resume(); resolve({ port, active: true, code: res.statusCode }); });
    req.on('error', () => resolve({ port, active: false }));
    req.on('timeout', () => { req.destroy(); resolve({ port, active: false }); });
  });
}
(async () => {
  const [operativa, prueba, visor] = await Promise.all([ping(8080), ping(8082), ping(8788)]);
  const status = (operativa.active && prueba.active && visor.active) ? 'OK' : 'WARN';
  console.log(JSON.stringify({ status, probe: 'webapp_probe.js', timestamp: new Date().toISOString(), details: { webapp_operativa_8080: operativa, webapp_prueba_8082: prueba, visor_8788: visor } }, null, 2));
  process.exit(0);
})();