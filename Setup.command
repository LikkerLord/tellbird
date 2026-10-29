#!/bin/bash
# One-time setup for the Tellbird on macOS.
# First time: right-click -> Open (to get past the Gatekeeper warning).
cd "$(dirname "$0")"
echo "================================================"
echo "   Tellbird - setup (macOS)"
echo "================================================"

# Prefer the newest Python PyTorch supports (3.10-3.14).
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10; do
  command -v "$c" >/dev/null 2>&1 && PY="$c" && break
done
if [ -z "$PY" ]; then
  echo "Python 3.10-3.14 not found."
  echo "Install with Homebrew:  brew install python@3.12"
  echo "or from https://www.python.org/downloads/"
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
echo "Using $PY ($($PY --version))"

[ -d "$HOME/tellbird" ] || "$PY" -m venv "$HOME/tellbird"
source "$HOME/tellbird/bin/activate"
echo "Installing packages (PyTorch is large; this may take a few minutes) ..."
pip install --upgrade pip
pip install -r requirements.txt

[ -f config.json ] || cp config.example.json config.json

if command -v brew >/dev/null 2>&1; then
  brew list blackhole-2ch >/dev/null 2>&1 || brew install blackhole-2ch \
    || echo "Install BlackHole manually: https://existential.audio/blackhole/"
else
  echo "Homebrew not found -> install BlackHole manually: https://existential.audio/blackhole/"
fi

echo "Pre-downloading model + default voice ..."
python -c "from pocket_tts import TTSModel; m=TTSModel.load_model(language='english'); m.get_state_for_audio_prompt('alba'); print('Model ready.')" \
  || echo "(model will download on first start)"

echo ""
echo "Setup complete."
echo "1) Set output_device in config.json to \"BlackHole 2ch\" (+ set channel/voice)."
echo "2) Double-click 'Start Tellbird.command'."
read -n 1 -s -r -p "Press any key to close."
