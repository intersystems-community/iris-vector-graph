"""4.6.0: a count the reader docs state about what setup creates is the count setup creates (4.6.0).

ADMIN_GUIDE said setup's 56 routines were "those 55" of the catalog's 107 (the catalog lists 108, the classes project
28, not 29, and IRIS makes 18 `*_Extent` procedures, one per table), and CHANGELOG said 55 routines twice after the
56th landed: each sentence was right when written and went stale with the next routine. Every count below is derived
here from setup's DDL, docs/inventory.json and the read-path rule (tests/helpers/setup_inventory.py,
iris_vector_graph._engine.legacy), never written down, so a doc that states another number fails until it says the
new one.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

from tests.helpers.setup_inventory import INVENTORY, inventory_tables, setup_routine_names

REPO = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def _counts() -> dict:
    from iris_vector_graph._engine.legacy import read_path_routines

    ddl = {n.lower() for n in setup_routine_names()}
    catalog = [n for e in INVENTORY["entries"] if e["kind"] in ("function", "procedure") and e["present"] == "setup"
               for n in e["names"]]
    extent = [n for n in catalog if n.endswith("_Extent")]
    ifind = [n for n in catalog if "ifind" in n.lower()]
    projected = [n for n in catalog if n.lower() not in ddl and n not in extent and n not in ifind]
    return {
        "routines": len(ddl),
        "tables": len(inventory_tables()),
        "catalog": len(catalog),
        "extent": len(extent),
        "ifind": len(ifind),
        "projected": len(projected),
        "cypherfn": sum(n.startswith("CypherFn_") for n in projected),
        "read_path": len(read_path_routines()),
    }


#: phrase (whitespace-normalised, one number captured) -> the count it states
PHRASES = {
    r"\b(\d+) routines\b": "routines",
    r"counts the (\d+) functions and procedures setup creates": "routines",
    r"as the inventory does: those (\d+);": "routines",
    r"\b(\d+) tables, \d+ routines": "tables",
    r"`INFORMATION_SCHEMA\.ROUTINES` lists (\d+) in the IVG Schema": "catalog",
    r"(\d+) that IVG's classes project": "projected",
    r"each of its (\d+) procedures answers": "projected",
    r"the (\d+) `CypherFn_\*`": "cypherfn",
    r"(\d+) that IRIS makes for the two iFind indexes": "ifind",
    r"(\d+) `\*_Extent` procedures": "extent",
    r"the (\d+) functions that read nothing outside": "read_path",
}


def _docs() -> dict:
    """The docs a 4.6.0 reader meets: README, the top-level docs, docs/setup, the 4.6.0 migration guide and the
    CHANGELOG's 4.6.0 section (older sections and migration guides state their own releases' counts)."""
    paths = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md")), *sorted((REPO / "docs" / "setup").glob("*.md")),
             REPO / "docs" / "migration" / "v4.6.0.md"]
    out = {str(p.relative_to(REPO)): p.read_text() for p in paths}
    changelog = (REPO / "CHANGELOG.md").read_text()
    out["CHANGELOG.md (v4.6.0)"] = changelog[changelog.index("### v4.6.0"):changelog.index("### v4.5.1")]
    return out


def _stated() -> list:
    found = []
    for doc, text in _docs().items():
        flat = re.sub(r"\s+", " ", text)
        for phrase, what in PHRASES.items():
            for m in re.finditer(phrase, flat):
                found.append((doc, what, int(m.group(1)), flat[max(0, m.start() - 40):m.end() + 20]))
    return found


def test_the_counts_are_derived_not_written_down():
    c = _counts()
    # the parts add up to the catalog, and one *_Extent procedure per table
    assert c["routines"] + c["projected"] + c["ifind"] + c["extent"] == c["catalog"], c
    assert c["extent"] == c["tables"], c


def test_the_docs_state_counts_this_test_reads():
    # a phrase list that matches nothing would check nothing
    assert {w for _, w, _, _ in _stated()} >= {"routines", "tables", "catalog", "projected", "extent"}


@pytest.mark.parametrize("doc, what, number, context", _stated(), ids=lambda v: v if isinstance(v, str) else None)
def test_a_stated_count_is_the_count(doc, what, number, context):
    assert number == _counts()[what], f"{doc} says {number} for {what}, setup has {_counts()[what]}: …{context}…"
