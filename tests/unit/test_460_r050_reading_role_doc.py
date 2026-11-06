"""4.6.0 R050 (expert walkthrough W1): ADMIN_GUIDE §3's role rows name the routines database, and the role script IVG
ships agrees with them.

On a namespace whose routines are in a database of their own, the reading row (globals database at `R`, nothing on the
routines database) got `<PROTECT>` from PageRank. Every row now names `<routines database resource>:R`, and the bullet
under the table says what it is and which reads need it. `scripts/setup_security.sql` granted 3.x's tables in SQLUser;
it now makes the rows' SQL grants and lists their resources. Live: tests/e2e/test_460_r202_reading_role_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "ADMIN_GUIDE.md"
SCRIPT = REPO / "scripts" / "setup_security.sql"


def rows() -> dict:
    """{label: (resources, SQL statements)} of the §3 privileges table."""
    out = {}
    for line in GUIDE.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] in ("reading", "reading, fully", "writing"):
            out[cells[0]] = (re.findall(r"`([^`]+:[RWU]+)`", cells[1]), re.findall(r"`(GRANT [^`]+)`", cells[2]))
    return out


@pytest.mark.parametrize("label", ["reading", "reading, fully", "writing"])
def test_every_row_names_the_routines_database_at_r(label):
    resources, _ = rows()[label]
    assert "<routines database resource>:R" in resources
    assert not any(r.startswith("<routines database resource>:") and r != "<routines database resource>:R"
                   for r in resources), "no row writes the routines database"


def test_the_guide_says_what_the_routines_database_resource_is_and_what_needs_it():
    text = " ".join(GUIDE.read_text().split())
    assert "`<routines database resource>` is the resource of the namespace's routines database" in text
    assert "every IVG class method the engine calls over the Native API runs code from the routines database" in text


def _script_grants(role: str) -> list:
    sql = re.sub(r"--[^\n]*", "", SCRIPT.read_text())
    return [" ".join(s.split()) for s in sql.split(";") if f"TO {role}" in s]


def _script_resources(role: str) -> list:
    (line,) = [ln for ln in SCRIPT.read_text().splitlines() if ln.startswith(f"--   {role}:")]
    return [r.strip() for r in line.split(":", 1)[1].split(",")]


@pytest.mark.parametrize("role, label", [("graph_reader", "reading"), ("graph_writer", "writing")])
def test_the_role_script_makes_the_rows_grants_and_lists_their_resources(role, label):
    resources, statements = rows()[label]
    want = [s.replace(" TO role", f" TO {role}") for s in statements]
    if label == "writing":  # "the same, and ...": the reading row's grants first
        want = [s.replace(" TO role", f" TO {role}") for s in rows()["reading"][1]] + want
    assert _script_grants(role) == want
    assert _script_resources(role) == resources
