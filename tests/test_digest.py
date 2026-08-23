"""The compact digest is list-only: one Apply link per job, a score badge, and
a one-line reason. These lock the subject-line wording and the escaping, since
a broken subject or an unescaped title in an HTML email is invisible until it
lands in your inbox."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt.digest import build
from jobhunt.fetch import Job


def _job(**kw) -> Job:
    base = dict(job_id="greenhouse:x:1", ats="greenhouse", company="Acme",
                title="Software Engineer", location="Bengaluru, India",
                url="https://example.com/apply", description="JD")
    base.update(kw)
    return Job(**base)


def test_subject_pluralises_and_dates():
    one = _job(score=8.0)
    subject, _ = build([one], scanned=100, candidates=1, stats={})
    assert subject.startswith("1 new job matched")   # singular

    two = build([one, _job(job_id="greenhouse:x:2", score=7.0)],
                scanned=100, candidates=2, stats={})[0]
    assert two.startswith("2 new jobs matched")       # plural


def test_empty_list_says_no_matches_and_shows_scan_count():
    subject, doc = build([], scanned=250, candidates=0, stats={})
    assert subject.startswith("No new matches")
    assert "250" in doc


def test_build_emits_one_apply_link_per_job_with_score_and_reason():
    jobs = [_job(score=9.1, reason="Strong Go + distributed systems fit"),
            _job(job_id="greenhouse:x:2", title="Backend Engineer", score=7.4,
                 reason="Java match, hybrid Bengaluru")]
    _, doc = build(jobs, scanned=100, candidates=2, stats={"tracked": 5, "applied": 1})

    assert doc.count('href="https://example.com/apply"') == 2
    assert "9.1" in doc and "7.4" in doc
    assert "Strong Go" in doc
    assert "Backend Engineer" in doc
    assert "5 seen" in doc and "1 applied" in doc      # tracker footer


def test_build_escapes_html_in_untrusted_fields():
    """Titles/reasons come straight from third-party boards. An unescaped
    angle bracket would corrupt the email markup (or worse)."""
    j = _job(title="Engineer <script>", reason="a & b <img>", score=8.0)
    _, doc = build([j], scanned=1, candidates=1, stats={})
    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc
    assert "a &amp; b" in doc
