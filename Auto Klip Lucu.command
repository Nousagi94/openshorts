#!/usr/bin/env bash
# Double-click in Finder: starts the local app and opens it in the browser.
cd "$(dirname "$0")"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break
done
if [ ! -x .venv/bin/python ]; then
  echo "Belum diinstal. Klik dua kali 'Install.command' dulu."
  read -r -p "Tekan Enter untuk menutup..." _
  exit 1
fi
if command -v ollama >/dev/null && ! curl -s localhost:11434 >/dev/null; then
  (ollama serve >/dev/null 2>&1 &)
  sleep 2
fi
echo "Auto Klip Lucu sedang berjalan. Halamannya terbuka di browser."
echo "Biarkan jendela ini terbuka selama dipakai; tutup untuk berhenti."
echo
exec .venv/bin/python -m funny.webapp
