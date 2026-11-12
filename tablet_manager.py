# -*- coding: utf-8 -*-
"""
Gestor de Vinculación y Sincronización en Vivo con Tablet Samsung Galaxy Tab S8+ (SM-X800)
Provee:
1. Detección automática de IP Wi-Fi/LAN y estado de conexión en tiempo real.
2. Catálogo público sanitizado (0 datos sensibles de caja o huéspedes hacia el cliente).
3. Comunicación bidireccional instantánea (Tablet <-> Laptop Recepción):
   - Selección de habitación desde la Tablet hacia el Check-In de la Laptop.
   - Auto-llenado elegante de DNI / RUC / Celular validado con Padrón/RENIEC/SUNAT.
   - Control remoto de pantalla desde Recepción (Mostrar/Ocultar precios, enviar Cobro QR Yape, pedir Firma S Pen).
   - Módulo 1-Clic de Limpieza por pisos.
   - Firma digital con S Pen para conformidad de Empresas y Buzón inborrable de Quejas/Mejoras.
"""

from datetime import datetime
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from config import APP_PORT, DATA_DIR, NOMBRE_HOTEL, STATIC_DIR
from database import db_transaction, get_connection
from domain_logic import (
    hoy_dmy,
    obtener_datos_panel_local,
    obtener_datos_vitrina_local,
)
from sunat_wsp_service import (
    consultar_dni_local_o_reniec,
    consultar_ruc_local_o_sunat,
)

FOTOS_HABS_DIR = DATA_DIR / "fotos_habitaciones"
FOTOS_HABS_DIR.mkdir(parents=True, exist_ok=True)

_tablet_lock = threading.Lock()

# Estado en memoria para respuesta en < 1 ms entre Laptop y Tablet S8+
_TABLET_LIVE_STATE: Dict[str, Any] = {
    "ultimo_latido_ts": 0.0,
    "tablet_ip": "",
    "tablet_user_agent": "",
    "mostrar_precios": True,
    "modo_pantalla": "catalogo",  # catalogo | checkin_express | cobro_qr | vitrina | firma_empresa | limpieza | sugerencias
    "orden_seq": 1,
    "hab_destacada": "",
    "cobro_qr": {
        "activo": False,
        "monto": 0.0,
        "hab": "",
        "concepto": "Hospedaje",
        "titular_yape": NOMBRE_HOTEL,
        "numero_yape": "",
    },
    "firma_solicitud": {
        "activa": False,
        "hab": "",
        "id_estancia": "",
        "huesped": "",
        "dni": "",
        "empresa": "",
        "check_in": "",
        "check_out": "",
        "tarifa": "",
        "notas_reglas": "Hora de Check-Out: 12:00 m. Prohibido fumar dentro de la habitación. Todo consumo adicional o daño será registrado a cuenta.",
    },
    # Bandeja de eventos enviados por el cliente desde la Tablet hacia la Laptop
    "evento_cliente": {
        "seq": 0,
        "timestamp": "",
        "tipo": "",  # HAB_ELEGIDA | DATOS_CHECKIN | PEDIDO_TIENDA | FIRMA_COMPLETADA | SUGERENCIA_NUEVA
        "leido_en_laptop": True,
        "hab": "",
        "tipo_hab": "",
        "precio_ref": "",
        "personas": 2,
        "dni": "",
        "nombre": "",
        "celular": "",
        "ruc": "",
        "razon_social": "",
        "items_tienda": [],
        "total_tienda": 0.0,
        "resumen": "",
    },
}


