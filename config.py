#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuración central de la Aplicación Local Offline-First para Recepción
Hotel El Óvalo Mariátegui
"""

import json
import os
import shutil
import socket
from pathlib import Path

BASE_APP_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_APP_DIR / "data"
STATIC_DIR = BASE_APP_DIR / "static"
PERFIL_HOTEL_PATH = DATA_DIR / "hotel_perfil.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

_ES_CARPETA_TERRAZAS = "terrazas" in str(BASE_APP_DIR).lower()

# Unificación de Base Compartida Grupo 2 (ovalo_sistema.db) para Hotel El Óvalo Mariátegui
if _ES_CARPETA_TERRAZAS:
    DB_PATH = DATA_DIR / "ovalo_local.db"
else:
    _ROOT_OVALO_DB = BASE_APP_DIR.parent / "ovalo_sistema.db"
    _DATA_OVALO_DB = DATA_DIR / "ovalo_sistema.db"
    _LEGACY_LOCAL_DB = DATA_DIR / "ovalo_local.db"
    if _ROOT_OVALO_DB.exists():
        DB_PATH = _ROOT_OVALO_DB
    else:
        if not _DATA_OVALO_DB.exists() and _LEGACY_LOCAL_DB.exists():
            try:
                shutil.copy2(str(_LEGACY_LOCAL_DB), str(_DATA_OVALO_DB))
            except Exception:
                pass
        DB_PATH = _DATA_OVALO_DB

_PERFIL_OVALO = {
    "nombre_hotel": "Hotel El Óvalo Mariátegui",
    "spreadsheet_id": "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8",
    "col_tipo_adicional": "BA",
    "formula_snacks_en_s2": False,
    "app_port": 8080,
}

_PERFIL_TERRAZAS = {
    "nombre_hotel": "Hotel Terrazas de Villa",
    "spreadsheet_id": "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss",
    "col_tipo_adicional": "AZ",
    "formula_snacks_en_s2": True,
    "app_port": 8081,
}

_ES_CARPETA_TERRAZAS = "terrazas" in str(BASE_APP_DIR).lower()
_DEFAULT_PERFIL = dict(_PERFIL_TERRAZAS if _ES_CARPETA_TERRAZAS else _PERFIL_OVALO)


def cargar_perfil_hotel() -> dict:
    if _ES_CARPETA_TERRAZAS:
        perfil_t = dict(_PERFIL_TERRAZAS)
        if PERFIL_HOTEL_PATH.exists():
            try:
                datos_t = json.loads(PERFIL_HOTEL_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(datos_t, dict) and datos_t.get("app_port"):
                    perfil_t["app_port"] = int(datos_t["app_port"])
            except Exception:
                pass
        try:
            PERFIL_HOTEL_PATH.write_text(
                json.dumps(perfil_t, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        return perfil_t

    if not PERFIL_HOTEL_PATH.exists():
        try:
            PERFIL_HOTEL_PATH.write_text(
                json.dumps(_DEFAULT_PERFIL, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        return dict(_DEFAULT_PERFIL)
    try:
        datos = json.loads(PERFIL_HOTEL_PATH.read_text(encoding="utf-8-sig"))
        perfil = dict(_DEFAULT_PERFIL)
        if isinstance(datos, dict):
            perfil.update({k: v for k, v in datos.items() if v is not None})
        return perfil
    except Exception:
        return dict(_DEFAULT_PERFIL)


PERFIL_HOTEL = cargar_perfil_hotel()
NOMBRE_HOTEL = str(PERFIL_HOTEL.get("nombre_hotel") or _DEFAULT_PERFIL["nombre_hotel"]).strip()

# --- MODO PRUEBA (Sistema "ovalo_mariategui_prueba") ---
# Se activa SOLO con "modo_prueba": true en data/hotel_perfil.json (carpeta data/ nunca la pisa el actualizador).
# En modo prueba: sin Supabase, sin Google Sheets, sin auto-actualización desde GitHub, sin SUNAT, sin WhatsApp.
MODO_PRUEBA = bool(PERFIL_HOTEL.get("modo_prueba", False)) and not _ES_CARPETA_TERRAZAS

# Google Sheets Maestro (leído dinámicamente de data/hotel_perfil.json en cada hotel)
SPREADSHEET_ID = str(PERFIL_HOTEL.get("spreadsheet_id") or _DEFAULT_PERFIL["spreadsheet_id"]).strip()
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Rutas posibles de credenciales de Service Account (solo lectura de ruta, nunca imprimir secreto)
CREDENTIAL_CANDIDATES = [
    DATA_DIR / "credentials.json",
    BASE_APP_DIR / "credentials.json",
    Path(r"c:\Users\josec\Downloads\trabajo\Sistema de agentes IA\HostalAI\config\credentials.json"),
    Path(r"C:\Hostal_Sistema\config\credentials.json"),
    BASE_APP_DIR.parent / "HostalAI" / "config" / "credentials.json",
]


def obtener_ruta_credenciales() -> str:
    for p in CREDENTIAL_CANDIDATES:
        if p.exists():
            return str(p)
    return str(CREDENTIAL_CANDIDATES[2])


# Rutas del motor SUNAT / WhatsApp en C:\Hostal_Sistema
HOSTAL_SISTEMA_DIR = Path(r"C:\Hostal_Sistema")
SCRIPTS_HOSTAL_DIR = HOSTAL_SISTEMA_DIR / "scripts"
EJECUTAR_REMOTO_SCRIPT = HOSTAL_SISTEMA_DIR / "ejecutar_remoto.py"
EMITIR_COMPROBANTE_SCRIPT = SCRIPTS_HOSTAL_DIR / "emitir_comprobante_sunat.py"
BRAZO_WSP_SCRIPT = SCRIPTS_HOSTAL_DIR / "brazo_wsp.py"
REGLAS_AUDITORIA_JSON = HOSTAL_SISTEMA_DIR / "reglas_auditoria.json"
DESCARGAS_RECEPCION_DIR = HOSTAL_SISTEMA_DIR / "descargas_recepcion"

# APIsPERU (Consulta DNI RENIEC / RUC SUNAT cuando hay internet; fallback a padrón SQLite offline)
APISPERU_TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
    "eyJlbWFpbCI6ImVkdWFyZG9jYWxhbmlndWV2YXJhQGdtYWlsLmNvbSIsImp0aSI6ImIxMDM1MmRkY2VmZDU4MDkifQ."
    "0sX69ADETj8CXcs_HLn9_n9ioe6KW2IwLKo_ot5XtiA"
)
APISPERU_DNI_URL = "https://dniruc.apisperu.com/api/v1/dni"
APISPERU_RUC_URL = "https://dniruc.apisperu.com/api/v1/ruc"

# Puerto del servidor local de Recepción (0.0.0.0 habilita vinculación Wi-Fi con Tablet Samsung S8+)
APP_HOST = "127.0.0.1" if MODO_PRUEBA else "0.0.0.0"
APP_PORT = int(PERFIL_HOTEL.get("app_port") or 8080)

# Intervalo de sincronización automática en segundo plano (segundos)
SYNC_INTERVAL_SECONDS = 25


# --- Estructura unificada para los 3 hoteles ---
COL_TIPO_ADICIONAL = str(PERFIL_HOTEL.get("col_tipo_adicional") or "BA").strip().upper()
FORMULA_SNACKS_EN_S2 = bool(PERFIL_HOTEL.get("formula_snacks_en_s2", False))


def _col_letra_a_indice(letras: str) -> int:
    n = 0
    for ch in letras.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1  # base 0


COL_TIPO_ADICIONAL_IDX = _col_letra_a_indice(COL_TIPO_ADICIONAL)

# --- Sincronización en Vivo con Supabase (Grupo 2 Compartido: ovalo_sistema.db <-> hostal-ovalo <-> Laptop 2) ---
SUPABASE_ENABLED = (not _ES_CARPETA_TERRAZAS) and (not MODO_PRUEBA)
SUPABASE_URL = "https://fmlfqupwscymosvwnnzx.supabase.co"
# Puente de pruebas: en MODO_PRUEBA solo escribe en sandbox_change_log (jamas en las tablas reales).
SUPABASE_SANDBOX_CONNECTED = bool(PERFIL_HOTEL.get("conectar_pruebas_supabase", True)) and MODO_PRUEBA
# SHA-256 del codigo de autorizacion para promover a operativa / tocar Arnes2 (codigo: ver al titular).
ARNES_AUTH_SHA256 = "24fe5b2863951e8d97cd7e19f2a5fed2534686189241b58951f0e7e2e8d666d7"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0."
    "pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g"
)
SUPABASE_SYNC_TOKEN = "ovalo_master_sync_2026_k9x"
EQUIPO_ID = (
    "LAPTOP_1_ADMIN"
    if (BASE_APP_DIR.parent / "wsp_wspdocs_russ.db").exists()
    else f"LAPTOP_2_RECEPCION_{socket.gethostname()}"
)
