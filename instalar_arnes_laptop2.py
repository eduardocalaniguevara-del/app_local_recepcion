# -*- coding: utf-8 -*-
"""Instalador del Arnés² Recepción en Laptop 2 (LAPTOP-JAPIGJT2).

Uso:  python instalar_arnes_laptop2.py [--verificar] [--auto]
- Instala .agent/harness en C:\\Recepcion_Ovalo_Local\\.agent\\harness,
  C:\\Hostal_Sistema\\leemeantrigravityrecepcion\\arnes_recepcion y
  C:\\Users\\ovalo mariategui\\Downloads\\leemeantrigravityrecepcion\\arnes_recepcion.
- Copia leemeantrigravityrecepcion.md, instalar_arnes_laptop2.py, Abrir_Visor_SQLite_y_Arnes_L2.bat
  y scheduler_recepcion_24x7.py a Descargas de Laptop 2.
- Actualiza C:\\ovalo_mariategui_prueba con los módulos del puente de pruebas conectado (8082 <-> 8082)
  sin tocar data/ovalo_sistema.db y asegurando "conectar_pruebas_supabase": true en data/hotel_perfil.json.
- Verifica hermeticidad (cero datos personales del Grupo 1) y sintaxis de sondas (node -c).
- Ejecuta operational_health_probe.js con ARNES_PUBLISH=1 y scheduler_recepcion_24x7.py --once.
GR-08: este instalador modifica la configuración del Arnés²; en modo interactivo si ya existe una instalación,
pide el código de autorización de administrador (SHA-256 / ARNES_AUTH_HASH).
Las operaciones diarias de la WebApp NO pasan por este candado.
"""
import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

AUTH_HASH = os.environ.get("ARNES_AUTH_HASH", "24fe5b2863951e8d97cd7e19f2a5fed2534686189241b58951f0e7e2e8d666d7").lower()
HERE = Path(__file__).resolve().parent
SRC = HERE / "arnes_recepcion"
DEST_MAIN = Path(r"C:\Hostal_Sistema\leemeantrigravityrecepcion\arnes_recepcion")
DEST_MIRROR = Path.home() / "Downloads" / "leemeantrigravityrecepcion" / "arnes_recepcion"
DEST_L2_DOWNLOADS = Path(r"C:\Users\ovalo mariategui\Downloads\leemeantrigravityrecepcion")
DEST_L2_OPERATIVA = Path(r"C:\Recepcion_Ovalo_Local")
DEST_PRUEBA = Path(r"C:\ovalo_mariategui_prueba")

PROHIBIDOS = re.compile(
    "|".join([
        "wsp_wspdocs" + "_russ",
        "7411" + "8293",
        "10741" + "182934",
        "credenciales" + "_dni",
        "emision_rus" + "_rhe_dni",
        "OVALO-ARNES" + "-2026",
    ]),
    re.I,
)


def es_interactivo() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty()) and "--auto" not in sys.argv
    except Exception:
        return False


def pedir_codigo() -> bool:
    code = os.environ.get("ARNES_AUTH_CODE")
    if not code:
        if not es_interactivo():
            return True
        code = getpass.getpass("Código de autorización GR-08: ")
    return hashlib.sha256(code.encode("utf-8")).hexdigest() == AUTH_HASH


def hermetico(root: Path):
    fugas = []
    for f in root.rglob("*"):
        if f.is_file():
            try:
                if PROHIBIDOS.search(f.read_text(encoding="utf-8", errors="ignore")):
                    fugas.append(str(f))
            except OSError:
                pass
    return fugas


def copiar_arbol(src_dir: Path, dst_dir: Path):
    try:
        if dst_dir.exists() and dst_dir.resolve() == src_dir.resolve():
            return
    except Exception:
        pass
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)


def copiar_archivo_seguro(src_file: Path, dst_file: Path):
    if not src_file.is_file():
        return
    try:
        if dst_file.exists() and dst_file.resolve() == src_file.resolve():
            return
    except Exception:
        pass
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dst_file)


