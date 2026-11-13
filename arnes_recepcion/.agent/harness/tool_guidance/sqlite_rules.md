# Guía de Ejecución: SQLite Local & Sincronización en Tiempo Real

## Configuración Obligatoria de Conexión
```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 15000;
PRAGMA synchronous = NORMAL;
```
En scripts de inspección o sondas de monitoreo, utilizar `PRAGMA busy_timeout = 5000;` para evitar contención prolongada.

## Patrón de Bloqueo Cero (0 ms Lock)
- **Principio Fundamental:** SQLite en Windows bloquea archivos si se mantiene una transacción abierta mientras se realiza I/O de red.
- **Regla Estricta:** Las funciones de sincronización externa (`push_to_supabase`, `pull_from_supabase` o llamadas a APIs REST) deben extraer o preparar los datos en memoria y **cerrar la conexión o transacción SQLite antes de emitir la petición HTTP**.
- Los datos recibidos de la red se persisten en una transacción rápida e independiente una vez concluida la respuesta remota.

## Autorreparación y B-tree Quick Check
- Ante sospecha de corrupción por corte de energía o cierre abrupto de Windows:
  ```sql
  PRAGMA quick_check(1);
  ```
- Si arroja `malformed`, invocar el mecanismo de autorreparación de `app_local_recepcion/database.py`, que preserva todas las tablas de negocio y recrea limpia la tabla temporal `_supabase_outbox`.
