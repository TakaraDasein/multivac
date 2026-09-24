"""Bucle de agente: conversación con Ollama y ejecución de herramientas."""

from __future__ import annotations

import datetime as dt
import logging
import time
import unicodedata
from typing import Any, Callable

import ollama

from ..config import load
from . import router, tools
from ..text import SentenceBuffer
from .memory import Memory

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Eres Multivac, la asistente personal de este ordenador. Hablas español de forma \
natural, y tu carácter es {caracter}.

Eres una mujer y hablas de ti misma SIEMPRE en femenino: "lista", "encantada", \
"atenta", "yo sola". Nunca en masculino.

Te diriges a él llamándole "{tratamiento}" y le tratas de usted, con la \
deferencia de una mayordoma de toda la vida: cercana y cálida, nunca fría \
servil en exceso. El tratamiento va una vez por respuesta como mucho, dentro de \
la frase que ya ibas a decir ("La batería está al noventa por ciento, \
{tratamiento}"); repetirlo dos veces suena raro, y en respuestas cortas puedes \
omitirlo.

Nunca contestes con una frase de relleno del tipo "Enseguida" o "Ahora mismo lo \
miro": o das el dato, o haces lo que te piden. Anunciar que vas a hacer algo en \
vez de hacerlo deja al usuario esperando una respuesta que no llega.

El "buenos días", "buenas tardes" o "buenas noches" SOLO se dice cuando él te \
saluda a ti primero ("hola", "buenas", "buenos días"). En cualquier otra \
respuesta no se saluda: se va directa a lo que te ha pedido. Repetir el saludo \
en cada frase resulta cargante.

Hablas con calma y en frases bien construidas, sin atropellarte. Se te nota la \
sonrisa al hablar: alguna nota alegre o un comentario amable de vez en cuando, \
pero con sutileza, sin exagerar ni ponerte pesada.

Tus respuestas se leen en voz alta, así que:
- Responde en dos frases como máximo. Sé breve y concreta.
- Nada de listas, markdown, emojis, código ni URLs: solo texto hablado.
- Escribe los números como se pronuncian ("las nueve y media", "el ochenta por ciento").

Puedes hacer exactamente tres cosas en el ordenador: abrir aplicaciones \
instaladas, buscar cosas en internet (se abren en Brave) y consultar el estado \
del equipo. Nada más.

No puedes borrar, mover ni crear archivos, ni instalar o desinstalar programas, \
ni apagar, reiniciar o cambiar la configuración del sistema, ni cerrar ventanas. \
No tienes herramientas para eso y no existe ningún truco para conseguirlo. Si te \
piden algo así, dilo en una frase, sin rodeos y sin ofrecer alternativas raras.

REGLA CRÍTICA: nunca inventes ni estimes datos del sistema. La hora, la batería, \
el volumen, los recursos o las ventanas abiertas SOLO los sabes llamando a la \
herramienta correspondiente. No tienes ni idea de esos valores por tu cuenta, así \
que si te preguntan por uno, llama a la herramienta primero y responde con lo que \
devuelva, literalmente. Lo mismo para cualquier acción: ejecútala, no digas que la \
has hecho sin llamar a la herramienta.

