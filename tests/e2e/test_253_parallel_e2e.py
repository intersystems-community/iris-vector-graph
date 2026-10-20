"""Spec 253: a native aggregate split across worker processes (a label scan, or a variable-length hop's ends) answers
as the SQL path does.

The family B fixture and cases, run with `native_parallel_min = 0` so every aggregate that starts from a label scan
takes the parallel path, in shadow mode: each answer is held equal to the SQL path's, and the grouped ones must have
run in workers.

Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_253_family_b_e2e import CASES, engine, n  # noqa: F401  (engine is the module's fixture)

# aggregates that start from a label scan and walk at least one hop (a scan with no hop has no work to split)
LABEL_SCAN_AGGREGATES = [c for c in CASES if "count(" in c[1] and "$id" not in c[1] and "-[" in c[1]]
# anchored aggregates whose first hop is variable-length: the hop's ends are shared out
ENDS_AGGREGATES = [c for c in CASES if "count(" in c[1] and "WHERE p." in c[1] and "_KNOWS*" in c[1]]


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
def test_a_variable_length_aggregate_shares_its_ends_among_workers(parallel, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = parallel.__dict__.get("_native_parallel_runs", 0)
    r = parallel.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert parallel.__dict__.get("_native_parallel_runs", 0) == before + 1, "the ends were not shared out"


def test_ends_shared_by_two_anchors_are_counted_once(parallel):
    """Two anchors reach e and f at different positions of their ends, so in different workers' shares: the workers'
    count(DISTINCT) sets overlap and are merged as sets, not added (8 would be the double count)."""
    label = f"Ov{n('x')}"
    nodes = [f"{label}_{k}" for k in "abcdefgh"]
    for k, node in zip("abcdefgh", nodes):
        parallel.create_node(node, labels=[label], properties={"sid": node, "firstName": "Ann" if k in "ab" else "X"})
    for x, y in ("ac", "ad", "ae", "af", "bf", "be", "bg", "bh"):
        parallel.create_edge(f"{label}_{x}", "OVK", f"{label}_{y}")
    try:
        q = f"MATCH (p:{label})-[:OVK*1..1]-(f:{label}) WHERE p.firstName = 'Ann' RETURN count(DISTINCT f)"
        before = parallel.__dict__.get("_native_parallel_runs", 0)
        r = parallel.execute_cypher(q)
        assert getattr(r, "error", None) is None, r.error
        assert [list(row) for row in r.rows] == [[6]]
        assert parallel.__dict__.get("_native_parallel_runs", 0) == before + 1
    finally:
        for node in nodes:
            parallel.delete_node(node)
