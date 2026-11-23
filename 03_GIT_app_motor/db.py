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
    "yape_conciliacion_hostal": ("id",),
    "calzado_cache": ("clave_calzado",),
    "sunat_lineas_cache": ("id_linea",),
}

# Tablas caché o pesadas locales que NUNCA suben a Supabase (protección de Egress; viajan por Syncthing si aplica)
TABLAS_SOLO_LOCAL_CACHE = {
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
    Inicializa todas las tablas operativas en 02_DB_sqlite_local/ovalo.db con el 100% de las 651
    columnas homologadas entre Laptop 1, Laptop 2, Laptop 3 y Supabase.
    """
    repair_db_if_malformed()
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS _supabase_outbox (
                tabla      TEXT NOT NULL,
                pk1        TEXT NOT NULL,
                pk2        TEXT NOT NULL DEFAULT '',
                op         TEXT NOT NULL DEFAULT 'UPSERT',
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (tabla, pk1, pk2)
            );
            CREATE TABLE IF NOT EXISTS _supabase_sync_control (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                pulling INTEGER NOT NULL DEFAULT 0
            , last_log_id INTEGER NOT NULL DEFAULT 0, updated_at TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS almacen_movimientos (
                    mov_uuid        TEXT PRIMARY KEY,
                    lote_uuid       TEXT NOT NULL DEFAULT '',
                    id_transaccion  TEXT NOT NULL DEFAULT '',
                    fecha_dmy       TEXT NOT NULL DEFAULT '',
                    producto        TEXT NOT NULL DEFAULT '',
                    cantidad        REAL NOT NULL DEFAULT 0,
                    costo_total     REAL NOT NULL DEFAULT 0,
                    costo_unitario  REAL NOT NULL DEFAULT 0,
                    metodo_pago     TEXT NOT NULL DEFAULT 'N/A',
                    tipo_movimiento TEXT NOT NULL DEFAULT '',
                    nota            TEXT NOT NULL DEFAULT '',
                    sheet_row       INTEGER NOT NULL DEFAULT 0,
                    synced          INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS asistente_movimientos_log (
                    log_uuid          TEXT PRIMARY KEY,
                    fecha_dmy         TEXT NOT NULL DEFAULT '',
                    hora              TEXT NOT NULL DEFAULT '',
                    ts_iso            TEXT NOT NULL DEFAULT '',
                    recepcionista     TEXT NOT NULL DEFAULT '',
                    tipo_interaccion  TEXT NOT NULL DEFAULT '',
                    pedido_original   TEXT NOT NULL DEFAULT '',
                    accion_ejecutada  TEXT NOT NULL DEFAULT '',
                    hab               TEXT NOT NULL DEFAULT '',
                    huesped           TEXT NOT NULL DEFAULT '',
                    monto             REAL NOT NULL DEFAULT 0,
                    estado_anterior   TEXT NOT NULL DEFAULT '{}',
                    estado_nuevo      TEXT NOT NULL DEFAULT '{}',
                    resumen_dueno     TEXT NOT NULL DEFAULT '',
                    equipo            TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS auditoria_audio_noche (
                clip_id              TEXT PRIMARY KEY,
                fecha_turno          TEXT NOT NULL,
                fecha_hora_inicio    TEXT NOT NULL,
                fecha_hora_fin       TEXT NOT NULL,
                duracion_seg         REAL NOT NULL DEFAULT 0,
                archivo_wav          TEXT NOT NULL,
                transcripcion        TEXT NOT NULL DEFAULT '',
                resumen_ia           TEXT NOT NULL DEFAULT '',
                es_atencion_cliente  INTEGER NOT NULL DEFAULT 0,
                hab_detectada        TEXT NOT NULL DEFAULT '',
                dni_detectado        TEXT NOT NULL DEFAULT '',
                huesped_detectado    TEXT NOT NULL DEFAULT '',
                precio_hablado       REAL NOT NULL DEFAULT 0,
                metodo_pago_hablado  TEXT NOT NULL DEFAULT '',
                alerta_fuga_voz      TEXT NOT NULL DEFAULT '',
                estado_cruce         TEXT NOT NULL DEFAULT 'PENDIENTE',
                detalle_cruce        TEXT NOT NULL DEFAULT '',
                estancia_id_match    TEXT NOT NULL DEFAULT '',
                monto_registrado     REAL NOT NULL DEFAULT 0,
                diferencia_soles     REAL NOT NULL DEFAULT 0,
                procesado_ia         INTEGER NOT NULL DEFAULT 0,
                created_at           TEXT NOT NULL DEFAULT ''
            , hablantes_detectados TEXT NOT NULL DEFAULT '', tipo_interaccion TEXT NOT NULL DEFAULT '', incidencia_mantenimiento TEXT NOT NULL DEFAULT '', fuente_transcripcion TEXT NOT NULL DEFAULT '', banderas TEXT NOT NULL DEFAULT '', transcripcion_imou TEXT DEFAULT '', confianza_laptop REAL DEFAULT 0.0, confianza_imou REAL DEFAULT 0.0, fuente_preferida TEXT DEFAULT 'LAPTOP', veredicto_verificador TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS calzado_cache (
                    clave_calzado       TEXT PRIMARY KEY,
                    fila_calzado        INTEGER NOT NULL DEFAULT 0,
                    noche_dmy           TEXT NOT NULL DEFAULT '',
                    hab                 TEXT NOT NULL DEFAULT '',
                    huesped             TEXT NOT NULL DEFAULT '',
                    dni                 TEXT NOT NULL DEFAULT '',
                    fila_est            TEXT NOT NULL DEFAULT '',
                    monto_sunat         REAL NOT NULL DEFAULT 0,
                    comprobantes        TEXT NOT NULL DEFAULT '',
                    monto_aj            REAL NOT NULL DEFAULT 0,
                    yape_ab             REAL NOT NULL DEFAULT 0,
                    efectivo_aa         REAL NOT NULL DEFAULT 0,
                    excedente_efec      REAL NOT NULL DEFAULT 0,
                    yape_bcp            REAL NOT NULL DEFAULT 0,
                    estado_bcp          TEXT NOT NULL DEFAULT '',
                    estado_conciliacion TEXT NOT NULL DEFAULT '',
                    accion              TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS catalogos_local (
                    categoria TEXT NOT NULL,
                    clave     TEXT NOT NULL,
                    valor     TEXT NOT NULL DEFAULT '',
                    orden     INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (categoria, clave)
                );
            CREATE TABLE IF NOT EXISTS compras_tienda_lotes (
                    compra_uuid      TEXT PRIMARY KEY,
                    fecha_dmy        TEXT NOT NULL DEFAULT '',
                    monto_total      REAL NOT NULL DEFAULT 0,
                    monto_desglosado REAL NOT NULL DEFAULT 0,
                    metodo_pago      TEXT NOT NULL DEFAULT 'Efectivo',
                    responsable      TEXT NOT NULL DEFAULT 'ELEIZA',
                    tipo_comp        TEXT NOT NULL DEFAULT 'Boleta',
                    nro_comp         TEXT NOT NULL DEFAULT '',
                    nota             TEXT NOT NULL DEFAULT '',
                    gasto_uuid       TEXT NOT NULL DEFAULT '',
                    estado           TEXT NOT NULL DEFAULT 'PENDIENTE',
                    created_at       TEXT NOT NULL DEFAULT '',
                    updated_at       TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS control_pases (
                    pase_uuid       TEXT PRIMARY KEY,
                    fecha_dmy       TEXT NOT NULL,
                    ymd             INTEGER NOT NULL DEFAULT 0,
                    hora            TEXT NOT NULL DEFAULT '',
                    hab             TEXT NOT NULL DEFAULT '',
                    estancia_uuid   TEXT NOT NULL DEFAULT '',
                    id_estancia     TEXT NOT NULL DEFAULT '',
                    huesped_titular TEXT NOT NULL DEFAULT '',
                    dni_visita      TEXT NOT NULL DEFAULT '',
                    nombre_visita   TEXT NOT NULL DEFAULT '',
                    estado          TEXT NOT NULL DEFAULT 'ACEPTADO',
                    monto           REAL NOT NULL DEFAULT 10.0,
                    motivo_rechazo  TEXT NOT NULL DEFAULT '',
                    equipo          TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL DEFAULT '',
                    synced          INTEGER NOT NULL DEFAULT 0
                );
            CREATE TABLE IF NOT EXISTS decisiones_boleta (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha_iso          TEXT NOT NULL DEFAULT '',
                    estancia_uuid      TEXT NOT NULL DEFAULT '',
                    escenario          TEXT NOT NULL DEFAULT '',
                    opciones_json      TEXT NOT NULL DEFAULT '',
                    sugerida           TEXT NOT NULL DEFAULT '',
                    elegida            TEXT NOT NULL DEFAULT '',
                    receptora_uuid     TEXT NOT NULL DEFAULT '',
                    origen_tienda_json TEXT NOT NULL DEFAULT '',
                    colchon            REAL NOT NULL DEFAULT 0,
                    usuario            TEXT NOT NULL DEFAULT '',
                    created_at         TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS empresas_padron (
                    ruc          TEXT PRIMARY KEY,
                    razon_social TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS estado_sistema (
                    clave      TEXT PRIMARY KEY,
                    valor      TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS estancias (
                    estancia_uuid        TEXT PRIMARY KEY,
                    id_timestamp_str     TEXT UNIQUE NOT NULL,
                    sheet_row            INTEGER NOT NULL DEFAULT 0,
                    parent_uuid          TEXT NOT NULL DEFAULT '',
                    fecha_in_dmy         TEXT NOT NULL DEFAULT '',
                    fecha_out_dmy        TEXT NOT NULL DEFAULT '',
                    ymd_in               INTEGER NOT NULL DEFAULT 0,
                    ymd_out              INTEGER NOT NULL DEFAULT 0,
                    hora_registro        TEXT NOT NULL DEFAULT '',
                    huesped              TEXT NOT NULL DEFAULT '',
                    dni                  TEXT NOT NULL DEFAULT '',
                    sexo                 TEXT NOT NULL DEFAULT 'M',
                    n_acomp              TEXT NOT NULL DEFAULT '',
                    dni_acomp            TEXT NOT NULL DEFAULT '',
                    nac                  TEXT NOT NULL DEFAULT 'Perú',
                    reg                  TEXT NOT NULL DEFAULT 'Lima',
                    mot                  TEXT NOT NULL DEFAULT 'Vacaciones, recreo u ocio',
                    hab                  TEXT NOT NULL DEFAULT '',
                    tipo_hab             TEXT NOT NULL DEFAULT '',
                    visita               TEXT NOT NULL DEFAULT 'Visita N° 1',
                    noches               INTEGER NOT NULL DEFAULT 1,
                    tarifa               REAL NOT NULL DEFAULT 0,
                    adic_snacks          REAL NOT NULL DEFAULT 0,
                    adic_servicios       REAL NOT NULL DEFAULT 0,
                    adic_comp            REAL NOT NULL DEFAULT 0,
                    pago_ini_efec        REAL NOT NULL DEFAULT 0,
                    pago_ini_yape        REAL NOT NULL DEFAULT 0,
                    pago_post_efec       REAL NOT NULL DEFAULT 0,
                    pago_post_yape       REAL NOT NULL DEFAULT 0,
                    pago_visa            REAL NOT NULL DEFAULT 0,
                    deuda_anterior       REAL NOT NULL DEFAULT 0,
                    cuenta_destino       TEXT NOT NULL DEFAULT '',
                    solicita_comp        TEXT NOT NULL DEFAULT 'No',
                    tipo_comp            TEXT NOT NULL DEFAULT '',
                    estado_comp          TEXT NOT NULL DEFAULT '',
                    nro_comp             TEXT NOT NULL DEFAULT '',
                    monto_factura        REAL NOT NULL DEFAULT 0,
                    estado_estancia      TEXT NOT NULL DEFAULT 'En Curso',
                    empresa              TEXT NOT NULL DEFAULT '',
                    proyecto             TEXT NOT NULL DEFAULT '',
                    triple1              TEXT NOT NULL DEFAULT '',
                    triple2              TEXT NOT NULL DEFAULT '',
                    hotel_apoyo          TEXT NOT NULL DEFAULT '',
                    observaciones        TEXT NOT NULL DEFAULT '',
                    nro_op_yape          TEXT NOT NULL DEFAULT '',
                    celular              TEXT NOT NULL DEFAULT '',
                    ruc                  TEXT NOT NULL DEFAULT '',
                    excedente_efec_sunat REAL NOT NULL DEFAULT 0,
                    dirty                INTEGER NOT NULL DEFAULT 0,
                    updated_at           TEXT NOT NULL DEFAULT ''
                , tipo_adicional TEXT NOT NULL DEFAULT '', unificado_en_uuid TEXT NOT NULL DEFAULT '', opcion_decision TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS gastos (
                    gasto_uuid      TEXT PRIMARY KEY,
                    id_ts_str       TEXT UNIQUE NOT NULL,
                    fecha_gasto_iso TEXT NOT NULL DEFAULT '',
                    fecha_gasto_dmy TEXT NOT NULL DEFAULT '',
                    hora_registro   TEXT NOT NULL DEFAULT '',
                    descripcion     TEXT NOT NULL DEFAULT '',
                    monto           REAL NOT NULL DEFAULT 0,
                    categoria       TEXT NOT NULL DEFAULT '',
                    metodo_pago     TEXT NOT NULL DEFAULT '',
                    tipo_comp       TEXT NOT NULL DEFAULT '',
                    nro_comp        TEXT NOT NULL DEFAULT '',
                    responsable     TEXT NOT NULL DEFAULT 'Admin',
                    sheet_row       INTEGER NOT NULL DEFAULT 0,
                    synced          INTEGER NOT NULL DEFAULT 0
                , personal TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS habitaciones_colores (
                    hab         TEXT NOT NULL,
                    fecha_dmy   TEXT NOT NULL,
                    color_hex   TEXT NOT NULL,
                    nota        TEXT NOT NULL DEFAULT '',
                    updated_at  TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (hab, fecha_dmy)
                );
            CREATE TABLE IF NOT EXISTS habitaciones_config (
                    hab                 TEXT PRIMARY KEY,
                    orden               INTEGER NOT NULL DEFAULT 0,
                    tipo                TEXT NOT NULL DEFAULT '',
                    precio_ref          TEXT NOT NULL DEFAULT '',
                    color_personalizado TEXT NOT NULL DEFAULT '',
                    dirty_color         INTEGER NOT NULL DEFAULT 0
                );
            CREATE TABLE IF NOT EXISTS historial_movimientos (
                    mov_id       TEXT PRIMARY KEY,
                    fecha_dmy    TEXT NOT NULL DEFAULT '',
                    hora         TEXT NOT NULL DEFAULT '',
                    ts_iso       TEXT NOT NULL DEFAULT '',
                    accion       TEXT NOT NULL DEFAULT '',
                    hab          TEXT NOT NULL DEFAULT '',
                    huesped      TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT '',
                    resumen      TEXT NOT NULL DEFAULT '',
                    detalle_json TEXT NOT NULL DEFAULT '[]',
                    monto        REAL NOT NULL DEFAULT 0
                );
            CREATE TABLE IF NOT EXISTS horario_auditoria (
                    audit_uuid     TEXT PRIMARY KEY,
                    empleado       TEXT NOT NULL DEFAULT '',
                    fecha_dmy      TEXT NOT NULL DEFAULT '',
                    campo          TEXT NOT NULL DEFAULT '',
                    valor_anterior TEXT NOT NULL DEFAULT '',
                    valor_nuevo    TEXT NOT NULL DEFAULT '',
                    editado_ts     TEXT NOT NULL DEFAULT '',
                    equipo         TEXT NOT NULL DEFAULT '',
                    synced         INTEGER NOT NULL DEFAULT 0
                );
            CREATE TABLE IF NOT EXISTS horario_liquidaciones (
                    liq_uuid     TEXT PRIMARY KEY,
                    empleado     TEXT NOT NULL DEFAULT '',
                    desde_dmy    TEXT NOT NULL DEFAULT '',
                    hasta_dmy    TEXT NOT NULL DEFAULT '',
                    neto         REAL NOT NULL DEFAULT 0,
                    yape         REAL NOT NULL DEFAULT 0,
                    efectivo     REAL NOT NULL DEFAULT 0,
                    detalle_json TEXT NOT NULL DEFAULT '{}',
                    created_at   TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT ''
                , a_efec REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS horario_registros (
                    empleado     TEXT NOT NULL,
                    fecha_dmy    TEXT NOT NULL,
                    ymd          INTEGER NOT NULL DEFAULT 0,
                    e1           TEXT NOT NULL DEFAULT '',
                    s1           TEXT NOT NULL DEFAULT '',
                    e2           TEXT NOT NULL DEFAULT '',
                    s2           TEXT NOT NULL DEFAULT '',
                    estado       TEXT NOT NULL DEFAULT '',
                    extras       TEXT NOT NULL DEFAULT '',
                    extra_noche  INTEGER NOT NULL DEFAULT 0,
                    nota         TEXT NOT NULL DEFAULT '',
                    ult_marca_ts TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT '',
                    synced       INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (empleado, fecha_dmy)
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
            CREATE TABLE IF NOT EXISTS huespedes_padron (
                    dni          TEXT PRIMARY KEY,
                    nombre       TEXT NOT NULL DEFAULT '',
                    sexo         TEXT NOT NULL DEFAULT 'M',
                    nac          TEXT NOT NULL DEFAULT 'Perú',
                    reg          TEXT NOT NULL DEFAULT 'Lima',
                    mot          TEXT NOT NULL DEFAULT 'Vacaciones, recreo u ocio',
                    hab_frec     TEXT NOT NULL DEFAULT '',
                    tarifa_frec  REAL NOT NULL DEFAULT 0,
                    empresa      TEXT NOT NULL DEFAULT '',
                    celular      TEXT NOT NULL DEFAULT '',
                    ruc          TEXT NOT NULL DEFAULT '',
                    visitas      INTEGER NOT NULL DEFAULT 1,
                    updated_at   TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS maelen_transferencias_correo (
                msg_id               TEXT PRIMARY KEY,
                cuenta_correo        TEXT DEFAULT '',
                fecha_operacion      TEXT NOT NULL DEFAULT '',
                hora_operacion       TEXT DEFAULT '',
                tipo_movimiento      TEXT DEFAULT '',
                remitente_o_destino  TEXT NOT NULL DEFAULT '',
                monto                REAL DEFAULT 0,
                nro_operacion        TEXT DEFAULT '',
                cuenta_origen_destino TEXT DEFAULT 'BCP CTA CTE 8023 MAELEN',
                asunto               TEXT DEFAULT '',
                estado_cruce         TEXT DEFAULT 'PENDIENTE',
                id_cruce_recepcion   TEXT DEFAULT '',
                detalle_cruce        TEXT DEFAULT '',
                created_at           TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pagos (
                    pago_id       TEXT PRIMARY KEY,
                    estancia_uuid TEXT NOT NULL DEFAULT '',
                    id_estancia   TEXT NOT NULL DEFAULT '',
                    fecha_iso     TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    huesped       TEXT NOT NULL DEFAULT '',
                    monto         REAL NOT NULL DEFAULT 0,
                    medio_pago    TEXT NOT NULL DEFAULT '',
                    tipo_pago     TEXT NOT NULL DEFAULT '',
                    concepto      TEXT NOT NULL DEFAULT '',
                    nro_operacion TEXT NOT NULL DEFAULT '',
                    sheet_row     INTEGER NOT NULL DEFAULT 0,
                    synced        INTEGER NOT NULL DEFAULT 0
                );
            CREATE TABLE IF NOT EXISTS personal_config (
                    empleado        TEXT PRIMARY KEY,
                    rol             TEXT NOT NULL DEFAULT '',
                    categoria_tienda TEXT NOT NULL DEFAULT '',
                    alias_json      TEXT NOT NULL DEFAULT '[]',
                    sueldo          REAL NOT NULL DEFAULT 0,
                    diario_manana   REAL NOT NULL DEFAULT 50,
                    diario_noche    REAL NOT NULL DEFAULT 55,
                    afp             REAL NOT NULL DEFAULT 0,
                    descanso_dow    INTEGER NOT NULL DEFAULT 0,
                    turno           TEXT NOT NULL DEFAULT 'DIA',
                    orden           INTEGER NOT NULL DEFAULT 0,
                    activo          INTEGER NOT NULL DEFAULT 1
                );
            CREATE TABLE IF NOT EXISTS servicios_detalle (
                    serv_uuid     TEXT PRIMARY KEY,
                    estancia_uuid TEXT NOT NULL DEFAULT '',
                    id_estancia   TEXT NOT NULL DEFAULT '',
                    fecha_dmy     TEXT NOT NULL DEFAULT '',
                    hora          TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    tipo_servicio TEXT NOT NULL DEFAULT '',
                    metodo_pago   TEXT NOT NULL DEFAULT 'Cargar a la Cuenta',
                    monto         REAL NOT NULL DEFAULT 0.0,
                    created_at    TEXT NOT NULL DEFAULT ''
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
            CREATE TABLE IF NOT EXISTS sunat_lineas_cache (
                    id_linea      INTEGER PRIMARY KEY AUTOINCREMENT,
                    nro_cp        TEXT NOT NULL DEFAULT '',
                    tipo          TEXT NOT NULL DEFAULT 'BOLETA',
                    noche_dmy     TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    monto_linea   REAL NOT NULL DEFAULT 0,
                    total_cp      REAL NOT NULL DEFAULT 0,
                    cliente       TEXT NOT NULL DEFAULT '',
                    doc           TEXT NOT NULL DEFAULT '',
                    fecha_emision TEXT NOT NULL DEFAULT '',
                    estado        TEXT NOT NULL DEFAULT 'VIGENTE',
                    descripcion   TEXT NOT NULL DEFAULT ''
                );
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
                updated_at        TEXT DEFAULT (datetime('now', 'localtime')), tipo TEXT NOT NULL DEFAULT '', monto_total REAL DEFAULT 0, actualizado_en TEXT DEFAULT '',
                UNIQUE(tipo_doc, nro_cp)
            );
            CREATE TABLE IF NOT EXISTS sync_queue (
                    queue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    op_uuid      TEXT UNIQUE NOT NULL,
                    op_type      TEXT NOT NULL,
                    entity_uuid  TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    delta_caja   REAL NOT NULL DEFAULT 0,
                    status       TEXT NOT NULL DEFAULT 'PENDING',
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    last_error   TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL DEFAULT '',
                    synced_at    TEXT NOT NULL DEFAULT ''
                );
            CREATE TABLE IF NOT EXISTS tablet_buzon_mejoras (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora      TEXT NOT NULL,
                calificacion    INTEGER NOT NULL DEFAULT 5,
                categoria       TEXT NOT NULL DEFAULT 'General',
                hab             TEXT NOT NULL DEFAULT '',
                comentario      TEXT NOT NULL DEFAULT '',
                huesped_opcional TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tablet_firmas_conformidad (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora      TEXT NOT NULL,
                hab             TEXT NOT NULL DEFAULT '',
                id_estancia     TEXT NOT NULL DEFAULT '',
                huesped         TEXT NOT NULL DEFAULT '',
                dni             TEXT NOT NULL DEFAULT '',
                empresa         TEXT NOT NULL DEFAULT '',
                celular         TEXT NOT NULL DEFAULT '',
                firma_base64    TEXT NOT NULL DEFAULT '',
                observaciones   TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tablet_limpieza_dia (
                hab             TEXT NOT NULL,
                fecha_dmy       TEXT NOT NULL,
                estado_limpieza TEXT NOT NULL DEFAULT 'PENDIENTE',
                nota            TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (hab, fecha_dmy)
            );
            CREATE TABLE IF NOT EXISTS ventas_tienda (
                    venta_uuid    TEXT PRIMARY KEY,
                    lote_uuid     TEXT NOT NULL DEFAULT '',
                    estancia_uuid TEXT NOT NULL DEFAULT '',
                    id_estancia   TEXT NOT NULL DEFAULT '',
                    fecha_iso     TEXT NOT NULL DEFAULT '',
                    fecha_dmy     TEXT NOT NULL DEFAULT '',
                    producto      TEXT NOT NULL DEFAULT '',
                    cantidad      INTEGER NOT NULL DEFAULT 1,
                    metodo_pago   TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    personal      TEXT NOT NULL DEFAULT '',
                    precio_unit   REAL NOT NULL DEFAULT 0,
                    subtotal      REAL NOT NULL DEFAULT 0,
                    nro_op_yape   TEXT NOT NULL DEFAULT '',
                    sheet_row     INTEGER NOT NULL DEFAULT 0,
                    synced        INTEGER NOT NULL DEFAULT 0
                , yape_tienda_usado_en TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS vitrina_local (
                    producto      TEXT PRIMARY KEY,
                    orden         INTEGER NOT NULL DEFAULT 0,
                    stock_sheets  REAL NOT NULL DEFAULT 0,
                    estado_actual TEXT NOT NULL DEFAULT '',
                    h1_cant       TEXT NOT NULL DEFAULT '',
                    h1_est        TEXT NOT NULL DEFAULT '',
                    h2_cant       TEXT NOT NULL DEFAULT '',
                    h2_est        TEXT NOT NULL DEFAULT '',
                    h3_cant       TEXT NOT NULL DEFAULT '',
                    h3_est        TEXT NOT NULL DEFAULT '',
                    updated_at    TEXT NOT NULL DEFAULT ''
                , h4_cant TEXT NOT NULL DEFAULT '', h4_est TEXT NOT NULL DEFAULT '', h5_cant TEXT NOT NULL DEFAULT '', h5_est TEXT NOT NULL DEFAULT '', h6_cant TEXT NOT NULL DEFAULT '', h6_est TEXT NOT NULL DEFAULT '', h7_cant TEXT NOT NULL DEFAULT '', h7_est TEXT NOT NULL DEFAULT '', h8_cant TEXT NOT NULL DEFAULT '', h8_est TEXT NOT NULL DEFAULT '', h9_cant TEXT NOT NULL DEFAULT '', h9_est TEXT NOT NULL DEFAULT '', h10_cant TEXT NOT NULL DEFAULT '', h10_est TEXT NOT NULL DEFAULT '', h11_cant TEXT NOT NULL DEFAULT '', h11_est TEXT NOT NULL DEFAULT '', h12_cant TEXT NOT NULL DEFAULT '', h12_est TEXT NOT NULL DEFAULT '', h13_cant TEXT NOT NULL DEFAULT '', h13_est TEXT NOT NULL DEFAULT '', h14_cant TEXT NOT NULL DEFAULT '', h14_est TEXT NOT NULL DEFAULT '', h15_cant TEXT NOT NULL DEFAULT '', h15_est TEXT NOT NULL DEFAULT '', h16_cant TEXT NOT NULL DEFAULT '', h16_est TEXT NOT NULL DEFAULT '', h17_cant TEXT NOT NULL DEFAULT '', h17_est TEXT NOT NULL DEFAULT '', h18_cant TEXT NOT NULL DEFAULT '', h18_est TEXT NOT NULL DEFAULT '', h19_cant TEXT NOT NULL DEFAULT '', h19_est TEXT NOT NULL DEFAULT '', h20_cant TEXT NOT NULL DEFAULT '', h20_est TEXT NOT NULL DEFAULT '', h21_cant TEXT NOT NULL DEFAULT '', h21_est TEXT NOT NULL DEFAULT '', h22_cant TEXT NOT NULL DEFAULT '', h22_est TEXT NOT NULL DEFAULT '', h23_cant TEXT NOT NULL DEFAULT '', h23_est TEXT NOT NULL DEFAULT '', h24_cant TEXT NOT NULL DEFAULT '', h24_est TEXT NOT NULL DEFAULT '', h25_cant TEXT NOT NULL DEFAULT '', h25_est TEXT NOT NULL DEFAULT '', h26_cant TEXT NOT NULL DEFAULT '', h26_est TEXT NOT NULL DEFAULT '', h27_cant TEXT NOT NULL DEFAULT '', h27_est TEXT NOT NULL DEFAULT '', h28_cant TEXT NOT NULL DEFAULT '', h28_est TEXT NOT NULL DEFAULT '', h29_cant TEXT NOT NULL DEFAULT '', h29_est TEXT NOT NULL DEFAULT '', h30_cant TEXT NOT NULL DEFAULT '', h30_est TEXT NOT NULL DEFAULT '', h31_cant TEXT NOT NULL DEFAULT '', h31_est TEXT NOT NULL DEFAULT '', h32_cant TEXT NOT NULL DEFAULT '', h32_est TEXT NOT NULL DEFAULT '', h33_cant TEXT NOT NULL DEFAULT '', h33_est TEXT NOT NULL DEFAULT '', h34_cant TEXT NOT NULL DEFAULT '', h34_est TEXT NOT NULL DEFAULT '', h35_cant TEXT NOT NULL DEFAULT '', h35_est TEXT NOT NULL DEFAULT '', h36_cant TEXT NOT NULL DEFAULT '', h36_est TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS wsp_recepcion_chats (
                jid                  TEXT PRIMARY KEY,
                nombre               TEXT,
                tipo                 TEXT,
                ultimo_mensaje       TEXT,
                fecha_ultimo_mensaje TEXT,
                no_leidos            INTEGER DEFAULT 0,
                actualizado_en       TEXT DEFAULT (datetime('now', 'localtime'))
            , es_grupo INTEGER DEFAULT 0, ultimo_mensaje_ts INTEGER DEFAULT 0);
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
            , destino_telefono TEXT, cliente_nombre TEXT, nro_comprobante TEXT, texto TEXT, archivo_ruta TEXT);
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
            , mensaje_id TEXT, chat_nombre TEXT, archivo_ruta TEXT, ruc_dni TEXT);
            CREATE TABLE IF NOT EXISTS wsp_recepcion_estado (
      clave          TEXT PRIMARY KEY,
      valor          TEXT,
      actualizado_en TEXT
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
            , id TEXT, chat_jid TEXT, chat_nombre TEXT, remitente_nombre TEXT, desde_recepcion INTEGER DEFAULT 0, timestamp INTEGER, tipo TEXT, archivo_ruta TEXT);
            CREATE TABLE IF NOT EXISTS wsp_resumen_ejecutivo (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha_hora   TEXT NOT NULL,
                    slot_horario TEXT NOT NULL,
                    fuente_ia    TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
            CREATE TABLE IF NOT EXISTS yape_alertas_wsp_5min (
                id_alerta             INTEGER PRIMARY KEY AUTOINCREMENT,
                id_yape               INTEGER UNIQUE,
                fecha_operacion       TEXT NOT NULL DEFAULT '',
                hora_operacion        TEXT NOT NULL DEFAULT '',
                remitente_bcp         TEXT NOT NULL DEFAULT '',
                monto_bcp             REAL DEFAULT 0,
                minutos_transcurridos REAL DEFAULT 0,
                mensaje_wsp           TEXT NOT NULL DEFAULT '',
                telefono_destino      TEXT DEFAULT '964139749',
                estado_envio          TEXT DEFAULT 'EN_COLA',
                id_cola_wsp           TEXT DEFAULT '',
                creado_en             TEXT DEFAULT '',
                enviado_en            TEXT DEFAULT ''
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
            CREATE TABLE IF NOT EXISTS yape_revisor_830_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_ejecucion   TEXT NOT NULL DEFAULT '',
                mes_evaluado      TEXT NOT NULL DEFAULT '',
                tipo_accion       TEXT NOT NULL DEFAULT '',
                id_pago           TEXT DEFAULT '',
                id_yape           INTEGER DEFAULT 0,
                habitacion        TEXT DEFAULT '',
                antes_json        TEXT DEFAULT '',
                despues_json      TEXT DEFAULT '',
                explicacion       TEXT DEFAULT '',
                estado_resolucion TEXT DEFAULT '',
                wsp_enviado       INTEGER DEFAULT 0
            );
            INSERT OR IGNORE INTO _supabase_sync_control (id, pulling) VALUES (1, 0);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_maelen_bcp_unique ON maelen_transferencias_correo (fecha_operacion, hora_operacion, monto, nro_operacion, remitente_o_destino);
            """
        )
        conn.commit()

        # Migrar aprendizaje_compartido_hostal si fue creada con id INTEGER PRIMARY KEY en vez de TEXT PRIMARY KEY
        try:
            ap_cols = conn.execute("PRAGMA table_info(aprendizaje_compartido_hostal)").fetchall()
            id_col = next((c for c in ap_cols if c[1] == "id"), None)
            if id_col and "INT" in str(id_col[2] or "").upper():
                conn.execute("DROP TABLE IF EXISTS aprendizaje_compartido_hostal;")
                conn.execute(
                    """
                    CREATE TABLE aprendizaje_compartido_hostal (
                        id               TEXT PRIMARY KEY,
                        fecha_hora       TEXT DEFAULT (datetime('now', 'localtime')),
                        origen_equipo    TEXT DEFAULT '',
                        actor            TEXT DEFAULT '',
                        categoria        TEXT DEFAULT '',
                        pregunta_o_evento TEXT DEFAULT '',
                        patron_detectado TEXT DEFAULT '',
                        regla_aprendida  TEXT DEFAULT '',
                        updated_at       TEXT DEFAULT (datetime('now', 'localtime')),
                        fecha            TEXT DEFAULT '',
                        origen_nodo      TEXT DEFAULT '',
                        titulo           TEXT DEFAULT '',
                        contenido        TEXT DEFAULT ''
                    );
                    """
                )
                conn.commit()
        except Exception:
            pass

        # Homologación 100% de las 651 columnas (Master SQLite + Supabase) para cualquier base existente o nueva
        _estandar_cols = [
            [
                        "_supabase_outbox",
                        "\"tabla\" TEXT DEFAULT ''"
            ],
            [
                        "_supabase_outbox",
                        "\"pk1\" TEXT DEFAULT ''"
            ],
            [
                        "_supabase_outbox",
                        "\"pk2\" TEXT DEFAULT ''"
            ],
            [
                        "_supabase_outbox",
                        "\"op\" TEXT DEFAULT ''"
            ],
            [
                        "_supabase_outbox",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "_supabase_sync_control",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "_supabase_sync_control",
                        "\"pulling\" INTEGER DEFAULT 0"
            ],
            [
                        "_supabase_sync_control",
                        "\"last_log_id\" INTEGER DEFAULT 0"
            ],
            [
                        "_supabase_sync_control",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"mov_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"lote_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"id_transaccion\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"producto\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"cantidad\" REAL DEFAULT 0"
            ],
            [
                        "almacen_movimientos",
                        "\"costo_total\" REAL DEFAULT 0"
            ],
            [
                        "almacen_movimientos",
                        "\"costo_unitario\" REAL DEFAULT 0"
            ],
            [
                        "almacen_movimientos",
                        "\"metodo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"tipo_movimiento\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"nota\" TEXT DEFAULT ''"
            ],
            [
                        "almacen_movimientos",
                        "\"sheet_row\" INTEGER DEFAULT 0"
            ],
            [
                        "almacen_movimientos",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"id\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"origen_equipo\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"actor\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"pregunta_o_evento\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"patron_detectado\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"regla_aprendida\" TEXT DEFAULT ''"
            ],
            [
                        "aprendizaje_compartido_hostal",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"log_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"hora\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"ts_iso\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"recepcionista\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"tipo_interaccion\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"pedido_original\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"accion_ejecutada\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "asistente_movimientos_log",
                        "\"estado_anterior\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"estado_nuevo\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"resumen_dueno\" TEXT DEFAULT ''"
            ],
            [
                        "asistente_movimientos_log",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"clip_id\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"fecha_turno\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"fecha_hora_inicio\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"fecha_hora_fin\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"duracion_seg\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"archivo_wav\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"transcripcion\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"resumen_ia\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"es_atencion_cliente\" INTEGER DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"hab_detectada\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"dni_detectado\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"huesped_detectado\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"precio_hablado\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"metodo_pago_hablado\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"alerta_fuga_voz\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"estado_cruce\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"detalle_cruce\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"estancia_id_match\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"monto_registrado\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"diferencia_soles\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"procesado_ia\" INTEGER DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"hablantes_detectados\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"tipo_interaccion\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"incidencia_mantenimiento\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"fuente_transcripcion\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"banderas\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"transcripcion_imou\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"confianza_laptop\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"confianza_imou\" REAL DEFAULT 0"
            ],
            [
                        "auditoria_audio_noche",
                        "\"fuente_preferida\" TEXT DEFAULT ''"
            ],
            [
                        "auditoria_audio_noche",
                        "\"veredicto_verificador\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"clave_calzado\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"fila_calzado\" INTEGER DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"noche_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"dni\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"fila_est\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"monto_sunat\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"comprobantes\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"monto_aj\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"yape_ab\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"efectivo_aa\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"excedente_efec\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"yape_bcp\" REAL DEFAULT 0"
            ],
            [
                        "calzado_cache",
                        "\"estado_bcp\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"estado_conciliacion\" TEXT DEFAULT ''"
            ],
            [
                        "calzado_cache",
                        "\"accion\" TEXT DEFAULT ''"
            ],
            [
                        "catalogos_local",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "catalogos_local",
                        "\"clave\" TEXT DEFAULT ''"
            ],
            [
                        "catalogos_local",
                        "\"valor\" TEXT DEFAULT ''"
            ],
            [
                        "catalogos_local",
                        "\"orden\" INTEGER DEFAULT 0"
            ],
            [
                        "compras_tienda_lotes",
                        "\"compra_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"monto_total\" REAL DEFAULT 0"
            ],
            [
                        "compras_tienda_lotes",
                        "\"monto_desglosado\" REAL DEFAULT 0"
            ],
            [
                        "compras_tienda_lotes",
                        "\"metodo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"responsable\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"tipo_comp\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"nro_comp\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"nota\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"gasto_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "compras_tienda_lotes",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"pase_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"ymd\" INTEGER DEFAULT 0"
            ],
            [
                        "control_pases",
                        "\"hora\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"id_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"huesped_titular\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"dni_visita\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"nombre_visita\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "control_pases",
                        "\"motivo_rechazo\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "control_pases",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "decisiones_boleta",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "decisiones_boleta",
                        "\"fecha_iso\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"escenario\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"opciones_json\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"sugerida\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"elegida\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"receptora_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"origen_tienda_json\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"colchon\" REAL DEFAULT 0"
            ],
            [
                        "decisiones_boleta",
                        "\"usuario\" TEXT DEFAULT ''"
            ],
            [
                        "decisiones_boleta",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "empresas_padron",
                        "\"ruc\" TEXT DEFAULT ''"
            ],
            [
                        "empresas_padron",
                        "\"razon_social\" TEXT DEFAULT ''"
            ],
            [
                        "empresas_padron",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "estado_sistema",
                        "\"clave\" TEXT DEFAULT ''"
            ],
            [
                        "estado_sistema",
                        "\"valor\" TEXT DEFAULT ''"
            ],
            [
                        "estado_sistema",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"id_timestamp_str\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"sheet_row\" INTEGER DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"parent_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"fecha_in_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"fecha_out_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"ymd_in\" INTEGER DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"ymd_out\" INTEGER DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"hora_registro\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"dni\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"sexo\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"n_acomp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"dni_acomp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"nac\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"reg\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"mot\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"tipo_hab\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"visita\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"noches\" INTEGER DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"tarifa\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"adic_snacks\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"adic_servicios\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"adic_comp\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"pago_ini_efec\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"pago_ini_yape\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"pago_post_efec\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"pago_post_yape\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"pago_visa\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"deuda_anterior\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"cuenta_destino\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"solicita_comp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"tipo_comp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"estado_comp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"nro_comp\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"monto_factura\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"estado_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"empresa\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"proyecto\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"triple1\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"triple2\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"hotel_apoyo\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"observaciones\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"nro_op_yape\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"celular\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"ruc\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"excedente_efec_sunat\" REAL DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"dirty\" INTEGER DEFAULT 0"
            ],
            [
                        "estancias",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"tipo_adicional\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"unificado_en_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "estancias",
                        "\"opcion_decision\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"gasto_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"id_ts_str\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"fecha_gasto_iso\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"fecha_gasto_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"hora_registro\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"descripcion\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "gastos",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"metodo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"tipo_comp\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"nro_comp\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"responsable\" TEXT DEFAULT ''"
            ],
            [
                        "gastos",
                        "\"sheet_row\" INTEGER DEFAULT 0"
            ],
            [
                        "gastos",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "gastos",
                        "\"personal\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_colores",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_colores",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_colores",
                        "\"color_hex\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_colores",
                        "\"nota\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_colores",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_config",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_config",
                        "\"orden\" INTEGER DEFAULT 0"
            ],
            [
                        "habitaciones_config",
                        "\"tipo\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_config",
                        "\"precio_ref\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_config",
                        "\"color_personalizado\" TEXT DEFAULT ''"
            ],
            [
                        "habitaciones_config",
                        "\"dirty_color\" INTEGER DEFAULT 0"
            ],
            [
                        "historial_movimientos",
                        "\"mov_id\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"hora\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"ts_iso\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"accion\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"resumen\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"detalle_json\" TEXT DEFAULT ''"
            ],
            [
                        "historial_movimientos",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "horario_auditoria",
                        "\"audit_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"empleado\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"campo\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"valor_anterior\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"valor_nuevo\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"editado_ts\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "horario_auditoria",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "horario_liquidaciones",
                        "\"liq_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"empleado\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"desde_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"hasta_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"neto\" REAL DEFAULT 0"
            ],
            [
                        "horario_liquidaciones",
                        "\"yape\" REAL DEFAULT 0"
            ],
            [
                        "horario_liquidaciones",
                        "\"efectivo\" REAL DEFAULT 0"
            ],
            [
                        "horario_liquidaciones",
                        "\"detalle_json\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "horario_liquidaciones",
                        "\"a_efec\" REAL DEFAULT 0"
            ],
            [
                        "horario_registros",
                        "\"empleado\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"ymd\" INTEGER DEFAULT 0"
            ],
            [
                        "horario_registros",
                        "\"e1\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"s1\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"e2\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"s2\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"extras\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"extra_noche\" INTEGER DEFAULT 0"
            ],
            [
                        "horario_registros",
                        "\"nota\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"ult_marca_ts\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"equipo\" TEXT DEFAULT ''"
            ],
            [
                        "horario_registros",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"id_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"jid\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"origen_chat\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"tipo_archivo\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"nombre_archivo\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"ruta_local\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"metodo_extraccion\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"ruc_emisor\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"serie_numero\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"monto_detectado\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"operacion_nro\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"texto_extraido\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"fecha_procesado\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"dni\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"nombre\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"sexo\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"nac\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"reg\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"mot\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"hab_frec\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"tarifa_frec\" REAL DEFAULT 0"
            ],
            [
                        "huespedes_padron",
                        "\"empresa\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"celular\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"ruc\" TEXT DEFAULT ''"
            ],
            [
                        "huespedes_padron",
                        "\"visitas\" INTEGER DEFAULT 0"
            ],
            [
                        "huespedes_padron",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"msg_id\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"cuenta_correo\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"fecha_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"hora_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"tipo_movimiento\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"remitente_o_destino\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"nro_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"cuenta_origen_destino\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"asunto\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"estado_cruce\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"id_cruce_recepcion\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"detalle_cruce\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"pago_id\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"id_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"fecha_iso\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "pagos",
                        "\"medio_pago\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"tipo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"concepto\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"nro_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "pagos",
                        "\"sheet_row\" INTEGER DEFAULT 0"
            ],
            [
                        "pagos",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"empleado\" TEXT DEFAULT ''"
            ],
            [
                        "personal_config",
                        "\"rol\" TEXT DEFAULT ''"
            ],
            [
                        "personal_config",
                        "\"categoria_tienda\" TEXT DEFAULT ''"
            ],
            [
                        "personal_config",
                        "\"alias_json\" TEXT DEFAULT ''"
            ],
            [
                        "personal_config",
                        "\"sueldo\" REAL DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"diario_manana\" REAL DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"diario_noche\" REAL DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"afp\" REAL DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"descanso_dow\" INTEGER DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"turno\" TEXT DEFAULT ''"
            ],
            [
                        "personal_config",
                        "\"orden\" INTEGER DEFAULT 0"
            ],
            [
                        "personal_config",
                        "\"activo\" INTEGER DEFAULT 0"
            ],
            [
                        "servicios_detalle",
                        "\"serv_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"id_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"hora\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"tipo_servicio\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"metodo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "servicios_detalle",
                        "\"monto\" REAL DEFAULT 0"
            ],
            [
                        "servicios_detalle",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"periodo_mes\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"fecha_emision\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"tipo_cp\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"serie_numero\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"ruc_proveedor\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"razon_social\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"base_imponible\" REAL DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"igv\" REAL DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"monto_total\" REAL DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"moneda\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"estado_respaldo\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"estado_bancarizacion\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"observacion\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"origen_excel\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"id_linea\" INTEGER DEFAULT 0"
            ],
            [
                        "sunat_lineas_cache",
                        "\"nro_cp\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"tipo\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"noche_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"monto_linea\" REAL DEFAULT 0"
            ],
            [
                        "sunat_lineas_cache",
                        "\"total_cp\" REAL DEFAULT 0"
            ],
            [
                        "sunat_lineas_cache",
                        "\"cliente\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"doc\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"fecha_emision\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_lineas_cache",
                        "\"descripcion\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "sunat_ventas",
                        "\"periodo_mes\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"tipo_doc\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"nro_cp\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"serie\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"numero\" INTEGER DEFAULT 0"
            ],
            [
                        "sunat_ventas",
                        "\"fecha_emision\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"receptor_doc_tipo\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"receptor_doc\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"receptor_nombre\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"moneda\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"total\" REAL DEFAULT 0"
            ],
            [
                        "sunat_ventas",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"resumen_items\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"items_json\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"origen_json\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"tipo\" TEXT DEFAULT ''"
            ],
            [
                        "sunat_ventas",
                        "\"monto_total\" REAL DEFAULT 0"
            ],
            [
                        "sunat_ventas",
                        "\"actualizado_en\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"queue_id\" INTEGER DEFAULT 0"
            ],
            [
                        "sync_queue",
                        "\"op_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"op_type\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"entity_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"payload_json\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"delta_caja\" REAL DEFAULT 0"
            ],
            [
                        "sync_queue",
                        "\"status\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"attempts\" INTEGER DEFAULT 0"
            ],
            [
                        "sync_queue",
                        "\"last_error\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"created_at\" TEXT DEFAULT ''"
            ],
            [
                        "sync_queue",
                        "\"synced_at\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"calificacion\" INTEGER DEFAULT 0"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"comentario\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_buzon_mejoras",
                        "\"huesped_opcional\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"id_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"huesped\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"dni\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"empresa\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"celular\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"firma_base64\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_firmas_conformidad",
                        "\"observaciones\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_limpieza_dia",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_limpieza_dia",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_limpieza_dia",
                        "\"estado_limpieza\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_limpieza_dia",
                        "\"nota\" TEXT DEFAULT ''"
            ],
            [
                        "tablet_limpieza_dia",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"venta_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"lote_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"id_estancia\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"fecha_iso\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"fecha_dmy\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"producto\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"cantidad\" INTEGER DEFAULT 0"
            ],
            [
                        "ventas_tienda",
                        "\"metodo_pago\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"hab\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"personal\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"precio_unit\" REAL DEFAULT 0"
            ],
            [
                        "ventas_tienda",
                        "\"subtotal\" REAL DEFAULT 0"
            ],
            [
                        "ventas_tienda",
                        "\"nro_op_yape\" TEXT DEFAULT ''"
            ],
            [
                        "ventas_tienda",
                        "\"sheet_row\" INTEGER DEFAULT 0"
            ],
            [
                        "ventas_tienda",
                        "\"synced\" INTEGER DEFAULT 0"
            ],
            [
                        "ventas_tienda",
                        "\"yape_tienda_usado_en\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"producto\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"orden\" INTEGER DEFAULT 0"
            ],
            [
                        "vitrina_local",
                        "\"stock_sheets\" REAL DEFAULT 0"
            ],
            [
                        "vitrina_local",
                        "\"estado_actual\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h1_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h1_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h2_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h2_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h3_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h3_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h4_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h4_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h5_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h5_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h6_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h6_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h7_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h7_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h8_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h8_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h9_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h9_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h10_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h10_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h11_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h11_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h12_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h12_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h13_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h13_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h14_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h14_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h15_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h15_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h16_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h16_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h17_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h17_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h18_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h18_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h19_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h19_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h20_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h20_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h21_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h21_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h22_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h22_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h23_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h23_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h24_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h24_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h25_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h25_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h26_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h26_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h27_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h27_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h28_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h28_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h29_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h29_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h30_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h30_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h31_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h31_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h32_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h32_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h33_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h33_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h34_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h34_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h35_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h35_est\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h36_cant\" TEXT DEFAULT ''"
            ],
            [
                        "vitrina_local",
                        "\"h36_est\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"nombre\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"tipo\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"ultimo_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"fecha_ultimo_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"no_leidos\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"actualizado_en\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"es_grupo\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_chats",
                        "\"ultimo_mensaje_ts\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"telefono_o_jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"ruta_adjunto\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"nombre_adjunto\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"estado\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"detalle_error\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"creado_en\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"enviado_en\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"destino_telefono\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"cliente_nombre\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"nro_comprobante\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"texto\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_cola_envio",
                        "\"archivo_ruta\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"id_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"tipo_archivo\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"nombre_archivo\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"ruta_local\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"metodo_extraccion\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"categoria\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"ruc_emisor\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"serie_numero\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"monto_detectado\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"operacion_nro\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"texto_extraido\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"fecha_procesado\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"mensaje_id\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"chat_nombre\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"archivo_ruta\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_documentos",
                        "\"ruc_dni\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_estado",
                        "\"clave\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_estado",
                        "\"valor\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_estado",
                        "\"actualizado_en\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"id_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"remitente_jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"nombre_emisor\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"es_mio\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"timestamp_unix\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"tipo_mensaje\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"texto\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"tiene_archivo\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"ruta_archivo\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"creado_en\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"id\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"chat_jid\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"chat_nombre\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"remitente_nombre\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"desde_recepcion\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"timestamp\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"tipo\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_recepcion_mensajes",
                        "\"archivo_ruta\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_resumen_ejecutivo",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "wsp_resumen_ejecutivo",
                        "\"fecha_hora\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_resumen_ejecutivo",
                        "\"slot_horario\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_resumen_ejecutivo",
                        "\"fuente_ia\" TEXT DEFAULT ''"
            ],
            [
                        "wsp_resumen_ejecutivo",
                        "\"payload_json\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"id_alerta\" INTEGER DEFAULT 0"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"id_yape\" INTEGER DEFAULT 0"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"fecha_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"hora_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"remitente_bcp\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"monto_bcp\" REAL DEFAULT 0"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"minutos_transcurridos\" REAL DEFAULT 0"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"mensaje_wsp\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"telefono_destino\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"estado_envio\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"id_cola_wsp\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"creado_en\" TEXT DEFAULT ''"
            ],
            [
                        "yape_alertas_wsp_5min",
                        "\"enviado_en\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"fecha_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"hora_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"remitente_bcp\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"monto_bcp\" REAL DEFAULT 0"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"nro_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"id_estancia_calzada\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"hab_calzada\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"huesped_calzado\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"estado_auditoria\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"observacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_conciliacion_hostal",
                        "\"updated_at\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"id\" INTEGER DEFAULT 0"
            ],
            [
                        "yape_revisor_830_log",
                        "\"fecha_ejecucion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"mes_evaluado\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"tipo_accion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"id_pago\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"id_yape\" INTEGER DEFAULT 0"
            ],
            [
                        "yape_revisor_830_log",
                        "\"habitacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"antes_json\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"despues_json\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"explicacion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"estado_resolucion\" TEXT DEFAULT ''"
            ],
            [
                        "yape_revisor_830_log",
                        "\"wsp_enviado\" INTEGER DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"archivo_respaldo\" TEXT DEFAULT ''"
            ],
            [
                        "sire_compras_hostal",
                        "\"requiere_bancarizacion\" INTEGER DEFAULT 0"
            ],
            [
                        "sire_compras_hostal",
                        "\"nro_operacion_bcp\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"mensaje_id\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"archivo_ruta\" TEXT DEFAULT ''"
            ],
            [
                        "hostal_documentos_ocr",
                        "\"procesado_en\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"fecha_correo\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"remitente\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"banco_origen\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"tipo_operacion\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"titular_destino\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"cuenta_destino\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"ordenante\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"moneda\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"fecha_hora_bcp\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"mensaje_cliente\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"cuerpo_resumen\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"usado_en_estancia_uuid\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"usado_en_hab\" TEXT DEFAULT ''"
            ],
            [
                        "maelen_transferencias_correo",
                        "\"usado_fecha_iso\" TEXT DEFAULT ''"
            ]
]
        existing_cols_map = {}
        for tbl_name, col_def in _estandar_cols:
            if tbl_name not in existing_cols_map:
                try:
                    existing_cols_map[tbl_name] = {r[1] for r in conn.execute(f'PRAGMA table_info("{tbl_name}")').fetchall()}
                except Exception:
                    existing_cols_map[tbl_name] = set()
            col_name = col_def.split()[0].strip('"')
            if col_name not in existing_cols_map[tbl_name]:
                try:
                    conn.execute(f'ALTER TABLE "{tbl_name}" ADD COLUMN {col_def};')
                    existing_cols_map[tbl_name].add(col_name)
                except sqlite3.OperationalError:
                    pass

        for t_cache in TABLAS_SOLO_LOCAL_CACHE:
            conn.execute("DELETE FROM _supabase_outbox WHERE tabla = ?", (t_cache,))

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
