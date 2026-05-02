@echo off
REM Registruje monitor da se automatski pokrece pri Windows logovanju.
REM Pokreni jednom (dupli klik). Za odjavu: unregister-startup.bat

setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%~dp0watchdog.bat"
set "SHORTCUT=%STARTUP%\SureBetMonitor.lnk"

if not exist "%TARGET%" (
    echo GRESKA: nije nadjen watchdog.bat na %TARGET%
    pause
    exit /b 1
)

REM Napravi precicu preko PowerShell-a (radi minimizovano)
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath = '%TARGET%';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'Sure Bet Monitor (auto-start)';" ^
  "$s.Save()"

if exist "%SHORTCUT%" (
    echo OK: monitor ce se pokretati automatski sa Windowsom.
    echo     Precica: %SHORTCUT%
    echo     Za odjavu pokreni unregister-startup.bat
) else (
    echo GRESKA: precica nije napravljena.
)
pause
