"""Leer la pantalla por OCR.

Un modelo de visión no cabe: la GPU son 8 GB y qwen3:8b, nomic-embed y Whisper
ya ocupan ~6,1 GB. La alternativa barata es capturar la pantalla con `grim` y
pasarla por `tesseract`, que corre en CPU y no toca la VRAM. La herramienta
devuelve el TEXTO para que el modelo lo use: "¿qué dice este error?",
"resúmeme esto", "tradúceme esto".
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

from .registry import NotAllowed, run, tool

# El prompt del sistema pide respuestas de dos frases y el contexto de un modelo
# de 8B es caro: una pantalla llena de texto lo llenaría entero. Se trunca y se
# avisa, que es mejor que devolver 20.000 caracteres inservibles.
MAX_CARACTERES = 1500

# `slurp` es interactivo: bloquea hasta que el usuario arrastra el ratón. Los
# 10 s por defecto de `run()` no valen aquí, y tampoco vale esperar para
# siempre: si el usuario se distrae, la herramienta tiene que rendirse sola.
ESPERA_SELECCION = 60.0


@functools.lru_cache(maxsize=1)
def _idioma() -> str | None:
    """Idioma de OCR disponible, prefiriendo español.

    Los datos de idioma se instalan aparte (tesseract-data-spa). Si no está el
    español se usa el inglés, que reconoce razonablemente un texto castellano
    sin acentos; si no hay ninguno, se devuelve None y la herramienta lo dice
    en vez de fallar con un error críptico.
    """
    salida = run(["tesseract", "--list-langs"])
    idiomas = {linea.strip() for linea in salida.splitlines()[1:]}
    for candidato in ("spa", "eng"):
        if candidato in idiomas:
            return candidato
    return None


def _ocr(imagen: Path) -> str:
    idioma = _idioma()
    if idioma is None:
        raise RuntimeError("tesseract no tiene ningún idioma instalado")
    # "stdout" como destino: tesseract escribe el texto en la salida estándar.
    return run(["tesseract", str(imagen), "stdout", "-l", idioma], timeout=60.0)


def _recortar(texto: str) -> str:
    limpio = "\n".join(l.strip() for l in texto.splitlines() if l.strip())
    if not limpio:
        return "No se leyó ningún texto en la captura."
    if len(limpio) <= MAX_CARACTERES:
        return limpio
    return (
        limpio[:MAX_CARACTERES]
        + f"\n[...texto cortado, había {len(limpio)} caracteres en total]"
    )


@tool()
def leer_pantalla(region: bool = True) -> str:
    """Lee con OCR el texto que hay en la pantalla y lo devuelve.

    Args:
        region: Si es verdadero, el usuario selecciona con el ratón la zona a leer; si es falso, se lee la pantalla entera.
    """
    if _idioma() is None:
        return (
            "No puedo leer la pantalla: tesseract no tiene datos de idioma "
            "instalados."
        )

    # Directorio propio y borrado siempre, incluso si falla el OCR: una captura
    # de pantalla puede contener cualquier cosa y no debe quedarse en /tmp.
    with tempfile.TemporaryDirectory(prefix="multivac-ocr-") as tmp:
        captura = Path(tmp) / "pantalla.png"
        argv = ["grim"]
        if region:
            try:
                geometria = run(["slurp"], timeout=ESPERA_SELECCION)
            except subprocess.TimeoutExpired:
                return "No seleccionaste ninguna zona a tiempo."
            # `slurp` sale con código distinto de cero si se cancela con Escape.
            if geometria.startswith("(fallo") or "," not in geometria:
                return "Cancelaste la selección, no he leído nada."
            argv += ["-g", geometria]
        argv.append(str(captura))

        salida = run(argv, timeout=ESPERA_SELECCION)
        if salida.startswith("(fallo") or not captura.exists():
            return "No pude capturar la pantalla."

        try:
            texto = _ocr(captura)
        except (NotAllowed, RuntimeError, subprocess.TimeoutExpired) as exc:
            return f"No pude leer la captura: {exc}"

    if texto.startswith("(fallo"):
        return "No pude leer la captura."
    return _recortar(texto)
