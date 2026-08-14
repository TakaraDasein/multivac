"""Utilidades de texto compartidas por `core` y `voice`.

Viven aquí y no en `voice/tts.py` para que `core` no tenga que importar Piper ni
sounddevice solo para trocear frases: son dos servicios distintos y el core no
toca el audio.
"""

from __future__ import annotations

import re

# Corte en puntuación fuerte seguida de espacio y comienzo de frase. Exigir la
# mayúscula (o signo de apertura) detrás evita partir por decimales como "3.5".
SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])")


def split_sentences(text: str) -> list[str]:
    partes = [p.strip() for p in SENTENCE.split(text) if p.strip()]
    return partes or ([text.strip()] if text.strip() else [])


class SentenceBuffer:
    """Acumula texto según llega del modelo y suelta frases ya completas.

    Sirve para empezar a hablar antes de que la respuesta esté entera. El mínimo
    de caracteres evita soltar fragmentos ridículos ("Claro.") como enunciado
    suelto, que suenan entrecortados; por debajo de ese umbral se agrupan con la
    frase siguiente.
    """

    def __init__(self, minimo: int = 25, minimo_primera: int = 12):
        self.minimo = minimo
        # La primera frase es la que rompe el silencio, así que se suelta antes
        # aunque sea corta: media frase de retraso ahí se nota mucho más que un
        # "Enseguida, señor." dicho por separado.
        self.minimo_primera = minimo_primera
        self._buffer = ""
        self._primera = True

    def add(self, fragmento: str) -> list[str]:
        """Añade un trozo y devuelve las frases que ya se pueden pronunciar."""
        self._buffer += fragmento
        listas: list[str] = []

        while (corte := self._siguiente_corte()) is not None:
            listas.append(self._buffer[: corte.start()].strip())
            self._buffer = self._buffer[corte.end():]
            self._primera = False

        return listas

    def _siguiente_corte(self) -> re.Match[str] | None:
        """Primer final de frase que deje por delante texto suficiente.

        Si una frase se queda corta ("Claro.") no se corta ahí: se sigue
        buscando el final siguiente, de modo que se pronuncie junto con la que
        viene. Cortar igualmente sonaría entrecortado, y quedarse esperando
        atascaría el buffer entero.
        """
        minimo = self.minimo_primera if self._primera else self.minimo
        desde = 0
        while (corte := SENTENCE.search(self._buffer, desde)) is not None:
            if corte.start() >= minimo:
                return corte
            desde = corte.end()
        return None

    def flush(self) -> str:
        """Lo que quede al terminar (la última frase no lleva separador detrás)."""
        resto = self._buffer.strip()
        self._buffer = ""
        self._primera = True
        return resto
