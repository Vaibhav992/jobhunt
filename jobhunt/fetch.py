"""Fetch jobs from public ATS APIs. No auth, no scraping, no ToS risk."""
from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable

import requests

UA = {"User-Agent": "jobhunt/1.0 (personal job search agent)"}
TIMEOUT = 20

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


@dataclass
class Job:
    job_id: str          # stable global id for dedupe: "<ats>:<slug>:<id>"
    ats: str
    company: str
    title: str
    location: str
    url: str
    description: str
    posted_at: str | None = None
    salary: str | None = None
    # Some boards (SmartRecruiters, Workday) omit the JD from their list
    # endpoint. We keep a pointer here and fetch the body *only* for the handful
    # of jobs that survive the free prefilter — see hydrate_descriptions().
    detail_url: str | None = None
    # filled in later by the pipeline
    score: float | None = None
    reason: str | None = None
    draft: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Adapters. Each takes the raw JSON body and returns list[Job].
# Keeping parse separate from HTTP is what makes offline testing possible.
# --------------------------------------------------------------------------

def parse_greenhouse(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        out.append(Job(
            job_id=f"greenhouse:{slug}:{j.get('id')}",
            ats="greenhouse",
            company=company,
            title=(j.get("title") or "").strip(),
            location=loc.strip(),
            url=j.get("absolute_url") or "",
            description=strip_html(j.get("content")),
            posted_at=j.get("updated_at") or j.get("first_published"),
        ))
    return out


def parse_lever(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or []):
        cats = j.get("categories") or {}
        # Lever splits the JD across descriptionPlain + a `lists` array.
        chunks = [j.get("descriptionPlain") or strip_html(j.get("description"))]
        for lst in (j.get("lists") or []):
            chunks.append(str(lst.get("text") or ""))
            chunks.append(strip_html(lst.get("content")))
        chunks.append(j.get("additionalPlain") or strip_html(j.get("additional")))
        ts = j.get("createdAt")
        posted = None
        if isinstance(ts, (int, float)):
            posted = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        out.append(Job(
            job_id=f"lever:{slug}:{j.get('id')}",
            ats="lever",
            company=company,
            title=(j.get("text") or "").strip(),
            location=(cats.get("location") or "").strip(),
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            description="\n\n".join(c for c in chunks if c).strip(),
            posted_at=posted,
            salary=cats.get("commitment"),
        ))
    return out


def parse_ashby(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        salary = None
        summary = comp.get("compensationTierSummary") or comp.get("summaryComponents")
        if isinstance(summary, str):
            salary = summary
        out.append(Job(
            job_id=f"ashby:{slug}:{j.get('id')}",
            ats="ashby",
            company=company,
            title=(j.get("title") or "").strip(),
            location=(j.get("location") or "").strip(),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            description=(j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")) or "").strip(),
            posted_at=j.get("publishedAt"),
            salary=salary,
        ))
    return out


def parse_remotive(slug: str, company: str, body: Any) -> list[Job]:
    """Map Remotive's documented public API and keep requested categories."""
    categories = {part.strip().lower() for part in slug.split("|") if part.strip()}
    out = []
    for j in (body or {}).get("jobs", []):
        category = (j.get("category") or "").strip().lower().replace(" ", "-")
        if categories and category not in categories:
            continue

        required_location = (j.get("candidate_required_location") or "").strip()
        remote_scope = required_location.lower()
        india_eligible = not required_location or any(
            hint in remote_scope
            for hint in ("worldwide", "anywhere", "global", "india", "asia", "apac")
        )
        location = (f"Remote — {required_location or 'Worldwide'}"
                    if india_eligible else required_location)

        out.append(Job(
            job_id=f"remotive:{slug}:{j.get('id')}",
            ats="remotive",
            company=(j.get("company_name") or company or "Remotive").strip(),
            title=(j.get("title") or "").strip(),
            location=location,
            url=j.get("url") or "",
            description=strip_html(j.get("description")),
            posted_at=j.get("publication_date"),
            salary=(j.get("salary") or "").strip() or None,
        ))
    return out


def _workable_location(j: dict) -> str:
    parts: list[str] = []
    for loc in j.get("locations") or []:
        if isinstance(loc, dict):
            chunk = ", ".join(
                p for p in (loc.get("city"), loc.get("region") or loc.get("state"), loc.get("country")) if p
            )
            if chunk:
                parts.append(chunk)
        elif loc:
            parts.append(str(loc))
    if not parts:
        chunk = ", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p)
        if chunk:
            parts.append(chunk)
    loc = " / ".join(parts)
    remote = bool(j.get("telecommuting"))
    nested = j.get("location")
    if isinstance(nested, dict) and nested.get("telecommuting"):
        remote = True
    if remote:
        return f"Remote — {loc}" if loc else "Remote"
    return loc


def parse_workable(slug: str, company: str, body: Any) -> list[Job]:
    """Map Workable's public widget board (`?details=true` includes the JD)."""
    out = []
    board_name = ((body or {}).get("name") or company or slug).strip()
    for j in (body or {}).get("jobs", []):
        shortcode = j.get("shortcode") or j.get("id")
        if not shortcode:
            continue
        desc = j.get("description") or j.get("full_description") or ""
        out.append(Job(
            job_id=f"workable:{slug}:{shortcode}",
            ats="workable",
            company=board_name,
            title=(j.get("title") or "").strip(),
            location=_workable_location(j),
            url=j.get("url") or j.get("application_url") or j.get("shortlink")
                or f"https://apply.workable.com/{slug}/j/{shortcode}/",
            description=strip_html(desc),
            posted_at=j.get("published_on") or j.get("created_at"),
        ))
    return out


def parse_recruitee(slug: str, company: str, body: Any) -> list[Job]:
    """Map Recruitee's public offers JSON."""
    out = []
    for j in (body or {}).get("offers") or []:
        jid = j.get("id") or j.get("slug")
        if not jid:
            continue
        loc = (j.get("location") or "").strip()
        if j.get("remote") or j.get("hybrid"):
            loc = f"Remote — {loc}" if loc else "Remote"
        out.append(Job(
            job_id=f"recruitee:{slug}:{jid}",
            ats="recruitee",
            company=(j.get("company_name") or company or slug).strip(),
            title=(j.get("title") or "").strip(),
            location=loc,
            url=j.get("careers_url") or j.get("url") or "",
            description=strip_html(j.get("description") or j.get("requirements")),
            posted_at=j.get("published_at") or j.get("created_at"),
        ))
    return out


def parse_arbeitnow(slug: str, company: str, body: Any) -> list[Job]:
    """Map Arbeitnow's public job-board API. Slug is a tag/remote filter."""
    filters = {part.strip().lower() for part in slug.split("|") if part.strip()}
    want_remote = "remote" in filters
    keywords = filters - {"remote", "all"}
    items = (body or {}).get("data") if isinstance(body, dict) else body
    out = []
    for j in items or []:
        loc = (j.get("location") or "").strip()
        is_remote = bool(j.get("remote")) or "remote" in loc.lower()
        if want_remote and not is_remote:
            continue
        tags = [str(t).lower() for t in (j.get("tags") or [])]
        hay = " ".join(tags + [(j.get("title") or "").lower(), loc.lower()])
        if keywords and not any(k in hay for k in keywords):
            continue
        jid = j.get("slug") or j.get("url") or j.get("id")
        if not jid:
            continue
        out.append(Job(
            job_id=f"arbeitnow:{slug}:{jid}",
            ats="arbeitnow",
            company=(j.get("company_name") or company or "Arbeitnow").strip(),
            title=(j.get("title") or "").strip(),
            location=f"Remote — {loc}" if is_remote and loc else (loc or ("Remote" if is_remote else "")),
            url=j.get("url") or "",
            description=strip_html(j.get("description")),
            posted_at=j.get("created_at"),
        ))
    return out


ENDPOINTS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse),
    "lever":      ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby":      ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", parse_ashby),
    "remotive":   ("https://remotive.com/api/remote-jobs", parse_remotive),
    "workable":   ("https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", parse_workable),
}


