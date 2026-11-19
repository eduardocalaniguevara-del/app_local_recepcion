#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Puente Bidireccional de WhatsApp vía Google Sheets de Prueba (`2026_Prueba_Ovalo`)
para el Entorno de Prueba (`:8082`) — 0% Supabase (Decisión D3).

Flujo:
  1. Laptop 2 (Recepción — Rol EMISOR_L2):
     - Detecta automáticamente `C:\\Recepcion_Ovalo_Local\\data\\ovalo_sistema.db` con mensajes reales.
     - Lee en modo ESTRICTAMENTE SOLO LECTURA (`mode=ro` + `PRAGMA query_only=1`) las tablas
       `wsp_recepcion_estado`, `wsp_recepcion_chats`, `wsp_recepcion_mensajes` y `wsp_recepcion_documentos`.
     - Las refleja en su propia SQLite de prueba (`C:\\ovalo_mariategui_prueba\\data\\ovalo_sistema.db`).
     - Las publica en las 4 pestañas espejo de `2026_Prueba_Ovalo` (`WSP_Estado`, `WSP_Chats`,
       `WSP_Mensajes`, `WSP_Documentos`) usando `RAW` (sin riesgo de fórmulas con `+51` o `=`).

  2. Laptop 1 (Administración — Rol RECEPTOR_L1):
     - Lee en 1 sola llamada `values_batch_get` las 4 pestañas `WSP_*` desde `2026_Prueba_Ovalo`.
     - Guarda/actualiza (UPSERT híbrido) esos datos en `C:\\ovalo_mariategui_prueba\\data\\ovalo_sistema.db`.
     - Deja ambas SQLite de Prueba (`L2 :8082` y `L1 :8082`) 100% enlazadas con los mismos mensajes.

Candados Duros de Seguridad:
  - Solo opera si `MODO_PRUEBA == True` y `SPREADSHEET_ID == 1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE`.
  - JAMÁS escribe en la Hoja Operativa (`1KudqxM1...`), Terrazas ni en la SQLite Operativa (`:8080`).
  - JAMÁS toca Supabase ni envía mensajes de WhatsApp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import gspread
from google.oauth2.service_account import Credentials

import config as _cfg
from config import (
    HOJA_PRUEBA_HABILITADA,
    MODO_PRUEBA,
    SCOPES,
    SPREADSHEET_ID,
    _IDS_OPERATIVOS_PROHIBIDOS_EN_PRUEBA,
    obtener_ruta_credenciales,
)
from database import get_connection

ID_HOJA_PRUEBA_PERMITIDA = "1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE"

TAB_WSP_ESTADO = "WSP_Estado"
TAB_WSP_CHATS = "WSP_Chats"
TAB_WSP_MENSAJES = "WSP_Mensajes"
TAB_WSP_DOCUMENTOS = "WSP_Documentos"

TABS_WSP_PRUEBA = (
    TAB_WSP_ESTADO,
    TAB_WSP_CHATS,
    TAB_WSP_MENSAJES,
    TAB_WSP_DOCUMENTOS,
)

HEADERS_ESTADO = ["clave", "valor", "actualizado_en", "origen_equipo"]
HEADERS_CHATS = [
    "jid",
    "nombre",
    "tipo",
    "es_grupo",
    "ultimo_mensaje",
    "fecha_ultimo_mensaje",
    "ultimo_mensaje_ts",
    "no_leidos",
    "actualizado_en",
]
HEADERS_MENSAJES = [
    "id_mensaje",
    "jid",
    "chat_nombre",
    "remitente_jid",
    "nombre_emisor",
    "es_mio",
    "timestamp_unix",
    "fecha_hora",
    "tipo_mensaje",
    "texto",
    "tiene_archivo",
    "ruta_archivo",
    "creado_en",
]
HEADERS_DOCUMENTOS = [
    "id_mensaje",
    "jid",
    "chat_nombre",
    "fecha_hora",
    "tipo_archivo",
    "nombre_archivo",
    "ruta_local",
    "metodo_extraccion",
    "categoria",
    "ruc_emisor",
    "serie_numero",
    "monto_detectado",
    "operacion_nro",
    "texto_extraido",
    "fecha_procesado",
]

MAX_MENSAJES_SHEETS = 2000
MAX_CHATS_SHEETS = 500
MAX_DOCS_SHEETS = 500
MAX_CELL_CHARS = 4000
INTERVALO_SYNC_AUTO_SEG = 60.0

_bridge_lock = threading.Lock()
_gc_client: Optional[gspread.Client] = None
_sh_prueba: Optional[gspread.Spreadsheet] = None
_last_sync_ts: float = 0.0
_last_push_fingerprint: str = ""
_last_pull_fingerprint: str = ""
_tablas_locales_listas: bool = False


# ---------------------------------------------------------------------------
# Utilidades y Candados de Seguridad
# ---------------------------------------------------------------------------
def _s(v: Any, max_len: int = MAX_CELL_CHARS) -> str:
    if v is None:
        return ""
    txt = str(v)
    if len(txt) > max_len:
        return txt[: max_len - 3] + "..."
    return txt


