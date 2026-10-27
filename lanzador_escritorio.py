# -*- coding: utf-8 -*-
"""
Lanzador Autónomo de Escritorio — Recepción Hotel Óvalo Mariátegui
Se ejecuta con doble clic desde el ícono del Escritorio ("Recepción Hotel Óvalo (Local)"):
- No usa ni consume nada de Antigravity / IA (corre 100% en tu Windows local).
- Si el motor local aún no está prendido, lo enciende en silencio y abre Chrome.
- Si ya estaba prendido, abre la ventana de Recepción inmediatamente.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

HOST = "127.0.0.1"
PORT = 8080
URL = f"http://{HOST}:{PORT}"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]


def servidor_activo() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def abrir_navegador_app() -> None:
    if "--no-browser" in sys.argv:
        return
    for chrome_exe in CHROME_PATHS:
        if os.path.exists(chrome_exe):
            try:
                subprocess.Popen(
                    [chrome_exe, f"--app={URL}"],
                    cwd=str(APP_DIR),
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


def _esperar_y_abrir_navegador() -> None:
    for _ in range(40):
        if servidor_activo():
            break
        time.sleep(0.2)
    abrir_navegador_app()


def main() -> None:
    if servidor_activo():
        abrir_navegador_app()
        return

    log_path = APP_DIR / "server_local.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file

    # Abrir el navegador apenas el puerto 8080 responda
    threading.Thread(target=_esperar_y_abrir_navegador, daemon=True).start()

    # Iniciar el servidor FastAPI directamente en este proceso silencioso
    import uvicorn
    from main import app

    uvicorn.run(app, host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
