"""`multivacctl`: hablarle por texto, ver el estado o disparar la escucha."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .bus import BusClient
from .config import state_dir


async def _send(text: str, esperar: bool) -> int:
    bus = BusClient("ctl")
    try:
        await bus.connect(retries=2, delay=0.5)
    except ConnectionError:
        print("multivac-core no está corriendo.", file=sys.stderr)
        return 1

    await bus.send({"type": "utterance", "text": text})
    if not esperar:
        return 0

    try:
        async with asyncio.timeout(120):
            async for msg in bus.messages():
                if msg.get("type") == "answer":
                    print(msg.get("text", ""))
                    return 0
    except TimeoutError:
        print("sin respuesta (timeout).", file=sys.stderr)
        return 1
    return 1


async def _enviar_suelto(msg: dict) -> int:
    """Manda una orden que no espera respuesta (`listen`, `stop`)."""
    bus = BusClient("ctl")
    try:
        await bus.connect(retries=2, delay=0.5)
    except ConnectionError:
        print("multivac-core no está corriendo.", file=sys.stderr)
        return 1
    await bus.send(msg)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="multivacctl", description="Control de Multivac")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_say = sub.add_parser("di", help="envía una frase como si la hubieras dicho")
    p_say.add_argument("texto", nargs="+")
    p_say.add_argument(
        "-q", "--sin-esperar", action="store_true", help="no esperar la respuesta"
    )

    sub.add_parser("estado", help="muestra el estado actual")
    sub.add_parser("escucha", help="fuerza una escucha (push-to-talk)")
    sub.add_parser("calla", help="interrumpe lo que esté diciendo ahora mismo")

    args = parser.parse_args()

    if args.cmd == "estado":
        archivo = state_dir() / "state"
        print(archivo.read_text().strip() if archivo.exists() else "parado")
        return 0
    if args.cmd == "escucha":
        return asyncio.run(_enviar_suelto({"type": "listen"}))
    if args.cmd == "calla":
        return asyncio.run(_enviar_suelto({"type": "stop"}))
    return asyncio.run(_send(" ".join(args.texto), not args.sin_esperar))


if __name__ == "__main__":
    raise SystemExit(main())
