#!/usr/bin/env node
/** Sonda SQLite Laptop 2: Operativa (8080) y Prueba (8082). Fallback a rutas locales de Laptop 1. */
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..', '..', '..');
const DB_OPERATIVA_CANDIDATES = ['C:\\Recepcion_Ovalo_Local\\data\\ovalo_sistema.db', path.join(ROOT, 'ovalo_sistema.db')];
const DB_PRUEBA_CANDIDATES = ['C:\\ovalo_mariategui_prueba\\data\\ovalo_sistema.db'];
const pick = (c) => c.find(p => fs.existsSync(p)) || c[0];
const DB_OPERATIVA = pick(DB_OPERATIVA_CANDIDATES);
const DB_PRUEBA = pick(DB_PRUEBA_CANDIDATES);
function readDb(dbPath, fn) {
  const { DatabaseSync } = require('node:sqlite');
  let db;
  try { db = new DatabaseSync(dbPath, { readOnly: true }); db.exec('PRAGMA busy_timeout = 5000;'); return fn(db); }
  finally { if (db) { try { db.close(); } catch (_) {} } }
}
function out(o) { console.log(JSON.stringify(o, null, 2)); process.exit(0); }
function checkDb(p) {
  if (!fs.existsSync(p)) return { exists: false, status: 'WARN', path: p, error: 'File not found' };
  try {
    return readDb(p, (db) => {
      const qc = db.prepare('PRAGMA quick_check(1);').get();
      const jm = db.prepare('PRAGMA journal_mode;').get();
      const tc = db.prepare("SELECT count(*) AS c FROM sqlite_master WHERE type='table';").get();
      let outbox = 0; try { outbox = db.prepare('SELECT count(*) AS c FROM _supabase_outbox;').get().c; } catch (_) {}
      const ok = qc && qc.quick_check === 'ok';
      return { exists: true, path: p, integrity: qc && qc.quick_check, journal_mode: jm && jm.journal_mode, table_count: tc.c, outbox_pending: outbox, status: ok ? 'OK' : 'WARN' };
    });
  } catch (e) { return { exists: true, path: p, status: 'FAIL', error: e.message }; }
}
const operativa = checkDb(DB_OPERATIVA), prueba = checkDb(DB_PRUEBA);
const st = [operativa.status, prueba.status];
out({ status: st.includes('FAIL') ? 'FAIL' : (st.every(s => s === 'OK') ? 'OK' : 'WARN'), probe: 'sqlite_probe.js', timestamp: new Date().toISOString(), details: { operativa_8080: operativa, prueba_8082: prueba } });