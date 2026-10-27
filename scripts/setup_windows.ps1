# One-time setup for running Auto Klip Lucu locally on Windows 10/11.
# Installs ffmpeg, Python 3.12 and Ollama with winget, the Python deps into
# .venv, and pulls the local model that picks the funny moments. Safe to re-run.
# ASCII only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Update-PathFromRegistry {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install-WithWinget($id, $name) {
    Write-Host "    memasang $name ..."
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
    Update-PathFromRegistry
}

function Test-Python312 {
    try { & py -3.12 --version *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget belum ada. Buka Microsoft Store, update 'App Installer', lalu jalankan Install.bat lagi."
    exit 1
}

$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$model = if ($env:FUNNY_LLM_MODEL) { $env:FUNNY_LLM_MODEL } elseif ($ramGB -lt 12) { 'qwen2.5:3b' } else { 'qwen2.5:7b' }

Write-Host '==> ffmpeg, python, ollama'
Update-PathFromRegistry
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-WithWinget 'Gyan.FFmpeg' 'ffmpeg' }
if (-not (Test-Python312)) { Install-WithWinget 'Python.Python.3.12' 'Python 3.12' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Install-WithWinget 'Ollama.Ollama' 'Ollama' }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'ffmpeg tidak ditemukan setelah instalasi. Restart komputer lalu jalankan Install.bat lagi.' }
if (-not (Test-Python312)) { throw 'Python 3.12 tidak ditemukan setelah instalasi. Restart komputer lalu jalankan Install.bat lagi.' }

Write-Host '==> Paket Python (.venv), 5-15 menit, biarkan berjalan'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & py -3.12 -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -r requirements-funny.txt | Select-String -Pattern '^(Collecting|Successfully)' | Out-Host
& .venv\Scripts\python.exe -c "import faster_whisper, numpy, pydantic"
if ($LASTEXITCODE -ne 0) { throw 'Paket Python gagal dipasang.' }

Write-Host "==> Model AI lokal ($model, RAM $ramGB GB)"
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434 -TimeoutSec 3 | Out-Null }
catch { Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep -Seconds 4 }
& ollama pull $model
if ($LASTEXITCODE -ne 0) { throw 'Model AI gagal di-download. Cek internet lalu jalankan Install.bat lagi.' }

if (-not (Test-Path '.env') -or -not (Select-String -Path '.env' -Pattern '^LLM_BASE_URL' -Quiet)) {
    @(
        '# Auto Klip Lucu: everything local',
        'LLM_BASE_URL=http://localhost:11434/v1',
        "LLM_MODEL=$model",
        'TRANSCRIBE_BACKEND=whisper',
        'WHISPER_MODEL=small',
        'WHISPER_DEVICE=cpu',
        'WHISPER_COMPUTE=int8'
    ) | Add-Content -Path '.env' -Encoding ASCII
}

Write-Host ''
Write-Host "Selesai. Klik dua kali 'Auto Klip Lucu.bat' untuk mulai."
