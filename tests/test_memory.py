"""`_is_worth_remembering`: qué frases del usuario acaban en el índice vectorial.

Es puramente funcional y decide sola la calidad del recuerdo a largo plazo: si
deja pasar preguntas y órdenes, compiten en similitud con las preguntas futuras
y desplazan a los hechos útiles.
"""

import pytest

from multivac.core.memory import _is_worth_remembering


@pytest.mark.parametrize(
    "frase",
    [
        "me llamo Efrén y vivo en Madrid",
        "mi proyecto principal es Multivac",
        "trabajo mejor por las noches",
        "prefiero que me hables de usted",
    ],
)
def test_las_afirmaciones_se_recuerdan(frase):
    assert _is_worth_remembering(frase) is True


@pytest.mark.parametrize(
    "frase",
    [
        "¿qué hora es?",
        "cuánta batería queda",       # interrogativa sin signos (Whisper)
        "como se llama esa cancion",
        "dónde está el informe",
    ],
)
def test_las_preguntas_no_se_recuerdan(frase):
    assert _is_worth_remembering(frase) is False


@pytest.mark.parametrize(
    "frase",
    [
        "abre el navegador ahora",
        "busca recetas de arepas",
        "pon música tranquila",
        "dime la hora exacta",
    ],
)
def test_las_ordenes_no_se_recuerdan(frase):
    assert _is_worth_remembering(frase) is False


@pytest.mark.parametrize("frase", ["", "   ", "sí", "vale ya", "gracias"])
def test_lo_demasiado_corto_no_se_recuerda(frase):
    # Menos de tres palabras no es un hecho: es una muletilla.
    assert _is_worth_remembering(frase) is False


def test_las_interrogativas_de_dos_palabras_se_escapan():
    """Comportamiento actual: solo se mira la PRIMERA palabra.

    "por qué" está en `_INTERROGATIVAS`, pero la comprobación compara palabra a
    palabra, así que "por qué tarda tanto" sí se indexa. Queda fijado aquí para
    que arreglarlo sea un cambio consciente.
    """
    assert _is_worth_remembering("por qué tarda tanto") is True


def test_el_signo_de_apertura_basta_para_descartar():
    # Whisper a veces abre la pregunta y no la cierra.
    assert _is_worth_remembering("¿me puedes ayudar con esto") is False


def test_no_le_afecta_el_espaciado_ni_las_mayusculas():
    assert _is_worth_remembering("  Me Llamo Efrén Y Vivo Aquí  ") is True
