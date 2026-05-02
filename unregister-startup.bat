@echo off
REM Skida monitor iz Windows auto-start-a.

set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SureBetMonitor.lnk"

if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo OK: monitor vise nece da se pokrece automatski.
) else (
    echo Monitor nije bio registrovan za auto-start.
)
pause
