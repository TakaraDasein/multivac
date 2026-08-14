"""Volumen, brillo y reproducción multimedia."""

from __future__ import annotations

from typing import Literal

from .registry import run, tool

SINK = "@DEFAULT_AUDIO_SINK@"


def _volumen_actual() -> int:
    salida = run(["wpctl", "get-volume", SINK])  # "Volume: 0.65" | "... [MUTED]"
    try:
        return round(float(salida.split()[1]) * 100)
    except (IndexError, ValueError):
        return -1


@tool()
def ajustar_volumen(porcentaje: int) -> str:
    """Fija el volumen del sistema a un valor absoluto.

    Args:
        porcentaje: Volumen deseado, de 0 a 100.
    """
    porcentaje = max(0, min(100, porcentaje))
    run(["wpctl", "set-volume", SINK, f"{porcentaje / 100:.2f}"])
    return f"Volumen al {porcentaje} por ciento."


@tool()
def cambiar_volumen(delta: int) -> str:
    """Sube o baja el volumen de forma relativa.

    Args:
        delta: Cuánto cambiar, en puntos porcentuales. Negativo para bajar.
    """
    signo = "+" if delta >= 0 else "-"
    run(["wpctl", "set-volume", "-l", "1.0", SINK, f"{abs(delta) / 100:.2f}{signo}"])
    actual = _volumen_actual()
    return f"Volumen al {actual} por ciento." if actual >= 0 else "Volumen ajustado."


@tool()
def silenciar(activar: bool) -> str:
    """Silencia o restaura el sonido.

    Args:
        activar: True para silenciar, False para volver a oír.
    """
    run(["wpctl", "set-mute", SINK, "1" if activar else "0"])
    return "Silenciado." if activar else "Sonido restaurado."


@tool()
def control_musica(accion: Literal["play", "pause", "siguiente", "anterior"]) -> str:
    """Controla el reproductor de música activo (Spotify, navegador, etc.).

    Args:
        accion: Qué hacer con la reproducción.
    """
    mapa = {
        "play": "play",
        "pause": "pause",
        "siguiente": "next",
        "anterior": "previous",
    }
    resultado = run(["playerctl", mapa[accion]])
    if "fallo" in resultado:
        return "No hay ningún reproductor activo."
    return f"Reproducción: {accion}."


@tool()
def que_suena() -> str:
    """Dice qué canción se está reproduciendo."""
    resultado = run(["playerctl", "metadata", "--format", "{{artist}} - {{title}}"])
    if "fallo" in resultado or not resultado:
        return "No hay nada reproduciéndose."
    return f"Suena {resultado}."


@tool()
def ajustar_brillo(porcentaje: int) -> str:
    """Fija el brillo de la pantalla.

    Args:
        porcentaje: Brillo deseado, de 5 a 100.
    """
    porcentaje = max(5, min(100, porcentaje))  # nunca a 0: dejaría la pantalla negra
    run(["brightnessctl", "set", f"{porcentaje}%"])
    return f"Brillo al {porcentaje} por ciento."
