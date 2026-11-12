#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PROMOVER A OPERATIVA — pasa a la operativa SOLO lo que cambiaste y probaste en el sistema de PRUEBA.

Seguridad:
  - Solo corre desde la carpeta de PRUEBA (data/hotel_perfil.json con modo_prueba=true).
  - Compara 3 versiones por archivo: BASE (cuando se creó la prueba), PRUEBA (tu edición) y OPERATIVA (hoy).
      * Cambió solo en PRUEBA            -> se promueve.
      * Cambió en PRUEBA y en OPERATIVA  -> CONFLICTO: NO se toca (hay que revisarlo a mano).
  - Antes de copiar: compila los .py, guarda RESPALDO de los originales y crea una etiqueta git de retorno.
  - Nunca copia data/, bases .db, logs ni hotel_perfil.json.
  - Por defecto SOLO MUESTRA EL PLAN. Para aplicar: --aplicar (pide escribir SI, o --si para no preguntar).

Uso:
  python promover_a_operativa.py                      -> muestra el plan
  python promover_a_operativa.py --aplicar            -> aplica (pregunta)
  python promover_a_operativa.py --aplicar --si -m "Mensaje del cambio"
  python promover_a_operativa.py --aplicar --sin-publicar   (solo copia a la operativa de esta laptop, sin GitHub)
