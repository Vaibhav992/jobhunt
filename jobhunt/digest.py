"""Build the daily HTML digest. Inline CSS only — Gmail strips <style> blocks.

The digest is a compact ranked list: every match on one line — score, title,
company, location, a one-sentence reason, and an Apply button. No per-job cover
notes or bullets; screening is the only LLM cost. This is what lets a run show
50+ roles without a token bill or a wall of text you'll never read.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .fetch import Job

BG = "#0f1115"
CARD = "#171a21"
LINE = "#262b36"
TEXT = "#e6e8ec"
MUTED = "#8b93a3"
ACCENT = "#7c9cff"


def _badge(score: float | None) -> str:
    s = score or 0
    color = "#3fb950" if s >= 8.5 else "#d29922" if s >= 7 else "#8b949e"
    return (f'<span style="display:inline-block;min-width:34px;text-align:center;'
            f'background:{color};color:#0f1115;font-weight:700;padding:3px 8px;'
            f'border-radius:8px;font-size:13px;">{s:.1f}</span>')


def _row(j: Job) -> str:
    """One job as a single compact card."""
    meta = " · ".join(x for x in [j.company, j.location or "—", j.ats] if x)
    salary = (f'<span style="color:{MUTED};font-size:12px;"> · {html.escape(j.salary)}</span>'
              if j.salary else "")
    reason = (f'<div style="color:{TEXT};font-size:13px;line-height:1.5;margin-top:6px;">'
              f'{html.escape(j.reason)}</div>' if j.reason else "")

    return f"""
<div style="background:{CARD};border:1px solid {LINE};border-radius:10px;
     padding:14px 16px;margin-bottom:10px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="vertical-align:top;padding-right:12px;width:44px;">{_badge(j.score)}</td>
    <td style="vertical-align:top;">
      <div style="font-size:15px;font-weight:700;color:{TEXT};line-height:1.35;">
        {html.escape(j.title)}</div>
      <div style="color:{MUTED};font-size:12px;margin-top:3px;">{html.escape(meta)}{salary}</div>
      {reason}
    </td>
    <td style="vertical-align:top;text-align:right;padding-left:10px;white-space:nowrap;">
      <a href="{html.escape(j.url)}" style="display:inline-block;background:{ACCENT};
         color:#0f1115;font-weight:700;font-size:13px;text-decoration:none;
         padding:8px 14px;border-radius:8px;">Apply →</a>
    </td>
  </tr></table>
</div>"""


def build(jobs: list[Job], scanned: int, candidates: int, stats: dict) -> tuple[str, str]:
    today = datetime.now().strftime("%d %b %Y")
    subject = (f"{len(jobs)} new job{'s' if len(jobs) != 1 else ''} matched — {today}"
               if jobs else f"No new matches today — {today}")

    if jobs:
        body = "".join(_row(j) for j in jobs)
    else:
        body = (f'<div style="background:{CARD};border:1px solid {LINE};border-radius:12px;'
                f'padding:24px;color:{MUTED};font-size:14px;">Scanned {scanned} postings, '
                f'nothing new cleared the bar this run.</div>')

    html_doc = f"""<!doctype html><html><body style="margin:0;padding:20px;background:{BG};
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:680px;margin:0 auto;">
  <div style="color:{TEXT};font-size:22px;font-weight:800;">Your job digest</div>
  <div style="color:{MUTED};font-size:13px;margin:6px 0 20px 0;">
    {today} · scanned {scanned} postings · {candidates} new after filters ·
    {len(jobs)} in this list<br>
    tracker: {stats.get('tracked', 0)} seen · {stats.get('applied', 0)} applied
  </div>
  {body}
  <div style="color:{MUTED};font-size:11px;line-height:1.6;margin-top:18px;
       border-top:1px solid {LINE};padding-top:14px;">
    Ranked by fit against your profile. Open the JD before applying — the score
    is a filter, not a verdict. You press submit; this agent never does.
  </div>
</div></body></html>"""
    return subject, html_doc


def write(html_doc: str, path: str | Path = "out/digest.html") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
    return path
