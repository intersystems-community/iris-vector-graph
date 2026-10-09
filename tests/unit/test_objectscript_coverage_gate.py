"""The ObjectScript line-coverage gate: merge arithmetic, the floor checks, and the floor file itself.

The gate runs against a live container (scripts/line_coverage/run_gate.sh, a pre-release step);
what is checked here needs no IRIS: the merge and check logic on hand-written dumps, and that the
committed floor file still describes the classes the repo ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.line_coverage import linecov

ROOT = Path(__file__).resolve().parents[2]


def dump(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text("".join(f"L|{r}|{n}|{c}|{t}\n" for r, n, c, t in rows) + "DONE\n")
    return linecov.load(path)


def floor(**kw):
    base = {"adjusted_percent_min": 50.0, "excluded": {}, "allow_zero": {}, "expected_routines": []}
    base.update(kw)
    return base


# ---------------------------------------------------------------- what counts as a line


@pytest.mark.parametrize(
    "text,counts",
    [
        ("\tSet x = 1", True),
        ("\tIf a { Quit }", True),
        ("", False),
        (" ;Generated for class A.", False),
        ("\t// a comment", False),
        ("\t}", False),
        ("CreateWebApp(path=\"/kg\") methodimpl {", False),
        ("%0dsqlA\tn %xxsql", False),
        ('s %xxsql("S",1)="DELETE"', False),
        ("if $g(%c10418413)'=\"\" { s SQLCODE=-101 }", False),
        ("\tq", False),
    ],
)
def test_adjusted_lines(text, counts):
    assert linecov.executable(text, adjusted=True) is counts


def test_raw_counts_the_generated_lines_adjusted_leaves_out():
    assert linecov.executable("CreateWebApp() methodimpl {", adjusted=False)
    assert not linecov.executable(" ;comment", adjusted=False)


# ---------------------------------------------------------------------------- the merge


def test_a_line_is_covered_when_any_dump_executed_it(tmp_path):
    a = dump(tmp_path, "a", [("A.1", 1, 0, "Set x=1"), ("A.1", 2, 0, "Set y=2")])
    b = dump(tmp_path, "b", [("A.1", 1, 3, "Set x=1"), ("A.1", 2, 0, "Set y=2")])
    assert linecov.merge([a, b])["A.1"] == (1, 2)


def test_the_same_line_number_with_different_text_is_a_different_line(tmp_path):
    old = dump(tmp_path, "old", [("A.1", 1, 5, "Set x=1")])
    new = dump(tmp_path, "new", [("A.1", 1, 0, "Set x=2")])
    assert linecov.merge([old, new])["A.1"] == (1, 2)


def test_skipped_routines_are_left_out(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("B.1", 1, 0, "Set y=1")])
    assert set(linecov.merge([d], skip=["B.1"])) == {"A.1"}


def test_overall_percentage(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("A.1", 2, 0, "Set y=1"), ("A.1", 3, 1, "Set z=1"), ("A.1", 4, 1, "Set w=1")])
    covered, total, pct = linecov.overall(linecov.merge([d]))
    assert (covered, total, pct) == (3, 4, 75.0)


# ---------------------------------------------------------------------------- the floor


def test_passes_at_or_above_the_floor(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("A.1", 2, 0, "Set y=1")])
    ok, messages, pct = linecov.check([d], floor(adjusted_percent_min=50.0))
    assert ok and pct == 50.0 and messages == []


def test_fails_under_the_floor(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("A.1", 2, 0, "Set y=1")])
    ok, messages, _ = linecov.check([d], floor(adjusted_percent_min=51.0))
    assert not ok and "under the floor" in messages[0]


def test_a_class_with_no_executed_line_fails_unless_it_is_allowed(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("B.1", 1, 0, "Set y=1")])
    ok, messages, _ = linecov.check([d], floor(adjusted_percent_min=0))
    assert not ok and "B.1" in messages[0]
    ok, messages, _ = linecov.check([d], floor(adjusted_percent_min=0, allow_zero={"B.1": "needs a web gateway"}))
    assert ok


def test_an_allow_zero_entry_that_gained_coverage_must_be_removed(tmp_path):
    d = dump(tmp_path, "d", [("B.1", 1, 4, "Set y=1")])
    ok, messages, _ = linecov.check([d], floor(adjusted_percent_min=0, allow_zero={"B.1": "old reason"}))
    assert not ok and "remove them" in messages[0]


def test_an_expected_class_that_no_dump_measured_fails(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1")])
    ok, messages, _ = linecov.check([d], floor(adjusted_percent_min=0, expected_routines=["A.1", "C.1"]))
    assert not ok and "C.1" in messages[0]


def test_excluded_classes_are_neither_counted_nor_required(tmp_path):
    d = dump(tmp_path, "d", [("A.1", 1, 1, "Set x=1"), ("G.1", 1, 0, "Set g=1")])
    ok, _, pct = linecov.check([d], floor(adjusted_percent_min=100, excluded={"G.1": "generated"}, expected_routines=["A.1", "G.1"]))
    assert ok and pct == 100.0


# ------------------------------------------------------------------- the committed floor


def test_the_floor_file_names_every_class_the_repo_ships():
    committed = linecov.load_floor()
    shipped = linecov.routine_names()
    assert sorted(committed["expected_routines"]) == shipped, (
        "iris_src/src gained or lost a class: update expected_routines in docs/objectscript-coverage-floor.json "
        "and say in allow_zero why the class has no coverage, or add a test for it"
    )


def test_every_exception_in_the_floor_file_has_a_reason_and_is_a_shipped_class():
    committed = linecov.load_floor()
    shipped = set(linecov.routine_names())
    for key in ("excluded", "allow_zero"):
        for routine, reason in committed[key].items():
            assert routine in shipped, f"{key}: {routine} is not a shipped class"
            assert len(reason.split()) >= 3, f"{key}: {routine} needs a reason"
    assert not set(committed["excluded"]) & set(committed["allow_zero"])


def test_the_floor_is_a_sane_percentage():
    committed = linecov.load_floor()
    assert 50 <= committed["adjusted_percent_min"] <= 100
    json.dumps(committed)  # serialisable
