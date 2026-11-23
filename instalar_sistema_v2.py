#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INSTALADOR OFICIAL DESDE CERO (Git + Supabase Directo — CERO ZIPs manuales):
- Clona o actualiza C:\Ovalo_Sistema_v2 directamente con `git` desde GitHub (`main`).
- Inicializa `02_DB_sqlite_local\ovalo.db` y descarga el 100% de las tablas desde Supabase.
- Configura `.env` sin BOM (`ROL_NODO=NIVEL_3_TERCIARIA` o `NIVEL_2_MADRE_HOTEL`).
- Crea el acceso directo en el Escritorio y levanta el servidor en `:8080`.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEST = Path(r"C:\Ovalo_Sistema_v2")
REPO_URL = "https://github.com/eduardocalaniguevara-del/app_local_recepcion.git"


def instalar(modo: str = "TERCIARIA") -> None:
    print("=" * 74)
    print(f"  INSTALANDO HOSTAL OVALO SISTEMA V2 (GIT + SUPABASE DIRECTO — {modo})")
    print("=" * 74)

    git_exe = shutil.which("git")
    if not git_exe:
        cands = [
            r"C:\Program Files\Git\cmd\git.exe",
            os.path.expandvars(r"%LocalAppData%\Programs\Git\cmd\git.exe"),
            r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe",
        ]
        import glob as _glob
        cands.extend(_glob.glob(os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Packages\Git.MinGit*\cmd\git.exe")))
        cands.extend(_glob.glob(os.path.expandvars(r"%LocalAppData%\Microsoft\WinGet\Links\git.exe")))
        for c in cands:
            if os.path.exists(c):
                git_exe = c
                break
    if not git_exe:
        raise RuntimeError("Git no está instalado en el PATH. Instálalo con: winget install --id Git.MinGit -e")

    DEST.mkdir(parents=True, exist_ok=True)
    print("[1/4] Conectando C:\\Ovalo_Sistema_v2 directo con GitHub (rama main)...")
    if not (DEST / ".git").exists():
        subprocess.run([git_exe, "init", "-b", "main"], cwd=str(DEST), check=True)
        subprocess.run([git_exe, "remote", "add", "origin", REPO_URL], cwd=str(DEST), check=True)
    subprocess.run([git_exe, "fetch", "origin", "main"], cwd=str(DEST), check=True)
    subprocess.run([git_exe, "reset", "--hard", "origin/main"], cwd=str(DEST), check=True)

    (DEST / "01_PRIV_borradores").mkdir(parents=True, exist_ok=True)
    (DEST / "02_DB_sqlite_local").mkdir(parents=True, exist_ok=True)

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
    (DEST / ".env").write_bytes(env_txt.encode("utf-8"))

    print("[3/4] Inicializando SQLite y descargando 100% de los datos desde Supabase...")
    subprocess.run([sys.executable, str(DEST / "03_GIT_app_motor" / "db.py")], cwd=str(DEST), check=True)
    subprocess.run([sys.executable, str(DEST / "03_GIT_app_motor" / "sync_nube.py"), "--sync"], cwd=str(DEST), check=True)

    print("[4/4] Creando ícono en el Escritorio y encendiendo el sistema en :8080...")
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
    print("\n✅ LISTO: C:\\Ovalo_Sistema_v2 conectado por Git + Supabase en http://127.0.0.1:8080")


if __name__ == "__main__":
    modo_arg = "MADRE_L2" if ("--madre" in sys.argv or "--l2" in sys.argv) else "TERCIARIA"
    instalar(modo_arg)
