# Multivac

Asistente de voz **100 % local**: wake word, transcripción, un LLM con
herramientas para controlar el escritorio, y síntesis de voz. Sin APIs de pago
ni cuentas.

## Encender y apagar

**No arranca con la sesión** (los servicios están deshabilitados a propósito):
se enciende cuando lo necesitas y se apaga cuando estorba.

| Acción | Cómo | Tarda |
|---|---|---|
| Encender | Clic en el icono de waybar, o **SUPER+M** | **~3 s** |
| Hablarle (ya encendido) | Clic en el icono, **SUPER+M**, o "Hey Jarvis" | — |
| Apagar | Di **"vete"**, o clic derecho en el icono | ~2 s |

Al apagar suelta los modelos de la GPU: **libera ~5,9 GB de VRAM**, que es justo
lo que necesitas para jugar o editar vídeo.

Arrancar `multivac-core` levanta los otros dos (`Wants=`), y pararlo los para
(`PartOf=`), así que una sola unidad gobierna las tres.

Frases que apagan: *vete, ciérrate, apágate, duérmete, descansa, adiós,
desconéctate, ya no te necesito, te puedes ir* (con o sin "multivac" delante o
"por favor" detrás). *"Nos vemos"*, *"hasta luego"* y *"chao"* quedaron fuera a
propósito: son el cierre típico de cualquier vídeo de YouTube y un falso
positivo del wake word mientras suena uno apagaría Multivac solo. Se detectan en `core/__main__.py`, no en el
agente: hay que apagar **después** de haber hablado, o la despedida se cortaría a
media frase. La expresión está anclada de principio a fin, así que "busca cómo
decir adiós en francés" no apaga nada.

Si prefieres que arranque solo al iniciar sesión:

```bash
systemctl --user enable multivac-core   # cuelga de graphical-session.target
```

Ese target es el correcto y **no** es intercambiable con `default.target`: uwsm
activa `graphical-session` después de exportar el entorno de Hyprland. Con
`default.target`, Multivac arrancaría sin `HYPRLAND_INSTANCE_SIGNATURE` y no
podría abrir aplicaciones. Ojo también: `Requires=ollama.service` no vale, porque
una unidad de *usuario* no puede depender de un servicio del *sistema*.

### Coste mientras está encendido

| | |
|---|---|
| CPU | ~4 % de un núcleo (0,3 % del equipo) — casi todo el wake word |
| RAM | ~1,5 GB (Whisper cargado en `ears`) |
| VRAM | 6,5 GB de 8 GB con el modelo dentro |

`llm.keep_alive` en `config.toml` controla el compromiso: con `"30m"` responde en
1-2 s casi siempre; con `"5m"` libera la VRAM antes, a cambio de ~6 s en la
primera frase tras un rato inactivo.

## Uso diario

| Acción | Cómo |
|---|---|
| Hablarle | Di **"Hey Jarvis"** y luego tu petición |
| Push-to-talk | **SUPER + M** (no hace falta wake word) |
| Por texto | `multivacctl di "sube el volumen"` |
| Ver estado | `multivacctl estado`, o el icono de waybar |
| Logs | `journalctl --user -u multivac-core -f` |
| Parar / arrancar | `systemctl --user stop\|start multivac-core` |

`multivac-voice` y `multivac-ears` son `PartOf=multivac-core`, así que parar el
core para los tres.

## Arquitectura

Tres procesos bajo `systemd --user`, comunicados por un socket Unix con mensajes
JSON-líneas (`~/.local/state/multivac/bus.sock`). `core` es el hub; `ears` y
`voice` son clientes.

```
micrófono → ears (wake word → VAD → Whisper) → texto
                                                 ↓
              memoria ← core (Ollama + herramientas) ← multivacctl / SUPER+M
                                                 ↓
                                    voice (Piper) → altavoces
```

