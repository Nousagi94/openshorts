#!/usr/bin/env bash
# Double-click in Finder: one-time setup of Auto Klip Lucu on this Mac.
cd "$(dirname "$0")"
echo "=== Instalasi Auto Klip Lucu ==="
echo "Biarkan jendela ini terbuka sampai muncul tulisan Selesai (20-40 menit)."
echo
if bash scripts/setup_mac.sh; then
  echo
  echo "=== Instalasi selesai. Jendela ini boleh ditutup. ==="
  echo "Untuk mulai: klik dua kali 'Auto Klip Lucu.command'."
else
  echo
  echo "=== Instalasi gagal. Screenshot jendela ini dan kirim ke thread project. ==="
fi
read -r -p "Tekan Enter untuk menutup..." _
