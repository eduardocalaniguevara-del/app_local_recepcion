# -*- coding: utf-8 -*-
"""
lanzador_escritorio.py — Lanzador Silencioso (0 ventanas CMD) con Google Chrome
Se ejecuta desde el ícono del Escritorio ("Hostal Ovalo - Sistema v2") vía pythonw.exe:
- Cero ventana negra de CMD.
- Si el servidor local de C:\\Ovalo_Sistema_v2 no está encendido, lo levanta en silencio.
- Abre directamente la ventana limpia de Google Chrome (--app=http://127.0.0.1:PUERTO).
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = Path(__file__).resolve().parent
MOTOR_DIR = BASE_DIR / "03_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

from config import APP_HOST, APP_PORT

LOCAL_HOST = "127.0.0.1"
PORT = APP_PORT
URL = f"http://{LOCAL_HOST}:{PORT}"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]


def servidor_activo() -> bool:
    try:
        with socket.create_connection((LOCAL_HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def abrir_google_chrome_app() -> None:
    if "--no-browser" in sys.argv:
        return
    for chrome_exe in CHROME_PATHS:
        if os.path.exists(chrome_exe):
            try:
                subprocess.Popen(
                    [chrome_exe, f"--app={URL}"],
                    cwd=str(BASE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                return
            except Exception:
                pass
    try:
        os.startfile(URL)
    except Exception:
        webbrowser.open(URL)


def _esperar_y_abrir_chrome() -> None:
    for _ in range(40):
        if servidor_activo():
            break
        time.sleep(0.2)
    abrir_google_chrome_app()


def _auto_git_pull_silencioso() -> None:
    if not (BASE_DIR / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except Exception:
        pass


def main() -> None:
    _auto_git_pull_silencioso()
    if servidor_activo():
        abrir_google_chrome_app()
        return

    log_path = BASE_DIR / "01_PRIV_borradores" / "server_v2_local.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file

    threading.Thread(target=_esperar_y_abrir_chrome, daemon=True).start()

    import uvicorn
    from main import app

    uvicorn.run(app, host=APP_HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
