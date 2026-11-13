# Directiva del Bloque Coach — Aprendizaje y Modelos Antigravity

## Regla de Posicionamiento Obligatorio
El bloque Coach (`🎓` + `🤖` + `📋`) DEBE ubicarse **SIEMPRE en las primeras líneas** de la respuesta del agente, antes de cualquier otro texto o acción.

## Estructura Obligatoria del Bloque Coach
```markdown
> **🎓 En esta respuesta puedes aplicar:** [Herramienta o Comando] — [Explicación sencilla y cotidiana de para qué sirve hoy, cero jerga técnica]
> **🤖 Modelo o Equipo Multi-Modelo sugerido:** [Modelo individual o combinación /teamwork óptima para esta tarea y por qué]
> **📋 Cómo pedirlo (ejemplo listo para copiar):** "[Frase exacta que el usuario puede copiar y pegar para ejecutarlo]"
```

## Catálogo de Comandos y Disparadores Frecuentes
- `/plan`: Tareas nuevas, refactorizaciones grandes o módulos complejos antes de modificar archivos.
- `/compact`: Al superar ~200-250 pasos de conversación o al concluir un hito operativo.
- `/schedule`: Procesos periódicos o alarmas automáticas por hora o fecha determinada.
- `/learn`: Cuando se corrige un error difícil, se depura un bug sutil o se formaliza una nueva regla de negocio.
- `/grill-me`: Cuando existan varios caminos alternativos y se requiera una breve entrevista guiada para definir la decisión.
- `/goal`: Tareas largas de ejecución continua sin requerir intervención paso a paso.
- `/browser`: Interacciones directas con portales web externos (SUNAT, banca por internet).
- `Subagentes / Equipos (/teamwork)`: Procesos que admiten paralelización (análisis + implementación + auditoría) o lectura intensiva de código sin saturar el contexto principal.

## Registro Permanente de Aprendizajes
Todo nuevo truco, regla fija o patrón depurado debe registrarse en `Documentacion/APRENDIZAJE.md` para preservar la memoria institucional del sistema.
