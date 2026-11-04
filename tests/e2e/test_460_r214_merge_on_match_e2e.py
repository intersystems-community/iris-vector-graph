"""4.6.0 R214, live: `MERGE ... ON MATCH SET` writes on a matched node; `ON CREATE SET` does not.

Walkthrough 2 (W1) ran its probe on d2f00abe, before R186. Re-run on 71b39fc8 (release-4.6.0 after R186) it still
fails, on both execution paths (native_cypher True and False), with no error:

- `ON MATCH SET` never writes. The walkthrough's sequence, by hand: the first MERGE creates om (c = 1), the second
  matches it, so m = 5 and c stays 1.
- `ON CREATE SET` fires on a matched node that lacks the property: a node made by a plain `MERGE (n:OM {id:'s'})`,
  merged again with `ON CREATE SET n.c = 1`, comes back with c = 1. Where c is already set it is left alone, so
  `ON CREATE SET` behaves as "set where absent", whichever branch MERGE took.

Strict xfail: the fix belongs to R212 (MERGE ON MATCH, another worker's), and these flip to a failure there when it
lands. Runs in an IVG460FX* scratch namespace (scripts/dev/make_scratch_namespace.py), emptied first.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
    pytest.mark.xfail(strict=True, reason="R214: MERGE ON MATCH SET is ignored on both paths; fixed with R212"),
]


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    yield c
    iris.createIRIS(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.commit()
    c.close()


@pytest.fixture(params=[True, False], ids=["native", "sql"])
def engine(request, conn):
    import iris

    from iris_vector_graph import IRISGraphEngine

    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=request.param)
    eng.initialize_schema()
    return eng


def _rows(engine, q):
    r = engine.execute_cypher(q)
    assert r.error is None, r.error
    return r.rows


def test_the_walkthrough_sequence(engine):
    assert _rows(engine, "MERGE (n:OM {id:'om'}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 1 RETURN n.c, n.m") == [
        [1, None]
    ]
    assert _rows(engine, "MERGE (n:OM {id:'om'}) ON CREATE SET n.c = 2 ON MATCH SET n.m = 5 RETURN n.c, n.m") == [
        [1, 5]
    ]
    assert _rows(engine, "MATCH (n:OM {id:'om'}) RETURN n.c, n.m") == [[1, 5]]


def test_on_create_does_not_fire_on_a_matched_node(engine):
    _rows(engine, "MERGE (n:OM {id:'s'})")
    assert _rows(engine, "MERGE (n:OM {id:'s'}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 5 RETURN n.c, n.m") == [
        [None, 5]
    ]
    assert _rows(engine, "MATCH (n:OM {id:'s'}) RETURN n.c, n.m") == [[None, 5]]
