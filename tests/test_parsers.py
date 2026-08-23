"""Parsers + prefilter, run against the fixtures in their native ATS shapes.

No network, no API key. This is the suite that catches the two bugs that cost
me an evening each: Lever's epoch-milliseconds timestamps, and a bare `sde`
regex that silently matches nothing.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt import mock
from jobhunt.fetch import (
    Job,
    _sr_location,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
    parse_arbeitnow,
    parse_recruitee,
    parse_remotive,
    parse_smartrecruiters,
    parse_smartrecruiters_detail,
    parse_workable,
    strip_html,
)
from jobhunt.mock import fetch_all_mock
from jobhunt.prefilter import prefilter

CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml")
                        .read_text(encoding="utf-8"))
FILTERS = CONFIG["filters"]


# ------------------------------------------------------------- strip_html ---

def test_strip_html_unescapes_twice():
    """Greenhouse ships HTML-entity-escaped HTML: unescape, strip, unescape."""
    raw = "&lt;p&gt;Go &amp;amp; Java&lt;/p&gt;"
    assert strip_html(raw) == "Go & Java"


def test_strip_html_turns_block_tags_into_newlines():
    out = strip_html("<p>One</p><p>Two</p><ul><li>a</li><li>b</li></ul>")
    assert "One" in out and "Two" in out and "a" in out and "b" in out
    assert "<" not in out


def test_strip_html_handles_none_and_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""


# ---------------------------------------------------------------- parsers ---

def test_greenhouse_maps_every_field():
    jobs = parse_greenhouse("acme-edge", "Acme Edge", mock.GREENHOUSE["acme-edge"])
    j = next(j for j in jobs if j.title.startswith("Software Engineer II"))
    assert j.job_id == "greenhouse:acme-edge:5501001"
    assert j.ats == "greenhouse"
    assert j.company == "Acme Edge"
    assert j.location == "Bangalore, India"
    assert j.url.startswith("https://boards.greenhouse.io/")
    assert "distributed services" in j.description


def test_lever_concatenates_description_lists_and_additional():
    """The requirements live in lists[], not descriptionPlain. Drop the
    concatenation and every Lever job looks unqualified."""
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert "market data pipeline" in j.description      # descriptionPlain
    assert "Requirements" in j.description              # lists[].text
    assert "2-5 years backend experience" in j.description  # lists[].content
    assert "No take-home" in j.description              # additionalPlain


def test_lever_createdAt_is_epoch_milliseconds():
    """1.7e12 is milliseconds. Reading it as seconds dates the post to 1970
    and the freshness filter eats the whole board without a word."""
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert j.posted_at == two_days_ago.isoformat()


def test_ashby_skips_unlisted_drafts():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    assert all("unlisted" not in j.url for j in jobs)
    assert len(jobs) == 2   # 3 postings, one isListed: false


def test_ashby_reads_compensation_and_html_fallback():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    networking = next(j for j in jobs if j.title == "Software Engineer, Networking")
    assert networking.salary == "₹32L – ₹48L"
    ds = next(j for j in jobs if j.title == "Data Scientist, Growth")
    assert "Causal inference" in ds.description   # descriptionHtml fallback


def test_remotive_filters_categories_and_maps_india_eligible_remote_job():
    body = {"jobs": [
        {
            "id": 101,
            "title": "Backend Engineer",
            "company_name": "Remote Co",
            "category": "Software Development",
            "candidate_required_location": "Asia, Worldwide",
            "url": "https://remotive.com/jobs/101",
            "description": "<p>Build Java APIs</p>",
            "publication_date": "2026-08-09T12:00:00",
            "salary": "$30k-$50k",
        },
        {
            "id": 102,
            "title": "Account Executive",
            "company_name": "Remote Co",
            "category": "Sales",
            "candidate_required_location": "Worldwide",
        },
    ]}

    jobs = parse_remotive("software-development|devops", "Remotive", body)

    assert len(jobs) == 1
    assert jobs[0].job_id == "remotive:software-development|devops:101"
    assert jobs[0].company == "Remote Co"
    assert jobs[0].location == "Remote — Asia, Worldwide"
    assert jobs[0].description == "Build Java APIs"


def test_remotive_does_not_mark_usa_only_role_as_globally_remote():
    body = {"jobs": [{
        "id": 103,
        "title": "Software Engineer",
        "company_name": "US Co",
        "category": "Software Development",
        "candidate_required_location": "USA only",
    }]}

    job = parse_remotive("software-development", "Remotive", body)[0]

    assert job.location == "USA only"


# ------------------------------------------------------ smartrecruiters ---

def test_smartrecruiters_list_defers_the_description_and_keeps_a_detail_url():
    """The list endpoint has no JD. The parser must set detail_url (so hydrate
    can fetch it later) and leave description empty — not invent one."""
    jobs = parse_smartrecruiters("globaltech", "GlobalTech",
                                 mock.SMARTRECRUITERS["globaltech"])
    j = next(j for j in jobs if j.title == "Software Engineer, Cloud Platform")
    assert j.job_id == "smartrecruiters:globaltech:743999000000001"
    assert j.ats == "smartrecruiters"
    assert j.company == "GlobalTech"
    assert j.location == "Bengaluru, Karnataka, India"   # city/region/country join
    assert j.description == ""                            # not hydrated yet
    assert j.detail_url.endswith("/globaltech/postings/743999000000001")


def test_smartrecruiters_location_variants():
    assert _sr_location({"fullLocation": "Berlin, Germany"}) == "Berlin, Germany"
    assert _sr_location({"city": "Pune", "country": "India"}) == "Pune, India"
    assert _sr_location({"fullLocation": "Bengaluru", "remote": True}) == "Remote — Bengaluru"
    assert _sr_location({"city": "Chennai", "hybrid": True}) == "Hybrid — Chennai"
    assert _sr_location({}) == ""
    assert _sr_location(None) == ""


def test_smartrecruiters_detail_concatenates_sections_and_strips_html():
    jd = parse_smartrecruiters_detail(mock.SMARTRECRUITERS_DETAIL["743999000000001"])
    assert "control plane" in jd            # jobDescription
    assert "1-2 years of backend" in jd     # qualifications
    assert "Bengaluru office" in jd         # additionalInformation
    assert "<" not in jd                    # html stripped
    # order preserved: description, then qualifications, then additional
    assert jd.index("control plane") < jd.index("1-2 years") < jd.index("Bengaluru office")


def test_smartrecruiters_detail_tolerates_missing_sections():
    assert parse_smartrecruiters_detail({}) == ""
    assert parse_smartrecruiters_detail({"jobAd": {"sections": {}}}) == ""


def test_workable_maps_remote_india_role_and_strips_html():
    body = {"name": "Acme", "jobs": [{
        "shortcode": "AB12",
        "title": "Backend Engineer",
        "city": "Bengaluru",
        "country": "India",
        "telecommuting": True,
        "url": "https://apply.workable.com/acme/j/AB12/",
        "published_on": "2026-08-20",
        "description": "<p>Build Java APIs</p>",
    }]}
    jobs = parse_workable("acme", "Fallback", body)
    assert len(jobs) == 1
    assert jobs[0].job_id == "workable:acme:AB12"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Remote — Bengaluru, India"
    assert jobs[0].description == "Build Java APIs"


def test_recruitee_marks_remote_and_uses_careers_url():
    body = {"offers": [{
        "id": 9,
        "title": "Software Engineer",
        "company_name": "Recruitee Co",
        "location": "Pune",
        "remote": True,
        "careers_url": "https://example.recruitee.com/o/9",
        "description": "<p>Spring Boot</p>",
        "published_at": "2026-08-21",
    }]}
    job = parse_recruitee("example", "Fallback", body)[0]
    assert job.job_id == "recruitee:example:9"
    assert job.location == "Remote — Pune"
    assert job.url.endswith("/o/9")
    assert job.description == "Spring Boot"


def test_arbeitnow_slug_keeps_remote_software_roles_only():
    body = {"data": [
        {"slug": "a", "title": "Backend Engineer", "company_name": "A",
         "location": "Berlin", "remote": True, "tags": ["software"],
         "url": "https://www.arbeitnow.com/a", "description": "Go",
         "created_at": "2026-08-20"},
        {"slug": "b", "title": "Nurse", "company_name": "B",
         "location": "Berlin", "remote": True, "tags": ["healthcare"],
         "url": "https://www.arbeitnow.com/b"},
        {"slug": "c", "title": "Backend Engineer", "company_name": "C",
         "location": "Berlin", "remote": False, "tags": ["software"],
         "url": "https://www.arbeitnow.com/c"},
    ]}
    jobs = parse_arbeitnow("remote|software", "Arbeitnow", body)
    assert [j.job_id for j in jobs] == ["arbeitnow:remote|software:a"]
    assert jobs[0].location.startswith("Remote")


def test_job_ids_are_globally_unique_and_namespaced():
    jobs = fetch_all_mock()
    ids = [j.job_id for j in jobs]
    assert len(ids) == len(set(ids))
    assert all(re.match(r"^(greenhouse|lever|ashby|smartrecruiters):[^:]+:.+$", i)
               for i in ids)


def test_parsers_take_decoded_json_not_a_response():
    """Parsers are pure: body in, list[Job] out. That is what makes --mock
    exercise the real code path instead of a second implementation."""
    assert parse_greenhouse("x", "X", {}) == []
    assert parse_lever("x", "X", []) == []
    assert parse_ashby("x", "X", {}) == []


# -------------------------------------------------------------- prefilter ---

@pytest.mark.parametrize("title", [
    "Software Engineer II, Distributed Systems",
    "Software Development Engineer, Core Infra",
    "Backend Engineer (Go)",
    "Site Reliability Engineer",
    "SDE II",
    "Software Developer",
    "Fresher Software Developer",
    "Java Developer - Fresher",
    "Graduate Engineer Trainee",
    "Software Trainee",
])
def test_include_titles_match_real_titles(title):
    inc = FILTERS["include_titles"]
    assert any(re.search(p, title, re.I) for p in inc), title


def test_bare_sde_regex_does_not_match_the_spelled_out_title():
    """The bug: `sde` looks like it covers "Software Development Engineer".
    It does not — they share no substring. \\bsde\\b plus the spelled-out
    variant is why both titles survive the filter."""
    assert not re.search(r"\bsde\b", "Software Development Engineer", re.I)
    assert re.search(r"\bsde\b", "SDE II", re.I)
    inc = FILTERS["include_titles"]
    assert any(re.search(p, "Software Development Engineer, Core Infra", re.I) for p in inc)


@pytest.mark.parametrize("title", [
    "Staff Software Engineer, Storage",       # too senior
    "Engineering Manager, Platform",          # management track
    "Enterprise Account Executive",           # wrong function
    "Frontend Engineer, Design Systems",      # wrong discipline
    "Data Scientist, Growth",                 # wrong discipline
    "Software Engineer Intern",               # internship, not fresher
])
def test_junk_titles_are_rejected(title):
    inc, exc = FILTERS["include_titles"], FILTERS["exclude_titles"]
    included = any(re.search(p, title, re.I) for p in inc)
    excluded = any(re.search(p, title, re.I) for p in exc)
    assert excluded or not included, f"{title!r} would have survived"


def test_full_mock_funnel_keeps_only_matching_entry_level_roles():
    kept = prefilter(fetch_all_mock(), FILTERS)
    titles = sorted(j.title for j in kept)
    assert titles == [
        "Site Reliability Engineer",
        "Software Development Engineer, Core Infra",
        "Software Engineer II, Distributed Systems",
        "Software Engineer, Cloud Platform",      # SmartRecruiters, JD not yet hydrated
        "Software Engineer, Networking",
    ]


def test_stale_posting_is_dropped_by_freshness_gate():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("Senior Software Engineer, Platform" == j.title for j in kept)


def test_wrong_city_dropped_but_remote_kept():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("San Francisco" in (j.location or "") for j in kept)
    assert any("Remote" in (j.location or "") for j in kept)


def test_allow_remote_is_what_lets_an_out_of_region_remote_role_through():
    """"Remote (India)" already matches the `india` location, so it is the
    wrong fixture for this. Use a remote role that names no allowed city."""
    remote = Job(job_id="lever:x:1", ats="lever", company="X",
                 title="Backend Engineer", location="Remote - Global",
                 url="https://example.com", description="Go")

    kept_on = prefilter([remote], dict(FILTERS, allow_remote=True))
    kept_off = prefilter([remote], dict(FILTERS, allow_remote=False))

    assert len(kept_on) == 1
    assert kept_off == []


def test_india_city_without_country_name_passes_location_gate():
    job = Job(job_id="x:1", ats="x", company="X", title="Software Engineer",
              location="Pune, Maharashtra", url="", description="")

    assert prefilter([job], FILTERS) == [job]


def test_indiana_does_not_match_india_location_filter():
    job = Job(job_id="x:1", ats="x", company="X", title="Software Engineer",
              location="Indiana, United States", url="", description="")

    assert prefilter([job], FILTERS) == []


def test_remotive_usa_only_role_does_not_pass_as_remote():
    body = {"jobs": [{
        "id": 104,
        "title": "Software Engineer",
        "company_name": "US Co",
        "category": "Software Development",
        "candidate_required_location": "USA only",
        "description": "Requires 1 year of experience.",
    }]}
    job = parse_remotive("software-development", "Remotive", body)[0]

    assert prefilter([job], FILTERS) == []


@pytest.mark.parametrize("requirement", [
    "Candidates should have 3+ years of backend experience.",
    "Requires 3 years of professional software development experience.",
    "You have 2-5 years of relevant experience.",
    "Experience of at least 4 years building APIs.",
    "Minimum three years of relevant engineering experience.",
    "Requires 4+ yrs of backend experience.",
])
def test_experience_gate_rejects_requirements_above_two_years(requirement):
    job = Job(job_id="x:1", ats="x", company="X", title="Software Engineer",
              location="India", url="", description=requirement)

    assert prefilter([job], {"max_experience_years": 2}) == []


@pytest.mark.parametrize("requirement", [
    "Open to candidates with 0-2 years of experience.",
    "Requires 2 years of relevant backend experience.",
    "Strong Java and Spring Boot skills.",
])
def test_experience_gate_keeps_zero_to_two_year_roles(requirement):
    job = Job(job_id="x:1", ats="x", company="X", title="Software Engineer",
              location="India", url="", description=requirement)

    assert prefilter([job], {"max_experience_years": 2}) == [job]


def test_empty_filters_keep_everything():
    jobs = fetch_all_mock()
    assert len(prefilter(jobs, {})) == len(jobs)
