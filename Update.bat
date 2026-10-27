@echo off
rem Double-click in Explorer: fetch the latest Auto Klip Lucu code into this folder.
rem Keeps .venv, .env, output and your own music and sound effects.
chcp 65001 >nul
cd /d "%~dp0"
echo === Update Auto Klip Lucu ===
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$tmp = Join-Path $env:TEMP ('aklu_' + [guid]::NewGuid());" ^
  "New-Item -ItemType Directory $tmp | Out-Null;" ^
  "Invoke-WebRequest -UseBasicParsing 'https://github.com/Nousagi94/openshorts/archive/refs/heads/funny-clips.zip' -OutFile (Join-Path $tmp 'u.zip');" ^
  "Expand-Archive (Join-Path $tmp 'u.zip') $tmp;" ^
  "$src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName;" ^
  "robocopy $src . /E /NFL /NDL /NJH /NJS /XD .venv output music sfx /XF .env | Out-Null;" ^
  "if ($LASTEXITCODE -ge 8) { throw 'Gagal menyalin file update' };" ^
  "Remove-Item -Recurse -Force $tmp"
if errorlevel 1 (
  echo === Update gagal. Screenshot jendela ini dan kirim ke thread project. ===
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip install -q -r requirements-funny.txt
echo.
echo === Update selesai. Buka lagi "Auto Klip Lucu.bat". ===
pause