def sincronizar_descargas_y_operativa():
    # a) Instalar .agent/harness tanto en operativa local como en Descargas
    harness_src = SRC / ".agent" / "harness"
    if harness_src.is_dir():
        copiar_arbol(harness_src, HERE / ".agent" / "harness")
        if DEST_L2_OPERATIVA.exists():
            copiar_arbol(harness_src, DEST_L2_OPERATIVA / ".agent" / "harness")
        if Path(r"C:\Users\ovalo mariategui").exists():
            copiar_arbol(SRC, DEST_L2_DOWNLOADS / "arnes_recepcion")

    # b) Copiar archivos clave hacia Descargas de Laptop 2 (y del usuario actual)
    archivos_clave = [
        "leemeantrigravityrecepcion.md",
        "instalar_arnes_laptop2.py",
        "Abrir_Visor_SQLite_y_Arnes_L2.bat",
        "scheduler_recepcion_24x7.py",
    ]
    carpetas_descargas = [Path.home() / "Downloads" / "leemeantrigravityrecepcion"]
    if Path(r"C:\Users\ovalo mariategui\Downloads").exists():
        carpetas_descargas.append(DEST_L2_DOWNLOADS)

    for carpeta_dl in carpetas_descargas:
        carpeta_dl.mkdir(parents=True, exist_ok=True)
        for nombre in archivos_clave:
            cand = HERE / nombre
            if cand.is_file():
                copiar_archivo_seguro(cand, carpeta_dl / nombre)
        leeme_src = HERE / "leemeantrigravityrecepcion.md"
        if leeme_src.is_file():
            copiar_archivo_seguro(leeme_src, carpeta_dl.parent / "leemeantrigravityrecepcion.md")


