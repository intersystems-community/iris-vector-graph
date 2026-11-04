"""4.6.0 R208: a SET, REMOVE, DELETE, MERGE or CREATE after a WITH writes the nodes the WITH carried.

`MATCH (n:X {k: 1}) WITH n SET n.q = 3` raised SQLCODE -29 (the SET selected `Stage1.node_id`, which the stage does
not have), and after `SET n.k = 2` every statement past the WITH re-ran the MATCH, found no `k: 1` and wrote nothing.

Every case builds its own nodes (labels unique to the run): A `{k: 1, keep: 'x'}`, B `{k: 1, keep: 'x'}`,
C `{k: 5, keep: 'x'}`, and for MERGE / CREATE one node M with another label. Worked by hand:

- `WITH n SET n.q = 3`: A and B get `q: 3`; C unchanged
- `SET n.k = 2 WITH n SET n.q = 3`: A and B `{k: 2, keep: 'x', q: 3}`
- `WITH n SET n.k = 2, n.q = 3`: the same
- `WITH n REMOVE n.keep`: A and B `{k: 1}`; `SET n.k = 2 WITH n REMOVE n.keep`: `{k: 2}`
- `SET n.k = 2 WITH n SET n:Extra`: A and B carry both labels
- `SET n.k = 2 WITH n DELETE n`: A and B gone, C there
- `SET n.k = 2 WITH n, m MERGE (n)-[:T]->(m)`: edges A->M and B->M; the same MERGE again (from `k: 2`): still two
- `SET n.k = 2 WITH n, m CREATE (n)-[:T]->(m)`: two edges
- `SET n.k = 2 WITH n RETURN n.id, n.k`: A and B, each with `k` 2
- `SET n.k = 2 WITH n SET n.q = 3 WITH n SET n.r = 4`: A and B `{k: 2, keep: 'x', q: 3, r: 4}`
- `SET n.k = 2 WITH n WHERE n.k = 2 SET n.q = 3`: refused; nothing written
- `WITH n MERGE (c:C)`: one C node, labelled C
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    made: list = []

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    def abc(case):
        """A, B (`k: 1`) and C (`k: 5`), each with `keep: 'x'`, labelled `L<case><run>`; returns (ids, label)."""
        label = f"L{case}{run}"
        ids = []
        for name, k in (("a", 1), ("b", 1), ("c", 5)):
            nid = f"r208_{run}_{case}_{name}"
            assert e.create_node(nid, labels=[label], properties={"k": k, "keep": "x"})
            made.append(nid)
            ids.append(nid)
        return ids, label

    def other(case):
        label = f"M{case}{run}"
        nid = f"r208_{run}_{case}_m"
        assert e.create_node(nid, labels=[label], properties={})
        made.append(nid)
        return nid, label

    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, run_q, abc, other
    for nid in made:
        e.delete_node(nid)


def _props(run_q, nid):
    rows = run_q("MATCH (n) WHERE n.id = $id RETURN properties(n)", {"id": nid})
    assert len(rows) == 1, rows
    props = json.loads(rows[0][0])
    props.pop("id")
    return props


def _count(run_q, nid):
    return run_q("MATCH (n) WHERE n.id = $id RETURN count(n)", {"id": nid})[0][0]


@pytest.mark.parametrize(
    "query, want_ab",
    [
        ("MATCH (n:{L} {{k: 1}}) WITH n SET n.q = 3", {"k": 1, "keep": "x", "q": 3}),
        ("MATCH (n:{L} {{k: 1}}) SET n.k = 2 WITH n SET n.q = 3", {"k": 2, "keep": "x", "q": 3}),
        ("MATCH (n:{L} {{k: 1}}) WITH n SET n.k = 2, n.q = 3", {"k": 2, "keep": "x", "q": 3}),
        ("MATCH (n:{L} {{k: 1}}) WITH n REMOVE n.keep", {"k": 1}),
        ("MATCH (n:{L} {{k: 1}}) SET n.k = 2 WITH n REMOVE n.keep", {"k": 2}),
        (
            "MATCH (n:{L} {{k: 1}}) SET n.k = 2 WITH n SET n.q = 3 WITH n SET n.r = 4",
            {"k": 2, "keep": "x", "q": 3, "r": 4},
        ),
    ],
    ids=["set", "set_after_key_change", "key_change_after_with", "remove", "remove_after_key_change", "two_withs"],
)
def test_node_writes_after_with(g, query, want_ab):
    _, run_q, abc, _ = g
    (a, b, c), label = abc("w")
    run_q(query.format(L=label))
    assert _props(run_q, a) == want_ab
    assert _props(run_q, b) == want_ab
    assert _props(run_q, c) == {"k": 5, "keep": "x"}


def test_label_after_with(g):
    _, run_q, abc, _ = g
    (a, b, c), label = abc("lb")
    run_q(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 WITH n SET n:Extra208")
    for nid, want in ((a, [label, "Extra208"]), (b, [label, "Extra208"]), (c, [label])):
        rows = run_q("MATCH (n) WHERE n.id = $id RETURN labels(n)", {"id": nid})
        assert sorted(json.loads(rows[0][0])) == sorted(want), nid


def test_delete_after_with(g):
    _, run_q, abc, _ = g
    (a, b, c), label = abc("dl")
    run_q(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 WITH n DELETE n")
    assert [_count(run_q, x) for x in (a, b, c)] == [0, 0, 1]


def test_merge_after_with(g):
    _, run_q, abc, other = g
    (a, b, c), label = abc("mg")
    m, mlabel = other("mg")
    run_q(f"MATCH (n:{label} {{k: 1}}), (m:{mlabel}) SET n.k = 2 WITH n, m MERGE (n)-[:T208]->(m)")
    edges = f"MATCH (n:{label})-[:T208]->(m:{mlabel}) RETURN n.id, m.id ORDER BY n.id"
    assert run_q(edges) == [[a, m], [b, m]]
    # the same MERGE again finds both edges
    run_q(f"MATCH (n:{label} {{k: 2}}), (m:{mlabel}) SET n.k = 3 WITH n, m MERGE (n)-[:T208]->(m)")
    assert run_q(edges) == [[a, m], [b, m]]
    assert [_props(run_q, x)["k"] for x in (a, b, c)] == [3, 3, 5]


def test_create_after_with(g):
    _, run_q, abc, other = g
    (a, b, _), label = abc("cr")
    m, mlabel = other("cr")
    run_q(f"MATCH (n:{label} {{k: 1}}), (m:{mlabel}) SET n.k = 2 WITH n, m CREATE (n)-[:T208C]->(m)")
    assert run_q(f"MATCH (n:{label})-[:T208C]->(m:{mlabel}) RETURN n.id, m.id ORDER BY n.id") == [[a, m], [b, m]]


def test_return_after_with(g):
    _, run_q, abc, _ = g
    (a, b, _), label = abc("rt")
    rows = run_q(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 WITH n RETURN n.id, n.k ORDER BY n.id")
    assert rows == [[a, 2], [b, 2]]


def test_with_reading_the_changed_key_is_refused_and_writes_nothing(g):
    e, run_q, abc, _ = g
    (a, b, _), label = abc("rf")
    with pytest.raises(Exception, match="is not supported"):
        r = e.execute_cypher(f"MATCH (n:{label} {{k: 1}}) SET n.k = 2 WITH n WHERE n.k = 2 SET n.q = 3", {})
        if r.error is not None:
            raise RuntimeError(r.error)
    assert _props(run_q, a) == {"k": 1, "keep": "x"}
    assert _props(run_q, b) == {"k": 1, "keep": "x"}


def test_node_merged_after_with_has_its_label(g):
    """`MATCH (n:L {k: 1}) WITH n MERGE (c:C)`: one C node, labelled C (it was labelled with its own id)."""
    _, run_q, abc, other = g
    _, label = abc("ml")
    _, mlabel = other("ml")  # a node of another label, so the C label below is the run's own
    c = f"C{mlabel}"
    try:
        run_q(f"MATCH (n:{label} {{k: 1}}) WITH n MERGE (c:{c})")
        rows = run_q(f"MATCH (c:{c}) RETURN labels(c)")
        assert [json.loads(r[0]) for r in rows] == [[c]]
    finally:
        run_q(f"MATCH (c:{c}) DETACH DELETE c")