def init_tablet_tables() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tablet_limpieza_dia (
                hab             TEXT NOT NULL,
                fecha_dmy       TEXT NOT NULL,
                estado_limpieza TEXT NOT NULL DEFAULT 'PENDIENTE',
                nota            TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (hab, fecha_dmy)
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

            CREATE TABLE IF NOT EXISTS tablet_buzon_mejoras (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora      TEXT NOT NULL,
                calificacion    INTEGER NOT NULL DEFAULT 5,
                categoria       TEXT NOT NULL DEFAULT 'General',
                hab             TEXT NOT NULL DEFAULT '',
                comentario      TEXT NOT NULL DEFAULT '',
                huesped_opcional TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def obtener_ips_locales() -> List[Dict[str, str]]:
    """Detecta todas las direcciones IPv4 de red local (priorizando Wi-Fi) para el Código QR."""
    candidatas: List[Dict[str, str]] = []
    vistas = set()

    # 1. Intentar detectar con PowerShell qué interfaz es Wi-Fi vs Ethernet
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\\.|169\\.254)' } | Select-Object InterfaceAlias,IPAddress | ConvertTo-Json -Compress",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, creationflags=0x08000000)
        if proc.returncode == 0 and proc.stdout.strip():
            import json
            raw = json.loads(proc.stdout.strip())
            if isinstance(raw, dict):
                raw = [raw]
            for item in raw or []:
                ip = str(item.get("IPAddress") or "").strip()
                alias = str(item.get("InterfaceAlias") or "Red Local").strip()
                if ip and ip not in vistas:
                    vistas.add(ip)
                    es_wifi = "wi-fi" in alias.lower() or "wifi" in alias.lower() or "wlan" in alias.lower()
                    candidatas.append({
                        "alias": alias,
                        "ip": ip,
                        "url": f"http://{ip}:{APP_PORT}/tablet",
                        "prioridad": 0 if es_wifi else 1,
                    })
    except Exception:
        pass

    # 2. Fallback por socket UDP sin enviar tráfico
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip_sock = s.getsockname()[0]
        s.close()
        if ip_sock and not ip_sock.startswith("127.") and ip_sock not in vistas:
            vistas.add(ip_sock)
            candidatas.append({
                "alias": "Red Principal",
                "ip": ip_sock,
                "url": f"http://{ip_sock}:{APP_PORT}/tablet",
                "prioridad": 0,
            })
    except Exception:
        pass

    candidatas.sort(key=lambda x: x.get("prioridad", 9))
    if not candidatas:
        candidatas.append({
            "alias": "Localhost",
            "ip": "127.0.0.1",
            "url": f"http://127.0.0.1:{APP_PORT}/tablet",
            "prioridad": 9,
        })
    return candidatas


def generar_qr_svg(texto: str) -> str:
    """Genera un código QR estándar ISO/IEC 18004 en SVG 100% nativo en Python (sin librerías externas)."""
    data_bytes = texto.encode("utf-8")
    # Soportamos Version 2-L (25x25, hasta 32 bytes), Version 3-L (29x29, hasta 53 bytes) y Version 4-L (33x33, hasta 78 bytes)
    if len(data_bytes) <= 32:
        version, size, total_dcw, ec_len, align = 2, 25, 34, 10, [6, 18]
    elif len(data_bytes) <= 53:
        version, size, total_dcw, ec_len, align = 3, 29, 55, 15, [6, 22]
    else:
        data_bytes = data_bytes[:78]
        version, size, total_dcw, ec_len, align = 4, 33, 80, 20, [6, 26]

    # 1. Codificación en modo Byte (0100) + longitud (8 bits) + datos + terminador
    bits: List[int] = [0, 1, 0, 0]
    n_len = len(data_bytes)
    for i in range(7, -1, -1):
        bits.append((n_len >> i) & 1)
    for b in data_bytes:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    for _ in range(min(4, total_dcw * 8 - len(bits))):
        bits.append(0)
    while len(bits) % 8 != 0:
        bits.append(0)
    codewords: List[int] = []
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i : i + 8]:
            val = (val << 1) | b
        codewords.append(val)
    pad_bytes = [0xEC, 0x11]
    idx_p = 0
    while len(codewords) < total_dcw:
        codewords.append(pad_bytes[idx_p % 2])
        idx_p += 1

    # 2. Reed-Solomon GF(256) con polinomio primitivo 0x11D
    exp_tbl = [0] * 512
    log_tbl = [0] * 256
    x = 1
    for i in range(255):
        exp_tbl[i] = x
        log_tbl[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp_tbl[i] = exp_tbl[i - 255]

    def gf_mul(a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return exp_tbl[log_tbl[a] + log_tbl[b]]

    gen = [1]
    for i in range(ec_len):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= gf_mul(g, exp_tbl[i])
        gen = nxt

    msg_out = list(codewords) + [0] * ec_len
    for i in range(len(codewords)):
        coef = msg_out[i]
        if coef != 0:
            for j in range(1, len(gen)):
                msg_out[i + j] ^= gf_mul(gen[j], coef)
    full_cw = list(codewords) + msg_out[len(codewords) :]

    # 3. Construir matriz QR y reservar patrones de función
    mat = [[0] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, val: int) -> None:
        if 0 <= r < size and 0 <= c < size:
            mat[r][c] = val
            is_func[r][c] = True

    def draw_finder(r0: int, c0: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r0 + dr, c0 + dc
                if 0 <= rr < size and 0 <= cc < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6
                    on_border = dr in (0, 6) or dc in (0, 6)
                    in_inner = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_func(rr, cc, 1 if (in_outer and (on_border or in_inner)) else 0)

    draw_finder(0, 0)
    draw_finder(0, size - 7)
    draw_finder(size - 7, 0)

    for i in range(8, size - 8):
        set_func(6, i, 1 if i % 2 == 0 else 0)
        set_func(i, 6, 1 if i % 2 == 0 else 0)

    # Patrón de alineación
    for ar in align:
        for ac in align:
            if is_func[ar][ac]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    val = 1 if (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0)) else 0
                    set_func(ar + dr, ac + dc, val)

    # Reservar bits de formato y módulo oscuro fijo
    for i in range(9):
        if not is_func[8][i]:
            set_func(8, i, 0)
        if not is_func[i][8]:
            set_func(i, 8, 0)
    for i in range(8):
        if not is_func[8][size - 1 - i]:
            set_func(8, size - 1 - i, 0)
        if not is_func[size - 1 - i][8]:
            set_func(size - 1 - i, 8, 0)
    set_func(size - 8, 8, 1)

    # 4. Colocar bits de datos en zig-zag de derecha a izquierda
    all_bits: List[int] = []
    for cw in full_cw:
        for i in range(7, -1, -1):
            all_bits.append((cw >> i) & 1)

    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        row_iter = range(size - 1, -1, -1) if upward else range(size)
        for r in row_iter:
            for dc in (0, 1):
                c = col - dc
                if not is_func[r][c]:
                    b = all_bits[bit_idx] if bit_idx < len(all_bits) else 0
                    bit_idx += 1
                    # Máscara 0: (r + c) % 2 == 0
                    if (r + c) % 2 == 0:
                        b ^= 1
                    mat[r][c] = b
        upward = not upward
        col -= 2

    # 5. Escribir información de formato para Nivel L (01) + Máscara 0 (000) -> 0x77C4
    fmt_val = 0x77C4
    fmt_bits = [(fmt_val >> i) & 1 for i in range(15)]  # bit 0 = LSB, bit 14 = MSB
    # Alrededor de arriba-izquierda
    coords_tl = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for idx_f, (rr, cc) in enumerate(coords_tl):
        mat[rr][cc] = fmt_bits[14 - idx_f]
    # Abajo-izquierda y arriba-derecha
    for idx_f in range(7):
        mat[size - 1 - idx_f][8] = fmt_bits[14 - idx_f]
    for idx_f in range(7, 15):
        mat[8][size - 15 + idx_f] = fmt_bits[14 - idx_f]

    # 6. Renderizar SVG nítido con margen blanco (quiet zone = 4 módulos)
    qz = 4
    total_mod = size + qz * 2
    rects = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_mod} {total_mod}" width="220" height="220" shape-rendering="crispEdges">',
        f'<rect width="{total_mod}" height="{total_mod}" fill="#ffffff" rx="2"/>',
    ]
    for r in range(size):
        for c in range(size):
            if mat[r][c]:
                rects.append(f'<rect x="{c + qz}" y="{r + qz}" width="1" height="1" fill="#0f172a"/>')
    rects.append("</svg>")
    return "".join(rects)


