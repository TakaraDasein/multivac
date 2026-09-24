# Multivac

Un asistente de voz que corre **entero en tu ordenador**. Le hablas, te entiende,
abre tus aplicaciones, busca en internet y te contesta en voz alta. Sin cuentas,
sin suscripciones y sin que salga de tu máquina una sola palabra de lo que dices.

```
Tú  ─ "Hey Jarvis, ¿cuánta batería me queda?"
Ella ─ "Está al noventa y ocho por ciento, señor, y conectado a la corriente."

Tú  ─ "Abre Blender"
Ella ─ "Abriendo Blender."          ← 0,08 s, ni toca la GPU

Tú  ─ "Busca recetas de arepas en YouTube"
Ella ─ "Buscando recetas de arepas en YouTube."

Tú  ─ "Vete"
Ella ─ "Hasta luego, señor. Aquí estaré cuando me necesite."   ← libera 5,9 GB de VRAM
```

Pensado para **Arch Linux + Hyprland** (probado en [Omarchy](https://omarchy.org)),
con una GPU NVIDIA de 8 GB.

---

## Cómo funciona

Tres procesos independientes bajo `systemd --user`, comunicados por un socket
Unix con mensajes JSON-líneas (`~/.local/state/multivac/bus.sock`). `core` hace de
hub; `ears` y `voice` son clientes suyos. Cada pieza se puede sustituir sin tocar
las demás.

```
  micrófono
      │
      ▼
┌───────────────┐  texto  ┌────────────────┐  respuesta  ┌───────────────┐
│     ears      │────────▶│      core      │────────────▶│     voice     │
│ wake word     │         │ router rápido  │             │ Piper TTS     │
│ VAD + Whisper │◀────────│ agente + tools │             │ por frases    │
└───────────────┘  mute   └────────────────┘             └───────────────┘
                               │      ▲                          │
                               ▼      │                          ▼
                         Ollama    memoria                  altavoces
                         qwen3:8b  SQLite+vec
```

| Pieza | Tecnología | Dónde |
|---|---|---|
| Palabra de activación | openWakeWord (`hey_jarvis`, ONNX, CPU) | `multivac/ears/wake.py` |
| Detección de voz | Silero VAD (incluido en openWakeWord) | `multivac/ears/wake.py` |
| Transcripción | faster-whisper `small`, int8_float16, CUDA | `multivac/ears/stt.py` |
| Cerebro | Ollama + `qwen3:8b` con tool-calling | `multivac/core/agent.py` |
| Órdenes directas | router por expresiones regulares | `multivac/core/router.py` |
| Herramientas | 11, con esquema generado desde los type hints | `multivac/core/tools/` |
| Índice de apps | 148 ficheros `.desktop` del sistema | `multivac/core/tools/apps.py` |
| Memoria | SQLite + sqlite-vec + `nomic-embed-text` | `multivac/core/memory.py` |
| Voz | Piper `es_AR-daniela-high` (CPU) | `multivac/voice/tts.py` |
| Troceado en frases | `SentenceBuffer`, compartido | `multivac/text.py` |
| Barra de estado | plugin de Quickshell (fuera del repo) | `efren-cyborg.multivac` |
| Depurar el bus | volcado de las líneas JSON en crudo | `multivac/bar.py` |

### Rendimiento real

Medido en un i7-12650H con RTX 4060 Laptop (8 GB):

| Acción | Tarda |
|---|---|
| "abre X" / "busca Y" (router, sin LLM) | **0,08–0,13 s** |
| Empezar a hablar (primera frase) | **1,3–2,3 s** |
| Respuesta completa | 2,9–4,4 s |
| Primera frase tras un rato inactivo | ~6 s (recarga el modelo) |
| Encender desde apagado | ~3 s |

Multivac **habla mientras el modelo sigue escribiendo**: en cuanto cierra una
frase, esa frase ya se sintetiza y suena. El silencio de espera baja de los 2,9-4,4 s
que tarda la respuesta entera a 1,3-2,3 s — hasta **3,1 s menos** en respuestas
largas.

En reposo: **0,3 % de CPU** del equipo, 1,5 GB de RAM. Con el modelo cargado,
6,5 GB de los 8 GB de VRAM.

---

## Instalación

```bash
git clone git@github.com:TakaraDasein/multivac.git
cd multivac
./scripts/setup.sh
```

El script crea el entorno, descarga los modelos (~5,5 GB) y deja instalados los
comandos y los servicios. Necesitas antes: `ollama`, `hyprland`, `brave`,
`pipewire`, `libnotify`, y los drivers de NVIDIA.

El script también deja la ruta del clon en `~/.config/multivac/entorno`
(`MULTIVAC_RAIZ`): las unidades de systemd la leen de ahí, así que el repositorio
puede estar donde quieras. Si lo mueves, vuelve a ejecutar `setup.sh`.

Luego, para el widget de la barra y el atajo de teclado:

<details>
<summary>Widget de la barra (Omarchy 4 + Quickshell)</summary>

Ya no hay módulo de waybar: la barra de Omarchy 4 es **Quickshell**, y Multivac se
integra como un plugin del shell, `efren-cyborg.multivac`. El plugin abre **una
sola conexión** al bus para todo el shell y de ahí cuelgan sus tres piezas:

| Pieza | Fichero | Qué es |
|---|---|---|
| Servicio | `MultivacBus.qml` | singleton: la conexión al bus, con reconexión |
| Widget | `BarWidget.qml` | icono del estado + onda de la voz; clic para hablar |
| Overlay | `Chat.qml` | la conversación en pantalla |

Instalarlo es dejarlo (o enlazarlo) en el directorio de plugins del shell y
añadir el widget a la barra:

```bash
ln -s ~/ruta/al/plugin/efren-cyborg.multivac \
      ~/.config/omarchy/shell/plugins/efren-cyborg.multivac
omarchy restart shell
```

Después, `omarchy menu` → **Bar** → añadir **Multivac** (aparece en la categoría
*System*, y por defecto se coloca a la derecha). Ajustes del widget, desde el
propio menú:

| Ajuste | Por defecto | Qué hace |
|---|---|---|
| `columns` | 9 | columnas de la onda; más columnas, más historia en pantalla |
| `gain` | 2.6 | multiplica el `level` del bus antes de recortar a 1.0 |
| `wave_color` | `accent` | `accent`, `foreground`, o un color literal `#26FFDF` |
| `bar_width` | 3 | ancho de una columna, en píxeles |
| `wave_height` | 14 | alto de una columna a plena escala, en píxeles |

La ganancia por defecto no es arbitraria: el RMS real de la voz de Piper se mueve
entre 0,19 y 0,37, y 2,6 lleva ese rango a la altura completa sin que la onda se
aplaste contra el techo.

Sin barra tampoco te quedas a ciegas: `python -m multivac.bar --sin-level` vuelca
por pantalla lo que el bus está publicando.
</details>

<details>
<summary>Atajo de teclado (<code>~/.config/hypr/bindings.conf</code>)</summary>

```
bindd = SUPER, M, Multivac escucha, exec, multivac-toggle
```
Comprueba antes que la tecla esté libre: `omarchy menu keybindings --print`.
</details>

---

## Uso

| Acción | Cómo |
|---|---|
| Encender | Clic en el widget de la barra, o **SUPER+M** (~3 s) |
| Hablarle | **"Hey Jarvis"**, o **SUPER+M**, o clic en el widget |
| Por texto | `multivacctl di "qué hora es"` |
| Apagar | Di **"vete"**, o clic derecho en el widget |
| Ver estado | `multivacctl estado` |
| Logs | `journalctl --user -u multivac-core -f` |
| Ver el bus | `python -m multivac.bar --sin-level` |

**No arranca con la sesión** a propósito: se enciende cuando lo necesitas. Al
apagarlo suelta los modelos y libera ~5,9 GB de VRAM, que es justo lo que hace
falta para jugar o editar vídeo. Si prefieres que arranque solo:
`systemctl --user enable multivac-core`.

Frases que lo apagan: *vete, ciérrate, apágate, duérmete, descansa, adiós,
desconéctate, ya no te necesito, te puedes ir*.

### Órdenes directas (sin pasar por el modelo)

Dos verbos se resuelven por expresiones regulares antes de llegar al LLM, así que
responden en milisegundos:

| Dices | Pasa |
|---|---|
| **abre** \<algo\> | Busca entre tus apps instaladas; si no la hay, prueba como web |
| **busca** \<algo\> | Busca en Brave (Google por defecto) |
| **busca** \<algo\> **en youtube** | También vale "busca en youtube \<algo\>" |

Sinónimos: *abre, ábreme, inicia, lanza, arranca, ejecuta* / *busca, búscame,
googlea*. Buscadores: google, youtube, wikipedia, maps, github, imágenes.

---

## Personalización

Todo en `config.toml`. Tu copia personal va en `~/.config/multivac/config.toml`,
que tiene prioridad sobre la del repositorio.

```toml
[tts]
voice       = "es_AR-daniela-high"  # voz femenina argentina
speed       = 0.88   # <1 habla más despacio y con más calma
noise_scale = 0.45   # variación de entonación: bajo = suave y uniforme
noise_w     = 0.55   # variación de duración de fonemas: bajo = dicción ordenada

[persona]
tratamiento = "señor"
caracter    = "atenta, cercana y directa"

[llm]
model      = "qwen3:8b"
keep_alive = "30m"   # cuánto sigue en VRAM sin usarse
```

Otras voces en [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
(el `.onnx` y su `.json` van a `~/.local/share/piper-voices/`):
`es_MX-claude-high` (mexicana), `es_ES-sharvard-medium` (castellana, lleva dos
voces: `speaker = 1` es la femenina), `es_ES-davefx-medium` (masculina).

### Añadir una herramienta

Decora una función en `multivac/core/tools/`. El esquema JSON que ve el modelo se
genera solo desde la firma y el docstring, así que no hay nada que duplicar:

```python
@tool()
def bloquear_pantalla() -> str:
    """Bloquea la sesión."""
    launch(["hyprlock"])
    return "Pantalla bloqueada."
```

Con `@tool(confirm=True)` pedirá confirmación hablada antes de ejecutarla. Y hay
que añadir el binario a `exec_allowlist` (ver abajo), o no se lanzará.

---

## Qué puede hacer, y qué no

Limitado a propósito a **abrir aplicaciones, buscar en la web y consultar el
estado del equipo**. No borra archivos, no instala programas, no apaga el equipo
ni toca la configuración.

Eso no depende de que el modelo se porte bien. No existen herramientas para ello,
y hay dos barreras:

```toml
[shell]
allowlist      = ["hyprctl", "notify-send"]  # binarios invocables
exec_allowlist = ["gtk-launch", "brave"]     # lo único que puede llegar a arrancar
```

La segunda es la que importa: `hyprctl dispatch exec <cualquier cosa>` equivale a
una shell, así que permitir `hyprctl` a secas dejaría la puerta abierta de par en
par. Todo lanzamiento pasa por `registry.launch()`, que cita cada argumento con
`shlex`: una URL con `&` llega intacta al navegador, y un `; rm -rf ~` viaja como
texto literal, no como comando.

Probado contra `rm` directo, `rm` vía hyprctl, `sh -c`, `systemctl poweroff`,
`killactive` e inyección en URL y en nombre de aplicación.

Para devolverle el control de volumen, brillo y música, añade `media` a la línea
de importación de `tools/__init__.py`.

---

## El bus (interfaz pública)

El bus dejó de ser un detalle interno el día que la barra se fue a otro
repositorio: hoy hay clientes QML que dependen de este protocolo, así que
**cambiarlo rompe cosas fuera de aquí**.

- **Socket:** `$XDG_STATE_HOME/multivac/bus.sock` (por defecto
  `~/.local/state/multivac/bus.sock`), modo `0600`.
- **Protocolo:** JSON-líneas — un objeto JSON por línea, terminada en `\n`, UTF-8.
- **Servidor:** `multivac-core`. Todo lo demás es cliente.

### Handshake

El primer mensaje del cliente, siempre:

```json
{"type": "hello", "role": "bar-1234"}
```

El hub guarda **una conexión por rol**, así que dos clientes con el mismo nombre
se dejan mudos el uno al otro. Los roles con sufijo (`bar-<pid>`, `chat-<pid>`)
permiten varias instancias: el hub difunde por prefijo. Roles reservados: `core`,
`ears`, `voice`, `ctl`, `bar-*`, `chat-*`.

### De `core` a los clientes

| `type` | Campos | A quién | Qué significa |
|---|---|---|---|
| `state` | `state` | a todos | `off` \| `starting` \| `idle` \| `listening` \| `thinking` \| `speaking` |
| `level` | `v` (0.0–1.0) | prefijo `bar-` | volumen instantáneo de la voz, ~21 Hz |
| `answer` | `text` | al que preguntó | la respuesta completa |
| `chat_user` | `text` | prefijo `chat-` | lo que dijo el usuario (transcrito o escrito) |
| `chat_chunk` | `text` | prefijo `chat-` | una frase de la respuesta, según se genera |
| `chat_end` | — | prefijo `chat-` | fin del turno |

### De los clientes a `core`

| `type` | Campos | Qué hace |
|---|---|---|
| `utterance` | `text` | una frase, como si se hubiera dicho en voz alta |
| `listen` | — | push-to-talk: fuerza una escucha |
| `stop` | — | interrumpe lo que esté diciendo ahora mismo |

Un cliente **nunca** debe morir porque `core` no esté: si el socket no existe, lo
correcto es quedarse en `connected = false` y `state = "off"`, y reintentar con
backoff. Así lo hacen `MultivacBus.qml` en el shell y `multivac/bar.py` aquí.

### Verlo en vivo

```bash
python -m multivac.bar               # todo, incluida la onda
python -m multivac.bar --sin-level   # sin los ~21 mensajes/s de `level`
socat - UNIX-CONNECT:$HOME/.local/state/multivac/bus.sock   # a pelo
```

---

## Desarrollo

```bash
uv sync --extra dev
.venv/bin/python -m pytest
```

Las pruebas cubren lo que se puede probar sin GPU, micrófono ni Ollama: el
troceado en frases (`text.py`), el router de intención, el filtro de la memoria y
—lo que más importa— la **allowlist**: que `run()` rechace un binario que no esté
en la lista y que `hyprctl dispatch exec` no pueda lanzar nada fuera de
`exec_allowlist`. El router se prueba con los lanzadores sustituidos: ninguna
prueba abre una ventana.

---

## Por qué estas decisiones

**Por qué un modelo de 8B.** Medido con `scripts/comparar_modelos.py`, que
comprueba en 13 frases reales si acierta la herramienta, si respeta los límites y
cuánto tarda:

| Modelo | Aciertos | Mediana | VRAM |
|---|---|---|---|
| **qwen3:8b** | **12/13** | **2,4 s** | 5,6 GB |
| qwen3:4b | 11/13 | 49 s | 2,5 GB |
| qwen3:1.7b | 4/13 | 0,6 s | 1,7 GB |

El 1.7b no llama a las herramientas: responde de memoria e inventa ("la batería
está al 35 %"), y no respeta los límites. El 4b acierta igual que el 8b pero
ignora `think=False` y vuelca su razonamiento en inglés como respuesta: gastó
**677 tokens en decir "hola"**, donde el 8b gasta 13. Así que 8B no es exagerado,
es el más pequeño que hace bien el trabajo.

**Python 3.11**, no más nuevo: `tflite-runtime` (dependencia de openWakeWord) no
publica ruedas para 3.12+.

**Las librerías CUDA vienen de las ruedas de pip** y se precargan con `ctypes` en
`ears/cuda_libs.py`, para no depender de un `LD_LIBRARY_PATH` externo en cada
unidad de systemd. Si fallan, Whisper cae a CPU él solo.

**La memoria distingue sesión de largo plazo.** El contexto inmediato solo ve la
sesión actual: las respuestas de sesiones viejas traen datos caducados ("la
batería está al 75 %") que el modelo repetiría en vez de volver a medir. A largo
plazo solo se indexan afirmaciones ("me llamo X"), nunca preguntas ni órdenes —
si no, compiten en similitud con las preguntas futuras y desplazan a los hechos.

**Se habla por frases, no por respuesta completa.** El agente entrega cada frase
en cuanto el modelo la cierra (`on_sentence`), y `voice` las reproduce en **un
único flujo de audio**: abrir un stream por frase metería un clic y un hueco
entre ellas. Un `id` de enunciado evita que el `speaking_done` de una respuesta
vieja reactive el micrófono en mitad de la siguiente.

**Nada de frases de relleno.** El prompt prohíbe expresamente responder
"Enseguida" o "Ahora mismo lo miro": el modelo lo hacía en lugar de llamar a la
herramienta, y el banco de pruebas pasó de 10/13 a 12/13 al quitarlo.

**Los emojis se filtran en el código**, no en el prompt. El prompt los prohíbe y
el modelo los cuela igualmente; Piper los pronunciaría.

**La franja del día se le da masticada** ("el saludo correcto es buenas noches")
en vez de solo la hora: los modelos pequeños fallan al deducirla de un número.

**`graphical-session.target`, no `default.target`,** para el arranque automático:
uwsm activa el primero *después* de exportar el entorno de Hyprland. Con el
segundo, Multivac arrancaría sin `HYPRLAND_INSTANCE_SIGNATURE` y no podría abrir
aplicaciones. Y `Requires=ollama.service` no vale: una unidad de *usuario* no
puede depender de un servicio del *sistema*.

---

## Limitaciones conocidas

- **La palabra de activación es "Hey Jarvis"**, no "Multivac": es un modelo
  preentrenado de openWakeWord. Para una palabra propia hay que entrenar un
  modelo (~1 h con el notebook oficial) y apuntarlo en `wake.models`.
- **Falsos positivos con audio de vídeos.** Si suena un YouTube por los
  altavoces, el wake word puede dispararse y transcribir lo que diga el vídeo.
  La solución de raíz es el cancelador de eco de PipeWire; mientras tanto, usar
  auriculares. Por eso "nos vemos" y "hasta luego" **no** están entre las frases
  que apagan: son el cierre típico de cualquier vídeo.
- **VRAM justa en 8 GB.** Con el modelo dentro quedan ~1,5 GB libres. Si abres un
  juego, `ollama stop qwen3:8b` o apágalo con "vete".
- **El router solo entiende el imperativo.** "Busca gatos" va por la vía rápida,
  pero "quiero que busques gatos" pasa por el LLM (2 s en vez de 0,1 s).
- **El modelo a veces llama a una herramienta cuando no toca**, o se salta la que
  debería. Es el 2/13 que falla en el banco de pruebas.

## Estructura

```
multivac/
├── bus.py              socket Unix, protocolo JSON-líneas
├── bar.py              cliente de depuración: vuelca el bus por stdout
├── config.py           carga de config.toml
├── ctl.py              CLI multivacctl
├── ears/               micrófono → texto
│   ├── wake.py         openWakeWord + Silero VAD
│   ├── stt.py          faster-whisper
│   └── cuda_libs.py    precarga de libcublas/libcudnn
├── core/               el cerebro
│   ├── agent.py        bucle de tool-calling contra Ollama
│   ├── router.py       "abre X" / "busca Y" sin LLM
│   ├── memory.py       SQLite + sqlite-vec
│   └── tools/          herramientas expuestas al modelo
└── voice/tts.py        Piper, sintetizando frase a frase

tests/                  pruebas sin GPU ni micrófono (pytest)
scripts/setup.sh        instalación completa, idempotente
systemd/                las tres unidades, sin rutas escritas dentro
```

La barra vive **fuera** de este repositorio, en el plugin
`efren-cyborg.multivac` del shell de Omarchy, y habla con `core` solo por el bus.