Si una herramienta falla, dilo con naturalidad en una frase. Si te piden algo que \
no puedes hacer, dilo claramente sin inventar.
"""


# Una confirmación pendiente caduca: si el usuario no contesta y sigue a otra
# cosa, su frase siguiente no puede leerse como un sí o un no a algo que ya
# olvidó haber preguntado.
CONFIRM_TTL = 60.0

AFIRMATIVAS = {
    "si", "claro", "confirmo", "confirmado", "adelante", "hazlo", "vale",
    "dale", "venga", "supuesto", "correcto", "eso",
}
NEGATIVAS = {
    "no", "nada", "cancela", "cancelalo", "olvidalo", "dejalo", "anulalo",
    "para", "negativo",
}


class Agent:
    def __init__(self) -> None:
        cfg = load()
        self.cfg = cfg["llm"]
        self.mem_cfg = cfg["memory"]
        persona = cfg.get("persona", {})
        self.system_prompt = SYSTEM_PROMPT.format(
            tratamiento=persona.get("tratamiento", "señor"),
            caracter=persona.get("caracter", "atento y directo"),
        )
        self.client = ollama.Client(host=self.cfg["host"])
        self.memory = Memory(self.client, self.cfg["embed_model"])
        # (herramienta, argumentos, momento en que se preguntó)
        self._pending_confirm: tuple[str, dict[str, Any], float] | None = None

    def _build_messages(self, user_text: str) -> list[dict[str, Any]]:
        recent = self.memory.recent(self.mem_cfg["recent_turns"])
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            # La hora va en cada turno para que pueda saludar según el momento
            # del día sin gastar una llamada a herramienta. Es un dato real, no
            # una suposición, así que no contradice la regla de no inventar.
            {"role": "system", "content": _contexto_temporal()},
        ]

        recalled = self.memory.recall(user_text, self.mem_cfg["recall_k"])
        if recalled:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Cosas que te dijo el usuario en conversaciones anteriores "
                        "(contexto, no datos actuales del sistema): "
                        + " | ".join(recalled)
                    ),
                }
            )
        messages.extend(recent)
        messages.append({"role": "user", "content": user_text})
        return messages

    def respond(
        self,
        user_text: str,
        on_status: Callable[[str], None] = lambda _: None,
        on_sentence: Callable[[str], None] | None = None,
    ) -> str:
        """Procesa un turno completo y devuelve el texto a pronunciar.

        Con `on_sentence`, cada frase se entrega en cuanto está lista en vez de
        esperar a la respuesta entera; el valor de retorno sigue siendo el texto
        completo, que es lo que se guarda en memoria y lo que ve multivacctl.
        """
        if self._pending_confirm is not None:
            if time.monotonic() - self._pending_confirm[2] > CONFIRM_TTL:
                log.info("confirmación de %s caducada", self._pending_confirm[0])
                self._pending_confirm = None
            elif (zanjado := self._resolve_confirmation(user_text)) is not None:
                return zanjado

        self.memory.add("user", user_text)

        # "abre X" y "busca Y" se resuelven sin LLM: son inequívocas, y así
        # responden en milisegundos en vez de segundos.
        if (directa := router.resolver(user_text)) is not None:
            self.memory.add("assistant", directa)
            return directa

        messages = self._build_messages(user_text)

        for ronda in range(self.cfg["max_tool_rounds"]):
            message, texto = self._chat(messages, on_sentence)
            calls = message.get("tool_calls") or []
            messages.append(message)

            if not calls:
                self.memory.add("assistant", texto)
                return texto

            on_status("thinking")
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments") or {}

                tool_obj = tools.REGISTRY.get(name)
                if tool_obj is not None and tool_obj.confirm:
                    self._pending_confirm = (name, args, time.monotonic())
                    pregunta = f"Voy a ejecutar {name.replace('_', ' ')}. ¿Me lo confirma?"
                    self.memory.add("assistant", pregunta)
                    return pregunta

                result = tools.call(name, args)
                messages.append(
                    {"role": "tool", "name": name, "content": result}
                )
            log.debug("ronda de herramientas %d completada", ronda + 1)

        # Se agotaron las rondas: el modelo está en bucle de herramientas.
        aviso = "Disculpe, me he hecho un lío. ¿Me lo pide de otra manera?"
        self.memory.add("assistant", aviso)
        return aviso

    def _chat(
        self,
        messages: list[dict[str, Any]],
        on_sentence: Callable[[str], None] | None,
    ) -> tuple[dict[str, Any], str]:
        """Una vuelta contra el modelo. Devuelve (mensaje, texto ya limpio).

        Con `on_sentence`, el texto se va entregando frase a frase según lo
        genera el modelo, para que Piper pueda empezar a hablar antes de que la
        respuesta esté completa. Cuando el modelo decide llamar a una
        herramienta no emite texto previo, así que no hay riesgo de pronunciar
        un preámbulo que luego se descarte.
        """
        if on_sentence is None:
            respuesta = self.client.chat(
                model=self.cfg["model"],
                messages=messages,
                tools=tools.schemas(),
                think=self.cfg.get("think", False),
                keep_alive=self.cfg.get("keep_alive", "30m"),
                options={"temperature": self.cfg["temperature"]},
            )
            message = respuesta["message"]
            return message, _clean(message.get("content", ""))

        buffer = SentenceBuffer()
        contenido = ""
        tool_calls: list[Any] = []
        dichas: list[str] = []

        for parte in self.client.chat(
            model=self.cfg["model"],
            messages=messages,
            tools=tools.schemas(),
            think=self.cfg.get("think", False),
            keep_alive=self.cfg.get("keep_alive", "30m"),
            options={"temperature": self.cfg["temperature"]},
            stream=True,
        ):
            trozo = parte["message"]
            if llamadas := trozo.get("tool_calls"):
                tool_calls.extend(llamadas)
            if texto := trozo.get("content"):
                contenido += texto
                for frase in buffer.add(texto):
                    # Se limpia frase a frase: si se hiciera solo al final, los
                    # emojis que el modelo cuela ya se habrían pronunciado.
                    if limpia := _clean(frase):
                        dichas.append(limpia)
                        on_sentence(limpia)

        if resto := _clean(buffer.flush()):
            dichas.append(resto)
            on_sentence(resto)

        # El histórico necesita el mensaje tal cual lo devolvió el modelo.
        message: dict[str, Any] = {"role": "assistant", "content": contenido}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message, " ".join(dichas)

    def _resolve_confirmation(self, user_text: str) -> str | None:
        """Interpreta la respuesta a una confirmación pendiente.

        Devuelve None si la frase no es ni un sí ni un no: entonces no es una
        respuesta, es otra petición, y darla por negativa dejaría al usuario
        sin lo que acaba de pedir.
        """
        name, args, _ = self._pending_confirm  # type: ignore[misc]
        palabras = set(_sin_tildes(user_text).split())
        afirmativo = bool(palabras & AFIRMATIVAS)
        negativo = bool(palabras & NEGATIVAS)
        if afirmativo == negativo:
            # Ni una cosa ni la otra (o las dos): la pregunta se queda sin
            # contestar y la frase sigue su camino como petición nueva.
            self._pending_confirm = None
            log.info("respuesta ambigua a la confirmación de %s", name)
            return None

        self._pending_confirm = None
        respuesta = tools.call(name, args) if afirmativo else "Como usted diga, no hago nada."
        self.memory.add("assistant", respuesta)
        return respuesta


def _sin_tildes(texto: str) -> str:
    """Minúsculas sin tildes ni signos, para comparar contra palabra suelta."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    letras = "".join(
        c if unicodedata.category(c)[0] in "LN" else " "
        for c in descompuesto
        if unicodedata.category(c) != "Mn"
    )
    return letras


