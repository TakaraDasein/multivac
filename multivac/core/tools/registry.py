"""Registro de herramientas: `@tool` genera el JSON Schema desde los type hints.

Así la definición que ve el LLM nunca se desincroniza de la firma real de la
función, que es la fuente habitual de errores en los agentes con herramientas.
"""

from __future__ import annotations

import inspect
import logging
import shlex
import subprocess
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin

log = logging.getLogger(__name__)

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., str]
    schema: dict[str, Any]
    confirm: bool = False


REGISTRY: dict[str, Tool] = {}


def _json_type(annotation: Any) -> dict[str, Any]:
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    origin = get_origin(annotation)
    # Literal["play", "pause"] → enum, que guía mucho mejor al modelo que un
    # string libre.
    if origin is typing.Literal:
        values = list(get_args(annotation))
        return {"type": "string", "enum": values}
    if origin is list:
        (inner,) = get_args(annotation) or (str,)
        return {"type": "array", "items": _json_type(inner)}
    return {"type": "string"}


def tool(confirm: bool = False) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Registra una función como herramienta invocable por el modelo.

    `confirm=True` marca acciones irreversibles: el agente pedirá confirmación
    hablada antes de ejecutarlas.
    """

    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        sig = inspect.signature(func)
        hints = typing.get_type_hints(func)
        doc = inspect.getdoc(func) or ""
        # Convención: primera línea = descripción; líneas "param: texto" bajo
        # "Args:" describen cada parámetro.
        summary, params_doc = _parse_doc(doc)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, param in sig.parameters.items():
            spec = _json_type(hints.get(name, str))
            if name in params_doc:
                spec["description"] = params_doc[name]
            properties[name] = spec
            if param.default is inspect.Parameter.empty:
                required.append(name)

        REGISTRY[func.__name__] = Tool(
            name=func.__name__,
            description=summary,
            func=func,
            confirm=confirm,
            schema={
                "type": "function",
                "function": {
                    "name": func.__name__,
                    "description": summary,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
        )
        return func

    return decorator


def _parse_doc(doc: str) -> tuple[str, dict[str, str]]:
    lines = doc.splitlines()
    summary = lines[0].strip() if lines else ""
    params: dict[str, str] = {}
    in_args = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if in_args and ":" in stripped:
            name, _, desc = stripped.partition(":")
            params[name.strip()] = desc.strip()
        elif in_args and not stripped:
            in_args = False
    return summary, params


def schemas() -> list[dict[str, Any]]:
    return [t.schema for t in REGISTRY.values()]


def call(name: str, arguments: dict[str, Any]) -> str:
    """Ejecuta una herramienta. Nunca lanza: el error vuelve al modelo como texto."""
    tool_obj = REGISTRY.get(name)
    if tool_obj is None:
        return f"Error: no existe la herramienta {name!r}."
    try:
        log.info("tool %s(%s)", name, arguments)
        return tool_obj.func(**arguments)
    except TypeError as exc:
        return f"Error: argumentos inválidos para {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 — el modelo debe poder reaccionar
        log.exception("fallo en la herramienta %s", name)
        return f"Error ejecutando {name}: {exc}"


class NotAllowed(RuntimeError):
    pass


def _check_hyprctl(argv: list[str]) -> None:
    """Impide que `hyprctl dispatch exec` se convierta en una shell.

    `hyprctl dispatch exec <lo que sea>` ejecuta cualquier comando, así que
    permitir "hyprctl" a secas equivaldría a no tener allowlist. Se restringen
    los dispatchers admitidos y, para `exec`, el binario que puede lanzar.
    """
    from ...config import load

    cfg = load()["shell"]
    if len(argv) < 2:
        raise NotAllowed("hyprctl necesita un subcomando")

    # Consultas de solo lectura (-j clients, monitors…): inofensivas.
    if argv[1] != "dispatch":
        if argv[1] in {"-j", "clients", "monitors", "activewindow", "workspaces"}:
            return
        raise NotAllowed(f"hyprctl {argv[1]} no está permitido")

    if len(argv) < 3:
        raise NotAllowed("dispatch sin acción")
    dispatcher = argv[2]
    if dispatcher not in set(cfg["hypr_dispatchers"]):
        raise NotAllowed(f"el dispatcher {dispatcher} no está permitido")

    if dispatcher != "exec":
        return

    comando = " ".join(argv[3:]).strip()
    if not comando:
        raise NotAllowed("exec sin comando")
    binario = comando.split()[0]
    if binario not in set(cfg["exec_allowlist"]):
        raise NotAllowed(f"{binario} no se puede lanzar")


def run(argv: list[str], timeout: float = 10.0) -> str:
    """Ejecuta un comando externo sin pasar por una shell.

    La allowlist se comprueba aquí, en un único punto, y `shell=False` impide
    inyección por metacaracteres aunque un argumento venga del modelo.
    """
    from ...config import load

    allowed = set(load()["shell"]["allowlist"])
    if not argv:
        raise NotAllowed("comando vacío")
    if argv[0] not in allowed:
        raise NotAllowed(f"{argv[0]} no está en la allowlist")
    if argv[0] == "hyprctl":
        _check_hyprctl(argv)

    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, shell=False
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"(fallo, código {proc.returncode}) {err or out}"
    return out or "hecho"


def launch(argv: list[str]) -> str:
    """Lanza un programa gráfico a través de Hyprland.

    Único punto por el que Multivac puede arrancar procesos. Hyprland pasa el
    `exec` por `sh -c`, así que cada argumento se cita con shlex: una URL con
    `&`, `?` o comillas llega intacta al navegador y no puede convertirse en un
    comando extra. El binario se valida contra `exec_allowlist` en `run()`.
    """
    if not argv:
        raise NotAllowed("nada que lanzar")
    binario, *resto = argv
    comando = " ".join([binario, *(shlex.quote(a) for a in resto)])
    return run(["hyprctl", "dispatch", "exec", comando])
