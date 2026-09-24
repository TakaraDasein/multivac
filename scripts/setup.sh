#!/usr/bin/env bash
# Instala Multivac desde cero: entorno, modelos, voces y servicios.
# Idempotente: se puede volver a ejecutar sin romper nada.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOCES="$HOME/.local/share/piper-voices"
BIN="$HOME/.local/bin"
UNIDADES="$HOME/.config/systemd/user"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/multivac"
PLUGINS="$HOME/.config/omarchy/plugins"

info() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
aviso() { printf '\033[1;33m !\033[0m %s\n' "$*"; }

# --- Requisitos del sistema -------------------------------------------------
info "Comprobando requisitos"
faltan=()
for cmd in ollama hyprctl brave gtk-launch notify-send pipewire; do
  command -v "$cmd" >/dev/null || faltan+=("$cmd")
done
if ((${#faltan[@]})); then
  aviso "Faltan: ${faltan[*]}"
  aviso "En Arch: sudo pacman -S ollama hyprland gtk3 libnotify pipewire"
  aviso "Brave: yay -S brave-bin"
fi

command -v uv >/dev/null || {
  info "Instalando uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
}

# --- Entorno Python ---------------------------------------------------------
# 3.11 y no más nuevo: tflite-runtime (dependencia de openWakeWord) solo publica
# ruedas hasta cp311.
info "Creando el entorno (Python 3.11)"
cd "$RAIZ"
uv venv --python 3.11
uv sync

# --- Modelos ----------------------------------------------------------------
info "Descargando el modelo de wake word"
.venv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

info "Descargando los modelos de Ollama (unos 5,5 GB)"
ollama pull qwen3:8b
ollama pull nomic-embed-text

info "Descargando la voz de Piper"
mkdir -p "$VOCES"
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high
for ext in onnx onnx.json; do
  [[ -f "$VOCES/es_AR-daniela-high.$ext" ]] || \
    curl -fL --progress-bar -o "$VOCES/es_AR-daniela-high.$ext" \
      "$BASE/es_AR-daniela-high.$ext"
done

# Whisper se descarga solo la primera vez que arranca `ears`.

# --- Comandos y servicios ---------------------------------------------------
info "Instalando comandos en $BIN"
mkdir -p "$BIN"
ln -sf "$RAIZ/.venv/bin/multivacctl" "$BIN/multivacctl"
ln -sf "$RAIZ/scripts/multivac-toggle" "$BIN/multivac-toggle"
chmod +x "$RAIZ/scripts/multivac-toggle"

info "Instalando los servicios de usuario"
mkdir -p "$UNIDADES" "$CONFIG"
# Las unidades no llevan ninguna ruta escrita: la leen de aquí. Mover el
# repositorio es volver a ejecutar este script (o editar esta línea).
cat > "$CONFIG/entorno" <<ENTORNO
# Generado por scripts/setup.sh. Ruta del clon de Multivac; de aquí la sacan
# las tres unidades de systemd.
MULTIVAC_RAIZ=$RAIZ
ENTORNO
cp "$RAIZ"/systemd/multivac-*.service "$UNIDADES/"
systemctl --user daemon-reload

# --- Plugin de la barra (Quickshell) ---------------------------------------
if [[ -d "$PLUGINS" ]]; then
  info "Barra: plugin de Quickshell"
  if [[ -e "$PLUGINS/efren-cyborg.multivac" ]]; then
    echo "  Ya instalado en $PLUGINS/efren-cyborg.multivac"
  else
    aviso "Falta el plugin efren-cyborg.multivac en $PLUGINS"
    aviso "Copia o enlaza ahí el directorio del plugin y reinicia el shell."
  fi
else
  aviso "No encuentro $PLUGINS: ¿Omarchy 4 con Quickshell?"
fi

cat <<'FIN'

==> Listo.

  Encender:   multivac-toggle on     (o el widget de la barra, o SUPER+M)
  Probar:     multivacctl di "qué hora es"
  Apagar:     di "vete", o multivac-toggle off
  Logs:       journalctl --user -u multivac-core -f
  Ver el bus: .venv/bin/python -m multivac.bar --sin-level

Queda a mano, si lo quieres:
  - Atajo en ~/.config/hypr/bindings.conf:
      bindd = SUPER, M, Multivac escucha, exec, multivac-toggle
  - Widget en la barra: omarchy menu → Bar → añadir "Multivac" (ver README).
  - Arranque automático con la sesión:
      systemctl --user enable multivac-core
FIN
