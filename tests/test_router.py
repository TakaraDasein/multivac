"""Router de intención.

`_abrir` y `_buscar` lanzan procesos de verdad (abren el navegador o una app),
así que aquí se sustituyen por espías: lo que se prueba es qué decide el
router, no que Hyprland arranque nada.
"""

import pytest

from multivac.core import router


@pytest.fixture
def espia(monkeypatch):
    """Deja el router sin efectos: nada de ventanas ni de procesos."""
    hecho = {}

    def puntuar(objetivo, limite=1):
        return hecho.get("apps", [])

    def lanzar(desktop_id):
        hecho["lanzado"] = desktop_id

    def abrir_web(objetivo):
        hecho["web"] = objetivo
        return f"Abriendo {objetivo}."

    def buscar_en_web(consulta, donde="google"):
        hecho["busqueda"] = (consulta, donde)
        return f"Buscando {consulta} en {donde}."

    monkeypatch.setattr(router.apps, "puntuar", puntuar)
    monkeypatch.setattr(router.desktop, "lanzar", lanzar)
    monkeypatch.setattr(router.web, "abrir_web", abrir_web)
    monkeypatch.setattr(router.web, "buscar_en_web", buscar_en_web)
    return hecho


class _App:
    def __init__(self, name, desktop_id):
        self.name = name
        self.desktop_id = desktop_id


# --- normalización ---------------------------------------------------------

def test_normalizar_quita_tildes_signos_y_mayusculas():
    assert router._normalizar("  ¿Ábreme la Calculadora?  ") == "abreme la calculadora"


# --- "abre X" --------------------------------------------------------------

def test_abre_una_app_instalada(espia):
    espia["apps"] = [(0.95, _App("Blender", "blender.desktop"))]
    assert router.resolver("abre Blender") == "Abriendo Blender."
    assert espia["lanzado"] == "blender.desktop"


def test_abre_ignora_articulos_y_muletillas(espia):
    espia["apps"] = [(0.9, _App("Calculadora", "calc.desktop"))]
    assert router.resolver("ábreme la calculadora") == "Abriendo Calculadora."
    assert espia["lanzado"] == "calc.desktop"


def test_abre_cae_a_la_web_si_ninguna_app_encaja(espia):
    espia["apps"] = []
    assert router.resolver("abre youtube") == "Abriendo youtube."
    assert espia["web"] == "youtube"


def test_abre_acepta_un_dominio(espia):
    espia["apps"] = []
    router.resolver("abre x.com")
    assert espia["web"] == "x.com"


def test_una_coincidencia_floja_no_abre_nada(espia):
    # Por debajo de UMBRAL_APP no se abre a ciegas: se dice que no la hay.
    espia["apps"] = [(router.UMBRAL_APP - 0.01, _App("Blandes", "blandes.desktop"))]
    respuesta = router.resolver("abre blender")
    assert "No tengo instalada" in respuesta
    assert "lanzado" not in espia


def test_abrir_sin_objeto_pregunta():
    assert router._abrir("  ") == "¿Qué quieres que abra?"


# --- "busca Y" -------------------------------------------------------------

def test_busca_por_defecto_en_google(espia):
    router.resolver("busca recetas de arepas")
    assert espia["busqueda"] == ("recetas de arepas", "google")


def test_busca_con_el_buscador_delante(espia):
    router.resolver("busca en youtube vallenato")
    assert espia["busqueda"] == ("vallenato", "youtube")


def test_busca_con_el_buscador_detras(espia):
    router.resolver("busca vallenato en youtube")
    assert espia["busqueda"] == ("vallenato", "youtube")


def test_la_consulta_conserva_tildes_del_original(espia):
    # El router normaliza para reconocer el verbo, pero lo que se busca sale
    # del texto original: "camión" no puede llegar a Google como "camion".
    router.resolver("Busca el camión más grande")
    assert espia["busqueda"] == ("camión más grande", "google")


def test_extraer_buscador_reconoce_los_alias():
    assert router._extraer_buscador("en la wiki turing") == ("turing", "wikipedia")
    assert router._extraer_buscador("gatos en fotos") == ("gatos", "imagenes")
    assert router._extraer_buscador("algo suelto") == ("algo suelto", "google")


def test_extraer_buscador_prefiere_la_coincidencia_mas_larga():
    # "google maps" tiene que ganarle a "google".
    assert router._extraer_buscador("en google maps bogota") == ("bogota", "maps")


def test_buscar_sin_consulta_pregunta():
    assert router._buscar("   ") == "¿Qué quieres que busque?"


def test_busca_solo_el_buscador_se_queda_como_consulta(espia):
    """Comportamiento actual, no ideal: "busca en youtube" a secas.

    El extractor exige algo detrás del buscador, así que "en youtube" acaba
    buscándose literalmente en Google en vez de preguntar qué buscar. Se fija
    aquí para que un cambio futuro sea deliberado y no accidental.
    """
    router.resolver("busca en youtube")
    assert espia["busqueda"] == ("en youtube", "google")


# --- lo que NO es una orden ------------------------------------------------

@pytest.mark.parametrize(
    "frase",
    [
        "¿qué hora es?",
        "cuéntame un chiste",
        "quiero que busques gatos",  # el router solo entiende el imperativo
        "me buscaba la vida solo",
        "",
        "   ",
    ],
)
def test_una_frase_normal_va_al_llm(frase, espia):
    assert router.resolver(frase) is None
