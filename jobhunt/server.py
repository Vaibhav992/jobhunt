"""Tiny HTTP wrapper so a free external cron (cron-job.org) can run the pipeline
4x/day on Render — where there is no native scheduler and the disk is wiped on
every spin-down.

    GET  /health   liveness probe (Render + uptime pings)
    GET  /         plain status: last run, tracker counts
    POST /run      trigger a run. Requires the RUN_TOKEN secret. Returns 202 and
                   runs in the background so the pinger doesn't wait (or time out)
                   on the whole fetch+screen+email cycle.

The seen-store MUST be Upstash here (Render's free disk does not persist), so a
run-lock in Redis stops two near-simultaneous pings from both firing.
"""
from __future__ import annotations

import hmac
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request

from . import cli
from .store import open_store

app = Flask(__name__)


def _bootstrap_profile() -> None:
    """profile.json is gitignored, so on Render it arrives as an env var.
    Write it to disk once at boot if it isn't already there (same trick the
    GitHub Actions workflow uses)."""
    blob = (os.getenv("PROFILE_JSON") or "").strip()
    if blob and not Path("profile.json").exists():
        Path("profile.json").write_text(blob, encoding="utf-8")


_bootstrap_profile()

_lock = threading.Lock()          # in-process guard (single gunicorn worker)
_running = False
_last: dict = {"status": "idle", "detail": "no run yet this boot"}


def _authorized(req) -> bool:
    expected = (os.getenv("RUN_TOKEN") or "").strip()
    if not expected:
        return False  # no token configured => endpoint stays closed
    got = (req.headers.get("X-Run-Token")
           or req.args.get("token")
           or "").strip()
    return bool(got) and hmac.compare_digest(got, expected)


def _do_run() -> None:
    """Run the full pipeline once, holding a cross-process Redis lock."""
    global _running, _last
    store = open_store({})
    if not store.acquire_lock(ttl=900):
        _last = {"status": "skipped", "detail": "another run holds the lock"}
        return
    try:
        _last = {"status": "running", "detail": ""}
        rc = cli.main(["run", "--send"])
        _last = {"status": "ok" if rc == 0 else "error", "detail": f"exit={rc}"}
    except Exception as e:  # never let a run crash the worker
        _last = {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        store.release_lock()
        with _lock:
            _running = False


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    try:
        stats = open_store({}).stats()
    except Exception as e:
        stats = {"error": f"{type(e).__name__}: {e}"}
    return jsonify(service="jobhunt", last_run=_last, tracker=stats)


@app.post("/run")
def run():
    global _running
    if not _authorized(request):
        return jsonify(error="unauthorized"), 401
    with _lock:
        if _running:
            return jsonify(status="already running"), 409
        _running = True
    threading.Thread(target=_do_run, daemon=True).start()
    return jsonify(status="started"), 202


# `flask --app jobhunt.server run` for local testing; gunicorn in production.
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
