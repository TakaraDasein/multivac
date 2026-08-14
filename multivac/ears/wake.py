"""Detección de palabra de activación y captura de la frase que la sigue.

openWakeWord consume tramas de 1280 muestras (80 ms @ 16 kHz) en int16. El VAD
de Silero viene incluido en el propio paquete, así que lo reutilizamos en vez de
añadir otra dependencia.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Iterator

import numpy as np
from openwakeword.model import Model
from openwakeword.vad import VAD

SAMPLE_RATE = 16_000
FRAME = 1280  # muestras por trama (80 ms), exigido por openWakeWord
_FRAME_SECONDS = FRAME / SAMPLE_RATE

log = logging.getLogger(__name__)


@dataclass
class WakeConfig:
    models: list[str]
    threshold: float
    silence_timeout: float
    max_utterance: float
    # Margen de audio previo al fin de la wake word que se conserva, para no
    # cortar la primera sílaba de quien empieza a hablar de inmediato.
    preroll: float = 0.3


class WakeListener:
    def __init__(self, cfg: WakeConfig):
        self.cfg = cfg
        # ONNX en vez de tflite: mismo modelo, y evita el runtime de tflite en
        # el camino caliente.
        self.model = Model(wakeword_models=cfg.models, inference_framework="onnx")
        self.vad = VAD()
        # Se toca desde el hilo del bus y se lee desde el de captura.
        self._muted = threading.Event()
        # Lo activa el atajo de teclado: graba sin esperar la palabra clave.
        self.force_listen = threading.Event()

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    def set_muted(self, muted: bool) -> None:
        """Silencia la detección mientras Multivac habla (evita auto-activarse)."""
        if muted:
            self._muted.set()
            self.model.reset()
        else:
            self._muted.clear()

    def process(self, frames: Iterator[np.ndarray]) -> Iterator[np.ndarray]:
        """Consume tramas int16 y emite el audio de cada frase detectada."""
        preroll_len = max(1, int(self.cfg.preroll / _FRAME_SECONDS))
        preroll: list[np.ndarray] = []

        for frame in frames:
            preroll.append(frame)
            if len(preroll) > preroll_len:
                preroll.pop(0)

            forced = self.force_listen.is_set()
            if forced:
                self.force_listen.clear()
                log.info("escucha forzada (push-to-talk)")
            else:
                # El silencio solo bloquea la palabra clave; el atajo de teclado
                # debe funcionar incluso mientras Multivac habla.
                if self._muted.is_set():
                    continue
                scores = self.model.predict(frame)
                if max(scores.values()) < self.cfg.threshold:
                    continue
                name = max(scores, key=scores.get)
                log.info("wake word detectada (%s, %.2f)", name, scores[name])
            # Sin reset, el buffer interno seguiría disparando en las tramas
            # siguientes con la misma activación.
            self.model.reset()
            self.vad.reset_states()

            audio = self._record(frames, preroll.copy())
            preroll.clear()
            if audio is not None:
                yield audio

    def _record(
        self, frames: Iterator[np.ndarray], initial: list[np.ndarray]
    ) -> np.ndarray | None:
        """Graba hasta detectar silencio sostenido o agotar el tiempo máximo."""
        collected = list(initial)
        silence = 0.0
        elapsed = 0.0
        heard_speech = False

        for frame in frames:
            collected.append(frame)
            elapsed += _FRAME_SECONDS

            speech = float(self.vad.predict(frame)) > 0.5
            if speech:
                heard_speech = True
                silence = 0.0
            else:
                silence += _FRAME_SECONDS

            # Solo cortamos por silencio una vez que hubo voz: si no, un
            # arranque lento cerraría la captura antes de empezar.
            if heard_speech and silence >= self.cfg.silence_timeout:
                break
            if elapsed >= self.cfg.max_utterance:
                log.warning("corte por duración máxima (%.1fs)", elapsed)
                break

        if not heard_speech:
            log.info("activación sin voz posterior, descartada")
            return None
        return np.concatenate(collected)
