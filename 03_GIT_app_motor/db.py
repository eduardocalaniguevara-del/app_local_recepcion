"""
04_GIT_app_motor/db.py — Cimientos SQLite (03_DB_sqlite_local/ovalo.db) para los 3 Niveles:
- Modo WAL + busy_timeout=20000 (0 bloqueos en lectura/escritura simultánea).
- Autorreparación B-tree en caliente (repair_db_if_malformed).
- Esquema limpio con candados UNIQUE anti-duplicados (especialmente en maelen_transferencias_correo,
  pagos, estancias, sunat_ventas y cola de WhatsApp Recepción).
"""
import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA_DIR, DB_PATH, ROL_NODO

_db_lock = threading.Lock()

# Mapa oficial de tablas sincronizadas por Supabase entre Nivel 1, Nivel 2 (Madre) y Nivel 3 (Terciarias)
TABLAS_SINCRONIZADAS_PK: Dict[str, Tuple[str, ...]] = {
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
    "sunat_ventas": ("nro_cp",),
    "sire_compras_hostal": ("id",),
    "hostal_documentos_ocr": ("id",),
    "maelen_transferencias_correo": ("msg_id",),
    "tablet_limpieza_dia": ("hab", "fecha_dmy"),
    "tablet_firmas_conformidad": ("id",),
    "tablet_buzon_mejoras": ("id",),
    "wsp_recepcion_chats": ("jid",),
    "wsp_recepcion_mensajes": ("id",),
    "wsp_recepcion_documentos": ("id",),
    "wsp_recepcion_cola_envio": ("id",),
    "wsp_recepcion_estado": ("clave",),
    "wsp_resumen_ejecutivo": ("id",),
    "aprendizaje_compartido_hostal": ("id",),
    "estado_sistema": ("clave",),
}

# Tablas caché o pesadas locales que NUNCA suben a Supabase (protección de Egress; viajan por Syncthing si aplica)
TABLAS_SOLO_LOCAL_CACHE = {
    "yape_conciliacion_hostal",
    "calzado_cache",
    "sunat_lineas_cache",
    "auditoria_audio_noche",
}


def get_connection() -> sqlite3.Connection:
    """Devuelve conexión SQLite en modo WAL lista para lecturas y escrituras concurrentes."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_transaction() -> Iterator[sqlite3.Connection]:
    """Transacción atómica BEGIN IMMEDIATE con rollback automático en caso de error."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def repair_db_if_malformed(force: bool = False) -> bool:
    """Verifica con PRAGMA quick_check y reconstruye ovalo.db en caliente si detecta daño por apagón."""
    if not DB_PATH.exists():
        return False
    with _db_lock:
        needs_repair = force
        if not needs_repair:
            try:
                with sqlite3.connect(str(DB_PATH), timeout=10.0) as chk:
                    res = chk.execute("PRAGMA quick_check(1);").fetchall()
                    if not res or str(res[0][0]).strip().lower() != "ok":
                        needs_repair = True
            except sqlite3.DatabaseError:
                needs_repair = True
        if not needs_repair:
            return False

        tmp_clean = DB_PATH.parent / "ovalo_clean_rebuild.db"
        if tmp_clean.exists():
            tmp_clean.unlink()

        src = sqlite3.connect(str(DB_PATH), timeout=20.0)
        dst = sqlite3.connect(str(tmp_clean), timeout=20.0)
        try:
            tables = src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
            ).fetchall()
            for _, sql in tables:
                dst.execute(sql)
            for t_name, _ in tables:
                if t_name == "_supabase_outbox":
                    continue
                cols = [c[1] for c in dst.execute(f'PRAGMA table_info("{t_name}")').fetchall()]
                if not cols:
                    continue
                cols_q = ", ".join(f'"{c}"' for c in cols)
                ph = ", ".join("?" for _ in cols)
                rows = src.execute(f'SELECT {cols_q} FROM "{t_name}"').fetchall()
                if rows:
                    dst.executemany(f'INSERT OR IGNORE INTO "{t_name}" ({cols_q}) VALUES ({ph})', rows)
            dst.commit()
        finally:
            src.close()
            dst.close()
        tmp_clean.replace(DB_PATH)
        return True


