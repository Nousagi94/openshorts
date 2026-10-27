#!/usr/bin/env bash
# Double-click in Finder: fetch the latest Auto Klip Lucu code into this folder.
# Keeps the installed environment (.venv), settings (.env), results (output/)
# and your own music and sound effects; only the program files are replaced.
set -euo pipefail
cd "$(dirname "$0")"
BRANCH="${FUNNY_BRANCH:-funny-clips}"
URL="https://github.com/Nousagi94/openshorts/archive/refs/heads/$BRANCH.zip"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== Update Auto Klip Lucu ==="
echo "Mengambil versi terbaru..."
curl -fsSL "$URL" -o "$TMP/update.zip"
unzip -q "$TMP/update.zip" -d "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
rsync -a --exclude ".venv/" --exclude ".env" --exclude "output/" \
      --exclude "assets/music/" --exclude "assets/sfx/" "$SRC/" ./
# Files from the internet are quarantined by macOS; these are ours, so clear it.
xattr -dr com.apple.quarantine . 2>/dev/null || true
chmod +x ./*.command klip scripts/*.sh 2>/dev/null || true

if [ -x .venv/bin/pip ]; then
  echo "Memperbarui paket Python..."
  .venv/bin/pip install -q -r requirements-funny.txt
fi
echo
echo "=== Update selesai. Buka lagi 'Auto Klip Lucu.command'. ==="
read -r -p "Tekan Enter untuk menutup..." _
