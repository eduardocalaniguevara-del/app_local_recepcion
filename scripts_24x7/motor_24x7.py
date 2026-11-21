"""
scripts_24x7/motor_24x7.py — Vigilante nocturno 24/7.
REGLA ARQUITECTÓNICA:
- Se programa y actualiza por Git desde Laptop 1.
- SOLO se mantiene corriendo 24/7 en Laptop 2 (Recepción), donde vive el WhatsApp físico y la emisión SUNAT.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROL_NODO, ACTIVAR_MOTOR_24X7
from app.salud import reporte_salud


def iniciar_vigilante_24x7(modo_prueba_unica: bool = False) -> None:
    if not ACTIVAR_MOTOR_24X7 and not modo_prueba_unica:
        print(
            f"[motor_24x7] En reposo automático: este nodo es '{ROL_NODO}'. "
            "El vigilante 24/7 solo se mantiene encendido en LAPTOP_2_RECEPCION."
        )
        return

    print(f"[motor_24x7] Vigilante activo en {ROL_NODO}. Ejecutando ronda de salud...")
    estado = reporte_salud()
    print(f"[motor_24x7] Salud OK={estado['ok']} | Timestamp={estado['timestamp']}")
    if modo_prueba_unica:
        return

    while True:
        time.sleep(1800)  # Ronda cada 30 minutos
        estado = reporte_salud()
        print(f"[motor_24x7] Ronda 30m -> Salud OK={estado['ok']} ({estado['timestamp']})")


if __name__ == "__main__":
    iniciar_vigilante_24x7(modo_prueba_unica="--once" in sys.argv)
