#!/usr/bin/env bash
# One-time setup for running funny_clip.py locally on a Mac (Apple Silicon or Intel).
# Installs ffmpeg + Python + Ollama with Homebrew, the Python deps into .venv,
# and pulls the local model that picks the funny moments. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${FUNNY_LLM_MODEL:-qwen2.5:7b}"
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [ "$RAM_GB" -lt 12 ] && [ -z "${FUNNY_LLM_MODEL:-}" ]; then
  MODEL="qwen2.5:3b"   # 8 GB Macs: the 7B model does not fit next to whisper
fi

load_brew() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)" && return 0
  done
  return 1
}
if ! command -v brew >/dev/null && ! load_brew; then
  echo "==> Memasang Homebrew (masukkan password Mac kalau diminta; ketikannya memang tidak terlihat)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew || { echo "Homebrew gagal dipasang. Coba lagi, atau pasang dari https://brew.sh"; exit 1; }
fi

echo "==> ffmpeg, python, ollama"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list ollama >/dev/null 2>&1 || brew install ollama

echo "==> Python packages (.venv)"
[ -d .venv ] || "$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-funny.txt

echo "==> Font untuk subtitle"
mkdir -p "$HOME/Library/Fonts"
cp -n fonts/*.ttf "$HOME/Library/Fonts/" 2>/dev/null || true

echo "==> Model AI lokal ($MODEL, RAM ${RAM_GB} GB)"
(pgrep -x ollama >/dev/null) || (brew services start ollama >/dev/null 2>&1 || (ollama serve >/dev/null 2>&1 &))
sleep 3
ollama pull "$MODEL"

if [ ! -f .env ] || ! grep -q "^LLM_BASE_URL" .env; then
  cat >> .env <<ENV
# funny_clip.py: everything local
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=$MODEL
TRANSCRIBE_BACKEND=mlx
MLX_WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
WHISPER_MODEL=small
ENV
fi

chmod +x klip "Auto Klip Lucu.command" 2>/dev/null || true
echo
echo "Selesai. Klik dua kali 'Auto Klip Lucu.command' untuk mulai,"
echo "atau dari Terminal:  ./klip -u https://youtu.be/xxxx --dari 10:00 --sampai 25:00"
