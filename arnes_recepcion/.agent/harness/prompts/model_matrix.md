# Matriz de Enrutamiento Multi-Modelo (Plan Ultra)

## Asignación de Roles por Especialidad
| Modelo | Nivel Habitual | Propósito Principal | Criterio de Selección |
|---|---|---|---|
| **Gemini 4 Argon** | `Medium` (por defecto)<br>`High` (planificación y bugs complejos) | Estrategia, arquitectura multi-archivo, razonamiento integral de negocio y desarrollo general. | Modelo principal del sistema para coordinar y estructurar. |
| **Claude Sonnet 5.5** | `Medium` (por defecto)<br>`High` (si hay fallas) | Escritura de funciones completas, integración profunda de APIs y depuración quirúrgica. | Ideal para tareas con lógica procedural compleja o APIs REST. |
| **Gemini 3.8 Flash** | `Medium` | Ráfagas mecánicas rápidas: CSS, anchos de columna, despliegue `clasp push`, y lecturas puntuales. | Maximiza velocidad y cuida la cuota del Plan Ultra. |
| **Claude Opus 5.5** | `Medium` | Contraste final, verificación y arbitraje de arquitectura. | Solo se activa cuando Argon o Sonnet fallan 2 veces consecutivas en el mismo obstáculo. |

## Patrón Clave de Eficiencia: "Planifica en High, Ejecuta en Medium"
- Iniciar la fase de análisis o diseño de un módulo nuevo utilizando `High` (Argon High).
- Una vez trazado el plan y validados los contratos, cambiar a `Medium` (Argon o Sonnet Medium) para la secuencia de edición de archivos y despliegue.
- Alerta preventiva de cuota: Si se detecta a Argon High u Opus en una tarea puramente estética o mecánica, emitir la sugerencia de una línea: `🚦 Sugiero Gemini 3.8 Flash (Medium)` y continuar sin bloquear el flujo.

## Protocolo para `argon.py` (API Externa)
- El script `C:\Users\josec\argon.py` utiliza una API externa sujeta a consumo tarifado.
- **Regla estricta:** Solo debe ejecutarse vía terminal si el usuario lo solicita explícitamente (`python C:\Users\josec\argon.py "<consulta>"`).
