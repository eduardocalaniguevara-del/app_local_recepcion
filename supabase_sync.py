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
}


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
    if not SUPABASE_ENABLED:
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
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER trg_sb_upd_{tabla}
                AFTER UPDATE ON {tabla}
                FOR EACH ROW
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', {new_pk1}, {new_pk2}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER trg_sb_del_{tabla}
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
            pk_cols = _resolve_pk_cols(conn, tabla)
            if not pk_cols:
                conn.executemany(
                    "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                    [(tabla, p1, p2) for (p1, p2) in pk_list],
                )
                conn.commit()
                continue

            try:
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
            except urllib.error.HTTPError as he:
                body_err = ""
                try:
                    body_err = he.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    pass
                print(f"[SupabaseSync] Aviso HTTP {he.code} en ({tabla}, {op}): {body_err}")
                if he.code in (400, 404):
                    # Descartar lote con esquema incompatible para nunca bloquear estancias/pagos
                    conn.executemany(
                        "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                        [(tabla, p1, p2) for (p1, p2) in pk_list],
                    )
                    conn.commit()
                else:
                    raise

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


def _reconciliar_ultimos_dias_una_vez() -> None:
    """
    Asegura por única vez en Laptop 1 y Laptop 2 que cualquier estancia, pago, gasto,
    venta o movimiento registrado localmente en los últimos días (mientras algún trigger
    estuvo en reparación) se encole de inmediato en _supabase_outbox y sync_queue.
    """
    if not SUPABASE_ENABLED:
        return
    marker_key = "sb_resync_20261112_v3"
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
            INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'estancias', estancia_uuid, '', 'UPSERT', ?
            FROM estancias
            WHERE ymd_in >= 20261108 OR ymd_out >= 20261111 OR sheet_row = 0 OR dirty = 1
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'pagos', pago_uuid, '', 'UPSERT', ?
            FROM pagos
            WHERE ymd_pago >= 20261108 OR sheet_row = 0 OR dirty = 1
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'gastos', gasto_uuid, '', 'UPSERT', ?
            FROM gastos
            WHERE ymd_gasto >= 20261108 OR sheet_row = 0 OR dirty = 1
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'ventas_tienda', venta_uuid, '', 'UPSERT', ?
            FROM ventas_tienda
            WHERE ymd_venta >= 20261108 OR sheet_row = 0 OR dirty = 1
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
            SELECT 'colores_habitacion', habitacion, '', 'UPSERT', ?
            FROM colores_habitacion
            """,
            (now_iso,),
        )
        conn.execute(
            """
            UPDATE sync_queue
            SET status = 'PENDING', retry_count = 0, last_error = ''
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
    if not SUPABASE_ENABLED:
        return {"status": "DISABLED", "pushed": 0, "pulled": 0}
    lock = _sb_lock
    if not lock.acquire(blocking=False):
        return {"status": "BUSY", "pushed": 0, "pulled": 0}
    try:
        try:
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
