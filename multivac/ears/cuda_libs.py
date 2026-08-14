"""Hace visibles las librerías CUDA instaladas vía pip.

CTranslate2 abre libcublas/libcudnn con dlopen por soname, y las ruedas de
NVIDIA las dejan dentro de site-packages, fuera del camino del enlazador. En vez
de exigir un LD_LIBRARY_PATH en cada unidad de systemd, las precargamos con
RTLD_GLOBAL: así ya están en el proceso cuando CTranslate2 las busca.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# El orden importa: cudnn depende de símbolos de cublas.
_LIBS = [
    ("nvidia/cublas/lib", "libcublasLt.so.12"),
    ("nvidia/cublas/lib", "libcublas.so.12"),
    ("nvidia/cuda_nvrtc/lib", "libnvrtc.so.12"),
    ("nvidia/cudnn/lib", "libcudnn.so.9"),
]

_loaded = False


def preload() -> bool:
    """Devuelve True si se cargó todo; False si falta alguna (→ usar CPU)."""
    global _loaded
    if _loaded:
        return True

    roots = [Path(p) for p in sys.path if p.endswith("site-packages")]
    ok = True
    for subdir, soname in _LIBS:
        for root in roots:
            candidate = root / subdir / soname
            if candidate.exists():
                try:
                    ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
                except OSError as exc:
                    log.warning("no se pudo cargar %s: %s", candidate, exc)
                    ok = False
                break
        else:
            log.warning("no encontrada: %s", soname)
            ok = False

    _loaded = ok
    return ok