def actualizar_entorno_prueba():
    # c) Actualizar C:\ovalo_mariategui_prueba sin borrar data/ovalo_sistema.db
    if not DEST_PRUEBA.exists():
        crear_py = HERE / "crear_entorno_prueba.py"
        if crear_py.is_file() and (HERE / "data").exists() and HERE.resolve() != DEST_PRUEBA.resolve():
            subprocess.run([sys.executable, str(crear_py)], check=False)

    if not DEST_PRUEBA.exists():
        return

    archivos_prueba = [
        "config.py",
        "supabase_sync.py",
        "main.py",
        "promover_a_operativa.py",
        "crear_entorno_prueba.py",
        "scheduler_recepcion_24x7.py",
        "instalar_arnes_laptop2.py",
        "Abrir_Visor_SQLite_y_Arnes_L2.bat",
        "leemeantrigravityrecepcion.md",
    ]
    for nombre in archivos_prueba:
        src_f = HERE / nombre
        if src_f.is_file():
            copiar_archivo_seguro(src_f, DEST_PRUEBA / nombre)

    if (SRC / ".agent" / "harness").is_dir():
        copiar_arbol(SRC, DEST_PRUEBA / "arnes_recepcion")
        copiar_arbol(SRC / ".agent" / "harness", DEST_PRUEBA / ".agent" / "harness")

    perfil_path = DEST_PRUEBA / "data" / "hotel_perfil.json"
    perfil_path.parent.mkdir(parents=True, exist_ok=True)
    perfil = {
        "nombre_hotel": "Ovalo Mariategui PRUEBA",
        "spreadsheet_id": "SIN_HOJA_MODO_PRUEBA",
        "col_tipo_adicional": "BA",
        "formula_snacks_en_s2": False,
        "app_port": 8082,
        "modo_prueba": True,
        "conectar_pruebas_supabase": True,
        "carpeta_operativa": str(DEST_L2_OPERATIVA if DEST_L2_OPERATIVA.exists() else HERE),
    }
    if perfil_path.exists():
        try:
            existente = json.loads(perfil_path.read_text(encoding="utf-8-sig"))
            if isinstance(existente, dict):
                perfil.update(existente)
        except Exception:
            pass
    perfil["modo_prueba"] = True
    perfil["app_port"] = 8082
    perfil["conectar_pruebas_supabase"] = True
    perfil_path.write_text(json.dumps(perfil, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Actualizado entorno de prueba conectado en", DEST_PRUEBA)


def asegurar_demonio_24x7(scheduler_py: Path):
    if not scheduler_py.is_file():
        return
    # Ejecutar una pasada inmediata (--once)
    try:
        r = subprocess.run(
            [sys.executable, str(scheduler_py), "--once"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if r.stdout:
            print(r.stdout[-800:])
    except Exception as exc:
        print("Aviso en scheduler --once:", exc)

    # En Laptop 2, asegurar proceso demonio 24/7 en segundo plano
    host = socket.gethostname().upper()
    if host == "LAPTOP-JAPIGJT2" or Path(r"C:\Users\ovalo mariategui").exists():
        try:
            ps_check = (
                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*scheduler_recepcion_24x7.py*--daemon*' } "
                "| Select-Object -ExpandProperty ProcessId"
            )
            chk = subprocess.run(["powershell", "-NoProfile", "-Command", ps_check], capture_output=True, text=True, timeout=10)
            if not (chk.stdout or "").strip():
                flags = 0x08000000 | 0x00000008 if os.name == "nt" else 0
                subprocess.Popen(
                    [sys.executable, str(scheduler_py), "--daemon"],
                    cwd=str(scheduler_py.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                print("Demonio 24/7 (scheduler_recepcion_24x7.py --daemon) iniciado en Laptop 2.")
        except Exception as exc:
            print("Aviso al iniciar demonio 24/7:", exc)


def obtener_node_exe():
    w = shutil.which("node")
    if w:
        return w
    for cand in (r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.exists(cand):
            return cand
    return None


def main():
    if "terrazas" in str(HERE).lower():
        return 0
    if not SRC.is_dir():
        print("ERROR: no se encuentra", SRC)
        return 1
    verificar = "--verificar" in sys.argv
    if not verificar:
        if (DEST_MAIN / ".agent" / "harness" / "manifest.json").exists() and DEST_MAIN.resolve() != SRC.resolve():
            if not pedir_codigo():
                print("GR-08: código incorrecto. Instalación cancelada.")
                return 2
        fugas = hermetico(SRC)
        if fugas:
            print("ABORTADO: datos personales detectados en el paquete:", *fugas, sep="\n  ")
            return 3
        for d in (DEST_MAIN, DEST_MIRROR):
            copiar_arbol(SRC, d)
            print("Instalado en", d)
        sincronizar_descargas_y_operativa()
        actualizar_entorno_prueba()

    base = DEST_MAIN if DEST_MAIN.exists() else SRC
    probes = base / ".agent" / "harness" / "probes"
    ok = True
    node_exe = obtener_node_exe()
    es_l2 = socket.gethostname().upper() == "LAPTOP-JAPIGJT2" or Path(r"C:\Users\ovalo mariategui").exists()
    if node_exe:
        for js in sorted(probes.glob("*.js")):
            r = subprocess.run([node_exe, "-c", str(js)], capture_output=True, text=True)
            print(("OK   " if r.returncode == 0 else "FALLA"), js.name)
            ok &= r.returncode == 0

        env = dict(os.environ)
        if es_l2 or "--publish" in sys.argv:
            env["ARNES_PUBLISH"] = "1"
        r = subprocess.run(
            [node_exe, str(probes / "operational_health_probe.js")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        print(r.stdout[-1500:] if r.stdout else r.stderr)

    scheduler_py = HERE / "scheduler_recepcion_24x7.py"
    if not scheduler_py.is_file() and (DEST_L2_OPERATIVA / "scheduler_recepcion_24x7.py").is_file():
        scheduler_py = DEST_L2_OPERATIVA / "scheduler_recepcion_24x7.py"
    asegurar_demonio_24x7(scheduler_py)

    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
