"""4.6.0 R245, live: ``materialize_inference`` as a reader raises ``GraphWriteError`` and writes nothing.

It answered ``{"inferred": 0}`` (``except Exception: continue`` around each INSERT), the answer for a graph with
nothing to infer. By hand: the default graph holds r245:A subClassOf r245:B and r245:B subClassOf r245:C, so RDFS infers
one edge, r245:A subClassOf r245:C. A reader holding ADMIN_GUIDE §3's reading role (SELECT and EXECUTE on the IVG Schema,
the database at ``R``) reads both edges and is refused the INSERT at Prepare (SQLCODE -99). The owner then infers that
one edge, and ``retract_inference`` removes it.

    IVG_NAMESPACE=IVG460FXT pytest tests/e2e/test_460_r245_materialize_inference_refused_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R245"
PREFIX = "FIXES29"
SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def _subclass_edges(conn) -> list:
    cur = conn.cursor()
    cur.execute(f"SELECT s, o_id, qualifiers FROM {rr.SCHEMA}.rdf_edges WHERE p = ? AND COALESCE(graph_id, '') = '' "
                "ORDER BY s, o_id", [SUBCLASS])
    return [tuple(r) for r in cur.fetchall()]


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    owner.erase_graph(None)
    for n in ("r245:A", "r245:B", "r245:C"):
        assert owner.create_node(n) is True
    assert owner.create_edge("r245:A", SUBCLASS, "r245:B") is True
    assert owner.create_edge("r245:B", SUBCLASS, "r245:C") is True
    user = rr.make_reader(TAG, rr.reading_resources("R"), prefix=PREFIX)
    rconn = rr.connect(user, rr.PASSWORD)
    reader = IRISGraphEngine(rconn, embedding_dimension=rr.DIM)
    try:
        yield owner, reader, sysconn
    finally:
        rconn.close()
        rr.drop_reader(TAG, PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def test_a_reader_is_refused_and_nothing_is_written(setup):
    from iris_vector_graph import GraphWriteError

    owner, reader, sysconn = setup
    before = (rr.state(sysconn), _subclass_edges(sysconn))
    with pytest.raises(GraphWriteError) as raised:
        reader.materialize_inference()
    assert raised.value.operation == "materialize_inference"
    assert raised.value.sqlcode == -99, raised.value
    assert "materialize_inference failed" in str(raised.value) and "nothing written" in str(raised.value)
    assert (rr.state(sysconn), _subclass_edges(sysconn)) == before


def test_the_owner_infers_the_one_edge(setup):
    owner, _reader, sysconn = setup
    assert owner.materialize_inference() == {"inferred": 1}
    edges = _subclass_edges(sysconn)
    assert [(s, o) for s, o, _ in edges] == [("r245:A", "r245:B"), ("r245:A", "r245:C"), ("r245:B", "r245:C")]
    assert "inferred" in (dict((s + o, q) for s, o, q in edges)["r245:Ar245:C"] or "")
    assert owner.retract_inference() == 1
    assert [(s, o) for s, o, _ in _subclass_edges(sysconn)] == [("r245:A", "r245:B"), ("r245:B", "r245:C")]
