"""Servicio `multivac-ears`: micrófono → wake word → VAD → texto → bus.

La captura y la inferencia son bloqueantes, así que viven en un hilo aparte. El
hilo publica texto en una cola; el bucle asyncio la vacía hacia el bus. Los
mensajes que llegan del bus (mute, listen) se aplican sobre el objeto
`WakeListener`, cuyos flags son thread-safe (threading.Event).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading

import numpy as np
import sounddevice as sd

from ..bus import BusClient
from ..config import load
from .stt import SttConfig, Transcriber
from .wake import FRAME, SAMPLE_RATE, WakeConfig, WakeListener

log = logging.getLogger("multivac.ears")


def _frames(audio_q: "queue.Queue[np.ndarray]"):
    """Generador infinito de tramas provenientes del callback de sounddevice."""
    while True:
        yield audio_q.get()


def _capture_thread(
    out: "queue.Queue[str]",
    ready: "queue.Queue[WakeListener]",
    stop: threading.Event,
) -> None:
    cfg = load()
    listener = WakeListener(WakeConfig(**cfg["wake"]))
    # Se publica antes de cargar Whisper para que el push-to-talk quede
    # disponible cuanto antes.
    ready.put(listener)
    transcriber = Transcriber(SttConfig(**cfg["stt"]))

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def on_audio(indata, _frames_count, _time_info, status):
        if status:
            log.debug("estado de audio: %s", status)
        audio_q.put(indata[:, 0].copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=FRAME,
        channels=1,
        dtype="int16",
        callback=on_audio,
    ):
        log.info("escuchando; di la palabra de activación")
        out.put("__ready__")
        for audio in listener.process(_frames(audio_q)):
            if stop.is_set():
                return
            was_muted = listener.muted
            listener.set_muted(True)  # no reaccionar a la propia transcripción
            try:
                text = transcriber.transcribe(audio)
            finally:
                listener.set_muted(was_muted)
            # Siempre se publica algo, aunque la transcripción venga vacía: el
            # core necesita saber que la escucha terminó para salir del estado
            # "listening" (si no, el indicador se queda colgado ahí).
            out.put(text or "")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    bus = BusClient("ears")
    await bus.connect()

    out: "queue.Queue[str]" = queue.Queue()
    ready: "queue.Queue[WakeListener]" = queue.Queue()
    stop = threading.Event()
    threading.Thread(
        target=_capture_thread, args=(out, ready, stop), daemon=True
    ).start()

    loop = asyncio.get_running_loop()
    listener = await loop.run_in_executor(None, ready.get)

    async def pump_to_bus() -> None:
        while True:
            text = await loop.run_in_executor(None, out.get)
            if text == "__ready__":
                log.info("micrófono activo")
                # Whisper ya está cargado y el micro abierto: hasta este momento
                # Multivac no oye nada, así que es ahora cuando el core puede
                # anunciarse como disponible.
                await bus.send({"type": "ready"})
                continue
            await bus.send({"type": "utterance", "text": text})

    async def handle_from_bus() -> None:
        async for msg in bus.messages():
            kind = msg.get("type")
            if kind == "mute":
                # `core` nos silencia mientras Multivac habla, para que su propia
                # voz no dispare la palabra de activación.
                listener.set_muted(bool(msg.get("on")))
            elif kind == "listen":
                listener.force_listen.set()

    try:
        await asyncio.gather(pump_to_bus(), handle_from_bus())
    finally:
        stop.set()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
