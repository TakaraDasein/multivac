"""Síntesis de voz con Piper, troceada por frases.

Piper genera más rápido que el tiempo real, así que sintetizamos frase a frase y
las vamos reproduciendo: la respuesta empieza a sonar antes de estar completa.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

log = logging.getLogger(__name__)

VOICES_DIR = Path.home() / ".local/share/piper-voices"

# Cortamos en puntuación fuerte; el límite de 2 caracteres evita partir por
# abreviaturas o iniciales sueltas.
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])")


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


def split_sentences(text: str) -> list[str]:
    partes = [p.strip() for p in _SENTENCE.split(text) if p.strip()]
    return partes or ([text.strip()] if text.strip() else [])


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
        self._stop = False
        with sd.OutputStream(samplerate=self.rate, channels=1, dtype="int16") as stream:
            for chunk in self._synthesize(text):
                if self._stop:
                    break
                stream.write(chunk)

    def _synthesize(self, text: str) -> Iterator[np.ndarray]:
        for sentence in split_sentences(text):
            if self._stop:
                return
            for chunk in self.voice.synthesize(sentence, syn_config=self.syn):
                yield np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
