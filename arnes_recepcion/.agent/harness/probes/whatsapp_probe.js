#!/usr/bin/env node
/** Sonda WhatsApp Recepción: 4 tablas wsp_recepcion_* dentro de ovalo_sistema.db. */
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
const TABLAS = ['wsp_recepcion_chats', 'wsp_recepcion_mensajes', 'wsp_recepcion_documentos', 'wsp_recepcion_cola_envio'];
if (!fs.existsSync(DB_OPERATIVA)) out({ status: 'WARN', probe: 'whatsapp_probe.js', timestamp: new Date().toISOString(), error: 'ovalo_sistema.db not found: ' + DB_OPERATIVA });
try {
  const det = readDb(DB_OPERATIVA, (db) => {
    const r = {};
    for (const t of TABLAS) { try { r[t] = { exists: true, rows: db.prepare('SELECT count(*) AS c FROM ' + t + ';').get().c }; } catch (e) { r[t] = { exists: false, error: e.message }; } }
    return r;
  });
  const missing = TABLAS.filter(t => !det[t].exists);
  out({ status: missing.length ? 'WARN' : 'OK', probe: 'whatsapp_probe.js', timestamp: new Date().toISOString(), details: { db: DB_OPERATIVA, tablas: det, faltantes: missing } });
} catch (e) { out({ status: 'WARN', probe: 'whatsapp_probe.js', timestamp: new Date().toISOString(), error: e.message }); }