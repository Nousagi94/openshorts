# Panduan Auto Clipping Momen Lucu

Sistem ini mengambil video panjang (podcast, live stream, vlog), mencari
momen paling lucu, lalu mengeditnya jadi klip vertikal 9:16 siap upload ke
TikTok, Reels, dan Shorts: subtitle per kata, efek saat punchline, dan musik
latar yang aman dari copyright. Semuanya jalan di Mac kamu, tanpa cloud
berbayar.

## Cara paling mudah: tanpa Terminal

1. Download kodenya sebagai ZIP:
   [funny-clips.zip](https://github.com/Nousagi94/openshorts/archive/refs/heads/funny-clips.zip)
   (setelah pull request di-merge: tombol **Code**, lalu **Download ZIP** di halaman repo).
2. Klik dua kali file ZIP-nya, lalu pindahkan folder hasilnya ke folder Home kamu.
3. Buka folder itu. **Klik kanan** `Install.command`, pilih **Open**, lalu **Open** lagi.
   Klik kanan diperlukan karena macOS menahan file dari internet saat pertama kali dibuka.
   Masukkan password Mac kalau diminta (ketikannya memang tidak terlihat), lalu tunggu
   sampai muncul "Instalasi selesai" (20-40 menit).
4. Untuk memakai: klik dua kali `Auto Klip Lucu.command` (pertama kali: klik kanan, lalu
   Open). Halaman Auto Klip Lucu terbuka di browser.
5. Tempel link atau seret file video, isi menit mulai dan selesai kalau perlu, pilih gaya,
   lalu klik **Buat Klip**. Klipnya bisa langsung ditonton, di-download, dan caption-nya
   bisa di-copy dari halaman itu.

Jendela Terminal yang ikut terbuka biarkan saja selama aplikasi dipakai. Tutup jendela
itu kalau sudah selesai. Bagian di bawah ini untuk yang mau memakai Terminal.

## 1. Pasang sekali saja (lewat Terminal)

Butuh: Mac (disarankan Apple Silicon M1 ke atas, RAM 16 GB), ruang kosong
sekitar 15 GB, dan [Homebrew](https://brew.sh).

```bash
git clone <link fork kamu>
cd openshorts
scripts/setup_mac.sh
```

Skrip ini memasang ffmpeg, Python, Ollama (AI lokal), dan model AI-nya.
Pertama kali butuh waktu agak lama karena download model.

## 2. Pakai

```bash
# Cukup link
./klip -u https://youtu.be/xxxx

# Link + rentang waktu (lebih cepat untuk video panjang)
./klip -u https://youtu.be/xxxx --dari 10:00 --sampai 25:30

# File yang sudah ada di Mac
./klip -i ~/Movies/podcast.mp4

# Klip live stream: facecam di atas, game/layar di bawah
./klip -i stream.mp4 --layout stream --facecam 1470,630,420,420
```

Hasilnya ada di folder `output/funny/`:

- `funny_01.mp4`, `funny_02.mp4`, ... klip siap upload
- `funny_01.txt`, ... judul, caption, dan hashtag siap copy-paste
- `funny_clips.json` detail lengkap (skor lucu, waktu punchline, dll)

## 3. Atur gaya

| Opsi | Fungsi |
|---|---|
| `--style streamer` | (bawaan) subtitle 1 kata, biru outline putih, seperti klip streamer |
| `--style meme` | subtitle kapital kuning, kata lucu merah miring, ada teks meme |
| `--layout blur` | (bawaan) video utuh di tengah, latar blur |
| `--layout crop` | potong tengah sampai penuh (cocok 1 orang) |
| `--layout stream` | facecam atas, layar bawah, wajah full screen saat punchline |
| `--max-clips 3` | jumlah klip maksimal |
| `--no-music`, `--no-effects`, `--no-captions` | matikan bagian tertentu |
| `--mood chill` | pilih musik dengan mood tertentu |
| `--music-db -24` | musik lebih pelan |

`--facecam x,y,w,h` adalah posisi kotak facecam di video asli (piksel,
atau pecahan 0-1). Contoh `0.75,0.55,0.25,0.4` artinya facecam di pojok
kanan bawah.

## 4. Musik

Lagu trending TikTok itu ber-copyright: kalau ditempel langsung di video,
klip bisa di-mute atau diklaim. Jadi:

- Taruh musik bebas royalti di folder `assets/music/` (YouTube Audio
  Library, Pixabay Music, Uppbeat). Tanpa file, sistem pakai musik buatan
  sendiri yang aman.
- Mau pakai sound yang lagi FYP? Render dengan `--no-music` atau musik
  pelan, lalu tambahkan sound trending itu langsung di aplikasi TikTok saat
  upload. Di sana lisensinya sudah diurus TikTok.

## 5. Kalau ada masalah

- **AI tidak jalan / tidak ada Ollama:** sistem tetap jalan dan memotong
  di sekitar suara tawa saja. Jalankan `ollama serve` lalu coba lagi.
- **Download link gagal:** update yt-dlp: `.venv/bin/pip install -U yt-dlp`.
- **Mau pakai Gemini (lebih pintar, berbayar):** isi `GEMINI_API_KEY` di
  `.env` dan hapus baris `LLM_BASE_URL`.
