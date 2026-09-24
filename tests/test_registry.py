"""Registro de herramientas: esquema generado y allowlist.

La segunda mitad de este fichero es la barrera de seguridad del proyecto: lo
único que impide que `hyprctl dispatch exec` se convierta en una shell.
Ninguna de estas pruebas ejecuta un proceso.
"""

from typing import Literal

import pytest

from multivac.core.tools import registry
from multivac.core.tools.registry import NotAllowed


# --- JSON Schema desde los type hints --------------------------------------

@pytest.fixture
def registro_limpio():
    """Deja `REGISTRY` como estaba: registrar es un efecto global."""
    antes = dict(registry.REGISTRY)
    yield
    registry.REGISTRY.clear()
    registry.REGISTRY.update(antes)


def test_tipos_basicos():
    assert registry._json_type(str) == {"type": "string"}
    assert registry._json_type(int) == {"type": "integer"}
    assert registry._json_type(float) == {"type": "number"}
    assert registry._json_type(bool) == {"type": "boolean"}


def test_literal_se_convierte_en_enum():
    assert registry._json_type(Literal["play", "pause"]) == {
        "type": "string",
        "enum": ["play", "pause"],
    }


def test_lista_lleva_el_tipo_de_dentro():
    assert registry._json_type(list[int]) == {
        "type": "array",
        "items": {"type": "integer"},
    }


def test_un_tipo_desconocido_cae_a_string():
    assert registry._json_type(dict) == {"type": "string"}


def test_la_firma_y_el_docstring_generan_el_esquema(registro_limpio):
    @registry.tool()
    def _prueba_subir(
        cuanto: int, donde: Literal["altavoz", "micro"] = "altavoz"
    ) -> str:
        """Sube el volumen.

        Args:
            cuanto: cuántos pasos subir
            donde: qué dispositivo
        """
        return "hecho"

    herramienta = registry.REGISTRY["_prueba_subir"]
    assert herramienta.description == "Sube el volumen."
    assert herramienta.confirm is False

    params = herramienta.schema["function"]["parameters"]
    assert params["properties"]["cuanto"] == {
        "type": "integer",
        "description": "cuántos pasos subir",
    }
    assert params["properties"]["donde"]["enum"] == ["altavoz", "micro"]
    # Solo lo que no tiene valor por defecto es obligatorio.
    assert params["required"] == ["cuanto"]
    assert herramienta.schema["function"]["name"] == "_prueba_subir"


def test_confirm_se_propaga_a_la_herramienta(registro_limpio):
    @registry.tool(confirm=True)
    def _prueba_peligrosa() -> str:
        """Algo irreversible."""
        return "hecho"

    assert registry.REGISTRY["_prueba_peligrosa"].confirm is True
    assert registry.REGISTRY["_prueba_peligrosa"].schema["function"]["parameters"][
        "required"
    ] == []


def test_call_de_una_herramienta_inexistente_no_lanza():
    respuesta = registry.call("no_existe_esta", {})
    assert "no existe la herramienta" in respuesta


def test_call_devuelve_el_error_como_texto(registro_limpio):
    @registry.tool()
    def _prueba_revienta() -> str:
        """Revienta siempre."""
        raise ValueError("boom")

    # El modelo tiene que poder leer el fallo y reaccionar, no morirse el core.
    assert "boom" in registry.call("_prueba_revienta", {})
    assert "argumentos inválidos" in registry.call("_prueba_revienta", {"x": 1})


# --- allowlist: la barrera de verdad ---------------------------------------

@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", str("/")],
        ["sh", "-c", "echo hola"],
        ["bash"],
        ["systemctl", "poweroff"],
        ["curl", "https://ejemplo"],
        ["python"],
    ],
)
def test_run_rechaza_un_binario_fuera_de_la_allowlist(argv):
    with pytest.raises(NotAllowed):
        registry.run(argv)


def test_run_rechaza_un_comando_vacio():
    with pytest.raises(NotAllowed):
        registry.run([])


def test_hyprctl_exec_rechaza_un_binario_no_permitido():
    # Esta es LA prueba: `hyprctl dispatch exec <cualquier cosa>` es una shell.
    with pytest.raises(NotAllowed):
        registry._check_hyprctl(["hyprctl", "dispatch", "exec", "rm -rf ~"])


@pytest.mark.parametrize(
    "comando",
    [
        "sh -c 'rm -rf ~'",
        "bash",
        "/bin/rm algo",
        "gtk-launcher",       # parecido a uno permitido, pero no es el mismo
        "notify-send hola",   # está en allowlist, pero NO en exec_allowlist
    ],
)
def test_hyprctl_exec_solo_admite_la_exec_allowlist(comando):
    with pytest.raises(NotAllowed):
        registry._check_hyprctl(["hyprctl", "dispatch", "exec", comando])


def test_hyprctl_exec_admite_lo_que_si_esta_permitido():
    registry._check_hyprctl(["hyprctl", "dispatch", "exec", "brave https://x.com"])
    registry._check_hyprctl(["hyprctl", "dispatch", "exec", "gtk-launch blender"])


def test_hyprctl_exec_sin_comando_se_rechaza():
    with pytest.raises(NotAllowed):
        registry._check_hyprctl(["hyprctl", "dispatch", "exec"])


def test_hyprctl_rechaza_un_dispatcher_no_permitido():
    for dispatcher in ["killactive", "exit", "movetoworkspace"]:
        with pytest.raises(NotAllowed):
            registry._check_hyprctl(["hyprctl", "dispatch", dispatcher])


def test_hyprctl_deja_pasar_las_consultas_de_solo_lectura():
    registry._check_hyprctl(["hyprctl", "monitors"])
    registry._check_hyprctl(["hyprctl", "-j", "clients"])


def test_hyprctl_rechaza_subcomandos_que_escriben():
    for sub in ["keyword", "reload", "setcursor", "dispatch"]:
        with pytest.raises(NotAllowed):
            registry._check_hyprctl(["hyprctl", sub])


def test_hyprctl_sin_subcomando_se_rechaza():
    with pytest.raises(NotAllowed):
        registry._check_hyprctl(["hyprctl"])


def test_launch_cita_los_argumentos(monkeypatch):
    """Un `;` o un `&` en una URL no puede convertirse en un comando extra."""
    visto = {}
    monkeypatch.setattr(registry, "run", lambda argv: visto.setdefault("argv", argv))

    registry.launch(["brave", "https://a?b&c; rm -rf ~"])
    assert visto["argv"][:3] == ["hyprctl", "dispatch", "exec"]
    comando = visto["argv"][3]
    assert comando.startswith("brave ")
    # Todo lo peligroso viaja dentro de comillas, como un solo argumento.
    assert "'https://a?b&c; rm -rf ~'" in comando


def test_launch_vacio_se_rechaza():
    with pytest.raises(NotAllowed):
        registry.launch([])
