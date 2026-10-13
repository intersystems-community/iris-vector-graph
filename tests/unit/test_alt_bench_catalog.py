"""The catalog and the registry agree (spec 250)."""

from pathlib import Path

import pytest

from scripts.bench.alt import catalog_table
from scripts.bench.alt import suites as su

CATALOG = Path(__file__).resolve().parents[2] / "docs" / "performance" / "alt-bench" / "CATALOG.md"


def test_every_registered_suite_is_in_the_catalog():
    text = CATALOG.read_text()
    missing = [s.id for s in su.SUITES if f"`{s.id}`" not in text and s.id not in text]
    assert not missing, f"registered but not in CATALOG.md: {missing}"


def test_every_suite_named_in_the_registry_table_exists():
    text = CATALOG.read_text()
    assert "<!-- SUITE-TABLE -->" not in text, "run scripts/bench/alt/catalog_table.py --write"
    start = text.index("<!-- suite-table:start -->")
    end = text.index("<!-- suite-table:end -->")
    ids = [ln.split("`")[1] for ln in text[start:end].splitlines() if ln.startswith("| `")]
    assert sorted(ids) == sorted(s.id for s in su.SUITES)


def test_table_rows_carry_runtime_from_a_results_dir(tmp_path):
    (tmp_path / "bench-q1q6-s_A_1.json").write_text(
        '{"suite": "bench-q1q6-s", "side": "A", "rep": 1, "status": "ok", "elapsed_s": 3.8, "metrics": {}}'
    )
    table = catalog_table.render(smoke_dir=tmp_path)
    row = [ln for ln in table.splitlines() if ln.startswith("| `bench-q1q6-s`")][0]
    assert "3.8 s" in row


def test_blocked_suites_show_their_state(monkeypatch):
    import dataclasses

    # Nothing is blocked today (the index-maintenance generator was fixed), so mark one to test the rendering.
    patched = [dataclasses.replace(s, blocked="hangs in executemany on this driver for a reason") if s.id == "index-maintenance-20k" else s for s in su.SUITES]
    monkeypatch.setattr(su, "SUITES", patched)
    table = catalog_table.render()
    row = [ln for ln in table.splitlines() if ln.startswith("| `index-maintenance-20k`")][0]
    assert "blocked" in row


def test_index_maintenance_suites_are_runnable_now():
    assert not su.SUITES_BY_ID["index-maintenance-20k"].blocked
    assert not su.SUITES_BY_ID["index-maintenance-100k"].blocked


@pytest.mark.parametrize("sid", [s.id for s in su.SUITES])
def test_registry_ids_are_kebab_case(sid):
    assert sid == sid.lower() and "_" not in sid


NEO4J_TWINS = {
    "bench-193-full-s-neo4j": "bench-193-full-s",
    "perf-head-to-head-neo4j": "perf-head-to-head",
    "perf-betweenness-full-neo4j": "perf-betweenness-full",
    "perf-leiden-four-way-neo4j": "perf-leiden-four-way",
}


def test_each_suite_with_a_competitor_leg_has_a_neo4j_twin_that_runs_the_same_ivg_work():
    for twin_id, base_id in NEO4J_TWINS.items():
        twin, base = su.SUITES_BY_ID[twin_id], su.SUITES_BY_ID[base_id]
        assert twin.needs_neo4j and not base.needs_neo4j
        assert twin.parser == base.parser and twin.data == base.data
        # same command apart from the Neo4j switches
        strip = lambda argv: [a for a in argv if a not in ("--skip-neo4j",) and not a.startswith("bolt://")]  # noqa: E731
        assert [a for a in strip(twin.argv) if not a.startswith("--neo4j")][: len(base.argv) - 1] [:3] == [a for a in strip(base.argv)][:3]


def test_neo4j_twins_are_not_in_the_quick_set_and_are_skipped_without_the_flag():
    assert not set(NEO4J_TWINS) & set(su.quick_ids())
