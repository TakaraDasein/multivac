"""Transcripción con faster-whisper (CTranslate2) sobre GPU."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from . import cuda_libs

cuda_libs.preload()  # antes de importar faster_whisper/ctranslate2

from faster_whisper import WhisperModel  # noqa: E402

log = logging.getLogger(__name__)


@dataclass
class SttConfig:
    model: str
    device: str
    compute_type: str
    language: str
    beam_size: int


class Transcriber:
    def __init__(self, cfg: SttConfig):
        self.cfg = cfg
        log.info("cargando whisper %s en %s (%s)", cfg.model, cfg.device, cfg.compute_type)
        try:
            self.model = WhisperModel(
                cfg.model, device=cfg.device, compute_type=cfg.compute_type
            )
            # El constructor no toca la GPU: los fallos de cuBLAS/cuDNN solo
            # afloran en la primera inferencia. Forzamos una aquí para poder
            # caer a CPU en el arranque y no a mitad de una conversación.
            self._warmup()
        except Exception as exc:  # CUDA ausente, VRAM llena, cuDNN incompleto…
            log.warning("fallo usando %s (%s); recurro a CPU", cfg.device, exc)
            self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
            self._warmup()
        log.info("whisper listo")

    def _warmup(self) -> None:
        segments, _ = self.model.transcribe(
            np.zeros(16_000, dtype=np.float32), language=self.cfg.language
        )
        list(segments)  # el generador es perezoso: hay que consumirlo

    def transcribe(self, audio: np.ndarray) -> str:
        """`audio` es int16 mono a 16 kHz; whisper espera float32 en [-1, 1]."""
        samples = audio.astype(np.float32) / 32768.0
        started = time.monotonic()
        segments, _ = self.model.transcribe(
            samples,
            language=self.cfg.language,
            beam_size=self.cfg.beam_size,
            vad_filter=False,  # el VAD ya se aplicó al capturar
            condition_on_previous_text=False,  # evita bucles de repetición
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info(
            "transcrito en %.2fs (%.1fs de audio): %r",
            time.monotonic() - started,
            len(audio) / 16_000,
            text,
        )
        return text
