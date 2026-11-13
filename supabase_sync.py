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
import sqlite3
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
    SUPABASE_SANDBOX_CONNECTED,
    MODO_PRUEBA,
    SUPABASE_SYNC_TOKEN,
    SUPABASE_URL,
)
from database import get_connection, repair_db_if_malformed

_sb_lock = globals().get("_sb_lock") or threading.Lock()
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
    "sunat_ventas": ("nro_cp",),
    "sunat_lineas_cache": ("id_linea",),
    "calzado_cache": ("clave_calzado",),
    "sire_compras_hostal": ("id",),
    "hostal_documentos_ocr": ("id",),
    "yape_conciliacion_hostal": ("id",),
    "tablet_limpieza_dia": ("hab", "fecha_dmy"),
    "tablet_firmas_conformidad": ("id",),
    "tablet_buzon_mejoras": ("id",),
    "wsp_recepcion_chats": ("jid",),
    "wsp_recepcion_mensajes": ("id",),
    "wsp_recepcion_documentos": ("id",),
    "wsp_recepcion_cola_envio": ("id",),
    "aprendizaje_compartido_hostal": ("id",),
}

_SANDBOX = bool(MODO_PRUEBA and SUPABASE_SANDBOX_CONNECTED)


def _resolve_pk_cols(conn, tabla: str) -> Tuple[str, ...]:
    cfg_pks = TABLAS_GRUPO2_PK.get(tabla)
    info = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    if not info:
        return ()
    col_names = {str(r[1]) for r in info}
    if cfg_pks and all(c in col_names for c in cfg_pks):
        return cfg_pks
    real_pks = tuple(str(r[1]) for r in sorted([r for r in info if int(r[5] or 0) > 0], key=lambda x: int(x[5])))
    return real_pks[:2] if real_pks else ()


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
    if not (SUPABASE_ENABLED or _SANDBOX):
        return

    conn = get_connection()
    try:
        conn.executescript(
            """
            -- Tablas adicionales del Grupo 2 (Facturación SUNAT, SIRE, OCR Hostal, Conciliación Yape, WSP Recepción)
            CREATE TABLE IF NOT EXISTS sunat_ventas (
                nro_cp          TEXT PRIMARY KEY,
                tipo            TEXT NOT NULL DEFAULT '',
                periodo_mes     TEXT NOT NULL DEFAULT '',
                fecha_emision   TEXT DEFAULT '',
                receptor_doc    TEXT DEFAULT '',
                receptor_nombre TEXT DEFAULT '',
                monto_total     REAL DEFAULT 0,
                estado          TEXT DEFAULT 'VIGENTE',
                resumen_items   TEXT DEFAULT '',
                actualizado_en  TEXT DEFAULT (datetime('now', 'localtime'))
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
                jid               TEXT PRIMARY KEY,
                nombre            TEXT,
                es_grupo          INTEGER DEFAULT 0,
                ultimo_mensaje_ts INTEGER DEFAULT 0,
                actualizado_en    TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_mensajes (
                id               TEXT PRIMARY KEY,
                chat_jid         TEXT,
                chat_nombre      TEXT,
                remitente_nombre TEXT,
                desde_recepcion  INTEGER DEFAULT 0,
                fecha_hora       TEXT,
                timestamp        INTEGER,
                tipo             TEXT,
                texto            TEXT,
                archivo_ruta     TEXT
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mensaje_id      TEXT UNIQUE,
                fecha_hora      TEXT,
                chat_nombre     TEXT,
                categoria       TEXT,
                serie_numero    TEXT,
                ruc_dni         TEXT,
                monto_detectado REAL,
                operacion_nro   TEXT,
                archivo_ruta    TEXT,
                texto_extraido  TEXT
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_cola_envio (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                destino_telefono TEXT NOT NULL,
                cliente_nombre   TEXT,
                nro_comprobante  TEXT,
                texto            TEXT,
                archivo_ruta     TEXT,
                estado           TEXT DEFAULT 'PENDIENTE',
                creado_en        TEXT DEFAULT (datetime('now', 'localtime')),
                enviado_en       TEXT
            );

            CREATE TABLE IF NOT EXISTS aprendizaje_compartido_hostal (
                id                TEXT PRIMARY KEY,
                fecha_hora        TEXT NOT NULL,
                origen_equipo     TEXT NOT NULL,
                actor             TEXT NOT NULL DEFAULT 'RECEPCIONISTA',
                categoria         TEXT NOT NULL DEFAULT 'OPERATIVO',
                pregunta_o_evento TEXT NOT NULL DEFAULT '',
                patron_detectado  TEXT NOT NULL DEFAULT '',
                regla_aprendida   TEXT NOT NULL DEFAULT '',
                updated_at        TEXT DEFAULT ''
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

        for tabla in TABLAS_GRUPO2_PK.keys():
            if tabla not in existing_tables:
                continue
            pk_cols = _resolve_pk_cols(conn, tabla)
            if not pk_cols:
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
                DROP TRIGGER IF EXISTS trg_sb_ins_{tabla};
                DROP TRIGGER IF EXISTS trg_sb_upd_{tabla};
                DROP TRIGGER IF EXISTS trg_sb_del_{tabla};

                CREATE TRIGGER trg_sb_ins_{tabla}
                AFTER INSERT ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET
                        op = excluded.op,
                        updated_at = excluded.updated_at;
                END;

                CREATE TRIGGER trg_sb_upd_{tabla}
                AFTER UPDATE ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET
                        op = excluded.op,
                        updated_at = excluded.updated_at;
                END;

                CREATE TRIGGER trg_sb_del_{tabla}
                AFTER DELETE ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {old_pk1}, {old_pk2}, 'DELETE', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET
                        op = excluded.op,
                        updated_at = excluded.updated_at;
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
    Separa lectura SQLite, peticiones HTTP y borrado SQLite para mantener 0 ms de bloqueo en ovalo_sistema.db.
    """
    if MODO_PRUEBA:
        return _push_sandbox() if _SANDBOX else 0
    if not SUPABASE_ENABLED:
        return 0

    # FASE A: Lectura rápida en SQLite (< 5 ms) sin retener conexión durante peticiones HTTP
    batches_to_send: List[Tuple[str, str, Tuple[str, ...], List[Tuple[str, str, str]], List[Dict[str, Any]]]] = []
    invalid_keys: List[Tuple[str, str, str]] = []

    conn = get_connection()
    try:
        items = conn.execute(
            "SELECT tabla, pk1, pk2, op, COALESCE(updated_at, '') AS u_at FROM _supabase_outbox ORDER BY updated_at ASC LIMIT 400"
        ).fetchall()
        if not items:
            return 0

        grouped: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {}
        for r in items:
            key = (str(r["tabla"]), str(r["op"]))
            grouped.setdefault(key, []).append((str(r["pk1"]), str(r["pk2"]), str(r["u_at"])))

        for (tabla, op), pk_list in grouped.items():
            pk_cols = _resolve_pk_cols(conn, tabla)
            if not pk_cols:
                invalid_keys.extend((tabla, p1, p2) for (p1, p2, _) in pk_list)
                continue

            rows_payload: List[Dict[str, Any]] = []
            if op == "UPSERT":
                for p1, p2, _ in pk_list:
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
                    if row is not None:
                        rows_payload.append(dict(row))

            batches_to_send.append((tabla, op, pk_cols, pk_list, rows_payload))
    finally:
        conn.close()

    # FASE B: Red HTTP hacia Supabase (0 conexiones SQLite abiertas -> imposible bloquear la base)
    completed_keys: List[Tuple[str, str, str, str]] = []
    total_pushed = 0
    first_fatal_exc: Exception | None = None

    for tabla, op, pk_cols, pk_list, rows_payload in batches_to_send:
        try:
            if op == "UPSERT":
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
                completed_keys.extend((tabla, p1, p2, u_at) for (p1, p2, u_at) in pk_list)

            elif op == "DELETE":
                if len(pk_cols) == 1:
                    col0 = pk_cols[0]
                    for idx_chunk in range(0, len(pk_list), 80):
                        chunk = pk_list[idx_chunk : idx_chunk + 80]
                        in_vals = ",".join(
                            '"' + p1.replace('\\', '\\\\').replace('"', '\\"') + '"'
                            for (p1, _, _) in chunk
                        )
                        q = f"{col0}=in.({urllib.parse.quote(in_vals)})"
                        url = f"{SUPABASE_URL}/rest/v1/{tabla}?{q}"
                        req = urllib.request.Request(url, headers=_headers(), method="DELETE")
                        with urllib.request.urlopen(req, timeout=15):
                            pass
                        completed_keys.extend((tabla, p1, p2, u_at) for (p1, p2, u_at) in chunk)
                        total_pushed += len(chunk)
                else:
                    for p1, p2, u_at in pk_list:
                        q = f"{pk_cols[0]}=eq.{urllib.parse.quote(p1)}&{pk_cols[1]}=eq.{urllib.parse.quote(p2)}"
                        url = f"{SUPABASE_URL}/rest/v1/{tabla}?{q}"
                        req = urllib.request.Request(url, headers=_headers(), method="DELETE")
                        with urllib.request.urlopen(req, timeout=15):
                            pass
                        completed_keys.append((tabla, p1, p2, u_at))
                        total_pushed += 1
        except urllib.error.HTTPError as he:
            body_err = ""
            try:
                body_err = he.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            print(f"[SupabaseSync] Aviso HTTP {he.code} en ({tabla}, {op}): {body_err}")
            if he.code in (400, 404):
                completed_keys.extend((tabla, p1, p2, u_at) for (p1, p2, u_at) in pk_list)
            elif first_fatal_exc is None:
                first_fatal_exc = he
        except Exception as exc:
            if first_fatal_exc is None:
                first_fatal_exc = exc

    # FASE C: Limpieza atómica ultrarrápida (< 5 ms) en _supabase_outbox
    if invalid_keys or completed_keys:
        conn_del = get_connection()
        try:
            if invalid_keys:
                conn_del.executemany(
                    "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                    invalid_keys,
                )
            if completed_keys:
                conn_del.executemany(
                    """
                    DELETE FROM _supabase_outbox
                    WHERE tabla = ? AND pk1 = ? AND pk2 = ?
                      AND (? = '' OR updated_at IS NULL OR updated_at <= ?)
                    """,
                    [(t, p1, p2, u_at, u_at) for (t, p1, p2, u_at) in completed_keys],
                )
            conn_del.commit()
        finally:
            conn_del.close()

    if first_fatal_exc is not None and not completed_keys:
        raise first_fatal_exc

    return total_pushed


def pull_from_supabase() -> int:
    """
    Descarga novedades desde public.supabase_change_log en 1 sola petición HTTP
    fuera de cualquier bloqueo SQLite y las aplica sin disparar bucle de eco.
    """
    if MODO_PRUEBA:
        return _pull_sandbox() if _SANDBOX else 0
    if not SUPABASE_ENABLED:
        return 0

    conn_r = get_connection()
    try:
        r_cur = conn_r.execute(
            "SELECT valor FROM estado_sistema WHERE clave = 'sb_last_log_id'"
        ).fetchone()
        last_log_id = int(r_cur["valor"]) if (r_cur and str(r_cur["valor"]).isdigit()) else 0
    finally:
        conn_r.close()

    url = (
        f"{SUPABASE_URL}/rest/v1/supabase_change_log"
        f"?log_id=gt.{last_log_id}&order=log_id.asc&limit=500"
    )
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        changes = json.loads(resp.read().decode("utf-8"))

    if not changes:
        return 0

    conn = get_connection()
    try:
        pulled_count = 0
        max_log_id = last_log_id

        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
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
                pk_cols = _resolve_pk_cols(conn, tabla)
                if not pk_cols or not isinstance(row_data, dict):
                    continue

                valid_cols = _get_table_columns(conn, tabla)
                if not valid_cols:
                    continue

                if op == "UPSERT":
                    # Si es estancias y localmente tiene dirty=1 sin subir aún, respetar edición local
                    if tabla == "estancias" and row_data.get("estancia_uuid"):
                        in_uuid = str(row_data["estancia_uuid"])
                        in_ts = str(row_data.get("id_timestamp_str") or "").strip()
                        loc = conn.execute(
                            "SELECT estancia_uuid, dirty FROM estancias WHERE estancia_uuid = ? OR (id_timestamp_str != '' AND id_timestamp_str = ?)",
                            (in_uuid, in_ts),
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
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

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
            try:
                conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
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
                conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
        return total_boot
    except Exception:
        return total_boot
    finally:
        conn.close()


_last_arnes_sync_ts: float = 0.0
_arnes_instalado_sesion: bool = False


def _ejecutar_ciclo_arnes_background() -> None:
    global _arnes_instalado_sesion
    try:
        if not _arnes_instalado_sesion:
            _arnes_instalado_sesion = True
            try:
                import sys as _sys
                import instalar_arnes_laptop2 as _inst
                old_argv = list(_sys.argv)
                _sys.argv = ["instalar_arnes_laptop2.py", "--auto"]
                try:
                    _inst.main()
                finally:
                    _sys.argv = old_argv
            except Exception:
                pass
        try:
            import scheduler_recepcion_24x7 as _sched
            _sched.paso_completo_automatizaciones()
        except Exception:
            pass
    except Exception:
        pass


def _enviar_heartbeat_equipo(force: bool = False) -> None:
    """Registra en Supabase que este equipo (Laptop 1 o Laptop 2) está en línea junto con su versión y cursor."""
    global _last_heartbeat_ts, _last_arnes_sync_ts
    now = time.time()
    if not force and (now - _last_heartbeat_ts < 45.0):
        return
    _last_heartbeat_ts = now
    if now - _last_arnes_sync_ts >= 1800.0:
        _last_arnes_sync_ts = now
        threading.Thread(target=_ejecutar_ciclo_arnes_background, name="Arnes2HeartbeatWorker", daemon=True).start()
    try:
        ver = ""
        try:
            from auto_updater import obtener_version_actual
            ver = obtener_version_actual()
        except Exception:
            ver = ""
        last_lid = "0"
        try:
            c = get_connection()
            try:
                r_cur = c.execute("SELECT valor FROM estado_sistema WHERE clave = 'sb_last_log_id'").fetchone()
                if r_cur and r_cur["valor"]:
                    last_lid = str(r_cur["valor"])
            finally:
                c.close()
        except Exception:
            pass
        now_iso = datetime.now().isoformat()
        payload = [
            {
                "clave": f"heartbeat_{EQUIPO_ID}",
                "valor": f"ONLINE | {now_iso} | v={ver} | log={last_lid}",
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


def _limpiar_inundacion_outbox_una_vez() -> None:
    """
    Purga por única vez los miles de registros espejo históricos acumulados en _supabase_outbox
    por los ciclos previos de pull_from_sheets(), conservando cambios reales/recientes.
    """
    if not SUPABASE_ENABLED:
        return
    marker_key = "sb_outbox_flood_cleanup_v1"
    conn = get_connection()
    try:
        r = conn.execute("SELECT valor FROM estado_sistema WHERE clave = ?", (marker_key,)).fetchone()
        if r and str(r["valor"]) == "OK":
            return
        now_iso = datetime.now().isoformat()
        conn.execute(
            """
            DELETE FROM _supabase_outbox
            WHERE tabla IN (
                'sunat_lineas_cache', 'calzado_cache', 'catalogos_local',
                'habitaciones_config', 'vitrina_local', 'almacen_movimientos',
                'horario_registros', 'horario_auditoria'
            )
            """
        )
        conn.execute(
            """
            DELETE FROM _supabase_outbox
            WHERE tabla = 'ventas_tienda'
              AND pk1 NOT IN (SELECT venta_uuid FROM ventas_tienda WHERE synced = 0)
            """
        )
        conn.execute(
            """
            DELETE FROM _supabase_outbox
            WHERE tabla = 'pagos'
              AND pk1 NOT IN (SELECT pago_id FROM pagos WHERE synced = 0)
            """
        )
        conn.execute(
            """
            DELETE FROM _supabase_outbox
            WHERE tabla = 'gastos'
              AND pk1 NOT IN (SELECT gasto_uuid FROM gastos WHERE synced = 0)
            """
        )
        conn.execute(
            """
            DELETE FROM _supabase_outbox
            WHERE tabla = 'estancias' AND op = 'UPSERT'
              AND pk1 NOT IN (
                  SELECT estancia_uuid FROM estancias
                  WHERE dirty = 1 OR sheet_row = 0 OR ymd_in >= 20261111 OR ymd_out >= 20261112
              )
            """
        )
        conn.execute(
            """
            INSERT INTO estado_sistema (clave, valor, updated_at)
            VALUES (?, 'OK', ?)
            ON CONFLICT(clave) DO UPDATE SET valor = 'OK', updated_at = excluded.updated_at
            """,
            (marker_key, now_iso),
        )
        conn.commit()
    except Exception as exc:
        print(f"[SupabaseSync] Aviso limpiando cola histórica: {exc}")
    finally:
        conn.close()


def _reconciliar_ultimos_dias_una_vez() -> None:
    """
    Asegura por única vez en Laptop 1 y Laptop 2 que cualquier estancia, pago, gasto,
    venta o movimiento registrado localmente en los últimos días (mientras algún trigger
    estuvo en reparación) se encole de inmediato en _supabase_outbox y sync_queue.
    """
    if not SUPABASE_ENABLED:
        return
    marker_key = "sb_resync_20261112_v4"
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT valor FROM estado_sistema WHERE clave = ?", (marker_key,)
        ).fetchone()
        if r and str(r["valor"]) == "OK":
            return

        now_iso = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'estancias', CAST(estancia_uuid AS TEXT), '', 'UPSERT', ?
            FROM estancias
            WHERE ymd_in >= 20261108 OR ymd_out >= 20261111 OR sheet_row = 0 OR dirty = 1
            ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'pagos', CAST(pago_id AS TEXT), '', 'UPSERT', ?
            FROM (SELECT pago_id FROM pagos ORDER BY rowid DESC LIMIT 80)
            WHERE TRUE
            ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'gastos', CAST(gasto_uuid AS TEXT), '', 'UPSERT', ?
            FROM (SELECT gasto_uuid FROM gastos ORDER BY rowid DESC LIMIT 60)
            WHERE TRUE
            ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'ventas_tienda', CAST(venta_uuid AS TEXT), '', 'UPSERT', ?
            FROM (SELECT venta_uuid FROM ventas_tienda ORDER BY rowid DESC LIMIT 60)
            WHERE TRUE
            ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'habitaciones_colores', CAST(hab AS TEXT), CAST(fecha_dmy AS TEXT), 'UPSERT', ?
            FROM habitaciones_colores
            WHERE TRUE
            ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )
        conn.execute(
            """
            UPDATE sync_queue
            SET status = 'PENDING', attempts = 0, last_error = ''
            WHERE status IN ('ERROR', 'AUTOCURADO') AND created_at >= '2026-11-10'
            """
        )
        conn.execute(
            """
            INSERT INTO estado_sistema (clave, valor, updated_at)
            VALUES (?, 'OK', ?)
            ON CONFLICT(clave) DO UPDATE SET valor = 'OK', updated_at = excluded.updated_at
            """,
            (marker_key, now_iso),
        )
        conn.commit()
    except Exception as exc:
        print(f"[SupabaseSync] Aviso en reconciliación inicial: {exc}")
    finally:
        conn.close()


def sync_with_supabase() -> Dict[str, Any]:
    """
    Ejecuta un ciclo completo Bidireccional (Push local -> Supabase + Pull Supabase -> local).
    """
    if MODO_PRUEBA and _SANDBOX:
        if not _sb_lock.acquire(blocking=False):
            return {"status": "BUSY", "pushed": 0, "pulled": 0}
        try:
            pushed = push_to_supabase()
            pulled = pull_from_supabase()
            return {"status": "OK_SANDBOX", "equipo": EQUIPO_ID, "pushed": pushed, "pulled": pulled,
                    "ts": datetime.now().isoformat()}
        except Exception as exc:
            print(f"[SupabaseSync][SANDBOX] Error ({EQUIPO_ID}): {exc}")
            return {"status": "ERROR", "equipo": EQUIPO_ID, "error": str(exc), "ts": datetime.now().isoformat()}
        finally:
            _sb_lock.release()
    if not SUPABASE_ENABLED:
        return {"status": "DISABLED", "pushed": 0, "pulled": 0}
    lock = _sb_lock
    if not lock.acquire(blocking=False):
        return {"status": "BUSY", "pushed": 0, "pulled": 0}
    try:
        try:
            _limpiar_inundacion_outbox_una_vez()
            _reconciliar_ultimos_dias_una_vez()
            boot = _bootstrap_tablas_hostal_si_vacias()
            pushed = push_to_supabase()
            pulled = pull_from_supabase() + boot
        except sqlite3.DatabaseError as db_err:
            if any(k in str(db_err).lower() for k in ("malformed", "corrupt", "not a database")):
                if repair_db_if_malformed(force=True):
                    init_supabase_local_triggers()
                    pushed = push_to_supabase()
                    pulled = pull_from_supabase()
                else:
                    raise
            else:
                raise
        _enviar_heartbeat_equipo()
        return {
            "status": "OK",
            "equipo": EQUIPO_ID,
            "pushed": pushed,
            "pulled": pulled,
            "ts": datetime.now().isoformat(),
        }
    except Exception as exc:
        print(f"[SupabaseSync] Error en sincronización ({EQUIPO_ID}): {exc}")
        return {
            "status": "ERROR",
            "equipo": EQUIPO_ID,
            "error": str(exc),
            "ts": datetime.now().isoformat(),
        }
    finally:
        lock.release()


# ======================================================================
# PUENTE DE PRUEBAS (MODO_PRUEBA): solo escribe en sandbox_change_log.
# Las tablas reales de Supabase JAMAS reciben POST/DELETE desde la prueba.
# ======================================================================
def _sb_guard_sandbox_url(url: str, method: str) -> None:
    """Candado: en MODO_PRUEBA solo se permite POST a sandbox_change_log / aprendizaje; el resto GET."""
    if method.upper() == "GET":
        return
    if method.upper() == "POST" and ("/rest/v1/sandbox_change_log" in url or "/rest/v1/aprendizaje_compartido_hostal" in url):
        return
    raise PermissionError(f"MODO_PRUEBA: escritura bloqueada hacia {method} {url}")


def _http_json(url: str, method: str = "GET", payload: Any = None, extra: Dict[str, str] | None = None, timeout: int = 20):
    if MODO_PRUEBA:
        _sb_guard_sandbox_url(url, method)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(extra), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else None


def _push_sandbox() -> int:
    """Empaqueta _supabase_outbox -> POST unicamente a /rest/v1/sandbox_change_log."""
    conn = get_connection()
    try:
        items = conn.execute(
            "SELECT tabla, pk1, pk2, op, COALESCE(updated_at,'') AS u_at FROM _supabase_outbox ORDER BY updated_at ASC LIMIT 200"
        ).fetchall()
        payload: List[Dict[str, Any]] = []
        keys: List[Tuple[str, str, str, str]] = []
        for r in items:
            tabla, p1, p2, op, u_at = str(r["tabla"]), str(r["pk1"]), str(r["pk2"]), str(r["op"]), str(r["u_at"])
            pk_cols = _resolve_pk_cols(conn, tabla)
            if not pk_cols:
                keys.append((tabla, p1, p2, u_at))
                continue
            row_data: Dict[str, Any] | None = None
            if op == "UPSERT":
                if len(pk_cols) == 1:
                    row = conn.execute(f"SELECT * FROM {tabla} WHERE CAST({pk_cols[0]} AS TEXT) = ?", (p1,)).fetchone()
                else:
                    row = conn.execute(
                        f"SELECT * FROM {tabla} WHERE CAST({pk_cols[0]} AS TEXT) = ? AND CAST({pk_cols[1]} AS TEXT) = ?",
                        (p1, p2),
                    ).fetchone()
                if row is None:
                    keys.append((tabla, p1, p2, u_at))
                    continue
                row_data = dict(row)
            else:
                row_data = {pk_cols[0]: p1}
                if len(pk_cols) > 1:
                    row_data[pk_cols[1]] = p2
            payload.append({"tabla": tabla, "op": op, "pk1": p1, "pk2": p2, "row_data": row_data, "origen_equipo": EQUIPO_ID})
            keys.append((tabla, p1, p2, u_at))
    finally:
        conn.close()
    if not keys:
        return 0
    if payload:
        _http_json(f"{SUPABASE_URL}/rest/v1/sandbox_change_log", "POST", payload, {"Prefer": "return=minimal"})
    conn = get_connection()
    try:
        conn.executemany(
            "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ? AND (? = '' OR updated_at IS NULL OR updated_at <= ?)",
            [(t, a, b, u, u) for (t, a, b, u) in keys],
        )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def _aplicar_fila_local(conn, tabla: str, op: str, row_data: Dict[str, Any]) -> bool:
    """UPSERT/DELETE en SQLite de prueba. Requiere pulling=1 ya activo."""
    if tabla not in TABLAS_GRUPO2_PK or not isinstance(row_data, dict):
        return False
    pk_cols = _resolve_pk_cols(conn, tabla)
    valid_cols = _get_table_columns(conn, tabla)
    if not pk_cols or not valid_cols:
        return False
    if op == "DELETE":
        vals = [row_data.get(c) for c in pk_cols]
        if any(v is None for v in vals):
            return False
        conn.execute(f"DELETE FROM {tabla} WHERE " + " AND ".join(f"{c} = ?" for c in pk_cols), vals)
        return True
    cols = [c for c in valid_cols if c in row_data and row_data[c] is not None]
    if not cols:
        return False
    vals = [v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False) for v in (row_data[c] for c in cols)]
    conn.execute(f"INSERT OR REPLACE INTO {tabla} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    return True


def _pull_sandbox() -> int:
    """Lee SOLO /rest/v1/sandbox_change_log (cursor sb_sandbox_last_log_id) y lo aplica en la SQLite de prueba."""
    conn_r = get_connection()
    try:
        r = conn_r.execute("SELECT valor FROM estado_sistema WHERE clave = 'sb_sandbox_last_log_id'").fetchone()
        last = int(r["valor"]) if (r and str(r["valor"]).isdigit()) else 0
    finally:
        conn_r.close()
    changes = _http_json(f"{SUPABASE_URL}/rest/v1/sandbox_change_log?log_id=gt.{last}&order=log_id.asc&limit=500") or []
    if not changes:
        return 0
    conn = get_connection()
    n = 0
    try:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
            mx = last
            for it in changes:
                mx = max(mx, int(it.get("log_id") or 0))
                if str(it.get("origen_equipo") or "") == EQUIPO_ID:
                    continue
                if _aplicar_fila_local(conn, str(it.get("tabla") or ""), str(it.get("op") or "UPSERT"), it.get("row_data") or {}):
                    n += 1
            now_iso = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO estado_sistema (clave, valor, updated_at) VALUES ('sb_sandbox_last_log_id', ?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at",
                (str(mx), now_iso),
            )
            conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn.commit()
        except Exception:
            try:
                conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
                conn.rollback()
            except Exception:
                pass
            raise
    finally:
        conn.close()
    return n


def extraer_datos_reales_supabase_readonly(tablas: List[str] | None = None) -> Dict[str, Any]:
    """SOLO LECTURA (GET) de las tablas reales de Supabase hacia la SQLite de prueba. Nunca escribe en la real."""
    if not MODO_PRUEBA:
        return {"status": "ERROR", "error": "Solo disponible en MODO_PRUEBA"}
    elegidas = [t for t in (tablas or list(TABLAS_GRUPO2_PK.keys())) if t in TABLAS_GRUPO2_PK]
    resumen: Dict[str, Any] = {}
    total = 0
    for tabla in elegidas:
        try:
            filas: List[Dict[str, Any]] = []
            off = 0
            while True:
                lote = _http_json(f"{SUPABASE_URL}/rest/v1/{tabla}?select=*&limit=1000&offset={off}", "GET", timeout=30) or []
                filas.extend(lote)
                if len(lote) < 1000:
                    break
                off += 1000
            conn = get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
                    ok = sum(1 for f in filas if _aplicar_fila_local(conn, tabla, "UPSERT", f))
                    conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
                    conn.commit()
                except Exception:
                    try:
                        conn.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
                        conn.rollback()
                    except Exception:
                        pass
                    raise
            finally:
                conn.close()
            resumen[tabla] = ok
            total += ok
        except Exception as exc:
            resumen[tabla] = f"ERROR: {exc}"
    return {"status": "OK", "solo_lectura": True, "total_filas": total, "tablas": resumen}


def registrar_aprendizaje_compartido(actor: str, categoria: str, pregunta_o_evento: str,
                                     patron_detectado: str = "", regla_aprendida: str = "") -> Dict[str, Any]:
    """Guarda un aprendizaje local (y lo sube a aprendizaje_compartido_hostal) para compartir entre laptops."""
    import uuid
    now = datetime.now().isoformat(timespec="seconds")
    fila = {
        "id": uuid.uuid4().hex, "fecha_hora": now, "origen_equipo": EQUIPO_ID,
        "actor": (actor or "RECEPCIONISTA").strip() or "RECEPCIONISTA",
        "categoria": (categoria or "OPERATIVO").strip() or "OPERATIVO",
        "pregunta_o_evento": pregunta_o_evento or "", "patron_detectado": patron_detectado or "",
        "regla_aprendida": regla_aprendida or "", "updated_at": now,
    }
    conn = get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS aprendizaje_compartido_hostal (id TEXT PRIMARY KEY, fecha_hora TEXT NOT NULL, "
            "origen_equipo TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'RECEPCIONISTA', categoria TEXT NOT NULL DEFAULT 'OPERATIVO', "
            "pregunta_o_evento TEXT NOT NULL DEFAULT '', patron_detectado TEXT NOT NULL DEFAULT '', "
            "regla_aprendida TEXT NOT NULL DEFAULT '', updated_at TEXT DEFAULT '')"
        )
        cols = list(fila.keys())
        conn.execute(f"INSERT INTO aprendizaje_compartido_hostal ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [fila[c] for c in cols])
        conn.commit()
    finally:
        conn.close()
    remoto = "no_enviado"
    try:
        _http_json(f"{SUPABASE_URL}/rest/v1/aprendizaje_compartido_hostal?on_conflict=id", "POST", [fila],
                   {"Prefer": "resolution=merge-duplicates,return=minimal"})
        remoto = "enviado"
    except Exception as exc:
        remoto = f"pendiente: {exc}"
    return {"status": "OK", "id": fila["id"], "remoto": remoto}
