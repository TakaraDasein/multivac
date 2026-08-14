"""`multivac-waybar`: el módulo de la barra, con la onda de la voz.

Escribe una línea JSON por fotograma en la salida estándar y no termina nunca:
waybar lee ese flujo tal cual (el módulo va sin `interval`, así no hay sondeo
cada segundo). Se conecta al bus como un cliente más con el rol `bar` y pinta
lo que le llega: `state` para el icono y `level` para la onda.

La onda son bloques Unicode, no un dibujo: un módulo `custom/` de waybar solo
sabe pintar texto. Con nueve columnas y una Nerd Font se lee perfectamente
como un vúmetro.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

from .bus import BusClient
from .config import state_dir

ICONOS = {
    "off": "󰍮",
    "starting": "󰔟",
    "idle": "󰍬",
    "listening": "󰋎",
    "thinking": "󰔟",
    "speaking": "󰔊",
}

TOOLTIPS = {
    "off": "Multivac parado",
    "starting": "Multivac arrancando…",
    "idle": "Multivac en espera",
    "listening": "Multivac escuchando",
    "thinking": "Multivac pensando",
    "speaking": "Multivac hablando",
}

BLOQUES = " ▁▂▃▄▅▆▇█"
COLUMNAS = 9

# Cada cuánto se repinta cuando no llega nada del bus. Marca el ritmo del
# barrido de "pensando" y el desvanecido de la onda al callarse.
LATIDO = 0.07

# El RMS de la voz de Piper, medido, se mueve entre 0,19 (mediana) y 0,37 en los
# picos. Este factor lleva ese rango real a las nueve alturas del bloque sin
# saturarlo: con una ganancia mayor casi todo sale a tope y la onda se aplana.
GANANCIA = 2.6


_ultimo = ""


def _emitir(estado: str, onda: str = "") -> None:
    global _ultimo
    icono = ICONOS.get(estado, ICONOS["idle"])
    linea = json.dumps(
        {
            "text": f"{icono}  {onda}" if onda else icono,
            "alt": estado,
            "class": estado,
            "tooltip": TOOLTIPS.get(estado, "Multivac"),
        },
        ensure_ascii=False,
    )
    # Repetir el fotograma anterior no cambia nada en pantalla y obliga a waybar
    # a reconstruir el módulo; en reposo eso serían miles de repintados al día.
    if linea == _ultimo:
        return
    _ultimo = linea
    print(linea, flush=True)


def _dibujar(niveles: list[float]) -> str:
    """Convierte el historial de volúmenes en columnas, la más nueva a la derecha."""
    return "".join(
        BLOQUES[min(len(BLOQUES) - 1, int(v * (len(BLOQUES) - 1) + 0.5))]
        for v in niveles
    )


def _barrido(paso: int) -> str:
    """Onda de espera mientras piensa: una columna que va y viene."""
    ciclo = 2 * (COLUMNAS - 1)
    pos = paso % ciclo
    if pos >= COLUMNAS:
        pos = ciclo - pos
    return "".join("▄" if i == pos else "▁" for i in range(COLUMNAS))


def _core_activo() -> bool:
    return (
        subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "multivac-core.service"]
        ).returncode
        == 0
    )


def _estado_en_disco() -> str:
    archivo = state_dir() / "state"
    try:
        return archivo.read_text().strip() or "idle"
    except OSError:
        return "idle"


async def _sesion() -> None:
    """Una conexión al bus, desde que se logra hasta que `core` se va."""
    # El pid en el rol distingue una barra de otra: con dos monitores hay dos
    # módulos vivos, y un rol compartido dejaría a uno sin recibir nada.
    bus = BusClient(f"bar-{os.getpid()}")
    # Sin reintentos largos: si core no está, el bucle exterior ya vuelve a
    # intentarlo, y mientras tanto la barra sigue mostrando "parado".
    await bus.connect(retries=1, delay=0)

    estado = _estado_en_disco()
    niveles = [0.0] * COLUMNAS
    paso = 0
    _emitir(estado)

    mensajes = bus.messages().__aiter__()
    siguiente = asyncio.ensure_future(mensajes.__anext__())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(asyncio.shield(siguiente), LATIDO)
            except TimeoutError:
                # Nada nuevo: la onda decae sola y el barrido avanza. Sin esto,
                # los bloques se quedarían congelados en el último valor.
                paso += 1
                if estado == "thinking":
                    _emitir(estado, _barrido(paso))
                elif estado == "speaking" or any(niveles):
                    niveles = niveles[1:] + [0.0]
                    _emitir(estado, _dibujar(niveles) if any(niveles) else "")
                continue
            except StopAsyncIteration:
                return

            siguiente = asyncio.ensure_future(mensajes.__anext__())
            tipo = msg.get("type")
            if tipo == "state":
                nuevo = str(msg.get("state", "idle"))
                if nuevo != estado:
                    estado = nuevo
                    if estado != "speaking":
                        niveles = [0.0] * COLUMNAS
                    _emitir(estado, _barrido(paso) if estado == "thinking" else "")
            elif tipo == "level":
                v = min(1.0, float(msg.get("v", 0.0)) * GANANCIA)
                niveles = niveles[1:] + [v]
                _emitir(estado, _dibujar(niveles))
    finally:
        siguiente.cancel()


async def _bucle() -> None:
    while True:
        if not _core_activo():
            _emitir("off")
            await asyncio.sleep(2)
            continue
        try:
            await _sesion()
        except (ConnectionError, OSError):
            # core arrancando todavía, o recién apagado.
            await asyncio.sleep(1)


def main() -> int:
    try:
        asyncio.run(_bucle())
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        # waybar se cerró.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
