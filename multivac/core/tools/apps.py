"""Índice de aplicaciones instaladas, leído de los ficheros .desktop.

En vez de mantener una lista de alias a mano, se descubren las apps del sistema
igual que hace un lanzador (walker, rofi): las entradas .desktop de
/usr/share/applications y ~/.local/share/applications. Así Multivac conoce
cualquier programa instalado, incluidas las webapps que crea Omarchy.
"""

from __future__ import annotations

import configparser
import difflib
import functools
import logging
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SEARCH_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
]

# Nombres coloquiales → texto que sí aparece en algún .desktop. Solo para
# palabras genéricas que nadie pone como nombre de aplicación.
SINONIMOS = {
    "navegador": ["brave", "chromium", "firefox"],
    "buscador": ["brave", "chromium", "firefox"],
    "terminal": ["alacritty", "ghostty", "kitty", "foot"],
    "consola": ["alacritty", "ghostty"],
    "editor": ["visual studio code", "code", "neovim"],
    "codigo": ["visual studio code", "code"],
    "archivos": ["nautilus", "files", "thunar"],
    "explorador": ["nautilus", "files"],
    "musica": ["spotify"],
    "calculadora": ["calculator", "galculator"],
    "correo": ["thunderbird", "mail"],
    "notas": ["obsidian", "typora"],
    "contraseñas": ["1password"],
    "juegos": ["steam"],
}


@dataclass
class App:
    """Una entrada .desktop lanzable."""

    desktop_id: str  # nombre de fichero sin .desktop; lo que espera gtk-launch
    name: str
    keywords: list[str] = field(default_factory=list)


def _normalizar(texto: str) -> str:
    """Minúsculas y sin acentos: 'Música' y 'musica' deben coincidir."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _leer_desktop(path: Path) -> App | None:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
        return None

    if entry.get("Type") != "Application":
        return None
    # NoDisplay marca entradas internas (manejadores de protocolo, etc.) que un
    # lanzador no debería ofrecer.
    if entry.getboolean("NoDisplay", fallback=False):
        return None
    if entry.getboolean("Hidden", fallback=False):
        return None

    name = entry.get("Name", "").strip()
    if not name:
        return None

    keywords = [name]
    for campo in ("GenericName", "Comment", "Keywords", "StartupWMClass"):
        if valor := entry.get(campo, "").strip():
            keywords.extend(v for v in valor.replace(";", " ").split() if v)
    # El binario también sirve para buscar ("brave-browser" → "brave").
    if exec_line := entry.get("Exec", "").strip():
        keywords.append(Path(exec_line.split()[0]).name)

    return App(desktop_id=path.stem, name=name, keywords=keywords)


@functools.lru_cache(maxsize=1)
def _indice_cacheado(_ventana: int) -> list[App]:
    apps: dict[str, App] = {}
    for directorio in SEARCH_DIRS:
        if not directorio.is_dir():
            continue
        for path in directorio.glob("*.desktop"):
            # Las entradas de ~/.local ganan a las del sistema (mismo stem).
            if (app := _leer_desktop(path)) is not None:
                apps[app.desktop_id] = app
    log.info("índice de aplicaciones: %d entradas", len(apps))
    return list(apps.values())


def indice() -> list[App]:
    """Índice de apps, recalculado como mucho cada 5 minutos."""
    return _indice_cacheado(int(time.time() // 300))


def buscar(consulta: str, limite: int = 5) -> list[App]:
    return [app for _, app in puntuar(consulta, limite)]


def puntuar(consulta: str, limite: int = 5) -> list[tuple[float, App]]:
    """Busca apps por nombre, con tolerancia a errores de transcripción.

    El texto viene de Whisper, así que puede traer acentos raros o palabras
    partidas; por eso se combinan coincidencia exacta, por prefijo, por
    subcadena y difusa, en ese orden de preferencia.
    """
    objetivo = _normalizar(consulta).strip()
    if not objetivo:
        return []

    candidatos = [objetivo, *SINONIMOS.get(objetivo, [])]
    apps = indice()
    puntuadas: list[tuple[float, App]] = []

    for app in apps:
        nombre = _normalizar(app.name)
        claves = [_normalizar(k) for k in app.keywords]
        mejor = 0.0
        for candidato in candidatos:
            if nombre == candidato:
                mejor = max(mejor, 1.0)
            elif nombre.startswith(candidato):
                mejor = max(mejor, 0.9)
            elif candidato in nombre:
                mejor = max(mejor, 0.8)
            elif any(candidato == k for k in claves):
                mejor = max(mejor, 0.75)
            elif any(candidato in k for k in claves):
                mejor = max(mejor, 0.6)
            else:
                ratio = difflib.SequenceMatcher(None, candidato, nombre).ratio()
                if ratio > 0.8:
                    mejor = max(mejor, ratio * 0.7)
        if mejor > 0:
            puntuadas.append((mejor, app))

    # A igualdad de puntuación, gana el nombre más corto: "Brave" antes que
    # "Brave (modo privado)".
    puntuadas.sort(key=lambda par: (-par[0], len(par[1].name)))
    return puntuadas[:limite]
