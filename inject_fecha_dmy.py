# -*- coding: utf-8 -*-
"""
Inserta (de forma idempotente) el script de fechas DD/MM/AAAA dentro de las vistas de apps_script_ovalo/
para que Apps Script y la App Local muestren exactamente el mismo formato.
Fuente única del código: app_local_recepcion/static/js/fecha_dmy.js
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (Path(__file__).resolve().parent / "static" / "js" / "fecha_dmy.js").read_text(encoding="utf-8")
VISTAS = ["PanelDisponibilidad.html", "CheckInRapido.html", "PagosPosteriores.html", "VentaTienda.html", "GastosRegistro.html"]
BEGIN, END = "<!-- FECHA_DMY_BEGIN -->", "<!-- FECHA_DMY_END -->"
BLOQUE = f"{BEGIN}\n<script>\n{JS}\n</script>\n{END}\n"

for nombre in VISTAS:
    p = ROOT / "apps_script_ovalo" / nombre
    html = p.read_text(encoding="utf-8")
    html = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", html, flags=re.S)
    if "</body>" not in html:
        print(f"[SKIP] {nombre}: sin </body>")
        continue
    html = html.replace("</body>", BLOQUE + "</body>", 1)
    p.write_text(html, encoding="utf-8")
    print(f"[OK] {nombre}")
