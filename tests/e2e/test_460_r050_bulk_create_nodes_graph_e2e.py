"""4.6.0 R050 W3, live: `bulk_create_nodes(nodes, graph="g")` writes the nodes into graph g; a node's own `graph` key wins.

By hand: the batch {id: a}, {id: b, labels: [L]}, {id: c, graph: other} with graph=w3g puts a and b in w3g (b labelled
L), c in `other`, and none in the default graph. With the classes deployed and without, the same rows.

Run with IVG_NAMESPACE set to an IVG460FX* scratch namespace that has IVG installed (scripts/dev/make_scratch_namespace.py).
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    yield c
    c.close()


@pytest.mark.parametrize("server", [False, True], ids=["sql", "classes-deployed"])
def test_the_keyword_names_the_graph(conn, server):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.capabilities import IRISCapabilities

    engine = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    engine.capabilities = IRISCapabilities(objectscript_deployed=server)
    tag = uuid.uuid4().hex[:6]
    a, b, c = (f"w3{tag}{x}" for x in "abc")
    try:
        engine.bulk_create_nodes(
            [{"id": a}, {"id": b, "labels": ["L"]}, {"id": c, "graph": f"other{tag}"}], graph=f"w3g{tag}"
        )
        conn.commit()
        cur = conn.cursor()
        cur.execute(
            f"SELECT %EXACT(graph_id), %EXACT(node_id) FROM {engine._t('nodes')} WHERE node_id IN (?, ?, ?)", [a, b, c]
        )
        assert sorted(tuple(r) for r in cur.fetchall()) == sorted(
            [(f"w3g{tag}", a), (f"w3g{tag}", b), (f"other{tag}", c)]
        )
        assert engine.node_count(graph=f"w3g{tag}") == 2
        assert engine.get_node(b, graph=f"w3g{tag}")["labels"] == ["L"]
        assert engine.get_node(a) is None
    finally:
        for g, n in ((f"w3g{tag}", a), (f"w3g{tag}", b), (f"other{tag}", c)):
            engine.delete_node(n, graph=g)
        conn.commit()
