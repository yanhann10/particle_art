#!/usr/bin/env python3
"""Feedback mutation API server.

Runs on the AWS VM at port 7654.
Accepts POST /mutate {parent_id, directive} → spawns improv_tick.py → returns job status.

Start with:
    python3 scripts/feedback_api.py
or via systemd / tmux for persistence.

The Vercel /api/mutate proxy forwards to this server.
CORS is open (Vercel → this server is server-to-server; no browser-CORS issue).
"""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("FEEDBACK_API_PORT", 7654))

# in-memory job store: {job_id: {status, new_id?, error?}}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _run_mutation(job_id: str, parent_id: str, directive: str) -> None:
    with _lock:
        _jobs[job_id] = {"status": "running", "new_id": None}

    cmd = [
        sys.executable, str(REPO / "scripts" / "improv_tick.py"),
        "--parent", parent_id,
        "--user-directive", directive,
        "--no-critic",  # faster for interactive feedback
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True, timeout=360
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # Parse new_id from stdout line "new_id: <id>"
        m = re.search(r"^new_id:\s*([a-z0-9]{3})", stdout, re.MULTILINE)
        if result.returncode == 0 and m:
            with _lock:
                _jobs[job_id] = {"status": "done", "new_id": m.group(1)}
            print(f"[job {job_id}] done → {m.group(1)}")
        else:
            err = stderr[-400:] if stderr else stdout[-400:]
            with _lock:
                _jobs[job_id] = {"status": "error", "error": err, "rc": result.returncode}
            print(f"[job {job_id}] error rc={result.returncode}: {err[:120]}")
    except subprocess.TimeoutExpired:
        with _lock:
            _jobs[job_id] = {"status": "error", "error": "timeout after 360s"}
    except Exception as e:
        with _lock:
            _jobs[job_id] = {"status": "error", "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {fmt % args}")

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path == "/replace":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                drop_id = data["drop_id"]
                new_id  = data["new_id"]
            except Exception:
                self._send_json({"error": "bad request"}, 400)
                return
            try:
                result = subprocess.run(
                    [sys.executable, str(REPO / "scripts" / "drop_and_reparent.py"),
                     "--drop", drop_id, "--new-id", new_id],
                    cwd=str(REPO), capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "error": result.stderr[:200]})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if self.path == "/mutate":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                parent_id = data["parent_id"]
                directive = data["directive"]
            except Exception:
                self._send_json({"error": "bad request"}, 400)
                return

            job_id = str(uuid.uuid4())[:8]
            t = threading.Thread(target=_run_mutation, args=(job_id, parent_id, directive),
                                 daemon=True)
            t.start()
            self._send_json({"job_id": job_id, "status": "running"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_GET(self):
        if self.path.startswith("/status/"):
            job_id = self.path.split("/status/")[1].strip("/")
            with _lock:
                state = _jobs.get(job_id)
            if state is None:
                self._send_json({"error": "unknown job"}, 404)
            else:
                self._send_json(state)
        elif self.path == "/health":
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Feedback API listening on port {PORT}")
    print(f"  POST /mutate   {{parent_id, directive}}")
    print(f"  GET  /status/<job_id>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
