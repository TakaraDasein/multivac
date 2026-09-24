"""Servicio `multivac-core`: hub del bus y orquestador del agente.

Recibe frases de `ears` (o de `multivacctl`), las pasa por el agente y manda el
resultado a `voice`. El agente es bloqueante (Ollama + herramientas), así que se
ejecuta en un hilo del executor para no congelar el bus.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import time
import unicodedata
from functools import partial

from ..bus import BusServer, Message
from ..config import load, state_dir
from .agent import Agent

log = logging.getLogger("multivac.core")

STATE_FILE = state_dir() / "state"  # lo leen el widget de la barra y multivac-toggle

# Órdenes de despedida: apagan los tres servicios y liberan la GPU. Se detectan
# aquí y no en el agente porque apagarse no es una herramienta más — hay que
# hacerlo DESPUÉS de haber hablado, o se cortaría la despedida a media frase.
# Ojo con qué frases se incluyen: si el wake word tiene un falso positivo
# mientras suena un vídeo, se transcribe lo que diga el vídeo. "Nos vemos" y
# "hasta luego" son el cierre típico de cualquier YouTube (y ya apareció uno en
# los logs), así que quedan fuera: apagarse solo a media película es peor que
# tener que decir "vete".
DESPEDIDA = re.compile(
    r"^(?:multivac[,\s]+)?(?:ya\s+)?"
    r"(?:vete|vete ya|cierrate|cierra|apagate|apaga|duermete|descansa|"
    r"adios|desconectate|ya no te necesito|puedes irte|te puedes ir)"
    r"(?:\s+(?:multivac|por favor|ya|gracias))*[.!]?$"
)

# El tratamiento sale de config.toml, igual que en el resto de respuestas: si
# estuviera escrito aquí, cambiar `persona.tratamiento` no afectaría a las
# despedidas.
DESPEDIDAS_HABLADAS = [
    "Hasta luego, {tratamiento}. Aquí estaré cuando me necesite.",
    "Como usted diga. Que descanse, {tratamiento}.",
    "Me retiro. Llámeme cuando quiera, {tratamiento}.",
]


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower().strip())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return sin_tildes.strip(" .,;:!?¿¡\"'").strip()


class Core:
    def __init__(self) -> None:
        self.agent = Agent()
        self.bus = BusServer(self.handle)
        self._busy = asyncio.Lock()
        self._unmute_guard: asyncio.Task | None = None
        self._apagar_al_terminar = False
        # Último estado publicado. Se guarda porque decidir si una frase nueva
        # interrumpe o se descarta depende de si estamos hablando o pensando.
        self._estado = "off"
        # Identifica cada respuesta hablada: un speaking_done rezagado de una
        # respuesta anterior no debe reactivar el micro durante la siguiente.
        self._enunciado = 0

    async def set_state(self, state: str) -> None:
        """Publica el estado y lo deja en disco para la barra de estado."""
        self._estado = state
        try:
            STATE_FILE.write_text(state)
        except OSError:
            pass
        await self.bus.broadcast({"type": "state", "state": state})

    async def _a_chat(self, msg: Message) -> None:
        """Difunde a los clientes de chat, igual que `level` va a las barras.

        Si no hay ninguno conectado, `broadcast` no encuentra roles con el
        prefijo y no cuesta nada.
        """
        await self.bus.broadcast(msg, prefix="chat-")

    async def handle(self, msg: Message) -> None:
        kind = msg.get("type")

        if kind == "utterance":
            await self._on_utterance(msg.get("text", ""), reply_to=msg.get("from"))
        elif kind == "speaking_done":
            # `voice` terminó de hablar: volvemos a escuchar. Se ignoran los
            # avisos de enunciados ya superados.
            if int(msg.get("id", self._enunciado)) == self._enunciado:
                await self._resume_listening()
            else:
                log.debug("speaking_done rezagado del enunciado %s", msg.get("id"))
        elif kind == "ready":
            # `ears` ya cargó Whisper: a partir de ahora sí escucha de verdad.
            log.info("escucha lista")
            await self.set_state("idle")
        elif kind == "stop":
            # Cualquiera puede mandar callar: el atajo de teclado, la barra o
            # una ventana de chat.
            await self._interrumpir()
        elif kind == "listen":
            # Push-to-talk desde el atajo de teclado, vía multivacctl.
            await self.bus.send("ears", {"type": "listen"})
            await self.set_state("listening")
        elif kind == "level":
            # Volumen instantáneo de la voz, para la onda de la barra. Va solo
            # a las barras: si no hay ninguna, se descarta sin coste.
            await self.bus.broadcast(msg, prefix="bar-")
        elif kind == "ping":
            await self.bus.send(msg.get("from", "ctl"), {"type": "pong"})
        else:
            log.debug("mensaje ignorado: %s", kind)

    async def _on_utterance(self, text: str, reply_to: str | None) -> None:
        text = text.strip()
        if not text:
            # Escucha que no produjo nada (silencio o ruido): volvemos a idle.
            # Si está hablando o pensando, el estado lo gobierna ese turno.
            if not self._busy.locked() and self._estado != "speaking":
                await self.set_state("idle")
            return
        if self._busy.locked():
            # El lock solo sigue tomado mientras se piensa: la llamada al LLM
            # es bloqueante y no hay forma limpia de abortarla a mitad, así que
            # la frase se pierde. Al hablar el lock ya está libre y se cae en
            # la interrupción de abajo.
            log.info("pensando todavía, descarto: %r", text)
            return

        # Hablando: manda la frase nueva. Cortamos el enunciado en curso en vez
        # de dejar que se solapen dos voces.
        await self._interrumpir()

        await self._a_chat({"type": "chat_user", "text": text})

        if DESPEDIDA.match(_normalizar(text)):
            await self._despedirse(text, reply_to)
            return

        async with self._busy:
            log.info("usuario: %s", text)
            await self.set_state("thinking")
            # Silenciamos el micro ya: si no, el propio TTS se autoescucharía.
            await self.bus.send("ears", {"type": "mute", "on": True})

            loop = asyncio.get_running_loop()
            self._enunciado += 1
            id_enunciado = self._enunciado
            hablando = False
            arranque = time.monotonic()

            def decir(frase: str) -> None:
                """Publica una frase en cuanto el modelo la termina.

                Se llama desde el hilo del executor, de ahí el salto de vuelta
                al bucle de eventos.
                """
                nonlocal hablando
                if not hablando:
                    hablando = True
                    # Este número es la mejora que persigue el streaming: el
                    # tiempo que el usuario pasa en silencio antes de oír algo.
                    log.info(
                        "primera frase lista en %.2fs", time.monotonic() - arranque
                    )
                    asyncio.run_coroutine_threadsafe(
                        self._empezar_a_hablar(id_enunciado), loop
                    ).result()
                asyncio.run_coroutine_threadsafe(
                    self._difundir_frase(id_enunciado, frase), loop
                )

            try:
                answer = await loop.run_in_executor(
                    None, partial(self.agent.respond, text, on_sentence=decir)
                )
            except Exception:
                log.exception("fallo del agente")
                answer = "Disculpe, algo ha fallado por dentro. Inténtelo otra vez."

            log.info(
                "multivac (%.2fs): %s", time.monotonic() - arranque, answer
            )

            # Quien preguntó por texto quiere el texto completo de vuelta.
            if reply_to and reply_to != "ears":
                await self.bus.send(reply_to, {"type": "answer", "text": answer})

            if hablando:
                await self.bus.send("voice", {"type": "speak_end", "id": id_enunciado})
            else:
                # Nada se dijo por el camino (router, error, o confirmación):
                # el texto sale entero de una vez. `speak` ya abre y cierra su
                # propio enunciado, así que aquí NO va un speak_start.
                await self.set_state("speaking")
                await self.bus.send(
                    "voice", {"type": "speak", "id": id_enunciado, "text": answer}
                )
                # Por aquí pasan router, despedidas y errores: sin streaming no
                # hubo ningún chat_chunk, y el chat se quedaría en blanco.
                await self._a_chat({"type": "chat_chunk", "text": answer})
            await self._a_chat({"type": "chat_end"})
            self._arm_unmute_guard(answer)

    async def _difundir_frase(self, id_enunciado: int, frase: str) -> None:
        await self.bus.send(
            "voice", {"type": "speak_chunk", "id": id_enunciado, "text": frase}
        )
        await self._a_chat({"type": "chat_chunk", "text": frase})

    async def _interrumpir(self) -> bool:
        """Corta lo que `voice` esté diciendo y deja el sistema en `idle`.

        Solo tiene sentido mientras se habla: pensando no hay nada que cortar.
        """
        if self._estado != "speaking":
            return False

        log.info("interrupción: corto el enunciado %s", self._enunciado)
        await self.bus.send("voice", {"type": "stop"})
        # El `speaking_done` del enunciado cortado llegará igualmente; al
        # avanzar el contador deja de coincidir y no reactivará nada tarde.
        self._enunciado += 1
        if self._unmute_guard is not None:
            self._unmute_guard.cancel()
            self._unmute_guard = None
        if self._apagar_al_terminar:
            # Cortar la despedida es arrepentirse de ella: apagarse después
            # sería apagarse a destiempo, al final del turno siguiente.
            log.info("despedida interrumpida, cancelo el apagado")
            self._apagar_al_terminar = False
        await self.bus.send("ears", {"type": "mute", "on": False})
        await self.set_state("idle")
        await self._a_chat({"type": "chat_end"})
        return True

    async def _empezar_a_hablar(self, id_enunciado: int) -> None:
        await self.set_state("speaking")
        await self.bus.send("voice", {"type": "speak_start", "id": id_enunciado})

    async def _despedirse(self, text: str, reply_to: str | None) -> None:
        """Responde a "vete" y programa el apagado para cuando acabe de hablar."""
        import random

        log.info("usuario: %s (despedida)", text)
        tratamiento = load().get("persona", {}).get("tratamiento", "señor")
        respuesta = random.choice(DESPEDIDAS_HABLADAS).format(tratamiento=tratamiento)
        self._apagar_al_terminar = True

        if reply_to and reply_to != "ears":
            await self.bus.send(reply_to, {"type": "answer", "text": respuesta})
        self._enunciado += 1
        await self.set_state("speaking")
        await self.bus.send(
            "voice", {"type": "speak", "id": self._enunciado, "text": respuesta}
        )
        await self._a_chat({"type": "chat_chunk", "text": respuesta})
        await self._a_chat({"type": "chat_end"})
        self._arm_unmute_guard(respuesta)

    def _apagar(self) -> None:
        """Para los tres servicios y suelta el modelo de la GPU.

        Se lanza con systemd-run para que el proceso que ejecuta el apagado no
        sea hijo de este servicio: si lo fuera, systemd lo mataría a mitad de
        faena al parar la unidad.
        """
        log.info("apagando Multivac por orden de voz")
        # Los modelos salen de la config: escritos aquí a mano, cambiar
        # `llm.model` dejaría la VRAM ocupada, que es justo lo que se venía a
        # liberar. Se citan porque esto acaba en /bin/sh -c.
        llm = load().get("llm", {})
        modelos = [
            llm.get("model", "qwen3:8b"),
            llm.get("embed_model", "nomic-embed-text"),
        ]
        parar = "".join(f"ollama stop {shlex.quote(str(m))}; " for m in modelos if m)
        subprocess.Popen(
            [
                "systemd-run", "--user", "--collect", "--quiet",
                "--unit=multivac-apagado",
                "/bin/sh", "-c",
                parar + "systemctl --user stop multivac-core.service",
            ],
            start_new_session=True,
        )

    async def _resume_listening(self) -> None:
        if self._unmute_guard is not None:
            self._unmute_guard.cancel()
            self._unmute_guard = None

        if self._apagar_al_terminar:
            self._apagar_al_terminar = False
            STATE_FILE.write_text("off")
            self._apagar()
            return

        await self.bus.send("ears", {"type": "mute", "on": False})
        await self.set_state("idle")

    def _arm_unmute_guard(self, text: str) -> None:
        """Reactiva el micro aunque `voice` nunca conteste (caído o atascado).

        Sin esto, un fallo del TTS dejaría a Multivac sordo de forma permanente.
        El plazo se estima por longitud del texto, con un margen generoso.
        """
        timeout = 10.0 + len(text) / 10.0

        async def guard() -> None:
            try:
                await asyncio.sleep(timeout)
                log.warning("voice no confirmó en %.0fs; reactivo el micro", timeout)
                await self._resume_listening()
            except asyncio.CancelledError:
                pass

        if self._unmute_guard is not None:
            self._unmute_guard.cancel()
        self._unmute_guard = asyncio.create_task(guard())


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    core = Core()
    await core.bus.start()
    # "starting" hasta que ears confirme; así el botón sabe cuándo avisar de
    # que ya se le puede hablar.
    await core.set_state("starting")
    log.info("core listo, esperando a ears")
    await asyncio.Event().wait()  # servir para siempre


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
