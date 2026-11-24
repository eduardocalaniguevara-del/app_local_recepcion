"""
04_GIT_app_motor/sync_nube.py — Puente Bidireccional SQLite <-> Supabase para los 3 Niveles:
- NIVEL_1_INGENIERIA: Sube transferencias de 02_PRIV_correo_bcp y recibe toda la operación.
- NIVEL_2_MADRE_HOTEL: Sube estancias, pagos, SUNAT y chats del único QR de WhatsApp de Recepción.
- NIVEL_3_TERCIARIA: Recibe todo en vivo sin escanear QR y encola envíos de WhatsApp hacia Nivel 2.
"""
import json
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    EQUIPO_ID,
    HOSTAL_SYNC_TOKEN,
    MODO_PRUEBA,
    ROL_NODO,
    SCOPES,
    SPREADSHEET_ID,
    SUPABASE_CONFIGURADO,
    SUPABASE_KEY,
    SUPABASE_URL,
    TIENE_QR_WSP_FISICO,
    obtener_ruta_credenciales,
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
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(NEW.{pk1} AS TEXT), {pk2_new}, 'UPSERT', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at;
                END;

                CREATE TRIGGER trg_sb_upd_{tabla}
                AFTER UPDATE ON {tabla}
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(NEW.{pk1} AS TEXT), {pk2_new}, 'UPSERT', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at;
                END;

                CREATE TRIGGER trg_sb_del_{tabla}
                AFTER DELETE ON {tabla}
                WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
                BEGIN
                    INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
                    VALUES ('{tabla}', CAST(OLD.{pk1} AS TEXT), {pk2_old}, 'DELETE', datetime('now', 'localtime'))
                    ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at;
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
    y los sube por lotes a Supabase (incluyendo DELETE y recorte automático de columnas locales extra).
    """
    if not SUPABASE_CONFIGURADO:
        return {"ok": False, "modo": "OFFLINE_SIN_LLAVE", "enviados": 0}

    conn = get_connection()
    lotes: Dict[str, List[Dict[str, Any]]] = {}
    deletes_por_tabla: Dict[str, List[Tuple[str, str]]] = {}
    sobres_limpiar: set = set()
    sobres_leidos = []
    try:
        rows = conn.execute(
            "SELECT tabla, pk1, pk2, op FROM _supabase_outbox ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        for r in rows:
            tabla, pk1, pk2, op = r["tabla"], r["pk1"], r["pk2"], r["op"]
            if tabla in TABLAS_SOLO_LOCAL_CACHE or tabla not in TABLAS_SINCRONIZADAS_PK:
                sobres_limpiar.add((tabla, pk1, pk2))
                sobres_leidos.append((tabla, pk1, pk2))
                continue
            pks = TABLAS_SINCRONIZADAS_PK[tabla]
            if op == "DELETE":
                deletes_por_tabla.setdefault(tabla, []).append((pk1, pk2))
            else:
                if len(pks) == 1:
                    fila = conn.execute(f'SELECT * FROM "{tabla}" WHERE CAST({pks[0]} AS TEXT) = ?', (pk1,)).fetchone()
                else:
                    fila = conn.execute(
                        f'SELECT * FROM "{tabla}" WHERE CAST({pks[0]} AS TEXT) = ? AND CAST({pks[1]} AS TEXT) = ?',
                        (pk1, pk2),
                    ).fetchone()
                if fila:
                    lotes.setdefault(tabla, []).append(dict(fila))
                else:
                    sobres_limpiar.add((tabla, pk1, pk2))
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
                        for f in filas:
                            pk1_v = str(f.get(pks[0], ""))
                            pk2_v = str(f.get(pks[1], "")) if len(pks) > 1 else ""
                            sobres_limpiar.add((tabla, pk1_v, pk2_v))
                        break
            except urllib.error.HTTPError as he:
                err_body = ""
                try:
                    err_body = he.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                # Si Supabase reporta una columna local extra que no existe en la nube, recortarla y reintentar al instante
                if he.code == 400 and ("column" in err_body or "PGRST204" in err_body):
                    m_col = re.search(r"Could not find the '([^']+)' column", err_body)
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

    for tabla, items_del in deletes_por_tabla.items():
        pks = TABLAS_SINCRONIZADAS_PK[tabla]
        for pk1, pk2 in items_del:
            query = f"{pks[0]}=eq.{urllib.parse.quote(str(pk1))}"
            if len(pks) > 1:
                query += f"&{pks[1]}=eq.{urllib.parse.quote(str(pk2))}"
            url_del = f"{SUPABASE_URL}/rest/v1/{tabla}?{query}"
            req_del = urllib.request.Request(url_del, method="DELETE", headers=_headers())
            try:
                with urllib.request.urlopen(req_del, timeout=8) as resp:
                    if 200 <= resp.status < 300:
                        enviados += 1
                        sobres_limpiar.add((tabla, pk1, pk2))
            except urllib.error.HTTPError as he:
                err_b = ""
                try:
                    err_b = he.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                errores.append(f"DEL {tabla} (HTTP {he.code}): {err_b[:120]}")
            except Exception as exc:
                errores.append(f"DEL {tabla}: {exc}")

    # Limpiar de _supabase_outbox los sobres que ya subieron correctamente
    conn_clean = get_connection()
    try:
        for tabla, pk1, pk2 in sobres_limpiar:
            conn_clean.execute(
                "DELETE FROM _supabase_outbox WHERE tabla = ? AND pk1 = ? AND pk2 = ?",
                (tabla, pk1, pk2),
            )
        conn_clean.commit()
    finally:
        conn_clean.close()

    return {"ok": len(errores) == 0, "enviados": enviados, "errores": errores}


def bootstrap_initial_from_supabase() -> dict:
    """
    Si esta base local ovalo.db es nueva (tiene < 100 estancias o 0 habitaciones_config),
    descarga de un solo golpe el 100% de las tablas maestras desde Supabase para que una
    Laptop Terciaria (L3) recién instalada tenga todas las habitaciones y fechas históricas.
    """
    if not SUPABASE_CONFIGURADO:
        return {"ok": False, "modo": "OFFLINE_SIN_LLAVE"}
    conn = get_connection()
    try:
        c_est = conn.execute("SELECT COUNT(*) FROM estancias").fetchone()[0]
        c_hab = conn.execute("SELECT COUNT(*) FROM habitaciones_config").fetchone()[0]
        c_20nov = conn.execute("SELECT COUNT(*) FROM estancias WHERE ymd_in = 20261120").fetchone()[0]
        done_flag = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'sb_bootstrap_full_v3'").fetchone()
    finally:
        conn.close()
    if (done_flag or (c_est >= 5000 and c_20nov >= 10)) and c_est >= 500 and c_hab >= 15 and c_20nov >= 10:
        return {"ok": True, "bootstrap": "ALREADY_DONE", "estancias": c_est}

    total_descargados = {}
    conn_w = get_connection()
    try:
        conn_w.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
        for tabla, pk_tuple in TABLAS_SINCRONIZADAS_PK.items():
            if tabla in TABLAS_SOLO_LOCAL_CACHE:
                continue
            cols_locales = {r[1] for r in conn_w.execute(f'PRAGMA table_info("{tabla}")').fetchall()}
            if not cols_locales:
                continue
            order_clause = ",".join(f"{col}.asc" for col in pk_tuple)
            offset = 0
            page_size = 1000
            t_count = 0
            while True:
                url = f"{SUPABASE_URL}/rest/v1/{tabla}?select=*&order={order_clause}&limit={page_size}&offset={offset}"
                req = urllib.request.Request(url, method="GET", headers=_headers())
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        rows = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    break
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if tabla == "estado_sistema" and row.get("clave") in ("sb_last_log_id", "network_online", "offline_since_iso"):
                        continue
                    datos = {k: v for k, v in row.items() if k in cols_locales and v is not None}
                    if not datos:
                        continue
                    cols_q = ", ".join(f'"{k}"' for k in datos.keys())
                    ph = ", ".join("?" for _ in datos)
                    try:
                        conn_w.execute(f'INSERT OR REPLACE INTO "{tabla}" ({cols_q}) VALUES ({ph})', tuple(datos.values()))
                        t_count += 1
                    except sqlite3.IntegrityError:
                        continue
                if len(rows) < page_size:
                    break
                offset += page_size
            total_descargados[tabla] = t_count

        # Fijar cursor al último log_id actual de supabase_change_log
        try:
            url_max = f"{SUPABASE_URL}/rest/v1/supabase_change_log?select=log_id&order=log_id.desc&limit=1"
            req_m = urllib.request.Request(url_max, method="GET", headers=_headers())
            with urllib.request.urlopen(req_m, timeout=10) as rm:
                arr = json.loads(rm.read().decode("utf-8"))
                if arr and isinstance(arr, list):
                    max_lid = int(arr[0].get("log_id") or 0)
                    conn_w.execute("UPDATE _supabase_sync_control SET last_log_id = ? WHERE id = 1;", (max_lid,))
                    conn_w.execute(
                        "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('sb_last_log_id', ?, datetime('now', 'localtime'))",
                        (str(max_lid),),
                    )
        except Exception:
            pass

        conn_w.execute(
            "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('sb_bootstrap_full_v3', 'OK', datetime('now', 'localtime'))"
        )
        conn_w.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
        conn_w.commit()
    finally:
        try:
            conn_w.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn_w.commit()
        except Exception:
            pass
        conn_w.close()
    return {"ok": True, "bootstrap": "COMPLETED", "tablas": total_descargados}


CLAVES_ESTADO_COMPARTIDAS_SUPABASE = (
    "last_row_estancias",
    "wsp_recepcion_conexion",
    "wsp_recepcion_usuario_actual",
)


def _sincronizar_claves_estado_compartidas_supabase(conn) -> int:
    """
    Como la tabla `estado_sistema` en Supabase no genera eventos en `supabase_change_log`,
    sincroniza directamente las claves compartidas de estado (`last_row_estancias`, estado WSP).
    Nota: `obtener_caja_efectivo_actual` ya se calcula 100% desde SQLite local (`estancias + ventas_tienda - gastos`)
    sin depender de `caja_sheets_g1` ni de Google Sheets.
    """
    if not SUPABASE_CONFIGURADO:
        return 0
    claves_in = ",".join(CLAVES_ESTADO_COMPARTIDAS_SUPABASE)
    url = f"{SUPABASE_URL}/rest/v1/estado_sistema?clave=in.({claves_in})&select=clave,valor,updated_at"
    req = urllib.request.Request(url, method="GET", headers=_headers())
    actualizadas = 0
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if isinstance(rows, list):
            for r in rows:
                if not isinstance(r, dict):
                    continue
                cl = str(r.get("clave") or "").strip()
                vl = r.get("valor")
                up = str(r.get("updated_at") or "")
                if not cl or vl is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                    (cl, str(vl), up),
                )
                actualizadas += 1
    except Exception:
        pass
    return actualizadas


def pull_from_supabase(limit: int = 400) -> dict:
    """
    Descarga en 1 sola llamada HTTP los cambios nuevos desde supabase_change_log (columna row_json)
    para que cualquier Laptop (Nivel 1, Nivel 2 o Nivel 3 Terciaria) se mantenga al día.
    """
    if not SUPABASE_CONFIGURADO:
        return {"ok": False, "modo": "OFFLINE_SIN_LLAVE", "aplicados": 0}

    bootstrap_initial_from_supabase()

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
        conn_touch = get_connection()
        try:
            ahora_iso = datetime.now().isoformat(timespec="seconds")
            ahora_hms = datetime.now().strftime("%H:%M:%S")
            conn_touch.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1;")
            _sincronizar_claves_estado_compartidas_supabase(conn_touch)
            for cl_t, vl_t in (("network_online", "1"), ("last_sync_ts", ahora_hms), ("last_successful_pull_iso", ahora_iso), ("offline_since_iso", "")):
                conn_touch.execute(
                    "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                    (cl_t, vl_t, ahora_iso),
                )
            conn_touch.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1;")
            conn_touch.commit()
        finally:
            conn_touch.close()
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
            if tabla == "estado_sistema" and payload.get("clave") in ("sb_last_log_id", "network_online", "offline_since_iso"):
                continue

            cols_locales = {
                r[1] for r in conn_apply.execute(f'PRAGMA table_info("{tabla}")').fetchall()
            }
            datos_filtrados = {k: v for k, v in payload.items() if k in cols_locales and v is not None}
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

        ahora_iso = datetime.now().isoformat(timespec="seconds")
        ahora_hms = datetime.now().strftime("%H:%M:%S")
        _sincronizar_claves_estado_compartidas_supabase(conn_apply)
        for cl_t, vl_t in (("sb_last_log_id", str(nuevo_cursor)), ("network_online", "1"), ("last_sync_ts", ahora_hms), ("last_successful_pull_iso", ahora_iso), ("offline_since_iso", "")):
            conn_apply.execute(
                "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                (cl_t, vl_t, ahora_iso),
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
    cliente_nombre: str = "",
) -> dict:
    """
    Permite que cualquier Laptop Terciaria (Nivel 3) o Laptop 1 (Nivel 1), que NO tienen
    WhatsApp abierto, envíen un mensaje o boleta a través de la Laptop Madre (Nivel 2).
    """
    import os
    tel_clean = re.sub(r"\D", "", str(destino_celular or "").strip())
    if len(tel_clean) == 9:
        tel_clean = "51" + tel_clean
    msg_clean = str(mensaje_texto or "").strip()
    nom_adj = os.path.basename(adjunto_pdf_path) if adjunto_pdf_path else ""
    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO wsp_recepcion_cola_envio (
                telefono_o_jid, destino_telefono, mensaje, texto,
                ruta_adjunto, archivo_ruta, nombre_adjunto,
                nro_comprobante, cliente_nombre, estado, creado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', ?)
            """,
            (
                tel_clean,
                tel_clean,
                msg_clean,
                msg_clean,
                adjunto_pdf_path,
                adjunto_pdf_path,
                nom_adj,
                nro_comprobante,
                cliente_nombre or ROL_NODO,
                ahora_str,
            ),
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


