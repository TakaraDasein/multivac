"""Servicio `multivac-voice`: recibe texto del bus y lo pronuncia.

Admite dos formas de hablar:

- `speak`: un texto ya completo (despedidas, avisos de error).
- `speak_start` / `speak_chunk` / `speak_end`: frases que van llegando mientras
  el modelo todavía escribe. Se encolan y se reproducen en un único flujo de
  audio, así la respuesta empieza a sonar sin esperar a estar entera.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading

from ..bus import BusClient
from ..config import load
from .tts import Speaker, TtsConfig

log = logging.getLogger("multivac.voice")


class Reproductor:
    """Reproduce enunciados en un hilo aparte, alimentado por una cola.

    La reproducción bloquea, así que no puede vivir en el bucle de eventos: si
    lo hiciera, `voice` dejaría de atender el bus mientras habla y no podría
    recibir ni un `stop` ni las frases siguientes.
    """

    def __init__(self, speaker: Speaker, al_terminar):
        self.speaker = speaker
        self._al_terminar = al_terminar
        self._cola: queue.Queue = queue.Queue()
        # Enunciado que quedó por empezar porque llegó mientras sonaba otro.
        self._pendiente: int | None = None
        threading.Thread(target=self._bucle, daemon=True).start()

    def empezar(self, id_enunciado: int) -> None:
        self._cola.put(("inicio", id_enunciado))

    def añadir(self, texto: str) -> None:
        self._cola.put(("frase", texto))

    def terminar(self) -> None:
        self._cola.put(("fin", None))

    def cortar(self) -> None:
        self.speaker.stop()

    def _frases_de(self, id_enunciado: int):
        """Va sacando frases de la cola hasta el final del enunciado."""
        while True:
            tipo, dato = self._cola.get()
            if tipo == "fin":
                return
            if tipo == "inicio":
                # Un enunciado nuevo sin cerrar el anterior: no debería pasar,
                # pero si pasa, mejor abandonar el viejo que mezclarlos.
                log.warning("enunciado %s interrumpido por %s", id_enunciado, dato)
                self._pendiente = dato
                return
            yield dato

    def _bucle(self) -> None:
        while True:
            if self._pendiente is not None:
                id_enunciado, self._pendiente = self._pendiente, None
            else:
                tipo, dato = self._cola.get()
                if tipo != "inicio":
                    continue  # frases sueltas sin enunciado abierto: se ignoran
                id_enunciado = dato

            try:
                self.speaker.say_stream(self._frases_de(id_enunciado))
            except Exception:
                log.exception("fallo sintetizando")
            finally:
                # Pase lo que pase, `core` debe saber que puede volver a escuchar.
                self._al_terminar(id_enunciado)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    speaker = Speaker(TtsConfig(**load()["tts"]))
    bus = BusClient("voice")
    await bus.connect()
    loop = asyncio.get_running_loop()

    def avisar_terminado(id_enunciado: int) -> None:
        # Se llama desde el hilo del reproductor: hay que volver al bucle.
        asyncio.run_coroutine_threadsafe(
            bus.send({"type": "speaking_done", "id": id_enunciado}), loop
        )

    reproductor = Reproductor(speaker, avisar_terminado)
    log.info("voz lista")

    async for msg in bus.messages():
        kind = msg.get("type")

        if kind == "speak":
            # Texto completo de una vez: se envuelve como un enunciado corto.
            texto = msg.get("text", "").strip()
            id_enunciado = int(msg.get("id", 0))
            reproductor.empezar(id_enunciado)
            if texto:
                reproductor.añadir(texto)
            reproductor.terminar()

        elif kind == "speak_start":
            reproductor.empezar(int(msg.get("id", 0)))
        elif kind == "speak_chunk":
            if texto := msg.get("text", "").strip():
                reproductor.añadir(texto)
        elif kind == "speak_end":
            reproductor.terminar()
        elif kind == "stop":
            reproductor.cortar()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
