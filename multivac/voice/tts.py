"""Síntesis de voz con Piper, troceada por frases.

Piper genera más rápido que el tiempo real, así que sintetizamos frase a frase y
las vamos reproduciendo: la respuesta empieza a sonar antes de estar completa.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from ..text import split_sentences

log = logging.getLogger(__name__)

VOICES_DIR = Path.home() / ".local/share/piper-voices"


@dataclass
class TtsConfig:
    voice: str
    speed: float = 1.0
    # Algunos modelos traen varias voces en el mismo fichero (sharvard: M=0,
    # F=1). None usa la que el modelo tenga por defecto.
    speaker: int | None = None
    # Variabilidad de la entonación. Más bajo = más suave y uniforme.
    noise_scale: float | None = None
    # Variabilidad de la duración de cada fonema. Más bajo = dicción más
    # ordenada, menos atropellada.
    noise_w: float | None = None
    volume: float = 1.0


class Speaker:
    def __init__(self, cfg: TtsConfig):
        model = VOICES_DIR / f"{cfg.voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(
                f"falta la voz {model}; descárgala de rhasspy/piper-voices"
            )
        self.voice = PiperVoice.load(model)
        # length_scale < 1 acelera el habla.
        self.syn = SynthesisConfig(
            length_scale=1.0 / max(0.1, cfg.speed),
            speaker_id=cfg.speaker,
            noise_scale=cfg.noise_scale,
            noise_w_scale=cfg.noise_w,
            volume=cfg.volume,
        )
        self.rate = self.voice.config.sample_rate
        self._stop = False

    def stop(self) -> None:
        """Corta la reproducción en curso (para poder interrumpir a Multivac)."""
        self._stop = True
        sd.stop()

    def say(self, text: str) -> None:
        """Pronuncia un texto ya completo."""
        self.say_stream(iter([text]))

    def say_stream(self, frases: Iterable[str]) -> None:
        """Pronuncia frases según van llegando del iterable.

        Se abre UN solo OutputStream para todo el enunciado: abrir y cerrar uno
        por frase mete un clic y un pequeño retardo entre ellas, y la respuesta
        sonaría a trompicones en vez de continua. El iterable puede bloquear
        entre frases —es justo lo que pasa mientras el modelo sigue escribiendo—
        y el stream aguanta ese silencio sin cortarse.
        """
        self._stop = False
        stream: sd.OutputStream | None = None
        try:
            for frase in frases:
                if self._stop:
                    break
                frase = frase.strip()
                if not frase:
                    continue
                for audio in self._synthesize(frase):
                    if self._stop:
                        break
                    # El stream se abre con la primera frase, no antes: así no
                    # se retiene el dispositivo de audio mientras se piensa.
                    if stream is None:
                        stream = sd.OutputStream(
                            samplerate=self.rate, channels=1, dtype="int16"
                        )
                        stream.start()
                    stream.write(audio)
        finally:
            if stream is not None:
                if not self._stop:
                    # Sin esto se cortaría la última sílaba: write() solo encola.
                    time.sleep(stream.latency)
                stream.stop()
                stream.close()

    def _synthesize(self, text: str) -> Iterator[np.ndarray]:
        for sentence in split_sentences(text):
            if self._stop:
                return
            for chunk in self.voice.synthesize(sentence, syn_config=self.syn):
                yield np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
