#!/usr/bin/env bash
cd "$(dirname "$0")"
# ~/tts-bot is the venv from before the rename - still works, no need to re-run setup.
if [ -f "$HOME/tellbird/bin/activate" ]; then
  source "$HOME/tellbird/bin/activate"
else
  source "$HOME/tts-bot/bin/activate"
fi
python -m tellbird