def _info_amenidades_por_tipo(tipo_raw: str) -> Dict[str, Any]:
    t = (tipo_raw or "").strip().lower()
    if "matrimonial" in t:
        return {
            "icono": "💑",
            "camas": "1 Cama Matrimonial (2 Plazas / Queen)",
            "capacidad": "1 a 2 huéspedes",
            "descripcion": "Ideal para parejas o ejecutivos que buscan máximo confort y descanso privado.",
            "amenidades": ["Smart TV c/ Cable", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad", "Toallas y Aseo"],
            "gradiente": "from-amber-900/50 via-slate-900 to-slate-950",
            "acento": "#f59e0b",
        }
    if "doble" in t:
        return {
            "icono": "🛏️🛏️",
            "camas": "2 Camas Independientes",
            "capacidad": "2 huéspedes (o hasta 3)",
            "descripcion": "Perfecta para amigos, compañeros de trabajo o familiares con camas separadas.",
            "amenidades": ["2 Camas Amplias", "Smart TV", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad"],
            "gradiente": "from-sky-900/50 via-slate-900 to-slate-950",
            "acento": "#38bdf8",
        }
    if "triple" in t:
        return {
            "icono": "👨‍👩‍👦",
            "camas": "3 Camas Independientes",
            "capacidad": "3 huéspedes",
            "descripcion": "Habitación espaciosa pensada para grupos de 3 personas o delegaciones de empresa.",
            "amenidades": ["3 Camas Cómodas", "Smart TV", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad"],
            "gradiente": "from-indigo-900/50 via-slate-900 to-slate-950",
            "acento": "#818cf8",
        }
    if "familiar" in t or "cuadruple" in t:
        return {
            "icono": "👨‍👩‍👧‍👦",
            "camas": "Camas Múltiples Familiares",
            "capacidad": "3 a 4+ huéspedes",
            "descripcion": "Nuestra habitación más amplia para familias completas o grupos con total comodidad.",
            "amenidades": ["Espacio Familiar", "Smart TV Grande", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad"],
            "gradiente": "from-emerald-900/50 via-slate-900 to-slate-950",
            "acento": "#34d399",
        }
    return {
        "icono": "🛌",
        "camas": "1 Cama Confort",
        "capacidad": "1 a 2 huéspedes",
        "descripcion": "Habitación práctica, silenciosa y totalmente equipada para una estadía impecable.",
        "amenidades": ["Smart TV", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad", "Atención 24h"],
        "gradiente": "from-purple-900/50 via-slate-900 to-slate-950",
        "acento": "#c084fc",
    }


FOTOS_PRODS_DIR = DATA_DIR / "fotos_productos"
FOTOS_PRODS_DIR.mkdir(parents=True, exist_ok=True)
THUMBS_DIR = FOTOS_HABS_DIR / "_miniaturas"
_TAMANOS_FOTO = {"thumb": (640, 75), "med": (1600, 82)}

_DOCS_FOTOS_CANDS = [
    Path.home() / "Documents" / "FOTOS HABITACIONES",
    Path(r"C:\Users\ovalo mariategui\Documents\FOTOS HABITACIONES"),
]
_DOCS_TIENDA_CANDS = [
    Path.home() / "Documents" / "FOTOS TIENDA",
    Path(r"C:\Users\ovalo mariategui\Documents\FOTOS TIENDA"),
]
_ULTIMA_SYNC_FOTOS_TS = 0.0
_NOTAS_CARPETA_HAB: Dict[str, str] = {}


def _sincronizar_fotos_desde_documentos() -> None:
    """Sincroniza automáticamente Documents/FOTOS HABITACIONES y Documents/FOTOS TIENDA cada 25 s (TABLET_LOBBY_ANDINO_V2)."""
    global _ULTIMA_SYNC_FOTOS_TS
    ahora = time.time()
    if ahora - _ULTIMA_SYNC_FOTOS_TS < 25.0:
        return
    _ULTIMA_SYNC_FOTOS_TS = ahora
    import re as _re
    import shutil as _sh
    exts = (".jpg", ".jpeg", ".png", ".webp")

    for root in _DOCS_FOTOS_CANDS:
        if not root.exists():
            continue
        try:
            for folder in sorted(root.iterdir()):
                if not folder.is_dir():
                    continue
                m = _re.search(r"(\d{3})", folder.name)
                if not m:
                    continue
                hab_num = m.group(1)
                # Extraer etiqueta especial de carpeta si existe (ej. "VISTA A LA CALLE")
                partes = folder.name.split("-", 1)
                if len(partes) == 2:
                    nota_raw = partes[1].strip()
                    if nota_raw and "compartid" not in nota_raw.lower():
                        _NOTAS_CARPETA_HAB[hab_num] = nota_raw.title()
                files = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts])
                for idx, f in enumerate(files, 1):
                    dst_f = FOTOS_HABS_DIR / f"{hab_num}_{idx:02d}.jpg"
                    if not dst_f.exists() or dst_f.stat().st_size != f.stat().st_size:
                        _sh.copy2(f, dst_f)
        except Exception:
            pass
        break

    for root_t in _DOCS_TIENDA_CANDS:
        if not root_t.exists():
            continue
        try:
            for f in sorted(root_t.iterdir()):
                if f.is_file() and f.suffix.lower() in exts:
                    dst_p = FOTOS_PRODS_DIR / f.name
                    if not dst_p.exists() or dst_p.stat().st_size != f.stat().st_size:
                        _sh.copy2(f, dst_p)
        except Exception:
            pass
        break


def _ajustar_info_por_tipo(tipo_raw: str, info: Dict[str, Any], nota_carpeta: str = "") -> Dict[str, Any]:
    """Corrige baño compartido/privado, tina, simple y familiar; agrega maxPersonas y notas especiales."""
    t = (tipo_raw or "").strip().lower()
    info = dict(info)
    amen = list(info.get("amenidades") or [])
    compartido = "compartid" in t
    if "simple" in t:
        info["camas"] = "1 Cama de 1.5 Plazas"
        info["capacidad"] = "1 huésped"
        info["descripcion"] = "Habitación individual, silenciosa y práctica para descansar con total tranquilidad."
        info["icono"] = "🛌"
    if "familiar" in t:
        info["camas"] = "2 Camas Amplias (Familiar)"
        info["capacidad"] = "Hasta 4 huéspedes"
        info["descripcion"] = "Nuestra habitación más amplia, pensada para familias o grupos con total comodidad."
        info["icono"] = "👨‍👩‍👧‍👦"
        amen = ["Espacio Familiar", "2 Camas Amplias", "Smart TV", "Baño Privado", "Agua Caliente 24h", "Wi-Fi Alta Velocidad"]
    if "tina" in t:
        info["descripcion"] = "Matrimonial con tina de baño propia: el plus ideal para relajarse después del viaje."
        if "Tina de Baño" not in amen:
            amen.insert(0, "Tina de Baño")
    if compartido:
        amen = ["Baño Compartido" if a == "Baño Privado" else a for a in amen]
        info["descripcion"] = info["descripcion"].replace("descanso privado.", "estadía cómoda y económica.")
    if nota_carpeta and nota_carpeta not in amen:
        amen.insert(0, nota_carpeta)
    info["amenidades"] = amen
    info["banoPrivado"] = not compartido
    if "familiar" in t:
        info["maxPersonas"] = 4
    elif "triple" in t or "doble" in t:
        info["maxPersonas"] = 3
    elif "simple" in t:
        info["maxPersonas"] = 1
    else:
        info["maxPersonas"] = 2
    return info


def _parsear_precio_ref(precio_raw: str):
    """'75-65' -> (75.0, 65.0) ; '80' -> (80.0, None)."""
    import re as _re
    nums = []
    for m in _re.findall(r"\d+(?:[.,]\d+)?", precio_raw or ""):
        try:
            nums.append(float(m.replace(",", ".")))
        except Exception:
            pass
    if not nums:
        return None, None
    noche = max(nums)
    desde = min(nums)
    return noche, (desde if desde < noche else None)


def _familia_tipo_hab(tipo: str) -> str:
    t = (tipo or "").lower()
    for k in ("tina", "familiar", "matrimonial", "doble", "simple", "triple"):
        if k in t:
            return k
    return "otro"


def obtener_ruta_foto_tablet(filename: str, w: str = "", carpeta: Optional[Path] = None) -> Optional[Path]:
    """Foto original o versión liviana (?w=thumb | ?w=med) cacheada en disco (reduce fotos de 8 MB a ~55 KB)."""
    base_dir = carpeta or FOTOS_HABS_DIR
    safe = Path(filename or "").name
    src = base_dir / safe
    if not safe or not src.is_file():
        return None
    cfg = _TAMANOS_FOTO.get((w or "").strip().lower())
    if not cfg:
        return src
    lado, calidad = cfg
    try:
        st = src.stat()
        t_dir = base_dir / "_miniaturas"
        t_dir.mkdir(parents=True, exist_ok=True)
        dst = t_dir / f"{src.stem}__{w}_{int(st.st_mtime)}_{st.st_size}.jpg"
        if not dst.exists():
            from PIL import Image, ImageOps
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((lado, lado), Image.LANCZOS)
                tmp = dst.with_name(f"{dst.stem}.{threading.get_ident()}.tmp")
                im.save(tmp, "JPEG", quality=calidad, optimize=True, progressive=True)
                tmp.replace(dst)
        return dst
    except Exception:
        return src


def _normalizar_slug_prod(texto: str) -> str:
    import re as _re
    import unicodedata as _ud
    s = _ud.normalize("NFKD", (texto or "").lower()).encode("ascii", "ignore").decode("ascii")
    return _re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _buscar_foto_producto(nombre: str) -> str:
    slug = _normalizar_slug_prod(nombre)
    if not slug or not FOTOS_PRODS_DIR.exists():
        return ""
    exts = (".jpg", ".jpeg", ".png", ".webp")
    try:
        for f in sorted(FOTOS_PRODS_DIR.iterdir(), key=lambda x: x.name.lower()):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            f_slug = _normalizar_slug_prod(f.stem)
            if f_slug == slug or (len(f_slug) >= 4 and (f_slug in slug or slug in f_slug)):
                return f"/api/tablet/producto-foto/{f.name}"
    except Exception:
        pass
    return ""


def _embellecer_nombre_producto(nombre: str) -> str:
    mapa = {
        "hs pequeño": "Shampoo H&S (Sachet)",
        "hs pequeno": "Shampoo H&S (Sachet)",
        "head shoulders": "Shampoo Head & Shoulders",
        "pantene": "Shampoo Pantene (Sachet)",
        "ponds": "Crema Facial Pond's",
        "rexona hombre y mujer": "Desodorante Rexona",
        "bloqueador": "Bloqueador Solar (Sachet)",
        "prestobarba": "Afeitadora Prestobarba",
        "cepillo": "Cepillo Dental",
        "colinos chicos": "Pasta Dental Chica",
        "jabon floreal": "Jabón de Tocador",
        "toallas higienicas": "Toallas Higiénicas",
        "preservativo": "Preservativo",
        "peine": "Peine Personal",
        "agua 600ml": "Agua Mineral 600 ml",
        "agua 1l": "Agua Mineral 1 Litro",
        "coca cola y gatorade": "Coca-Cola / Gatorade",
        "coca cola lata": "Coca-Cola en Lata",
        "gaseosa kr-oro": "Gaseosa KR / Oro",
        "gaseosa energina-black": "Energina / Black",
        "chicles-sublime": "Chicles / Sublime",
        "soda-integral-bonobon": "Galleta Soda / Integral / Bon o Bon",
        "galleta general": "Galleta Dulce",
        "wafer grande": "Wafer Grande",
        "papa lays": "Papas Lays",
        "paleta": "Paleta Dulce",
    }
    low = (nombre or "").strip().lower()
    if low in mapa:
        return mapa[low]
    limpio = (nombre or "").replace("-", " / ").strip()
    return limpio[:1].upper() + limpio[1:] if limpio else ""


def _clasificar_producto_tienda(nombre: str) -> Dict[str, str]:
    n = f" {(nombre or '').lower()} "
    # 1. Aseo y Cuidado Personal (corregido: incluye marcas Pantene, H&S, Pond's, Rexona, Bloqueador, etc.)
    if any(k in n for k in ("shampoo", "champu", "pantene", "head", "h&s", " hs ", "sedal", "acondicionador")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🧴", "subtipo": "shampoo"}
    if any(k in n for k in ("cepillo", "colinos", "colgate", "pasta", "dental", "enjuague", "listerine")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🪥", "subtipo": "dental"}
    if any(k in n for k in ("jabon", "jabón", "floreal", "dove")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🧼", "subtipo": "jabon"}
    if any(k in n for k in ("rexona", "desodorante", "axe")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "✨", "subtipo": "desodorante"}
    if any(k in n for k in ("ponds", "pond", "crema", "bloqueador", "protector", "nivea")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🧴", "subtipo": "crema"}
    if any(k in n for k in ("prestobarba", "gillette", "afeitar")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🪒", "subtipo": "afeitar"}
    if any(k in n for k in ("toalla", "papel", "kotex", "nosotras", "higienic", "higiénic", "preservativo", "condon", "condón", "peine", "algodon")):
        return {"categoria": "Aseo y Cuidado Personal", "icono": "🛡️", "subtipo": "cuidado"}

    # 2. Bebidas
    if "agua" in n:
        return {"categoria": "Bebidas Heladas y Calientes", "icono": "💧", "subtipo": "agua"}
    if any(k in n for k in ("coca", "inca", "gaseosa", " kr", "kr-", "frugos", "jugo")):
        return {"categoria": "Bebidas Heladas y Calientes", "icono": "🥤", "subtipo": "gaseosa"}
    if any(k in n for k in ("sporade", "gatorade", "energ", "volt", "red bull")):
        return {"categoria": "Bebidas Heladas y Calientes", "icono": "⚡", "subtipo": "hidratante"}
    if any(k in n for k in ("cerveza", "pilsen", "cusque", "anís", "anis", "mate", " té", " te ", "café", "cafe")):
        return {"categoria": "Bebidas Heladas y Calientes", "icono": "☕", "subtipo": "bebida"}

    # 3. Snacks, Galletas y Chocolates
    if "wafer" in n or "galleta" in n or "soda" in n:
        return {"categoria": "Snacks, Galletas y Chocolates", "icono": "🍪", "subtipo": "galleta"}
    if "papa" in n or "lays" in n or "piqueo" in n:
        return {"categoria": "Snacks, Galletas y Chocolates", "icono": "🥔", "subtipo": "papas"}
    return {"categoria": "Snacks, Galletas y Chocolates", "icono": "🍫", "subtipo": "chocolate"}


def _buscar_fotos_habitacion(hab: str, tipo: str) -> List[str]:
    """Busca si el usuario colocó fotos reales en data/fotos_habitaciones/ (por N° hab o por Tipo)."""
    urls: List[str] = []
    exts = (".jpg", ".jpeg", ".png", ".webp")
    try:
        archivos = list(FOTOS_HABS_DIR.iterdir())
    except Exception:
        archivos = []

    hab_low = hab.lower().strip()
    tipo_low = (tipo or "").lower().strip().replace(" ", "_")

    vistas_huella = set()
    for f in sorted(archivos, key=lambda x: x.name.lower()):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        stem = f.stem.lower()
        if stem.startswith(("logo_", "qr_", "icon_", "_")):
            continue
        if stem == hab_low or stem.startswith(f"{hab_low}_") or stem.startswith(f"hab_{hab_low}"):
            try:
                st_sz = f.stat().st_size
                with f.open("rb") as fh:
                    huella = (st_sz, fh.read(4096))
                if huella in vistas_huella:
                    continue
                vistas_huella.add(huella)
            except Exception:
                pass
            urls.append(f"/api/tablet/foto/{f.name}")
    if not urls and tipo_low:
        for f in sorted(archivos, key=lambda x: x.name.lower()):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            stem = f.stem.lower()
            if stem.startswith(("logo_", "qr_", "icon_", "_")):
                continue
            if tipo_low in stem or stem in tipo_low:
                urls.append(f"/api/tablet/foto/{f.name}")
    return urls


def registrar_latido_tablet(client_ip: str, user_agent: str = "") -> None:
    if not client_ip:
        return
    with _tablet_lock:
        ip_actual = str(_TABLET_LIVE_STATE.get("tablet_ip") or "")
        es_local = client_ip in ("127.0.0.1", "::1", "localhost")
        if es_local and ip_actual and ip_actual not in ("127.0.0.1", "::1", "localhost"):
            return
        _TABLET_LIVE_STATE["ultimo_latido_ts"] = time.time()
        _TABLET_LIVE_STATE["tablet_ip"] = client_ip
        if user_agent:
            _TABLET_LIVE_STATE["tablet_user_agent"] = user_agent[:120]


def obtener_datos_tablet_publico(client_ip: str = "", user_agent: str = "") -> Dict[str, Any]:
    """Devuelve toda la información sanitizada para la Tablet S8+ en 0 ms."""
    registrar_latido_tablet(client_ip, user_agent)
    _sincronizar_fotos_desde_documentos()
    panel = obtener_datos_panel_local()
    vitrina = obtener_datos_vitrina_local()
    fecha_hoy = hoy_dmy()

    # Leer estados de limpieza marcados hoy en la tablet
    mapa_limpieza_hoy: Dict[str, Dict[str, str]] = {}
    conn = get_connection()
    try:
        for r in conn.execute(
            "SELECT hab, estado_limpieza, nota, updated_at FROM tablet_limpieza_dia WHERE fecha_dmy = ?",
            (fecha_hoy,),
        ).fetchall():
            mapa_limpieza_hoy[str(r["hab"]).strip()] = {
                "estado": str(r["estado_limpieza"] or ""),
                "nota": str(r["nota"] or ""),
                "updatedAt": str(r["updated_at"] or ""),
            }
    finally:
        conn.close()

    habitaciones_publicas: List[Dict[str, Any]] = []
    pisos_set = set()
    tipos_set = []

    for h in panel.get("habitaciones", []):
        hab_num = str(h.get("hab") or "").strip()
        if not hab_num:
            continue
        piso = hab_num[0] if hab_num and hab_num[0].isdigit() else "1"
        pisos_set.add(piso)

        tipo_raw = str(h.get("tipo") or "Estándar").strip()
        if tipo_raw and tipo_raw not in tipos_set:
            tipos_set.append(tipo_raw)

        precio_raw = str(h.get("precioRef") or "").strip()
        color_pers = str(h.get("colorPersonalizado") or "").strip().lower()
        nota_color = str(h.get("notaColor") or "").strip()
        estado_panel = str(h.get("estado") or "libre").strip().lower()
        ocupada_ayer = bool(h.get("ocupadaAyer"))
        tuvo_salida_hoy = len(h.get("historialDia") or []) > 0
        continuo_est = bool(h.get("continuoEstancia"))

        # Determinar estado de limpieza real hoy
        reg_limp = mapa_limpieza_hoy.get(hab_num, {})
        est_limp_manual = reg_limp.get("estado", "")

        if color_pers == "#f87171":
            estado_limpieza = "MANTENIMIENTO"
        elif estado_panel != "libre":
            if est_limp_manual == "LIMPIA":
                estado_limpieza = "OCUPADA_LIMPIA"
            elif continuo_est:
                estado_limpieza = "REPASO_CONTINUA"
            else:
                estado_limpieza = "OCUPADA_ACTUAL"
        else:
            # Habitación libre hoy
            if est_limp_manual == "LIMPIA":
                estado_limpieza = "LIMPIA"
            elif est_limp_manual in ("PENDIENTE", "EN_PREPARACION"):
                estado_limpieza = "PENDIENTE"
            elif tuvo_salida_hoy or ocupada_ayer:
                # Salió un huésped hoy o estuvo ocupada anoche y aún no fue marcada limpia hoy
                estado_limpieza = "PENDIENTE"
            else:
                estado_limpieza = "LIMPIA"

        # Determinar estado público para el catálogo de venta
        if color_pers == "#f87171":
            estado_publico = "MANTENIMIENTO"
        elif estado_panel != "libre":
            estado_publico = "OCUPADA"
        elif est_limp_manual == "EN_PREPARACION":
            estado_publico = "EN_PREPARACION"
        else:
            estado_publico = "LIBRE"

        info_tipo = _ajustar_info_por_tipo(tipo_raw, _info_amenidades_por_tipo(tipo_raw), _NOTAS_CARPETA_HAB.get(hab_num, ""))
        fotos = _buscar_fotos_habitacion(hab_num, tipo_raw)
        if hab_num in ("105", "201") and len(fotos) > 1:
            fotos = fotos[1:] + fotos[:1]
        precio_noche, precio_desde = _parsear_precio_ref(precio_raw)

        habitaciones_publicas.append({
            "hab": hab_num,
            "piso": piso,
            "tipo": tipo_raw,
            "precioRef": precio_raw,
            "estadoPublico": estado_publico,
            "estadoLimpieza": estado_limpieza,
            "notaLimpieza": reg_limp.get("nota") or nota_color,
            "horaLimpieza": reg_limp.get("updatedAt", ""),
            "ocupadaAyer": ocupada_ayer,
            "tuvoSalidaHoy": tuvo_salida_hoy,
            "continuoEstancia": continuo_est,
            "icono": info_tipo["icono"],
            "camas": info_tipo["camas"],
            "capacidad": info_tipo["capacidad"],
            "descripcion": info_tipo["descripcion"],
            "amenidades": info_tipo["amenidades"],
            "gradiente": info_tipo["gradiente"],
            "acento": info_tipo["acento"],
            "fotos": fotos,
            "precioNoche": precio_noche,
            "precioDesde": precio_desde,
            "banoPrivado": info_tipo["banoPrivado"],
            "maxPersonas": info_tipo["maxPersonas"],
            "notaDestacada": _NOTAS_CARPETA_HAB.get(hab_num, ""),
        })

    # Fotos representativas por tipo para habitaciones sin carpeta propia (ej. 303 toma galería de 304)
    _fotos_por_tipo: Dict[str, List[str]] = {}
    _fotos_por_familia: Dict[str, List[str]] = {}
    for _hp in habitaciones_publicas:
        if _hp["fotos"]:
            _fotos_por_tipo.setdefault(_hp["tipo"], _hp["fotos"][:5])
            _fotos_por_familia.setdefault(_familia_tipo_hab(_hp["tipo"]), _hp["fotos"][:5])
    for _hp in habitaciones_publicas:
        if _hp["fotos"]:
            _hp["fotoTipo"] = ""
            _hp["fotosReferenciales"] = []
            _hp["fotoReferencial"] = False
        else:
            _ref_list = _fotos_por_tipo.get(_hp["tipo"]) or _fotos_por_familia.get(_familia_tipo_hab(_hp["tipo"]), [])
            _hp["fotoTipo"] = _ref_list[0] if _ref_list else ""
            _hp["fotosReferenciales"] = list(_ref_list)
            _hp["fotoReferencial"] = bool(_ref_list)

    # Productos de Vitrina Tienda sanitizados para el cliente
    productos_tienda: List[Dict[str, Any]] = []
    for p in vitrina.get("productos", []):
        nom = str(p.get("producto") or "").strip()
        if not nom:
            continue
        try:
            st_val = float(str(p.get("stockVitrina") or "0").replace(",", "."))
        except Exception:
            st_val = 0.0
        precio_v = float(p.get("precioVenta") or 0.0)
        meta_p = _clasificar_producto_tienda(nom)
        productos_tienda.append({
            "producto": nom,
            "nombreMostrado": _embellecer_nombre_producto(nom),
            "precio": precio_v,
            "stockDisponible": max(0, int(st_val)),
            "disponible": st_val > 0,
            "nivelStock": "AGOTADO" if st_val <= 0 else ("ULTIMAS" if st_val <= 3 else "OK"),
            "categoria": meta_p["categoria"],
            "icono": meta_p["icono"],
            "subtipo": meta_p.get("subtipo", "snack"),
            "fotoUrl": _buscar_foto_producto(nom),
        })

    with _tablet_lock:
        ctrl = {
            "mostrarPrecios": bool(_TABLET_LIVE_STATE["mostrar_precios"]),
            "modoPantalla": str(_TABLET_LIVE_STATE["modo_pantalla"]),
            "ordenSeq": int(_TABLET_LIVE_STATE["orden_seq"]),
            "habDestacada": str(_TABLET_LIVE_STATE["hab_destacada"]),
            "cobroQr": dict(_TABLET_LIVE_STATE["cobro_qr"]),
            "firmaSolicitud": dict(_TABLET_LIVE_STATE["firma_solicitud"]),
            "seleccionActualHab": str(_TABLET_LIVE_STATE["evento_cliente"].get("hab") or ""),
        }

    try:
        mtime_t = int((STATIC_DIR / "TabletKiosco.html").stat().st_mtime)
    except Exception:
        mtime_t = 1

    return {
        "nombreHotel": NOMBRE_HOTEL,
        "fechaHoy": fecha_hoy,
        "uiVersionTablet": mtime_t,
        "control": ctrl,
        "pisos": sorted(list(pisos_set)),
        "tipos": tipos_set,
        "habitaciones": habitaciones_publicas,
        "productosTienda": productos_tienda,
    }


def seleccionar_habitacion_desde_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    hab = str(payload.get("hab") or "").strip()
    tipo = str(payload.get("tipo") or "").strip()
    precio = str(payload.get("precioRef") or "").strip()
    personas = int(payload.get("personas") or 2)
    if not hab:
        raise ValueError("Selecciona una habitación válida.")

    ahora_str = datetime.now().strftime("%H:%M:%S")
    with _tablet_lock:
        ev = _TABLET_LIVE_STATE["evento_cliente"]
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev["timestamp"] = ahora_str
        ev["tipo"] = "HAB_ELEGIDA"
        ev["leido_en_laptop"] = False
        ev["hab"] = hab
        ev["tipo_hab"] = tipo
        ev["precio_ref"] = precio
        ev["personas"] = personas
        ev["resumen"] = f"Huésped en Tablet eligió Hab. {hab} ({tipo}) para {personas} pers."
        return {"ok": True, "evento": dict(ev)}


def consultar_documento_desde_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    tipo_doc = str(payload.get("tipo") or "DNI").upper().strip()
    numero = str(payload.get("numero") or "").strip()
    if tipo_doc == "RUC":
        return consultar_ruc_local_o_sunat(numero)
    return consultar_dni_local_o_reniec(numero)


def enviar_datos_checkin_desde_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    dni = str(payload.get("dni") or "").strip()
    nombre = str(payload.get("nombre") or "").strip().upper()
    celular = str(payload.get("celular") or "").strip()
    ruc = str(payload.get("ruc") or "").strip()
    razon_social = str(payload.get("razonSocial") or "").strip().upper()
    hab = str(payload.get("hab") or "").strip()

    if not dni and not celular and not ruc:
        raise ValueError("Ingresa al menos tu DNI o documento de identidad.")

    # Si el huésped digitó su DNI pero aún no se consultó su nombre, consultarlo en 0ms
    if dni and not nombre:
        try:
            r_dni = consultar_dni_local_o_reniec(dni)
            if r_dni and r_dni.get("nombre"):
                nombre = str(r_dni["nombre"]).strip().upper()
        except Exception:
            pass

    if ruc and not razon_social:
        try:
            r_ruc = consultar_ruc_local_o_sunat(ruc)
            if r_ruc and r_ruc.get("razonSocial"):
                razon_social = str(r_ruc["razonSocial"]).strip().upper()
        except Exception:
            pass

    ahora_str = datetime.now().strftime("%H:%M:%S")
    with _tablet_lock:
        ev = _TABLET_LIVE_STATE["evento_cliente"]
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev["timestamp"] = ahora_str
        ev["tipo"] = "DATOS_CHECKIN"
        ev["leido_en_laptop"] = False
        if hab:
            ev["hab"] = hab
        ev["dni"] = dni
        ev["nombre"] = nombre
        ev["celular"] = celular
        ev["ruc"] = ruc
        ev["razon_social"] = razon_social
        hab_txt = f" [Hab. {ev['hab']}]" if ev.get("hab") else ""
        ev["resumen"] = f"Datos recibidos{hab_txt}: {nombre or dni} · Cel: {celular or '-'}" + (f" · RUC: {ruc}" if ruc else "")
        return {"ok": True, "nombreDetectado": nombre, "razonSocialDetectada": razon_social, "evento": dict(ev)}


def enviar_pedido_vitrina_desde_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = payload.get("items") or []
    hab = str(payload.get("hab") or "Mostrador").strip()
    if not items:
        raise ValueError("Agrega al menos un producto al pedido.")

    total = 0.0
    partes = []
    items_limpios = []
    for it in items:
        prod = str(it.get("producto") or "").strip()
        cant = max(1, int(it.get("cantidad") or 1))
        precio = float(it.get("precio") or 0.0)
        sub = round(cant * precio, 2)
        total = round(total + sub, 2)
        partes.append(f"{cant}x {prod}")
        items_limpios.append({"producto": prod, "cantidad": cant, "precio": precio, "subtotal": sub})

    ahora_str = datetime.now().strftime("%H:%M:%S")
    with _tablet_lock:
        ev = _TABLET_LIVE_STATE["evento_cliente"]
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev["timestamp"] = ahora_str
        ev["tipo"] = "PEDIDO_TIENDA"
        ev["leido_en_laptop"] = False
        ev["hab"] = hab
        ev["items_tienda"] = items_limpios
        ev["total_tienda"] = total
        ev["resumen"] = f"Pedido Tienda ({hab}): {', '.join(partes)} — Total S/ {total:.2f}"
        return {"ok": True, "evento": dict(ev)}


def cambiar_estado_limpieza_desde_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    hab = str(payload.get("hab") or "").strip()
    nuevo_estado = str(payload.get("estado") or "LIMPIA").strip().upper()
    nota = str(payload.get("nota") or "").strip()[:80]
    if not hab:
        raise ValueError("Habitación requerida.")
    if nuevo_estado not in ("LIMPIA", "PENDIENTE", "EN_PREPARACION"):
        nuevo_estado = "LIMPIA"

    fecha_hoy = hoy_dmy()
    ahora_hm = datetime.now().strftime("%H:%M")
    with db_transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO tablet_limpieza_dia (hab, fecha_dmy, estado_limpieza, nota, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (hab, fecha_hoy, nuevo_estado, nota, ahora_hm),
        )
    return {"ok": True, "hab": hab, "estado": nuevo_estado, "hora": ahora_hm}


def guardar_firma_conformidad_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    hab = str(payload.get("hab") or "").strip()
    huesped = str(payload.get("huesped") or "").strip().upper()
    dni = str(payload.get("dni") or "").strip()
    empresa = str(payload.get("empresa") or "").strip().upper()
    celular = str(payload.get("celular") or "").strip()
    firma_b64 = str(payload.get("firmaBase64") or "").strip()
    obs = str(payload.get("observaciones") or "").strip()
    id_estancia = str(payload.get("idEstancia") or "").strip()

    if not firma_b64 or not firma_b64.startswith("data:image/"):
        raise ValueError("Por favor, realiza tu firma en el recuadro con el S Pen o el dedo.")

    fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with db_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO tablet_firmas_conformidad
            (fecha_hora, hab, id_estancia, huesped, dni, empresa, celular, firma_base64, observaciones)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fecha_hora, hab, id_estancia, huesped, dni, empresa, celular, firma_b64, obs),
        )
        firma_id = cur.lastrowid

    with _tablet_lock:
        _TABLET_LIVE_STATE["firma_solicitud"]["activa"] = False
        ev = _TABLET_LIVE_STATE["evento_cliente"]
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev["timestamp"] = datetime.now().strftime("%H:%M:%S")
        ev["tipo"] = "FIRMA_COMPLETADA"
        ev["leido_en_laptop"] = False
        ev["hab"] = hab
        ev["nombre"] = huesped
        ev["dni"] = dni
        ev["resumen"] = f"✍️ Firma de conformidad registrada: {huesped or dni} (Hab. {hab or '-'} · {empresa or 'Particular'})"

    return {"ok": True, "id": firma_id, "fechaHora": fecha_hora}


def guardar_sugerencia_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    calificacion = max(1, min(5, int(payload.get("calificacion") or 5)))
    categoria = str(payload.get("categoria") or "General").strip()
    hab = str(payload.get("hab") or "").strip()
    comentario = str(payload.get("comentario") or "").strip()
    huesped_opc = str(payload.get("huesped") or "").strip()

    fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with db_transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO tablet_buzon_mejoras
            (fecha_hora, calificacion, categoria, hab, comentario, huesped_opcional)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (fecha_hora, calificacion, categoria, hab, comentario, huesped_opc),
        )
        sug_id = cur.lastrowid

    with _tablet_lock:
        ev = _TABLET_LIVE_STATE["evento_cliente"]
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev["timestamp"] = datetime.now().strftime("%H:%M:%S")
        ev["tipo"] = "SUGERENCIA_NUEVA"
        ev["leido_en_laptop"] = False
        ev["hab"] = hab
        ev["resumen"] = f"{'⭐' * calificacion} ({categoria}) Hab. {hab or '-'}: {comentario[:80] or 'Calificación enviada'}"

    return {"ok": True, "id": sug_id, "fechaHora": fecha_hora}


def obtener_panel_control_tablet() -> Dict[str, Any]:
    """Llamado por la Laptop de Recepción para ver el QR, estado de conexión, bandeja en vivo, firmas y buzón."""
    ips = obtener_ips_locales()
    ahora = time.time()

    with _tablet_lock:
        latido = float(_TABLET_LIVE_STATE["ultimo_latido_ts"] or 0.0)
        hace_seg = int(ahora - latido) if latido > 0 else 999999
        conectada = hace_seg <= 12
        estado_ctrl = {
            "conectada": conectada,
            "haceSegundos": hace_seg,
            "tabletIp": _TABLET_LIVE_STATE["tablet_ip"],
            "mostrarPrecios": _TABLET_LIVE_STATE["mostrar_precios"],
            "modoPantalla": _TABLET_LIVE_STATE["modo_pantalla"],
            "habDestacada": _TABLET_LIVE_STATE["hab_destacada"],
            "cobroQr": dict(_TABLET_LIVE_STATE["cobro_qr"]),
            "firmaSolicitud": dict(_TABLET_LIVE_STATE["firma_solicitud"]),
            "eventoCliente": dict(_TABLET_LIVE_STATE["evento_cliente"]),
        }

    conn = get_connection()
    try:
        firmas = [
            {
                "id": int(r["id"]),
                "fechaHora": str(r["fecha_hora"]),
                "hab": str(r["hab"]),
                "huesped": str(r["huesped"]),
                "dni": str(r["dni"]),
                "empresa": str(r["empresa"]),
                "celular": str(r["celular"]),
                "firmaBase64": str(r["firma_base64"]),
                "observaciones": str(r["observaciones"]),
            }
            for r in conn.execute("SELECT * FROM tablet_firmas_conformidad ORDER BY id DESC LIMIT 15").fetchall()
        ]
        sugerencias = [
            {
                "id": int(r["id"]),
                "fechaHora": str(r["fecha_hora"]),
                "calificacion": int(r["calificacion"]),
                "categoria": str(r["categoria"]),
                "hab": str(r["hab"]),
                "comentario": str(r["comentario"]),
                "huesped": str(r["huesped_opcional"]),
            }
            for r in conn.execute("SELECT * FROM tablet_buzon_mejoras ORDER BY id DESC LIMIT 25").fetchall()
        ]
    finally:
        conn.close()

    for item_ip in ips:
        try:
            item_ip["qrSvg"] = generar_qr_svg(item_ip["url"])
        except Exception:
            item_ip["qrSvg"] = ""
    url_principal = ips[0]["url"] if ips else f"http://127.0.0.1:{APP_PORT}/tablet"
    qr_principal = ips[0].get("qrSvg", "") if ips else generar_qr_svg(url_principal)

    return {
        "urls": ips,
        "urlPrincipal": url_principal,
        "qrSvgPrincipal": qr_principal,
        "estado": estado_ctrl,
        "firmasRecientes": firmas,
        "sugerenciasRecientes": sugerencias,
    }


def actualizar_control_remoto_tablet(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Llamado por el recepcionista desde la Laptop para cambiar lo que muestra la Tablet S8+ en vivo."""
    with _tablet_lock:
        if "mostrarPrecios" in payload:
            _TABLET_LIVE_STATE["mostrar_precios"] = bool(payload["mostrarPrecios"])
        if "modoPantalla" in payload and payload["modoPantalla"]:
            _TABLET_LIVE_STATE["modo_pantalla"] = str(payload["modoPantalla"]).strip()
        if "habDestacada" in payload:
            _TABLET_LIVE_STATE["hab_destacada"] = str(payload["habDestacada"] or "").strip()

        if "cobroQr" in payload and isinstance(payload["cobroQr"], dict):
            c_in = payload["cobroQr"]
            _TABLET_LIVE_STATE["cobro_qr"].update({
                "activo": bool(c_in.get("activo", True)),
                "monto": float(c_in.get("monto") or 0.0),
                "hab": str(c_in.get("hab") or "").strip(),
                "concepto": str(c_in.get("concepto") or "Hospedaje").strip(),
                "numero_yape": str(c_in.get("numeroYape") or _TABLET_LIVE_STATE["cobro_qr"].get("numero_yape") or "").strip(),
            })
            if c_in.get("activo", True):
                _TABLET_LIVE_STATE["modo_pantalla"] = "cobro_qr"

        if "firmaSolicitud" in payload and isinstance(payload["firmaSolicitud"], dict):
            f_in = payload["firmaSolicitud"]
            _TABLET_LIVE_STATE["firma_solicitud"].update({
                "activa": bool(f_in.get("activa", True)),
                "hab": str(f_in.get("hab") or "").strip(),
                "id_estancia": str(f_in.get("idEstancia") or "").strip(),
                "huesped": str(f_in.get("huesped") or "").strip(),
                "dni": str(f_in.get("dni") or "").strip(),
                "empresa": str(f_in.get("empresa") or "").strip(),
                "check_in": str(f_in.get("checkIn") or "").strip(),
                "check_out": str(f_in.get("checkOut") or "").strip(),
                "tarifa": str(f_in.get("tarifa") or "").strip(),
            })
            if f_in.get("activa", True):
                _TABLET_LIVE_STATE["modo_pantalla"] = "firma_empresa"

        if payload.get("marcarEventoLeido"):
            _TABLET_LIVE_STATE["evento_cliente"]["leido_en_laptop"] = True

        _TABLET_LIVE_STATE["orden_seq"] = int(_TABLET_LIVE_STATE["orden_seq"]) + 1

    return obtener_panel_control_tablet()
