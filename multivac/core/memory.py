"""Memoria conversacional: SQLite para el historial + sqlite-vec para recuerdo.

Un único archivo, sin servidores. Los embeddings los calcula Ollama con
nomic-embed-text, que se carga bajo demanda y ocupa poca VRAM.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
import time
from typing import Any

import sqlite_vec

from ..config import state_dir

log = logging.getLogger(__name__)

EMBED_DIM = 768  # nomic-embed-text


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class Memory:
    def __init__(self, client: Any, embed_model: str):
        self._client = client
        self._embed_model = embed_model
        self._embeddings_ok = True
        # El contexto inmediato se limita a esta sesión: las respuestas de
        # sesiones pasadas contienen datos caducados ("la batería está al 75%")
        # y el modelo los repetiría en vez de volver a llamar a la herramienta.
        self.session = f"{time.time():.0f}"

        # El agente se ejecuta en hilos del executor, no en el que construyó
        # esta clase. Desactivamos la comprobación de sqlite3 y serializamos con
        # un lock propio: los accesos son escasos y siempre de un turno a la vez.
        self._lock = threading.Lock()
        self.db = sqlite3.connect(
            state_dir() / "memory.db", check_same_thread=False
        )
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._migrate()

    def _migrate(self) -> None:
        self.db.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS turns (
                id      INTEGER PRIMARY KEY,
                ts      REAL NOT NULL,
                session TEXT NOT NULL DEFAULT '',
                role    TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS turns_session ON turns (session, id);
            CREATE VIRTUAL TABLE IF NOT EXISTS turn_vectors USING vec0(
                turn_id INTEGER PRIMARY KEY,
                embedding FLOAT[{EMBED_DIM}]
            );
            """
        )
        self.db.commit()

    def _embed(self, text: str, kind: str) -> list[float] | None:
        """`kind` es "search_document" al guardar y "search_query" al buscar.

        nomic-embed-text se entrenó con estos prefijos y sin ellos la búsqueda
        asimétrica (pregunta → afirmación) empeora bastante.
        """
        if not self._embeddings_ok:
            return None
        try:
            # keep_alive generoso: con 30s se descargaba entre turnos y volvía a
            # cargarse en cada frase, y al recargarlo con el LLM ya en VRAM caía
            # a medias en CPU y frenaba la respuesta entera. Son 323 MB; sale
            # mucho más barato dejarlo puesto.
            resp = self._client.embed(
                model=self._embed_model, input=f"{kind}: {text}", keep_alive="30m"
            )
            return list(resp["embeddings"][0])
        except Exception as exc:  # modelo no descargado, Ollama caído…
            # Degradamos a memoria solo-reciente en vez de tumbar la conversación.
            log.warning("embeddings desactivados (%s)", exc)
            self._embeddings_ok = False
            return None

    def add(self, role: str, content: str) -> None:
        with self._lock:
            self._add(role, content)

    def _add(self, role: str, content: str) -> None:
        cur = self.db.execute(
            "INSERT INTO turns (ts, session, role, content) VALUES (?, ?, ?, ?)",
            (time.time(), self.session, role, content),
        )
        turn_id = cur.lastrowid
        if (
            role == "user"
            and _is_worth_remembering(content)
            and (vector := self._embed(content, "search_document")) is not None
        ):
            self.db.execute(
                "INSERT INTO turn_vectors (turn_id, embedding) VALUES (?, ?)",
                (turn_id, _pack(vector)),
            )
        self.db.commit()

    def recent(self, limit: int) -> list[dict[str, str]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT role, content FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?",
                (self.session, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def recall(self, query: str, k: int) -> list[str]:
        """Cosas que dijo el usuario en sesiones pasadas y vienen al caso.

        Solo se indexan turnos del usuario: sus preferencias y datos ("me llamo
        X", "mi proyecto es Y") siguen siendo ciertos mañana, mientras que las
        respuestas del asistente suelen contener estado que caduca.
        """
        vector = self._embed(query, "search_query")
        if vector is None:
            return []
        with self._lock:
            rows = self._recall_rows(vector, k)
        return [r[0] for r in rows]

    def _recall_rows(self, vector: list[float], k: int):
        return self.db.execute(
            """
            SELECT t.content
            FROM turn_vectors v
            JOIN turns t ON t.id = v.turn_id
            WHERE v.embedding MATCH ? AND k = ?
              AND t.session != ?
            ORDER BY distance
            """,
            (_pack(vector), k, self.session),
        ).fetchall()

    def close(self) -> None:
        self.db.close()


# Órdenes y preguntas no aportan nada al recordarlas más tarde, y además
# compiten en similitud con las preguntas futuras, desplazando a los hechos
# útiles. Solo indexamos afirmaciones.
_INTERROGATIVAS = (
    "qué", "que", "cuál", "cual", "cuánto", "cuanto", "cuánta", "cuanta",
    "cómo", "como", "dónde", "donde", "cuándo", "cuando", "quién", "quien",
    "por qué", "porqué",
)
_IMPERATIVAS = (
    "abre", "cierra", "sube", "baja", "pon", "quita", "pausa", "reproduce",
    "silencia", "apaga", "enciende", "cambia", "ajusta", "dime", "ejecuta",
    "lanza", "muestra", "lista", "busca",
)


def _is_worth_remembering(text: str) -> bool:
    limpio = text.strip().lower().lstrip("¿¡")
    if not limpio or len(limpio.split()) < 3:
        return False
    if limpio.endswith("?") or text.strip().startswith("¿"):
        return False
    primera = limpio.split()[0]
    if primera in _INTERROGATIVAS or primera in _IMPERATIVAS:
        return False
    return True