# --------------------------------------------------------------------------
# SmartRecruiters is different: its list endpoint paginates and — crucially —
# omits the job description, so a keyword can't gate on the JD until it is
# hydrated. We bound the fetch server-side to India (many global enterprises
# run their India roles here: Bosch, McDonald's, Visa, ...) and defer the JD to
# hydrate_descriptions(), which only runs for prefilter survivors.
# --------------------------------------------------------------------------

SR_BASE = "https://api.smartrecruiters.com/v1/companies"
SR_COUNTRY = "in"          # ISO-3166 alpha-2; matches the India-focused config
SR_PAGE = 100              # max the API allows per page
SR_MAX_PAGES = 6           # hard cap so one huge employer can't dominate a run


def _sr_location(loc: dict | None) -> str:
    loc = loc or {}
    full = (loc.get("fullLocation") or "").strip()
    if not full:
        parts = [loc.get("city"), loc.get("region"), loc.get("country")]
        full = ", ".join(p for p in (x and str(x).strip() for x in parts) if p)
    if loc.get("remote"):
        return f"Remote — {full}" if full else "Remote"
    if loc.get("hybrid") and full:
        return f"Hybrid — {full}"
    return full


def parse_smartrecruiters(slug: str, company: str, body: Any) -> list[Job]:
    """Map one page of the postings list. Description is filled in later."""
    out = []
    for j in (body or {}).get("content", []):
        jid = j.get("id")
        if not jid:
            continue
        posted = j.get("releasedDate")
        out.append(Job(
            job_id=f"smartrecruiters:{slug}:{jid}",
            ats="smartrecruiters",
            company=((j.get("company") or {}).get("name") or company).strip(),
            title=(j.get("name") or "").strip(),
            location=_sr_location(j.get("location")),
            url=f"https://jobs.smartrecruiters.com/{slug}/{jid}",
            description="",  # hydrated on demand for survivors only
            posted_at=posted,
            detail_url=f"{SR_BASE}/{slug}/postings/{jid}",
        ))
    return out


