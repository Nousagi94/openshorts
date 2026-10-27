"""Local browser UI for the funny-clip pipeline: no Terminal typing needed.

    python -m funny.webapp            # opens http://127.0.0.1:8765

Paste a link or drop a file, pick the look, press "Buat Klip"; progress
streams into the page and the finished clips play and download there.

Standard library only (ThreadingHTTPServer), bound to 127.0.0.1, one job at a
time (a Mac has one GPU and whisper + the LLM already fill it). Uploads are
sent as the raw request body with the name in a header, so a multi-GB video
streams to disk without a multipart parser.

Each job gets ``output/web/<job id>/``; the pipeline writes there exactly
what the CLI writes, so a job folder can be reopened with ``--transcript``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import pipeline

HOST = "127.0.0.1"
PORT = int(os.environ.get("FUNNY_WEB_PORT", "8765"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ROOT = os.environ.get("FUNNY_WEB_OUTPUT") or os.path.join(pipeline.REPO_ROOT, "output", "web")
UPLOADS = os.path.join(ROOT, "uploads")
CHUNK = 1 << 20

# Facecam corners offered in the UI, as (x, y, w, h) fractions of the frame,
# for the three sizes a stream overlay usually comes in.
FACECAM_SIZES = {"kecil": (0.2, 0.3), "sedang": (0.25, 0.4), "besar": (0.33, 0.5)}
FACECAM_CORNERS = ("kanan-bawah", "kiri-bawah", "kanan-atas", "kiri-atas")

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")
_JOB_ID = re.compile(r"^\d{8}-\d{6}$")


def facecam_spec(corner, size):
    """``"x,y,w,h"`` fractions for a corner + size picked in the UI."""
    w, h = FACECAM_SIZES.get(size, FACECAM_SIZES["sedang"])
    x = 1.0 - w if corner.startswith("kanan") else 0.0
    y = 1.0 - h if corner.endswith("bawah") else 0.0
    return f"{x:.3f},{y:.3f},{w:.3f},{h:.3f}"


def options_from_form(form):
    """Pipeline options + run kwargs from the page's JSON form."""
    layout = form.get("layout") or "blur"
    options = {"style": form.get("style") or "streamer", "layout": layout,
               "music": bool(form.get("music", True)),
               "effects": bool(form.get("effects", True)),
               "captions": bool(form.get("captions", True)),
               "context": bool(form.get("context", True))}
    if "meme" in form and form["meme"] is not None:
        options["meme"] = bool(form["meme"])
    if layout == "stream":
        options["facecam"] = facecam_spec(form.get("corner") or "kanan-bawah",
                                          form.get("size") or "sedang")
    kwargs = {"max_clips": max(1, min(10, int(form.get("max_clips") or 5))),
              "start": pipeline.parse_time(form.get("dari") or None),
              "end": pipeline.parse_time(form.get("sampai") or None),
              "use_model": not form.get("no_model")}
    return options, kwargs


class Job:
    def __init__(self, job_id, source, options, kwargs):
        self.id = job_id
        self.source = source
        self.options = options
        self.kwargs = kwargs
        self.dir = os.path.join(ROOT, job_id)
        self.state = "antri"      # antri -> jalan -> selesai | gagal
        self.log = []
        self.clips = []
        self.error = None
        self.started = time.time()

    def add_log(self, *parts):
        line = " ".join(str(p) for p in parts)
        self.log.append(line)
        print(line, flush=True)

    def to_dict(self):
        return {"id": self.id, "state": self.state, "log": self.log[-200:],
                "clips": self.clips, "error": self.error,
                "elapsed": round(time.time() - self.started)}


