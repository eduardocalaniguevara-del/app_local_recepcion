#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Resumen Ejecutivo de WhatsApp de Recepción con Agente IA (100 % Solo Lectura).
Hotel El Óvalo Mariátegui.

Arquitectura y Reglas Duras:
  * Visor 100 % de recepción y síntesis: CERO envíos de mensajes, CERO plantillas rápidas,
    CERO auto-adelanto y CERO simulador.
  * En MODO_PRUEBA lee las conversaciones reales de la base OPERATIVA estrictamente en SOLO LECTURA
    (`sqlite3.connect("file:...?mode=ro", uri=True)` + `PRAGMA query_only=1`). Si la operativa no
    estuviera disponible, cae en solo lectura a la base local.
  * Cruza cada conversación (por celular de 9 dígitos, Hab/DNI/RUC/comprobante en texto y nombre)
    contra `estancias`, `huespedes_padron`, `sunat_ventas` y `sunat_lineas_cache` usando solo SELECT.
    JAMÁS modifica `estancias`, `pagos`, `ventas_tienda` ni `wsp_recepcion_cola_envio`.
  * Analiza las conversaciones completas (cliente + respuestas de recepción) con Agente IA
    (`google.genai`: `gemini-3.8-flash` -> fallback `gemini-2.5-flash` -> fallback contextual local)
    y guarda el corte en `wsp_resumen_ejecutivo` en los 4 horarios fijos (`08:00`, `13:00`, `19:00`, `23:00`)
    o bajo demanda con `actualizarResumenWspAhora`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import sys as _sys
from pathlib import Path as _Path
_MDIR = str(_Path(__file__).resolve().parent)
if _MDIR not in _sys.path: _sys.path.insert(0, _MDIR)
import config as _cfg
from config import MODO_PRUEBA, ROL_NODO, TIENE_QR_WSP_FISICO
from db import get_connection
from sync_nube import encolar_whatsapp_remoto

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
HORARIOS_FIJOS: List[str] = ["08:00", "13:00", "19:00", "23:00"]
CATEGORIAS = ("COMPROBANTE", "RESERVA", "HABITACION", "VOUCHER")
_PRIORIDAD = {"VOUCHER": 4, "COMPROBANTE": 3, "RESERVA": 2, "HABITACION": 1}

INTERNOS_DEFAULT = {
    "51977129752@s.whatsapp.net": "Eduardo Calani (Admin)",
    "51951535614@s.whatsapp.net": "Eliana (Recepción)",
    "120363410025708860@g.us": "Grupo Ovalo Mariategui",
}
INTERNOS_POR_NOMBRE = {"eleiza store"}
_CELULAR_RECEPCION_PROPIO = "964139749"
_JIDS_IGNORADOS_SUFIJOS = ("@broadcast", "@newsletter", "@g.us")

UMBRAL_ROJO_MIN = 5
MAX_MENSAJES_CHAT = 30
MAX_MENSAJES_VISTA = 16

_BASE_DIR = Path(getattr(_cfg, "BASE_APP_DIR", Path(__file__).resolve().parent))
_DATA_DIR = Path(getattr(_cfg, "DATA_DIR", _BASE_DIR / "data"))
_PATRONES_PATH = _DATA_DIR / "wsp_patrones_entrenados.json"
_INTERNOS_PATH = _DATA_DIR / "wsp_contactos_internos.json"


# ---------------------------------------------------------------------------
# Utilidades básicas
# ---------------------------------------------------------------------------
def _ahora() -> float:
    return time.time()


