#!/bin/bash
# Double-click launcher for Tellbird on macOS.
# Put this file next to tellbird.py. On first use you may need to make it
# executable once:  chmod +x "Start Tellbird.command"
# macOS opens .command files in Terminal on double-click.

cd "$(dirname "$0")"

# Activate the virtual environment if it exists (adjust the path if needed).
# ~/tts-bot is the venv from before the rename - still works, no need to re-run setup.
if [ -f "$HOME/tellbird/bin/activate" ]; then
    source "$HOME/tellbird/bin/activate"
elif [ -f "$HOME/tts-bot/bin/activate" ]; then
    source "$HOME/tts-bot/bin/activate"
fi

python3 tellbird.py