class Jobs:
    """Every job of this run; one worker thread runs them in order."""

    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self.gate = threading.Lock()

    def submit(self, source, options, kwargs):
        with self.lock:
            job_id = time.strftime("%Y%m%d-%H%M%S")
            while job_id in self.jobs:
                time.sleep(1)
                job_id = time.strftime("%Y%m%d-%H%M%S")
            job = self.jobs[job_id] = Job(job_id, source, options, kwargs)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job):
        with self.gate:
            job.state = "jalan"
            try:
                summary = pipeline.run(job.source, job.dir, options=job.options,
                                       log=job.add_log, **job.kwargs)
                job.clips = [{
                    "file": c["file"], "title": c["title"], "caption": c["caption"],
                    "hashtags": c["hashtags"], "score": c["funny_score"],
                    "duration": round(c["end"] - c["start"]),
                    "reason": c.get("reason", "")} for c in summary["clips"]]
                job.state = "selesai"
                job.add_log(f"✅ Selesai: {len(job.clips)} klip")
            except Exception as e:
                job.state = "gagal"
                job.error = f"{type(e).__name__}: {e}"
                job.add_log("❌ " + job.error)
                traceback.print_exc()


JOBS = Jobs()


def open_folder(path):
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, path])


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoKlipLucu/1"

    def log_message(self, fmt, *args):  # keep the console for pipeline logs
        pass

    # --- helpers ------------------------------------------------------------

    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _send_file(self, path, ctype):
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range: the last N bytes
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the player seeked elsewhere
                left -= len(chunk)

    # --- routes -------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/api/jobs":
            return self._json([j.to_dict() for j in sorted(JOBS.jobs.values(),
                                                           key=lambda j: j.id, reverse=True)])
        m = re.match(r"^/api/jobs/([\d-]+)$", path)
        if m:
            job = JOBS.jobs.get(m.group(1))
            return self._json(job.to_dict()) if job else self._error("job tidak ada", 404)
        m = re.match(r"^/files/([\d-]+)/([^/]+)$", path)
        if m:
            job_id, name = m.group(1), unquote(m.group(2))
            if not _JOB_ID.match(job_id) or not re.match(r"^funny_\d{2}\.(mp4|txt)$", name):
                return self._error("file tidak boleh", 403)
            full = os.path.join(ROOT, job_id, name)
            if not os.path.exists(full):
                return self._error("file tidak ada", 404)
            ctype = "video/mp4" if name.endswith(".mp4") else "text/plain; charset=utf-8"
            return self._send_file(full, ctype)
        self._error("tidak ditemukan", 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/upload":
            return self._upload()
        if path == "/api/jobs":
            return self._create_job()
        m = re.match(r"^/api/jobs/([\d-]+)/open$", path)
        if m and m.group(1) in JOBS.jobs:
            open_folder(JOBS.jobs[m.group(1)].dir)
            return self._json({"ok": True})
        self._error("tidak ditemukan", 404)

    def _upload(self):
        name = _SAFE.sub("_", unquote(self.headers.get("X-Filename") or "video.mp4"))[-120:]
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._error("file kosong")
        os.makedirs(UPLOADS, exist_ok=True)
        dest = os.path.join(UPLOADS, f"{int(time.time())}_{name}")
        with open(dest, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(CHUNK, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            os.remove(dest)
            return self._error("upload terputus")
        self._json({"upload": os.path.basename(dest)})

    def _create_job(self):
        try:
            form = self._body_json()
            options, kwargs = options_from_form(form)
        except (ValueError, TypeError) as e:
            return self._error(f"isian tidak valid: {e}")
        if form.get("upload"):
            source = os.path.join(UPLOADS, os.path.basename(form["upload"]))
            if not os.path.exists(source):
                return self._error("file upload tidak ditemukan")
        elif re.match(r"^https?://", (form.get("url") or "").strip()):
            source = form["url"].strip()
        else:
            return self._error("isi link video atau pilih file dulu")
        if kwargs["start"] is not None and kwargs["end"] is not None \
                and kwargs["end"] <= kwargs["start"]:
            return self._error("waktu 'sampai' harus setelah 'dari'")
        job = JOBS.submit(source, options, kwargs)
        self._json(job.to_dict(), HTTPStatus.CREATED)


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(pipeline.REPO_ROOT, ".env"))
    except ImportError:
        pass
    os.makedirs(ROOT, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Auto Klip Lucu jalan di {url}  (tutup jendela ini untuk berhenti)", flush=True)
    if os.environ.get("FUNNY_WEB_NO_BROWSER") != "1":
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
