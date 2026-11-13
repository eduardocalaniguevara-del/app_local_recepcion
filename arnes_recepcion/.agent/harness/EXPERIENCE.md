# 📚 Catálogo Maestro de Experiencias y Reglas Ganadoras — Harness²

Este catálogo indexa lecciones de fallas reales, contrastes operativos y reglas probadas en el workspace.

---

## Índice por Módulos Operativos
- [SQLITE_SUPABASE](#módulo-sqlite_supabase) (4 Tarjetas)
- [SUNAT_RUS_SIRE](#módulo-sunat_rus_sire) (4 Tarjetas)
- [WHATSAPP_OCR](#módulo-whatsapp_ocr) (4 Tarjetas)
- [WEBAPP_RECEPCION](#módulo-webapp_recepcion) (4 Tarjetas)
- [APPS_SCRIPT_SHEETS](#módulo-apps_script_sheets) (3 Tarjetas)
- [ARQUITECTURA_MULTI_LAPTOP](#módulo-arquitectura_multi_laptop) (2 Tarjetas)

---

## Módulo: [SQLITE_SUPABASE]

### [EXP-SQL-01] [SQLITE_SUPABASE] Autorreparación de Bandeja SQLite (_supabase_outbox) y Páginas B-tree Corruptas
- **Módulo**: `[SQLITE_SUPABASE]`
- **ID**: `EXP-SQL-01`
- **Severidad**: Crítica
- **Contexto / Síntoma**: Cortes de energía imprevistos o reinicios forzados en Windows corrompen páginas B-tree de SQLite arrojando `sqlite3.DatabaseError: database disk image is malformed` al intentar leer la bandeja temporal `_supabase_outbox` en el arranque del servidor.
- **Contraste / Qué Falló**: Intentar restaurar copias antiguas de la base de datos o reemplazar archivos a ciegas (`ovalo_sistema.corrupt_bak.db`) causaba la pérdida irreversible de estancias registradas en el día, ventas de tienda y pases no sincronizados, además de colisiones de bloqueo por el archivo `-wal`.
- **Regla Ganadora / Script Probado**: Se implementó `repair_db_if_malformed(force=False)` con `PRAGMA quick_check(1);`. Si detecta corrupción, abre una base limpia temporal, clona todas las 33 tablas del negocio una por una preservando íntegramente sus registros, descarta y recrea desde cero la tabla temporal `_supabase_outbox` y reconstruye los Triggers con `init_supabase_local_triggers()`.
  ```python
  # Implementado en app_local_recepcion/database.py
  res = chk_conn.execute("PRAGMA quick_check(1);").fetchall()
  if not res or str(res[0][0]).strip().lower() != "ok":
      repair_db_if_malformed(force=True)
  ```

### [EXP-SQL-02] [SQLITE_SUPABASE] Triggers sin Bloqueo de Conexión SQLite (0 ms Lock) y Prevención de Bucles Espejo
- **Módulo**: `[SQLITE_SUPABASE]`
- **ID**: `EXP-SQL-02`
- **Severidad**: Alta
- **Contexto / Síntoma**: Concurrencia entre la WebApp de recepción, el hilo de Google Sheets y el sincronizador de Supabase provocaba `sqlite3.OperationalError: database is locked`.
- **Contraste / Qué Falló**: Mantener conexiones abiertas a SQLite mientras se realizaban peticiones HTTPS a Supabase mantenía bloqueado `ovalo_sistema.db` durante cientos de milisegundos. Además, al descargar cambios desde la nube, los triggers locales volvían a encolar la misma fila, generando un ciclo infinito de sincronización (efecto ping-pong).
- **Regla Ganadora / Script Probado**:
  1. `push_to_supabase` y `pull_from_supabase` leen y escriben en lotes rápidos y **cierran o liberan la conexión SQLite antes de iniciar cualquier llamada por internet**, garantizando 0 ms de bloqueo retenido.
  2. Supresión de bucles mediante tabla `_supabase_sync_control`: los triggers tienen la cláusula `WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0`. Durante la descarga desde la nube, `pulling` se activa en `1`, silenciando los disparadores locales.
  ```sql
  -- Triggers implementados en app_local_recepcion/supabase_sync.py
  CREATE TRIGGER trg_sb_upd_estancias AFTER UPDATE ON estancias FOR EACH ROW
  WHEN (SELECT pulling FROM _supabase_sync_control WHERE id = 1) = 0
  BEGIN
      INSERT INTO _supabase_outbox (tabla, pk1, pk2, op, updated_at)
      VALUES ('estancias', CAST(NEW.estancia_uuid AS TEXT), '', 'UPSERT', datetime('now', 'localtime'))
      ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at;
  END;
  ```

### [EXP-SQL-03] [SQLITE_SUPABASE] Aislamiento Hermético de Bóveda: Grupo 1 Privado vs Grupo 2 Hostal
- **Módulo**: `[SQLITE_SUPABASE]`
- **ID**: `EXP-SQL-03`
- **Severidad**: Crítica (Seguridad y Privacidad)
- **Contraste / Qué Falló**: Scripts iniciales de sincronización genéricos corrían el riesgo de subir tablas de WhatsApp personal o recibos de honorarios hacia el proyecto público de Supabase o compartirlos con la Laptop 2 de recepción.
- **Regla Ganadora / Script Probado**: Separación física y lógica obligatoria en dos archivos SQLite:
  - **Grupo 2 Compartido (`ovalo_sistema.db`)**: Sincronizado en tiempo real con Supabase (`hostal-ovalo`, proyecto `fmlfqupwscymosvwnnzx`) mediante token `x-hostal-sync-token: ovalo_master_sync_2026_k9x` y filtrado estricto en la lista de tablas `TABLAS_GRUPO2_PK`.
  ```json
  // supabase_config.json
  {
    "local_db_grupo2": "ovalo_sistema.db",
    "hostal_sync_header": "x-hostal-sync-token"
  }
  ```

### [EXP-SQL-04] [SQLITE_SUPABASE] Compatibilidad con Upsert Externo (ON CONFLICT DO UPDATE vs INSERT OR REPLACE)
- **Módulo**: `[SQLITE_SUPABASE]`
- **ID**: `EXP-SQL-04`
- **Severidad**: Media
- **Contexto / Síntoma**: Errores en cascada durante operaciones masivas de inserción o actualización cuando múltiples procesos interactuaban con SQLite.
- **Contraste / Qué Falló**: El uso de `INSERT OR REPLACE` en triggers eliminaba internamente la fila antes de reinsertarla, borrando claves foráneas o generando errores de conflicto con transacciones externas en curso.
- **Regla Ganadora / Script Probado**: Se reemplazaron todas las sentencias de triggers por sintaxis estándar de SQLite 3.24+: `INSERT INTO _supabase_outbox (...) VALUES (...) ON CONFLICT(tabla, pk1, pk2) DO UPDATE SET op = excluded.op, updated_at = excluded.updated_at;`.

---

## Módulo: [SUNAT_RUS_SIRE]

### [EXP-SUN-01] [SUNAT_RUS_SIRE] Falsa Ilusión de Éxito en Playwright (Exit Code 0) y Virtual Scroll en dojoxGrid
- **Módulo**: `[SUNAT_RUS_SIRE]`
- **ID**: `EXP-SUN-01`
- **Severidad**: Crítica
- **Contexto / Síntoma**: La extracción automatizada de comprobantes de pago de SUNAT (facturas E001 y boletas EB01) en meses de alto volumen omitía hasta el 80% de los comprobantes.
- **Contraste / Qué Falló**: El script de Playwright finalizaba con código de salida `0` (sin errores de Python), reportando falsamente que la extracción había culminado. Sin embargo, la interfaz de SUNAT SOL utiliza componentes Dojo (`dojoxGrid`) con renderizado virtual: solo existen 10 a 15 elementos en el DOM simultáneamente. Al hacer `locator("tr.dojoxGridRow").all()`, no se extraían los comprobantes fuera del área visible.
- **Regla Ganadora / Script Probado**:
  1. Regla de Oro de `.geminirules`: Código de salida `0` NO es éxito. Se debe validar el conteo de elementos extraídos contra el total declarado en la cabecera de la tabla de SUNAT.
  2. Implementación de scroll progresivo mediante JavaScript sobre `.dojoxGridScrollbox, div[dojoattachpoint='scrollbox']`, desplazando `scrollTop` en bloques de 350px y acumulando resultados en un diccionario indexado por serie y número normalizado.
  ```python
  # Extraccion_Octubre_Completa.py
  scrollbox = frame.locator(".dojoxGridScrollbox, div[dojoattachpoint='scrollbox']").first
  scrollbox.evaluate("el => el.scrollTop += 350")
  time.sleep(0.8)
  ```

### [EXP-SUN-02] [SUNAT_RUS_SIRE] Retención de Estado y Verificación de Identidad de Comprobante en Popups
- **Módulo**: `[SUNAT_RUS_SIRE]`
- **ID**: `EXP-SUN-02`
- **Severidad**: Alta
- **Contexto / Síntoma**: Al hacer clic en una fila para extraer el detalle del comprobante (número de habitación y noches ocupadas), los datos resultantes correspondían a la fila consultada anteriormente.
- **Contraste / Qué Falló**: SUNAT reutiliza el iframe o ventana modal del popup. Si el scraper lee el texto antes de que la nueva llamada AJAX responda, extrae los datos del comprobante previo, corrompiendo la auditoría de noches de hotel.
- **Regla Ganadora / Script Probado**: Validación estricta mediante `tiene_popup_valido(item)`: el script extrae la serie y número (`EB01 - XXXXX` o `E001 - XXXXX`) directamente del texto del cuerpo del popup. Si no coincide exactamente con el comprobante de la fila seleccionada, se descarta y se reintenta hasta confirmar carga real.
  ```python
  # Extraccion_Octubre_Completa.py
  def tiene_popup_valido(item):
      cp_norm = normalizar_cp(item.get("numero_cp", ""))
      for it in item.get("items", []):
          txt = it.get("texto_completo", "")
          m_cp = re.search(r"\b((?:EB01|E001)\s*-\s*\d+)\b", txt, re.I)
          if m_cp and normalizar_cp(m_cp.group(1)) == cp_norm:
              return True
      return False
  ```

### [EXP-SUN-03] [SUNAT_RUS_SIRE] Doble Régimen Tributario Determinista: Nuevo RUS vs RHE vs Hostal 10.5%
- **Módulo**: `[SUNAT_RUS_SIRE]`
- **ID**: `EXP-SUN-03`
- **Severidad**: Alta (Riesgo Tributario y Multas)
- **Contexto / Síntoma**: Emisión de comprobantes mensuales bajo distintos regímenes tributarios en los portales de SUNAT.
- **Contraste / Qué Falló**: Aplicar la división entre `1.105` o `1.18` a boletas del Nuevo RUS causaba descuadres en la declaración mensual de S/ 7,800. Configurar incorrectamente el medio de pago en Recibos por Honorarios provocaba rechazos en el formulario virtual.
  - **Hostal (RUC 20611986484, Ley 31556)**: Valor Unitario = `monto / 1.105`, IGV (10.5%) = `monto - valor_unitario`.

### [EXP-SUN-04] [SUNAT_RUS_SIRE] Manejo de Sesión SOL e Iframe Anidado (iframeApplication)
- **Módulo**: `[SUNAT_RUS_SIRE]`
- **ID**: `EXP-SUN-04`
- **Severidad**: Media
- **Contexto / Síntoma**: Las consultas dentro de SUNAT fallaban indicando que los selectores de fechas o botones no existían en la página.
- **Contraste / Qué Falló**: Buscar elementos en el objeto `page` principal arrojaba timeouts porque la aplicación funcional de SUNAT se carga dentro de un marco anidado: `iframe[name="iframeApplication"]`.
- **Regla Ganadora / Script Probado**: Obtener siempre el contexto del frame antes de cualquier interacción:
  ```python
  frame = page.locator("iframe[name=\"iframeApplication\"]").content_frame
  frame.locator(".dijitReset.dijitRight").click()
  ```

---

## Módulo: [WHATSAPP_OCR]

### [EXP-WSP-01] [WHATSAPP_OCR] Normalización Obligatoria de Modo de Color a RGB para RapidOCR
- **Módulo**: `[WHATSAPP_OCR]`
- **ID**: `EXP-WSP-01`
- **Severidad**: Alta
- **Contexto / Síntoma**: Vouchers de pago o comprobantes enviados por WhatsApp en formatos PNG o WebP causaban caídas inesperadas en el motor OCR local con errores en numpy o librerías de visión artificial.
- **Contraste / Qué Falló**: Pasar directamente imágenes con canal alfa de transparencia (`RGBA`), mapas de paleta (`P`) o escala de grises a `RapidOCR(np.array(img))` provocaba excepciones no controladas.
- **Regla Ganadora / Script Probado**: Conversión explícita con Pillow antes de cualquier procesamiento OCR: `rgb = pil_img.convert("RGB")`. Asimismo, filtrar imágenes con menos de 15 caracteres (`MIN_LETRAS_REALES = 15`) para no desperdiciar ciclos de CPU en stickers o fotos de habitaciones.
  ```python
  # whatsapp_mcp/ocr_extractor.py
  def ocr_imagen_pil(pil_img: Image.Image) -> str:
      rgb = pil_img.convert("RGB")
      res = get_ocr()(np.array(rgb))
      if not res or not getattr(res, "txts", None):
          return ""
      return "\n".join([t.strip() for t in res.txts if t and t.strip()])
  ```

### [EXP-WSP-02] [WHATSAPP_OCR] Cruce Automático de Teléfonos de Recepción con Padrón de Huéspedes
- **Módulo**: `[WHATSAPP_OCR]`
- **ID**: `EXP-WSP-02`
- **Severidad**: Media
- **Contexto / Síntoma**: Los vouchers recibidos por el WhatsApp de recepción en Laptop 2 quedaban como mensajes aislados sin identificar a qué huésped o habitación correspondían.
- **Contraste / Qué Falló**: Comparar números por coincidencia exacta de cadena fallaba porque en WhatsApp los números llevan prefijo internacional (`51987654321@s.whatsapp.net`), mientras que en recepción se anotan con espacios o sin código de país (`987 654 321`).
- **Regla Ganadora / Script Probado**: Normalizar a nivel de consulta SQL extrayendo los últimos 9 dígitos:
  ```sql
  -- whatsapp_mcp/visor_web.js
  SELECT hp.nombre || ' (DNI ' || hp.dni || ' | Hab ' || hp.hab_frec || ')'
  FROM huespedes_padron hp
  WHERE hp.celular != '' 
    AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(COALESCE(m.chat_jid, ''), '@s.whatsapp.net', ''), -9)
  LIMIT 1;
  ```

### [EXP-WSP-03] [WHATSAPP_OCR] Despachador de OCR Asíncrono no Bloqueante con Debounce
- **Módulo**: `[WHATSAPP_OCR]`
- **ID**: `EXP-WSP-03`
- **Severidad**: Media
- **Contexto / Síntoma**: La llegada simultánea de múltiples imágenes en grupos o chats provocaba la creación descontrolada de procesos Python simultáneos en la laptop ASUS TUF de recepción.
- **Contraste / Qué Falló**: Invocar el script de OCR en cada evento de mensaje recibido saturaba la CPU de 8 núcleos y causaba retrasos en el socket de WhatsApp Baileys.
- **Regla Ganadora / Script Probado**: Patrón debounce con proceso en segundo plano en `wsp_daemon.js`:
  ```javascript
  let ocrRunning = false;
  let ocrPendingAgain = false;
  function triggerOcrPendientes() {
    if (ocrRunning) { ocrPendingAgain = true; return; }
    ocrRunning = true;
    const child = spawn('python', [OCR_SCRIPT, '--pendientes'], { windowsHide: true });
    child.on('close', () => {
      ocrRunning = false;
      if (ocrPendingAgain) { ocrPendingAgain = false; triggerOcrPendientes(); }
    });
  }
  ```

### [EXP-WSP-04] [WHATSAPP_OCR] Aislamiento de Chats de Proveedores del Hostal vs Chats Personales
- **Módulo**: `[WHATSAPP_OCR]`
- **ID**: `EXP-WSP-04`
- **Severidad**: Alta
- **Contexto / Síntoma**: Las facturas de proveedores enviadas a grupos específicos debían canalizarse a la contabilidad del hotel sin mezclar los chats privados del administrador.
- **Contraste / Qué Falló**: Clasificar todas las imágenes como documentos del hostal sobrecargaba la tabla `hostal_documentos_ocr` con recibos y contratos personales.
- **Regla Ganadora / Script Probado**: Lista blanca de IDs de WhatsApp en `ocr_extractor.py` (`CHATS_HOSTAL_FACTURAS`): únicamente los comprobantes provenientes de estos JIDs se marcan con `es_del_hostal = 1` y se copian a `hostal_documentos_ocr` en `ovalo_sistema.db`.

---

## Módulo: [WEBAPP_RECEPCION]

### [EXP-WEB-01] [WEBAPP_RECEPCION] Disciplina Estricta de Sandbox (Ovalo_Mariategui_PRUEBA) Antes de Promoción
- **Módulo**: `[WEBAPP_RECEPCION]`
- **ID**: `EXP-WEB-01`
- **Severidad**: Crítica
- **Contexto / Síntoma**: Ediciones directas en producción provocaron fallos en check-in y discrepancias en la asignación de colores de habitaciones durante turnos de recepción.
- **Contraste / Qué Falló**: Modificar archivos en `app_local_recepcion` y hacer `git add -A` incluía accidentalmente scripts en observación (`auditor_audio_noche.py`, `pases_logic.py`) o bases de datos SQLite locales en uso.
- **Regla Ganadora / Script Probado**:
  1. Regla grabada con `/learn`: Todo cambio se edita y prueba en `C:\ovalo_mariategui_prueba` (puerto `8082`, sandbox aislado con foto de base de datos y sin sincronizadores externos).
  2. Promoción controlada únicamente con `Promover_a_Operativa.bat` (`promover_a_operativa.py`), el cual valida respaldos, excluye archivos en observación y ejecuta pruebas de integridad antes de transferir a producción.

### [EXP-WEB-02] [WEBAPP_RECEPCION] Identidad Inmutable de Huésped al Mover Habitación (estanciaUuid + habOriginal)
- **Módulo**: `[WEBAPP_RECEPCION]`
- **ID**: `EXP-WEB-02`
- **Severidad**: Alta
- **Contexto / Síntoma**: Al cambiar de habitación a un huésped mediante el ícono de lápiz `✏️`, se perdían los consumos registrados en tienda y los pagos quedaban desvinculados.
- **Contraste / Qué Falló**: Actualizar registros indexando únicamente por el número de habitación nuevo causaba colisiones o borraba registros previos asociados a la estancia original.
- **Regla Ganadora / Script Probado**: Toda operación de cambio de habitación requiere obligatoriamente `estanciaUuid` y `habOriginal`. El backend actualiza la habitación destino reasignando en una única transacción atómica la estancia, sus pagos (`pago_id`) y consumos de tienda (`venta_uuid`).

### [EXP-WEB-03] [WEBAPP_RECEPCION] Única Fuente de Distribución (GitHub) y Eliminación de Overlays Locales
- **Módulo**: `[WEBAPP_RECEPCION]`
- **ID**: `EXP-WEB-03`
- **Severidad**: Media
- **Contexto / Síntoma**: Desfase entre Laptop 1 y Laptop 2 donde un botón de interfaz (ej. "Yapes") aparecía en un equipo y desaparecía en el otro tras pocos minutos.
- **Contraste / Qué Falló**: La existencia de un overlay local (`_preservar_tablet_andino_local`) copiaba archivos de una carpeta local cada 20 segundos, sobreescribiendo los cambios descargados de Git.
- **Regla Ganadora / Script Probado**: GitHub (`origin/main`) es la única e indiscutible fuente de distribución. Los overlays locales están desactivados por defecto y solo se habilitan con una bandera explícita `activar_overlay_local.flag`.

### [EXP-WEB-04] [WEBAPP_RECEPCION] Inyección en Caliente sin Caída de Servicio y Desacople de Procesos Windows
- **Módulo**: `[WEBAPP_RECEPCION]`
- **ID**: `EXP-WEB-04`
- **Severidad**: Media
- **Contexto / Síntoma**: Actualizar código en la laptop de recepción cerraba la ventana del servidor web o requería reiniciar el equipo.
- **Contraste / Qué Falló**: `os.system` o `subprocess.Popen` estándar heredaban los manejadores de consola, haciendo que al cerrarse el actualizador se cerrara el servidor FastAPI.
- **Regla Ganadora / Script Probado**: Uso de inyección en memoria de rutas (`_inyectar_rutas_en_servidor_activo` con `**_kwargs`) y ejecución de procesos en segundo plano desacoplados mediante `Win32_Process.Create` de WMI o `creationflags = subprocess.CREATE_NO_WINDOW`.

---

## Módulo: [APPS_SCRIPT_SHEETS]

### [EXP-APP-01] [APPS_SCRIPT_SHEETS] Motor Multicapa de Conciliación Bancaria Yape/Plin (Normalización AM/PM y Pagos Grupales)
- **Módulo**: `[APPS_SCRIPT_SHEETS]`
- **ID**: `EXP-APP-01`
- **Severidad**: Alta
- **Contexto / Síntoma**: El cotejo de reportes de Yape contra `Registro_Pagos` arrojaba cientos de alertas falsas de `FALTANTE BANCO`.
- **Contraste / Qué Falló**: La comparación directa por hora y monto fallaba porque las recepcionistas omitían AM/PM (escribiendo `70 6:30`), anotaban un único Yape para dos habitaciones distintas o registraban con 30 minutos de retraso.
- **Regla Ganadora / Script Probado**: Pipeline determinista de 8 pasadas en `auditor_yape.py`:
  - Pasada 0A: Pagos grupales unificados (`CUADRADO (PAGO GRUPAL COMPARTIDO)`).
  - Pasada 0C: Abonos parciales del banco en menos de 10 min.
  - Pasada 1: Tiempo $\le 15$ min y similitud de nombres $\ge 35\%$.
  - Pasada 1b: Voucher exacto $\le 2$ min para terceros pagadores.
  - Pasada 3: Desfase AM/PM ($\approx 12$ horas) con similitud obligatoria de nombres $\ge 35\%$.
  - Regla de Vueltos: Pagos con concepto `Vuelto a cliente` (monto negativo) se omiten para evitar falsos descuadres.

### [EXP-APP-02] [APPS_SCRIPT_SHEETS] Auto-Curación de Fórmulas Dinámicas Matriz LET/MAP/LAMBDA ante Error #REF! en A10
- **Módulo**: `[APPS_SCRIPT_SHEETS]`
- **ID**: `EXP-APP-02`
- **Severidad**: Alta
- **Contexto / Síntoma**: El panel de ocupación general de la hoja de cálculo mostraba `#REF!` bloqueando la visualización de todas las habitaciones.
- **Contraste / Qué Falló**: Las recepcionistas pegaban notas o valores en celdas intermedias (ej. `E15`), interrumpiendo el área de desbordamiento (spill range) de la matriz dinámica `LET/MAP/LAMBDA`.
- **Regla Ganadora / Script Probado**: Script auditor en Python (`auditor.py` con `gspread`) que monitorea la celda `A10`. Si detecta `#REF!`, limpia automáticamente el rango `A11:AL250` y reinyecta la fórmula maestra desde `Documentacion/formulas_y_procedimiento.txt`, restaurando el panel sin intervención humana.

### [EXP-APP-03] [APPS_SCRIPT_SHEETS] Disciplina de Despliegues Apps Script: Validación node -c y Protección de Versión @28
- **Módulo**: `[APPS_SCRIPT_SHEETS]`
- **ID**: `EXP-APP-03`
- **Severidad**: Crítica
- **Contexto / Síntoma**: Errores de sintaxis en `Código.js` subidos con `clasp push` rompían la interfaz de check-in rápido en producción.
- **Contraste / Qué Falló**: Subir código a Apps Script sin validación previa y desplegar nuevas versiones sobre el deployment estable `@28` obligaba a tediosos rollbacks manuales.
- **Regla Ganadora / Script Probado**:
  1. Validación local previa con `node -c Código.js`.
  2. Un solo `clasp push` por tarea.
  3. Prohibición estricta de sobrescribir el deployment estable `@28` (`AKfycbzTLX1eyuD67M7jQ3-ciGPaFkU-xr3A7QofkLA4AhZbMRwZ3s-TVAZRitsMYHWjUkug`).
  4. Búsqueda quirúrgica de líneas (`Select-String`) en lugar de reescribir los archivos completos de 3,000 líneas.

---

## Módulo: [ARQUITECTURA_MULTI_LAPTOP]

### [EXP-ARC-01] [ARQUITECTURA_MULTI_LAPTOP] Delegación de Automatizaciones Diarias al Nodo 24/7 (Laptop 2 Recepción) y Canal de Pruebas Conectado (8082 ↔ 8082)
- **Módulo**: `[ARQUITECTURA_MULTI_LAPTOP]`
- **ID**: `EXP-ARC-01`
- **Severidad**: Crítica
- **Contexto / Síntoma**: Las tareas batch diarias (conciliación bancaria Yape nocturna, consolidación de comprobantes SUNAT, procesamiento de cola WhatsApp) requerían que la Laptop 1 de administración estuviera encendida a altas horas de la noche o se interrumpían si el administrador cerraba sesión. Adicionalmente, el entorno de pruebas `Ovalo_Mariategui_PRUEBA` (puerto 8082) estaba completamente aislado, impidiendo contrastar datos reales o sincronizar pruebas entre ambas laptops.
- **Contraste / Qué Falló**: Programar automatizaciones en Laptop 1 provocaba pérdidas de ejecución cuando la máquina entraba en suspensión o se usaba para labores personales. Por otro lado, probar funcionalidades con bases de datos vacías o desactualizadas ocultaba errores de concurrencia y formato de datos reales de producción.
- **Regla Ganadora / Script Probado**:
  1. **Motor 24/7 en Laptop 2 (`scheduler_recepcion_24x7.py`)**: Aprovecha que la laptop de recepción opera continuamente 24/7 para ejecutar cada 30 minutos la sonda maestra de salud de Arnés² (`operational_health_probe.js`), reportar telemetría a `estado_sistema` (`arnes_salud_LAPTOP_2_RECEPCION`) y despachar las ranuras de `facturacion_diaria_sunat`, `conciliacion_yape_diaria`, `envio_wsp_comprobantes` y `captura_aprendizaje_recepcionistas`.
  2. **Canal de Pruebas Conectado (`8082 ↔ 8082` vía `sandbox_change_log`)**: En modo prueba (`MODO_PRUEBA = True`), los cambios en la sandbox NO tocan jamás las tablas operativas reales de Supabase (`/rest/v1/<tabla>`), sino que se encolan exclusivamente a `sandbox_change_log` para sincronizarse entre los entornos 8082 de ambas laptops.
  3. **Extracción en Solo Lectura (`extraer_datos_reales_supabase_readonly`)**: La base de prueba puede refrescarse en cualquier momento ejecutando solo peticiones `GET` hacia las tablas de Supabase real con `pulling = 1`, garantizando cero riesgo de sobrescritura en datos productivos.

### [EXP-ARC-02] [ARQUITECTURA_MULTI_LAPTOP] Aprendizaje Compartido Bidireccional (aprendizaje_compartido_hostal) y Candado de Código (GR-08)
- **Módulo**: `[ARQUITECTURA_MULTI_LAPTOP]`
- **ID**: `EXP-ARC-02`
- **Severidad**: Alta
- **Contexto / Síntoma**: Las recepcionistas realizaban correcciones operativas legítimas (pagos posteriores, cambios de habitación con `✏️`, reemplazo de snacks/bebidas por error de digitación, o cancelaciones inmediatas de estancias erróneas) que generaban alertas o bloqueos innecesarios si se aplicaban candados de autorización demasiado rígidos en la WebApp. Al mismo tiempo, las configuraciones críticas del sistema (Arnés², manifiesto, sondas o promoción a producción) quedaban expuestas si no existía un control de autorización estricto.
- **Contraste / Qué Falló**: Exigir códigos o contraseñas para operaciones cotidianas de recepción ralentizaba la atención al cliente, provocaba colas en recepción y descontento en el personal. Por el contrario, carecer de un candado en la promoción de código permitía que versiones en desarrollo de prueba se promovieran accidentalmente a producción sin validación.
- **Regla Ganadora / Script Probado**:
  1. **Guardrail `GR-08: Candado de Código para Arnés² y Promoción de Pruebas`**:
     - **100% LIBRE para Operaciones del Negocio**: Check-in rápido, pagos posteriores, cambios de habitación, reemplazo de productos en tienda y eliminación de estancias operan sin pedir ningún código.
     - **CÓDIGO OBLIGATORIO para Configuración y Promoción**: Cualquier cambio a `manifest.json`, `guardrails`, `probes` o ejecución de `promover_a_operativa.py --aplicar` exige el código de autorización validado mediante hash SHA-256 (`24fe5b2863951e8d97cd7e19f2a5fed2534686189241b58951f0e7e2e8d666d7` = `[código de administrador]`).
  2. **Aprendizaje Compartido Bidireccional (`aprendizaje_compartido_hostal`)**: El colector `analizar_aprendizaje_recepcionistas()` revisa `tablet_buzon_mejoras`, `historial_movimientos` y `wsp_recepcion_mensajes`, extrayendo patrones reales de trabajo de las recepcionistas e inquietudes de clientes para distribuirlos automáticamente vía Supabase entre Laptop 1 y Laptop 2.

