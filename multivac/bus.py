"""Bus de mensajes: socket Unix con protocolo JSON-lines.

El proceso `core` actúa de hub (servidor); `ears`, `voice` y `multivacctl` se
conectan como clientes. Cada cliente se anuncia con un mensaje `hello` que
declara su rol, y el hub enruta por rol. Cero infraestructura externa: se puede
inspeccionar en vivo con `socat - UNIX-CONNECT:~/.local/state/multivac/bus.sock`.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

Message = dict[str, Any]


def socket_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "multivac" / "bus.sock"


def _encode(msg: Message) -> bytes:
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode()


class BusServer:
    """Hub del bus. Mantiene una conexión por rol y difunde mensajes."""

    def __init__(self, handler: Callable[[Message], Awaitable[None]]):
        self._handler = handler
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Un socket huérfano de un arranque anterior impediría el bind.
        path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._on_client, path=str(path))
        path.chmod(0o600)
        # Al apagarse, el fichero sobrevive al proceso y los clientes que
        # comprueban si existe antes de conectar (el plugin de la barra) se
        # llevan un "connection refused" en vez de ver que no hay nadie. Se
        # borra en cuanto el proceso termina, sea por señal o por excepción.
        atexit.register(lambda: path.unlink(missing_ok=True))
        log.info("bus escuchando en %s", path)

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        role = "?"
        try:
            async for msg in _read_messages(reader):
                if msg.get("type") == "hello":
                    role = str(msg.get("role", "?"))
                    self._clients[role] = writer
                    log.info("cliente conectado: %s", role)
                    continue
                msg.setdefault("from", role)
                await self._handler(msg)
        except (OSError, asyncio.IncompleteReadError):
            # Un cliente que se va (multivacctl tras recibir su respuesta) deja
            # el socket roto; es lo normal, no un error que reportar.
            log.debug("conexión de %s cerrada", role)
        finally:
            if self._clients.get(role) is writer:
                del self._clients[role]
                log.info("cliente desconectado: %s", role)
            writer.close()

    async def send(self, role: str, msg: Message) -> None:
        """Envía a un rol concreto. Si no está conectado, se descarta."""
        writer = self._clients.get(role)
        if writer is None:
            log.debug("rol %s no conectado, descarto %s", role, msg.get("type"))
            return
        try:
            writer.write(_encode(msg))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self._clients.pop(role, None)

    async def broadcast(self, msg: Message, prefix: str | None = None) -> None:
        """Difunde a todos, o solo a los roles que empiecen por `prefix`.

        El prefijo separa a los clientes por familia sin que el hub tenga que
        saber cuántos hay: como guarda una conexión por rol, un nombre fijo
        dejaría mudo al segundo que se conectara. Así el plugin de Quickshell
        abre `bar-shell` para los niveles y `chat-shell` para la conversación,
        y `python -m multivac.bar` puede escuchar a la vez como `bar-<pid>`.
        """
        for role in list(self._clients):
            if prefix is None or role.startswith(prefix):
                await self.send(role, msg)


class BusClient:
    """Cliente del bus para los procesos satélite."""

    def __init__(self, role: str):
        self.role = role
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, retries: int = 30, delay: float = 1.0) -> None:
        """Conecta reintentando: al arrancar por systemd, `core` puede tardar."""
        last: Exception | None = None
        for _ in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    str(socket_path())
                )
                await self.send({"type": "hello", "role": self.role})
                log.info("conectado al bus como %s", self.role)
                return
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                last = exc
                await asyncio.sleep(delay)
        raise ConnectionError(f"no se pudo conectar al bus: {last}")

    async def send(self, msg: Message) -> None:
        assert self._writer is not None, "connect() primero"
        self._writer.write(_encode(msg))
        await self._writer.drain()

    async def messages(self) -> AsyncIterator[Message]:
        assert self._reader is not None, "connect() primero"
        async for msg in _read_messages(self._reader):
            yield msg


async def _read_messages(reader: asyncio.StreamReader) -> AsyncIterator[Message]:
    while True:
        line = await reader.readline()
        if not line:  # EOF
            return
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            log.warning("mensaje ilegible descartado: %r", line[:200])
