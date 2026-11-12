#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crea (o refresca) el SISTEMA DE PRUEBA "ovalo_mariategui_prueba" en ESTA laptop.

Qué hace:
  1. Copia el código de la operativa a C:\\ovalo_mariategui_prueba (sin tocar la operativa).
  2. Toma una FOTO (backup seguro) de la base de datos operativa -> base de datos propia de la prueba.
  3. Escribe data/hotel_perfil.json con modo_prueba=true, puerto 8082 y SIN hoja de Google real.
  4. Crea el acceso directo "Ovalo_Mariategui_PRUEBA" en el Escritorio (con ícono naranja "P").
  5. Si hay Supabase, deja constancia (estado_sistema) para que la otra laptop/Antigravity pueda verificarlo.

Uso:
  python crear_entorno_prueba.py                 -> crea si no existe (si ya existe no toca nada)
  python crear_entorno_prueba.py --solo-datos    -> refresca SOLO los datos (foto nueva de la operativa)
  python crear_entorno_prueba.py --solo-codigo   -> recopia el código de la operativa (pisa tus ediciones de prueba)
  python crear_entorno_prueba.py --rehacer       -> borra y vuelve a crear todo
  python crear_entorno_prueba.py --destino D:\\otra_carpeta
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DESTINO_DEFECTO = Path(r"C:\ovalo_mariategui_prueba")
PUERTO_PRUEBA = 8082
NOMBRE_ACCESO = "Ovalo_Mariategui_PRUEBA"

EXCLUIR_DIRS = {".git", "__pycache__", "data", "backups", "respaldos", "node_modules", ".venv", "scratch"}
EXCLUIR_EXT = {".db", ".db-wal", ".db-shm", ".db-journal", ".log", ".pyc", ".bak", ".flag"}
MAX_BYTES = 40 * 1024 * 1024

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def log(msg: str) -> None:
    linea = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(linea, flush=True)


