"""The seen-store doubles as the dedupe index AND the application tracker.

Two backends, one interface:

  FileStore   seen.json on disk — local dev, GitHub Actions cache.
  RedisStore  Upstash Redis over HTTP — survives Render's ephemeral disk and
              free-tier spin-down, so 4x/day dedupe actually holds.

open_store() picks RedisStore automatically when the Upstash env vars are set.
Both expose: unseen / record / mark_applied / stats / export_csv, plus an
advisory run-lock so two near-simultaneous cron pings can't both run.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .fetch import Job

TRACK_COLS = ["first_seen", "company", "title", "location", "score",
              "reason", "applied", "applied_on", "url"]


def _meta(j: Job, emailed: bool, now: str) -> dict:
    return {
        "first_seen": now,
        "company": j.company,
        "title": j.title,
        "location": j.location,
        "url": j.url,
        "score": j.score,
        "reason": j.reason,
        "emailed": emailed,
        "applied": False,
        "applied_on": None,
    }


def _write_csv(rows: dict[str, dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["job_id"] + TRACK_COLS, extrasaction="ignore")
        w.writeheader()
        for jid, row in sorted(rows.items(),
                               key=lambda kv: kv[1].get("first_seen", ""), reverse=True):
            w.writerow({"job_id": jid, **row})
    return path


def _count_stats(rows: dict[str, dict]) -> dict:
    return {
        "tracked": len(rows),
        "emailed": sum(1 for v in rows.values() if v.get("emailed")),
        "applied": sum(1 for v in rows.values() if v.get("applied")),
    }


class FileStore:
    """seen.json on local disk."""

    def __init__(self, path: str | Path = "seen.json"):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                print(f"  ! {self.path} corrupt, starting fresh")

    def unseen(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if j.job_id not in self.data]

    def record(self, jobs: list[Job], emailed: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for j in jobs:
            self.data.setdefault(j.job_id, _meta(j, emailed, now))
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    def mark_applied(self, job_id: str) -> bool:
        if job_id not in self.data:
            return False
        self.data[job_id]["applied"] = True
        self.data[job_id]["applied_on"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        return True

    def stats(self) -> dict:
        return _count_stats(self.data)

    def export_csv(self, path: str | Path = "out/tracker.csv") -> Path:
        return _write_csv(self.data, path)

    # Local runs are single-process; the lock is a no-op that always succeeds.
    def acquire_lock(self, ttl: int = 900) -> bool:
        return True

    def release_lock(self) -> None:
        pass


class RedisStore:
    """Upstash Redis via its HTTP REST API — one small `requests` call per op.

    The seen index lives in a single hash (field = job_id, value = JSON meta),
    so a run reads all ids with one HKEYS and writes new ones with one HSET.
    No persistent connection, which is exactly what a service that spins down
    on Render's free tier needs.
    """

    def __init__(self, url: str, token: str, key: str = "jobhunt:seen"):
        self.url = url.rstrip("/")
        self.token = token
        self.key = key
        self.lock_key = f"{key}:lock"

    def _cmd(self, *args):
        r = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}"},
            json=[str(a) for a in args],
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"upstash HTTP {r.status_code}: {r.text[:200]}")
        return r.json().get("result")

    @staticmethod
    def _pairs_to_dict(flat) -> dict[str, dict]:
        """HGETALL returns [field, value, field, value, ...]."""
        out: dict[str, dict] = {}
        it = iter(flat or [])
        for field, value in zip(it, it):
            try:
                out[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                out[field] = {}
        return out

    def _all(self) -> dict[str, dict]:
        return self._pairs_to_dict(self._cmd("HGETALL", self.key))

    def unseen(self, jobs: list[Job]) -> list[Job]:
        seen = set(self._cmd("HKEYS", self.key) or [])
        return [j for j in jobs if j.job_id not in seen]

    def record(self, jobs: list[Job], emailed: bool) -> None:
        if not jobs:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        flat: list[str] = []
        for j in jobs:
            flat += [j.job_id, json.dumps(_meta(j, emailed, now), ensure_ascii=False)]
        self._cmd("HSET", self.key, *flat)

    def mark_applied(self, job_id: str) -> bool:
        raw = self._cmd("HGET", self.key, job_id)
        if not raw:
            return False
        meta = json.loads(raw)
        meta["applied"] = True
        meta["applied_on"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._cmd("HSET", self.key, job_id, json.dumps(meta, ensure_ascii=False))
        return True

    def stats(self) -> dict:
        return _count_stats(self._all())

    def export_csv(self, path: str | Path = "out/tracker.csv") -> Path:
        return _write_csv(self._all(), path)

    def acquire_lock(self, ttl: int = 900) -> bool:
        """SET NX EX — returns True only if we got the lock (no run in flight)."""
        return self._cmd("SET", self.lock_key, "1", "NX", "EX", ttl) == "OK"

    def release_lock(self) -> None:
        try:
            self._cmd("DEL", self.lock_key)
        except Exception:
            pass  # the TTL will clear it anyway


# Back-compat alias: older code and tests import `Store`.
Store = FileStore


def open_store(cfg: dict | None = None):
    """Pick a backend. Upstash if its env vars are set, else a local file."""
    cfg = cfg or {}
    url = (os.getenv("UPSTASH_REDIS_REST_URL") or "").strip()
    token = (os.getenv("UPSTASH_REDIS_REST_TOKEN") or "").strip()
    backend = (os.getenv("STORE_BACKEND") or ("redis" if url and token else "file")).strip().lower()
    if backend == "redis":
        if not (url and token):
            raise SystemExit(
                "STORE_BACKEND=redis but UPSTASH_REDIS_REST_URL / "
                "UPSTASH_REDIS_REST_TOKEN are not set (see .env.example)")
        return RedisStore(url, token)
    return FileStore(cfg.get("seen_file", "seen.json"))
