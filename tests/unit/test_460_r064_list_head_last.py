"""4.6.0 R064: `LIST_HEAD` and `LIST_LAST` are gone from everything a reader or an operator is told IVG creates.

Called directly (tests/e2e/test_460_r064_list_head_last_e2e.py), the 4.5.1 bodies fail on every non-empty list
(`<COMMAND>` "Function must return a value") and `LIST_LAST('[]')` with `<RANGE>`; the translator answers `head()` and
`last()` inline and names neither. Spec 262 stopped creating them and gave them no replacement in the legacy manifest
(tests/unit/test_legacy_manifest.py). What is checked here is the rest: the inventory (`docs/inventory.json`, which
`scripts/dev/gen_inventory_json.py --check` keeps equal to setup) and the reader docs, ADMIN_GUIDE's inventory tables
among them, name neither except to say they are retired.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RETIRED = ("LIST_HEAD", "LIST_LAST")
#: the docs a reader or operator is sent to; the migration guide names them to say 4.6.0 creates neither
READER_DOCS = ("README.md", "docs/USER_GUIDE.md", "docs/ADMIN_GUIDE.md", "docs/KNOWN_ISSUES.md", "docs/API_REFERENCE.md")


def _names(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _names(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _names(v)
    else:
        yield str(obj)


def test_the_inventory_names_neither():
    inventory = json.loads((REPO / "docs" / "inventory.json").read_text())
    assert [s for s in _names(inventory) if any(n in s for n in RETIRED)] == []


@pytest.mark.parametrize("doc", READER_DOCS)
def test_no_reader_doc_names_either(doc):
    text = (REPO / doc).read_text()
    assert [n for n in RETIRED if n in text] == []


def test_the_migration_guide_says_they_are_retired():
    text = (REPO / "docs" / "migration" / "v4.6.0.md").read_text()
    at = text.index("`LIST_HEAD` and `LIST_LAST`")
    assert "retired" in text[at:at + 120]
