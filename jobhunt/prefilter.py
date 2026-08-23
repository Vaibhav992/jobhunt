"""Deterministic filter that runs BEFORE any LLM call.

This is the whole cost story: ~2000 raw jobs -> ~40 candidates for ~0 rupees,
so Claude only ever reads jobs that already passed title + location + freshness.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .fetch import Job

REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh", "distributed")
_YEAR_VALUES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_YEAR_TOKEN = r"(?:\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_EXPERIENCE_PATTERNS = (
    re.compile(
        rf"\b(?P<low>{_YEAR_TOKEN})\s*(?:-|–|—|to)\s*"
        rf"(?P<high>{_YEAR_TOKEN})\s*(?:\+?\s*)(?:years?|yrs?)\b"
        r".{0,45}\bexperience\b",
        re.I,
    ),
    re.compile(
        rf"\b(?P<years>{_YEAR_TOKEN})\s*\+\s*(?:years?|yrs?)\b"
        r".{0,45}\bexperience\b",
        re.I,
    ),
    re.compile(
        r"\b(?:minimum(?:\s+of)?|at\s+least|required)\s+"
        rf"(?P<years>{_YEAR_TOKEN})\s*(?:\+?\s*)(?:years?|yrs?)\b"
        r".{0,45}\bexperience\b",
        re.I,
    ),
    re.compile(
        rf"\b(?P<years>{_YEAR_TOKEN})\s+(?:years?|yrs?)\b"
        r".{0,45}\bexperience\b",
        re.I,
    ),
    re.compile(
        r"\bexperience\b.{0,30}\b(?:of\s+|minimum(?:\s+of\s+)?|at\s+least\s+)"
        rf"(?P<years>{_YEAR_TOKEN})\s*\+?\s*(?:years?|yrs?)\b",
        re.I,
    ),
)


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _location_match(locations: list[str], text: str) -> bool:
    """Match complete location names so `india` does not match `Indiana`."""
    return any(
        re.search(rf"(?<!\w){re.escape(location)}(?!\w)", text, re.I)
        for location in locations
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _exceeds_experience_limit(description: str, max_years: int | float) -> bool:
    """Return True when a JD explicitly asks for experience above the limit."""
    for pattern in _EXPERIENCE_PATTERNS:
        for match in pattern.finditer(description or ""):
            raw = (match.groupdict().get("high")
                   or match.groupdict().get("years")
                   or match.groupdict().get("low")
                   or "0").lower()
            required = _YEAR_VALUES.get(raw, int(raw) if raw.isdigit() else 0)
            if required > max_years:
                return True
    return False


def prefilter(jobs: list[Job], cfg: dict) -> list[Job]:
    inc = cfg.get("include_titles") or [r"."]
    exc = cfg.get("exclude_titles") or []
    locs = [l.lower() for l in (cfg.get("locations") or [])]
    allow_remote = bool(cfg.get("allow_remote", True))
    max_age = cfg.get("max_age_days")
    max_experience = cfg.get("max_experience_years")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None

    kept, stats = [], {"title": 0, "location": 0, "experience": 0, "age": 0}
    for j in jobs:
        if not _any_match(inc, j.title) or (exc and _any_match(exc, j.title)):
            stats["title"] += 1
            continue

        if max_experience is not None and _exceeds_experience_limit(
                j.description, float(max_experience)):
            stats["experience"] += 1
            continue

        if locs:
            hay = f"{j.location} {j.title}".lower()
            is_remote = allow_remote and any(h in hay for h in REMOTE_HINTS)
            if not is_remote and not _location_match(locs, hay):
                stats["location"] += 1
                continue

        if cutoff:
            posted = _parse_date(j.posted_at)
            if posted and posted < cutoff:
                stats["age"] += 1
                continue

        kept.append(j)

    print(f"  prefilter: {len(jobs)} -> {len(kept)} "
          f"(dropped title={stats['title']} experience={stats['experience']} "
          f"location={stats['location']} stale={stats['age']})")
    return kept
