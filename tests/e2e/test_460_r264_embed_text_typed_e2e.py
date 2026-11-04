"""4.6.0 R264: `embed_nodes` hands `text_fn` a node's properties with the types they are stored with, so the text (and
the vector) is the same under intersystems-irispython 5.4.0 and 5.4.1.

Run it under both drivers: scripts/dev/driver_matrix.sh does (the repo's .venv has 5.4.0; a fresh install gets 5.4.1).
Before R264, 5.4.1 read a Cypher-written 0.95 as IRIS's text, so the same stored node gave the text
``BRCA1 score=.94999999999999995559 count=29 k=5`` under 5.4.1 and ``BRCA1 score=0.95 count=29 k=5`` under 5.4.0.

The graph, by hand (label ``T264``, ids unique to the run):
- ``c``, written by Cypher: ``CREATE {name: 'BRCA1', score: 0.95, count: 29}``, then ``SET k = 2.5 * 2`` (stored as an
  IRIS decimal, which reads as the integer 5, as R218 reads it);
- ``a``, written by ``create_node``: ``{name: 'TP53', score: 0.95, count: 29}``, which the API stores as text.

So ``text_fn`` sees ``c`` as 'BRCA1', 0.95, 29, 5 (str, float, int, int) and ``a`` as 'TP53', '0.95', '29' (the API's
text, as 4.5.x read it under either driver), and the embedder is asked for exactly those two texts.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r264_{run}_{k}" for k in ("c", "a")}
    for q in (
        "CREATE (:T264 {id: $id, name: 'BRCA1', score: 0.95, count: 29})",
        "MATCH (n:T264 {id: $id}) SET n.k = 2.5 * 2",
    ):
        r = e.execute_cypher(q, {"id": ids["c"]})
        assert r.error is None, (q, r.error)
    assert e.create_node(ids["a"], labels=["T264"], properties={"name": "TP53", "score": 0.95, "count": 29})
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


def test_text_fn_sees_the_stored_types_under_any_driver(g):
    e, ids = g
    seen: dict = {}
    asked: list[str] = []
    width = e._get_embedding_dimension()

    def text_fn(node_id, props):
        seen[node_id] = dict(props)
        return " ".join(f"{k}={props[k]}" for k in sorted(props) if k != "id")

    def embedder(text):
        asked.append(text)
        return [0.5] * width

    r = e.embed_nodes(model=embedder, text_fn=text_fn, node_ids=[ids["c"], ids["a"]], force=True)

    c = {k: v for k, v in seen[ids["c"]].items() if k != "id"}
    a = {k: v for k, v in seen[ids["a"]].items() if k != "id"}
    assert c == {"name": "BRCA1", "score": 0.95, "count": 29, "k": 5}
    assert [type(c[k]) for k in ("name", "score", "count", "k")] == [str, float, int, int]
    assert a == {"name": "TP53", "score": "0.95", "count": "29"}
    assert sorted(asked) == ["count=29 k=5 name=BRCA1 score=0.95", "count=29 name=TP53 score=0.95"]
    assert r["embedded"] == 2 and r["errors"] == 0, r
