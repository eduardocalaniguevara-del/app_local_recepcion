"""
main.py — Interruptor general del servidor web de C:\\Ovalo_Sistema_v2.
Funciona igual en Laptop 1, Laptop 2 o una futura Laptop 3.
"""
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "04_GIT_app_motor"))
from config import PUERTO_WEB, WEB_DIR, ROL_NODO
from salud import reporte_salud


class OvaloV2Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/salud"):
            payload = json.dumps(reporte_salud(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        return super().do_GET()


def iniciar_servidor():
    server = ThreadingHTTPServer(("0.0.0.0", PUERTO_WEB), OvaloV2Handler)
    print(f"[Ovalo_Sistema_v2] Servidor listo en http://127.0.0.1:{PUERTO_WEB} (Nodo: {ROL_NODO})")
    server.serve_forever()


if __name__ == "__main__":
    iniciar_servidor()
