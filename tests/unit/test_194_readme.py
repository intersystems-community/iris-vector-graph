"""
Tests for Spec 194: README rewrite acceptance criteria.
All tests are pure file I/O — no IRIS connection required.
"""
import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
README = os.path.join(REPO_ROOT, "README.md")
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")


def _readme_lines():
    with open(README, encoding="utf-8") as f:
        return f.readlines()


def _readme_text():
    with open(README, encoding="utf-8") as f:
        return f.read()


def _changelog_text():
    with open(CHANGELOG, encoding="utf-8") as f:
        return f.read()


# T194-01
def test_readme_line_count():
    lines = _readme_lines()
    assert len(lines) <= 350, f"README.md is {len(lines)} lines — must be ≤ 350"


# T194-02
def test_changelog_exists_with_entries():
    assert os.path.exists(CHANGELOG), "CHANGELOG.md does not exist"
    count = len(re.findall(r"^### v", _changelog_text(), re.MULTILINE))
    assert count >= 50, f"CHANGELOG.md has only {count} version entries — expected ≥ 50"


# T194-03
def test_single_getting_started_heading():
    text = _readme_text()
    headings = re.findall(r"^## Getting Started", text, re.MULTILINE)
    assert len(headings) == 1, (
        f"Expected exactly 1 '## Getting Started' heading, found {len(headings)}"
    )


# T194-04
def test_doc_links_resolve():
    text = _readme_text()
    # Find all relative markdown links [text](path) — skip http(s) and anchors
    links = re.findall(r"\[.*?\]\(([^)]+)\)", text)
    broken = []
    for link in links:
        if link.startswith(("http://", "https://", "#")):
            continue
        # Strip any anchor fragment
        path = link.split("#")[0]
        if not path:
            continue
        full = os.path.normpath(os.path.join(REPO_ROOT, path))
        if not os.path.exists(full):
            broken.append(link)
    assert not broken, f"Broken doc links in README: {broken}"


# T194-05
def test_dropped_sections_absent():
    text = _readme_text()
    dropped = ["Interactive Demo", "Compliance", "HIPAA", "## Quick Start"]
    found = [s for s in dropped if s in text]
    assert not found, f"Dropped sections still present in README: {found}"


# T194-06
def test_spec193_results_present():
    text = _readme_text()
    assert "NKG fast-path" in text, (
        "README must mention 'NKG fast-path' (Spec 193 results)"
    )


# T194-07 phase gate: run manually — tests above must FAIL before rewrite, PASS after
