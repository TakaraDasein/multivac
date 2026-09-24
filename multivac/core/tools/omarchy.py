"""Integración con Omarchy: temas, menú y bloqueo de pantalla.

Omarchy trae su propio centro de mando (`omarchy <grupo> <comando>`), pero la
mayoría de sus comandos actualizan, instalan o borran cosas. Aquí solo se usan
los que son reversibles y no tocan paquetes ni ficheros del usuario; el resto
los rechaza `_check_omarchy` en `registry.run()`, que es el único cuello de
botella por el que Multivac puede ejecutar nada.
"""

from __future__ import annotations

import unicodedata

from .registry import run, tool


def _normalizar(texto: str) -> str:
    """Quita acentos, guiones y mayúsculas para comparar nombres dictados.

    El usuario dice "tokio night" o "catppuccin latte" y el STT no acierta con
    los guiones ni con las mayúsculas que usa `omarchy theme list`.
    """
    plano = unicodedata.normalize("NFKD", texto.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return "".join(c for c in plano if c.isalnum())


def _temas() -> list[str]:
    """Temas realmente instalados, tal y como los nombra Omarchy.

    Se preguntan al sistema en vez de mantener una lista escrita a mano, que se
    quedaría vieja en cuanto el usuario instale o quite un tema.
    """
    salida = run(["omarchy", "theme", "list"])
    if salida.startswith("(fallo"):
        return []
    return [linea.strip() for linea in salida.splitlines() if linea.strip()]


@tool()
def listar_temas() -> str:
    """Dice qué temas de escritorio hay instalados y cuál está puesto ahora."""
    temas = _temas()
    if not temas:
        return "No pude consultar los temas instalados."
    actual = run(["omarchy", "theme", "current"]).strip()
    # Son más de cien en un equipo con temas propios: decirlos todos por voz no
    # sirve de nada, así que se da el recuento y una muestra.
    muestra = ", ".join(temas[:12])
    cola = f" y {len(temas) - 12} más" if len(temas) > 12 else ""
    return f"Tienes {len(temas)} temas. Ahora está {actual}. Por ejemplo: {muestra}{cola}."


@tool()
def cambiar_tema(nombre: str) -> str:
    """Cambia el tema visual del escritorio.

    Args:
        nombre: Nombre del tema, p.ej. "Tokyo Night", "Gruvbox", "Osaka Jade".
    """
    temas = _temas()
    if not temas:
        return "No pude consultar los temas instalados."

    pedido = _normalizar(nombre)
    exacto = next((t for t in temas if _normalizar(t) == pedido), None)
    # Si no hay coincidencia exacta se acepta una parcial: "jade" → "Osaka Jade".
    elegido = exacto or next((t for t in temas if pedido and pedido in _normalizar(t)), None)
    if elegido is None:
        return f"No tengo ningún tema que se llame {nombre}."

    salida = run(["omarchy", "theme", "set", elegido], timeout=30.0)
    if salida.startswith("(fallo"):
        return f"No pude aplicar el tema {elegido}."
    return f"Tema cambiado a {elegido}."


@tool(confirm=True)
def bloquear_pantalla() -> str:
    """Bloquea la pantalla del ordenador y apaga el monitor."""
    # confirm=True porque, aunque no destruye nada, deja al usuario fuera hasta
    # que teclee su contraseña: no es algo que deba pasar por un falso positivo
    # del reconocimiento de voz.
    run(["omarchy", "system", "lock"], timeout=20.0)
    return "Bloqueando."


@tool()
def abrir_menu() -> str:
    """Abre el menú de Omarchy, desde el que se controla todo el sistema."""
    salida = run(["omarchy", "menu"], timeout=20.0)
    if salida.startswith("(fallo"):
        return "No pude abrir el menú."
    return "Menú abierto."
