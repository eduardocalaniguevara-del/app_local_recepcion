#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importación ÚNICA del historial 2026 de la pestaña 'Horario' (Eliana / eleiza / yolyi, lado a lado)
hacia la pestaña nativa 'import_Horario' (formato largo: una fila por empleado y día).
La pestaña 'Horario' original NO se modifica. Es idempotente: solo agrega lo que falta.

Uso:  python importar_horario_historico.py [--dry]
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gspread  # noqa: E402
from google.oauth2.service_account import Credentials  # noqa: E402

import config  # noqa: E402
from horario_logic import _norm_hora  # noqa: E402
from sync_engine import HORARIO_CABECERA  # noqa: E402

ANIO = 2026
# (empleado, col_fecha, col_e1, col_s1, col_e2, col_s2, col_estado, col_extras)  (índices base 0)
BLOQUES = [
    ("Eliana", 0, 1, 2, 3, 4, 5, 6),
    ("Eleiza", 7, 8, 9, 10, 11, 12, None),
    ("Yolyi", 13, 14, 15, 16, 17, 18, 19),
]
NUM = re.compile(r"^\d+(?:[.,]\d+)?(?:/\d+)?\s*[hH]?$")


def _celda(r, i):
    return str(r[i]).strip() if (i is not None and i < len(r) and r[i] is not None) else ""


def _hora_o_texto(x):
    """Devuelve (hh:mm, texto_sobrante). '8:5'->08:05, '16.3'->16:30, '16:50:00 a.m.'->16:50, 'Descanso'->('', 'Descanso')."""
    t = str(x or "").strip()
    if not t:
        return "", ""
    base = re.sub(r"\s*(a\.?\s?m\.?|p\.?\s?m\.?)\s*$", "", t, flags=re.I).strip()
    m = re.fullmatch(r"(\d{1,2})([:.])(\d{1,2})(?::\d{2})?\s*\.?", base)
    if m:
        mi = int(m.group(3))
        if m.group(2) == "." and len(m.group(3)) == 1:
            mi *= 10
        if int(m.group(1)) <= 23 and mi <= 59:
            return f"{int(m.group(1)):02d}:{mi:02d}", ""
    if re.fullmatch(r"\d{1,2}", base):
        return (f"{int(base):02d}:00", "") if int(base) <= 23 else ("", t)
    return "", t


def _clasificar(txt):
    """Devuelve (estado, extras, nota)."""
    t = txt.strip()
    if not t:
        return "", "", ""
    u = t.upper()
    if u == "F":
        return "FALTÓ", "", ""
    if u.startswith("FERIADO"):
        return "FERIADO", "", ""
    if NUM.match(t):
        return "", t.lower().replace("h", "").replace(",", ".").strip(), ""
    if u.startswith("FALT"):
        return "FALTÓ", "", ""
    if u == "DESCANSO":
        return "DESCANSO", "", ""
    return "", "", t


def main(dry=False, reparar=False):
    creds = Credentials.from_service_account_file(config.obtener_ruta_credenciales(), scopes=config.SCOPES)
    sh = gspread.authorize(creds).open_by_key(config.SPREADSHEET_ID)
    filas_hor = sh.worksheet("Horario").get("A4:T368", value_render_option="FORMATTED_VALUE")

    nuevas = []
    ahora = datetime.now().isoformat(timespec="seconds")
    for r in filas_hor:
        for emp, c_f, c_e1, c_s1, c_e2, c_s2, c_est, c_ext in BLOQUES:
            f = _celda(r, c_f)
            m = re.match(r"^(\d{1,2})-(\d{1,2})$", f)
            if not m:
                continue
            fecha = f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{ANIO}"
            marcas, sobrantes = [], []
            for c in (c_e1, c_s1, c_e2, c_s2):
                hh, extra_txt = _hora_o_texto(_celda(r, c))
                marcas.append(hh)
                if extra_txt:
                    sobrantes.append(extra_txt)
            est, ext, nota = _clasificar(_celda(r, c_est))
            for st in sobrantes:  # etiquetas escritas en casillas de hora (Descanso, Feriado, falto...)
                e_s, x_s, n_s = _clasificar(st)
                est = est or e_s
                ext = ext or x_s
                nota = (nota + " " + n_s).strip()
            if c_ext is not None:
                e2_, x2_, n2_ = _clasificar(_celda(r, c_ext))
                ext = ext or x2_
                est = est or e2_
                nota = (nota + " " + n2_).strip()
            if not any(marcas) and not est and not ext and not nota:
                continue
            nuevas.append([emp, fecha, *marcas, est, ext, 0, nota, ahora, "Importación Horario"])

    print(f"Filas con datos a importar: {len(nuevas)}")
    if dry:
        for x in nuevas[-8:]:
            print(x)
        return

    try:
        ws = sh.worksheet("import_Horario")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="import_Horario", rows=max(2000, len(nuevas) + 500), cols=len(HORARIO_CABECERA))
        ws.update(range_name="A1", values=[HORARIO_CABECERA], value_input_option="RAW")
        ws.freeze(rows=1)
    filas_ws = ws.get("A2:L8000") or []
    existentes = {(r[0].strip(), r[1].strip()) for r in filas_ws if len(r) >= 2}
    if reparar:
        # Corrige SOLO filas que siguen siendo de la importación (equipo 'Importación Horario'; nadie las editó)
        mapa = {(x[0], x[1]): x for x in nuevas}
        cambios = []
        for i, r in enumerate(filas_ws, start=2):
            if len(r) < 12 or r[11].strip() != "Importación Horario":
                continue
            nuevo = mapa.get((r[0].strip(), r[1].strip()))
            if not nuevo:
                continue
            fila_n = [str(v) for v in nuevo]
            actual = [str(v) for v in (r + [""] * 12)[:12]]
            if fila_n[:10] != actual[:10]:
                cambios.append({"range": f"A{i}:L{i}", "values": [nuevo]})
        print(f"Filas a reparar: {len(cambios)}")
        for k in range(0, len(cambios), 200):
            ws.batch_update(cambios[k:k + 200], value_input_option="RAW")
        return
    if not por_agregar:
        print("Nada nuevo: import_Horario ya estaba al día.")
        return
    inicio = len(existentes) + 2
    fin = inicio + len(por_agregar) - 1
    if fin > ws.row_count:
        ws.add_rows(fin - ws.row_count + 200)
    ws.update(range_name=f"A{inicio}:L{fin}", values=por_agregar, value_input_option="RAW")
    try:
        ws_a = sh.worksheet("import_Horario_Audit")
    except gspread.WorksheetNotFound:
        from sync_engine import HORARIO_AUDIT_CABECERA
        ws_a = sh.add_worksheet(title="import_Horario_Audit", rows=2000, cols=len(HORARIO_AUDIT_CABECERA))
        ws_a.update(range_name="A1", values=[HORARIO_AUDIT_CABECERA], value_input_option="RAW")
        ws_a.freeze(rows=1)
    print(f"Importadas {len(por_agregar)} filas en import_Horario (filas {inicio}-{fin}).")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv, reparar="--reparar" in sys.argv)
