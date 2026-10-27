@echo off
rem Double-click in Explorer: starts the local app and opens it in the browser.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
rem Pick up PATH changes from the installer even if Explorer has the old one.
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p"
if not exist ".venv\Scripts\python.exe" (
  echo Belum diinstal. Klik dua kali "Install.bat" dulu.
  pause
  exit /b 1
)
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434 -TimeoutSec 2 | Out-Null } catch { Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden }" >nul 2>&1
echo Auto Klip Lucu sedang berjalan. Halamannya terbuka di browser.
echo Biarkan jendela ini terbuka selama dipakai; tutup untuk berhenti.
echo.
".venv\Scripts\python.exe" -m funny.webapp
pause
