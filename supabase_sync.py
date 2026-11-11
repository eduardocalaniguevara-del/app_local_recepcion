#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Sincronización Bidireccional en Tiempo Real con Supabase (Grupo 2: ovalo_sistema.db)
- Captura automática de cambios locales (0 ms) mediante Triggers SQLite -> _supabase_outbox
- Subida Delta por lotes (POST /rest/v1/<tabla>?on_conflict=...)
- Descarga Delta en 1 sola petición HTTP (GET /rest/v1/supabase_change_log?log_id=gt.<cursor>)
- Aislamiento Total: Jamás toca wsp_wspdocs_russ.db (Grupo 1 Privado).
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Tuple

from config import (
    EQUIPO_ID,
    SUPABASE_ANON_KEY,
    SUPABASE_ENABLED,
    SUPABASE_SYNC_TOKEN,
    SUPABASE_URL,
)
from database import get_connection

_sb_lock = threading.Lock()
_table_cols_cache: Dict[str, List[str]] = {}
_last_heartbeat_ts: float = 0.0
_bootstrap_checked: bool = False

# Mapa de las 33 tablas compartidas del Grupo 2 y sus columnas Primary Key
TABLAS_GRUPO2_PK: Dict[str, Tuple[str, ...]] = {
    "estancias": ("estancia_uuid",),
    "pagos": ("pago_id",),
    "ventas_tienda": ("venta_uuid",),
    "vitrina_local": ("producto",),
    "almacen_movimientos": ("mov_uuid",),
    "compras_tienda_lotes": ("compra_uuid",),
    "gastos": ("gasto_uuid",),
    "habitaciones_config": ("hab",),
    "habitaciones_colores": ("hab", "fecha_dmy"),
    "catalogos_local": ("categoria", "clave"),
    "huespedes_padron": ("dni",),
    "empresas_padron": ("ruc",),
    "personal_config": ("empleado",),
    "horario_registros": ("empleado", "fecha_dmy"),
    "horario_auditoria": ("audit_uuid",),
    "horario_liquidaciones": ("liq_uuid",),
    "control_pases": ("pase_uuid",),
    "servicios_detalle": ("serv_uuid",),
    "historial_movimientos": ("mov_id",),
    "auditoria_audio_noche": ("clip_id",),
    "sunat_ventas": ("id",),
    "sunat_lineas_cache": ("id_linea",),
    "calzado_cache": ("clave_calzado",),
    "sire_compras_hostal": ("id",),
    "hostal_documentos_ocr": ("id",),
    "yape_conciliacion_hostal": ("id",),
    "tablet_limpieza_dia": ("hab", "fecha_dmy"),
    "tablet_firmas_conformidad": ("id",),
    "tablet_buzon_mejoras": ("id",),
    "wsp_recepcion_chats": ("jid",),
    "wsp_recepcion_mensajes": ("id_mensaje",),
    "wsp_recepcion_documentos": ("id",),
    "wsp_recepcion_cola_envio": ("id",),
}