"""

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
EXCLUIR_DIRS = {".git", "__pycache__", "data", "backups", "respaldos", "node_modules", ".venv", "scratch"}
EXCLUIR_EXT = {".db", ".db-wal", ".db-shm", ".db-journal", ".log", ".pyc", ".bak", ".flag"}
EXCLUIR_NOMBRES = {"hotel_perfil.json", "leeme_prueba.txt", "prueba.ico"}
RESPALDOS = Path(r"C:\Hostal_Sistema\respaldos_promocion")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def sha(p: Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def archivos_prueba():
    for dirpath, dirnames, filenames in os.walk(AQUI):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUIR_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXCLUIR_EXT or fn.lower() in EXCLUIR_NOMBRES:
                continue
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--si", action="store_true")
    ap.add_argument("--sin-publicar", action="store_true")
    ap.add_argument("-m", "--mensaje", default="")
    ap.add_argument("--solo", nargs="*", default=[], help="limitar a estos archivos (ruta relativa)")
    args = ap.parse_args()

    try:
        perfil = json.loads((AQUI / "data" / "hotel_perfil.json").read_text(encoding="utf-8-sig"))
    except Exception:
        perfil = {}
    if not perfil.get("modo_prueba") or not perfil.get("carpeta_operativa"):
        print("ERROR: este script solo se ejecuta desde el sistema de PRUEBA.")
        return 2
    oper = Path(perfil["carpeta_operativa"])
    if not oper.exists():
        print(f"ERROR: no existe la carpeta operativa {oper}")
        return 2
    try:
        base = json.loads((AQUI / "data" / "base_manifest.json").read_text(encoding="utf-8"))["archivos"]
    except Exception:
        print("ERROR: falta data/base_manifest.json (recrea la prueba con Crear_Entorno_Prueba.bat --rehacer).")
        return 2

    promover, conflictos, iguales = [], [], 0
    for p in archivos_prueba():
        rel = p.relative_to(AQUI).as_posix()
        if args.solo and rel not in args.solo:
            continue
        sp, sb, so = sha(p), base.get(rel), sha(oper / rel)
        if sp == sb:
            continue  # no lo tocaste en la prueba
        if sp == so:
            iguales += 1
            continue  # ya está igual en la operativa
        if sb is not None and so is not None and so != sb:
            conflictos.append(rel)
            continue
        promover.append((rel, "NUEVO" if so is None else "MODIFICADO"))

    print(f"Operativa: {oper}")
    print(f"Prueba   : {AQUI}")
    print("-" * 70)
    if promover:
        print(f"SE PROMOVERÁN {len(promover)} archivo(s):")
        for rel, tipo in promover:
            print(f"   [{tipo}] {rel}")
    else:
        print("No hay cambios probados para promover.")
    if conflictos:
        print(f"\nCONFLICTOS ({len(conflictos)}) — cambiaron en AMBOS lados, NO se tocarán:")
        for rel in conflictos:
            print(f"   [CONFLICTO] {rel}")
    if iguales:
        print(f"\n({iguales} archivo(s) ya estaban iguales en la operativa)")
    if not args.aplicar:
        print("\n(Solo plan. Para aplicar: Promover_a_Operativa.bat --aplicar)")
        return 0
    if not promover:
        return 0

    # 1) Compilar
    for rel, _ in promover:
        if rel.lower().endswith(".py"):
            try:
                py_compile.compile(str(AQUI / rel), doraise=True)
            except py_compile.PyCompileError as exc:
                print(f"ERROR de sintaxis en {rel}: {exc}\nNo se promovió nada.")
                return 3

    if not args.si:
        r = input("\nEscribe SI para aplicar estos cambios a la OPERATIVA: ").strip().upper()
        if r != "SI":
            print("Cancelado.")
            return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 2) Respaldo
    carpeta_resp = RESPALDOS / ts
    for rel, tipo in promover:
        if tipo == "MODIFICADO":
            dst = carpeta_resp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(oper / rel, dst)
    carpeta_resp.mkdir(parents=True, exist_ok=True)
    (carpeta_resp / "plan.json").write_text(json.dumps({"promovidos": promover}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Respaldo de originales: {carpeta_resp}")

    # 3) Etiqueta de retorno en git
    sys.path.insert(0, str(oper))
    git = None
    try:
        import publicar_github as pg

        git = pg.GIT_EXE
    except Exception:
        pass
    if git and (oper / ".git").exists():
        r = subprocess.run([git, "-C", str(oper), "tag", f"respaldo-previo-{ts}"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        print(f"Etiqueta de retorno git: respaldo-previo-{ts} (rc={r.returncode})")

    # 4) Copiar
    nuevos_base = dict(base)
    for rel, _ in promover:
        dst = oper / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(AQUI / rel, dst)
        nuevos_base[rel] = sha(dst)
    mf = json.loads((AQUI / "data" / "base_manifest.json").read_text(encoding="utf-8"))
    mf["archivos"] = nuevos_base
    (AQUI / "data" / "base_manifest.json").write_text(json.dumps(mf, indent=1), encoding="utf-8")
    print(f"Copiados {len(promover)} archivo(s) a la operativa.")

    # 5) Publicar a GitHub (solo esos archivos)
    if args.sin_publicar or not git or not (oper / ".git").exists():
        print("No se publicó a GitHub (modo sin-publicar o sin repositorio).")
        return 0
    msg = args.mensaje or f"Promocion desde ovalo_mariategui_prueba ({ts})"
    rels = [rel for rel, _ in promover]
    subprocess.run([git, "-C", str(oper), "add", "--"] + rels, check=False, creationflags=CREATE_NO_WINDOW)
    c = subprocess.run([git, "-C", str(oper), "commit", "-m", msg], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    print((c.stdout or c.stderr).strip()[:300])
    token = pg._obtener_token_windows()
    push_url = f"https://eduardocalaniguevara-del:{token}@{pg.REPO_SLUG}"
    p = subprocess.run([git, "-C", str(oper), "push", push_url, "main"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    print(((p.stdout or "") + (p.stderr or "")).replace(token, "***").strip()[-300:])
    if p.returncode == 0:
        print("PUBLICADO. Laptop 1 y Laptop 2 se actualizan solas en ~20 segundos.")
        print(f"Para deshacer: restaura desde {carpeta_resp} o usa la etiqueta git respaldo-previo-{ts}.")
        return 0
    print("ERROR al publicar en GitHub (los archivos SÍ quedaron copiados en la operativa de esta laptop).")
    return 4


if __name__ == "__main__":
    sys.exit(main())
