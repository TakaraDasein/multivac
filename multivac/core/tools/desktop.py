"""Control de Hyprland: lanzar apps, workspaces y ventanas."""

from __future__ import annotations

import json

from . import apps
from .registry import launch, run, tool


def lanzar(desktop_id: str) -> None:
    """Lanza una entrada .desktop dejando a Hyprland como padre del proceso.

    Si lanzáramos el proceso como hijo nuestro, systemd lo mataría junto al
    servicio al reiniciarlo. `gtk-launch` se encarga de interpretar la línea
    Exec del .desktop (los %U, %F y demás).
    """
    launch(["gtk-launch", desktop_id])


@tool()
def abrir_app(nombre: str) -> str:
    """Abre una aplicación instalada en el ordenador.

    Args:
        nombre: Nombre de la aplicación, p.ej. "Spotify", "navegador", "Blender".
    """
    encontradas = apps.buscar(nombre, limite=3)
    if not encontradas:
        return f"No tengo instalada ninguna aplicación que se llame {nombre}."
    elegida = encontradas[0]
    lanzar(elegida.desktop_id)
    return f"Abriendo {elegida.name}."


@tool()
def buscar_app(nombre: str) -> str:
    """Busca qué aplicaciones instaladas coinciden con un nombre, sin abrirlas.

    Args:
        nombre: Texto a buscar entre las aplicaciones instaladas.
    """
    encontradas = apps.buscar(nombre, limite=5)
    if not encontradas:
        return f"No encuentro nada instalado parecido a {nombre}."
    return "Tienes: " + ", ".join(a.name for a in encontradas) + "."


@tool()
def listar_apps_abiertas() -> str:
    """Lista las ventanas abiertas ahora mismo, con su workspace."""
    salida = run(["hyprctl", "-j", "clients"])
    try:
        clientes = json.loads(salida)
    except json.JSONDecodeError:
        return "No pude leer la lista de ventanas."
    visibles = [c for c in clientes if c.get("mapped")]
    if not visibles:
        return "No hay ventanas abiertas."
    return "; ".join(
        f"{c.get('class') or '?'} en workspace {c.get('workspace', {}).get('id', '?')}"
        for c in visibles
    )


@tool()
def notificar(mensaje: str) -> str:
    """Muestra una notificación en pantalla.

    Args:
        mensaje: Texto a mostrar.
    """
    run(["notify-send", "Multivac", mensaje])
    return "Notificación enviada."