def _headers(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    h = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "x-hostal-sync-token": SUPABASE_SYNC_TOKEN,
        "x-hostal-equipo": EQUIPO_ID,
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def init_supabase_local_triggers() -> None:
    """
    Asegura que todas las tablas del Grupo 2 y los triggers de captura Delta (_supabase_outbox)
    existan en ovalo_sistema.db.
    """
    if not SUPABASE_ENABLED:
        return

    conn = get_connection()
    try:
        conn.executescript(
            """
            -- Tablas adicionales del Grupo 2 (Facturación SUNAT, SIRE, OCR Hostal, Conciliación Yape, WSP Recepción)
            CREATE TABLE IF NOT EXISTS sunat_ventas (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                periodo_mes       TEXT NOT NULL DEFAULT '',
                tipo_doc          TEXT NOT NULL DEFAULT '',
                nro_cp            TEXT NOT NULL DEFAULT '',
                serie             TEXT NOT NULL DEFAULT '',
                numero            INTEGER NOT NULL DEFAULT 0,
                fecha_emision     TEXT NOT NULL DEFAULT '',
                receptor_doc_tipo TEXT NOT NULL DEFAULT '',
                receptor_doc      TEXT NOT NULL DEFAULT '',
                receptor_nombre   TEXT NOT NULL DEFAULT '',
                moneda            TEXT NOT NULL DEFAULT 'PEN',
                total             REAL NOT NULL DEFAULT 0,
                estado            TEXT NOT NULL DEFAULT 'VIGENTE',
                resumen_items     TEXT NOT NULL DEFAULT '',
                items_json        TEXT NOT NULL DEFAULT '[]',
                origen_json       TEXT NOT NULL DEFAULT '',
                updated_at        TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(tipo_doc, nro_cp)
            );

            CREATE TABLE IF NOT EXISTS sire_compras_hostal (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                periodo_mes          TEXT NOT NULL DEFAULT '',
                fecha_emision        TEXT NOT NULL DEFAULT '',
                tipo_cp              TEXT NOT NULL DEFAULT '',
                serie_numero         TEXT NOT NULL DEFAULT '',
                ruc_proveedor        TEXT NOT NULL DEFAULT '',
                razon_social         TEXT NOT NULL DEFAULT '',
                base_imponible       REAL NOT NULL DEFAULT 0,
                igv                  REAL NOT NULL DEFAULT 0,
                monto_total          REAL NOT NULL DEFAULT 0,
                moneda               TEXT NOT NULL DEFAULT 'PEN',
                estado_respaldo      TEXT NOT NULL DEFAULT '',
                estado_bancarizacion TEXT NOT NULL DEFAULT '',
                observacion          TEXT NOT NULL DEFAULT '',
                origen_excel         TEXT NOT NULL DEFAULT '',
                updated_at           TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(periodo_mes, ruc_proveedor, serie_numero)
            );

            CREATE TABLE IF NOT EXISTS hostal_documentos_ocr (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                id_mensaje       TEXT UNIQUE,
                jid              TEXT NOT NULL DEFAULT '',
                origen_chat      TEXT NOT NULL DEFAULT '',
                fecha_hora       TEXT NOT NULL DEFAULT '',
                tipo_archivo     TEXT NOT NULL DEFAULT '',
                nombre_archivo   TEXT NOT NULL DEFAULT '',
                ruta_local       TEXT NOT NULL DEFAULT '',
                metodo_extraccion TEXT NOT NULL DEFAULT '',
                categoria        TEXT NOT NULL DEFAULT '',
                ruc_emisor       TEXT,
                serie_numero     TEXT,
                monto_detectado  TEXT,
                operacion_nro    TEXT,
                texto_extraido   TEXT NOT NULL DEFAULT '',
                fecha_procesado  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS yape_conciliacion_hostal (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_operacion    TEXT NOT NULL DEFAULT '',
                hora_operacion     TEXT NOT NULL DEFAULT '',
                remitente_bcp      TEXT NOT NULL DEFAULT '',
                monto_bcp          REAL NOT NULL DEFAULT 0,
                nro_operacion      TEXT NOT NULL DEFAULT '',
                id_estancia_calzada TEXT DEFAULT '',
                hab_calzada        TEXT DEFAULT '',
                huesped_calzado    TEXT DEFAULT '',
                estado_auditoria   TEXT DEFAULT 'PENDIENTE',
                observacion        TEXT DEFAULT '',
                updated_at         TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_chats (
                jid                  TEXT PRIMARY KEY,
                nombre               TEXT,
                tipo                 TEXT,
                ultimo_mensaje       TEXT,
                fecha_ultimo_mensaje TEXT,
                no_leidos            INTEGER DEFAULT 0,
                actualizado_en       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_mensajes (
                id_mensaje     TEXT PRIMARY KEY,
                jid            TEXT NOT NULL,
                remitente_jid  TEXT,
                nombre_emisor  TEXT,
                es_mio         INTEGER DEFAULT 0,
                timestamp_unix INTEGER,
                fecha_hora     TEXT,
                tipo_mensaje   TEXT,
                texto          TEXT,
                tiene_archivo  INTEGER DEFAULT 0,
                ruta_archivo   TEXT,
                creado_en      TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                id_mensaje        TEXT UNIQUE,
                jid               TEXT,
                fecha_hora        TEXT,
                tipo_archivo      TEXT,
                nombre_archivo    TEXT,
                ruta_local        TEXT,
                metodo_extraccion TEXT,
                categoria         TEXT,
                ruc_emisor        TEXT,
                serie_numero      TEXT,
                monto_detectado   TEXT,
                operacion_nro     TEXT,
                texto_extraido    TEXT,
                fecha_procesado   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_cola_envio (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono_o_jid   TEXT NOT NULL,
                mensaje          TEXT,
                ruta_adjunto     TEXT,
                nombre_adjunto   TEXT,
                estado           TEXT DEFAULT 'PENDIENTE',
                detalle_error    TEXT,
                creado_en        TEXT DEFAULT (datetime('now', 'localtime')),
                enviado_en       TEXT
            );

            -- Control de bucle y Bandeja de Salida Delta hacia Supabase
            CREATE TABLE IF NOT EXISTS _supabase_sync_control (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                pulling INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO _supabase_sync_control (id, pulling) VALUES (1, 0);
            UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;

            CREATE TABLE IF NOT EXISTS _supabase_outbox (
                tabla      TEXT NOT NULL,
                pk1        TEXT NOT NULL,
                pk2        TEXT NOT NULL DEFAULT '',
                op         TEXT NOT NULL DEFAULT 'UPSERT',
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (tabla, pk1, pk2)
            );
            """
        )

        existing_tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        for tabla, pk_cols in TABLAS_GRUPO2_PK.items():
            if tabla not in existing_tables:
                continue
            if len(pk_cols) == 1:
                c1 = pk_cols[0]
                new_pk1, new_pk2 = f"CAST(NEW.{c1} AS TEXT)", "''"
                old_pk1, old_pk2 = f"CAST(OLD.{c1} AS TEXT)", "''"
            else:
                c1, c2 = pk_cols[0], pk_cols[1]
                new_pk1, new_pk2 = f"CAST(NEW.{c1} AS TEXT)", f"CAST(NEW.{c2} AS TEXT)"
                old_pk1, old_pk2 = f"CAST(OLD.{c1} AS TEXT)", f"CAST(OLD.{c2} AS TEXT)"

            conn.executescript(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_sb_ins_{tabla}
                AFTER INSERT ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER IF NOT EXISTS trg_sb_upd_{tabla}
                AFTER UPDATE ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER IF NOT EXISTS trg_sb_del_{tabla}
                AFTER DELETE ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {old_pk1}, {old_pk2}, 'DELETE', datetime('now', 'localtime'));
                END;
                """
            )
        conn.commit()
    finally:
        conn.close()


def _get_table_columns(conn, tabla: str) -> List[str]:
    if tabla not in _table_cols_cache:
        rows = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        _table_cols_cache[tabla] = [r[1] for r in rows]
    return _table_cols_cache[tabla]


def push_to_supabase() -> int:
    """
    Sube en lote todas las filas modificadas localmente en _supabase_outbox hacia Supabase.
    Retorna la cantidad de registros sincronizados.
    """
    if not SUPABASE_ENABLED:
        return 0

    conn = get_connection()
    try:
        items = conn.execute(
            "SELECT tabla, pk1, pk2, op FROM _supabase_outbox ORDER BY updated_at ASC LIMIT 400"
        ).fetchall()
        if not items:
            return 0

        # Agrupar por (tabla, op)
        grouped: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for r in items:
            key = (str(r["tabla"]), str(r["op"]))
            grouped.setdefault(key, []).append((str(r["pk1"]), str(r["pk2"])))

        total_pushed = 0

        for (tabla, op), pk_list in grouped.items():
            pk_cols = TABLAS_GRUPO2_PK.get(tabla)
            if not pk_cols:
                conn.executemany(
                    "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                    [(tabla, p1, p2) for (p1, p2) in pk_list],
                )
                conn.commit()
                continue

            if op == "UPSERT":
                rows_payload = []
                processed_pks = []
                for p1, p2 in pk_list:
                    if len(pk_cols) == 1:
                        row = conn.execute(
                            f"SELECT * FROM {tabla} WHERE CAST({pk_cols[0]} AS TEXT) = ?",
                            (p1,),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            f"SELECT * FROM {tabla} WHERE CAST({pk_cols[0]} AS TEXT) = ? AND CAST({pk_cols[1]} AS TEXT) = ?",
                            (p1, p2),
                        ).fetchone()
                    processed_pks.append((tabla, p1, p2))
                    if row is not None:
                        rows_payload.append(dict(row))

                if rows_payload:
                    on_conflict = ",".join(pk_cols)
                    url = f"{SUPABASE_URL}/rest/v1/{tabla}?on_conflict={on_conflict}"
                    data_bytes = json.dumps(rows_payload, ensure_ascii=False).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=data_bytes,
                        headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        if resp.status not in (200, 201, 204):
                            raise RuntimeError(f"HTTP {resp.status} subiendo {tabla}")
                    total_pushed += len(rows_payload)

                conn.executemany(
                    "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                    processed_pks,
                )
                conn.commit()

            elif op == "DELETE":
                for p1, p2 in pk_list:
                    if len(pk_cols) == 1:
                        q = f"{pk_cols[0]}=eq.{urllib.parse.quote(p1)}"
                    else:
                        q = f"{pk_cols[0]}=eq.{urllib.parse.quote(p1)}&{pk_cols[1]}=eq.{urllib.parse.quote(p2)}"
                    url = f"{SUPABASE_URL}/rest/v1/{tabla}?{q}"
                    req = urllib.request.Request(url, headers=_headers(), method="DELETE")
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        pass
                    conn.execute(
                        "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                        (tabla, p1, p2),
                    )
                    total_pushed += 1
                conn.commit()

        return total_pushed
    finally:
        conn.close()


def pull_from_supabase() -> int:
    """
    Descarga novedades desde public.supabase_change_log en 1 sola petición HTTP
    y las aplica en ovalo_sistema.db sin disparar bucle de eco.
    """
    if not SUPABASE_ENABLED:
        return 0

    conn = get_connection()
    try:
        r_cur = conn.execute(
            "SELECT valor FROM estado_sistema WHERE clave = 'sb_last_log_id'"
        ).fetchone()
        last_log_id = int(r_cur["valor"]) if (r_cur and str(r_cur["valor"]).isdigit()) else 0

        url = (
            f"{SUPABASE_URL}/rest/v1/supabase_change_log"
            f"?log_id=gt.{last_log_id}&order=log_id.asc&limit=500"
        )
        req = urllib.request.Request(url, headers=_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            changes = json.loads(resp.read().decode("utf-8"))

        if not changes:
            return 0

        pulled_count = 0
        max_log_id = last_log_id

        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
        try:
            for item in changes:
                lid = int(item.get("log_id") or 0)
                if lid > max_log_id:
                    max_log_id = lid

                # Si el cambio fue originado por este mismo equipo, no re-escribir
                if str(item.get("origen_equipo") or "") == EQUIPO_ID:
                    continue

                tabla = str(item.get("tabla") or "")
                op = str(item.get("op") or "UPSERT")
                row_data = item.get("row_json") or {}
                pk_cols = TABLAS_GRUPO2_PK.get(tabla)
                if not pk_cols or not isinstance(row_data, dict):
                    continue

                valid_cols = _get_table_columns(conn, tabla)
                if not valid_cols:
                    continue

                if op == "UPSERT":
                    # Si es estancias y localmente tiene dirty=1 sin subir aún, respetar edición local
                    if tabla == "estancias" and row_data.get("estancia_uuid"):
                        loc = conn.execute(
                            "SELECT dirty FROM estancias WHERE estancia_uuid = ?",
                            (str(row_data["estancia_uuid"]),),
                        ).fetchone()
                        if loc and int(loc["dirty"] or 0) == 1:
                            continue

                    cols_present = [c for c in valid_cols if c in row_data and row_data[c] is not None]
                    if not cols_present:
                        continue
                    placeholders = ",".join(["?"] * len(cols_present))
                    cols_sql = ",".join(cols_present)
                    vals = [row_data[c] for c in cols_present]
                    conn.execute(
                        f"INSERT OR REPLACE INTO {tabla} ({cols_sql}) VALUES ({placeholders})",
                        vals,
                    )
                    pulled_count += 1

                elif op == "DELETE":
                    if len(pk_cols) == 1:
                        v1 = row_data.get(pk_cols[0])
                        if v1 is not None:
                            conn.execute(f"DELETE FROM {tabla} WHERE {pk_cols[0]} = ?", (v1,))
                            pulled_count += 1
                    else:
                        v1, v2 = row_data.get(pk_cols[0]), row_data.get(pk_cols[1])
                        if v1 is not None and v2 is not None:
                            conn.execute(
                                f"DELETE FROM {tabla} WHERE {pk_cols[0]} = ? AND {pk_cols[1]} = ?",
                                (v1, v2),
                            )
                            pulled_count += 1

            now_iso = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES ('sb_last_log_id', ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (str(max_log_id), now_iso),
            )
            conn.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES ('sb_last_sync_iso', ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (now_iso, now_iso),
            )
        finally:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn.commit()

        return pulled_count
    finally:
        conn.close()


def _bootstrap_tablas_hostal_si_vacias() -> int:
    """
    En Laptop 2 (Recepción), descarga por única vez las tablas nuevas del Grupo 2
    (sunat_ventas, sire_compras_hostal, hostal_documentos_ocr) desde Supabase si están vacías localmente.
    """
    global _bootstrap_checked
    if _bootstrap_checked or not SUPABASE_ENABLED:
        return 0
    _bootstrap_checked = True

    tablas_boot = ("sunat_ventas", "sire_compras_hostal", "hostal_documentos_ocr")
    total_boot = 0
    conn = get_connection()
    try:
        for t in tablas_boot:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if cnt > 0:
                continue
            url = f"{SUPABASE_URL}/rest/v1/{t}?limit=1000"
            req = urllib.request.Request(url, headers=_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
            if not rows:
                continue
            valid_cols = _get_table_columns(conn, t)
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
            try:
                for row_data in rows:
                    cols_present = [c for c in valid_cols if c in row_data and row_data[c] is not None]
                    if not cols_present:
                        continue
                    placeholders = ",".join(["?"] * len(cols_present))
                    cols_sql = ",".join(cols_present)
                    vals = [row_data[c] for c in cols_present]
                    conn.execute(
                        f"INSERT OR REPLACE INTO {t} ({cols_sql}) VALUES ({placeholders})",
                        vals,
                    )
                    total_boot += 1
            finally:
                conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
                conn.commit()
        return total_boot
    except Exception:
        return total_boot
    finally:
        conn.close()


def _enviar_heartbeat_equipo() -> None:
    """Registra cada 60s en Supabase que este equipo (Laptop 1 o Laptop 2) está en línea."""
    global _last_heartbeat_ts
    now = time.time()
    if now - _last_heartbeat_ts < 60.0:
        return
    _last_heartbeat_ts = now
    try:
        now_iso = datetime.now().isoformat()
        payload = [
            {
                "clave": f"heartbeat_{EQUIPO_ID}",
                "valor": f"ONLINE | {now_iso}",
                "updated_at": now_iso,
            }
        ]
        url = f"{SUPABASE_URL}/rest/v1/estado_sistema?on_conflict=clave"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


def sync_with_supabase() -> Dict[str, Any]:
    """
    Ejecuta un ciclo completo Bidireccional (Push local -> Supabase + Pull Supabase -> local).
    """
    if not SUPABASE_ENABLED:
        return {"status": "DISABLED", "pushed": 0, "pulled": 0}
    if not _sb_lock.acquire(blocking=False):
        return {"status": "BUSY", "pushed": 0, "pulled": 0}
    try:
        boot = _bootstrap_tablas_hostal_si_vacias()
        pushed = push_to_supabase()
        pulled = pull_from_supabase() + boot
        _enviar_heartbeat_equipo()
        return {
            "status": "OK",
            "equipo": EQUIPO_ID,
            "pushed": pushed,
            "pulled": pulled,
            "ts": datetime.now().isoformat(),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "equipo": EQUIPO_ID,
            "error": str(exc),
            "ts": datetime.now().isoformat(),
        }
    finally:
        _sb_lock.release()
