"""Troceado de texto: `split_sentences` y `SentenceBuffer`.

Es la pieza de la que depende que Multivac empiece a hablar antes de que el
modelo termine de escribir, y su comportamiento sutil (los dos mínimos) no se
ve en ninguna otra prueba.
"""

from multivac.text import SentenceBuffer, split_sentences


def test_split_separa_por_puntuacion_fuerte():
    assert split_sentences("Hola. ¿Qué tal? ¡Bien!") == [
        "Hola.",
        "¿Qué tal?",
        "¡Bien!",
    ]


def test_split_no_parte_decimales():
    # "3.5" no lleva espacio detrás del punto: no es un final de frase.
    assert split_sentences("Mide 3.5 metros. Pesa 2 kilos.") == [
        "Mide 3.5 metros.",
        "Pesa 2 kilos.",
    ]


def test_split_exige_comienzo_de_frase_detras():
    # Minúscula detrás del punto: abreviatura, no frase nueva.
    assert split_sentences("Son las 3 p.m. aproximadamente") == [
        "Son las 3 p.m. aproximadamente"
    ]


def test_split_de_texto_sin_puntuacion_devuelve_el_texto():
    assert split_sentences("sin puntuacion ninguna") == ["sin puntuacion ninguna"]


def test_split_de_texto_vacio_no_devuelve_frases():
    assert split_sentences("   ") == []


def test_buffer_no_suelta_frase_incompleta():
    buf = SentenceBuffer()
    # Sin nada detrás del punto no hay corte: podría venir un decimal.
    assert buf.add("Buenas tardes, señor.") == []


def test_buffer_suelta_pronto_la_primera_aunque_sea_corta():
    buf = SentenceBuffer(minimo=25, minimo_primera=12)
    # 21 caracteres: por debajo del mínimo normal, por encima del de la primera.
    assert buf.add("Buenas tardes, señor. Hoy es lunes.") == ["Buenas tardes, señor."]


def test_buffer_agrupa_una_primera_frase_demasiado_corta():
    buf = SentenceBuffer(minimo=25, minimo_primera=12)
    # "Claro." son 6 caracteres: ni siquiera llega al mínimo de la primera, así
    # que no se corta ahí — se pronuncia junto con la siguiente.
    assert buf.add("Claro. ") == []
    assert buf.add("Ya voy. ") == []
    assert buf.add("Aquí tienes el informe completo. ") == ["Claro. Ya voy."]
    assert buf.flush() == "Aquí tienes el informe completo."


def test_buffer_exige_mas_a_partir_de_la_segunda():
    buf = SentenceBuffer(minimo=25, minimo_primera=12)
    assert buf.add("Buenas tardes, señor. Vale. ") == ["Buenas tardes, señor."]
    # Ya no es la primera: "Vale." (5) y "Ya está." no llegan a 25 juntas, así
    # que esperan a la frase larga y salen agrupadas con ella.
    assert buf.add("Ya está. ") == []
    assert buf.add("El informe quedó terminado anoche. Nada más.") == [
        "Vale. Ya está. El informe quedó terminado anoche."
    ]


def test_buffer_sale_por_fragmentos_partidos_a_media_frase():
    """El modelo entrega tokens, no frases: el corte puede caer en cualquier sitio."""
    buf = SentenceBuffer(minimo=25, minimo_primera=12)
    sueltas = []
    for trozo in ["La bat", "ería está al no", "venta por ciento", ", señor. ", "Y car", "gando."]:
        sueltas += buf.add(trozo)
    assert sueltas == ["La batería está al noventa por ciento, señor."]
    assert buf.flush() == "Y cargando."


def test_flush_vacia_el_buffer_y_vuelve_a_ser_primera():
    buf = SentenceBuffer()
    buf.add("Algo pendiente")
    assert buf.flush() == "Algo pendiente"
    assert buf.flush() == ""
    # Tras el flush empieza otro turno: vuelve a valer el mínimo de la primera.
    assert buf.add("Buenas tardes, señor. Hoy es lunes.") == ["Buenas tardes, señor."]
