# Guía de Ejecución: Microsoft Edge MCP & Browser Automation

## Regla de Selección Primaria
- En este sistema, el navegador predeterminado para toda automatización, pruebas y scraping es **Microsoft Edge** (`edge-browser` MCP o Playwright con ejecutable del sistema `msedge`).
- Google Chrome se reserva estrictamente para cuando el usuario lo solicite explícitamente.

## Buenas Prácticas de Automatización con Edge
1. **Navegación y Espera Activa:** Tras invocar `browser_navigate`, verificar siempre que el DOM haya cargado mediante `browser_wait_for` o evaluando la presencia del selector clave con `browser_evaluate`.
2. **Trampas Modales de SUNAT (Dojo):** Si se detecta un modal emergente o bloqueo (`dijitDialog`, alertas SOL), no intentar adivinar clicks a ciegas. Obtener captura con `browser_take_screenshot` y solicitar confirmación.
3. **Gestión de Recursos:** Cerrar páginas o pestañas temporales tras completar la extracción para no saturar memoria RAM en las laptops de recepción.
4. **Respeto a Descargas Seguras:** Configurar la ruta de descarga para archivos PDF y XML en carpetas de trabajo conocidas dentro del workspace, evitando rutas temporales efímeras.
