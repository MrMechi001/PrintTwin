@echo off
rem PrintTwin (Windows): start the bridge, the viewer opens in your browser.
rem Needs Python 3.11 or newer (python.org) - nothing else to install.
cd /d "%~dp0"
set "PY="
if exist "%~dp0pyenv\Scripts\python.exe" set "PY="%~dp0pyenv\Scripts\python.exe""
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul || goto nopython
%PY% -u "%~dp0run.py" --open
pause
exit /b

:nopython
echo PrintTwin needs Python 3.11 or newer: https://www.python.org/downloads/
echo (in the installer tick "Add python.exe to PATH"), then start PrintTwin.bat again.
pause
exit /b 1
