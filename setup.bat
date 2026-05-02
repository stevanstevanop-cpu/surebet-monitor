@echo off
REM Instalira sve sto je potrebno i pokrece monitor

where python >nul 2>nul
if errorlevel 1 (
    echo Python nije instaliran. Instaliraj Python 3.10+ sa https://www.python.org/downloads/
    pause
    exit /b 1
)

echo === Pravim virtuelno okruzenje (.venv) ===
python -m venv .venv
call .venv\Scripts\activate.bat

echo === Instaliram pakete ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo === Instaliram Chromium za Playwright ===
python -m playwright install chromium

echo.
echo Gotovo. Za pokretanje:
echo   .venv\Scripts\activate.bat
echo   python main.py
echo.
pause
