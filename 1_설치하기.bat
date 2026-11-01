@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo [!] Python not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
python -m pip install --user --quiet -r requirements.txt
python alimi.py setup
pause
