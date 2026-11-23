#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INSTALADOR 1-CLIC: Hostal El Óvalo - Sistema v2 (Sin archivos .bat bloqueados por Gmail/WhatsApp)
Funciona en cualquier PC con Python + Internet (con o sin Git instalado).
"""
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

DEST = Path(r"C:\Ovalo_Sistema_v2")
ZIP_URL = "https://github.com/eduardocalaniguevara-del/app_local_recepcion/archive/refs/heads/main.zip"
REPO_URL = "https://github.com/eduardocalaniguevara-del/app_local_recepcion.git"

def instalar(modo="TERCIARIA"):
    print("=" * 74)
    print(f"  INSTALANDO HOSTAL OVALO SISTEMA V2 EN {DEST} (MODO: {modo})")
    print("=" * 74)
    DEST.mkdir(parents=True, exist_ok=True)

    # 1. Descargar código desde GitHub (vía git o vía HTTPS directo si no hay git)
    has_git = shutil.which("git") is not None
    if has_git:
        print("[1/4] Descargando código desde GitHub con Git...")
        if not (DEST / ".git").exists():
            subprocess.run(["git", "init", "-b", "main"], cwd=str(DEST))
            subprocess.run(["git", "remote", "add", "origin", REPO_URL], cwd=str(DEST))
        subprocess.run(["git", "fetch", "origin", "main"], cwd=str(DEST))
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(DEST))
    else:
        print("[1/4] Descargando paquete directo desde GitHub HTTPS (sin requerir Git)...")
        data = urllib.request.urlopen(ZIP_URL, timeout=30).read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            prefix = zf.namelist()[0].split("/")[0] + "/"
            for member in zf.namelist():
                rel = member[len(prefix):] if member.startswith(prefix) else member
                if not rel:
                    continue
                target = DEST / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))

    (DEST / "01_PRIV_borradores").mkdir(parents=True, exist_ok=True)
    (DEST / "02_DB_sqlite_local").mkdir(parents=True, exist_ok=True)

    # 2. Escribir .env según el modo elegido
    print(f"[2/4] Configurando archivo .env ({modo})...")
    if modo == "MADRE_L2":
        env_txt = (
            "ROL_NODO=NIVEL_2_MADRE_HOTEL\n"
            "EQUIPO_ID=LAPTOP_2_RECEPCION\n"
            "PUERTO_WEB=8080\n"
            "NOMBRE_HOTEL=Hotel El Óvalo Mariátegui\n"
            "CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com\n"
            "ACTIVAR_MOTOR_24X7=true\n"
            "TIENE_QR_WSP_FISICO=true\n"
        )
    else:
        hostname = os.environ.get("COMPUTERNAME", "LAPTOP_TERCIARIA")
        env_txt = (
            "ROL_NODO=NIVEL_3_TERCIARIA\n"
            f"EQUIPO_ID={hostname}\n"
            "PUERTO_WEB=8080\n"
            "NOMBRE_HOTEL=Hotel El Óvalo Mariátegui\n"
            "CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com\n"
            "ACTIVAR_MOTOR_24X7=false\n"
            "TIENE_QR_WSP_FISICO=false\n"
        )
    (DEST / ".env").write_text(env_txt, encoding="utf-8")

    # 3. Inicializar SQLite y descargar datos desde Supabase
    print("[3/4] Conectando SQLite local con Supabase y descargando datos del hotel...")
    subprocess.run([sys.executable, str(DEST / "03_GIT_app_motor" / "db.py")], cwd=str(DEST))
    subprocess.run([sys.executable, str(DEST / "03_GIT_app_motor" / "sync_nube.py"), "--test"], cwd=str(DEST))

    # 4. Crear acceso directo en el Escritorio y abrir el sistema en :8080
    print("[4/4] Creando ícono en el Escritorio y abriendo el sistema en :8080...")
    ps_cmd = (
        "$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; "
        "if (-not $pyw) { $pyw = 'python.exe' }; "
        "$chromeIcon = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe,0'; "
        "if (-not (Test-Path 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')) { $chromeIcon = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe,0' }; "
        "$WshShell = New-Object -ComObject WScript.Shell; "
        "$d = [Environment]::GetFolderPath('Desktop'); "
        "if ($d -and (Test-Path $d)) { "
        "$sc = $WshShell.CreateShortcut((Join-Path $d 'Hostal Ovalo - Sistema v2.lnk')); "
        "$sc.TargetPath = $pyw; "
        "$sc.Arguments = '\"C:\\Ovalo_Sistema_v2\\lanzador_escritorio.py\"'; "
        "$sc.WorkingDirectory = 'C:\\Ovalo_Sistema_v2'; "
        "$sc.IconLocation = $chromeIcon; "
        "$sc.Save() }; "
        "Start-Process -FilePath $pyw -ArgumentList '\"C:\\Ovalo_Sistema_v2\\lanzador_escritorio.py\"' -WorkingDirectory 'C:\\Ovalo_Sistema_v2'"
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd])
    print("\n✅ LISTO: Sistema instalado en C:\\Ovalo_Sistema_v2 y abriéndose en http://127.0.0.1:8080")

if __name__ == "__main__":
    modo_arg = "MADRE_L2" if ("--madre" in sys.argv or "--l2" in sys.argv) else "TERCIARIA"
    instalar(modo_arg)
