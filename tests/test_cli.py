from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.cli import _load_companies


def test_load_companies_accepts_top_level_list(tmp_path: Path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "- {ats: greenhouse, slug: example, name: Example}\n",
        encoding="utf-8",
    )

    assert _load_companies(path) == [
        {"ats": "greenhouse", "slug": "example", "name": "Example"}
    ]


def test_load_companies_accepts_wrapped_list(tmp_path: Path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "companies:\n  - {ats: lever, slug: example}\n",
        encoding="utf-8",
    )

    assert _load_companies(path) == [{"ats": "lever", "slug": "example"}]


def test_load_companies_rejects_invalid_entry(tmp_path: Path):
    path = tmp_path / "companies.yaml"
    path.write_text("- {name: Missing identifiers}\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="'ats' and 'slug' are required"):
        _load_companies(path)