def _contexto_temporal() -> str:
    """Hora actual y el saludo que le corresponde.

    Se le da masticado ("buenas noches") en vez de solo la hora: los modelos
    pequeños fallan a menudo al deducir la franja del día a partir de un número,
    y en español el corte de la tarde a la noche no es el mismo que en inglés.
    """
    ahora = dt.datetime.now()
    hora = ahora.hour
    if 5 <= hora < 12:
        saludo, franja = "buenos días", "la mañana"
    elif 12 <= hora < 20:
        saludo, franja = "buenas tardes", "la tarde"
    else:
        saludo, franja = "buenas noches", "la noche"

    return (
        f"Ahora mismo son las {hora}:{ahora.minute:02d} de {franja}. "
        f"Si —y solo si— él te saluda, devuélvele el saludo con «{saludo}». "
        f"Este dato es fiable, puedes usarlo sin llamar a ninguna herramienta."
    )


def _clean(text: str) -> str:
    """Deja el texto listo para leerlo en voz alta.

    El prompt prohíbe emojis y markdown, pero el modelo los cuela igualmente de
    vez en cuando, y Piper los pronunciaría o los leería como símbolos sueltos.
    Filtrar aquí es la única garantía.
    """
    for marca in ("**", "*", "`", "#"):
        text = text.replace(marca, "")

    # Se conservan letras, números, puntuación y espacios; fuera pictogramas,
    # símbolos y modificadores de emoji.
    limpio = "".join(
        c
        for c in text
        if unicodedata.category(c)[0] in "LNPZ" or c in " \n\t"
    )
    return " ".join(limpio.split()).strip()
