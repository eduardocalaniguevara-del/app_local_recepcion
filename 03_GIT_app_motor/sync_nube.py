"""
04_GIT_app_motor/sync_nube.py — Puente Bidireccional SQLite <-> Supabase para los 3 Niveles:
- NIVEL_1_INGENIERIA: Sube transferencias de 02_PRIV_correo_bcp y recibe toda la operación.
- NIVEL_2_MADRE_HOTEL: Sube estancias, pagos, SUNAT y chats del único QR de WhatsApp de Recepción.
- NIVEL_3_TERCIARIA: Recibe todo en vivo sin escanear QR y encola envíos de WhatsApp hacia Nivel 2.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    EQUIPO_ID,
    HOSTAL_SYNC_TOKEN,
    ROL_NODO,
    SUPABASE_CONFIGURADO,
    SUPABASE_KEY,
    SUPABASE_URL,
    TIENE_QR_WSP_FISICO,
)
from db import (
    TABLAS_SINCRONIZADAS_PK,
    TABLAS_SOLO_LOCAL_CACHE,
    get_connection,
    init_db,
)


def _headers(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "x-hostal-sync-token": HOSTAL_SYNC_TOKEN,
        "x-hostal-equipo": EQUIPO_ID,
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def instalar_triggers_outbox() -> dict:
    """
    Crea los triggers SQLite de 0 ms para todas las tablas de TABLAS_SINCRONIZADAS_PK.
    Garantiza que las 3 tablas de TABLAS_SOLO_LOCAL_CACHE jamás generen tráfico a Supabase.
    """
    init_db()
    conn = get_connection()
    instalados = 0
    try:
        for tabla_cache in TABLAS_SOLO_LOCAL_CACHE:
            for pref in ("ins", "upd", "del"):
                conn.execute(f"DROP TRIGGER IF EXISTS trg_sb_{pref}_{tabla_cache};")

        for tabla, pks in TABLAS_SINCRONIZADAS_PK.items():
            if tabla in TABLAS_SOLO_LOCAL_CACHE or not pks:
                continue
            pk1 = pks[0]
            pk2_new = f"CAST(NEW.{pks[1]} AS TEXT)" if len(pks) > 1 else "''"
            pk2_old = f"CAST(OLD.{pks[1]} AS TEXT)" if len(pks) > 1 else "''"

            conn.executescript(
                f"""
                DROP TRIGGER IF EXISTS trg_sb_ins_{tabla};
                DROP TRIGGER IF EXISTS trg_sb_upd_{tabla};
                DROP TRIGGER IF EXISTS trg_sb_del_{tabla};

                CREATE TRIGGER trg_sb_ins_{tabla}
                AFTER INSERT ON {tabla}
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(NEW.{pk1} AS TEXT), {pk2_new}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER trg_sb_upd_{tabla}
                AFTER UPDATE ON {tabla}
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(NEW.{pk1} AS TEXT), {pk2_new}, 'UPSERT', datetime('now', 'localtime'));
                END;

                CREATE TRIGGER trg_sb_del_{tabla}
                AFTER DELETE ON {tabla}
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT OR REPLACE INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(OLD.{pk1} AS TEXT), {pk2_old}, 'DELETE', datetime('now', 'localtime'));
                END;
                """
            )
            instalados += 3
        conn.commit()
        return {"ok": True, "triggers_activos": instalados, "tablas_protegidas_egress": sorted(TABLAS_SOLO_LOCAL_CACHE)}
    finally:
        conn.close()


_SUPABASE_COLS_CACHE: Dict[str, set] = {}
_VITRINA_EXTRA_COLS = {f"h{i}_{suf}" for i in range(9, 37) for suf in ("cant", "est")}


def _obtener_columnas_reales_supabase(tabla: str) -> set:
    if tabla in _SUPABASE_COLS_CACHE:
        return _SUPABASE_COLS_CACHE[tabla]
    url = f"{SUPABASE_URL}/rest/v1/{tabla}?select=*&limit=1"
    req = urllib.request.Request(url, method="GET", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                cols = set(rows[0].keys())
                _SUPABASE_COLS_CACHE[tabla] = cols
                return cols
    except Exception:
        pass
    return set()


def _recortar_columnas_locales_extra(tabla: str, filas: List[Dict[str, Any]], cols_permitidas: set | None = None) -> List[Dict[str, Any]]:
    recortadas = []
    for f in filas:
        d = dict(f)
        if tabla == "vitrina_local":
            for k_extra in _VITRINA_EXTRA_COLS:
                d.pop(k_extra, None)
        if cols_permitidas:
            d = {k: v for k, v in d.items() if k in cols_permitidas}
        recortadas.append(d)
    return recortadas


def push_to_supabase(limit: int = 150) -> dict:
    """
    Lee los sobres pendientes en _supabase_outbox, cierra SQLite en <5 ms (cero bloqueos)
    y los sube por lotes a Supabase recortando automáticamente cualquier columna local extra.
    """
    if not SUPABASE_CONFIGURADO:
        return {"ok": False, "modo": "OFFLINE_SIN_LLAVE", "enviados": 0}

    conn = get_connection()
    lotes: Dict[str, List[Dict[str, Any]]] = {}
    sobres_leidos = []
    try:
        rows = conn.execute(
            "SELECT tabla, pk1, pk2, op FROM _supabase_outbox ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        for r in rows:
            tabla, pk1, pk2, op = r["tabla"], r["pk1"], r["pk2"], r["op"]
            if tabla in TABLAS_SOLO_LOCAL_CACHE or tabla not in TABLAS_SINCRONIZADAS_PK:
                sobres_leidos.append((tabla, pk1, pk2))
                continue
            pks = TABLAS_SINCRONIZADAS_PK[tabla]
            if op == "UPSERT":
                if len(pks) == 1:
                    fila = conn.execute(f'SELECT * FROM "{tabla}" WHERE CAST({pks[0]} AS TEXT) = ?', (pk1,)).fetchone()
                else:
                    fila = conn.execute(
                        f'SELECT * FROM "{tabla}" WHERE CAST({pks[0]} AS TEXT) = ? AND CAST({pks[1]} AS TEXT) = ?',
                        (pk1, pk2),
                    ).fetchone()
                if fila:
                    lotes.setdefault(tabla, []).append(dict(fila))
            sobres_leidos.append((tabla, pk1, pk2))
    finally:
        conn.close()  # Cierra SQLite antes de hablar por red

    if not sobres_leidos:
        return {"ok": True, "enviados": 0, "pendientes": 0}

    enviados = 0
    errores = []
    tablas_ok = set()
    for tabla, filas in lotes.items():
        pks = TABLAS_SINCRONIZADAS_PK[tabla]
        on_conflict = ",".join(pks)
        url = f"{SUPABASE_URL}/rest/v1/{tabla}?on_conflict={urllib.parse.quote(on_conflict)}"
        filas_limpias = _recortar_columnas_locales_extra(tabla, filas, _SUPABASE_COLS_CACHE.get(tabla))

        for intento in range(3):
            body = json.dumps(filas_limpias, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if 200 <= resp.status < 300:
                        enviados += len(filas_limpias)
                        tablas_ok.add(tabla)
                        break
            except urllib.error.HTTPError as he:
                err_body = ""
                try:
                    err_body = he.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                # Si Supabase reporta una columna local extra que no existe en la nube, recortarla y reintentar al instante
                if he.code == 400 and ("column" in err_body or "PGRST204" in err_body):
                    import re as _re
                    m_col = _re.search(r"Could not find the '([^']+)' column", err_body)
                    cols_sb = _obtener_columnas_reales_supabase(tabla)
                    if m_col:
                        col_desconocida = m_col.group(1)
                        filas_limpias = [{k: v for k, v in f.items() if k != col_desconocida} for f in filas_limpias]
                        if cols_sb:
                            cols_sb.discard(col_desconocida)
                        continue
                    elif cols_sb:
                        filas_limpias = _recortar_columnas_locales_extra(tabla, filas_limpias, cols_sb)
                        continue
                errores.append(f"{tabla} (HTTP {he.code}): {err_body[:160]}")
                break
            except Exception as exc:
                errores.append(f"{tabla}: {exc}")
                break

    # Limpiar de _supabase_outbox los sobres que ya subieron correctamente
    conn_clean = get_connection()
    try:
        for tabla, pk1, pk2 in sobres_leidos:
            if tabla in tablas_ok or tabla not in lotes:
                conn_clean.execute(
                    "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                    (tabla, pk1, pk2),
                )
        conn_clean.commit()
    finally:
        conn_clean.close()

    return {"ok": len(errores) == 0, "enviados": enviados, "errores": errores}


def pull_from_supabase(limit: int = 400) -> dict:
    """
    Descarga en 1 sola llamada HTTP los cambios nuevos desde supabase_change_log (columna row_json)
    para que cualquier Laptop (Nivel 1, Nivel 2 o Nivel 3 Terciaria) se mantenga al día.
    """
    if not SUPABASE_CONFIGURADO:
        return {"ok": False, "modo": "OFFLINE_SIN_LLAVE", "aplicados": 0}

    conn = get_connection()
    try:
        ctrl = conn.execute("SELECT last_log_id FROM _supabase_sync_control WHERE id = 1").fetchone()
        ctrl_id = int(ctrl["last_log_id"]) if (ctrl and ctrl["last_log_id"]) else 0
        sb_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'sb_last_log_id'").fetchone()
        sb_id = int(sb_row["valor"]) if (sb_row and str(sb_row["valor"]).isdigit()) else 0
        last_log_id = max(ctrl_id, sb_id)
    finally:
        conn.close()

    url = (
        f"{SUPABASE_URL}/rest/v1/supabase_change_log"
        f"?select=log_id,tabla,op,row_json,origen_equipo&log_id=gt.{last_log_id}&order=log_id.asc&limit={limit}"
    )
    req = urllib.request.Request(url, method="GET", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logs = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "aplicados": 0, "error": str(exc)}

    if not isinstance(logs, list) or not logs:
        return {"ok": True, "aplicados": 0, "last_log_id": last_log_id}

    conn_apply = get_connection()
    aplicados = 0
    nuevo_cursor = last_log_id
    try:
        conn_apply.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
        for item in logs:
            lid = int(item.get("log_id") or 0)
            if lid > nuevo_cursor:
                nuevo_cursor = lid
            tabla = str(item.get("tabla") or "")
            op = str(item.get("op") or "UPSERT").upper()
            payload = item.get("row_json") if item.get("row_json") is not None else (item.get("payload") or {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if tabla not in TABLAS_SINCRONIZADAS_PK or tabla in TABLAS_SOLO_LOCAL_CACHE or not isinstance(payload, dict):
                continue

            cols_locales = {
                r[1] for r in conn_apply.execute(f'PRAGMA table_info("{tabla}")').fetchall()
            }
            datos_filtrados = {k: v for k, v in payload.items() if k in cols_locales}
            if not datos_filtrados:
                continue

            if op == "DELETE":
                pks = TABLAS_SINCRONIZADAS_PK[tabla]
                if all(k in datos_filtrados for k in pks):
                    where_sql = " AND ".join(f'"{k}" = ?' for k in pks)
                    conn_apply.execute(
                        f'DELETE FROM "{tabla}" WHERE {where_sql}',
                        tuple(datos_filtrados[k] for k in pks),
                    )
                    aplicados += 1
            else:
                cols_q = ", ".join(f'"{k}"' for k in datos_filtrados.keys())
                ph = ", ".join("?" for _ in datos_filtrados)
                conn_apply.execute(
                    f'INSERT OR REPLACE INTO "{tabla}" ({cols_q}) VALUES ({ph})',
                    tuple(datos_filtrados.values()),
                )
                aplicados += 1

        conn_apply.execute(
            "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('sb_last_log_id', ?, datetime('now', 'localtime'))",
            (str(nuevo_cursor),),
        )
        conn_apply.execute(
            "UPDATE _supabase_sync_control SET pulling = 0, last_log_id = ?, updated_at = datetime('now', 'localtime') WHERE id = 1;",
            (nuevo_cursor,),
        )
        conn_apply.commit()
        return {"ok": True, "aplicados": aplicados, "last_log_id": nuevo_cursor}
    finally:
        try:
            conn_apply.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn_apply.commit()
        except Exception:
            pass
        conn_apply.close()


def encolar_whatsapp_remoto(
    destino_celular: str,
    mensaje_texto: str,
    nro_comprobante: str = "",
    adjunto_pdf_path: str = "",
) -> dict:
    """
    Permite que cualquier Laptop Terciaria (Nivel 3) o Laptop 1 (Nivel 1), que NO tienen
    WhatsApp abierto, envíen un mensaje o boleta a través de la Laptop Madre (Nivel 2).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO wsp_recepcion_cola_envio
            (destino_celular, mensaje_texto, adjunto_pdf_path, nro_comprobante, solicitado_por, estado)
            VALUES (?, ?, ?, ?, ?, 'PENDIENTE')
            """,
            (destino_celular.strip(), mensaje_texto.strip(), adjunto_pdf_path, nro_comprobante, ROL_NODO),
        )
        conn.commit()
        cola_id = cur.lastrowid
    finally:
        conn.close()

    res_push = push_to_supabase(limit=20)
    return {
        "ok": True,
        "cola_id": cola_id,
        "solicitado_desde": ROL_NODO,
        "quien_dispara_wsp_fisico": "NIVEL_2_MADRE_HOTEL (Laptop 2 Recepción)",
        "sincronizado_nube": res_push.get("ok", False),
    }


def diagnostico_fase2() -> dict:
    trg = instalar_triggers_outbox()
    conn = get_connection()
    try:
        outbox_n = conn.execute("SELECT COUNT(*) FROM _supabase_outbox").fetchone()[0]
        ctrl = conn.execute("SELECT pulling, last_log_id FROM _supabase_sync_control WHERE id = 1").fetchone()
    finally:
        conn.close()

    prueba_sb = {"conectado": False}
    if SUPABASE_CONFIGURADO:
        url = f"{SUPABASE_URL}/rest/v1/wsp_recepcion_chats?select=jid&limit=1"
        req = urllib.request.Request(url, method="GET", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                prueba_sb = {"conectado": resp.status == 200, "http_status": resp.status}
        except Exception as exc:
            prueba_sb = {"conectado": False, "error": str(exc)}

    return {
        "ok": trg["ok"],
        "nodo_actual": ROL_NODO,
        "equipo_id": EQUIPO_ID,
        "tiene_qr_wsp_fisico": TIENE_QR_WSP_FISICO,
        "triggers_sqlite_0ms": trg["triggers_activos"],
        "outbox_pendientes": outbox_n,
        "cursor_delta_log_id": ctrl["last_log_id"] if ctrl else 0,
        "supabase_configurado": SUPABASE_CONFIGURADO,
        "supabase_lectura_vivo": prueba_sb,
    }


if __name__ == "__main__":
    if "--sync" in sys.argv:
        instalar_triggers_outbox()
        up = push_to_supabase()
        down = pull_from_supabase()
        print(json.dumps({"push": up, "pull": down}, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(diagnostico_fase2(), indent=2, ensure_ascii=False))
