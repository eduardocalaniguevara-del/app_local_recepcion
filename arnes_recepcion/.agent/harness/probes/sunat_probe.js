#!/usr/bin/env node
/** Sonda SUNAT: solo bóveda y comprobantes del Hostal (RUC 20611986484). */
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
const RUC = '20611986484';
const VAULTS = [path.join(ROOT, 'Finanzas', 'credenciales_seguras.json'), 'C:\\Hostal_Sistema\\config\\credenciales_seguras.json'];
const vault = VAULTS.some(p => fs.existsSync(p));
let ventas = 0, lineas = 0, err = null;
if (fs.existsSync(DB_OPERATIVA)) {
  try {
    readDb(DB_OPERATIVA, (db) => {
      try { ventas = db.prepare('SELECT count(*) AS c FROM sunat_ventas;').get().c; } catch (_) {}
      try { lineas = db.prepare('SELECT count(*) AS c FROM sunat_lineas_cache;').get().c; } catch (_) {}
    });
  } catch (e) { err = e.message; }
} else { err = 'ovalo_sistema.db not found: ' + DB_OPERATIVA; }
out({ status: (vault || ventas > 0) && !err ? 'OK' : 'WARN', probe: 'sunat_probe.js', timestamp: new Date().toISOString(), details: { ruc_hostal: RUC, vault_hostal_exists: vault, sunat_ventas: ventas, sunat_lineas_cache: lineas, error: err } });