"""reparse.py: rebuild a run's metrics from its saved output and artefacts, so a parser fix reaches runs already taken."""

import json

from scripts.bench.alt import reparse as rp


def test_reparse_updates_metrics_from_the_saved_artefacts(tmp_path):
    out = tmp_path / "perf-head-to-head_A_1.out"
    out.mkdir()
    (out / "head_to_head_x.json").write_text(json.dumps({"graphs": {"g": {"engines": {"neo4j_gds": {"degree_ms": 55.2}}}}}))
    (tmp_path / "perf-head-to-head_A_1.txt").write_text("")
    (tmp_path / "perf-head-to-head_A_1.json").write_text(
        json.dumps({"suite": "perf-head-to-head", "side": "A", "rep": 1, "status": "ok", "metrics": {"old": 1.0}})
    )
    changed = rp.reparse(tmp_path)
    doc = json.loads((tmp_path / "perf-head-to-head_A_1.json").read_text())
    assert doc["metrics"]["g.neo4j_gds.degree_ms"] == 55.2
    assert changed == 1
    assert rp.reparse(tmp_path) == 0, "a second pass changes nothing"