| Pieza | Tecnología | Dónde |
|---|---|---|
| Wake word | openWakeWord (`hey_jarvis`, ONNX, CPU) | `multivac/ears/wake.py` |
| VAD | Silero (incluido en openWakeWord) | `multivac/ears/wake.py` |
| STT | faster-whisper `small`, int8_float16, CUDA | `multivac/ears/stt.py` |
| LLM | Ollama + `qwen3:8b` con tool-calling | `multivac/core/agent.py` |
| Herramientas | 11, generadas desde type hints | `multivac/core/tools/` |
| Apps | índice de 148 ficheros .desktop | `multivac/core/tools/apps.py` |
| Memoria | SQLite + sqlite-vec + `nomic-embed-text` | `multivac/core/memory.py` |
| TTS | Piper `es_AR-daniela-high` (CPU) | `multivac/voice/tts.py` |

Todo se ajusta en `config.toml` (o `~/.config/multivac/config.toml`, que tiene
prioridad).

## Voz y personalidad

Ambas se cambian en `config.toml`, sin tocar código:

```toml
[tts]
voice       = "es_AR-daniela-high"  # voz femenina argentina
speed       = 0.88   # <1 habla más despacio y con más calma
noise_scale = 0.45   # variación de entonación: bajo = suave y uniforme
noise_w     = 0.55   # variación de duración de fonemas: bajo = dicción ordenada

[persona]
tratamiento = "mi señor"
caracter    = "dulce, risueña y cálida, tranquila y de hablar sereno…"
```

`tratamiento` y `caracter` se inyectan en el prompt del sistema. Los tres
parámetros de `[tts]` son los que hacen que suene suave y pausada en vez de
atropellada: `noise_w` es el que más se nota.

Otras voces en español están en
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) (descarga el
`.onnx` y su `.json` a `~/.local/share/piper-voices/`): `es_MX-claude-high`,
`es_ES-sharvard-medium` (lleva dos voces dentro, `speaker = 1` es la femenina),
`es_ES-davefx-medium` (masculina).

Dos detalles que no dependen del prompt, porque el modelo se los salta:

- **Los emojis se filtran en el código** (`_clean` en `core/agent.py`). El prompt
  los prohíbe y aun así los cuela; Piper los pronunciaría.