def init_db() -> dict:
    """
    Inicializa todas las tablas operativas en 03_DB_sqlite_local/ovalo.db con candados UNIQUE
    anti-duplicados. Se ejecuta automáticamente al arrancar en Nivel 1, Nivel 2 o Nivel 3.
    """
    repair_db_if_malformed()
    conn = get_connection()
    try:
        conn.executescript(
            """
            -- 0. Control de Sincronización Delta con Supabase
            CREATE TABLE IF NOT EXISTS _supabase_sync_control (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                pulling     INTEGER NOT NULL DEFAULT 0,
                last_log_id INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );
            INSERT OR IGNORE INTO _supabase_sync_control (id, pulling) VALUES (1, 0);

            CREATE TABLE IF NOT EXISTS _supabase_outbox (
                tabla      TEXT NOT NULL,
                pk1        TEXT NOT NULL,
                pk2        TEXT NOT NULL DEFAULT '',
                op         TEXT NOT NULL DEFAULT 'UPSERT',
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (tabla, pk1, pk2)
            );

            -- 1. Operación Principal de Mostrador (Estancias, Pagos, Tienda, Gastos, Padrones)
            CREATE TABLE IF NOT EXISTS estancias (
                estancia_uuid    TEXT PRIMARY KEY,
                id_timestamp_str TEXT DEFAULT '',
                sheet_row        INTEGER DEFAULT 0,
                hab              TEXT NOT NULL DEFAULT '',
                tipo_hab         TEXT DEFAULT '',
                huesped          TEXT DEFAULT '',
                dni              TEXT DEFAULT '',
                celular          TEXT DEFAULT '',
                empresa          TEXT DEFAULT '',
                ruc_empresa      TEXT DEFAULT '',
                fecha_in_dmy     TEXT DEFAULT '',
                fecha_out_dmy    TEXT DEFAULT '',
                ymd_in           TEXT DEFAULT '',
                ymd_out          TEXT DEFAULT '',
                noches           INTEGER DEFAULT 1,
                tarifa           REAL DEFAULT 0,
                pago_ini_efec    REAL DEFAULT 0,
                pago_ini_yape    REAL DEFAULT 0,
                pago_post_efec   REAL DEFAULT 0,
                pago_post_yape   REAL DEFAULT 0,
                pago_visa        REAL DEFAULT 0,
                nro_comp         TEXT DEFAULT '',
                estado_estancia  TEXT DEFAULT 'ACTIVA',
                observaciones    TEXT DEFAULT '',
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS pagos (
                pago_id          TEXT PRIMARY KEY,
                estancia_uuid    TEXT DEFAULT '',
                sheet_row        INTEGER DEFAULT 0,
                fecha_iso        TEXT DEFAULT '',
                fecha_dmy        TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                huesped          TEXT DEFAULT '',
                medio_pago       TEXT DEFAULT '',
                monto            REAL DEFAULT 0,
                tipo_pago        TEXT DEFAULT '',
                concepto         TEXT DEFAULT '',
                nro_operacion    TEXT DEFAULT '',
                titular_yape     TEXT DEFAULT '',
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS ventas_tienda (
                venta_uuid       TEXT PRIMARY KEY,
                sheet_row        INTEGER DEFAULT 0,
                fecha_dmy        TEXT DEFAULT '',
                hora_str         TEXT DEFAULT '',
                producto         TEXT NOT NULL DEFAULT '',
                cantidad         INTEGER DEFAULT 1,
                precio_unit      REAL DEFAULT 0,
                subtotal         REAL DEFAULT 0,
                metodo_pago      TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS vitrina_local (
                producto         TEXT PRIMARY KEY,
                categoria        TEXT DEFAULT 'BEBIDAS',
                precio_venta     REAL DEFAULT 0,
                stock_vitrina    INTEGER DEFAULT 0,
                stock_almacen    INTEGER DEFAULT 0,
                activo           INTEGER DEFAULT 1,
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS almacen_movimientos (
                mov_uuid         TEXT PRIMARY KEY,
                fecha_dmy        TEXT DEFAULT '',
                producto         TEXT DEFAULT '',
                tipo_mov         TEXT DEFAULT '',
                cantidad         INTEGER DEFAULT 0,
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS compras_tienda_lotes (
                compra_uuid      TEXT PRIMARY KEY,
                fecha_dmy        TEXT DEFAULT '',
                producto         TEXT DEFAULT '',
                cantidad         INTEGER DEFAULT 0,
                costo_total      REAL DEFAULT 0,
                proveedor        TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS gastos (
                gasto_uuid       TEXT PRIMARY KEY,
                id_ts_str        TEXT DEFAULT '',
                sheet_row        INTEGER DEFAULT 0,
                fecha_gasto_dmy  TEXT DEFAULT '',
                categoria        TEXT DEFAULT '',
                descripcion      TEXT DEFAULT '',
                monto            REAL DEFAULT 0,
                metodo_pago      TEXT DEFAULT '',
                responsable      TEXT DEFAULT '',
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS habitaciones_config (
                hab              TEXT PRIMARY KEY,
                piso             INTEGER DEFAULT 2,
                tipo             TEXT DEFAULT 'MATRIMONIAL',
                tarifa_base      REAL DEFAULT 50,
                estado_limpieza  TEXT DEFAULT 'LIMPIA',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS habitaciones_colores (
                hab              TEXT NOT NULL,
                fecha_dmy        TEXT NOT NULL,
                color_hex        TEXT DEFAULT '',
                estado           TEXT DEFAULT '',
                PRIMARY KEY (hab, fecha_dmy)
            );

            CREATE TABLE IF NOT EXISTS catalogos_local (
                categoria        TEXT NOT NULL,
                clave            TEXT NOT NULL,
                valor            TEXT DEFAULT '',
                PRIMARY KEY (categoria, clave)
            );

            CREATE TABLE IF NOT EXISTS huespedes_padron (
                dni              TEXT PRIMARY KEY,
                nombre           TEXT DEFAULT '',
                celular          TEXT DEFAULT '',
                hab_frec         TEXT DEFAULT '',
                notas            TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS empresas_padron (
                ruc              TEXT PRIMARY KEY,
                razon_social     TEXT DEFAULT '',
                direccion        TEXT DEFAULT '',
                correo           TEXT DEFAULT '',
                celular          TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            -- 2. Personal, Horarios y Tablet Recepción
            CREATE TABLE IF NOT EXISTS personal_config (
                empleado         TEXT PRIMARY KEY,
                rol              TEXT DEFAULT 'RECEPCION',
                activo           INTEGER DEFAULT 1,
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS horario_registros (
                empleado         TEXT NOT NULL,
                fecha_dmy        TEXT NOT NULL,
                hora_entrada     TEXT DEFAULT '',
                hora_salida      TEXT DEFAULT '',
                turno            TEXT DEFAULT '',
                PRIMARY KEY (empleado, fecha_dmy)
            );

            CREATE TABLE IF NOT EXISTS horario_auditoria (
                audit_uuid       TEXT PRIMARY KEY,
                empleado         TEXT DEFAULT '',
                fecha_dmy        TEXT DEFAULT '',
                detalle          TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS horario_liquidaciones (
                liq_uuid         TEXT PRIMARY KEY,
                empleado         TEXT DEFAULT '',
                periodo          TEXT DEFAULT '',
                monto            REAL DEFAULT 0,
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS control_pases (
                pase_uuid        TEXT PRIMARY KEY,
                fecha_dmy        TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                detalle          TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS servicios_detalle (
                serv_uuid        TEXT PRIMARY KEY,
                estancia_uuid    TEXT DEFAULT '',
                concepto         TEXT DEFAULT '',
                monto            REAL DEFAULT 0,
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS historial_movimientos (
                mov_id           TEXT PRIMARY KEY,
                fecha_hora       TEXT DEFAULT '',
                usuario          TEXT DEFAULT '',
                accion           TEXT DEFAULT '',
                detalle          TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS auditoria_audio_noche (
                clip_id          TEXT PRIMARY KEY,
                fecha_hora       TEXT DEFAULT '',
                resumen          TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS tablet_limpieza_dia (
                hab              TEXT NOT NULL,
                fecha_dmy        TEXT NOT NULL,
                estado           TEXT DEFAULT 'PENDIENTE',
                personal         TEXT DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (hab, fecha_dmy)
            );

            CREATE TABLE IF NOT EXISTS tablet_firmas_conformidad (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                estancia_uuid    TEXT DEFAULT '',
                dni              TEXT DEFAULT '',
                fecha_hora       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS tablet_buzon_mejoras (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora       TEXT DEFAULT (datetime('now', 'localtime')),
                autor            TEXT DEFAULT '',
                mensaje          TEXT DEFAULT ''
            );

            -- 3. Transferencias BCP/Yape extraídas en Nivel 1 (02_PRIV_correo_bcp) -> Supabase -> Nivel 2 y 3
            -- CANDADO UNIQUE ANTI-DUPLICADOS: impide que una misma transferencia bancaria se duplique jamás
            CREATE TABLE IF NOT EXISTS maelen_transferencias_correo (
                msg_id                TEXT PRIMARY KEY,
                cuenta_correo         TEXT DEFAULT '',
                fecha_operacion       TEXT NOT NULL DEFAULT '',
                hora_operacion        TEXT DEFAULT '',
                tipo_movimiento       TEXT DEFAULT '',
                remitente_o_destino   TEXT NOT NULL DEFAULT '',
                monto                 REAL NOT NULL DEFAULT 0,
                nro_operacion         TEXT DEFAULT '',
                cuenta_origen_destino TEXT DEFAULT 'BCP CTA CTE 8023 MAELEN',
                asunto                TEXT DEFAULT '',
                estado_cruce          TEXT DEFAULT 'PENDIENTE',
                id_cruce_recepcion    TEXT DEFAULT '',
                detalle_cruce         TEXT DEFAULT '',
                created_at            TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_maelen_bcp_unique
            ON maelen_transferencias_correo (fecha_operacion, hora_operacion, monto, nro_operacion, remitente_o_destino);

            -- 4. Facturación Electrónica SUNAT y Compras SIRE
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
                monto_total          REAL DEFAULT 0,
                estado_respaldo      TEXT DEFAULT 'PENDIENTE',
                estado_bancarizacion TEXT DEFAULT 'NO_APLICA',
                observacion          TEXT DEFAULT '',
                UNIQUE (ruc_proveedor, tipo_cp, serie_numero)
            );

            CREATE TABLE IF NOT EXISTS hostal_documentos_ocr (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora       TEXT DEFAULT '',
                origen_chat      TEXT DEFAULT '',
                categoria        TEXT DEFAULT '',
                serie_numero     TEXT DEFAULT '',
                ruc_emisor       TEXT DEFAULT '',
                monto_detectado  REAL DEFAULT 0,
                operacion_nro    TEXT DEFAULT '',
                texto_extraido   TEXT DEFAULT '',
                archivo_hash     TEXT UNIQUE
            );

            -- 5. WhatsApp Centralizado en Nivel 2 (Laptop Madre con único QR) -> Expandido a Nivel 1 y Nivel 3
            CREATE TABLE IF NOT EXISTS wsp_recepcion_chats (
                jid               TEXT PRIMARY KEY,
                nombre            TEXT DEFAULT '',
                es_grupo          INTEGER DEFAULT 0,
                ultimo_mensaje_ts INTEGER DEFAULT 0,
                actualizado_en    TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_mensajes (
                id               TEXT PRIMARY KEY,
                chat_jid         TEXT NOT NULL DEFAULT '',
                chat_nombre      TEXT DEFAULT '',
                remitente_jid    TEXT DEFAULT '',
                remitente_nombre TEXT DEFAULT '',
                desde_recepcion  INTEGER DEFAULT 0,
                timestamp        INTEGER DEFAULT 0,
                fecha_hora       TEXT DEFAULT '',
                tipo             TEXT DEFAULT 'texto',
                texto            TEXT DEFAULT '',
                media_path       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                mensaje_id       TEXT UNIQUE,
                chat_jid         TEXT DEFAULT '',
                fecha_hora       TEXT DEFAULT '',
                tipo_doc         TEXT DEFAULT '',
                archivo_nombre   TEXT DEFAULT '',
                texto_ocr        TEXT DEFAULT '',
                monto_detectado  REAL DEFAULT 0,
                operacion_nro    TEXT DEFAULT ''
            );

            -- Cola Remota de Envío: Nivel 1 o Nivel 3 (Terciarias sin QR) insertan aquí y Nivel 2 (Madre) dispara el WSP
            CREATE TABLE IF NOT EXISTS wsp_recepcion_cola_envio (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                destino_celular  TEXT NOT NULL DEFAULT '',
                mensaje_texto    TEXT DEFAULT '',
                adjunto_pdf_path TEXT DEFAULT '',
                nro_comprobante  TEXT DEFAULT '',
                solicitado_por   TEXT DEFAULT '',
                estado           TEXT DEFAULT 'PENDIENTE',
                intentos         INTEGER DEFAULT 0,
                error_detalle    TEXT DEFAULT '',
                creado_en        TEXT DEFAULT (datetime('now', 'localtime')),
                enviado_en       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_estado (
                clave            TEXT PRIMARY KEY,
                valor            TEXT DEFAULT '',
                actualizado_en   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wsp_resumen_ejecutivo (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_dmy        TEXT UNIQUE,
                resumen_json     TEXT DEFAULT '{}',
                actualizado_en   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS aprendizaje_compartido_hostal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha            TEXT DEFAULT (datetime('now', 'localtime')),
                origen_nodo      TEXT DEFAULT '',
                categoria        TEXT DEFAULT '',
                titulo           TEXT UNIQUE,
                contenido        TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS estado_sistema (
                clave            TEXT PRIMARY KEY,
                valor            TEXT NOT NULL DEFAULT '',
                updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS sync_queue (
                queue_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type          TEXT NOT NULL DEFAULT '',
                payload_json     TEXT NOT NULL DEFAULT '{}',
                status           TEXT NOT NULL DEFAULT 'PENDING',
                retries          INTEGER NOT NULL DEFAULT 0,
                last_error       TEXT NOT NULL DEFAULT '',
                created_at       TEXT DEFAULT (datetime('now', 'localtime')),
                synced_at        TEXT DEFAULT ''
            );

            -- 6. Tablas Caché SOLO LOCALES (Bloqueadas de subir a Supabase para ahorro de Egress)
            CREATE TABLE IF NOT EXISTS yape_conciliacion_hostal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                pago_id          TEXT UNIQUE,
                fecha_dmy        TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                huesped          TEXT DEFAULT '',
                monto            REAL DEFAULT 0,
                estado_cruce     TEXT DEFAULT 'PENDIENTE',
                fuente_banco     TEXT DEFAULT '',
                operacion_banco  TEXT DEFAULT '',
                actualizado_en   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS calzado_cache (
                clave_calzado    TEXT PRIMARY KEY,
                fecha_noche_dmy  TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                estancia_uuid    TEXT DEFAULT '',
                nro_cp           TEXT DEFAULT '',
                estado_calzado   TEXT DEFAULT '',
                actualizado_en   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS sunat_lineas_cache (
                id_linea         TEXT PRIMARY KEY,
                nro_cp           TEXT DEFAULT '',
                tipo             TEXT DEFAULT '',
                noche_dmy        TEXT DEFAULT '',
                hab              TEXT DEFAULT '',
                monto_linea      REAL DEFAULT 0,
                total_cp         REAL DEFAULT 0,
                cliente          TEXT DEFAULT '',
                doc              TEXT DEFAULT '',
                fecha_emision    TEXT DEFAULT '',
                estado           TEXT DEFAULT '',
                descripcion      TEXT DEFAULT ''
            );
            """
        )
        conn.commit()
        for col_def in ("last_log_id INTEGER NOT NULL DEFAULT 0", "updated_at TEXT DEFAULT ''"):
            try:
                conn.execute(f"ALTER TABLE _supabase_sync_control ADD COLUMN {col_def};")
                conn.commit()
            except sqlite3.OperationalError:
                pass

        # Estandarización de columnas entre Laptop 1, Laptop 2 y Supabase
        _estandar_cols = [
            ("estancias", "unificado_en_uuid TEXT DEFAULT ''"),
            ("estancias", "opcion_decision TEXT DEFAULT ''"),
            ("ventas_tienda", "yape_tienda_usado_en TEXT DEFAULT ''"),
            ("sunat_ventas", "id INTEGER"),
            ("sunat_ventas", "tipo_doc TEXT DEFAULT ''"),
            ("sunat_ventas", "serie TEXT DEFAULT ''"),
            ("sunat_ventas", "numero TEXT DEFAULT ''"),
            ("sunat_ventas", "receptor_doc_tipo TEXT DEFAULT ''"),
            ("sunat_ventas", "moneda TEXT DEFAULT 'PEN'"),
            ("sunat_ventas", "total REAL DEFAULT 0"),
            ("sunat_ventas", "items_json TEXT DEFAULT ''"),
            ("sunat_ventas", "origen_json TEXT DEFAULT ''"),
            ("sunat_ventas", "updated_at TEXT DEFAULT ''"),
            ("sire_compras_hostal", "tipo_cp TEXT DEFAULT ''"),
            ("sire_compras_hostal", "moneda TEXT DEFAULT 'PEN'"),
            ("sire_compras_hostal", "origen_excel TEXT DEFAULT ''"),
            ("sire_compras_hostal", "updated_at TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "id_mensaje TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "jid TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "nombre_archivo TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "ruta_local TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "metodo_extraccion TEXT DEFAULT ''"),
            ("hostal_documentos_ocr", "fecha_procesado TEXT DEFAULT ''"),
        ]
        for tbl_name, col_def in _estandar_cols:
            try:
                conn.execute(f"ALTER TABLE {tbl_name} ADD COLUMN {col_def};")
            except sqlite3.OperationalError:
                pass

        # Limpiar de _supabase_outbox cualquier tabla de TABLAS_SOLO_LOCAL_CACHE (ej. auditoria_audio_noche)
        for t_cache in TABLAS_SOLO_LOCAL_CACHE:
            conn.execute("DELETE FROM _supabase_outbox WHERE tabla = ?", (t_cache,))

        # Sincronizar el cursor last_log_id con sb_last_log_id de estado_sistema si estaba en 0
        try:
            ctrl_row = conn.execute("SELECT last_log_id FROM _supabase_sync_control WHERE id = 1").fetchone()
            sb_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'sb_last_log_id'").fetchone()
            cur_ctrl = int(ctrl_row[0]) if ctrl_row and ctrl_row[0] else 0
            cur_sb = int(sb_row[0]) if (sb_row and str(sb_row[0]).isdigit()) else 0
            max_cur = max(cur_ctrl, cur_sb)
            if max_cur > 0 and cur_ctrl != max_cur:
                conn.execute("UPDATE _supabase_sync_control SET last_log_id = ? WHERE id = 1", (max_cur,))
        except Exception:
            pass
        conn.commit()

        total_tablas = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        return {
            "ok": True,
            "nodo": ROL_NODO,
            "db_path": str(DB_PATH),
            "tablas_totales": total_tablas,
            "tablas_sincronizadas": len(TABLAS_SINCRONIZADAS_PK),
            "tablas_cache_local": sorted(TABLAS_SOLO_LOCAL_CACHE),
        }
    finally:
        conn.close()


def probar_candado_unique_antiduplicados() -> dict:
    """
    Prueba unitaria rápida: intenta insertar 2 veces la misma transferencia bancaria BCP
    y verifica que el candado UNIQUE deje exactamente 1 sola fila.
    """
    init_db()
    conn = get_connection()
    try:
        sql = """
            INSERT OR IGNORE INTO maelen_transferencias_correo
            (msg_id, fecha_operacion, hora_operacion, monto, nro_operacion, remitente_o_destino)
            VALUES (?, '21/11/2026', '10:00:00', 55.0, 'OP-TEST-UNIQUE-999', 'CLIENTE PRUEBA UNIQUE')
        """
        conn.execute(sql, ("MSG-TEST-1",))
        conn.execute(sql, ("MSG-TEST-2",))  # Distinto msg_id pero misma operación/monto/hora: bloqueado por idx_maelen_bcp_unique
        conn.commit()

        conteo = conn.execute(
            "SELECT COUNT(*) FROM maelen_transferencias_correo WHERE nro_operacion = ?",
            ("OP-TEST-UNIQUE-999",),
        ).fetchone()[0]

        conn.execute(
            "DELETE FROM maelen_transferencias_correo WHERE nro_operacion = ?",
            ("OP-TEST-UNIQUE-999",),
        )
        conn.execute("DELETE FROM _supabase_outbox WHERE pk1 IN ('MSG-TEST-1', 'MSG-TEST-2')")
        conn.commit()
        return {"candado_unique_ok": conteo == 1, "filas_insertadas_tras_doble_intento": conteo}
    finally:
        conn.close()


if __name__ == "__main__":
    info = init_db()
    prueba = probar_candado_unique_antiduplicados()
    print(json.dumps({**info, **prueba}, indent=2, ensure_ascii=False))
