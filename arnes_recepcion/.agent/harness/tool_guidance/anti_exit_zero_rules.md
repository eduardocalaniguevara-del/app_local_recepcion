# Guía de Ejecución: Falacia de "Exit Code 0" y Verificación de Carga Real

## Principio Fundamental
En sistemas con interfaces dinámicas (Dojo, SUNAT SOL, WhatsApp Baileys, Google Apps Script), un proceso puede terminar con código de retorno `0` (sin errores de sintaxis ni excepciones no capturadas) pero haber fallado completamente en su objetivo de negocio:
- Extraer una tabla vacía debido a DOM virtualizado.
- Guardar un archivo JSON con `[]` o `null`.
- Enviar un mensaje de WhatsApp a un socket desconectado.
- Rebotar en un captcha no detectado.

## Reglas de Validación Obligatoria
1. **Verificación de Carga Útil (Payload Check):**
   - No considerar exitosa una extracción hasta comprobar que el archivo resultante (JSON, CSV, DB) existe, tiene tamaño mayor a cero y contiene el número esperado de registros válidos.
2. **Scroll Progresivo en DojoxGrid (SUNAT):**
   - Para tablas con Dojo, no confiar en selectores estáticos. Debe aplicarse desplazamiento continuo sobre `.dojoxGridScrollbox` hasta que el número de filas extraídas coincida con el total reportado en la cabecera.
3. **Validación de Identidad en Modales Reciclados:**
   - Si un popup de SUNAT o del sistema se reutiliza en pantalla, verificar explícitamente que el identificador (ej. `EB01 - XXXXX`) coincida con el registro solicitado antes de capturar datos.
4. **Respuesta ante Falla Silenciosa:**
   - Si los datos extraídos son vacíos o inconsistentes, tratar la ejecución como un fallo crítico, registrar el síntoma y detener el bucle.