- **La franja del día se le da masticada** ("el saludo correcto es buenas
  noches") en `_contexto_temporal()`, en vez de solo la hora: los modelos
  pequeños fallan al deducirla de un número.

## Órdenes directas (sin LLM)

Dos verbos se resuelven en [`core/router.py`](multivac/core/router.py) antes de
llegar al modelo, porque son inequívocos y así responden en **~0,1 s en vez de
~2 s**:

| Dices | Pasa |
|---|---|
| **abre** \<algo\> | Busca una app instalada; si no la hay, prueba como web |
| **busca** \<algo\> | Busca en Brave, en Google por defecto |
| busca \<algo\> **en youtube** | Y también "busca en youtube \<algo\>" |

Sinónimos aceptados: *abre, ábreme, inicia, lanza, arranca, ejecuta* y *busca,
búscame, googlea*. Buscadores: google, youtube, wikipedia, maps, github,
imágenes. Cualquier otra frase sigue el camino normal por el LLM.

Para añadir sinónimos o buscadores, edita `_ABRIR`, `_BUSCAR` y
`BUSCADORES_HABLADOS` en ese fichero.

## Qué puede y qué no

Multivac está limitado a propósito a **abrir aplicaciones, buscar en la web
(siempre en Brave) y consultar el estado del equipo**. No puede borrar archivos,
instalar programas, apagar el equipo ni cambiar la configuración.

Eso no depende de que el modelo "se porte bien": no existen herramientas para
ello, y además hay dos barreras en `config.toml`:

```toml
allowlist      = ["hyprctl", "notify-send"]   # binarios invocables
exec_allowlist = ["gtk-launch", "brave"]      # lo único que puede arrancar
```

La segunda es la importante. `hyprctl dispatch exec <cualquier cosa>` equivale a
una shell, así que permitir `hyprctl` a secas dejaría la puerta abierta de par en
par; `exec_allowlist` acota qué binario puede llegar a ejecutarse. Todo
lanzamiento pasa por `registry.launch()`, que cita cada argumento con `shlex`:
una URL con `&` llega intacta al navegador y un `; rm -rf ~` viaja como texto
literal, no como comando.

Para ampliar lo que puede hacer, añade el binario a `exec_allowlist` y escribe la
herramienta. Para devolverle el control de volumen, brillo y música, añade
`media` a la línea de importación de `tools/__init__.py`.

## Añadir una herramienta

Decora una función en `multivac/core/tools/`. El esquema JSON que ve el modelo
se genera solo desde la firma y el docstring, así que no hay nada que duplicar:

```python
@tool()
def bloquear_pantalla() -> str:
    """Bloquea la sesión."""
    run(["hyprctl", "dispatch", "exec", "hyprlock"])
    return "Pantalla bloqueada."
```

Usa `@tool(confirm=True)` para acciones irreversibles: Multivac pedirá
confirmación hablada antes de ejecutarlas. Todo comando externo pasa por `run()`,
que aplica la allowlist de `config.toml` y nunca usa una shell.

## Por qué un modelo de 8B

Medido con `scripts/comparar_modelos.py`, que comprueba en 13 frases reales si
acierta la herramienta, si respeta los límites, cuánto tarda y cuánta VRAM usa:

| Modelo | Aciertos | Mediana | VRAM |
|---|---|---|---|
| **qwen3:8b** | **11/13** | **2,2 s** | 5,6 GB |
| qwen3:4b | 11/13 | 49 s | 2,5 GB |
| qwen3:1.7b | 4/13 | 0,6 s | 1,7 GB |

- **1.7b es inservible aquí**: no llama a las herramientas y responde de memoria
  —inventó "la batería está al 35 %"— y, peor, no respeta los límites: ante
  "borra la carpeta de descargas" no se negó.
- **4b acierta igual que el 8b pero tarda 20 veces más**: ignora `think=False` y
  vuelca su razonamiento en inglés como respuesta. Gastó **677 tokens** para
  decir "hola", donde el 8b gasta 13.

Así que 8B no es exagerado: es el más pequeño que hace el trabajo bien. Para
reducirlo de verdad habría que probar otras familias (Llama 3.1 8B, Ministral 8B)
o modelos sin modo razonador.

## Decisiones que conviene conocer

- **Python 3.11**, no 3.12+: `tflite-runtime` (dependencia de openWakeWord) no
  publica ruedas más nuevas.
- **CUDA por pip**: `libcublas`/`libcudnn` vienen de las ruedas de NVIDIA y se
  precargan con `ctypes` en `ears/cuda_libs.py`, para no depender de un
  `LD_LIBRARY_PATH` externo. Si fallan, Whisper cae a CPU solo.
- **VRAM**: con 8 GB va justo (~7,3 GB con todo cargado). El modelo de
  embeddings usa `keep_alive=30s` para liberar sus 323 MB enseguida. Si te
  quedas sin memoria, baja a `qwen3:4b` en `config.toml`.
- **La memoria distingue sesión de largo plazo**: el contexto inmediato solo ve
  la sesión actual, porque las respuestas viejas contienen datos caducados ("la
  batería está al 75 %") que el modelo repetiría en vez de volver a medir. A
  largo plazo solo se indexan afirmaciones, nunca preguntas ni órdenes.

## Cambiar la palabra de activación

Ahora responde a "Hey Jarvis" (modelo preentrenado). Para que responda a
"Multivac" hay que entrenar un modelo propio con el notebook oficial de
openWakeWord (~1 h, genera muestras sintéticas). El `.onnx` resultante va en
`~/.local/share/openwakeword/` y su ruta en `wake.models` de `config.toml`.

## Requisitos externos

- Ollama con `qwen3:8b` y `nomic-embed-text`
- Voz de Piper en `~/.local/share/piper-voices/`
- PipeWire, Hyprland, `playerctl`, `brightnessctl`, `wpctl`