_sheets_lock = threading.Lock()
_gspread_client_v2 = None
_spreadsheet_v2 = None


def _canon_ts_id_sheets(v: Any) -> str:
    s = str(v or "").split(" #")[0].strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?(.*)$", s)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        tail = m.group(7) or ""
        if m.group(4) is not None:
            hh = int(m.group(4))
            mi = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            return f"{dd:02d}/{mm:02d}/{yy} {hh:02d}:{mi:02d}:{ss:02d}{tail}"
        return f"{dd:02d}/{mm:02d}/{yy}{tail}"
    return s


def _norm_dmy_simple(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        y, m, d = s.split("-")
        return f"{d}/{m}/{y}"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
    if m:
        y = ("20" + m.group(3)) if len(m.group(3)) == 2 else m.group(3)
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{y}"
    return s


def _parse_moneda_simple(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).replace("S/", "").replace("s/", "").replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def _celda_doc(v: Any) -> str:
    s = str(v or "").strip()
    return f"'{s}" if s.startswith("0") else s


def _get_spreadsheet_v2():
    global _gspread_client_v2, _spreadsheet_v2
    if MODO_PRUEBA:
        raise RuntimeError("MODO PRUEBA: el acceso a Google Sheets está bloqueado.")
    if _spreadsheet_v2 is not None and getattr(_spreadsheet_v2, "id", None) == SPREADSHEET_ID:
        return _spreadsheet_v2
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = obtener_ruta_credenciales()
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    _gspread_client_v2 = gspread.authorize(creds)
    try:
        _gspread_client_v2.set_timeout(25)
    except Exception:
        pass
    _spreadsheet_v2 = _gspread_client_v2.open_by_key(SPREADSHEET_ID)
    return _spreadsheet_v2


def _reset_spreadsheet_v2():
    global _gspread_client_v2, _spreadsheet_v2
    _gspread_client_v2 = None
    _spreadsheet_v2 = None


def _resolver_fila_en_col_a(col_a: List[str], sheet_row_hint: int, target_id: str) -> int:
    base_id = str(target_id or "").split(" #")[0].strip()
    canon_t = _canon_ts_id_sheets(base_id)
    if not canon_t:
        return 0
    if 2 <= sheet_row_hint <= len(col_a) and _canon_ts_id_sheets(col_a[sheet_row_hint - 1]) == canon_t:
        return sheet_row_hint
    matches = [idx + 1 for idx in range(1, len(col_a)) if _canon_ts_id_sheets(col_a[idx]) == canon_t]
    if not matches:
        return 0
    if sheet_row_hint and sheet_row_hint >= 2:
        return min(matches, key=lambda r: abs(r - sheet_row_hint))
    return matches[-1]


def _resolver_fila_tienda_vals(vals_ac: List[List[Any]], sheet_row_hint: int, fecha_iso: str, producto_prev: str = "") -> int:
    canon_f = _canon_ts_id_sheets(str(fecha_iso or "").split(" #")[0])
    prod_norm = str(producto_prev or "").strip().lower()

    def _match_row(idx0: int, strict_prod: bool) -> bool:
        if idx0 < 1 or idx0 >= len(vals_ac):
            return False
        r = vals_ac[idx0]
        c_a = _canon_ts_id_sheets(r[0] if len(r) > 0 else "")
        if canon_f and c_a != canon_f:
            return False
        if strict_prod and prod_norm:
            c_prod = str(r[2] if len(r) > 2 else "").strip().lower()
            if c_prod != prod_norm:
                return False
        return bool(canon_f or prod_norm)

    if 2 <= sheet_row_hint <= len(vals_ac) and _match_row(sheet_row_hint - 1, True):
        return sheet_row_hint
    matches = [i + 1 for i in range(1, len(vals_ac)) if _match_row(i, True)]
    if not matches and prod_norm and canon_f:
        if 2 <= sheet_row_hint <= len(vals_ac) and _match_row(sheet_row_hint - 1, False):
            return sheet_row_hint
        matches = [i + 1 for i in range(1, len(vals_ac)) if _match_row(i, False)]
    if not matches:
        return 0
    if sheet_row_hint and sheet_row_hint >= 2:
        return min(matches, key=lambda r: abs(r - sheet_row_hint))
    return matches[-1]


def _bloques_estancia_sheets(r: Dict[str, Any]) -> Dict[str, List[Any]]:
    m_fact = r.get("monto_factura")
    m_fact_cell = round(float(m_fact), 2) if (m_fact is not None and m_fact != "" and float(m_fact or 0) > 0) else ""
    exc_val = float(r.get("excedente_efec_sunat") or 0)
    exc_cell = round(exc_val, 2) if exc_val > 0 else ""
    snacks_val = float(r.get("adic_snacks") or 0)
    snacks_cell = round(snacks_val, 2) if snacks_val > 0 else ""

    b1_ao = [
        str(r.get("id_timestamp_str") or ""),
        str(r.get("fecha_in_dmy") or ""),
        str(r.get("fecha_out_dmy") or ""),
        str(r.get("huesped") or ""),
        _celda_doc(r.get("dni")),
        str(r.get("sexo") or "M"),
        str(r.get("n_acomp") or ""),
        _celda_doc(r.get("dni_acomp")),
        str(r.get("nac") or "Perú"),
        str(r.get("reg") or "Lima"),
        str(r.get("mot") or "Vacaciones, recreo u ocio"),
        str(r.get("hab") or ""),
        str(r.get("tipo_hab") or ""),
        str(r.get("visita") or "Visita N° 1"),
        str(r.get("hora_registro") or ""),
    ]
    return {
        "ao": b1_ao,
        "bo": b1_ao[1:],
        "q": [round(float(r.get("tarifa") or 0), 2)],
        "su": [snacks_cell, round(float(r.get("adic_servicios") or 0), 2), round(float(r.get("adic_comp") or 0), 2)],
        "wz": [
            round(float(r.get("pago_ini_efec") or 0), 2),
            round(float(r.get("pago_ini_yape") or 0), 2),
            round(float(r.get("pago_post_efec") or 0), 2),
            round(float(r.get("pago_post_yape") or 0), 2),
        ],
        "aeam": [
            str(r.get("cuenta_destino") or ""),
            str(r.get("solicita_comp") or "No"),
            str(r.get("tipo_comp") or ""),
            str(r.get("estado_comp") or ""),
            str(r.get("nro_comp") or ""),
            m_fact_cell,
            str(r.get("estado_estancia") or "En Curso"),
            round(float(r.get("deuda_anterior") or 0), 2),
            round(float(r.get("pago_visa") or 0), 2),
        ],
        "aqba": [
            str(r.get("empresa") or ""),
            str(r.get("proyecto") or ""),
            str(r.get("triple1") or ""),
            _celda_doc(r.get("triple2")),
            str(r.get("hotel_apoyo") or ""),
            str(r.get("observaciones") or ""),
            str(r.get("nro_op_yape") or ""),
            str(r.get("celular") or ""),
            str(r.get("ruc") or ""),
            exc_cell,
            str(r.get("tipo_adicional") or ""),
        ],
    }


def sincronizar_espejo_sheets(solo_recientes: bool = False, forzar: bool = False) -> dict:
    """
    Espejo UNIDIRECCIONAL SQLite -> Google Sheets (2026_Hostal Óvalo Mariategui).
    Jamás lee de Sheets para sobrescribir SQLite y protege el 100% de las fórmulas =MAP(...)
    (columnas P, R, V, AA, AB, AC, AD, AN, AO) y el formato de la Fila 2.
    """
    if MODO_PRUEBA:
        return {"ok": False, "status": "MODO_PRUEBA"}
    if not _sheets_lock.acquire(blocking=False):
        return {"ok": True, "status": "BUSY"}

    try:
        conn_chk = get_connection()
        try:
            has_sq = bool(
                conn_chk.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_queue'"
                ).fetchone()
            )
            queue_rows = (
                [
                    dict(r)
                    for r in conn_chk.execute(
                        "SELECT * FROM sync_queue WHERE status IN ('PENDING', 'ERROR') ORDER BY queue_id ASC LIMIT 120"
                    ).fetchall()
                ]
                if has_sq
                else []
            )
            est_dirty = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM estancias WHERE dirty = 1 OR sheet_row = 0 ORDER BY ymd_in ASC, rowid ASC"
                ).fetchall()
            ]
            pagos_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM pagos WHERE synced = 0 OR sheet_row = 0 ORDER BY rowid ASC"
                ).fetchall()
            ]
            tienda_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM ventas_tienda WHERE synced = 0 OR sheet_row = 0 ORDER BY rowid ASC"
                ).fetchall()
            ]
            gastos_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM gastos WHERE synced = 0 OR sheet_row = 0 ORDER BY rowid ASC"
                ).fetchall()
            ]
            hor_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM horario_registros WHERE synced = 0"
                ).fetchall()
            ] if "synced" in {c[1] for c in conn_chk.execute("PRAGMA table_info(horario_registros)").fetchall()} else []
            hor_aud_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM horario_auditoria WHERE synced = 0"
                ).fetchall()
            ] if "synced" in {c[1] for c in conn_chk.execute("PRAGMA table_info(horario_auditoria)").fetchall()} else []
            pases_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM control_pases WHERE synced = 0"
                ).fetchall()
            ] if "synced" in {c[1] for c in conn_chk.execute("PRAGMA table_info(control_pases)").fetchall()} else []
            alm_pend = [
                dict(r)
                for r in conn_chk.execute(
                    "SELECT * FROM almacen_movimientos WHERE synced = 0"
                ).fetchall()
            ] if "synced" in {c[1] for c in conn_chk.execute("PRAGMA table_info(almacen_movimientos)").fetchall()} else []
        finally:
            conn_chk.close()

        hay_pendientes = bool(
            queue_rows
            or est_dirty
            or pagos_pend
            or tienda_pend
            or gastos_pend
            or hor_pend
            or hor_aud_pend
            or pases_pend
            or alm_pend
        )
        if not hay_pendientes and not forzar:
            return {"ok": True, "status": "UP_TO_DATE", "estancias_sync": 0, "pagos_sync": 0, "tienda_sync": 0, "gastos_sync": 0}

        sh = _get_spreadsheet_v2()
        ws_bd = None
        ws_pagos = None
        ws_tienda = None
        ws_gastos = None
        col_a_bd: List[str] = []

        def _get_ws_bd():
            nonlocal ws_bd, col_a_bd
            if ws_bd is None:
                ws_bd = sh.worksheet("Registro_Estancias")
                col_a_bd = ws_bd.col_values(1) or []
            return ws_bd

        def _get_ws_pagos():
            nonlocal ws_pagos
            if ws_pagos is None:
                ws_pagos = sh.worksheet("Registro_Pagos")
            return ws_pagos

        def _get_ws_tienda():
            nonlocal ws_tienda
            if ws_tienda is None:
                ws_tienda = sh.worksheet("2.Registro_Ventas_Tienda")
            return ws_tienda

        def _get_ws_gastos():
            nonlocal ws_gastos
            if ws_gastos is None:
                ws_gastos = sh.worksheet("Registro_Gastos")
            return ws_gastos

        qids_ok: List[int] = []
        errores_q: List[str] = []
        especiales = {
            "ELIMINAR_ESTANCIA_ADMIN",
            "ELIMINAR_PAGO_ADMIN",
            "ELIMINAR_GASTO_ADMIN",
            "ELIMINAR_VENTA_TIENDA_ADMIN",
            "EDITAR_PAGO_ADMIN",
            "EDITAR_GASTO_ADMIN",
            "EDITAR_VENTA_TIENDA_ADMIN",
            "GUARDAR_CONTEO_VITRINA",
            "ELIMINAR_MOVIMIENTO_ALMACEN",
            "PASE_DELETE",
        }

        # FASE A: Operaciones especiales de sync_queue (borrados, ediciones puntuales o vitrina)
        for q in queue_rows:
            qid = int(q["queue_id"])
            op_type = str(q.get("op_type") or "")
            if op_type not in especiales:
                qids_ok.append(qid)
                continue
            try:
                payload = json.loads(q.get("payload_json") or "{}")
                if op_type == "ELIMINAR_ESTANCIA_ADMIN":
                    ws = _get_ws_bd()
                    r_e = _resolver_fila_en_col_a(col_a_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or "")
                    if r_e >= 3:
                        ws.delete_rows(r_e)
                        col_a_bd = ws.col_values(1) or []
                    elif r_e == 2:
                        ws.batch_clear(["A2:O2", "Q2", "S2:U2", "W2:Z2", "AE2:AM2", "AQ2:BA2"])
                        col_a_bd = ws.col_values(1) or []
                    pagos_vinc = payload.get("pagos_vinculados") or []
                    if pagos_vinc:
                        wp = _get_ws_pagos()
                        col_ap = wp.col_values(1) or []
                        filas_p_del = set()
                        for pv in pagos_vinc:
                            rp = _resolver_fila_en_col_a(col_ap, int(pv.get("sheet_row") or 0), pv.get("pago_id") or "")
                            if rp >= 2:
                                filas_p_del.add(rp)
                        for rp_d in sorted(filas_p_del, reverse=True):
                            wp.delete_rows(rp_d)

                elif op_type == "EDITAR_PAGO_ADMIN":
                    wp = _get_ws_pagos()
                    col_ap = wp.col_values(1) or []
                    rp = _resolver_fila_en_col_a(col_ap, int(payload.get("sheet_row") or 0), payload.get("pago_id") or "")
                    if rp >= 2:
                        wp.update(range_name=f"B{rp}:J{rp}", values=[payload["fila_bj"]], value_input_option="USER_ENTERED")

                elif op_type == "ELIMINAR_PAGO_ADMIN":
                    wp = _get_ws_pagos()
                    col_ap = wp.col_values(1) or []
                    rp = _resolver_fila_en_col_a(col_ap, int(payload.get("sheet_row") or 0), payload.get("pago_id") or "")
                    if rp >= 2:
                        wp.delete_rows(rp)

                elif op_type == "EDITAR_GASTO_ADMIN":
                    wg = _get_ws_gastos()
                    col_ag = wg.col_values(1) or []
                    rg = _resolver_fila_en_col_a(col_ag, int(payload.get("sheet_row") or 0), payload.get("id_ts_str") or "")
                    if rg >= 2:
                        wg.update(range_name=f"B{rg}:J{rg}", values=[payload["fila_bj"]], value_input_option="USER_ENTERED")

                elif op_type == "ELIMINAR_GASTO_ADMIN":
                    wg = _get_ws_gastos()
                    col_ag = wg.col_values(1) or []
                    rg = _resolver_fila_en_col_a(col_ag, int(payload.get("sheet_row") or 0), payload.get("id_ts_str") or "")
                    if rg >= 2:
                        wg.delete_rows(rg)

                elif op_type == "EDITAR_VENTA_TIENDA_ADMIN":
                    wt = _get_ws_tienda()
                    vals_ac = wt.get("A1:C") or []
                    rt = _resolver_fila_tienda_vals(vals_ac, int(payload.get("sheet_row") or 0), payload.get("fecha_iso") or "", payload.get("producto_prev") or "")
                    if rt >= 2:
                        wt.batch_update(
                            [
                                {"range": f"B{rt}:I{rt}", "values": [payload["fila_bi"]]},
                                {"range": f"L{rt}", "values": [[payload.get("nro_op_yape") or ""]]},
                            ],
                            value_input_option="USER_ENTERED",
                        )

                elif op_type == "ELIMINAR_VENTA_TIENDA_ADMIN":
                    wt = _get_ws_tienda()
                    vals_ac = wt.get("A1:C") or []
                    rt = _resolver_fila_tienda_vals(vals_ac, int(payload.get("sheet_row") or 0), payload.get("fecha_iso") or "", payload.get("producto_prev") or "")
                    if rt >= 2:
                        wt.delete_rows(rt)

                elif op_type == "GUARDAR_CONTEO_VITRINA":
                    ws_vit = sh.worksheet("Vitrina_Tienda")
                    responsable_v = str(payload.get("responsable") or "").strip()
                    fecha_hora_v = str(payload.get("fecha_hora_dmy") or "").strip()
                    hdr_conteo = f"Conteo:\n{responsable_v}\n{fecha_hora_v}"
                    hdr_estado = f"Estado:\n{responsable_v}\n{fecha_hora_v}"
                    mapa_items = payload.get("items_map") or {}
                    actual_a_i = ws_vit.get("A4:I45") or []
                    h4_actual = str(actual_a_i[0][7] or "").strip() if (len(actual_a_i) >= 1 and len(actual_a_i[0]) >= 8) else ""
                    if h4_actual != hdr_conteo.strip():
                        filas_prod = actual_a_i[2:] if len(actual_a_i) >= 3 else []
                        num_f = len(filas_prod)
                        if num_f > 0:
                            vals_hi, vals_aviso, vals_clr = [], [], []
                            for r_p in filas_prod:
                                nom_p = str(r_p[0] or "").strip() if len(r_p) > 0 else ""
                                it_p = mapa_items.get(nom_p)
                                if nom_p and it_p and str(it_p.get("cant", "")).strip() != "":
                                    try:
                                        cn = float(it_p["cant"])
                                        cc = int(cn) if cn.is_integer() else cn
                                    except Exception:
                                        cc = it_p["cant"]
                                    vals_hi.append([cc, str(it_p.get("estado") or "")])
                                    vals_aviso.append([str(it_p.get("estado") or "")])
                                else:
                                    vals_hi.append(["", ""])
                                    vals_aviso.append([""])
                                vals_clr.append([""])
                            ws_vit.insert_cols([[], []], col=8)
                            last_rv = 5 + num_f
                            ws_vit.batch_update(
                                [
                                    {"range": "E2", "values": [[responsable_v]]},
                                    {"range": "E3", "values": [[fecha_hora_v]]},
                                    {"range": f"D6:D{last_rv}", "values": vals_clr},
                                    {"range": f"G6:G{last_rv}", "values": vals_aviso},
                                    {"range": "H4:I4", "values": [[hdr_conteo, hdr_estado]]},
                                    {"range": f"H6:I{last_rv}", "values": vals_hi},
                                ],
                                value_input_option="USER_ENTERED",
                            )

                elif op_type == "ELIMINAR_MOVIMIENTO_ALMACEN":
                    ws_alm = sh.worksheet("import_Almacen")
                    m_u_del = str(payload.get("mov_uuid") or "").strip()
                    col_j = ws_alm.col_values(10) or []
                    for idx_a, cell_u in enumerate(col_j[1:], start=2):
                        if m_u_del and str(cell_u).strip() == m_u_del:
                            ws_alm.delete_rows(idx_a)
                            break

                elif op_type == "PASE_DELETE":
                    try:
                        ws_p = sh.worksheet("import_Control_Pases")
                        col_ap = ws_p.col_values(1) or []
                        pu = str(payload.get("pase_uuid") or "").strip()
                        for idx_p, vp in enumerate(col_ap[1:], start=2):
                            if str(vp).strip() == pu:
                                ws_p.delete_rows(idx_p)
                                break
                    except Exception:
                        pass

                qids_ok.append(qid)
            except Exception as q_err:
                errores_q.append(f"{op_type} #{qid}: {str(q_err)[:120]}")

        # FASE B: Reconciliación Delta SQLite -> Google Sheets (100% segura con =MAP)
        estancias_updates_db: List[Tuple[int, str, str]] = []
        estancias_upd_count = 0
        estancias_ins_count = 0

        if est_dirty:
            ws = _get_ws_bd()
            sh_est_map = {
                _canon_ts_id_sheets(v): idx + 1
                for idx, v in enumerate(col_a_bd[1:], start=1)
                if str(v or "").strip()
            }
            nuevas_est: List[Tuple[Dict[str, Any], Dict[str, List[Any]]]] = []
            rangos_upd: List[Dict[str, Any]] = []

            for r in est_dirty:
                c_id = _canon_ts_id_sheets(r.get("id_timestamp_str"))
                blq = _bloques_estancia_sheets(r)
                t_row = sh_est_map.get(c_id, 0) if c_id else 0
                if t_row >= 2:
                    if int(r.get("dirty") or 0) == 1:
                        rangos_upd.extend([
                            {"range": f"B{t_row}:O{t_row}", "values": [blq["bo"]]},
                            {"range": f"Q{t_row}", "values": [blq["q"]]},
                            {"range": f"S{t_row}:U{t_row}", "values": [blq["su"]]},
                            {"range": f"W{t_row}:Z{t_row}", "values": [blq["wz"]]},
                            {"range": f"AE{t_row}:AM{t_row}", "values": [blq["aeam"]]},
                            {"range": f"AQ{t_row}:BA{t_row}", "values": [blq["aqba"]]},
                        ])
                        estancias_upd_count += 1
                    estancias_updates_db.append((t_row, str(r["estancia_uuid"]), str(r.get("updated_at") or "")))
                elif c_id:
                    nuevas_est.append((r, blq))

            for k_chunk in range(0, len(rangos_upd), 60):
                ws.batch_update(rangos_upd[k_chunk : k_chunk + 60], value_input_option="USER_ENTERED")

            if nuevas_est:
                next_r = max(2, len(col_a_bd) + 1)
                end_r = next_r + len(nuevas_est) - 1
                if end_r > ws.row_count:
                    ws.add_rows(max(100, end_r - ws.row_count + 50))
                ws.batch_update(
                    [
                        {"range": f"A{next_r}:O{end_r}", "values": [b["ao"] for _, b in nuevas_est]},
                        {"range": f"Q{next_r}:Q{end_r}", "values": [b["q"] for _, b in nuevas_est]},
                        {"range": f"S{next_r}:U{end_r}", "values": [b["su"] for _, b in nuevas_est]},
                        {"range": f"W{next_r}:Z{end_r}", "values": [b["wz"] for _, b in nuevas_est]},
                        {"range": f"AE{next_r}:AM{end_r}", "values": [b["aeam"] for _, b in nuevas_est]},
                        {"range": f"AQ{next_r}:BA{end_r}", "values": [b["aqba"] for _, b in nuevas_est]},
                    ],
                    value_input_option="USER_ENTERED",
                )
                for off, (r_est, _) in enumerate(nuevas_est):
                    estancias_updates_db.append((next_r + off, str(r_est["estancia_uuid"]), str(r_est.get("updated_at") or "")))
                estancias_ins_count = len(nuevas_est)
                col_a_bd = ws.col_values(1) or col_a_bd

        # PAGOS -> Registro_Pagos
        pagos_updates_db: List[Tuple[int, str]] = []
        pagos_ins_count = 0
        if pagos_pend:
            wp = _get_ws_pagos()
            col_ap = wp.col_values(1) or []
            sh_p_map = {str(v).strip(): i + 1 for i, v in enumerate(col_ap[1:], start=1) if str(v or "").strip()}
            nuevos_p = []
            for p in pagos_pend:
                pid = str(p.get("pago_id") or "").strip()
                if not pid:
                    continue
                if pid in sh_p_map:
                    pagos_updates_db.append((sh_p_map[pid], pid))
                else:
                    nuevos_p.append((
                        pid,
                        [
                            pid,
                            str(p.get("fecha_iso") or ""),
                            str(p.get("id_estancia") or ""),
                            str(p.get("hab") or ""),
                            str(p.get("huesped") or ""),
                            round(float(p.get("monto") or 0), 2),
                            str(p.get("medio_pago") or ""),
                            str(p.get("tipo_pago") or ""),
                            str(p.get("concepto") or ""),
                            str(p.get("nro_operacion") or ""),
                        ],
                    ))
            if nuevos_p:
                next_p = max(2, len(col_ap) + 1)
                end_p = next_p + len(nuevos_p) - 1
                if end_p > wp.row_count:
                    wp.add_rows(max(100, end_p - wp.row_count + 50))
                wp.update(
                    range_name=f"A{next_p}:J{end_p}",
                    values=[fila for _, fila in nuevos_p],
                    value_input_option="USER_ENTERED",
                )
                for off, (pid, _) in enumerate(nuevos_p):
                    pagos_updates_db.append((next_p + off, pid))
                pagos_ins_count = len(nuevos_p)

        # VENTAS TIENDA -> 2.Registro_Ventas_Tienda
        tienda_updates_db: List[Tuple[int, str]] = []
        tienda_ins_count = 0
        if tienda_pend:
            wt = _get_ws_tienda()
            vals_ac = wt.get("A1:C") or []
            sh_t_map: Dict[Tuple[str, str], int] = {}
            for i_t, rt_v in enumerate(vals_ac[1:], start=2):
                ca_t = _canon_ts_id_sheets(rt_v[0] if len(rt_v) > 0 else "")
                cp_t = str(rt_v[2] if len(rt_v) > 2 else "").strip().lower()
                if ca_t:
                    sh_t_map[(ca_t, cp_t)] = i_t
            nuevas_t = []
            for vt in tienda_pend:
                v_u = str(vt.get("venta_uuid") or "").strip()
                k_t = (_canon_ts_id_sheets(vt.get("fecha_iso")), str(vt.get("producto") or "").strip().lower())
                if k_t in sh_t_map:
                    tienda_updates_db.append((sh_t_map[k_t], v_u))
                else:
                    nuevas_t.append((
                        v_u,
                        [
                            str(vt.get("fecha_iso") or ""),
                            str(vt.get("fecha_dmy") or ""),
                            str(vt.get("producto") or ""),
                            int(float(vt.get("cantidad") or 1)),
                            str(vt.get("metodo_pago") or ""),
                            str(vt.get("hab") or ""),
                            str(vt.get("personal") or ""),
                            round(float(vt.get("precio_unit") or 0), 2),
                            round(float(vt.get("subtotal") or 0), 2),
                            str(vt.get("id_estancia") or ""),
                            "",
                            str(vt.get("nro_op_yape") or ""),
                        ],
                    ))
            if nuevas_t:
                next_t = max(2, len(vals_ac) + 1)
                end_t = next_t + len(nuevas_t) - 1
                if end_t > wt.row_count:
                    wt.add_rows(max(100, end_t - wt.row_count + 50))
                wt.update(
                    range_name=f"A{next_t}:L{end_t}",
                    values=[fila for _, fila in nuevas_t],
                    value_input_option="USER_ENTERED",
                )
                for off, (v_u, _) in enumerate(nuevas_t):
                    tienda_updates_db.append((next_t + off, v_u))
                tienda_ins_count = len(nuevas_t)

        # GASTOS -> Registro_Gastos
        gastos_updates_db: List[Tuple[int, str]] = []
        gastos_ins_count = 0
        if gastos_pend:
            wg = _get_ws_gastos()
            col_ag = wg.col_values(1) or []
            sh_g_map = {
                _canon_ts_id_sheets(v): i + 1
                for i, v in enumerate(col_ag[1:], start=1)
                if str(v or "").strip()
            }
            nuevos_g = []
            for g in gastos_pend:
                g_u = str(g.get("gasto_uuid") or "").strip()
                id_g = str(g.get("id_ts_str") or "").strip()
                c_g = _canon_ts_id_sheets(id_g)
                if c_g and c_g in sh_g_map:
                    gastos_updates_db.append((sh_g_map[c_g], g_u))
                else:
                    nuevos_g.append((
                        g_u,
                        [
                            id_g or _norm_dmy_simple(g.get("fecha_gasto_dmy")),
                            str(g.get("fecha_gasto_dmy") or ""),
                            str(g.get("descripcion") or ""),
                            round(float(g.get("monto") or 0), 2),
                            str(g.get("categoria") or ""),
                            str(g.get("metodo_pago") or ""),
                            str(g.get("tipo_comp") or ""),
                            str(g.get("nro_comp") or ""),
                            str(g.get("responsable") or "Admin"),
                            str(g.get("personal") or ""),
                        ],
                    ))
            if nuevos_g:
                next_g = max(2, len(col_ag) + 1)
                end_g = next_g + len(nuevos_g) - 1
                if end_g > wg.row_count:
                    wg.add_rows(max(100, end_g - wg.row_count + 50))
                wg.update(
                    range_name=f"A{next_g}:J{end_g}",
                    values=[fila for _, fila in nuevos_g],
                    value_input_option="USER_ENTERED",
                )
                for off, (g_u, _) in enumerate(nuevos_g):
                    gastos_updates_db.append((next_g + off, g_u))
                gastos_ins_count = len(nuevos_g)

        # HORARIO / AUDITORIA / PASES / ALMACEN pendientes (synced=0)
        hor_done: List[Tuple[str, str]] = []
        if hor_pend:
            try:
                ws_h = sh.worksheet("import_Horario")
            except Exception:
                ws_h = sh.add_worksheet(title="import_Horario", rows=2000, cols=12)
                ws_h.update(range_name="A1", values=[["Empleado", "Fecha", "Entrada 1", "Salida 1", "Entrada 2", "Salida 2", "Estado (F)", "Horas extras (G)", "Extra nocturno", "Nota", "Actualizado", "Equipo"]], value_input_option="USER_ENTERED")
            vals_ab_h = ws_h.get("A2:B8000") or []
            idx_h = {(str(x[0]).strip(), _norm_dmy_simple(x[1]) if len(x) > 1 else ""): i + 2 for i, x in enumerate(vals_ab_h) if x and str(x[0]).strip()}
            next_h = len(vals_ab_h) + 2
            for hr in hor_pend:
                k_h = (str(hr.get("empleado") or "").strip(), _norm_dmy_simple(hr.get("fecha_dmy")))
                fila_h = [k_h[0], k_h[1], str(hr.get("e1") or ""), str(hr.get("s1") or ""), str(hr.get("e2") or ""), str(hr.get("s2") or ""), str(hr.get("estado") or ""), str(hr.get("extras") or ""), str(hr.get("extra_noche") or 0), str(hr.get("nota") or ""), str(hr.get("updated_at") or ""), str(hr.get("equipo") or "")]
                r_h = idx_h.get(k_h)
                if not r_h:
                    r_h = next_h
                    next_h += 1
                    idx_h[k_h] = r_h
                    if r_h > ws_h.row_count:
                        ws_h.add_rows(200)
                ws_h.update(range_name=f"A{r_h}:L{r_h}", values=[fila_h], value_input_option="RAW")
                hor_done.append(k_h)

        hor_aud_done: List[str] = []
        if hor_aud_pend:
            try:
                ws_ha = sh.worksheet("import_Horario_Audit")
            except Exception:
                ws_ha = sh.add_worksheet(title="import_Horario_Audit", rows=2000, cols=8)
                ws_ha.update(range_name="A1", values=[["ID", "Empleado", "Fecha turno", "Campo", "Valor anterior", "Valor nuevo", "Editado el", "Equipo"]], value_input_option="USER_ENTERED")
            col_aha = {str(v).strip() for v in (ws_ha.col_values(1) or [])[1:] if str(v).strip()}
            nuevas_ha = []
            for ar in hor_aud_pend:
                au = str(ar.get("audit_uuid") or "").strip()
                if au and au not in col_aha:
                    nuevas_ha.append([au, str(ar.get("empleado") or ""), str(ar.get("fecha_dmy") or ""), str(ar.get("campo") or ""), str(ar.get("valor_anterior") or ""), str(ar.get("valor_nuevo") or ""), str(ar.get("editado_ts") or ""), str(ar.get("equipo") or "")])
                if au:
                    hor_aud_done.append(au)
            if nuevas_ha:
                nr_ha = max(2, len(ws_ha.col_values(1) or []) + 1)
                if nr_ha + len(nuevas_ha) > ws_ha.row_count:
                    ws_ha.add_rows(200)
                ws_ha.update(range_name=f"A{nr_ha}:H{nr_ha + len(nuevas_ha) - 1}", values=nuevas_ha, value_input_option="RAW")

        pases_done: List[str] = []
        if pases_pend:
            try:
                ws_p = sh.worksheet("import_Control_Pases")
                col_ap_p = ws_p.col_values(1) or []
                map_p = {str(v).strip(): i + 1 for i, v in enumerate(col_ap_p[1:], start=1) if str(v).strip()}
                next_pr = max(2, len(col_ap_p) + 1)
                for pr in pases_pend:
                    pu = str(pr.get("pase_uuid") or "").strip()
                    if not pu:
                        continue
                    fila_p = [pu, str(pr.get("fecha_dmy") or ""), str(pr.get("hora") or ""), str(pr.get("hab") or ""), str(pr.get("estancia_uuid") or ""), str(pr.get("id_estancia") or ""), str(pr.get("huesped_titular") or ""), str(pr.get("dni_visita") or ""), str(pr.get("nombre_visita") or ""), str(pr.get("estado") or ""), float(pr.get("monto") or 0), str(pr.get("motivo_rechazo") or ""), str(pr.get("equipo") or ""), str(pr.get("created_at") or "")]
                    rp_i = map_p.get(pu)
                    if not rp_i:
                        rp_i = next_pr
                        next_pr += 1
                    ws_p.update(range_name=f"A{rp_i}:N{rp_i}", values=[fila_p], value_input_option="RAW")
                    pases_done.append(pu)
            except Exception:
                pass

        alm_updates_db: List[Tuple[int, str]] = []
        if alm_pend:
            try:
                ws_alm = sh.worksheet("import_Almacen")
                col_j = ws_alm.col_values(10) or []
                col_a_a = ws_alm.col_values(1) or []
                map_alm = {str(v).strip(): i + 1 for i, v in enumerate(col_j[1:], start=1) if str(v).strip()}
                nuevos_am = []
                for am in alm_pend:
                    mu = str(am.get("mov_uuid") or "").strip()
                    if not mu:
                        continue
                    if mu in map_alm:
                        alm_updates_db.append((map_alm[mu], mu))
                    else:
                        nuevos_am.append((mu, [str(am.get("id_transaccion") or ""), str(am.get("fecha_dmy") or ""), str(am.get("producto") or ""), float(am.get("cantidad") or 0), float(am.get("costo_total") or 0), float(am.get("costo_unitario") or 0), str(am.get("metodo_pago") or ""), str(am.get("tipo_movimiento") or ""), str(am.get("nota") or ""), mu]))
                if nuevos_am:
                    next_am = max(2, len(col_a_a) + 1, len(col_j) + 1)
                    end_am = next_am + len(nuevos_am) - 1
                    if end_am > ws_alm.row_count:
                        ws_alm.add_rows(max(100, end_am - ws_alm.row_count + 20))
                    ws_alm.update(range_name=f"A{next_am}:J{end_am}", values=[fl for _, fl in nuevos_am], value_input_option="USER_ENTERED")
                    for off, (mu, _) in enumerate(nuevos_am):
                        alm_updates_db.append((next_am + off, mu))
            except Exception:
                pass

        # Leer siempre caja_sheets_g1 actualizado desde Panel_Disponibilidad!G1
        caja_g1_txt = None
        try:
            ws_disp = sh.worksheet("Panel_Disponibilidad")
            caja_g1_txt = f"{_parse_moneda_simple(ws_disp.acell('G1').value):.2f}"
        except Exception:
            pass

        # FASE C: Actualizar SQLite cleanly
        ahora_iso = datetime.now().isoformat()
        ahora_hms = datetime.now().strftime("%H:%M:%S")
        conn_fin = get_connection()
        try:
            for s_row_e, e_uuid, snap_upd_at in estancias_updates_db:
                conn_fin.execute(
                    """
                    UPDATE estancias
                    SET sheet_row = ?,
                        dirty = CASE WHEN COALESCE(updated_at, '') = ? THEN 0 ELSE dirty END
                    WHERE estancia_uuid = ? AND (sheet_row != ? OR dirty != 0)
                    """,
                    (s_row_e, snap_upd_at, e_uuid, s_row_e),
                )
            for s_row_p, p_id in pagos_updates_db:
                conn_fin.execute(
                    "UPDATE pagos SET sheet_row = ?, synced = 1 WHERE pago_id = ? AND (sheet_row != ? OR synced != 1)",
                    (s_row_p, p_id, s_row_p),
                )
            for s_row_t, v_u in tienda_updates_db:
                conn_fin.execute(
                    "UPDATE ventas_tienda SET sheet_row = ?, synced = 1 WHERE venta_uuid = ? AND (sheet_row != ? OR synced != 1)",
                    (s_row_t, v_u, s_row_t),
                )
            for s_row_g, g_u in gastos_updates_db:
                conn_fin.execute(
                    "UPDATE gastos SET sheet_row = ?, synced = 1 WHERE gasto_uuid = ? AND (sheet_row != ? OR synced != 1)",
                    (s_row_g, g_u, s_row_g),
                )
            for emp_h, f_h in hor_done:
                conn_fin.execute("UPDATE horario_registros SET synced = 1 WHERE empleado = ? AND fecha_dmy = ?", (emp_h, f_h))
            for au in hor_aud_done:
                conn_fin.execute("UPDATE horario_auditoria SET synced = 1 WHERE audit_uuid = ?", (au,))
            for pu in pases_done:
                conn_fin.execute("UPDATE control_pases SET synced = 1 WHERE pase_uuid = ?", (pu,))
            for s_row_am, mu in alm_updates_db:
                conn_fin.execute("UPDATE almacen_movimientos SET sheet_row = ?, synced = 1 WHERE mov_uuid = ?", (s_row_am, mu))

            if qids_ok:
                ph_q = ", ".join("?" for _ in qids_ok)
                conn_fin.execute(
                    f"UPDATE sync_queue SET status = 'SYNCED', synced_at = ?, last_error = '' WHERE queue_id IN ({ph_q})",
                    tuple([ahora_iso] + qids_ok),
                )

            # Guardar marcas de estado local sin inundar _supabase_outbox
            conn_fin.execute("UPDATE _supabase_sync_control SET pulling = 1 WHERE id = 1")
            pares_estado = [
                ("network_online", "1"),
                ("last_sync_ts", ahora_hms),
                ("last_successful_pull_iso", ahora_iso),
                ("offline_since_iso", ""),
                ("last_sync_error", "; ".join(errores_q)[:240] if errores_q else ""),
                ("last_sheets_mirror_ts", ahora_iso),
            ]
            for cl_e, vl_e in pares_estado:
                conn_fin.execute(
                    "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                    (cl_e, vl_e, ahora_iso),
                )
            conn_fin.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")

            # Guardar caja_sheets_g1 y last_row_estancias con pulling=0 (solo si cambiaron) para que el trigger los suba a Supabase
            pares_compartidos = []
            if caja_g1_txt is not None:
                pares_compartidos.append(("caja_sheets_g1", caja_g1_txt))
            if col_a_bd:
                pares_compartidos.append(("last_row_estancias", str(len(col_a_bd))))
            for cl_s, vl_s in pares_compartidos:
                prev_row = conn_fin.execute("SELECT valor FROM estado_sistema WHERE clave = ?", (cl_s,)).fetchone()
                if not prev_row or str(prev_row[0]) != str(vl_s):
                    conn_fin.execute(
                        "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                        (cl_s, str(vl_s), ahora_iso),
                    )
            conn_fin.commit()
        finally:
            try:
                conn_fin.execute("UPDATE _supabase_sync_control SET pulling = 0 WHERE id = 1")
                conn_fin.commit()
            except Exception:
                pass
            conn_fin.close()

        return {
            "ok": len(errores_q) == 0,
            "status": "OK",
            "estancias_insertadas": estancias_ins_count,
            "estancias_actualizadas": estancias_upd_count,
            "pagos_sync": pagos_ins_count,
            "tienda_sync": tienda_ins_count,
            "gastos_sync": gastos_ins_count,
            "errores": errores_q,
        }
    except Exception as exc:
        _reset_spreadsheet_v2()
        return {"ok": False, "status": "ERROR", "error": str(exc)}
    finally:
        try:
            _sheets_lock.release()
        except RuntimeError:
            pass


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
    instalar_triggers_outbox()
    boot = bootstrap_initial_from_supabase()
    up = push_to_supabase()
    down = pull_from_supabase()
    if "--sync" in sys.argv:
        print(json.dumps({"bootstrap": boot, "push": up, "pull": down}, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({**diagnostico_fase2(), "bootstrap": boot, "pull": down}, indent=2, ensure_ascii=False))
