"""
main.py — Interruptor general del servidor web de C:\\Ovalo_Sistema_v2.
En Fase 1 expone la vista base y el chequeo /api/salud; en Fase 3 suma las rutas operativas de Recepción.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from app.config import PUERTO_WEB, WEB_DIR, ROL_NODO
from app.salud import reporte_salud


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
