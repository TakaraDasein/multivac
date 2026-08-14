"""Router de intención: resuelve "abre X" y "busca Y" sin pasar por el LLM.

Son las dos órdenes que más se repiten y no tienen ambigüedad real, así que
dejarlas en manos del modelo solo añade latencia y una posibilidad de que se
equivoque de herramienta. Aquí se reconocen por el verbo y se ejecutan directas;
todo lo demás sigue el camino normal por el agente.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from .tools import apps, desktop, web

log = logging.getLogger(__name__)

# Formas de "abrir" que se usan al hablar, incluidas las que Whisper devuelve
# sin tilde.
_ABRIR = r"abre|abrir|abreme|abrime|inicia|iniciar|lanza|lanzar|arranca|ejecuta|pon en marcha|ábreme"
_BUSCAR = r"busca|buscar|buscame|busque|investiga|googlea|google"

# Artículos y muletillas que sobran tras el verbo: "abre *la* calculadora".
_RELLENO = r"(?:me\s+)?(?:la|el|los|las|mi|mis|un|una|unos|unas)?\s*"

RE_ABRIR = re.compile(rf"^(?:{_ABRIR})\s+{_RELLENO}(.+)$", re.IGNORECASE)
RE_BUSCAR = re.compile(rf"^(?:{_BUSCAR})\s+{_RELLENO}(.+)$", re.IGNORECASE)

# Buscadores nombrables por voz → clave de web.BUSCADORES.
BUSCADORES_HABLADOS = {
    "google": "google",
    "el buscador": "google",
    "internet": "google",
    "la web": "google",
    "youtube": "youtube",
    "yutub": "youtube",
    "wikipedia": "wikipedia",
    "la wiki": "wikipedia",
    "maps": "maps",
    "google maps": "maps",
    "el mapa": "maps",
    "mapas": "maps",
    "github": "github",
    "imagenes": "imagenes",
    "google imagenes": "imagenes",
    "fotos": "imagenes",
}

# Por debajo de esto, la coincidencia con una app instalada es demasiado floja
# para abrirla sin preguntar.
UMBRAL_APP = 0.6


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower().strip())
    sin_tildes = "".join(
        c for c in descompuesto if unicodedata.category(c) != "Mn"
    )
    # Whisper puntúa las frases; los signos estorban al comparar.
    return sin_tildes.strip(" .,;:!?¿¡\"'").strip()


def _extraer_buscador(consulta: str) -> tuple[str, str]:
    """Separa el buscador de la consulta: devuelve (consulta, buscador).

    Acepta las dos formas naturales: "busca *en youtube* vallenato" y
    "busca vallenato *en youtube*".
    """
    texto = consulta.strip()

    # "en <buscador> <consulta>"
    for nombre, clave in sorted(
        BUSCADORES_HABLADOS.items(), key=lambda kv: -len(kv[0])
    ):
        prefijo = f"en {nombre} "
        if _normalizar(texto).startswith(prefijo):
            return texto[len(prefijo):].strip(), clave

    # "<consulta> en <buscador>"
    for nombre, clave in sorted(
        BUSCADORES_HABLADOS.items(), key=lambda kv: -len(kv[0])
    ):
        sufijo = f" en {nombre}"
        normalizado = _normalizar(texto)
        if normalizado.endswith(sufijo):
            return texto[: len(texto) - len(sufijo)].strip(), clave

    return texto, "google"


def _abrir(objetivo: str) -> str:
    objetivo = _normalizar(objetivo)
    if not objetivo:
        return "¿Qué quieres que abra?"

    # 1) ¿Hay una aplicación instalada que encaje bien?
    puntuadas = apps.puntuar(objetivo, limite=1)
    if puntuadas and puntuadas[0][0] >= UMBRAL_APP:
        app = puntuadas[0][1]
        desktop.lanzar(app.desktop_id)
        log.info("router: abrir app %s", app.name)
        return f"Abriendo {app.name}."

    # 2) ¿Es un sitio web conocido o un dominio? "abre youtube", "abre x.com".
    if objetivo in web.SITIOS or "." in objetivo.replace(" ", ""):
        log.info("router: abrir web %s", objetivo)
        return web.abrir_web(objetivo)

    return f"No tengo instalada ninguna aplicación que se llame {objetivo}."


def _buscar(consulta: str) -> str:
    consulta, donde = _extraer_buscador(consulta)
    if not consulta.strip():
        return "¿Qué quieres que busque?"
    log.info("router: buscar %r en %s", consulta, donde)
    return web.buscar_en_web(consulta, donde)


def resolver(texto: str) -> str | None:
    """Devuelve la respuesta si la frase es una orden directa, o None.

    None significa "esto no es un 'abre' ni un 'busca'": que lo gestione el
    agente con el LLM.
    """
    normalizado = _normalizar(texto)
    if not normalizado:
        return None

    if match := RE_ABRIR.match(normalizado):
        return _abrir(match.group(1))
    if match := RE_BUSCAR.match(normalizado):
        # La consulta se toma del texto original para no perder tildes ni
        # mayúsculas en lo que se va a buscar.
        crudo = _recortar_verbo(texto, match.group(1))
        return _buscar(crudo)
    return None


def _recortar_verbo(original: str, resto_normalizado: str) -> str:
    """Recupera del texto original el trozo que sigue al verbo.

    Se alinea por longitud: el normalizado solo quita tildes y signos, así que
    conserva el número de palabras.
    """
    palabras_resto = len(resto_normalizado.split())
    palabras = original.strip().strip(".,;:!?¿¡").split()
    return " ".join(palabras[-palabras_resto:]) if palabras_resto else ""
