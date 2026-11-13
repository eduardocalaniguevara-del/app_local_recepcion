#!/usr/bin/env node
/**
 * Sonda Maestra Laptop 2: ejecuta las sondas y publica el JSON en Supabase
 * estado_sistema (clave = 'arnes_salud_LAPTOP_2_RECEPCION') usando supabase_config.json.
 * Solo publica desde LAPTOP-JAPIGJT2 (o con ARNES_PUBLISH=1) para que Laptop 1 no pise la clave.
 */
const { spawnSync } = require('node:child_process');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

const PROBES_DIR = __dirname;
const SUB_PROBES = ['sqlite_probe.js', 'supabase_probe.js', 'sunat_probe.js', 'whatsapp_probe.js', 'webapp_probe.js'];
const CLAVE = 'arnes_salud_LAPTOP_2_RECEPCION';
const ROOT = path.resolve(__dirname, '..', '..', '..');

function runProbe(file) {
  const full = path.join(PROBES_DIR, file);
  if (!fs.existsSync(full)) return { status: 'WARN', probe: file, error: 'File not found' };
  const t0 = Date.now();
  const res = spawnSync(process.execPath, [full], { encoding: 'utf-8', timeout: 8000 });
  const latency = Date.now() - t0;
  if (res.error || res.status !== 0) return { status: 'FAIL', probe: file, latency_ms: latency, error: res.error ? res.error.message : (res.stderr || 'Non-zero exit code') };
  try { const d = JSON.parse(res.stdout.trim()); d.latency_ms = latency; return d; }
  catch (_) { return { status: 'WARN', probe: file, latency_ms: latency, raw_output: res.stdout.slice(0, 300) }; }
}

const DEFAULT_CFG = {
  project_name: 'hostal-ovalo',
  project_id: 'fmlfqupwscymosvwnnzx',
  supabase_url: 'https://fmlfqupwscymosvwnnzx.supabase.co',
  supabase_anon_key: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0.pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g',
  hostal_sync_token: 'ovalo_master_sync_2026_k9x'
};

function loadConfig() {
  const c = [path.join(ROOT, 'supabase_config.json'), path.join(ROOT, '..', 'supabase_config.json'), 'C:\\Hostal_Sistema\\leemeantrigravityrecepcion\\supabase_config.json', 'C:\\Users\\ovalo mariategui\\Downloads\\leemeantrigravityrecepcion\\supabase_config.json', 'C:\\Users\\josec\\Downloads\\leemeantrigravityrecepcion\\supabase_config.json'].find(p => fs.existsSync(p));
  if (!c) return DEFAULT_CFG;
  try { return Object.assign({}, DEFAULT_CFG, JSON.parse(fs.readFileSync(c, 'utf-8'))); } catch (_) { return DEFAULT_CFG; }
}

function publish(payload) {
  return new Promise((resolve) => {
    const host = os.hostname().toUpperCase();
    if (host !== 'LAPTOP-JAPIGJT2' && process.env.ARNES_PUBLISH !== '1') return resolve({ published: false, reason: 'No es Laptop 2 (host=' + host + '); use ARNES_PUBLISH=1 para forzar' });
    const cfg = loadConfig();
    if (!cfg) return resolve({ published: false, reason: 'supabase_config.json missing' });
    const body = JSON.stringify([{ clave: CLAVE, valor: JSON.stringify(payload), updated_at: new Date().toISOString() }]);
    const u = new URL(cfg.supabase_url + '/rest/v1/estado_sistema?on_conflict=clave');
    const key = cfg.supabase_anon_key || cfg.supabase_publishable_key || '';
    const req = https.request({ method: 'POST', hostname: u.hostname, path: u.pathname + u.search, timeout: 6000, headers: {
      'apikey': key, 'Authorization': 'Bearer ' + key, 'x-hostal-sync-token': cfg.hostal_sync_token || '',
      'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates,return=minimal', 'Content-Length': Buffer.byteLength(body) } }, (res) => {
      res.resume();
      resolve({ published: res.statusCode >= 200 && res.statusCode < 300, http_code: res.statusCode, clave: CLAVE });
    });
    req.on('error', (e) => resolve({ published: false, error: e.message }));
    req.on('timeout', () => { req.destroy(); resolve({ published: false, error: 'timeout' }); });
    req.write(body); req.end();
  });
}

async function main() {
  const t0 = Date.now();
  const results = {}; let fail = 0, warn = 0;
  for (const p of SUB_PROBES) { const r = runProbe(p); results[p.replace('.js', '')] = r; if (r.status === 'FAIL') fail++; else if (r.status === 'WARN') warn++; }
  const payload = {
    status: fail ? 'FAIL' : (warn ? 'WARN' : 'OK'), probe: 'operational_health_probe.js', node: 'LAPTOP_2_RECEPCION',
    host: os.hostname(), timestamp: new Date().toISOString(), total_latency_ms: Date.now() - t0,
    summary: { total_probes: SUB_PROBES.length, passed: SUB_PROBES.length - fail - warn, warnings: warn, failures: fail },
    probes: results
  };
  payload.supabase_publish = await publish(payload);
  console.log(JSON.stringify(payload, null, 2));
  process.exit(0);
}
main();
