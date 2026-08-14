"""Servicio `multivac-voice`: recibe texto del bus y lo pronuncia."""

from __future__ import annotations

import asyncio
import logging

from ..bus import BusClient
from ..config import load
from .tts import Speaker, TtsConfig

log = logging.getLogger("multivac.voice")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    speaker = Speaker(TtsConfig(**load()["tts"]))
    bus = BusClient("voice")
    await bus.connect()
    log.info("voz lista")

    loop = asyncio.get_running_loop()

    async for msg in bus.messages():
        kind = msg.get("type")
        if kind == "speak":
            text = msg.get("text", "").strip()
            if not text:
                await bus.send({"type": "speaking_done"})
                continue
            try:
                # Reproducir bloquea: fuera del bucle de eventos, para poder
                # seguir recibiendo un "stop" mientras habla.
                await loop.run_in_executor(None, speaker.say, text)
            except Exception:
                log.exception("fallo sintetizando")
            finally:
                # Pase lo que pase, `core` debe saber que puede volver a escuchar.
                await bus.send({"type": "speaking_done"})
        elif kind == "stop":
            speaker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