def parse_smartrecruiters_detail(body: Any) -> str:
    """Pull the JD out of a single-posting detail response."""
    sections = ((body or {}).get("jobAd") or {}).get("sections") or {}
    chunks = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        text = (sections.get(key) or {}).get("text")
        if text:
            chunks.append(strip_html(text))
    return "\n\n".join(c for c in chunks if c).strip()


def fetch_smartrecruiters(slug: str, company: str | None,
                          session: requests.Session) -> list[Job]:
    """Paginate the India-filtered postings list for one SmartRecruiters org."""
    jobs: list[Job] = []
    for page in range(SR_MAX_PAGES):
        try:
            r = session.get(
                f"{SR_BASE}/{slug}/postings",
                params={"country": SR_COUNTRY, "limit": SR_PAGE,
                        "offset": page * SR_PAGE},
                headers=UA, timeout=TIMEOUT,
            )
        except Exception as e:
            print(f"  ! smartrecruiters/{slug} -> {type(e).__name__}: {e}")
            break
        if r.status_code != 200:
            print(f"  ! smartrecruiters/{slug} -> HTTP {r.status_code}")
            break
        body = r.json()
        page_jobs = parse_smartrecruiters(slug, company or slug, body)
        jobs.extend(page_jobs)
        if len(jobs) >= (body.get("totalFound") or 0) or not page_jobs:
            break
    return jobs


def fetch_recruitee(slug: str, company: str | None,
                    session: requests.Session) -> list[Job]:
    """Recruitee hosts the public offers JSON on the company subdomain."""
    url = f"https://{slug}.recruitee.com/api/offers/"
    try:
        r = session.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  ! recruitee/{slug} -> HTTP {r.status_code}")
            return []
        return parse_recruitee(slug, company or slug, r.json())
    except Exception as e:
        print(f"  ! recruitee/{slug} -> {type(e).__name__}: {e}")
        return []


def fetch_arbeitnow(slug: str, company: str | None,
                    session: requests.Session) -> list[Job]:
    """One documented aggregator endpoint; slug filters tags / remote."""
    try:
        r = session.get("https://www.arbeitnow.com/api/job-board-api",
                        headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  ! arbeitnow/{slug} -> HTTP {r.status_code}")
            return []
        return parse_arbeitnow(slug, company or "Arbeitnow", r.json())
    except Exception as e:
        print(f"  ! arbeitnow/{slug} -> {type(e).__name__}: {e}")
        return []


def fetch_board(ats: str, slug: str, company: str | None = None,
                session: requests.Session | None = None) -> list[Job]:
    """Hit one company's public board. Returns [] on any failure (never raises)."""
    sess = session or requests.Session()
    if ats == "smartrecruiters":
        return fetch_smartrecruiters(slug, company, sess)
    if ats == "recruitee":
        return fetch_recruitee(slug, company, sess)
    if ats == "arbeitnow":
        return fetch_arbeitnow(slug, company, sess)
    if ats not in ENDPOINTS:
        raise ValueError(f"unknown ATS: {ats}")
    url_tpl, parser = ENDPOINTS[ats]
    try:
        r = sess.get(url_tpl.format(slug=slug), headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  ! {ats}/{slug} -> HTTP {r.status_code}")
            return []
        return parser(slug, company or slug, r.json())
    except Exception as e:  # dead slug, rate limit, network blip
        print(f"  ! {ats}/{slug} -> {type(e).__name__}: {e}")
        return []


def hydrate_descriptions(jobs: Iterable[Job],
                         session: requests.Session | None = None,
                         workers: int = 8) -> None:
    """Fill in JDs for jobs that carry a detail_url but no description yet.

    Call this AFTER the prefilter so only the surviving handful trigger a
    per-posting fetch — SmartRecruiters can list thousands of roles, but a run
    only ever hydrates the few that already cleared title/location.
    """
    todo = [j for j in jobs if j.detail_url and not j.description]
    if not todo:
        return
    sess = session or requests.Session()

    def _one(j: Job) -> None:
        try:
            r = sess.get(j.detail_url, headers=UA, timeout=TIMEOUT)
            if r.status_code != 200:
                return
            if j.ats == "smartrecruiters":
                j.description = parse_smartrecruiters_detail(r.json())
        except Exception as e:
            print(f"  ! hydrate {j.job_id} -> {type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, todo))
    print(f"  hydrated {sum(1 for j in todo if j.description)}/{len(todo)} descriptions")


def fetch_all(companies: Iterable[dict], workers: int = 12) -> list[Job]:
    """Poll every board concurrently. Order of results is not significant."""
    companies = list(companies)
    session = requests.Session()

    def _one(c: dict) -> list[Job]:
        got = fetch_board(c["ats"], c["slug"], c.get("name"), session=session)
        if got:
            print(f"  {c.get('name') or c['slug']:<28} {len(got):>4} jobs  ({c['ats']})")
        return got

    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for got in pool.map(_one, companies):
            jobs.extend(got)
    return jobs
