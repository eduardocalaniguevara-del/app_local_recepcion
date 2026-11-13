#!/usr/bin/env node
/**
 * Sonda de Conectividad y Token de Supabase (hostal-ovalo)
 * Harness² Probe Sensor
 */
const https = require('node:https');
const path = require('node:path');
const fs = require('node:fs');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const DEFAULT_CFG = {
  project_name: 'hostal-ovalo',
  project_id: 'fmlfqupwscymosvwnnzx',
  supabase_url: 'https://fmlfqupwscymosvwnnzx.supabase.co',
  supabase_anon_key: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0.pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g',
  hostal_sync_token: 'ovalo_master_sync_2026_k9x'
};
const CONFIG_PATH = [path.join(ROOT, 'supabase_config.json'), path.join(ROOT, '..', 'supabase_config.json'), 'C:\\\\Hostal_Sistema\\\\leemeantrigravityrecepcion\\\\supabase_config.json', 'C:\\\\Users\\\\ovalo mariategui\\\\Downloads\\\\leemeantrigravityrecepcion\\\\supabase_config.json', 'C:\\\\Users\\\\josec\\\\Downloads\\\\leemeantrigravityrecepcion\\\\supabase_config.json'].find(p => fs.existsSync(p));

function main() {
  let cfg = Object.assign({}, DEFAULT_CFG);
  if (CONFIG_PATH && fs.existsSync(CONFIG_PATH)) {
    try {
      cfg = Object.assign({}, DEFAULT_CFG, JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8')));
    } catch (_) {}
  }

  const start = Date.now();
  const urlStr = `${cfg.supabase_url}/rest/v1/wsp_recepcion_chats?select=count&limit=1`;
  
  let req;
  try {
    req = https.get(urlStr, {
      headers: {
        'apikey': cfg.supabase_anon_key || '',
        'Authorization': `Bearer ${cfg.supabase_anon_key || ''}`,
        'x-hostal-sync-token': cfg.hostal_sync_token || ''
      },
      timeout: 5000
    }, (res) => {
      const latency = Date.now() - start;
      const ok = res.statusCode >= 200 && res.statusCode < 300;
      console.log(JSON.stringify({
        status: ok ? 'OK' : 'WARN',
        probe: 'supabase_probe.js',
        timestamp: new Date().toISOString(),
        latency_ms: latency,
        http_code: res.statusCode,
        project_id: cfg.project_id || 'unknown',
        endpoint_reachable: true
      }, null, 2));
      process.exit(0);
    });

    req.on('error', (err) => {
      console.log(JSON.stringify({
        status: 'WARN',
        probe: 'supabase_probe.js',
        timestamp: new Date().toISOString(),
        endpoint_reachable: false,
        offline_safe: true,
        error: err.message
      }, null, 2));
      process.exit(0);
    });

    req.on('timeout', () => {
      req.destroy();
      console.log(JSON.stringify({
        status: 'WARN',
        probe: 'supabase_probe.js',
        timestamp: new Date().toISOString(),
        endpoint_reachable: false,
        timeout: true
      }, null, 2));
      process.exit(0);
    });
  } catch (err) {
    console.log(JSON.stringify({
      status: 'WARN',
      probe: 'supabase_probe.js',
      timestamp: new Date().toISOString(),
      error: err.message
    }, null, 2));
    process.exit(0);
  }
}

main();
