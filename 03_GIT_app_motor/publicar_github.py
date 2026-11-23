#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_GIT_app_motor/publicar_github.py — Publicador automático a GitHub desde Laptop 1 (Modo Editor)
Usa la sesión de GitHub Desktop en Windows Credential Manager sin exponer tokens en archivos.
"""
import ctypes
import ctypes.wintypes as wt
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GIT_EXE = shutil.which("git") or r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe"
REPO_SLUG = "github.com/eduardocalaniguevara-del/app_local_recepcion.git"
CRED_TARGET = "GitHub - https://api.github.com/eduardocalaniguevara-del"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


def _obtener_token_windows() -> str:
    advapi32 = ctypes.windll.advapi32
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if advapi32.CredReadW(CRED_TARGET, 1, 0, ctypes.byref(pcred)):
        cred = pcred.contents
        blob = (
            ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            .decode("utf-8", errors="ignore")
            .strip("\x00")
        )
        advapi32.CredFree(pcred)
        return blob
    raise RuntimeError("No se encontró la sesión de GitHub Desktop en Windows Credential Manager.")


def publicar(rama_destino: str = "main") -> dict:
    token = _obtener_token_windows()
    push_url = f"https://eduardocalaniguevara-del:{token}@{REPO_SLUG}"
    for rama in ("main", "sistema-v2"):
        r = subprocess.run(
            [GIT_EXE, "-C", str(BASE_DIR), "push", "--force", push_url, f"HEAD:{rama}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.replace(token, "***"))
    sha = subprocess.check_output(
        [GIT_EXE, "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
        text=True,
    ).strip()
    return {"ok": True, "repo": REPO_SLUG, "ramas_actualizadas": ["main", "sistema-v2"], "commit": sha}


if __name__ == "__main__":
    rama = sys.argv[1] if len(sys.argv) > 1 else "sistema-v2"
    print(publicar(rama))
