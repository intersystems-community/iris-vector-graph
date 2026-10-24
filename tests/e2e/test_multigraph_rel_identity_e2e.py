"""In a multigraph, relationship equality tells parallel relationships apart (KNOWN_ISSUES, found 2026-10-23).

Two parallel K relationships a -> b and one b -> c. `MATCH (a)-[r1:K]->(b), (a)-[r2:K]->(b)` pairs five relationship
combinations: 2 x 2 over (a, b) and 1 over (b, c). `r1 = r2` keeps 3 (each relationship with itself), `r1 <> r2` keeps
2. Both paths answered 5 and 0: the comparison was on (s, p, o_id), which parallel relationships share; it now compares
edge_id too. Outside multigraph mode a triple is one relationship and nothing changes.

Run: IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture(scope="module")
def graphs(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    e._native_privileges_cached = True
    t = uuid.uuid4().hex[:5]
    multi, plain, label = f"mri{t}_m", f"mri{t}_p", f"Mri{t}"
    e.set_multigraph(multi, True)
    for g in (multi, plain):
        for k in "abc":
            e.create_node(f"{t}{k}", labels=[label], graph=g)
        e.create_edge(f"{t}a", "K", f"{t}b", graph=g)
        e.create_edge(f"{t}b", "K", f"{t}c", graph=g)
    # the parallel relationship: a second CREATE of (a, K, b) in multigraph mode
    r = e.execute_cypher(
        f"USE GRAPH '{multi}' MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) CREATE (a)-[:K]->(b)", {"a": f"{t}a", "b": f"{t}b"}
    )
    assert getattr(r, "error", None) is None, r.error
    yield e, multi, plain, label
    for g in (multi, plain):
        e.erase_graph(g)
    e.set_multigraph(multi, False)


def _count(e, q, native):
    e.native_cypher = native
    try:
        r = e.execute_cypher(q)
    finally:
        e.native_cypher = True
    assert getattr(r, "error", None) is None, r.error
    return r.rows[0][0]


PAIRS = "MATCH (a:{L})-[r1:K]->(b:{L}), (a)-[r2:K]->(b) WHERE {cond} RETURN count(*) AS n"
STAGED = "MATCH (a:{L})-[r1:K]->(b:{L}) WITH a, b, r1 MATCH (a)-[r2:K]->(b) WHERE {cond} RETURN count(*) AS n"


@pytest.mark.parametrize("native", [True, False], ids=["native", "sql"])
@pytest.mark.parametrize(
    "shape,cond,multi_expect,plain_expect",
    [
        (PAIRS, "r1 = r2", 3, 2),
        (PAIRS, "r1 <> r2", 2, 0),
        (STAGED, "r1 = r2", 3, 2),
        (STAGED, "r1 <> r2", 2, 0),
    ],
    ids=["pairs-eq", "pairs-ne", "staged-eq", "staged-ne"],
)
def test_relationship_equality_tells_parallel_relationships_apart(graphs, native, shape, cond, multi_expect, plain_expect):
    e, multi, plain, label = graphs
    body = shape.format(L=label, cond=cond)
    assert _count(e, f"USE GRAPH '{multi}' " + body, native) == multi_expect
    assert _count(e, f"USE GRAPH '{plain}' " + body, native) == plain_expect
