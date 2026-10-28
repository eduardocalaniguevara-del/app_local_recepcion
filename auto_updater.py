#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Auto-Actualización silenciosa vía Git para los 3 Hoteles.
Revisa periódicamente si hay cambios en GitHub (origin/main), descarga el código
nuevo sin tocar la carpeta data/ (ni hotel_perfil.json ni ovalo_local.db) y
recarga los módulos e interfaces en memoria sin tumbar el servidor ni abrir ventanas CMD.
"""

import importlib
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


def recargar_en_memoria_sin_cortes() -> None:
    """
    Recarga domain_logic directamente en memoria y actualiza el timestamp
    de PanelDisponibilidad.html para que el navegador refresque la vista sin
    matar el servidor ni interrumpir el hilo de sincronización de sync_engine.
    """
    try:
        if "domain_logic" in sys.modules:
            importlib.reload(sys.modules["domain_logic"])
    except Exception as exc:
        print(f"[AUTO-UPDATER] Aviso recargando domain_logic en memoria: {exc}")

    try:
        panel_html = BASE_APP_DIR / "static" / "PanelDisponibilidad.html"
        if panel_html.exists():
            now_ts = time.time()
            os.utime(panel_html, (now_ts, now_ts))
    except Exception:
        pass


def verificar_y_aplicar_actualizacion() -> Dict[str, Any]:
    """
    Comprueba si origin/main tiene un commit más reciente que HEAD.
    Si hay cambios, ejecuta git reset --hard origin/main (preservando data/)
    y recarga en memoria sin desconectar el servidor.
    """
    git_dir = BASE_APP_DIR / ".git"
    if not git_dir.exists():
        return {"actualizado": False, "motivo": "sin_repo_git", "version": obtener_version_actual()}

    try:
        f = _run_git(["fetch", "origin", "main", "--quiet"], timeout=15)
        if f.returncode != 0:
            return {"actualizado": False, "motivo": "fetch_error", "detalle": f.stderr.strip()}

        loc = _run_git(["rev-parse", "HEAD"], timeout=5)
        rem = _run_git(["rev-parse", "origin/main"], timeout=5)
        if loc.returncode != 0 or rem.returncode != 0:
            return {"actualizado": False, "motivo": "rev_parse_error"}

        sha_local = loc.stdout.strip()
        sha_remoto = rem.stdout.strip()
        if not sha_remoto or sha_local == sha_remoto:
            return {"actualizado": False, "motivo": "al_dia", "version": sha_local[:7]}

        rst = _run_git(["reset", "--hard", "origin/main"], timeout=15)
        if rst.returncode != 0:
            return {"actualizado": False, "motivo": "reset_error", "detalle": rst.stderr.strip()}

        recargar_en_memoria_sin_cortes()

        return {
            "actualizado": True,
            "version_anterior": sha_local[:7],
            "version_nueva": sha_remoto[:7],
        }
    except Exception as exc:
        return {"actualizado": False, "motivo": "excepcion", "detalle": str(exc)}


def reiniciar_servidor_en_caliente() -> None:
    """Alias compatible: aplica recarga en memoria sin tumbar el puerto 8080."""
    recargar_en_memoria_sin_cortes()


def iniciar_hilo_auto_actualizacion(intervalo_segundos: int = 30) -> None:
    def _worker():
        while True:
            time.sleep(intervalo_segundos)
            res = verificar_y_aplicar_actualizacion()
            if res.get("actualizado"):
                print(
                    f"[AUTO-UPDATER] Actualizado en caliente ({res.get('version_anterior')} -> "
                    f"{res.get('version_nueva')}) sin desconexión."
                )

    t = threading.Thread(target=_worker, name="GitAutoUpdater", daemon=True)
    t.start()
