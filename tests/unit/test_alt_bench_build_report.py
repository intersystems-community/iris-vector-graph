"""The report's breadth tables come from result files; these pin the readers on small synthetic result trees."""

import json

from scripts.bench.alt import build_report as br


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_fhir_table_gives_median_per_side_and_ratio(tmp_path):
    for side, vals in (("A", (100.0, 300.0)), ("B", (110.0, 130.0))):
        for rnd, v in enumerate(vals, 1):
            _write(tmp_path / f"fhir_fixture_{side}_{rnd}.json", {"set": "fixture", "sync_ms": v, "coverage_ms": 10.0, "repo_resources": 5})
    out = br.fhir_table(tmp_path)
    row = next(line for line in out.splitlines() if line.startswith("| fixture | sync_ms"))
    assert "200" in row and "120" in row and "0.60" in row
    assert "repo_resources" not in out  # counts are not timings


def test_fhir_table_reports_nothing_when_a_side_is_missing(tmp_path):
    _write(tmp_path / "fhir_fixture_A_1.json", {"set": "fixture", "sync_ms": 1.0})
    assert "sync_ms" not in br.fhir_table(tmp_path)


def test_drkg_rows_pool_every_run_of_a_side(tmp_path):
    for name, side, total in (("breadth-drkg-fresh-1-B", "B", 680000.0), ("breadth-drkg-fresh-2-A", "A", 548000.0), ("breadth-drkg-fresh-3-A", "A", 509000.0), ("breadth-drkg-full", "B", 734000.0)):
        _write(tmp_path / name / f"drkg-load-full_{side}_1.json", {"suite": "drkg-load-full", "side": side, "status": "ok", "metrics": {"total": total, "load_edges": total - 5000}})
    out = br.drkg_table(tmp_path)
    assert "548" in out and "509" in out and "680" in out and "734" in out
    assert "| A | 2 |" in out and "| B | 2 |" in out  # one count per side


def test_migration_table_lists_the_headline_steps_with_both_sides(tmp_path):
    for side, total in (("A", 49000.0), ("B", 45000.0)):
        for rep in (1, 2, 3):
            _write(tmp_path / f"migration-4-0-0_{side}_{rep}.json", {"suite": "migration-4-0-0", "side": side, "rep": rep, "status": "ok",
                   "metrics": {"apply.total": total, "step.embeddings": 10000.0, "profile._plan": 200.0}})
    out = br.migration_table(tmp_path)
    assert "| apply.total | 49.0 | 45.0 | 0.92 |" in out
    assert "step.embeddings" in out
    assert "profile._plan" not in out  # only the headline rows
