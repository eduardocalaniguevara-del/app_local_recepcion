#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Auto-Actualización silenciosa vía Git para los 3 Hoteles.
Revisa periódicamente si hay cambios en GitHub (origin/main), descarga el código
nuevo sin tocar la carpeta data/ (ni hotel_perfil.json ni ovalo_local.db) y
reinicia el servidor local automáticamente.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any

from config import BASE_APP_DIR

_GIT_CANDIDATES = [
    r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]


def obtener_git_exe() -> str:
    en_path = shutil.which("git")
    if en_path:
        return en_path
    for p in _GIT_CANDIDATES:
        if os.path.exists(p):
            return p
    return "git"


def _run_git(args: list, timeout: int = 15) -> subprocess.CompletedProcess:
    git_bin = obtener_git_exe()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        [git_bin] + args,
        cwd=str(BASE_APP_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
    )


def obtener_version_actual() -> str:
    git_dir = BASE_APP_DIR / ".git"
    if not git_dir.exists():
        # Fallback: mtime de PanelDisponibilidad.html + domain_logic.py
        try:
            p1 = (BASE_APP_DIR / "domain_logic.py").stat().st_mtime
            p2 = (BASE_APP_DIR / "static" / "PanelDisponibilidad.html").stat().st_mtime
            return f"local-{int(max(p1, p2))}"
        except Exception:
            return "local-0"
    try:
        r = _run_git(["rev-parse", "--short", "HEAD"], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return "git-unknown"


def verificar_y_aplicar_actualizacion() -> Dict[str, Any]:
    """
    Comprueba si origin/main tiene un commit más reciente que HEAD.
    Si hay cambios, ejecuta git reset --hard origin/main (preservando data/).
    """
    git_dir = BASE_APP_DIR / ".git"
    if not git_dir.exists():
        return {"actualizado": False, "motivo": "sin_repo_git", "version": obtener_version_actual()}

    try:
        # 1. Fetch silencioso desde origin
        f = _run_git(["fetch", "origin", "main", "--quiet"], timeout=15)
        if f.returncode != 0:
            return {"actualizado": False, "motivo": "fetch_error", "detalle": f.stderr.strip()}

        # 2. Comparar HEAD vs origin/main
        loc = _run_git(["rev-parse", "HEAD"], timeout=5)
        rem = _run_git(["rev-parse", "origin/main"], timeout=5)
        if loc.returncode != 0 or rem.returncode != 0:
            return {"actualizado": False, "motivo": "rev_parse_error"}

        sha_local = loc.stdout.strip()
        sha_remoto = rem.stdout.strip()
        if not sha_remoto or sha_local == sha_remoto:
            return {"actualizado": False, "motivo": "al_dia", "version": sha_local[:7]}

        # 3. Aplicar actualización limpia (data/ y credentials.json están en .gitignore)
        rst = _run_git(["reset", "--hard", "origin/main"], timeout=15)
        if rst.returncode != 0:
            return {"actualizado": False, "motivo": "reset_error", "detalle": rst.stderr.strip()}

        return {
            "actualizado": True,
            "version_anterior": sha_local[:7],
            "version_nueva": sha_remoto[:7],
        }
    except Exception as exc:
        return {"actualizado": False, "motivo": "excepcion", "detalle": str(exc)}


def reiniciar_servidor_en_caliente() -> None:
    """
    Lanza un subproceso independiente que espera 1s, reemplaza el proceso actual
    y levanta main.py con el código nuevo.
    """
    py_exe = sys.executable
    main_py = str(BASE_APP_DIR / "main.py")
    pid_actual = os.getpid()
    cmd_relaunch = (
        f"import time, subprocess, os; "
        f"time.sleep(1.0); "
        f"subprocess.run(['taskkill', '/F', '/PID', '{pid_actual}'], capture_output=True); "
        f"time.sleep(0.8); "
        f"subprocess.Popen([r'{py_exe}', r'{main_py}'], cwd=r'{BASE_APP_DIR}', "
        f"creationflags=0x08000000)"
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen([py_exe, "-c", cmd_relaunch], cwd=str(BASE_APP_DIR), creationflags=creationflags)


def iniciar_hilo_auto_actualizacion(intervalo_segundos: int = 45) -> None:
    def _worker():
        while True:
            time.sleep(intervalo_segundos)
            res = verificar_y_aplicar_actualizacion()
            if res.get("actualizado"):
                print(
                    f"[AUTO-UPDATER] Nueva versión detectada ({res.get('version_anterior')} -> "
                    f"{res.get('version_nueva')}). Reiniciando servidor..."
                )
                reiniciar_servidor_en_caliente()
                break

    t = threading.Thread(target=_worker, name="GitAutoUpdater", daemon=True)
    t.start()
