@echo off
rem Double-click in Explorer: one-time setup of Auto Klip Lucu on this PC.
chcp 65001 >nul
cd /d "%~dp0"
echo === Instalasi Auto Klip Lucu ===
echo Biarkan jendela ini terbuka sampai muncul tulisan Instalasi selesai (20-40 menit).
echo Kalau muncul jendela "Do you want to allow this app...", klik Yes.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_windows.ps1"
if errorlevel 1 (
  echo.
  echo === Instalasi gagal. Screenshot jendela ini dan kirim ke thread project. ===
) else (
  echo.
  echo === Instalasi selesai. Jendela ini boleh ditutup. ===
  echo Untuk mulai: klik dua kali "Auto Klip Lucu.bat".
)
pause
