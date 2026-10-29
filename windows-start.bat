@echo off
cd /d "%~dp0"
REM %USERPROFILE%\tts-bot is the venv from before the rename - still works, no need to re-run setup.
if exist "%USERPROFILE%\tellbird\Scripts\activate.bat" (
  call "%USERPROFILE%\tellbird\Scripts\activate.bat"
) else (
  call "%USERPROFILE%\tts-bot\Scripts\activate.bat"
)
python -m tellbird
pause