def _i(v: Any, default: int = 0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(float(str(v).strip()))
    except Exception:
        return default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(str(v).replace(",", "").strip())
    except Exception:
        return default


def _verificar_candado_prueba() -> None:
    if not MODO_PRUEBA:
        raise RuntimeError("⛔ wsp_sheets_bridge solo opera en MODO_PRUEBA (:8082).")
    if not HOJA_PRUEBA_HABILITADA:
        raise RuntimeError("⛔ HOJA_PRUEBA_HABILITADA está desactivada.")
    if SPREADSHEET_ID in _IDS_OPERATIVOS_PROHIBIDOS_EN_PRUEBA:
        raise RuntimeError(f"⛔ CANDADO: SPREADSHEET_ID ({SPREADSHEET_ID}) es una Hoja Operativa prohibida.")
    if SPREADSHEET_ID != ID_HOJA_PRUEBA_PERMITIDA:
        raise RuntimeError(f"⛔ CANDADO: SPREADSHEET_ID ({SPREADSHEET_ID}) no coincide con 2026_Prueba_Ovalo.")


def _abrir_hoja_prueba(forzar_reconexion: bool = False) -> gspread.Spreadsheet:
    global _gc_client, _sh_prueba
    _verificar_candado_prueba()
    if not forzar_reconexion and _sh_prueba is not None and getattr(_sh_prueba, "id", None) == ID_HOJA_PRUEBA_PERMITIDA:
        return _sh_prueba
    creds_path = obtener_ruta_credenciales()
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    _gc_client = gspread.authorize(creds)
    try:
        _gc_client.set_timeout(25)
    except Exception:
        pass
    sh = _gc_client.open_by_key(ID_HOJA_PRUEBA_PERMITIDA)
    if "prueba" not in str(sh.title or "").lower():
        raise RuntimeError(f"⛔ CANDADO: La hoja '{sh.title}' no contiene 'Prueba' en su título.")
    _sh_prueba = sh
    return sh


def _asegurar_pestanas_wsp(sh: gspread.Spreadsheet) -> Dict[str, gspread.Worksheet]:
    """Verifica o crea las 4 pestañas WSP_* en `2026_Prueba_Ovalo` con sus encabezados."""
    specs = {
        TAB_WSP_ESTADO: (HEADERS_ESTADO, 50),
        TAB_WSP_CHATS: (HEADERS_CHATS, 600),
        TAB_WSP_MENSAJES: (HEADERS_MENSAJES, 2500),
        TAB_WSP_DOCUMENTOS: (HEADERS_DOCUMENTOS, 600),
    }
    existentes = {w.title: w for w in sh.worksheets()}
    resultado: Dict[str, gspread.Worksheet] = {}
    for nombre, (headers, filas_init) in specs.items():
        ws = existentes.get(nombre)
        if ws is None:
            ws = sh.add_worksheet(title=nombre, rows=filas_init, cols=len(headers) + 1)
            ws.update(range_name="A1", values=[headers], value_input_option="RAW")
            time.sleep(0.25)
        resultado[nombre] = ws
    return resultado


# ---------------------------------------------------------------------------
# Esquema SQLite Híbrido Local (compatible 100% con wsp_inteligencia.py y db.js)
# ---------------------------------------------------------------------------
def _ensure_column(conn: sqlite3.Connection, table: str, col_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
    except sqlite3.OperationalError:
        pass


def asegurar_tablas_wsp_prueba() -> None:
    global _tablas_locales_listas
    if _tablas_locales_listas:
        return
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wsp_recepcion_chats (
              jid                  TEXT PRIMARY KEY,
              nombre               TEXT,
              tipo                 TEXT,
              ultimo_mensaje       TEXT,
              fecha_ultimo_mensaje TEXT,
              no_leidos            INTEGER DEFAULT 0,
              es_grupo             INTEGER DEFAULT 0,
              ultimo_mensaje_ts    INTEGER DEFAULT 0,
              actualizado_en       TEXT
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_mensajes (
              id               TEXT PRIMARY KEY,
              id_mensaje       TEXT,
              chat_jid         TEXT,
              jid              TEXT,
              chat_nombre      TEXT,
              remitente_jid    TEXT,
              remitente_nombre TEXT,
              nombre_emisor    TEXT,
              desde_recepcion  INTEGER DEFAULT 0,
              es_mio           INTEGER DEFAULT 0,
              fecha_hora       TEXT,
              timestamp        INTEGER DEFAULT 0,
              timestamp_unix   INTEGER DEFAULT 0,
              tipo             TEXT,
              tipo_mensaje     TEXT,
              texto            TEXT,
              tiene_archivo    INTEGER DEFAULT 0,
              archivo_ruta     TEXT,
              ruta_archivo     TEXT,
              creado_en        TEXT
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
              id                INTEGER PRIMARY KEY AUTOINCREMENT,
              mensaje_id        TEXT UNIQUE,
              id_mensaje        TEXT,
              jid               TEXT,
              fecha_hora        TEXT,
              chat_nombre       TEXT,
              tipo_archivo      TEXT,
              nombre_archivo    TEXT,
              ruta_local        TEXT,
              archivo_ruta      TEXT,
              metodo_extraccion TEXT,
              categoria         TEXT,
              ruc_emisor        TEXT,
              ruc_dni           TEXT,
              serie_numero      TEXT,
              monto_detectado   REAL,
              operacion_nro     TEXT,
              texto_extraido    TEXT,
              fecha_procesado   TEXT
            );

            CREATE TABLE IF NOT EXISTS wsp_recepcion_estado (
              clave          TEXT PRIMARY KEY,
              valor          TEXT,
              actualizado_en TEXT
            );
            """
        )
        # Asegurar columnas híbridas en bases heredadas
        for col in ("tipo TEXT", "ultimo_mensaje TEXT", "fecha_ultimo_mensaje TEXT", "no_leidos INTEGER DEFAULT 0",
                    "es_grupo INTEGER DEFAULT 0", "ultimo_mensaje_ts INTEGER DEFAULT 0", "actualizado_en TEXT"):
            _ensure_column(conn, "wsp_recepcion_chats", col)

        for col in ("id_mensaje TEXT", "jid TEXT", "chat_jid TEXT", "chat_nombre TEXT", "remitente_jid TEXT",
                    "nombre_emisor TEXT", "remitente_nombre TEXT", "es_mio INTEGER DEFAULT 0",
                    "desde_recepcion INTEGER DEFAULT 0", "timestamp_unix INTEGER DEFAULT 0",
                    "timestamp INTEGER DEFAULT 0", "tipo_mensaje TEXT", "tipo TEXT", "texto TEXT",
                    "tiene_archivo INTEGER DEFAULT 0", "ruta_archivo TEXT", "archivo_ruta TEXT", "creado_en TEXT"):
            _ensure_column(conn, "wsp_recepcion_mensajes", col)

        for col in ("id_mensaje TEXT", "mensaje_id TEXT", "jid TEXT", "tipo_archivo TEXT", "nombre_archivo TEXT",
                    "ruta_local TEXT", "archivo_ruta TEXT", "metodo_extraccion TEXT", "ruc_emisor TEXT",
                    "ruc_dni TEXT", "fecha_procesado TEXT"):
            _ensure_column(conn, "wsp_recepcion_documentos", col)

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_wsp_msg_id_mensaje_bridge ON wsp_recepcion_mensajes (id_mensaje)"
        )
        conn.commit()
        _tablas_locales_listas = True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lectura SOLO LECTURA de Operativa (Laptop 2) o Local (semilla)
# ---------------------------------------------------------------------------
def _ruta_operativa_db() -> Optional[Path]:
    if not MODO_PRUEBA:
        return None
    base_dir = Path(getattr(_cfg, "BASE_APP_DIR", Path(__file__).resolve().parent))
    carpeta = str((getattr(_cfg, "PERFIL_HOTEL", {}) or {}).get("carpeta_operativa") or r"C:\Recepcion_Ovalo_Local").strip()
    if not carpeta:
        return None
    try:
        raiz = Path(carpeta)
        if raiz.resolve() == base_dir.resolve():
            return None
        db = raiz / "data" / "ovalo_sistema.db"
        return db if db.exists() and db.stat().st_size > 0 else None
    except Exception:
        return None


def _abrir_sqlite_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path or not db_path.exists():
        return None
    try:
        uri = "file:" + quote(db_path.as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=4.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA query_only=1;")
        return conn
    except Exception:
        return None


def _columnas_tabla(conn: sqlite3.Connection, tabla: str) -> set:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
    except Exception:
        return set()


def _extraer_wsp_desde_conn(conn: sqlite3.Connection, origen_etiqueta: str) -> Dict[str, Any]:
    """Lee estado, chats, mensajes y documentos en formato canónico desde una conexión SQLite RO."""
    equipo_id = str(getattr(_cfg, "EQUIPO_ID", "") or origen_etiqueta)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Estado
    estados: List[List[str]] = []
    cols_est = _columnas_tabla(conn, "wsp_recepcion_estado")
    if cols_est:
        if "clave" in cols_est and "valor" in cols_est:
            for r in conn.execute("SELECT clave, valor, actualizado_en FROM wsp_recepcion_estado ORDER BY clave").fetchall():
                estados.append([_s(r["clave"], 120), _s(r["valor"], 300), _s(r["actualizado_en"] or now_str, 40), equipo_id])
        elif "conexion" in cols_est:
            r = conn.execute("SELECT conexion FROM wsp_recepcion_estado LIMIT 1").fetchone()
            if r and r["conexion"]:
                estados.append(["conexion", _s(r["conexion"], 120), now_str, equipo_id])

    # 2. Chats
    chats: List[List[Any]] = []
    cols_ch = _columnas_tabla(conn, "wsp_recepcion_chats")
    if cols_ch:
        sel_tipo = "COALESCE(tipo,'')" if "tipo" in cols_ch else "''"
        sel_es_g = "COALESCE(es_grupo,0)" if "es_grupo" in cols_ch else "0"
        sel_um = "COALESCE(ultimo_mensaje,'')" if "ultimo_mensaje" in cols_ch else "''"
        sel_fum = "COALESCE(fecha_ultimo_mensaje,'')" if "fecha_ultimo_mensaje" in cols_ch else "''"
        sel_ts = "COALESCE(ultimo_mensaje_ts,0)" if "ultimo_mensaje_ts" in cols_ch else "0"
        sel_nl = "COALESCE(no_leidos,0)" if "no_leidos" in cols_ch else "0"
        sel_act = "COALESCE(actualizado_en,'')" if "actualizado_en" in cols_ch else "''"
        sql_ch = f"""
            SELECT COALESCE(jid,'') AS jid,
                   COALESCE(nombre,'') AS nombre,
                   {sel_tipo} AS tipo,
                   {sel_es_g} AS es_grupo,
                   {sel_um} AS ultimo_mensaje,
                   {sel_fum} AS fecha_ultimo_mensaje,
                   {sel_ts} AS ultimo_mensaje_ts,
                   {sel_nl} AS no_leidos,
                   {sel_act} AS actualizado_en
              FROM wsp_recepcion_chats
             WHERE COALESCE(jid,'') != '' AND COALESCE(jid,'') NOT LIKE 'SIM-%'
             ORDER BY ultimo_mensaje_ts DESC
             LIMIT {MAX_CHATS_SHEETS}
        """
        for r in conn.execute(sql_ch).fetchall():
            chats.append([
                _s(r["jid"], 120),
                _s(r["nombre"], 200),
                _s(r["tipo"], 40),
                _i(r["es_grupo"]),
                _s(r["ultimo_mensaje"], 500),
                _s(r["fecha_ultimo_mensaje"], 40),
                _i(r["ultimo_mensaje_ts"]),
                _i(r["no_leidos"]),
                _s(r["actualizado_en"], 40),
            ])

    # 3. Mensajes (últimos MAX_MENSAJES_SHEETS, ordenados ascendente para lectura natural)
    mensajes: List[List[Any]] = []
    cols_m = _columnas_tabla(conn, "wsp_recepcion_mensajes")
    if cols_m:
        c_mid = "COALESCE(NULLIF(id_mensaje,''), id)" if ("id_mensaje" in cols_m and "id" in cols_m) else ("id_mensaje" if "id_mensaje" in cols_m else "id")
        c_jid = "COALESCE(NULLIF(jid,''), chat_jid)" if ("jid" in cols_m and "chat_jid" in cols_m) else ("jid" if "jid" in cols_m else "chat_jid")
        c_cnom = "COALESCE(chat_nombre,'')" if "chat_nombre" in cols_m else "''"
        c_rjid = "COALESCE(remitente_jid,'')" if "remitente_jid" in cols_m else "''"
        c_em = "COALESCE(NULLIF(nombre_emisor,''), remitente_nombre, '')" if ("nombre_emisor" in cols_m and "remitente_nombre" in cols_m) else (
            "COALESCE(nombre_emisor,'')" if "nombre_emisor" in cols_m else "COALESCE(remitente_nombre,'')"
        )
        c_mio = "MAX(COALESCE(es_mio,0), COALESCE(desde_recepcion,0))" if ("es_mio" in cols_m and "desde_recepcion" in cols_m) else (
            "COALESCE(es_mio,0)" if "es_mio" in cols_m else "COALESCE(desde_recepcion,0)"
        )
        c_ts = "MAX(COALESCE(timestamp_unix,0), COALESCE(timestamp,0))" if ("timestamp_unix" in cols_m and "timestamp" in cols_m) else (
            "COALESCE(timestamp_unix,0)" if "timestamp_unix" in cols_m else "COALESCE(timestamp,0)"
        )
        c_fh = "COALESCE(fecha_hora,'')" if "fecha_hora" in cols_m else "''"
        c_tipo = "COALESCE(NULLIF(tipo_mensaje,''), tipo, 'texto')" if ("tipo_mensaje" in cols_m and "tipo" in cols_m) else (
            "COALESCE(tipo_mensaje,'texto')" if "tipo_mensaje" in cols_m else "COALESCE(tipo,'texto')"
        )
        c_ta = "COALESCE(tiene_archivo,0)" if "tiene_archivo" in cols_m else "0"
        c_ra = "COALESCE(NULLIF(ruta_archivo,''), archivo_ruta, '')" if ("ruta_archivo" in cols_m and "archivo_ruta" in cols_m) else (
            "COALESCE(ruta_archivo,'')" if "ruta_archivo" in cols_m else "COALESCE(archivo_ruta,'')"
        )
        c_cr = "COALESCE(creado_en,'')" if "creado_en" in cols_m else "''"

        sql_m = f"""
            SELECT * FROM (
                SELECT {c_mid}  AS mid,
                       {c_jid}  AS jid,
                       {c_cnom} AS chat_nombre,
                       {c_rjid} AS remitente_jid,
                       {c_em}   AS nombre_emisor,
                       {c_mio}  AS es_mio,
                       {c_ts}   AS ts,
                       {c_fh}   AS fecha_hora,
                       {c_tipo} AS tipo_mensaje,
                       COALESCE(texto,'') AS texto,
                       {c_ta}   AS tiene_archivo,
                       {c_ra}   AS ruta_archivo,
                       {c_cr}   AS creado_en
                  FROM wsp_recepcion_mensajes
                 WHERE {c_mid} IS NOT NULL AND TRIM({c_mid}) != ''
                   AND {c_jid} IS NOT NULL AND {c_jid} NOT LIKE 'SIM-%'
                 ORDER BY ts DESC
                 LIMIT {MAX_MENSAJES_SHEETS}
            ) sub
            ORDER BY ts ASC
        """
        for r in conn.execute(sql_m).fetchall():
            mensajes.append([
                _s(r["mid"], 160),
                _s(r["jid"], 120),
                _s(r["chat_nombre"], 200),
                _s(r["remitente_jid"], 120),
                _s(r["nombre_emisor"], 200),
                _i(r["es_mio"]),
                _i(r["ts"]),
                _s(r["fecha_hora"], 40),
                _s(r["tipo_mensaje"], 40),
                _s(r["texto"], MAX_CELL_CHARS),
                _i(r["tiene_archivo"]),
                _s(r["ruta_archivo"], 500),
                _s(r["creado_en"], 40),
            ])

    # 4. Documentos OCR
    docs: List[List[Any]] = []
    cols_d = _columnas_tabla(conn, "wsp_recepcion_documentos")
    if cols_d:
        c_dm = "COALESCE(NULLIF(id_mensaje,''), mensaje_id, '')" if ("id_mensaje" in cols_d and "mensaje_id" in cols_d) else (
            "COALESCE(id_mensaje,'')" if "id_mensaje" in cols_d else "COALESCE(mensaje_id,'')"
        )
        c_dj = "COALESCE(jid,'')" if "jid" in cols_d else "''"
        c_dc = "COALESCE(chat_nombre,'')" if "chat_nombre" in cols_d else "''"
        c_dfh = "COALESCE(fecha_hora,'')" if "fecha_hora" in cols_d else "''"
        c_dta = "COALESCE(tipo_archivo,'')" if "tipo_archivo" in cols_d else "''"
        c_dna = "COALESCE(nombre_archivo,'')" if "nombre_archivo" in cols_d else "''"
        c_drl = "COALESCE(NULLIF(ruta_local,''), archivo_ruta, '')" if ("ruta_local" in cols_d and "archivo_ruta" in cols_d) else (
            "COALESCE(ruta_local,'')" if "ruta_local" in cols_d else "COALESCE(archivo_ruta,'')"
        )
        c_dme = "COALESCE(metodo_extraccion,'')" if "metodo_extraccion" in cols_d else "''"
        c_dca = "COALESCE(categoria,'')" if "categoria" in cols_d else "''"
        c_druc = "COALESCE(NULLIF(ruc_emisor,''), ruc_dni, '')" if ("ruc_emisor" in cols_d and "ruc_dni" in cols_d) else (
            "COALESCE(ruc_emisor,'')" if "ruc_emisor" in cols_d else "COALESCE(ruc_dni,'')"
        )
        c_dser = "COALESCE(serie_numero,'')" if "serie_numero" in cols_d else "''"
        c_dmon = "COALESCE(monto_detectado,'')" if "monto_detectado" in cols_d else "''"
        c_dop = "COALESCE(operacion_nro,'')" if "operacion_nro" in cols_d else "''"
        c_dtxt = "COALESCE(texto_extraido,'')" if "texto_extraido" in cols_d else "''"
        c_dfp = "COALESCE(fecha_procesado,'')" if "fecha_procesado" in cols_d else "''"

        sql_d = f"""
            SELECT {c_dm}   AS id_mensaje,
                   {c_dj}   AS jid,
                   {c_dc}   AS chat_nombre,
                   {c_dfh}  AS fecha_hora,
                   {c_dta}  AS tipo_archivo,
                   {c_dna}  AS nombre_archivo,
                   {c_drl}  AS ruta_local,
                   {c_dme}  AS metodo_extraccion,
                   {c_dca}  AS categoria,
                   {c_druc} AS ruc_emisor,
                   {c_dser} AS serie_numero,
                   {c_dmon} AS monto_detectado,
                   {c_dop}  AS operacion_nro,
                   {c_dtxt} AS texto_extraido,
                   {c_dfp}  AS fecha_procesado
              FROM wsp_recepcion_documentos
             WHERE {c_dm} != ''
             ORDER BY id DESC
             LIMIT {MAX_DOCS_SHEETS}
        """
        for r in conn.execute(sql_d).fetchall():
            docs.append([
                _s(r["id_mensaje"], 160),
                _s(r["jid"], 120),
                _s(r["chat_nombre"], 200),
                _s(r["fecha_hora"], 40),
                _s(r["tipo_archivo"], 40),
                _s(r["nombre_archivo"], 240),
                _s(r["ruta_local"], 500),
                _s(r["metodo_extraccion"], 80),
                _s(r["categoria"], 80),
                _s(r["ruc_emisor"], 30),
                _s(r["serie_numero"], 60),
                _s(r["monto_detectado"], 40),
                _s(r["operacion_nro"], 80),
                _s(r["texto_extraido"], MAX_CELL_CHARS),
                _s(r["fecha_procesado"], 40),
            ])

    # Asegurar claves resumen en WSP_Estado
    claves_est = {row[0] for row in estados}
    if "conexion" not in claves_est:
        estados.append(["conexion", "CONECTADO" if mensajes else "SIN_DATOS", now_str, equipo_id])
    max_ts = max((_i(m[6]) for m in mensajes), default=0)
    max_fh = mensajes[-1][7] if mensajes else ""
    estados = [e for e in estados if e[0] not in ("bridge_ultimo_push", "bridge_total_mensajes", "bridge_ultimo_msg_ts", "bridge_ultimo_msg_fecha")]
    estados.extend([
        ["bridge_ultimo_push", now_str, now_str, equipo_id],
        ["bridge_total_mensajes", str(len(mensajes)), now_str, equipo_id],
        ["bridge_ultimo_msg_ts", str(max_ts), now_str, equipo_id],
        ["bridge_ultimo_msg_fecha", str(max_fh), now_str, equipo_id],
    ])

    fp_raw = json.dumps(
        {
            "n_ch": len(chats),
            "n_m": len(mensajes),
            "n_d": len(docs),
            "max_ts": max_ts,
            "ult_mid": mensajes[-1][0] if mensajes else "",
            "ult_ch": chats[0][0] if chats else "",
            "est_con": next((e[1] for e in estados if e[0] == "conexion"), ""),
        },
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:16]
    return {
        "estados": estados,
        "chats": chats,
        "mensajes": mensajes,
        "documentos": docs,
        "max_ts": max_ts,
        "max_fecha_hora": max_fh,
        "fingerprint": fingerprint,
    }


def detectar_rol_wsp_prueba() -> Tuple[str, Optional[Path]]:
    """
    Determina automáticamente el rol de esta laptop en el puente WSP de Prueba:
      - 'EMISOR_L2': existe `C:\\Recepcion_Ovalo_Local\\data\\ovalo_sistema.db` con mensajes WSP reales.
      - 'RECEPTOR_L1': no hay operativa local con WSP (Laptop 1), consume desde `2026_Prueba_Ovalo`.
    """
    op_db = _ruta_operativa_db()
    if op_db is None:
        return "RECEPTOR_L1", None
    conn = _abrir_sqlite_ro(op_db)
    if conn is None:
        return "RECEPTOR_L1", None
    try:
        r = conn.execute(
            "SELECT COUNT(*) FROM wsp_recepcion_mensajes WHERE COALESCE(chat_jid, jid, '') NOT LIKE 'SIM-%'"
        ).fetchone()
        if r and int(r[0] or 0) > 0:
            return "EMISOR_L2", op_db
    except Exception:
        pass
    finally:
        conn.close()
    return "RECEPTOR_L1", None


# ---------------------------------------------------------------------------
# Escritura en SQLite Local de Prueba (UPSERT híbrido sin pérdida)
# ---------------------------------------------------------------------------
def _guardar_lote_en_sqlite_prueba(data: Dict[str, Any]) -> Dict[str, int]:
    asegurar_tablas_wsp_prueba()
    conn = get_connection()
    try:
        antes_msgs = int(conn.execute("SELECT COUNT(*) FROM wsp_recepcion_mensajes").fetchone()[0] or 0)
        antes_max_ts = int(
            conn.execute(
                "SELECT COALESCE(MAX(MAX(COALESCE(timestamp_unix,0), COALESCE(timestamp,0))), 0) FROM wsp_recepcion_mensajes"
            ).fetchone()[0]
            or 0
        )

        # 1. Estado
        for row in data.get("estados") or []:
            if not row or not str(row[0]).strip():
                continue
            clave = _s(row[0], 120)
            valor = _s(row[1] if len(row) > 1 else "", 300)
            act = _s(row[2] if len(row) > 2 else "", 40)
            conn.execute(
                """
                INSERT INTO wsp_recepcion_estado (clave, valor, actualizado_en)
                VALUES (?, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET
                  valor = excluded.valor,
                  actualizado_en = excluded.actualizado_en
                """,
                (clave, valor, act),
            )

        # 2. Chats
        chats_ok = 0
        for row in data.get("chats") or []:
            if not row or not str(row[0]).strip():
                continue
            jid = _s(row[0], 120)
            nombre = _s(row[1] if len(row) > 1 else "", 200)
            tipo = _s(row[2] if len(row) > 2 else "", 40)
            es_grupo = _i(row[3] if len(row) > 3 else 0)
            ult_msg = _s(row[4] if len(row) > 4 else "", 500)
            f_ult = _s(row[5] if len(row) > 5 else "", 40)
            ts_ult = _i(row[6] if len(row) > 6 else 0)
            no_leidos = _i(row[7] if len(row) > 7 else 0)
            act = _s(row[8] if len(row) > 8 else "", 40)
            conn.execute(
                """
                INSERT INTO wsp_recepcion_chats
                  (jid, nombre, tipo, es_grupo, ultimo_mensaje, fecha_ultimo_mensaje, ultimo_mensaje_ts, no_leidos, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                  nombre = CASE WHEN excluded.nombre != '' THEN excluded.nombre ELSE wsp_recepcion_chats.nombre END,
                  tipo = excluded.tipo,
                  es_grupo = excluded.es_grupo,
                  ultimo_mensaje = excluded.ultimo_mensaje,
                  fecha_ultimo_mensaje = excluded.fecha_ultimo_mensaje,
                  ultimo_mensaje_ts = MAX(COALESCE(wsp_recepcion_chats.ultimo_mensaje_ts,0), excluded.ultimo_mensaje_ts),
                  no_leidos = excluded.no_leidos,
                  actualizado_en = excluded.actualizado_en
                """,
                (jid, nombre, tipo, es_grupo, ult_msg, f_ult, ts_ult, no_leidos, act),
            )
            chats_ok += 1

        # 3. Mensajes (poblamos columnas nuevas + heredadas para compatibilidad 100%)
        msgs_ok = 0
        for row in data.get("mensajes") or []:
            if not row or not str(row[0]).strip():
                continue
            mid = _s(row[0], 160)
            jid = _s(row[1] if len(row) > 1 else "", 120)
            if not jid:
                continue
            chat_nombre = _s(row[2] if len(row) > 2 else "", 200)
            rem_jid = _s(row[3] if len(row) > 3 else "", 120)
            nom_em = _s(row[4] if len(row) > 4 else "", 200)
            es_mio = _i(row[5] if len(row) > 5 else 0)
            ts = _i(row[6] if len(row) > 6 else 0)
            fh = _s(row[7] if len(row) > 7 else "", 40)
            tipo_m = _s(row[8] if len(row) > 8 else "texto", 40) or "texto"
            texto = _s(row[9] if len(row) > 9 else "", MAX_CELL_CHARS)
            tiene_arch = _i(row[10] if len(row) > 10 else 0)
            ruta_arch = _s(row[11] if len(row) > 11 else "", 500)
            creado_en = _s(row[12] if len(row) > 12 else "", 40)
            conn.execute(
                """
                INSERT INTO wsp_recepcion_mensajes
                  (id, id_mensaje, chat_jid, jid, chat_nombre, remitente_jid, remitente_nombre, nombre_emisor,
                   desde_recepcion, es_mio, fecha_hora, timestamp, timestamp_unix, tipo, tipo_mensaje,
                   texto, tiene_archivo, archivo_ruta, ruta_archivo, creado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  id_mensaje = excluded.id_mensaje,
                  chat_jid = excluded.chat_jid,
                  jid = excluded.jid,
                  chat_nombre = CASE WHEN excluded.chat_nombre != '' THEN excluded.chat_nombre ELSE wsp_recepcion_mensajes.chat_nombre END,
                  remitente_jid = excluded.remitente_jid,
                  remitente_nombre = excluded.remitente_nombre,
                  nombre_emisor = excluded.nombre_emisor,
                  desde_recepcion = excluded.desde_recepcion,
                  es_mio = excluded.es_mio,
                  fecha_hora = excluded.fecha_hora,
                  timestamp = excluded.timestamp,
                  timestamp_unix = excluded.timestamp_unix,
                  tipo = excluded.tipo,
                  tipo_mensaje = excluded.tipo_mensaje,
                  texto = excluded.texto,
                  tiene_archivo = excluded.tiene_archivo,
                  archivo_ruta = excluded.archivo_ruta,
                  ruta_archivo = excluded.ruta_archivo,
                  creado_en = excluded.creado_en
                """,
                (
                    mid, mid, jid, jid, chat_nombre, rem_jid, nom_em, nom_em,
                    es_mio, es_mio, fh, ts, ts, tipo_m, tipo_m,
                    texto, tiene_arch, ruta_arch, ruta_arch, creado_en,
                ),
            )
            msgs_ok += 1

        # 4. Documentos OCR
        docs_ok = 0
        for row in data.get("documentos") or []:
            if not row or not str(row[0]).strip():
                continue
            mid = _s(row[0], 160)
            jid = _s(row[1] if len(row) > 1 else "", 120)
            cnom = _s(row[2] if len(row) > 2 else "", 200)
            fh = _s(row[3] if len(row) > 3 else "", 40)
            tarch = _s(row[4] if len(row) > 4 else "", 40)
            narch = _s(row[5] if len(row) > 5 else "", 240)
            rloc = _s(row[6] if len(row) > 6 else "", 500)
            met = _s(row[7] if len(row) > 7 else "", 80)
            cat = _s(row[8] if len(row) > 8 else "", 80)
            ruc = _s(row[9] if len(row) > 9 else "", 30)
            serie = _s(row[10] if len(row) > 10 else "", 60)
            monto = _f(row[11] if len(row) > 11 else 0.0)
            op = _s(row[12] if len(row) > 12 else "", 80)
            txt = _s(row[13] if len(row) > 13 else "", MAX_CELL_CHARS)
            fproc = _s(row[14] if len(row) > 14 else "", 40)
            conn.execute(
                """
                INSERT INTO wsp_recepcion_documentos
                  (mensaje_id, id_mensaje, jid, fecha_hora, chat_nombre, tipo_archivo, nombre_archivo,
                   ruta_local, archivo_ruta, metodo_extraccion, categoria, ruc_emisor, ruc_dni,
                   serie_numero, monto_detectado, operacion_nro, texto_extraido, fecha_procesado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mensaje_id) DO UPDATE SET
                  id_mensaje = excluded.id_mensaje,
                  jid = excluded.jid,
                  fecha_hora = excluded.fecha_hora,
                  chat_nombre = excluded.chat_nombre,
                  tipo_archivo = excluded.tipo_archivo,
                  nombre_archivo = excluded.nombre_archivo,
                  ruta_local = excluded.ruta_local,
                  archivo_ruta = excluded.archivo_ruta,
                  metodo_extraccion = excluded.metodo_extraccion,
                  categoria = excluded.categoria,
                  ruc_emisor = excluded.ruc_emisor,
                  ruc_dni = excluded.ruc_dni,
                  serie_numero = excluded.serie_numero,
                  monto_detectado = excluded.monto_detectado,
                  operacion_nro = excluded.operacion_nro,
                  texto_extraido = excluded.texto_extraido,
                  fecha_procesado = excluded.fecha_procesado
                """,
                (mid, mid, jid, fh, cnom, tarch, narch, rloc, rloc, met, cat, ruc, ruc, serie, monto, op, txt, fproc),
            )
            docs_ok += 1

        conn.commit()
        despues_msgs = int(conn.execute("SELECT COUNT(*) FROM wsp_recepcion_mensajes").fetchone()[0] or 0)
        despues_max_ts = int(
            conn.execute(
                "SELECT COALESCE(MAX(MAX(COALESCE(timestamp_unix,0), COALESCE(timestamp,0))), 0) FROM wsp_recepcion_mensajes"
            ).fetchone()[0]
            or 0
        )
        return {
            "chats_guardados": chats_ok,
            "mensajes_guardados": msgs_ok,
            "documentos_guardados": docs_ok,
            "nuevos_mensajes": max(0, despues_msgs - antes_msgs) + (1 if despues_max_ts > antes_max_ts and despues_msgs == antes_msgs else 0),
            "total_mensajes_local": despues_msgs,
            "ultimo_ts_local": despues_max_ts,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Subida (EMISOR_L2 -> 2026_Prueba_Ovalo) y Bajada (2026_Prueba_Ovalo -> RECEPTOR_L1)
# ---------------------------------------------------------------------------
def _escribir_pestana_raw(ws: gspread.Worksheet, headers: List[str], filas: List[List[Any]]) -> None:
    payload = [headers] + filas
    filas_req = max(len(payload) + 20, 50)
    cols_req = len(headers)
    if ws.row_count < filas_req or ws.col_count < cols_req:
        ws.resize(rows=max(ws.row_count, filas_req), cols=max(ws.col_count, cols_req))
    ws.clear()
    ws.update(range_name="A1", values=payload, value_input_option="RAW")


def subir_wsp_a_sheets_prueba(
    fuente_db: Path,
    forzar: bool = False,
    reflejar_en_prueba_local: bool = True,
    origen_etiqueta: str = "EMISOR_L2",
) -> Dict[str, Any]:
    """Lee WSP desde `fuente_db` (RO), opcionalmente lo refleja en SQLite de prueba y lo sube a `2026_Prueba_Ovalo`."""
    global _last_push_fingerprint
    _verificar_candado_prueba()
    conn_ro = _abrir_sqlite_ro(fuente_db)
    if conn_ro is None:
        return {"ok": False, "accion": "push_error", "error": f"No se pudo abrir en solo lectura: {fuente_db}"}
    try:
        lote = _extraer_wsp_desde_conn(conn_ro, origen_etiqueta=origen_etiqueta)
    finally:
        conn_ro.close()

    res_local = {}
    if reflejar_en_prueba_local:
        res_local = _guardar_lote_en_sqlite_prueba(lote)

    fp = lote["fingerprint"]
    if not forzar and fp == _last_push_fingerprint:
        return {
            "ok": True,
            "accion": "push_sin_cambios",
            "rol": origen_etiqueta,
            "chats": len(lote["chats"]),
            "mensajes": len(lote["mensajes"]),
            "documentos": len(lote["documentos"]),
            "max_fecha_hora": lote["max_fecha_hora"],
            **res_local,
        }

    sh = _abrir_hoja_prueba()
    tabs = _asegurar_pestanas_wsp(sh)
    _escribir_pestana_raw(tabs[TAB_WSP_ESTADO], HEADERS_ESTADO, lote["estados"])
    _escribir_pestana_raw(tabs[TAB_WSP_CHATS], HEADERS_CHATS, lote["chats"])
    _escribir_pestana_raw(tabs[TAB_WSP_MENSAJES], HEADERS_MENSAJES, lote["mensajes"])
    _escribir_pestana_raw(tabs[TAB_WSP_DOCUMENTOS], HEADERS_DOCUMENTOS, lote["documentos"])
    _last_push_fingerprint = fp

    return {
        "ok": True,
        "accion": "push_ok",
        "rol": origen_etiqueta,
        "chats": len(lote["chats"]),
        "mensajes": len(lote["mensajes"]),
        "documentos": len(lote["documentos"]),
        "max_ts": lote["max_ts"],
        "max_fecha_hora": lote["max_fecha_hora"],
        "fingerprint": fp,
        **res_local,
    }


def bajar_wsp_desde_sheets_prueba(forzar: bool = False) -> Dict[str, Any]:
    """Lee las 4 pestañas WSP_* de `2026_Prueba_Ovalo` en 1 batchGet y actualiza SQLite local de Prueba."""
    global _last_pull_fingerprint
    _verificar_candado_prueba()
    sh = _abrir_hoja_prueba()
    _asegurar_pestanas_wsp(sh)

    rangos = [
        f"'{TAB_WSP_ESTADO}'!A2:D100",
        f"'{TAB_WSP_CHATS}'!A2:I{MAX_CHATS_SHEETS + 10}",
        f"'{TAB_WSP_MENSAJES}'!A2:M{MAX_MENSAJES_SHEETS + 10}",
        f"'{TAB_WSP_DOCUMENTOS}'!A2:O{MAX_DOCS_SHEETS + 10}",
    ]
    res_batch = sh.values_batch_get(rangos, params={"valueRenderOption": "UNFORMATTED_VALUE"})
    value_ranges = res_batch.get("valueRanges") or []

    def _vals(idx: int) -> List[List[Any]]:
        if idx >= len(value_ranges):
            return []
        return [r for r in (value_ranges[idx].get("values") or []) if any(str(c).strip() for c in r)]

    estados = _vals(0)
    chats = _vals(1)
    mensajes = _vals(2)
    docs = _vals(3)

    max_ts = max((_i(m[6] if len(m) > 6 else 0) for m in mensajes), default=0)
    fp_raw = json.dumps(
        {
            "n_e": len(estados),
            "n_c": len(chats),
            "n_m": len(mensajes),
            "n_d": len(docs),
            "max_ts": max_ts,
            "ult_mid": _s(mensajes[-1][0], 80) if mensajes else "",
        },
        sort_keys=True,
    )
    fp = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:16]

    if not mensajes and not chats:
        return {
            "ok": True,
            "accion": "pull_sheets_vacio",
            "rol": "RECEPTOR_L1",
            "chats": 0,
            "mensajes": 0,
            "documentos": 0,
            "nuevos_mensajes": 0,
        }

    if not forzar and fp == _last_pull_fingerprint:
        return {
            "ok": True,
            "accion": "pull_sin_cambios",
            "rol": "RECEPTOR_L1",
            "chats": len(chats),
            "mensajes": len(mensajes),
            "documentos": len(docs),
            "nuevos_mensajes": 0,
            "max_ts": max_ts,
        }

    res_db = _guardar_lote_en_sqlite_prueba(
        {
            "estados": estados,
            "chats": chats,
            "mensajes": mensajes,
            "documentos": docs,
        }
    )
    _last_pull_fingerprint = fp
    return {
        "ok": True,
        "accion": "pull_ok",
        "rol": "RECEPTOR_L1",
        "chats": len(chats),
        "mensajes": len(mensajes),
        "documentos": len(docs),
        "max_ts": max_ts,
        "fingerprint": fp,
        **res_db,
    }


def sincronizar_wsp_prueba_sheets(forzar: bool = False, sembrar_si_vacio: bool = False) -> Dict[str, Any]:
    """
    Punto de entrada principal llamado por `wsp_inteligencia.py` (en OvaloWspWorker y al pulsar
    'Actualizar resumen ahora').
      - En Laptop 2 (`EMISOR_L2`): sube conversaciones reales RO -> Prueba SQLite + `2026_Prueba_Ovalo`.
      - En Laptop 1 (`RECEPTOR_L1`): baja conversaciones desde `2026_Prueba_Ovalo` -> Prueba SQLite.
        (Si `sembrar_si_vacio=True` y `2026_Prueba_Ovalo` aún tiene 0 mensajes, siembra desde la SQLite local).
    """
    global _last_sync_ts
    if not MODO_PRUEBA or not HOJA_PRUEBA_HABILITADA:
        return {"ok": False, "accion": "omitido_no_prueba"}

    ahora = time.time()
    if not forzar and (ahora - _last_sync_ts) < INTERVALO_SYNC_AUTO_SEG:
        return {"ok": True, "accion": "throttle", "nuevos_mensajes": 0}

    if not _bridge_lock.acquire(blocking=forzar):
        return {"ok": True, "accion": "ocupado", "nuevos_mensajes": 0}

    try:
        _last_sync_ts = time.time()
        rol, op_db = detectar_rol_wsp_prueba()
        if rol == "EMISOR_L2" and op_db is not None:
            return subir_wsp_a_sheets_prueba(
                fuente_db=op_db,
                forzar=forzar,
                reflejar_en_prueba_local=True,
                origen_etiqueta="EMISOR_L2",
            )

        # Rol RECEPTOR_L1
        res_pull = bajar_wsp_desde_sheets_prueba(forzar=forzar)
        if sembrar_si_vacio and res_pull.get("accion") == "pull_sheets_vacio":
            db_local = Path(getattr(_cfg, "DB_PATH", Path(__file__).resolve().parent / "data" / "ovalo_sistema.db"))
            if db_local.exists():
                return subir_wsp_a_sheets_prueba(
                    fuente_db=db_local,
                    forzar=True,
                    reflejar_en_prueba_local=False,
                    origen_etiqueta="SEMILLA_L1",
                )
        return res_pull
    except Exception as exc:
        # Resetear cliente gspread ante corte de red/token expirado para próximo ciclo
        global _sh_prueba, _gc_client
        _sh_prueba = None
        _gc_client = None
        return {"ok": False, "accion": "error", "error": str(exc), "nuevos_mensajes": 0}
    finally:
        _bridge_lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Puente WSP Google Sheets para entorno de prueba (:8082)")
    parser.add_argument("--forzar", action="store_true", help="Ignorar throttle/fingerprint y sincronizar de inmediato")
    parser.add_argument("--sembrar-si-vacio", action="store_true", help="Si 2026_Prueba_Ovalo no tiene mensajes WSP aún, sembrar desde SQLite local")
    parser.add_argument("--solo-pull", action="store_true", help="Forzar lectura desde 2026_Prueba_Ovalo hacia SQLite local")
    args = parser.parse_args()

    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.solo_pull:
        res = bajar_wsp_desde_sheets_prueba(forzar=True)
    else:
        res = sincronizar_wsp_prueba_sheets(forzar=True if (args.forzar or args.sembrar_si_vacio) else False, sembrar_si_vacio=args.sembrar_si_vacio)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