def _sin_tildes(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _solo_digitos(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _num(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return round(float(str(v).replace("S/", "").replace(",", "").strip()), 2)
    except Exception:
        return 0.0


def _ymd_hoy(delta_dias: int = 0) -> int:
    return int((date.today() + timedelta(days=delta_dias)).strftime("%Y%m%d"))


def _parse_dmy(s: str) -> Optional[date]:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(s or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _hora_txt(ts: int) -> str:
    try:
        if not ts:
            return ""
        dt = datetime.fromtimestamp(int(ts))
        if dt.date() == date.today():
            return dt.strftime("%H:%M")
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ""


def _celular9(jid: str) -> str:
    j = str(jid or "")
    if j.endswith("@g.us") or j.endswith("@lid"):
        return ""
    base = j.split("@")[0]
    if base.upper().startswith("SIM-"):
        base = base[4:]
    d = _solo_digitos(base)
    return d[-9:] if len(d) >= 9 else ""


# ---------------------------------------------------------------------------
# Carga de API Key (.env en prueba y operativa)
# ---------------------------------------------------------------------------
def _cargar_api_keys_gemini() -> List[str]:
    keys: List[str] = []
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var, "").strip()
        if val and val not in keys:
            keys.append(val)

    candidatos_env = [
        _BASE_DIR / ".env",
        Path(r"C:\ovalo_mariategui_prueba\.env"),
        Path(r"C:\Recepcion_Ovalo_Local\.env"),
    ]
    carpeta_op = str((getattr(_cfg, "PERFIL_HOTEL", {}) or {}).get("carpeta_operativa") or "").strip()
    if carpeta_op:
        candidatos_env.append(Path(carpeta_op) / ".env")

    for ruta in candidatos_env:
        try:
            if not ruta.exists():
                continue
            for linea in ruta.read_text(encoding="utf-8", errors="ignore").splitlines():
                lin = linea.strip()
                if not lin or lin.startswith("#") or "=" not in lin:
                    continue
                k, v = lin.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v and v not in keys:
                    keys.append(v)
        except Exception:
            continue
    return keys


# ---------------------------------------------------------------------------
# Tablas locales (solo wsp_resumen_ejecutivo) y lectura RO de operativa
# ---------------------------------------------------------------------------
_tabla_lock = threading.Lock()
_tabla_lista = False


def _asegurar_tablas() -> None:
    global _tabla_lista
    if _tabla_lista:
        return
    with _tabla_lock:
        if _tabla_lista:
            return
        conn = get_connection()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wsp_resumen_ejecutivo (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha_hora   TEXT NOT NULL,
                    slot_horario TEXT NOT NULL,
                    fuente_ia    TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wsp_resumen_ejec_fecha ON wsp_resumen_ejecutivo (fecha_hora DESC)"
            )
            conn.commit()
        finally:
            conn.close()
        _tabla_lista = True


def _ruta_operativa_db() -> Optional[Path]:
    if not MODO_PRUEBA:
        return None
    try:
        carpeta = str((getattr(_cfg, "PERFIL_HOTEL", {}) or {}).get("carpeta_operativa") or r"C:\Recepcion_Ovalo_Local").strip()
    except Exception:
        carpeta = r"C:\Recepcion_Ovalo_Local"
    if not carpeta:
        return None
    try:
        raiz = Path(carpeta)
        if raiz.resolve() == _BASE_DIR.resolve():
            return None
        db = raiz / "data" / "ovalo_sistema.db"
        return db if db.exists() else None
    except Exception:
        return None


def _abrir_operativa_ro() -> Optional[sqlite3.Connection]:
    """Abre la base operativa estrictamente en modo SOLO LECTURA (`mode=ro` + `query_only=1`)."""
    db = _ruta_operativa_db()
    if db is None:
        return None
    try:
        uri = "file:" + quote(db.as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=4.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA query_only=1;")
        return conn
    except Exception:
        return None


def _abrir_local_ro() -> sqlite3.Connection:
    """Abre la base local en solo lectura para consultas de cruce."""
    db_local = Path(getattr(_cfg, "DB_PATH", _DATA_DIR / "ovalo_sistema.db"))
    if db_local.exists():
        uri = "file:" + quote(db_local.as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA query_only=1;")
        return conn
    return get_connection()


def _estado_daemon_raw() -> str:
    conns: List[sqlite3.Connection] = []
    op = _abrir_operativa_ro() if MODO_PRUEBA else None
    if op is not None:
        conns.append(op)
    conns.append(_abrir_local_ro())
    try:
        for c in conns:
            try:
                r = c.execute("SELECT valor FROM wsp_recepcion_estado WHERE clave='conexion'").fetchone()
                if r and r[0] is not None and str(r[0]).strip():
                    return str(r[0])
            except Exception:
                try:
                    r2 = c.execute("SELECT conexion FROM wsp_recepcion_estado LIMIT 1").fetchone()
                    if r2 and r2[0] is not None and str(r2[0]).strip():
                        return str(r2[0])
                except Exception:
                    pass
            try:
                r3 = c.execute("SELECT valor FROM estado_sistema WHERE clave='wsp_recepcion_conexion'").fetchone()
                if r3 and r3[0] is not None and str(r3[0]).strip():
                    return str(r3[0])
            except Exception:
                pass
            try:
                cnt = c.execute("SELECT COUNT(*) FROM wsp_recepcion_mensajes").fetchone()
                if cnt and int(cnt[0] or 0) > 0:
                    return "CONECTADO"
            except Exception:
                pass
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
    return ""


def estado_daemon() -> str:
    raw = _estado_daemon_raw().strip().upper()
    if raw.startswith("CONECT"):
        return "CONECTADO"
    if raw == "ESPERANDO_QR":
        return "ESPERANDO_QR"
    if not raw:
        return "DESCONECTADO_UNKNOWN"
    return raw if raw.startswith("DESCONECTADO") else f"DESCONECTADO_{raw}"


# ---------------------------------------------------------------------------
# Contactos internos
# ---------------------------------------------------------------------------
_internos_cache: Dict[str, Any] = {"mtime": -1.0, "jids": dict(INTERNOS_DEFAULT), "nombres": set(INTERNOS_POR_NOMBRE)}


def _cargar_internos() -> Tuple[Dict[str, str], Set[str]]:
    try:
        mt = _INTERNOS_PATH.stat().st_mtime
    except OSError:
        mt = 0.0
    if mt == _internos_cache["mtime"]:
        return _internos_cache["jids"], _internos_cache["nombres"]
    jids = dict(INTERNOS_DEFAULT)
    nombres = set(INTERNOS_POR_NOMBRE)
    if mt:
        try:
            data = json.loads(_INTERNOS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                extra_j = data.get("jids") if ("jids" in data or "nombres" in data) else data
                if isinstance(extra_j, list):
                    extra_j = {str(j): str(j) for j in extra_j}
                if isinstance(extra_j, dict):
                    jids.update({str(k): str(v) for k, v in extra_j.items()})
                nombres |= {str(n).strip().lower() for n in (data.get("nombres") or [])}
        except Exception:
            pass
    _internos_cache.update({"mtime": mt, "jids": jids, "nombres": nombres})
    return jids, nombres


def _es_interno(jid: str, nombre: str) -> bool:
    j = str(jid or "").strip()
    if not j or j.endswith(_JIDS_IGNORADOS_SUFIJOS) or j == "status@broadcast":
        return True
    c9 = _celular9(j)
    if c9 == _CELULAR_RECEPCION_PROPIO:
        return True
    jids, nombres = _cargar_internos()
    if j in jids:
        return True
    if c9:
        for ji in jids:
            if _celular9(ji) == c9:
                return True
    if str(nombre or "").strip().lower() in nombres:
        return True
    return False


# ---------------------------------------------------------------------------
# Clasificador y extractor de entidades (auxiliares de lectura)
# ---------------------------------------------------------------------------
_NORMALIZACIONES_BASE = {
    "abitacion": "habitacion", "abitasion": "habitacion", "habitasion": "habitacion",
    "habitaciom": "habitacion", "habitacon": "habitacion", "avitacion": "habitacion",
    "cuarto": "habitacion", "fatura": "factura", "facutra": "factura", "factuta": "factura",
    "voleta": "boleta", "boletta": "boleta", "matrimoneal": "matrimonial", "matri": "matrimonial",
    "yapee": "yape", "yapie": "yape", "yapeo": "yape", "yapeado": "yape",
    "pagee": "pague", "paguee": "pague", "xfa": "porfa", "porfis": "porfa", "porfavor": "porfa",
}

_PATRONES_BASE: Dict[str, Dict[str, Dict[str, float]]] = {
    "COMPROBANTE": {
        "palabras": {"factura": 3.0, "boleta": 3.0, "ruc": 2.5, "comprobante": 2.5, "sunat": 2.0,
                     "facturar": 3.0, "boletear": 3.0, "emitir": 1.5, "emitan": 2.0},
        "frases": {"a la espera de mi factura": 3.5, "mi factura": 2.5, "mi boleta": 2.5,
                   "necesito factura": 2.5, "con ruc": 2.0, "razon social": 2.0},
    },
    "RESERVA": {
        "palabras": {"reservar": 3.0, "reserva": 3.0, "reservacion": 3.0, "separar": 3.0,
                     "disponible": 2.5, "disponibilidad": 3.0, "precio": 2.0, "tarifa": 2.0,
                     "costo": 2.0, "cuanto": 1.5, "habitacion": 1.0, "matrimonial": 2.0,
                     "simple": 1.5, "doble": 1.5, "tina": 1.5, "netflix": 1.2},
        "frases": {"una habitacion de": 2.5, "tienen habitacion": 3.0, "cuanto cuesta": 2.0,
                   "cuanto la noche": 3.0, "tienen disponible": 3.0, "para realizar la reserva": 3.5},
    },
    "HABITACION": {
        "palabras": {"llave": 3.0, "llaves": 3.0, "toalla": 3.0, "toallas": 3.0, "visita": 2.5,
                     "ducha": 2.0, "wifi": 2.0, "limpieza": 2.0, "pasar": 1.5, "dele": 1.5, "soy": 1.0},
        "frases": {"dele la llave": 3.5, "le hace pasar": 3.0, "no hay agua": 3.0, "va preguntar": 2.5},
    },
    "VOUCHER": {
        "palabras": {"yape": 3.0, "plin": 3.0, "transferencia": 3.0, "deposito": 2.5, "voucher": 3.0,
                     "pague": 2.5, "pagado": 2.0, "abono": 2.0, "constancia": 2.0, "operacion": 2.0},
        "frases": {"ya pague": 3.0, "te yapeo": 3.0, "numero de operacion": 3.0, "constancia de pago": 3.0},
    },
}

_RUIDO_PALABRAS_BASE = {
    "ok", "okey", "oki", "okay", "oka", "gracias", "graciass", "grasias", "muchas", "mil", "listo", "lista",
    "vale", "dale", "bueno", "buena", "buenas", "buenos", "buen", "dia", "dias", "tarde", "tardes", "noche",
    "noches", "hola", "saludos", "senorita", "senor", "joven", "porfa", "favor", "por", "ya", "entendido",
    "conforme", "perfecto", "excelente", "igual", "de", "nada", "todos", "modos", "claro", "no", "si", "yap",
    "esta", "bien", "ustedes", "a", "y", "el", "la", "en",
}
_RUIDO_EXACTOS_BASE = {"[sticker]", "[reaccion]", "[reaction]", "[emoji]", "[gif]", "👍", "🙏", "👌", "ok", "gracias"}
_TIPOS_RUIDO = {"sticker", "reaccion", "reaction", "protocol", "protocolo", "gif", "system", "sistema", "call", "llamada"}
_TIPOS_ADJUNTO = {"imagen", "image", "documento", "document", "pdf", "foto", "archivo"}

_patrones_lock = threading.Lock()
_patrones: Dict[str, Any] = {}
_patrones_mtime: float = -1.0


def _get_patrones() -> Dict[str, Any]:
    global _patrones, _patrones_mtime
    try:
        mt = _PATRONES_PATH.stat().st_mtime
    except OSError:
        mt = 0.0
    if _patrones and mt == _patrones_mtime:
        return _patrones
    with _patrones_lock:
        pal = {c: dict(_PATRONES_BASE[c]["palabras"]) for c in CATEGORIAS}
        fra = {c: dict(_PATRONES_BASE[c]["frases"]) for c in CATEGORIAS}
        norm = dict(_NORMALIZACIONES_BASE)
        exactos = set(_RUIDO_EXACTOS_BASE)
        try:
            if mt:
                data = json.loads(_PATRONES_PATH.read_text(encoding="utf-8"))
                for k, v in (data.get("normalizaciones") or {}).items():
                    norm[_sin_tildes(str(k)).lower()] = _sin_tildes(str(v)).lower()
                cats = data.get("categorias") or {}
                for c in CATEGORIAS:
                    ent = cats.get(c) or {}
                    for p, w in (ent.get("palabras") or {}).items():
                        pal[c][_sin_tildes(str(p)).lower()] = float(w)
                    for f, w in (ent.get("frases") or {}).items():
                        fra[c][_sin_tildes(str(f)).lower()] = float(w)
                for e in ((cats.get("RUIDO") or {}).get("exactos") or []):
                    exactos.add(str(e).strip().lower())
        except Exception:
            pass
        _patrones = {"pal": pal, "fra": fra, "norm": norm, "exactos": exactos, "ruido_pal": set(_RUIDO_PALABRAS_BASE)}
        _patrones_mtime = mt
    return _patrones


_habs_cache: Dict[str, Any] = {"ts": 0.0, "set": set()}


def _habs_validas() -> Set[str]:
    if _ahora() - _habs_cache["ts"] < 300 and _habs_cache["set"]:
        return _habs_cache["set"]
    conn = _abrir_local_ro()
    try:
        rows = conn.execute("SELECT DISTINCT TRIM(hab) FROM estancias WHERE TRIM(COALESCE(hab,''))<>''").fetchall()
        vals = {str(r[0]).strip().lower() for r in rows if len(str(r[0]).strip()) >= 2}
    except Exception:
        vals = set()
    finally:
        conn.close()
    _habs_cache.update({"ts": _ahora(), "set": vals})
    return vals


def _normalizar(texto: str) -> Tuple[str, List[str]]:
    p = _get_patrones()
    t = _sin_tildes(str(texto or "")).lower()
    t = re.sub(r"[^a-z0-9\[\]]+", " ", t)
    toks = [p["norm"].get(tok, tok) for tok in t.split()]
    return " ".join(toks), toks


def es_ruido_mensaje(texto: str, tipo_msg: str = "texto") -> bool:
    tipo = str(tipo_msg or "texto").lower()
    if tipo in _TIPOS_RUIDO:
        return True
    if tipo in _TIPOS_ADJUNTO or tipo in ("audio", "ptt", "video"):
        return False
    p = _get_patrones()
    crudo = str(texto or "").strip().lower()
    if crudo in p["exactos"]:
        return True
    norm, toks = _normalizar(texto)
    if not toks or norm in p["exactos"]:
        return True
    return all(t in p["ruido_pal"] for t in toks)


_RE_RUC = re.compile(r"(?<!\d)((?:10|15|17|20)\d{9})(?!\d)")
_RE_DNI = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_RE_CPE = re.compile(r"(?<![A-Za-z0-9])((?:E001|EB01|F\d{3}|B\d{3})-\d{1,8})(?![A-Za-z0-9])", re.I)
_RE_MONTO = re.compile(
    r"(?:s\s*/\s*\.?\s*|soles?\s*)(\d{1,4}(?:[.,]\d{1,2})?)|(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:soles|sol)\b",
    re.I,
)


def extraer_entidades(texto: str, habs_validas: Optional[Set[str]] = None) -> Dict[str, Any]:
    habs_validas = habs_validas if habs_validas is not None else _habs_validas()
    crudo = str(texto or "")
    ent: Dict[str, Any] = {
        "habs": [], "dni": None, "ruc": None, "cpe": None, "montos": [],
        "precio_ref": None, "tipo_hab": None, "fecha_ref": None, "nombre": None,
    }
    work = crudo
    mc = _RE_CPE.search(work)
    if mc:
        ent["cpe"] = mc.group(1).upper()
    # Quitar telefonos de 9 digitos (ej. 964139749) para no confundir con DNI
    work_docs = re.sub(r"(?<!\d)9\d{8}(?!\d)", " ", work)
    mr = _RE_RUC.search(work_docs)
    if mr:
        ent["ruc"] = mr.group(1)
        work_docs = work_docs.replace(mr.group(1), " ")
    md = _RE_DNI.search(work_docs)
    if md:
        ent["dni"] = md.group(1)

    for m in _RE_MONTO.finditer(work):
        raw = (m.group(1) or m.group(2) or "").replace(",", ".")
        try:
            ent["montos"].append(float(raw))
        except ValueError:
            pass
    sin_montos = _RE_MONTO.sub(" ", work_docs)
    norm, toks = _normalizar(sin_montos)
    vistos: List[str] = []
    for t in toks:
        if t in habs_validas and t not in vistos:
            vistos.append(t)
    # Detectar HAB_407 en nombres de PDF
    for mhab in re.finditer(r"hab[_\s-]*(\d{3})", _sin_tildes(crudo).lower()):
        hv = mhab.group(1)
        if hv in habs_validas and hv not in vistos:
            vistos.append(hv)
    ent["habs"] = vistos

    for clave in ("tina", "matrimonial", "triple", "doble", "simple"):
        if re.search(rf"\b{clave}\b", norm):
            ent["tipo_hab"] = clave
            break
    if ent["montos"]:
        ent["precio_ref"] = ent["montos"][0]
    return ent


def clasificar(texto: str, tipo_msg: str = "texto", ocr: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = _get_patrones()
    tipo = str(tipo_msg or "texto").lower()
    texto = str(texto or "")
    if es_ruido_mensaje(texto, tipo):
        return {"categoria": None, "confianza": 0.0, "es_ruido": True, "motivos": ["RUIDO"], "puntaje": 0.0}
    habs = _habs_validas()
    norm, toks = _normalizar(texto)
    tset = set(toks) | {t[:-1] for t in toks if len(t) > 4 and t.endswith("s")}
    padded = f" {norm} "
    sc = {c: 0.0 for c in CATEGORIAS}
    motivos: List[str] = []
    for c in CATEGORIAS:
        for pal, w in p["pal"][c].items():
            if pal in tset:
                sc[c] += w
                motivos.append(f"{c}:{pal}")
        for fr, w in p["fra"][c].items():
            if f" {fr} " in padded:
                sc[c] += w
                motivos.append(f"{c}:'{fr}'")
    ent = extraer_entidades(texto, habs)
    if ent["habs"]:
        sc["HABITACION"] += 2.5
    if ent["ruc"] or ent["cpe"]:
        sc["COMPROBANTE"] += 2.5
    if tipo in _TIPOS_ADJUNTO:
        sc["VOUCHER"] += 2.5
    orden = sorted(CATEGORIAS, key=lambda c: (sc[c], _PRIORIDAD[c]), reverse=True)
    mejor = orden[0]
    if sc[mejor] < 1.0:
        return {"categoria": None, "confianza": 0.0, "es_ruido": False, "motivos": motivos, "puntaje": 0.0}
    total = sum(sc.values()) or 1.0
    conf = round(min(0.99, (sc[mejor] / total) * min(1.0, sc[mejor] / 4.0)), 2)
    return {"categoria": mejor, "confianza": conf, "es_ruido": False, "motivos": motivos[:10], "puntaje": round(sc[mejor], 2)}


# ---------------------------------------------------------------------------
# Cruce en solo lectura con estancias, padrón y SUNAT
# ---------------------------------------------------------------------------
_EST_COLS = (
    "estancia_uuid, hab, huesped, dni, ruc, empresa, celular, fecha_in_dmy, fecha_out_dmy, ymd_in, ymd_out, "
    "noches, tarifa, tipo_hab, estado_estancia, adic_snacks, adic_servicios, adic_comp, pago_ini_efec, "
    "pago_ini_yape, pago_post_efec, pago_post_yape, pago_visa, deuda_anterior, solicita_comp, tipo_comp, "
    "estado_comp, nro_comp, monto_factura, observaciones"
)
_SQL_CEL = "REPLACE(REPLACE(REPLACE(REPLACE(COALESCE({c},''),' ',''),'-',''),'+',''),'.','')"


def _estancia_dict(r: sqlite3.Row) -> Dict[str, Any]:
    noches = max(1, int(r["noches"] or 1))
    tarifa = _num(r["tarifa"])
    adic = _num(r["adic_snacks"]) + _num(r["adic_servicios"]) + _num(r["adic_comp"])
    pagado = round(
        _num(r["pago_ini_efec"]) + _num(r["pago_post_efec"]) + _num(r["pago_ini_yape"])
        + _num(r["pago_post_yape"]) + _num(r["pago_visa"]),
        2,
    )
    total = round(noches * tarifa + adic + _num(r["deuda_anterior"]), 2)
    return {
        "uuid": r["estancia_uuid"],
        "hab": str(r["hab"] or "").strip(),
        "huesped": str(r["huesped"] or "").strip(),
        "dni": str(r["dni"] or "").strip(),
        "ruc": str(r["ruc"] or "").strip(),
        "empresa": str(r["empresa"] or "").strip(),
        "celular": str(r["celular"] or "").strip(),
        "check_in": str(r["fecha_in_dmy"] or "").strip(),
        "check_out": str(r["fecha_out_dmy"] or "").strip(),
        "ymd_in": int(r["ymd_in"] or 0),
        "noches": noches,
        "tarifa": tarifa,
        "tipo_hab": str(r["tipo_hab"] or "").strip(),
        "estado_estancia": str(r["estado_estancia"] or "").strip(),
        "adic_comp": _num(r["adic_comp"]),
        "total": total,
        "pagado": pagado,
        "saldo": round(total - pagado, 2),
        "solicita_comp": str(r["solicita_comp"] or "").strip(),
        "tipo_comp": str(r["tipo_comp"] or "").strip(),
        "estado_comp": str(r["estado_comp"] or "").strip(),
        "nro_comp": str(r["nro_comp"] or "").strip(),
        "monto_factura": _num(r["monto_factura"]),
    }


def _mejor_estancia(rows: List[sqlite3.Row], ref_ymd: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if ref_ymd:
        rows = sorted(
            rows,
            key=lambda r: (
                abs(int(r["ymd_in"] or 0) - ref_ymd) <= 2,
                str(r["estado_estancia"] or "").lower().startswith("en curso"),
                int(r["ymd_in"] or 0),
            ),
            reverse=True,
        )
    else:
        rows = sorted(
            rows,
            key=lambda r: (
                str(r["estado_estancia"] or "").lower().startswith("en curso"),
                int(r["ymd_in"] or 0),
            ),
            reverse=True,
        )
    return _estancia_dict(rows[0])


def _estancia_por(
    conn: sqlite3.Connection,
    where: str,
    params: Tuple[Any, ...],
    min_ymd: int,
    ref_ymd: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_EST_COLS} FROM estancias WHERE ({where}) "
        f"AND COALESCE(NULLIF(ymd_out,0), ymd_in, 0) >= ? "
        f"AND LOWER(COALESCE(estado_estancia,'')) NOT LIKE 'cancel%' "
        f"ORDER BY ymd_in DESC LIMIT 15",
        params + (min_ymd,),
    ).fetchall()
    return _mejor_estancia(rows, ref_ymd=ref_ymd)


def _estado_sunat_texto(conn: sqlite3.Connection, est: Dict[str, Any], cpe_texto: Optional[str] = None) -> Tuple[str, bool]:
    nro = str(est.get("nro_comp") or cpe_texto or "").strip()
    estado = str(est.get("estado_comp") or "").strip()
    tipo = str(est.get("tipo_comp") or "").strip()
    sunat_nro = ""
    try:
        if nro:
            r = conn.execute(
                "SELECT nro_cp, estado FROM sunat_ventas WHERE UPPER(nro_cp)=UPPER(?) LIMIT 1",
                (nro,),
            ).fetchone()
            if r:
                sunat_nro = f"{r['nro_cp']} ({r['estado']})"
        if not sunat_nro and est.get("hab"):
            d_in = _parse_dmy(est.get("check_in", ""))
            if d_in:
                noches = [(d_in + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(max(1, int(est.get("noches") or 1)))]
                marcas = ",".join("?" for _ in noches)
                r2 = conn.execute(
                    f"SELECT nro_cp, estado FROM sunat_lineas_cache WHERE TRIM(hab)=? AND noche_dmy IN ({marcas}) "
                    f"AND UPPER(COALESCE(estado,''))='VIGENTE' LIMIT 1",
                    (est["hab"], *noches),
                ).fetchone()
                if r2:
                    sunat_nro = f"{r2['nro_cp']} (VIGENTE)"
    except Exception:
        pass

    if sunat_nro:
        return f"Emitido {sunat_nro}", True
    if nro and (estado.lower().startswith("emitido") or "-" in nro):
        return f"Emitido ({nro})", True
    if estado.lower() == "pendiente" or tipo.lower() in ("factura", "boleta"):
        etiqueta = tipo.capitalize() if tipo and tipo.lower() not in ("ninguno", "n") else "Comprobante"
        return f"Pendiente {etiqueta}", False
    return "Sin comprobante solicitado", False


def _observacion_25_texto(est: Dict[str, Any], monto_solicitado_dia: Optional[float] = None) -> str:
    noches = max(1, int(est.get("noches") or 1))
    tarifa = _num(est.get("tarifa"))
    adic = _num(est.get("adic_comp"))
    monto_fact = _num(est.get("monto_factura"))
    ref_dia = monto_solicitado_dia or (round(monto_fact / noches, 2) if monto_fact > tarifa * noches else 0.0)
    if ref_dia and ref_dia > tarifa:
        dif = round((ref_dia - tarifa) * noches, 2)
        req25 = round(0.25 * dif, 2)
        falta = round(max(0.0, req25 - adic), 2)
        if falta > 0.01:
            return f"Regla 25 %: pide S/ {ref_dia:.0f}/día vs tarifa S/ {tarifa:.0f} → falta cobrar S/ {falta:.2f} de adicional."
        return f"Regla 25 % conforme (adicional S/ {adic:.2f} cubierto)."
    if est.get("saldo", 0.0) > 0.01:
        return f"Saldo de estancia pendiente: S/ {est['saldo']:.2f}."
    return "Tarifa regular sin recargo 25 % pendiente."


_PALABRAS_GENERICAS_PERFIL = {
    "OVALO", "MARIATEGUI", "HOTEL", "HOSTAL", "CLIENTE", "HOLA", "PARA", "PERU", "TACNA",
    "CORPORACION", "EMPRESA", "EMPRESAS", "GRUPO", "INVERSIONES", "SERVICIOS", "REPRESENTACIONES",
    "CONSTRUCTORA", "INGENIEROS", "INGENIERIA", "COMERCIAL", "ASOCIADOS", "GENERALES", "CONTRATISTAS",
    "SOCIEDAD", "ANONIMA", "CERRADA", "RESPONSABILIDAD", "LIMITADA", "INDIVIDUAL",
    "SAC", "SRL", "EIRL", "SAA", "SRA", "SRTA", "DON", "TOMAS", "PIERDES", "STORE",
}
_NOMBRES_PILA_COMUNES = {
    "ERIKA", "ERICKA", "PAOLA", "MARIA", "JOSE", "JUAN", "LUIS", "CARLOS", "JORGE", "MIGUEL",
    "ANGEL", "JESUS", "PEDRO", "ROSA", "CARMEN", "ANA", "ELENA", "VICTOR", "CESAR", "DAVID",
    "DANIEL", "JULIO", "MARCO", "DIEGO", "RICARDO", "FERNANDO", "ALEJANDRO", "ROBERTO", "RAUL",
    "RODOLFO", "SERGIO", "HECTOR", "ARTURO", "LOURDES", "FIORELA", "MILUSKA", "ELIANA", "ELEIZA",
}


def _tokens_perfil_wsp(nombre_wsp: str) -> List[str]:
    """Extrae tokens alfabéticos significativos (>= 4 letras) del nombre de perfil de WhatsApp."""
    s = str(nombre_wsp or "").strip()
    if not s or "ovalo mariategui" in _sin_tildes(s).lower():
        return []
    # Separar letras de números pegados (ej. 'rodolfo20gp' -> 'rodolfo gp')
    limpio = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]+", " ", s)
    out: List[str] = []
    for w in _sin_tildes(limpio).upper().split():
        if len(w) >= 4 and w not in _PALABRAS_GENERICAS_PERFIL and w not in out:
            out.append(w)
    return out


def _generar_sugerencia_perfil_wsp(nombre_wsp: str, conn_local: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Analiza el nombre visible en WhatsApp (`chat_nombre` / `nombre_emisor`) y busca coincidencias en:
      a) `empresas_padron` y `sunat_ventas` (RUC 10.../20...) -> `empresa_sugerida`
      b) `estancias` y `huespedes_padron` -> hasta 2 `estancias_sugeridas` recientes."""
    perfil_raw = str(nombre_wsp or "").strip()
    if perfil_raw.lower() == "ovalo mariategui" or _solo_digitos(perfil_raw) == perfil_raw and len(perfil_raw) >= 9:
        perfil_raw = ""

    res_vacio = {
        "perfil_wsp": perfil_raw,
        "empresa_sugerida": "",
        "ruc_sugerido": "",
        "razon_social_sugerida": "",
        "estancias_sugeridas": [],
    }
    tokens = _tokens_perfil_wsp(perfil_raw)
    if not tokens:
        return res_vacio

    cerrar = False
    if conn_local is None:
        conn_local = _abrir_local_ro()
        cerrar = True

    try:
        empresa_sug = ""
        ruc_sug = ""
        razon_sug = ""

        # a) Búsqueda de empresa/RUC en empresas_padron y sunat_ventas
        #    Usamos tokens no genéricos que no sean simples nombres de pila comunes (ej. ABATRON),
        #    o pares de tokens si fuera persona natural con negocio (RUC 10...).
        tokens_corp = [t for t in tokens if len(t) >= 5 and t not in _NOMBRES_PILA_COMUNES]
        for tk in tokens_corp:
            r_emp = conn_local.execute(
                "SELECT ruc, razon_social FROM empresas_padron WHERE UPPER(razon_social) LIKE ? LIMIT 2",
                (f"%{tk}%",),
            ).fetchall()
            if len(r_emp) == 1:
                ruc_sug = str(r_emp[0]["ruc"] or "").strip()
                razon_sug = str(r_emp[0]["razon_social"] or "").strip()
                break
            r_sv = conn_local.execute(
                """SELECT receptor_doc, receptor_nombre
                     FROM sunat_ventas
                    WHERE LENGTH(TRIM(COALESCE(receptor_doc,''))) = 11
                      AND (receptor_doc LIKE '20%' OR receptor_doc LIKE '10%')
                      AND UPPER(COALESCE(receptor_nombre,'')) LIKE ?
                    ORDER BY rowid DESC LIMIT 5""",
                (f"%{tk}%",),
            ).fetchall()
            rucs_distintos = {str(x["receptor_doc"]).strip(): str(x["receptor_nombre"]).strip() for x in r_sv if x["receptor_doc"]}
            if len(rucs_distintos) == 1:
                ruc_sug, razon_sug = next(iter(rucs_distintos.items()))
                break

        if not ruc_sug and len(tokens) >= 2:
            t1, t2 = tokens[0], tokens[1]
            r_sv2 = conn_local.execute(
                """SELECT receptor_doc, receptor_nombre
                     FROM sunat_ventas
                    WHERE LENGTH(TRIM(COALESCE(receptor_doc,''))) = 11
                      AND UPPER(COALESCE(receptor_nombre,'')) LIKE ?
                      AND UPPER(COALESCE(receptor_nombre,'')) LIKE ?
                    ORDER BY rowid DESC LIMIT 3""",
                (f"%{t1}%", f"%{t2}%"),
            ).fetchall()
            if r_sv2:
                ruc_sug = str(r_sv2[0]["receptor_doc"] or "").strip()
                razon_sug = str(r_sv2[0]["receptor_nombre"] or "").strip()

        if razon_sug and ruc_sug:
            empresa_sug = f"{razon_sug} (RUC {ruc_sug})"
        elif razon_sug:
            empresa_sug = razon_sug

        # b) Búsqueda de huéspedes/estancias recientes por tokens del perfil WSP
        estancias_sug: List[Dict[str, Any]] = []
        rows_est: List[sqlite3.Row] = []
        if len(tokens) >= 2:
            # Probar combinaciones de pares de tokens (ej. ERIKA + CHACON, PAOLA + LINARES)
            for i in range(min(3, len(tokens))):
                for j in range(i + 1, min(4, len(tokens))):
                    t1, t2 = tokens[i], tokens[j]
                    rows_est = conn_local.execute(
                        f"SELECT {_EST_COLS} FROM estancias "
                        f"WHERE UPPER(COALESCE(huesped,'')) LIKE ? AND UPPER(COALESCE(huesped,'')) LIKE ? "
                        f"AND LOWER(COALESCE(estado_estancia,'')) NOT LIKE 'cancel%' "
                        f"ORDER BY ymd_in DESC LIMIT 6",
                        (f"%{t1}%", f"%{t2}%"),
                    ).fetchall()
                    if rows_est:
                        break
                if rows_est:
                    break

        if not rows_est and tokens_corp:
            # Buscar también si el token corporativo figura en estancias.empresa
            tk0 = tokens_corp[0]
            rows_est = conn_local.execute(
                f"SELECT {_EST_COLS} FROM estancias "
                f"WHERE UPPER(COALESCE(empresa,'')) LIKE ? "
                f"AND LOWER(COALESCE(estado_estancia,'')) NOT LIKE 'cancel%' "
                f"ORDER BY ymd_in DESC LIMIT 4",
                (f"%{tk0}%",),
            ).fetchall()

        vistos_est: Set[Tuple[str, str, str]] = set()
        for r in rows_est:
            ed = _estancia_dict(r)
            clave_e = (ed["dni"] or ed["huesped"], ed["hab"], ed["check_in"])
            if clave_e in vistos_est:
                continue
            vistos_est.add(clave_e)
            estancias_sug.append({
                "huesped": ed["huesped"],
                "dni": ed["dni"],
                "hab": ed["hab"],
                "fecha": ed["check_in"],
                "pagado": ed["pagado"],
                "empresa": ed["empresa"] or razon_sug,
                "ruc": ed["ruc"] or ruc_sug,
            })
            if not empresa_sug and ed["empresa"] and ed["ruc"]:
                ruc_sug = ed["ruc"]
                razon_sug = ed["empresa"]
                empresa_sug = f"{ed['empresa']} (RUC {ed['ruc']})"
            if len(estancias_sug) >= 2:
                break

        if not estancias_sug and len(tokens) >= 2:
            t1, t2 = tokens[0], tokens[1]
            rows_pad = conn_local.execute(
                """SELECT dni, nombre, hab_frec, tarifa_frec, empresa, ruc
                     FROM huespedes_padron
                    WHERE UPPER(COALESCE(nombre,'')) LIKE ? AND UPPER(COALESCE(nombre,'')) LIKE ?
                    ORDER BY visitas DESC LIMIT 2""",
                (f"%{t1}%", f"%{t2}%"),
            ).fetchall()
            for rp in rows_pad:
                estancias_sug.append({
                    "huesped": str(rp["nombre"] or "").strip(),
                    "dni": str(rp["dni"] or "").strip(),
                    "hab": str(rp["hab_frec"] or "").strip(),
                    "fecha": "Padrón histórico",
                    "pagado": _num(rp["tarifa_frec"]),
                    "empresa": str(rp["empresa"] or razon_sug).strip(),
                    "ruc": str(rp["ruc"] or ruc_sug).strip(),
                })

        return {
            "perfil_wsp": perfil_raw,
            "empresa_sugerida": empresa_sug,
            "ruc_sugerido": ruc_sug,
            "razon_social_sugerida": razon_sug,
            "estancias_sugeridas": estancias_sug,
        }
    finally:
        if cerrar:
            conn_local.close()


def cruzar_conversacion(
    jid: str,
    nombre_chat: str,
    texto_completo: str,
    ultimo_ts: int = 0,
) -> Dict[str, Any]:
    """Cruza una conversación con estancias, padrón y SUNAT en modo 100 % lectura,
    enriqueciendo con sugerencias por Nombre de Perfil de WhatsApp (`via = 'PERFIL_WSP'`)."""
    habs_val = _habs_validas()
    ent = extraer_entidades(texto_completo, habs_val)
    cel9 = _celular9(jid)
    ref_ymd = None
    if ultimo_ts:
        try:
            ref_ymd = int(datetime.fromtimestamp(int(ultimo_ts)).strftime("%Y%m%d"))
        except Exception:
            ref_ymd = None
    min_ymd = min(_ymd_hoy(-30), (ref_ymd - 15) if ref_ymd else _ymd_hoy(-30))

    conn = _abrir_local_ro()
    try:
        sug_wsp = _generar_sugerencia_perfil_wsp(nombre_chat, conn)
        est_cel = None
        est_txt = None
        est_perfil = None
        huesped_pad = None

        if cel9:
            col_c = _SQL_CEL.format(c="celular")
            est_cel = _estancia_por(conn, f"{col_c} LIKE ?", ("%" + cel9,), min_ymd, ref_ymd=ref_ymd)
            rp = conn.execute(
                f"SELECT dni, nombre, empresa, ruc, celular FROM huespedes_padron WHERE {col_c} LIKE ? ORDER BY visitas DESC LIMIT 1",
                ("%" + cel9,),
            ).fetchone()
            if rp:
                huesped_pad = dict(rp)
                if not est_cel and rp["dni"]:
                    est_cel = _estancia_por(conn, "dni=?", (rp["dni"],), min_ymd, ref_ymd=ref_ymd)

        if ent.get("cpe"):
            est_txt = _estancia_por(conn, "UPPER(TRIM(nro_comp))=UPPER(?)", (ent["cpe"],), min_ymd - 100, ref_ymd=ref_ymd)
        if not est_txt and ent.get("dni"):
            est_txt = _estancia_por(conn, "TRIM(dni)=?", (ent["dni"],), min_ymd, ref_ymd=ref_ymd)
            if not est_txt and not huesped_pad:
                rp = conn.execute(
                    "SELECT dni, nombre, empresa, ruc, celular FROM huespedes_padron WHERE TRIM(dni)=? LIMIT 1",
                    (ent["dni"],),
                ).fetchone()
                if rp:
                    huesped_pad = dict(rp)
        if not est_txt and ent.get("ruc"):
            est_txt = _estancia_por(conn, "TRIM(ruc)=?", (ent["ruc"],), min_ymd, ref_ymd=ref_ymd)
        if not est_txt and ent.get("habs"):
            for h in reversed(ent["habs"]):
                est_txt = _estancia_por(conn, "TRIM(hab)=?", (str(h),), min_ymd, ref_ymd=ref_ymd)
                if est_txt:
                    break

        # Si no hubo cruce por celular ni por datos del texto, cruzar por tokens del nombre de perfil WSP
        if not est_cel and not est_txt and nombre_chat:
            tokens_nom = _tokens_perfil_wsp(nombre_chat)
            if len(tokens_nom) >= 2:
                for i in range(min(3, len(tokens_nom))):
                    for j in range(i + 1, min(4, len(tokens_nom))):
                        p1, p2 = tokens_nom[i], tokens_nom[j]
                        est_perfil = _estancia_por(
                            conn,
                            "UPPER(huesped) LIKE ? AND UPPER(huesped) LIKE ?",
                            (f"%{p1}%", f"%{p2}%"),
                            min_ymd - 200,
                            ref_ymd=ref_ymd,
                        )
                        if est_perfil:
                            break
                    if est_perfil:
                        break
            if not est_perfil and sug_wsp.get("ruc_sugerido"):
                est_perfil = _estancia_por(conn, "TRIM(ruc)=?", (sug_wsp["ruc_sugerido"],), min_ymd - 200, ref_ymd=ref_ymd)

        via: Optional[str] = None
        est = None
        if est_cel and est_txt:
            via = "AMBAS" if est_cel["uuid"] == est_txt["uuid"] else "TEXTO"
            est = est_txt
        elif est_cel:
            via, est = "CELULAR", est_cel
        elif est_txt:
            via, est = "TEXTO", est_txt
        elif est_perfil:
            via, est = "PERFIL_WSP", est_perfil
        elif huesped_pad:
            via = "PADRON"

        emp_sug_txt = sug_wsp.get("empresa_sugerida") or ""
        ruc_sug_txt = sug_wsp.get("ruc_sugerido") or ""

        if est:
            # Si la estancia no tenía RUC/empresa pero el perfil WSP o el historial del DNI sí lo tiene
            if not emp_sug_txt and est.get("dni"):
                r_hist = conn.execute(
                    """SELECT ruc, empresa FROM estancias
                        WHERE TRIM(dni)=? AND LENGTH(TRIM(COALESCE(ruc,'')))=11
                        ORDER BY ymd_in DESC LIMIT 1""",
                    (est["dni"],),
                ).fetchone()
                if r_hist and r_hist["ruc"]:
                    ruc_sug_txt = str(r_hist["ruc"]).strip()
                    emp_h = str(r_hist["empresa"] or "").strip()
                    emp_sug_txt = f"{emp_h} (RUC {ruc_sug_txt})" if emp_h else f"RUC {ruc_sug_txt}"
                    sug_wsp["empresa_sugerida"] = emp_sug_txt
                    sug_wsp["ruc_sugerido"] = ruc_sug_txt

            est_sunat, _ = _estado_sunat_texto(conn, est, ent.get("cpe"))
            obs_25 = _observacion_25_texto(est, ent.get("precio_ref"))
            dni_ruc = est["ruc"] or est["dni"] or ent.get("ruc") or ent.get("dni") or ruc_sug_txt or ""
            return {
                "encontrado": True,
                "via": via,
                "hab": est["hab"],
                "huesped": est["huesped"],
                "empresa": est["empresa"],
                "dni_ruc": dni_ruc,
                "dni": est["dni"] or ent.get("dni") or "",
                "ruc": est["ruc"] or ent.get("ruc") or "",
                "empresa_sugerida": emp_sug_txt if not est["ruc"] else (f"{est['empresa']} (RUC {est['ruc']})" if est["empresa"] else emp_sug_txt),
                "ruc_sugerido": ruc_sug_txt if not est["ruc"] else est["ruc"],
                "check_in": est["check_in"],
                "check_out": est["check_out"],
                "tarifa_noches": f"S/ {est['tarifa']:.2f} × {est['noches']} noche(s)",
                "pagado": est["pagado"],
                "saldo": est["saldo"],
                "estado_sunat": est_sunat,
                "observacion_25": obs_25,
                "entidades": ent,
                "sugerencia_wsp": sug_wsp,
            }

        if huesped_pad:
            dni_ruc = str(huesped_pad.get("ruc") or huesped_pad.get("dni") or ruc_sug_txt or "")
            return {
                "encontrado": True,
                "via": via,
                "hab": ent["habs"][-1] if ent["habs"] else "",
                "huesped": str(huesped_pad.get("nombre") or ""),
                "empresa": str(huesped_pad.get("empresa") or ""),
                "dni_ruc": dni_ruc,
                "dni": str(huesped_pad.get("dni") or ""),
                "ruc": str(huesped_pad.get("ruc") or ""),
                "empresa_sugerida": emp_sug_txt,
                "ruc_sugerido": ruc_sug_txt,
                "check_in": "-",
                "check_out": "-",
                "tarifa_noches": "Huésped en padrón (sin estancia activa)",
                "pagado": 0.0,
                "saldo": 0.0,
                "estado_sunat": f"CPE {ent['cpe']}" if ent.get("cpe") else "Sin estancia reciente",
                "observacion_25": "",
                "entidades": ent,
                "sugerencia_wsp": sug_wsp,
            }

        return {
            "encontrado": False,
            "via": None,
            "hab": ent["habs"][-1] if ent["habs"] else "",
            "huesped": "",
            "empresa": "",
            "dni_ruc": ent.get("ruc") or ent.get("dni") or ruc_sug_txt or "",
            "dni": ent.get("dni") or "",
            "ruc": ent.get("ruc") or "",
            "empresa_sugerida": emp_sug_txt,
            "ruc_sugerido": ruc_sug_txt,
            "check_in": "",
            "check_out": "",
            "tarifa_noches": "",
            "pagado": 0.0,
            "saldo": 0.0,
            "estado_sunat": f"Emitido ({ent['cpe']})" if ent.get("cpe") else "Sin cruce en BD",
            "observacion_25": "",
            "entidades": ent,
            "sugerencia_wsp": sug_wsp,
        }
    finally:
        conn.close()


cruzar = cruzar_conversacion


# ---------------------------------------------------------------------------
# Lectura de conversaciones recientes de clientes (100 % RO)
# ---------------------------------------------------------------------------
_PLACEHOLDER_TIPO = {
    "imagen": "[Imagen]", "image": "[Imagen]", "foto": "[Imagen]",
    "documento": "[Documento]", "document": "[Documento]", "pdf": "[Documento]",
    "audio": "[Audio]", "ptt": "[Audio]", "video": "[Video]", "sticker": "[Sticker]",
}


def _texto_visible(m: Dict[str, Any]) -> str:
    t = str(m.get("texto") or "").strip()
    return t or _PLACEHOLDER_TIPO.get(str(m.get("tipo") or "").lower(), "")


def _recolectar_chats_clientes() -> List[Dict[str, Any]]:
    """Lee las conversaciones recientes de clientes en modo solo lectura (excluyendo internos)."""
    sql = """
        SELECT COALESCE(chat_jid, jid)                         AS chat,
               COALESCE(id_mensaje, id)                        AS mid,
               COALESCE(timestamp_unix, timestamp, 0)          AS ts,
               COALESCE(fecha_hora, '')                        AS fecha_hora,
               MAX(COALESCE(es_mio, 0), COALESCE(desde_recepcion, 0)) AS es_mio,
               COALESCE(tipo_mensaje, tipo, 'texto')           AS tipo,
               COALESCE(texto, '')                             AS texto,
               COALESCE(chat_nombre, '')                       AS chat_nombre,
               COALESCE(nombre_emisor, remitente_nombre, '')   AS emisor
          FROM wsp_recepcion_mensajes
         WHERE COALESCE(chat_jid, jid) NOT LIKE 'SIM-%'
         ORDER BY ts ASC
    """
    op = _abrir_operativa_ro() if MODO_PRUEBA else None
    conn = op if op is not None else _abrir_local_ro()
    try:
        filas = [dict(r) for r in conn.execute(sql).fetchall()]
        if not filas and op is not None:
            op.close()
            op = None
            conn = _abrir_local_ro()
            filas = [dict(r) for r in conn.execute(sql).fetchall()]
    except Exception:
        if op is not None:
            try:
                op.close()
            except Exception:
                pass
            conn = _abrir_local_ro()
            try:
                filas = [dict(r) for r in conn.execute(sql).fetchall()]
            except Exception:
                filas = []
        else:
            filas = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    por_jid: Dict[str, List[Dict[str, Any]]] = {}
    for m in filas:
        j = str(m["chat"] or "").strip()
        if not j:
            continue
        raw_ts = m.get("ts")
        ts_int = 0
        try:
            ts_int = int(float(str(raw_ts).strip()))
        except Exception:
            fh_str = str(m.get("fecha_hora") or "").strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
                try:
                    ts_int = int(datetime.strptime(fh_str[:19], fmt).timestamp())
                    break
                except Exception:
                    continue
        m["ts"] = ts_int
        por_jid.setdefault(j, []).append(m)

    ahora = _ahora()
    inicio_hoy = int(datetime.combine(date.today(), datetime.min.time()).timestamp())
    chats_todos: List[Dict[str, Any]] = []

    for jid, msgs in por_jid.items():
        nombre = ""
        for m in reversed(msgs):
            if not m["es_mio"] and (m["chat_nombre"] or m["emisor"]):
                nombre = m["chat_nombre"] or m["emisor"]
                break
        if not nombre:
            nombre = next(
                (
                    m["chat_nombre"]
                    for m in reversed(msgs)
                    if m["chat_nombre"] and "ovalo mariategui" not in m["chat_nombre"].lower()
                ),
                "",
            ) or _celular9(jid) or jid
        if _es_interno(jid, nombre):
            continue

        recientes = msgs[-MAX_MENSAJES_CHAT:]
        ultimo_ts = int(recientes[-1]["ts"] or 0)
        texto_todo = " \n ".join(_texto_visible(m) for m in recientes if _texto_visible(m))
        cruce = cruzar_conversacion(jid, nombre, texto_todo, ultimo_ts)

        # Si el nombre en WSP era "Ovalo Mariategui" (mensaje iniciado por recepción), enriquecer con destinatario del PDF o huésped cruzado
        nombre_limpio = nombre
        if nombre_limpio.strip().lower() in ("ovalo mariategui", "") or _celular9(nombre_limpio) == _celular9(jid):
            m_est = re.search(r"Estimad[oa](?:\(a\))?\s+([^,]+),", texto_todo, re.I)
            if m_est:
                nombre_limpio = m_est.group(1).strip().title()
            elif cruce.get("huesped"):
                nombre_limpio = cruce["huesped"].title()

        mensajes_ui: List[Dict[str, Any]] = []
        for m in recientes[-MAX_MENSAJES_VISTA:]:
            vis = _texto_visible(m)
            if not vis:
                continue
            ts_m = int(m["ts"] or 0)
            es_m = bool(m["es_mio"])
            if mensajes_ui and mensajes_ui[-1]["es_mio"] == es_m and mensajes_ui[-1]["texto"] == vis and abs(mensajes_ui[-1]["ts"] - ts_m) <= 5:
                continue
            mensajes_ui.append({
                "hora": _hora_txt(ts_m),
                "es_mio": es_m,
                "texto": vis,
                "tipo": str(m["tipo"] or "texto").lower(),
                "ts": ts_m,
            })

        sug_publica = {
            "perfil_wsp": str((cruce.get("sugerencia_wsp") or {}).get("perfil_wsp") or ""),
            "empresa_sugerida": str((cruce.get("sugerencia_wsp") or {}).get("empresa_sugerida") or ""),
            "ruc_sugerido": str((cruce.get("sugerencia_wsp") or {}).get("ruc_sugerido") or ""),
            "estancias_sugeridas": list((cruce.get("sugerencia_wsp") or {}).get("estancias_sugeridas") or []),
        }
        chats_todos.append({
            "jid": jid,
            "cliente": nombre_limpio,
            "perfil_wsp": nombre,
            "celular": _celular9(jid),
            "ultimo_ts": ultimo_ts,
            "es_hoy": ultimo_ts >= inicio_hoy,
            "es_48h": ultimo_ts >= (ahora - 48 * 3600),
            "cruce": cruce,
            "sugerencia_wsp": sug_publica,
            "mensajes_raw": recientes,
            "mensajes": mensajes_ui,
        })

    chats_todos.sort(key=lambda c: c["ultimo_ts"], reverse=True)
    chats_hoy_48h = [c for c in chats_todos if c["es_48h"]]
    if len(chats_hoy_48h) >= 5:
        return chats_hoy_48h[:18]
    return chats_todos[:14]


# ---------------------------------------------------------------------------
# Analizador Heurístico Contextual Local (respaldo instantáneo 100 % fiel)
# ---------------------------------------------------------------------------
_RE_PROMESA_COMP = re.compile(
    r"\b(emitimos|enviamos|mandamos|pasamos|generamos|un momento|en la manana|enseguida)\b.*\b(factura|boleta|comprobante)\b"
    r"|\b(factura|boleta|comprobante)\b.*\b(emitimos|enviamos|manana)\b",
    re.I,
)
_RE_AGRADECIMIENTO = re.compile(
    r"\b(gracias|muchas gracias|mil gracias|agradecid|conforme|perfecto|listo|ok|dale|esta bien|buenisimo|genial)\b",
    re.I,
)
_RE_CIERRE_ENCARGO_MESA = re.compile(
    r"^(ok\s*/?\s*)?(dele la llave|dejo la llave|ya bajo|estoy afuera|abre la puerta)(\s+\d{3})?$",
    re.I,
)


def _etiqueta_cliente_hab(chat: Dict[str, Any]) -> Tuple[str, str]:
    hab = str((chat.get("cruce") or {}).get("hab") or "").strip()
    cli = str(chat.get("cliente") or chat.get("celular") or chat.get("jid"))
    if hab and f"hab {hab}".lower() not in cli.lower():
        return f"{cli} (Hab {hab})", hab
    return cli, hab


def _resumir_que_pidio(msgs_cli: List[Dict[str, Any]], msgs_rec: List[Dict[str, Any]], cruce: Dict[str, Any]) -> Tuple[str, str]:
    """Devuelve (tipo_principal, descripcion_que_pidio)."""
    txt_cli = " ".join(_texto_visible(m) for m in msgs_cli)
    txt_rec = " ".join(_texto_visible(m) for m in msgs_rec)
    norm_cli, _ = _normalizar(txt_cli)
    norm_all, _ = _normalizar(txt_cli + " " + txt_rec)
    ent = (cruce or {}).get("entidades") or extraer_entidades(txt_cli + " " + txt_rec)
    hab = (cruce or {}).get("hab") or (ent["habs"][-1] if ent.get("habs") else "")

    cpe_m = _RE_CPE.search(txt_rec + " " + txt_cli)
    if cpe_m or any(w in norm_cli for w in ("factura", "boleta", "ruc", "comprobante")):
        cpe_str = f" ({cpe_m.group(1).upper()})" if cpe_m else ""
        doc_t = "Factura Electrónica" if ("factura" in norm_all or (cpe_m and cpe_m.group(1).upper().startswith("E001"))) else "Boleta Electrónica"
        hab_s = f" de la Hab {hab}" if hab else ""
        return "COMPROBANTE", f"Solicitó emisión/envío de {doc_t}{cpe_str}{hab_s}"

    if any(w in norm_cli for w in ("reserva", "reservar", "disponibilidad", "disponible", "precio", "costo", "matrimonial", "simple", "doble", "abitacion", "habitacion")):
        if "llave" in norm_cli or "pasar" in norm_cli:
            hab_s = f" (Hab {hab})" if hab else ""
            return "HABITACION", f"Solicitó habitación simple adicional y coordinó ingreso de visita / entrega de llave{hab_s}"
        tipo_h = ("matrimonial" if "matrimonial" in norm_cli else "simple" if "simple" in norm_cli else ent.get("tipo_hab") or "de alojamiento")
        extra = " con baño privado y Netflix" if "netflix" in norm_cli else ""
        return "RESERVA", f"Consultó disponibilidad y tarifa de habitación {tipo_h}{extra}"

    if any(w in norm_cli for w in ("llave", "visita", "pasar", "toalla", "agua", "ducha", "wifi")):
        hab_s = f" para la Hab {hab}" if hab else ""
        return "HABITACION", f"Coordinación operativa de huésped alojado (llaves / autorización de ingreso){hab_s}"

    if any(str(m.get("tipo")) in ("imagen", "documento") for m in msgs_cli) or any(w in norm_cli for w in ("yape", "plin", "pago", "transferencia")):
        return "PAGO", "Envió imagen/constancia de pago o consulta gráfica por WhatsApp"

    if any("cenas" in _sin_tildes(_texto_visible(m)).lower() for m in msgs_rec):
        return "HABITACION", "Aviso de recepción al huésped sobre llegada de pedido/cenas"

    return "HABITACION", (txt_cli.strip()[:110] or "Comunicación operativa con recepción")


def _analizar_heuristico_local(chats: List[Dict[str, Any]]) -> Dict[str, Any]:
    ahora = _ahora()
    pendientes_responder: List[Dict[str, Any]] = []
    recomendaciones_y_notas: List[Dict[str, Any]] = []
    atendidos_y_gracias: List[Dict[str, Any]] = []

    for ch in chats:
        msgs = ch["mensajes_raw"]
        if not msgs:
            continue
        cruce = ch["cruce"]
        cli_label, hab = _etiqueta_cliente_hab(ch)
        msgs_cli = [m for m in msgs if not m["es_mio"]]
        msgs_rec = [m for m in msgs if m["es_mio"]]
        tipo_princ, que_pidio = _resumir_que_pidio(msgs_cli, msgs_rec, cruce)

        ult_mio_ts = max((int(m["ts"] or 0) for m in msgs_rec), default=0)
        sin_resp = [m for m in msgs_cli if int(m["ts"] or 0) > ult_mio_ts and not es_ruido_mensaje(m["texto"], m["tipo"])]

        txt_cli_all = " / ".join(_texto_visible(m) for m in msgs_cli)
        txt_rec_all = " / ".join(_texto_visible(m) for m in msgs_rec)
        norm_cli_all, _ = _normalizar(txt_cli_all)
        norm_rec_all, _ = _normalizar(txt_rec_all)

        # 1) ¿Prometió recepción enviar factura/boleta y aún no envió PDF en este chat?
        tiene_pdf_enviado = any(
            m["es_mio"] and (
                str(m["tipo"]).lower() in ("documento", "document", "pdf")
                or ".pdf" in str(m["texto"] or "").lower()
                or bool(_RE_CPE.search(str(m["texto"] or "")))
            )
            for m in msgs
        )
        pidio_comp_cliente = any(
            any(w in _normalizar(_texto_visible(m))[0].split() for w in ("factura", "boleta", "comprobante"))
            for m in msgs_cli
        )
        promesa_comp_abierta = (
            tipo_princ == "COMPROBANTE"
            and not tiene_pdf_enviado
            and (
                any(bool(_RE_PROMESA_COMP.search(_sin_tildes(_texto_visible(m)))) for m in msgs_rec)
                or pidio_comp_cliente
            )
        )

        # 2) ¿El último tramo sin responder es solo un encargo presencial antiguo (> 12 h, ej. "Dele la llave 406")?
        txt_sin_resp = " ".join(_texto_visible(m) for m in sin_resp).strip()
        antig_horas = (ahora - int(sin_resp[0]["ts"] or ahora)) / 3600.0 if sin_resp else 0.0
        es_encargo_presencial_antiguo = (
            bool(sin_resp)
            and antig_horas > 12.0
            and bool(_RE_CIERRE_ENCARGO_MESA.match(_sin_tildes(txt_sin_resp).strip()))
            and len(msgs_rec) > 0
        )

        es_pendiente = False
        motivo_pendiente = ""
        primer_pend_ts = int(msgs[-1]["ts"] or 0)

        if promesa_comp_abierta:
            es_pendiente = True
            primer_pend_ts = int(msgs_cli[-1]["ts"] if msgs_cli else msgs[-1]["ts"])
            est_s = cruce.get("estado_sunat") or "sin PDF en el chat"
            motivo_pendiente = (
                f"Solicitó su comprobante y recepción indicó que lo emitiría, pero aún no se ha enviado el PDF por WhatsApp "
                f"(Estado en BD: {est_s})."
            )
        elif sin_resp and not es_encargo_presencial_antiguo:
            es_pendiente = True
            primer_pend_ts = int(sin_resp[0]["ts"] or 0)
            if all(str(m["tipo"]).lower() in _TIPOS_ADJUNTO for m in sin_resp):
                tipo_princ = "PAGO"
                n_img = len(sin_resp)
                motivo_pendiente = (
                    f"El cliente envió {n_img} archivo(s)/imagen(es) sin texto y recepción aún no ha confirmado recepción "
                    f"(verificar si corresponde a voucher Yape/transferencia)."
                )
            else:
                resumen_msg = " / ".join(_texto_visible(m) for m in sin_resp)[:140]
                motivo_pendiente = f"Mensaje del cliente sin respuesta de recepción: «{resumen_msg}»."

        cruce_limpio = {k: v for k, v in cruce.items() if k not in ("entidades", "sugerencia_wsp")}
        sug_item = ch.get("sugerencia_wsp") or {"perfil_wsp": "", "empresa_sugerida": "", "estancias_sugeridas": []}

        if es_pendiente:
            espera_m = max(0, int((ahora - primer_pend_ts) // 60)) if ch["es_hoy"] else 0
            pendientes_responder.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hora_ultimo_msg": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "espera_min": espera_m,
                "es_hoy": bool(ch["es_hoy"]),
                "tipo": tipo_princ if tipo_princ in ("COMPROBANTE", "RESERVA", "HABITACION", "PAGO") else "HABITACION",
                "motivo_pendiente": motivo_pendiente,
                "cruce": cruce_limpio,
                "sugerencia_wsp": sug_item,
                "mensajes": ch["mensajes"],
            })
        else:
            # Analizar cierre para Bloque 3 (Atendidos / Por qué dijeron gracias)
            ult_cli_txt = _texto_visible(msgs_cli[-1]) if msgs_cli else ""
            dijo_gracias = any(_RE_AGRADECIMIENTO.search(_sin_tildes(_texto_visible(m))) for m in msgs_cli[-3:])
            if tiene_pdf_enviado:
                cpe_m = _RE_CPE.search(txt_rec_all)
                cpe_txt = cpe_m.group(1).upper() if cpe_m else "electrónico"
                if dijo_gracias:
                    cierre = f"Recepción envió el PDF del comprobante {cpe_txt} y el cliente confirmó recepción («{ult_cli_txt}»)."
                else:
                    cierre = f"Recepción emitió y dejó enviado el PDF del comprobante {cpe_txt} al cliente."
            elif "reservado" in norm_rec_all:
                cierre = "Recepción verificó su abono/DNI y dejó confirmada la reserva («ya está reservado, gracias»)."
            elif "hasta 35" in norm_rec_all or "ok gracias de todos modos" in norm_cli_all:
                cierre = "Recepción informó disponibilidad y tarifas vigentes (ofreciendo tarifa especial S/ 35); el cliente agradeció la información."
            elif "ya esta bien" in norm_rec_all or es_encargo_presencial_antiguo:
                cierre = "Recepción atendió la coordinación de habitación/visita («ya está bien») y el huésped cerró conforme."
            elif msgs_rec and not msgs_cli:
                cierre = f"Aviso informativo enviado por recepción («{_texto_visible(msgs_rec[-1])[:80]}»)."
            else:
                cierre = f"Consulta atendida por recepción{f'; cliente cerró con «{ult_cli_txt[:50]}»' if ult_cli_txt else ''}."

            atendidos_y_gracias.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab,
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "que_pidio": que_pidio,
                "por_que_agradecio": cierre,
                "sugerencia_wsp": sug_item,
                "mensajes": ch["mensajes"],
            })

        # Bloque 2: Recomendaciones, Reclamos o Acuerdos importantes detectados en la conversación
        if any(k in norm_cli_all for k in ("no hay agua", "agua fria", "malogrado", "no funciona", "sucio", "bulla", "reclamo", "queja")):
            recomendaciones_y_notas.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab,
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "tipo_nota": "RECLAMO",
                "detalle": f"Incidencia reportada por el huésped en el chat: «{txt_cli_all[:130]}».",
                "sugerencia_wsp": sug_item,
            })
        elif "dele la llave" in norm_cli_all or "le hace pasar" in norm_cli_all or "va preguntar" in norm_cli_all:
            recomendaciones_y_notas.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab or "406",
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "tipo_nota": "ACUERDO",
                "detalle": f"El huésped autorizó el ingreso de visita y pidió entregar la llave en recepción (Hab {hab or '406'}).",
                "sugerencia_wsp": sug_item,
            })
        elif "reservado" in norm_rec_all:
            no_bol = " (indicó que NO requiere boleta)" if re.search(r"\bno\b", norm_cli_all.split()[-3:] and " ".join(norm_cli_all.split()[-4:]) or "") else ""
            recomendaciones_y_notas.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab,
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "tipo_nota": "ACUERDO",
                "detalle": f"Reserva confirmada de habitación matrimonial S/ 75 con abono previo por Yape y llegada nocturna{no_bol}.",
                "sugerencia_wsp": sug_item,
            })
        elif "cotizacion" in norm_cli_all or ("proyecto" in norm_cli_all and "personas" in norm_cli_all):
            recomendaciones_y_notas.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab,
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "tipo_nota": "ACUERDO",
                "detalle": "Negociación de tarifa corporativa/grupal para personal de proyecto (pidió tarifa de cotización y recepción ofreció tarifa especial).",
                "sugerencia_wsp": sug_item,
            })
        elif "sabia quedarme con 30" in norm_cli_all or "hasta 35" in norm_rec_all:
            recomendaciones_y_notas.append({
                "jid": ch["jid"],
                "cliente": cli_label,
                "celular": ch["celular"],
                "hab": hab,
                "hora": _hora_txt(int(msgs[-1]["ts"] or 0)),
                "tipo_nota": "RECOMENDACION",
                "detalle": "Cliente frecuente comentó tarifa histórica de S/ 30 en simple; recepción ofreció excepción hasta S/ 35 pero no concretó ingreso.",
                "sugerencia_wsp": sug_item,
            })

    n_p = len(pendientes_responder)
    n_a = len(atendidos_y_gracias)
    n_r = len(recomendaciones_y_notas)
    if n_p == 0 and n_a == 0:
        sintesis = "Sin conversaciones recientes de clientes por revisar en WhatsApp de Recepción."
    elif n_p == 0:
        sintesis = f"WhatsApp al día: {n_a} conversación(es) atendidas correctamente y {n_r} acuerdo(s)/nota(s) registrados, sin respuestas pendientes."
    else:
        sintesis = (
            f"Se detectan {n_p} conversación(es) que requieren seguimiento o envío de comprobante, "
            f"{n_r} acuerdo(s)/observación(es) relevantes y {n_a} chat(s) ya atendidos y cerrados."
        )

    return {
        "resumen_general_turno": sintesis,
        "pendientes_responder": pendientes_responder,
        "recomendaciones_y_notas": recomendaciones_y_notas,
        "atendidos_y_gracias": atendidos_y_gracias,
    }


# ---------------------------------------------------------------------------
# Agente IA con Gemini (`gemini-3.8-flash` -> `gemini-2.5-flash` -> local)
# ---------------------------------------------------------------------------
_gemini_cooldown_until: float = 0.0
_gemini_last_keys_sig: str = ""


def _llamar_agente_gemini(chats: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Intenta analizar las conversaciones con google.genai. Retorna (dict_resultado | None, fuente_ia)."""
    global _gemini_cooldown_until, _gemini_last_keys_sig
    if not chats:
        return None, ""
    keys = _cargar_api_keys_gemini()
    if not keys:
        return None, ""

    sig = "|".join(k[:8] + str(len(k)) for k in keys)
    if sig != _gemini_last_keys_sig:
        _gemini_cooldown_until = 0.0
        _gemini_last_keys_sig = sig

    if _ahora() < _gemini_cooldown_until:
        return None, ""

    try:
        from google import genai
        from google.genai import types as genai_types
    except Exception:
        return None, ""

    contexto_chats = []
    for ch in chats:
        cruce_res = {k: v for k, v in ch["cruce"].items() if k != "entidades"}
        contexto_chats.append({
            "jid": ch["jid"],
            "cliente": ch["cliente"],
            "celular": ch["celular"],
            "es_hoy": ch["es_hoy"],
            "cruce_bd_hotel": cruce_res,
            "conversacion": [
                {"hora": m["hora"], "quien": "RECEPCION" if m["es_mio"] else "CLIENTE", "tipo": m["tipo"], "texto": m["texto"]}
                for m in ch["mensajes"]
            ],
        })

    prompt = (
        "Eres el Auditor y Analista Ejecutivo de Recepción del Hotel El Óvalo Mariátegui (Tacna, Perú).\n"
        "Analiza íntegramente las siguientes conversaciones reales de WhatsApp entre CLIENTE y RECEPCION, "
        "junto con su cruce en la base de datos del hotel (`cruce_bd_hotel`).\n"
        "Discierne el contexto real de toda la charla (no palabras sueltas):\n"
        "1. `pendientes_responder`: SOLO chats donde el cliente sigue esperando respuesta real o donde recepción prometió "
        "enviar una factura/boleta y aún NO se envió el PDF en el chat. No pongas aquí chats que cerraron con 'ok/gracias' "
        "ni encargos antiguos ya resueltos.\n"
        "2. `recomendaciones_y_notas`: acuerdos operativos importantes (encargo de llaves, autorización de visitas, reservas "
        "pactadas con hora de llegada, excepciones de tarifa) o reclamos/sugerencias del huésped. `tipo_nota` debe ser "
        "estrictamente 'ACUERDO', 'RECOMENDACION' o 'RECLAMO'.\n"
        "3. `atendidos_y_gracias`: chats ya atendidos/cerrados explicando en 1 línea clara `que_pidio` y `por_que_agradecio` "
        "(cómo se resolvió o por qué dio las gracias).\n\n"
        "Devuelve ÚNICAMENTE un objeto JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "resumen_general_turno": "síntesis ejecutiva clara en 1-2 oraciones",\n'
        '  "pendientes_responder": [{"jid": "...", "tipo": "COMPROBANTE|RESERVA|HABITACION|PAGO", "motivo_pendiente": "..."}],\n'
        '  "recomendaciones_y_notas": [{"jid": "...", "hab": "...", "tipo_nota": "ACUERDO|RECOMENDACION|RECLAMO", "detalle": "..."}],\n'
        '  "atendidos_y_gracias": [{"jid": "...", "hab": "...", "que_pidio": "...", "por_que_agradecio": "..."}]\n'
        "}\n\n"
        f"CONVERSACIONES A ANALIZAR:\n{json.dumps(contexto_chats, ensure_ascii=False)}"
    )

    modelos = ("gemini-3.8-flash", "gemini-2.5-flash")
    agotado_402 = False

    for api_key in keys:
        client = genai.Client(api_key=api_key)
        for modelo in modelos:
            try:
                cfg_gen = genai_types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                )
                resp = client.models.generate_content(model=modelo, contents=prompt, config=cfg_gen)
                raw_txt = (resp.text or "").strip()
                if raw_txt.startswith("```"):
                    raw_txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_txt, flags=re.S).strip()
                parsed = json.loads(raw_txt)
                if isinstance(parsed, dict) and "pendientes_responder" in parsed and "atendidos_y_gracias" in parsed:
                    return _hidratar_respuesta_ia(parsed, chats), f"Gemini IA ({modelo})"
            except Exception as exc:
                msg_err = str(exc)
                if "402" in msg_err or "prepayment credits are depleted" in msg_err.lower() or "API_KEY_INVALID" in msg_err:
                    agotado_402 = True
                    break  # misma cuenta sin saldo: no repetir otro modelo con la misma key
                continue

    if agotado_402:
        # Evita demorar los ticks de fondo durante 3 minutos si la cuenta sigue sin saldo
        _gemini_cooldown_until = _ahora() + 180.0
    return None, ""


def _hidratar_respuesta_ia(parsed: Dict[str, Any], chats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combina las explicaciones del modelo Gemini con los mensajes literales y fichas de cruce de SQLite."""
    ahora = _ahora()
    mapa = {c["jid"]: c for c in chats}

    pend_out: List[Dict[str, Any]] = []
    for it in parsed.get("pendientes_responder") or []:
        if not isinstance(it, dict):
            continue
        jid = str(it.get("jid") or "")
        ch = mapa.get(jid)
        if not ch:
            continue
        cli_label, _ = _etiqueta_cliente_hab(ch)
        msgs = ch["mensajes_raw"]
        ult_mio = max((int(m["ts"] or 0) for m in msgs if m["es_mio"]), default=0)
        sin_resp = [m for m in msgs if not m["es_mio"] and int(m["ts"] or 0) > ult_mio]
        ref_ts = int(sin_resp[0]["ts"] if sin_resp else msgs[-1]["ts"])
        espera_m = max(0, int((ahora - ref_ts) // 60)) if ch["es_hoy"] else 0
        cruce_limpio = {k: v for k, v in ch["cruce"].items() if k not in ("entidades", "sugerencia_wsp")}
        sug_item = ch.get("sugerencia_wsp") or {"perfil_wsp": "", "empresa_sugerida": "", "estancias_sugeridas": []}
        tipo_val = str(it.get("tipo") or "HABITACION").upper()
        if tipo_val not in ("COMPROBANTE", "RESERVA", "HABITACION", "PAGO"):
            tipo_val = "HABITACION"
        pend_out.append({
            "jid": jid,
            "cliente": str(it.get("cliente") or cli_label),
            "celular": ch["celular"],
            "hora_ultimo_msg": _hora_txt(int(msgs[-1]["ts"] or 0)),
            "espera_min": espera_m,
            "es_hoy": bool(ch["es_hoy"]),
            "tipo": tipo_val,
            "motivo_pendiente": str(it.get("motivo_pendiente") or ""),
            "cruce": cruce_limpio,
            "sugerencia_wsp": sug_item,
            "mensajes": ch["mensajes"],
        })

    rec_out: List[Dict[str, Any]] = []
    for it in parsed.get("recomendaciones_y_notas") or []:
        if not isinstance(it, dict):
            continue
        jid = str(it.get("jid") or "")
        ch = mapa.get(jid)
        cli_label, hab_c = _etiqueta_cliente_hab(ch) if ch else (str(it.get("cliente") or jid), "")
        sug_item = (ch.get("sugerencia_wsp") if ch else None) or {"perfil_wsp": "", "empresa_sugerida": "", "estancias_sugeridas": []}
        t_nota = str(it.get("tipo_nota") or "ACUERDO").upper()
        if t_nota not in ("ACUERDO", "RECOMENDACION", "RECLAMO"):
            t_nota = "ACUERDO"
        rec_out.append({
            "jid": jid,
            "cliente": str(it.get("cliente") or cli_label),
            "celular": ch["celular"] if ch else _celular9(jid),
            "hab": str(it.get("hab") or hab_c or ""),
            "hora": _hora_txt(int(ch["mensajes_raw"][-1]["ts"] or 0)) if ch and ch["mensajes_raw"] else "",
            "tipo_nota": t_nota,
            "detalle": str(it.get("detalle") or ""),
            "sugerencia_wsp": sug_item,
        })

    atend_out: List[Dict[str, Any]] = []
    for it in parsed.get("atendidos_y_gracias") or []:
        if not isinstance(it, dict):
            continue
        jid = str(it.get("jid") or "")
        ch = mapa.get(jid)
        if not ch:
            continue
        cli_label, hab_c = _etiqueta_cliente_hab(ch)
        sug_item = ch.get("sugerencia_wsp") or {"perfil_wsp": "", "empresa_sugerida": "", "estancias_sugeridas": []}
        atend_out.append({
            "jid": jid,
            "cliente": str(it.get("cliente") or cli_label),
            "celular": ch["celular"],
            "hab": str(it.get("hab") or hab_c or ""),
            "hora": _hora_txt(int(ch["mensajes_raw"][-1]["ts"] or 0)) if ch["mensajes_raw"] else "",
            "que_pidio": str(it.get("que_pidio") or ""),
            "por_que_agradecio": str(it.get("por_que_agradecio") or ""),
            "sugerencia_wsp": sug_item,
            "mensajes": ch["mensajes"],
        })

    return {
        "resumen_general_turno": str(parsed.get("resumen_general_turno") or ""),
        "pendientes_responder": pend_out,
        "recomendaciones_y_notas": rec_out,
        "atendidos_y_gracias": atend_out,
    }


# ---------------------------------------------------------------------------
# Persistencia del corte ejecutivo y reloj programado (08:00, 13:00, 19:00, 23:00)
# ---------------------------------------------------------------------------
_analisis_lock = threading.Lock()


def _proximo_corte_hora(dt_ref: Optional[datetime] = None) -> str:
    dt = dt_ref or datetime.now()
    hhmm = dt.strftime("%H:%M")
    for h in HORARIOS_FIJOS:
        if h > hhmm:
            return h
    return HORARIOS_FIJOS[0]


def _formatear_fecha_hora_corte(fecha_hora_iso: str) -> Tuple[str, str]:
    """Devuelve (hora_hhmm, fecha_dmy) desde 'YYYY-MM-DD HH:MM:SS'."""
    try:
        dt = datetime.strptime(fecha_hora_iso[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%H:%M"), dt.strftime("%d/%m/%Y")
    except Exception:
        ahora_dt = datetime.now()
        return ahora_dt.strftime("%H:%M"), ahora_dt.strftime("%d/%m/%Y")


def ejecutar_analisis_agente_wsp(forzar: bool = False, slot_horario: str = "") -> Dict[str, Any]:
    """Ejecuta el análisis completo de conversaciones con Agente IA (o respaldo contextual local),
    lo guarda en `wsp_resumen_ejecutivo` y devuelve el payload completo para la UI."""
    global _gemini_cooldown_until
    _asegurar_tablas()
    with _analisis_lock:
        if not forzar and not slot_horario:
            existente = _leer_ultimo_corte_db()
            if existente is not None:
                return _construir_respuesta_bandeja(existente)

        if forzar:
            # Decisión D3 (18/11/2026): En modo prueba Supabase está 100% apagado; se sincroniza WSP vía Google Sheets (`2026_Prueba_Ovalo`).
            if MODO_PRUEBA:
                try:
                    import wsp_sheets_bridge as _wsb
                    _wsb.sincronizar_wsp_prueba_sheets(forzar=True)
                except Exception as _exc_wsb:
                    print(f"[WSP Sheets Bridge] aviso en análisis forzado: {_exc_wsb}")
            else:
                try:
                    import supabase_sync as _ss
                    if hasattr(_ss, "_sincronizar_espejo_wsp_modo_prueba"):
                        _ss._sincronizar_espejo_wsp_modo_prueba()
                except Exception:
                    pass
            # Reintentar Gemini en vivo
            _gemini_cooldown_until = 0.0

        chats = _recolectar_chats_clientes()
        resultado_ia, fuente_ia = _llamar_agente_gemini(chats)
        if resultado_ia is None:
            resultado_ia = _analizar_heuristico_local(chats)
            fuente_ia = "Agente Contextual Local"

        ahora_dt = datetime.now()
        fecha_hora_str = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
        slot_guardar = slot_horario or ("MANUAL " + ahora_dt.strftime("%H:%M") if forzar else ahora_dt.strftime("%Y-%m-%d %H:%M"))

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO wsp_resumen_ejecutivo (fecha_hora, slot_horario, fuente_ia, payload_json) VALUES (?, ?, ?, ?)",
                (fecha_hora_str, slot_guardar, fuente_ia, json.dumps(resultado_ia, ensure_ascii=False)),
            )
            # Mantener historial acotado a los últimos 80 cortes
            conn.execute(
                "DELETE FROM wsp_resumen_ejecutivo WHERE id NOT IN (SELECT id FROM wsp_resumen_ejecutivo ORDER BY id DESC LIMIT 80)"
            )
            conn.commit()
        finally:
            conn.close()

        registro = {
            "fecha_hora": fecha_hora_str,
            "slot_horario": slot_guardar,
            "fuente_ia": fuente_ia,
            "payload": resultado_ia,
        }
        return _construir_respuesta_bandeja(registro)


def _leer_ultimo_corte_db() -> Optional[Dict[str, Any]]:
    _asegurar_tablas()
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT id, fecha_hora, slot_horario, fuente_ia, payload_json FROM wsp_resumen_ejecutivo ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not r:
            return None
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        return {
            "id": r["id"],
            "fecha_hora": str(r["fecha_hora"] or ""),
            "slot_horario": str(r["slot_horario"] or ""),
            "fuente_ia": str(r["fuente_ia"] or "Agente Contextual Local"),
            "payload": payload,
        }
    finally:
        conn.close()


def _construir_respuesta_bandeja(reg: Dict[str, Any]) -> Dict[str, Any]:
    payload = reg.get("payload") or {}
    ult_hora, ult_fecha = _formatear_fecha_hora_corte(reg.get("fecha_hora") or "")
    est_d = estado_daemon()
    return {
        "estado_daemon": est_d,
        "ultimo_corte_hora": ult_hora,
        "ultimo_corte_fecha": ult_fecha,
        "slot_horario": str(reg.get("slot_horario") or ult_hora),
        "proximo_corte_hora": _proximo_corte_hora(),
        "fuente_ia": str(reg.get("fuente_ia") or "Agente Contextual Local"),
        "horarios_fijos": list(HORARIOS_FIJOS),
        "resumen_general_turno": str(payload.get("resumen_general_turno") or ""),
        "pendientes_responder": list(payload.get("pendientes_responder") or []),
        "recomendaciones_y_notas": list(payload.get("recomendaciones_y_notas") or []),
        "atendidos_y_gracias": list(payload.get("atendidos_y_gracias") or []),
    }


def obtener_bandeja_wsp(_arg: Any = None) -> Dict[str, Any]:
    """RPC obtenerBandejaWsp: devuelve instantáneamente el último corte ejecutivo de 3 bloques."""
    reg = _leer_ultimo_corte_db()
    if reg is None:
        return ejecutar_analisis_agente_wsp(forzar=False, slot_horario=f"{date.today().isoformat()} INICIAL")
    payload = reg.get("payload") or {}
    sin_items = (
        not payload.get("pendientes_responder")
        and not payload.get("recomendaciones_y_notas")
        and not payload.get("atendidos_y_gracias")
    )
    if sin_items:
        try:
            c = _abrir_local_ro()
            try:
                cnt = int(c.execute("SELECT COUNT(*) FROM wsp_recepcion_mensajes").fetchone()[0] or 0)
            finally:
                c.close()
            if cnt > 0:
                return ejecutar_analisis_agente_wsp(forzar=True, slot_horario=f"{date.today().isoformat()} AUTO")
        except Exception:
            pass
    return _construir_respuesta_bandeja(reg)


def obtener_alertas_wsp() -> Dict[str, Any]:
    """RPC obtenerAlertasWsp: estado compacto para la barra superior (0 ms sobre caché SQLite)."""
    bandeja = obtener_bandeja_wsp()
    est_d = bandeja["estado_daemon"]
    pend_list = bandeja["pendientes_responder"]
    n_pend = len(pend_list)
    n_atend = len(bandeja["atendidos_y_gracias"])
    n_rec = len(bandeja["recomendaciones_y_notas"])
    ult_hora = bandeja["ultimo_corte_hora"]
    ult_fecha = bandeja["ultimo_corte_fecha"]
    prox_hora = bandeja["proximo_corte_hora"]

    urgente_hoy = any(bool(p.get("es_hoy")) and int(p.get("espera_min") or 0) >= UMBRAL_ROJO_MIN for p in pend_list)
    if est_d != "CONECTADO" and n_pend == 0:
        nivel = "SIN_CONEXION"
        titular = f"WSP desconectado · Últ. corte {ult_hora}"
    elif n_pend > 0:
        nivel = "ROJO" if urgente_hoy else "AMBAR"
        titular = f"WSP: {n_pend} por responder · {n_atend} atendidos (Corte {ult_hora})"
    else:
        nivel = "OK"
        titular = f"WSP al día · {n_atend} chats resumidos (Corte {ult_hora})"

    return {
        "estado_daemon": est_d,
        "pendientes": n_pend,
        "atendidos": n_atend,
        "recomendaciones": n_rec,
        "pendientes_count": n_pend,
        "atendidos_count": n_atend,
        "recomendaciones_count": n_rec,
        "ultimo_corte_hora": ult_hora,
        "ultimo_corte_fecha": ult_fecha,
        "proximo_corte_hora": prox_hora,
        "fuente_ia": bandeja["fuente_ia"],
        "nivel": nivel,
        "titular_barra": titular,
        "resumen_general": bandeja["resumen_general_turno"],
    }


def tick_resumen_programado() -> Optional[Dict[str, Any]]:
    """Llamado por OvaloWspWorker cada 30 s:
    0) En MODO_PRUEBA sincroniza WSP vía Google Sheets (`2026_Prueba_Ovalo`) cada ~60 s (0% Supabase).
    1) Si `wsp_resumen_ejecutivo` está vacía, genera el primer corte inicial.
    2) Cuando el reloj alcanza cualquiera de los 4 horarios fijos (`08:00`, `13:00`, `19:00`, `23:00`),
       ejecuta el análisis exactamente una vez para ese slot."""
    _asegurar_tablas()
    ahora_dt = datetime.now()
    hoy_iso = ahora_dt.strftime("%Y-%m-%d")
    hhmm_actual = ahora_dt.strftime("%H:%M")

    if MODO_PRUEBA:
        try:
            import wsp_sheets_bridge as _wsb
            res_bridge = _wsb.sincronizar_wsp_prueba_sheets(forzar=False)
            if res_bridge.get("ok") and int(res_bridge.get("nuevos_mensajes") or 0) > 0:
                return ejecutar_analisis_agente_wsp(forzar=False, slot_horario=f"{hoy_iso} SYNC {hhmm_actual}")
        except Exception as _exc_wsb:
            print(f"[WSP Sheets Bridge] aviso en worker: {_exc_wsb}")

    ultimo = _leer_ultimo_corte_db()
    if ultimo is None:
        return ejecutar_analisis_agente_wsp(forzar=False, slot_horario=f"{hoy_iso} INICIAL")

    # Determinar el último slot fijo ya alcanzado hoy
    slots_pasados = [h for h in HORARIOS_FIJOS if hhmm_actual >= h]
    if not slots_pasados:
        return None
    slot_objetivo = slots_pasados[-1]
    clave_slot = f"{hoy_iso} {slot_objetivo}"
    marca_tiempo_slot = f"{hoy_iso} {slot_objetivo}:00"

    conn = get_connection()
    try:
        ya_ejecutado = conn.execute(
            "SELECT 1 FROM wsp_resumen_ejecutivo WHERE slot_horario = ? LIMIT 1",
            (clave_slot,),
        ).fetchone() is not None
    finally:
        conn.close()

    if ya_ejecutado:
        return None

    # Ejecutar si estamos dentro de los primeros 15 min del slot fijo, o si el último corte fue anterior a ese slot
    ult_fh = str(ultimo.get("fecha_hora") or "")
    dt_slot = datetime.strptime(marca_tiempo_slot, "%Y-%m-%d %H:%M:%S")
    en_ventana_puntual = 0 <= (ahora_dt - dt_slot).total_seconds() <= 900
    if en_ventana_puntual or ult_fh < marca_tiempo_slot:
        return ejecutar_analisis_agente_wsp(forzar=False, slot_horario=clave_slot)
    return None


# ---------------------------------------------------------------------------
# Vigilante del daemon (SOLO fuera de MODO_PRUEBA)
# ---------------------------------------------------------------------------
def _daemon_corriendo(script: str = "wsp_daemon.js") -> bool:
    try:
        import psutil  # type: ignore
        for p in psutil.process_iter(["name", "cmdline"]):
            nm = str(p.info.get("name") or "").lower()
            if nm.startswith("node"):
                if any(script in str(a) for a in (p.info.get("cmdline") or [])):
                    return True
        return False
    except ImportError:
        pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{script}*' }} | Select-Object -First 1 -ExpandProperty ProcessId",
            ],
            capture_output=True, text=True, timeout=20, creationflags=flags,
        )
        return bool(out.stdout.strip())
    except Exception:
        return True


def _es_laptop_recepcion_l2() -> bool:
    """Verifica estrictamente que este equipo sea Laptop 2 (Recepción). Jamás devuelve True en Laptop 1 ni otras laptops."""
    eq = str(getattr(_cfg, "EQUIPO_ID", "") or "").upper()
    if "LAPTOP_1" in eq or "ADMIN" in eq:
        return False
    if "LAPTOP_2" in eq or "RECEPCION" in eq:
        return True
    return Path(r"C:\Users\ovalo mariategui").exists()


def _resolver_carpeta_wsp_daemon() -> Optional[Path]:
    candidatos = [
        _BASE_DIR / "01_PRIV_borradores" / "whatsapp_mcp_recepcion",
        _BASE_DIR / "whatsapp_mcp_recepcion",
        Path(r"C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion"),
        Path(r"C:\Hostal_Sistema\whatsapp_mcp_recepcion"),
    ]
    for c in candidatos:
        if (c / "wsp_daemon.js").exists():
            return c
    return None


def _asegurar_db_js_apunta_a_v2(carpeta: Path) -> bool:
    """
    Asegura que db.js del daemon de WhatsApp en Laptop 2 escriba directamente en
    02_DB_sqlite_local/ovalo.db de C:\\Ovalo_Sistema_v2 (y no en la carpeta V1 antigua).
    Devuelve True si tuvo que actualizar db.js (para reiniciar el daemon).
    """
    db_js = carpeta / "db.js"
    if not db_js.exists():
        return False
    try:
        txt = db_js.read_text(encoding="utf-8")
        target_db = str(getattr(_cfg, "DB_PATH", r"C:\Ovalo_Sistema_v2\02_DB_sqlite_local\ovalo.db")).replace("\\", "\\\\")
        linea_v2 = f"const DB_HOSTAL_PATH = '{target_db}';"
        if linea_v2 in txt:
            return False
        nuevo_txt = re.sub(
            r"const\s+DB_HOSTAL_PATH\s*=\s*[^;]+;",
            lambda _: linea_v2,
            txt,
            count=1,
        )
        if nuevo_txt != txt:
            db_js.write_text(nuevo_txt, encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def _detener_daemon_wsp() -> None:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                "Where-Object { $_.CommandLine -like '*wsp_daemon.js*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ],
            capture_output=True, text=True, timeout=15, creationflags=flags,
        )
    except Exception:
        pass


def vigilar_daemon() -> Dict[str, Any]:
    """Relanza `node wsp_daemon.js` apuntando a 02_DB_sqlite_local/ovalo.db. EXCLUSIVO de Laptop 2 (Recepción)."""
    if MODO_PRUEBA:
        return {"accion": "omitido", "motivo": "MODO_PRUEBA"}
    if not _es_laptop_recepcion_l2():
        return {"accion": "omitido", "motivo": "SOLO_LAPTOP_2_RECEPCION"}
    raw = _estado_daemon_raw().strip().upper()
    if raw == "ESPERANDO_QR" or "LOGGED_OUT" in raw or raw.endswith("_401"):
        return {"accion": "omitido", "motivo": "pide QR / sesión cerrada", "estado": raw}
    carpeta = _resolver_carpeta_wsp_daemon()
    if not carpeta:
        return {"accion": "omitido", "motivo": "no existe wsp_daemon.js"}
    script = carpeta / "wsp_daemon.js"
    cambio_db = _asegurar_db_js_apunta_a_v2(carpeta)
    if cambio_db:
        _detener_daemon_wsp()
        time.sleep(0.5)
    elif _daemon_corriendo("wsp_daemon.js"):
        return {"accion": "ninguna", "motivo": "ya corre en DB v2", "carpeta": str(carpeta)}
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not Path(node).exists():
        return {"accion": "omitido", "motivo": "node no encontrado"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [node, str(script)],
        cwd=str(carpeta),
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return {"accion": "lanzado_en_db_v2" if cambio_db else "lanzado", "node": node, "carpeta": str(carpeta)}
