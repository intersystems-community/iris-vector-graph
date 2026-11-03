"""Spec 236, Story 5: every Arno claim in the known-issues record names the build it was checked on.

The Arno callout defects section moved with the rest of the 4.5.x record to docs/dev/notes/known-issues-history.md
in 4.6.0 (R027): the 4.6.0 triage could not confirm them on that build, so they are not in docs/KNOWN_ISSUES.md.
"""

from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "dev" / "notes" / "known-issues-history.md"
HEADING = "### Arno callout defects"
BUILD = re.compile(r"arno-callout \d+\.\d+\.\d+\+[0-9a-f]{12}|\d{4}-\d{2}-\d{2} build")
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _section() -> str:
    text = DOC.read_text()
    start = text.index(HEADING)
    end = text.index("\n### ", start + len(HEADING))
    return text[start:end]


def _bullets(section: str) -> list:
    bullets, cur = [], None
    for line in section.splitlines():
        if line.startswith("- "):
            cur = [line]
            bullets.append(cur)
        elif cur is not None and (line.startswith("  ") or not line.strip()):
            cur.append(line)
        else:
            cur = None
    return ["\n".join(b) for b in bullets]


def test_the_section_describes_the_current_build():
    assert "arno-callout 1.1.0+3b2123b8e7bb" in _section()


def test_every_entry_names_the_build_it_was_verified_against_and_a_date():
    bullets = _bullets(_section())
    assert len(bullets) >= 8, "the section lost entries"
    for b in bullets:
        head = b.splitlines()[0][:70]
        assert BUILD.search(b), f"no build named: {head!r}"
        assert DATE.search(b), f"no date: {head!r}"


def test_the_required_findings_are_stated():
    s = _section()
    for needle in (
        "undirected",  # betweenness
        "34,791",  # BFS argument limit
        "x86_64",  # never run on IRIS
        "kg_bfs_compute",
        "set_edge_weight",
        "USER",  # the readers' namespace
    ):
        assert needle in s, needle


def test_an_entry_no_longer_true_is_moved_to_the_ruled_out_part():
    s = _section()
    assert "Ruled out" in s and s.index("Ruled out") > s.index("Open")
