"""`python -m multivac.bar`: cliente de depuración del bus.

Vuelca por la salida estándar, en crudo, cada línea JSON que el hub envía a un
cliente con rol `bar-`. Sirve para ver qué está publicando `core` —los cambios
de `state` y el chorro de `level` de la voz— sin abrir Quickshell ni escribir
una línea de QML.

La barra de verdad ya no vive aquí: la pinta el plugin `efren-cyborg.multivac`
del shell de Omarchy. Esto es lo que queda, y es lo único que hacía falta que
quedara: un `tail -f` del bus.

    python -m multivac.bar            # todo
    python -m multivac.bar --sin-level  # solo lo interesante, sin la onda
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .bus import BusClient


async def _sesion(sin_level: bool) -> None:
    """Una conexión, desde que se logra hasta que `core` cierra el socket."""
    # El pid en el rol permite tener varios clientes de depuración a la vez sin
    # que uno deje mudo al otro: el hub guarda una conexión por rol.
    bus = BusClient(f"bar-{os.getpid()}")
    # Un solo intento: el bucle exterior ya se encarga de reintentar con espera.
    await bus.connect(retries=1, delay=0)
    print("# conectado al bus", file=sys.stderr, flush=True)

    async for msg in bus.messages():
        if sin_level and msg.get("type") == "level":
            continue
        print(json.dumps(msg, ensure_ascii=False), flush=True)


async def _bucle(sin_level: bool) -> None:
    espera = 0.5
    while True:
        try:
            await _sesion(sin_level)
            espera = 0.5  # hubo conexión: la próxima caída no espera de más
        except (ConnectionError, OSError):
            pass  # core parado, arrancando o recién caído
        print(f"# sin bus, reintento en {espera:.0f}s", file=sys.stderr, flush=True)
        await asyncio.sleep(espera)
        # Backoff hasta 8 s: con core apagado esto puede quedarse horas abierto.
        espera = min(espera * 2, 8.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sin-level",
        action="store_true",
        help="omite los mensajes 'level' (~21 por segundo mientras habla)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_bucle(args.sin_level))
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        pass  # el `head` o el `grep` del otro lado se cerró
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
