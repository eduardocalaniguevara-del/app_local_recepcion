#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sincroniza todos los mensajes, chats, documentos, estado y resumen de WhatsApp
desde la base de Laptop 2 hacia Supabase, y actualiza el motor de auto-actualización
del entorno de prueba (C:\\ovalo_mariategui_prueba) para que Laptop 1 y Laptop 2
vean exactamente los mismos mensajes en su entorno de prueba (8082).
NO modifica estancias, pagos, tienda ni Google Sheets.
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

SUPABASE_URL = "https://fmlfqupwscymosvwnnzx.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0."
    "pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g"
)
SUPABASE_SYNC_TOKEN = "ovalo_master_sync_2026_k9x"


def _post_supabase(tabla: str, conflict_col: str, rows: list) -> int:
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{tabla}?on_conflict={conflict_col}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "x-hostal-sync-token": SUPABASE_SYNC_TOKEN,
        "x-hostal-equipo": f"LAPTOP_2_RECEPCION_{socket.gethostname()}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    pushed = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i : i + 100]
        data = json.dumps(chunk, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=25) as r:
            if r.status in (200, 201, 204):
                pushed += len(chunk)
    return pushed


def subir_wsp_desde_db(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False}
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    res = {"db": str(db_path)}
    try:
        try:
            conn.execute(
                "UPDATE wsp_recepcion_mensajes SET id = id_mensaje WHERE (id IS NULL OR id = '') AND id_mensaje IS NOT NULL"
            )
            conn.commit()
        except Exception:
            pass

        for tabla, conflict_col in [
            ("wsp_recepcion_chats", "jid"),
            ("wsp_recepcion_mensajes", "id"),
            ("wsp_recepcion_documentos", "id"),
            ("wsp_recepcion_estado", "clave"),
            ("wsp_resumen_ejecutivo", "id"),
        ]:
            ex = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)
            ).fetchone()
            if not ex:
                continue
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {tabla}").fetchall()]
            if not rows:
                continue
            if tabla == "wsp_recepcion_mensajes":
                for r in rows:
                    if not r.get("id") and r.get("id_mensaje"):
                        r["id"] = r["id_mensaje"]
            if tabla == "wsp_resumen_ejecutivo":
                rows = [r for r in rows if "Sin conversaciones" not in str(r.get("payload_json") or "")]
            if rows:
                try:
                    res[tabla] = _post_supabase(tabla, conflict_col, rows)
                except Exception as e:
                    res[tabla] = f"ERR: {e}"
    finally:
        conn.close()
    return res


def actualizar_git_prueba_l2():
    prueba_dir = Path(r"C:\ovalo_mariategui_prueba")
    if not prueba_dir.exists():
        return
    git_exe = shutil.which("git") or r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe"
    if not Path(git_exe).exists() and git_exe != "git":
        for c in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"):
            if Path(c).exists():
                git_exe = c
                break
    repo_url = "https://github.com/eduardocalaniguevara-del/ovalo_mariategui_prueba.git"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        if not (prueba_dir / ".git").exists():
            subprocess.run([git_exe, "-C", str(prueba_dir), "init"], creationflags=flags, timeout=10)
            subprocess.run([git_exe, "-C", str(prueba_dir), "branch", "-M", "main"], creationflags=flags, timeout=10)
            subprocess.run([git_exe, "-C", str(prueba_dir), "remote", "add", "origin", repo_url], creationflags=flags, timeout=10)
        else:
            subprocess.run([git_exe, "-C", str(prueba_dir), "remote", "set-url", "origin", repo_url], creationflags=flags, timeout=10)
        f = subprocess.run([git_exe, "-C", str(prueba_dir), "fetch", "origin", "main", "--quiet"], creationflags=flags, timeout=20)
        if f.returncode == 0:
            subprocess.run([git_exe, "-C", str(prueba_dir), "reset", "--hard", "origin/main"], creationflags=flags, timeout=15)
            ps1 = prueba_dir / "reiniciar_app.ps1"
            if ps1.exists():
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)],
                    cwd=str(prueba_dir),
                    creationflags=flags,
                )
    except Exception:
        pass


def main():
    # Solo actuar en Laptop 2 (donde existen los mensajes reales de recepción)
    es_l2 = socket.gethostname().upper() == "LAPTOP-JAPIGJT2" or Path(r"C:\Users\ovalo mariategui").exists()
    for p in [
        Path(r"C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db"),
        Path(r"C:\ovalo_mariategui_prueba\data\ovalo_sistema.db"),
    ]:
        if p.exists():
            subir_wsp_desde_db(p)
    if es_l2:
        actualizar_git_prueba_l2()


if __name__ == "__main__":
    main()
