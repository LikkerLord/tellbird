@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   Tellbird - setup (Windows)
echo ============================================
echo.

REM Prefer the newest Python PyTorch supports (3.10-3.14).
set "PYCMD="
py -3.14 --version >nul 2>&1 && set "PYCMD=py -3.14"
if not defined PYCMD (
  py -3.13 --version >nul 2>&1 && set "PYCMD=py -3.13"
)
if not defined PYCMD (
  py -3.12 --version >nul 2>&1 && set "PYCMD=py -3.12"
)
if not defined PYCMD (
  py -3.11 --version >nul 2>&1 && set "PYCMD=py -3.11"
)
if not defined PYCMD (
  py -3.10 --version >nul 2>&1 && set "PYCMD=py -3.10"
)
if not defined PYCMD (
  echo Python 3.10-3.14 was not found.
  echo Install it from https://www.python.org/downloads/
  echo During install, tick "Add Python to PATH".
  pause
  exit /b 1
)
echo Using %PYCMD%

if not exist "%USERPROFILE%\tellbird\Scripts\activate.bat" (
  echo Creating virtual environment ...
  %PYCMD% -m venv "%USERPROFILE%\tellbird"
)
call "%USERPROFILE%\tellbird\Scripts\activate.bat"

echo Installing packages (PyTorch is large; this may take a while) ...
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist config.json copy config.example.json config.json

echo.
echo Pre-downloading model + default voice ...
python -c "from pocket_tts import TTSModel; m=TTSModel.load_model(language='english'); m.get_state_for_audio_prompt('alba'); print('Model ready.')"

echo.
echo ============================================
echo  Setup complete.
echo  1) Install VB-CABLE: https://vb-audio.com/Cable/
echo  2) Edit config.json: set channel, voice, and
echo     output_device to "CABLE Input (VB-Audio Virtual Cable)"
echo  3) Double-click windows-start.bat
echo ============================================
pause
