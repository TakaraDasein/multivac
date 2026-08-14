"""Síntesis de voz con Piper, troceada por frases.

Piper genera más rápido que el tiempo real, así que sintetizamos frase a frase y
las vamos reproduciendo: la respuesta empieza a sonar antes de estar completa.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from ..text import split_sentences

log = logging.getLogger(__name__)

VOICES_DIR = Path.home() / ".local/share/piper-voices"

# Tamaño del bloque con el que se alimenta la tarjeta de sonido: ~46 ms a
# 22 kHz. Piper devuelve trozos de duración arbitraria (una frase entera puede
# venir de golpe), y de ahí no se puede sacar el nivel instantáneo de la voz.
# Troceando a bloques fijos, cada `write` bloquea hasta que hay hueco en el
# búfer, así que el ritmo al que se miden los niveles es el ritmo al que suenan.
BLOQUE = 1024


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
        # Se llama con el volumen (0..1) de cada bloque que suena, desde el hilo
        # de reproducción. Lo usa la barra de estado para dibujar la onda.
        self.on_level: Callable[[float], None] | None = None

    def stop(self) -> None:
        """Corta la reproducción en curso (para poder interrumpir a Multivac)."""
        self._stop = True
        sd.stop()
        self._nivel(0.0)

    def _nivel(self, valor: float) -> None:
        if self.on_level is None:
            return
        try:
            self.on_level(valor)
        except Exception:
            # Un fallo pintando la onda no puede callar a Multivac.
            log.debug("on_level falló", exc_info=True)

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
                    self._reproducir(stream, audio)
        finally:
            if stream is not None:
                if not self._stop:
                    # Sin esto se cortaría la última sílaba: write() solo encola.
                    time.sleep(stream.latency)
                stream.stop()
                stream.close()
            self._nivel(0.0)

    def _reproducir(self, stream: sd.OutputStream, audio: np.ndarray) -> None:
        """Envía el audio en bloques y va publicando el volumen de cada uno."""
        for inicio in range(0, len(audio), BLOQUE):
            if self._stop:
                return
            bloque = audio[inicio : inicio + BLOQUE]
            stream.write(bloque)
            # RMS normalizado al fondo de escala de int16.
            rms = float(np.sqrt(np.mean(np.square(bloque.astype(np.float32)))))
            self._nivel(min(1.0, rms / 32768.0))

    def _synthesize(self, text: str) -> Iterator[np.ndarray]:
        for sentence in split_sentences(text):
            if self._stop:
                return
            for chunk in self.voice.synthesize(sentence, syn_config=self.syn):
                yield np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