def sha256_de(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def iterar_archivos_codigo(raiz: Path):
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUIR_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXCLUIR_EXT:
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield p


def ruta_db_operativa(app_dir: Path) -> Path:
    """Misma lógica de config.py: ../ovalo_sistema.db si existe, si no data/ovalo_sistema.db."""
    raiz = app_dir.parent / "ovalo_sistema.db"
    if raiz.exists() and raiz.stat().st_size > 0:
        return raiz
    return app_dir / "data" / "ovalo_sistema.db"


def carpeta_operativa() -> Path:
    """Si este script corre dentro de una carpeta de PRUEBA, la operativa es la que dice su perfil."""
    perfil = AQUI / "data" / "hotel_perfil.json"
    if perfil.exists():
        try:
            d = json.loads(perfil.read_text(encoding="utf-8-sig"))
            if d.get("modo_prueba") and d.get("carpeta_operativa"):
                return Path(d["carpeta_operativa"])
        except Exception:
            pass
    return AQUI


def detener_servidor_prueba(destino: Path) -> None:
    """Cierra el servidor de prueba (puerto 8082) si está encendido, para poder reemplazar archivos."""
    if os.name != "nt":
        return
    ps = (
        f"$p = (Get-NetTCPConnection -LocalPort {PUERTO_PRUEBA} -State Listen -ErrorAction SilentlyContinue).OwningProcess "
        "| Sort-Object -Unique; foreach ($i in $p) { if ($i -gt 4) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue } }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    time.sleep(1.5)


def copiar_codigo(origen: Path, destino: Path) -> int:
    n = 0
    manifiesto = {}
    for p in iterar_archivos_codigo(origen):
        rel = p.relative_to(origen)
        dst = destino / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        manifiesto[rel.as_posix()] = sha256_de(dst)
        n += 1
    (destino / "data").mkdir(parents=True, exist_ok=True)
    (destino / "data" / "base_manifest.json").write_text(
        json.dumps({"creado": datetime.now().isoformat(timespec="seconds"), "origen": str(origen), "archivos": manifiesto}, indent=1),
        encoding="utf-8",
    )
    # Extras de datos livianos (fotos de habitaciones/productos y diccionarios) para que la pantalla se vea igual
    for extra in ("fotos_habitaciones", "fotos_productos"):
        s = origen / "data" / extra
        if s.exists():
            d = destino / "data" / extra
            if not d.exists():
                try:
                    shutil.copytree(s, d)
                except Exception:
                    pass
    for jf in ("diccionario_hostal.json",):
        s = origen / "data" / jf
        if s.exists():
            shutil.copy2(s, destino / "data" / jf)
    return n


def foto_de_la_base_de_datos(origen: Path, destino: Path) -> Path:
    src_db = ruta_db_operativa(origen)
    dst_db = destino / "data" / "ovalo_sistema.db"
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if dst_db.exists():
        ant = destino / "data" / "ovalo_sistema_prueba_anterior.db"
        try:
            shutil.copy2(dst_db, ant)
        except Exception:
            pass
        for suf in ("", "-wal", "-shm"):
            try:
                Path(str(dst_db) + suf).unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
    log(f"Foto de la base de datos: {src_db} -> {dst_db}")
    src = sqlite3.connect(str(src_db), timeout=30)
    dst = sqlite3.connect(str(dst_db))
    try:
        src.backup(dst)
        # Limpieza: la prueba nunca envía nada a la nube
        for sql in (
            "DELETE FROM _supabase_outbox",
            "DELETE FROM estado_sistema WHERE clave LIKE 'heartbeat_%'",
        ):
            try:
                dst.execute(sql)
            except Exception:
                pass
        try:
            dst.execute(
                "INSERT INTO estado_sistema (clave, valor, updated_at) VALUES ('spreadsheet_id_activo', 'SIN_HOJA_MODO_PRUEBA', '') "
                "ON CONFLICT(clave) DO UPDATE SET valor='SIN_HOJA_MODO_PRUEBA'"
            )
        except Exception:
            pass
        try:
            dst.execute(
                "INSERT INTO estado_sistema (clave, valor, updated_at) VALUES ('modo_sistema', 'PRUEBA', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor='PRUEBA', updated_at=excluded.updated_at",
                (datetime.now().isoformat(),),
            )
        except Exception:
            pass
        dst.commit()
        n_est = dst.execute("SELECT COUNT(*) FROM estancias").fetchone()[0]
        log(f"Base de prueba lista: {n_est} estancias copiadas.")
    finally:
        src.close()
        dst.close()
    return dst_db


def escribir_perfil(origen: Path, destino: Path) -> None:
    perfil = {
        "nombre_hotel": "Ovalo Mariategui PRUEBA",
        "spreadsheet_id": "SIN_HOJA_MODO_PRUEBA",
        "col_tipo_adicional": "BA",
        "formula_snacks_en_s2": False,
        "app_port": PUERTO_PRUEBA,
        "modo_prueba": True,
        "carpeta_operativa": str(origen),
        "creado_en": datetime.now().isoformat(timespec="seconds"),
    }
    (destino / "data").mkdir(parents=True, exist_ok=True)
    (destino / "data" / "hotel_perfil.json").write_text(json.dumps(perfil, indent=2, ensure_ascii=False), encoding="utf-8")


def crear_icono(destino: Path) -> Path | None:
    ico = destino / "prueba.ico"
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 248, 248), fill=(234, 88, 12, 255), outline=(255, 255, 255, 255), width=10)
        try:
            fuente = ImageFont.truetype("arialbd.ttf", 150)
        except Exception:
            fuente = ImageFont.load_default()
        d.text((128, 132), "P", fill=(255, 255, 255, 255), font=fuente, anchor="mm")
        img.save(str(ico), format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        return ico
    except Exception as exc:
        log(f"(Ícono personalizado no disponible: {exc}; se usará el ícono por defecto)")
        return None


def crear_acceso_directo(destino: Path) -> str:
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw
    ico = crear_icono(destino)
    icono = f"{ico},0" if ico else "shell32.dll,43"
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$desk = [Environment]::GetFolderPath('Desktop'); "
        f"$lnk = Join-Path $desk '{NOMBRE_ACCESO}.lnk'; "
        "$s = $ws.CreateShortcut($lnk); "
        f"$s.TargetPath = '{exe}'; "
        f"$s.Arguments = '\"{destino}\\lanzador_escritorio.py\"'; "
        f"$s.WorkingDirectory = '{destino}'; "
        f"$s.IconLocation = '{icono}'; "
        "$s.Description = 'Sistema de PRUEBA - Ovalo Mariategui (no toca la operativa)'; "
        "$s.Save(); Write-Output $lnk"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    ruta = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
    log(f"Acceso directo en el Escritorio: {ruta or 'NO se pudo crear: ' + (r.stderr or '')[:200]}")
    return ruta


def escribir_leeme(destino: Path, origen: Path) -> None:
    txt = f"""SISTEMA DE PRUEBA - OVALO MARIATEGUI PRUEBA
=============================================
Carpeta de prueba : {destino}
Operativa real    : {origen}
Puerto de prueba  : http://127.0.0.1:{PUERTO_PRUEBA}   (la operativa usa 8080)

REGLAS DE ORO
 - Aqui puedes editar y probar lo que quieras: NO toca la hoja de Google, ni Supabase, ni SUNAT, ni WhatsApp,
   ni la otra laptop, ni la base de datos real.
 - Los datos de prueba son una FOTO de la operativa. Para traer datos frescos: Refrescar_Datos_Prueba.bat
 - Cuando TODO este OK en la prueba: Promover_a_Operativa.bat (muestra el plan, hace respaldo y publica a GitHub;
   las 2 laptops se actualizan solas).
 - Si algo cambio en la operativa mientras probabas, el promotor lo detecta (CONFLICTO) y NO lo pisa.
"""
    (destino / "LEEME_PRUEBA.txt").write_text(txt, encoding="utf-8")


def avisar_supabase(destino: Path, origen: Path) -> None:
    """Deja constancia en estado_sistema (solo corre en la operativa, que tiene Supabase)."""
    try:
        sys.path.insert(0, str(origen))
        import urllib.request

        import supabase_sync as sb

        if not sb.SUPABASE_ENABLED:
            return
        valor = f"CREADO | {destino} | puerto {PUERTO_PRUEBA} | {datetime.now().isoformat(timespec='seconds')}"
        payload = [{"clave": f"entorno_prueba_{sb.EQUIPO_ID}", "valor": valor, "updated_at": datetime.now().isoformat()}]
        req = urllib.request.Request(
            f"{sb.SUPABASE_URL}/rest/v1/estado_sistema?on_conflict=clave",
            data=json.dumps(payload).encode("utf-8"),
            headers=sb._headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).close()
        log("Constancia registrada en Supabase (estado_sistema).")
    except Exception as exc:
        log(f"(Sin constancia en Supabase: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default=str(DESTINO_DEFECTO))
    ap.add_argument("--solo-datos", action="store_true")
    ap.add_argument("--solo-codigo", action="store_true")
    ap.add_argument("--rehacer", action="store_true")
    args = ap.parse_args()

    origen = carpeta_operativa()
    destino = Path(args.destino)
    if destino.resolve() == origen.resolve():
        log("ERROR: el destino no puede ser la misma carpeta operativa.")
        return 2
    existe = (destino / "data" / "hotel_perfil.json").exists()
    log(f"Operativa: {origen}")
    log(f"Prueba   : {destino} (existe={existe})")

    if existe and not (args.solo_datos or args.solo_codigo or args.rehacer):
        log("El sistema de prueba YA existe: no se toca nada. (Usa --solo-datos / --solo-codigo / --rehacer si lo necesitas)")
        crear_acceso_directo(destino)
        return 0

    detener_servidor_prueba(destino)

    if args.rehacer and destino.exists():
        viejo = destino.with_name(destino.name + "_viejo_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.move(str(destino), str(viejo))
        log(f"Prueba anterior guardada en {viejo}")
        existe = False

    destino.mkdir(parents=True, exist_ok=True)

    if args.solo_datos:
        foto_de_la_base_de_datos(origen, destino)
    elif args.solo_codigo:
        n = copiar_codigo(origen, destino)
        log(f"Código recopiado de la operativa: {n} archivos.")
    else:
        n = copiar_codigo(origen, destino)
        log(f"Código copiado: {n} archivos.")
        foto_de_la_base_de_datos(origen, destino)
        escribir_perfil(origen, destino)
        escribir_leeme(destino, origen)
        crear_acceso_directo(destino)
        avisar_supabase(destino, origen)
    log("LISTO.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
