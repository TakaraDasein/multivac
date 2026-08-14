"""Búsquedas y navegación web.

Multivac no navega ni lee páginas: abre la búsqueda en tu navegador. Es lo que
quieres en un asistente de voz — la respuesta la lees tú, y no hay que meter un
scraper ni una API de búsqueda de pago en el camino.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus, urlparse

from .registry import launch, tool

BUSCADORES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "wikipedia": "https://es.wikipedia.org/w/index.php?search={}",
    "maps": "https://www.google.com/maps/search/{}",
    "github": "https://github.com/search?q={}",
    "imagenes": "https://www.google.com/search?tbm=isch&q={}",
}

# Sitios que se piden por nombre en voz alta.
SITIOS = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "drive": "https://drive.google.com",
    "maps": "https://maps.google.com",
    "netflix": "https://www.netflix.com",
    "twitch": "https://www.twitch.tv",
    "spotify": "https://open.spotify.com",
    "wikipedia": "https://es.wikipedia.org",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "x": "https://x.com",
    "twitter": "https://x.com",
    "reddit": "https://www.reddit.com",
    "whatsapp": "https://web.whatsapp.com",
    "correo": "https://mail.google.com",
}


def abrir_url(url: str) -> None:
    """Abre una URL en Brave, con Hyprland como padre del proceso.

    Se nombra el navegador explícitamente en vez de usar xdg-open: así la URL
    siempre acaba en Brave, sin depender de cuál sea el navegador por defecto
    del sistema en ese momento.
    """
    launch(["brave", url])


@tool()
def buscar_en_web(
    consulta: str,
    donde: Literal["google", "youtube", "wikipedia", "maps", "github", "imagenes"] = "google",
) -> str:
    """Busca algo en internet y abre los resultados en el navegador.

    Args:
        consulta: Lo que hay que buscar.
        donde: Buscador a usar. Por defecto Google.
    """
    consulta = consulta.strip()
    if not consulta:
        return "¿Qué quieres que busque?"
    plantilla = BUSCADORES.get(donde, BUSCADORES["google"])
    abrir_url(plantilla.format(quote_plus(consulta)))
    return f"Buscando {consulta} en {donde}."


@tool()
def abrir_web(sitio: str) -> str:
    """Abre una página web concreta en el navegador.

    Args:
        sitio: Nombre de un sitio conocido ("youtube", "gmail") o una dirección.
    """
    sitio = sitio.strip().lower().rstrip("/")
    if not sitio:
        return "¿Qué página quieres abrir?"

    if url := SITIOS.get(sitio):
        abrir_url(url)
        return f"Abriendo {sitio}."

    # ¿Parece un dominio? Se le pone el esquema y se abre.
    candidato = sitio if "://" in sitio else f"https://{sitio}"
    partes = urlparse(candidato)
    if partes.netloc and "." in partes.netloc and " " not in partes.netloc:
        abrir_url(candidato)
        return f"Abriendo {partes.netloc}."

    # No es un dominio: lo más útil es buscarlo.
    abrir_url(BUSCADORES["google"].format(quote_plus(sitio)))
    return f"No conocía esa página, así que he buscado {sitio} en Google."


@tool()
def reproducir_en_youtube(consulta: str) -> str:
    """Busca algo en YouTube y abre el primer resultado directamente.

    Args:
        consulta: Canción, vídeo o tema que quieres ver.
    """
    consulta = consulta.strip()
    if not consulta:
        return "¿Qué quieres que ponga?"
    # El truco de la lista de reproducción automática: YouTube reproduce el
    # primer resultado sin pasar por la página de búsqueda.
    url = f"https://www.youtube.com/results?search_query={quote_plus(consulta)}"
    abrir_url(url)
    return f"Buscando {consulta} en YouTube."
