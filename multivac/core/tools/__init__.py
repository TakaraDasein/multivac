"""Importar este paquete puebla el REGISTRY con todas las herramientas.

Multivac está deliberadamente limitado a abrir aplicaciones, buscar en la web y
consultar el estado del equipo. `media.py` (volumen, brillo, reproducción) sigue
en el repositorio pero NO se importa: bastaría añadirlo a esta línea para
reactivar esas herramientas.
"""

from . import desktop, system, web  # noqa: F401
from .registry import REGISTRY, call, schemas  # noqa: F401

__all__ = ["REGISTRY", "call", "schemas"]
