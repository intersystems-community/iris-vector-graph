"""Spec 253: a native query split across worker processes (a label scan, a variable-length hop's ends, or the first
hop's neighbours) answers as the SQL path does.

The family B fixture and cases, run with `native_parallel_min = 0` so every query that can be split takes the parallel
path, in shadow mode: each answer is held equal to the SQL path's, and the split ones must have run in workers.

Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import re

import pytest

from tests.e2e.test_253_family_b_e2e import CASES, engine, n  # noqa: F401  (engine is the module's fixture)


def _trailing_variable_length(cypher):
    """An aggregate whose one hop is variable-length: nothing after the hop to share out."""
    return "count(" in cypher and cypher.count("-[") == 1 and re.search(r"-\[[^\]]*\*", cypher) is not None


# aggregates that start from a label scan and walk at least one hop (a scan with no hop has no work to split)
LABEL_SCAN_AGGREGATES = [
    c for c in CASES if "count(" in c[1] and "$id" not in c[1] and "-[" in c[1] and not _trailing_variable_length(c[1])
]
# anchored aggregates whose only hop is variable-length: an end costs only its checks and a count, while every worker
# walks the hop again, so the ends are not shared out (DRKG's D_Q2: 1.9 s split against 0.9 in one process, whose
# walk splits its own levels instead, VarLength.LastLevel)
ENDS_AGGREGATES = [c for c in CASES if _trailing_variable_length(c[1])]
# anchored projections (no aggregate): the first hop's neighbours, or a variable-length hop's ends, are shared out
# and the workers' rows (each worker's own LIMIT) merged in order, DISTINCT across workers
PROJECTIONS = [c for c in CASES if "count(" not in c[1] and "WHERE p.sid = $id" in c[1] and "-[" in c[1]]


@pytest.fixture
def parallel(engine):  # noqa: F811
    saved = engine.native_parallel_min
    engine.native_parallel_min = 0
    yield engine
    engine.native_parallel_min = saved


@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_parallel_native_equals_sql(parallel, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = parallel.__dict__.get("_native_answers", 0)
    r = parallel.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert parallel.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"


@pytest.mark.parametrize("name,cypher,params", LABEL_SCAN_AGGREGATES, ids=[c[0] for c in LABEL_SCAN_AGGREGATES])
def test_a_label_scan_aggregate_runs_in_workers(parallel, name, cypher, params):
    before = parallel.__dict__.get("_native_parallel_runs", 0)
    r = parallel.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert parallel.__dict__.get("_native_parallel_runs", 0) == before + 1, "the scan did not run in workers"


@pytest.mark.parametrize("name,cypher,params", ENDS_AGGREGATES, ids=[c[0] for c in ENDS_AGGREGATES])
def test_a_trailing_variable_length_aggregate_keeps_its_ends_in_one_process(parallel, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = parallel.__dict__.get("_native_parallel_runs", 0)
    r = parallel.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert parallel.__dict__.get("_native_parallel_runs", 0) == before, "the ends were shared out"


def test_ends_shared_by_two_anchors_are_counted_once(parallel):
    """Two anchors reach e and f at different positions of their ends, so in different workers' shares: the workers'
    count(DISTINCT) sets overlap and are merged as sets, not added (8 would be the double count). Each end has a hop
    after it (to z), which is what makes the ends worth sharing out."""
    label = f"Ov{n('x')}"
    nodes = [f"{label}_{k}" for k in "abcdefghz"]
    for k, node in zip("abcdefghz", nodes):
        parallel.create_node(node, labels=[label], properties={"sid": node, "firstName": "Ann" if k in "ab" else "X"})
    for x, y in ("ac", "ad", "ae", "af", "bf", "be", "bg", "bh"):
        parallel.create_edge(f"{label}_{x}", "OVK", f"{label}_{y}")
    for x in "cdefgh":
        parallel.create_edge(f"{label}_{x}", "OVZ", f"{label}_z")
    try:
        q = f"MATCH (p:{label})-[:OVK*1..1]-(f:{label})-[:OVZ]->(z) WHERE p.firstName = 'Ann' RETURN count(DISTINCT f)"
        before = parallel.__dict__.get("_native_parallel_runs", 0)
        r = parallel.execute_cypher(q)
        assert getattr(r, "error", None) is None, r.error
        assert [list(row) for row in r.rows] == [[6]]
        assert parallel.__dict__.get("_native_parallel_runs", 0) == before + 1
    finally:
        for node in nodes:
            parallel.delete_node(node)


@pytest.mark.parametrize("name,cypher,params", PROJECTIONS, ids=[c[0] for c in PROJECTIONS])
def test_an_anchored_projection_runs_in_workers(parallel, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = parallel.__dict__.get("_native_parallel_runs", 0)
    r = parallel.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert parallel.__dict__.get("_native_parallel_runs", 0) == before + 1, "the projection did not run in workers"
