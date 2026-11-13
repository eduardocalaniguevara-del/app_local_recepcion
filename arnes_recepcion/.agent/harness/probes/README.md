# Sondas de Sensores y Telemetría Operativa — Harness²

## Propósito
Este directorio `.agent/harness/probes/` contiene los scripts de sondas de sensores que evalúan en tiempo real la salud de la infraestructura, las bases de datos y los servicios web del Hostal Óvalo Mariátegui.

## Contrato de Salida de las Sondas (JSON Estándar)
Cada sonda implementada en este directorio puede invocarse de manera independiente mediante `node .agent/harness/probes/<nombre_sonda>.js`. Toda sonda debe emitir por `stdout` un objeto JSON con la siguiente estructura:

```json
{
  "name": "nombre_de_la_sonda",
  "status": "PASS" | "WARN" | "FAIL",
  "exit_code": 0,
  "latency_ms": 45,
  "timestamp": "2026-11-13T05:00:00.000Z",
  "details": {
    "summary": "Descripción legible del resultado",
    "metrics": {}
  }
}
```

## Sondas Registradas en el Arnés (M2)
1. `operational_health_probe.js`: Sonda maestra de salud general del espacio de trabajo y subsistemas.
3. `supabase_probe.js`: Conectividad, latencia y autenticación de la API REST de Supabase (`hostal-ovalo`).
4. `sunat_probe.js`: Disponibilidad de credenciales y frescura de los lotes extraídos de facturas y boletas.
5. `whatsapp_probe.js`: Estado del daemon Baileys, conteo de chats, mensajes y documentos OCR pendientes.
6. `webapp_probe.js`: Disponibilidad del visor web (puerto `8788`) y del entorno sandbox (puerto `8082`).

## Regla de Integración
Las sondas son invocadas directamente tanto por la CLI de `harness_engine.js` (`node .agent/harness/harness_engine.js --run-probe <nombre>`) como por el endpoint REST `/api/harness/run-probe` de `whatsapp_mcp/visor_web.js`.
