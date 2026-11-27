"""Erasure reaches the BM25 indexes: no text of an erased node stays in ``^BM25Idx``.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_erase_search_indexes.py

``Graph.KG.Eraser`` deleted the ``Graph_KG.docs`` rows of an erased graph (spec 230) and left
``^BM25Idx``: the postings, the document record and the length of every erased node stayed, and
``bm25_search`` went on answering with the ids of nodes that no longer existed. ADR-0004 calls a
partial erasure a defect, and ADR-0013 says erasure and redaction together cover everything IVG can
reach.

Every assertion reads the index globals after the call (``^BM25Idx`` through the Native API), and
every expected answer is written out here, not read back from the index under test.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "erase_idx_acme"
OTHER = "erase_idx_globex"
IDX = "erase_idx_acme_text"
OTHER_IDX = "erase_idx_globex_text"
CLS = "Graph.KG.BM25Index"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    native = eng._iris_obj()
    for name in (IDX, OTHER_IDX):
        native.classMethodValue(CLS, "Drop", name)
    yield eng
    native = eng._iris_obj()
    for name in (IDX, OTHER_IDX):
        native.classMethodValue(CLS, "Drop", name)


def _g(engine, *subs):
    """The node ^BM25Idx(subs...): its value, or None when undefined."""
    return engine._iris_obj().get("BM25Idx", *subs)


def _defined(engine, *subs):
    """Nonzero when ^BM25Idx(subs...) holds a value or any descendant."""
    return int(engine._iris_obj().isDefined("BM25Idx", *subs))


def _seed(engine):
    """Two graphs, two documents each. 'zebra' and 'giraffe' are in ACME; 'zebra' is also in GLOBEX."""
    engine.create_node("acme_1", properties={"name": "zebra crossing"}, graph=GRAPH)
    engine.create_node("acme_2", properties={"name": "giraffe enclosure"}, graph=GRAPH)
    engine.create_node("globex_1", properties={"name": "zebra stripes"}, graph=OTHER)
    engine.create_node("globex_2", properties={"name": "otter river"}, graph=OTHER)
    engine.bm25_build(IDX, ["name"], graph=GRAPH)
    engine.bm25_build(OTHER_IDX, ["name"], graph=OTHER)


def _ids(hits):
    return [h[0] for h in hits]


def test_the_seed_is_found_before_the_erase(engine):
    """Guard the guard: the word is in the index, so the empty answers below mean something."""
    _seed(engine)
    assert _ids(engine.bm25_search(IDX, "zebra", k=5)) == ["acme_1"]
    assert _ids(engine.bm25_search(IDX, "giraffe", k=5)) == ["acme_2"]
    assert _defined(engine, IDX, "tf", "zebra", "acme_1") > 0
    assert _defined(engine, IDX, "doc", "acme_1") > 0


def test_erase_graph_leaves_no_text_in_the_graphs_index(engine):
    _seed(engine)

    engine.erase_graph(GRAPH)

    assert engine.bm25_search(IDX, "zebra", k=5) == []
    assert engine.bm25_search(IDX, "giraffe", k=5) == []
    for term, doc in (("zebra", "acme_1"), ("giraffe", "acme_2")):
        assert _defined(engine, IDX, "tf", term) == 0, f"a posting list for {term!r} is still in ^BM25Idx"
        assert _defined(engine, IDX, "df", term) == 0, f"df for {term!r} is still in ^BM25Idx"
        assert _defined(engine, IDX, "doc", doc) == 0, f"the document record of {doc} is still in ^BM25Idx"
        assert _defined(engine, IDX, "len", doc) == 0, f"the length of {doc} is still in ^BM25Idx"
    assert int(_g(engine, IDX, "cfg", "N")) == 0
    assert int(_g(engine, IDX, "cfg", "totalLen")) == 0
    assert int(_g(engine, IDX, "cfg", "vocab_size")) == 0


def test_erase_graph_keeps_the_indexes_configuration(engine):
    """The index is emptied, not dropped: its parameters and the graph it covers survive, so a re-ingest
    followed by bm25_insert scores with the same k1 and b."""
    _seed(engine)
    k1, graph = _g(engine, IDX, "cfg", "k1"), _g(engine, IDX, "cfg", "graph")

    engine.erase_graph(GRAPH)

    assert (_g(engine, IDX, "cfg", "k1"), _g(engine, IDX, "cfg", "graph")) == (k1, graph) == (k1, GRAPH)
    assert engine.bm25_insert(IDX, "acme_3", "zebra again") is True
    assert _ids(engine.bm25_search(IDX, "zebra", k=5)) == ["acme_3"]


def test_erase_graph_leaves_another_graphs_index_alone(engine):
    _seed(engine)

    engine.erase_graph(GRAPH)

    assert _ids(engine.bm25_search(OTHER_IDX, "zebra", k=5)) == ["globex_1"]
    assert _ids(engine.bm25_search(OTHER_IDX, "otter", k=5)) == ["globex_2"]
    assert int(_g(engine, OTHER_IDX, "cfg", "N")) == 2


def test_erase_graph_empties_every_index_of_that_graph(engine):
    """Two indexes over one graph (different properties) are both emptied."""
    _seed(engine)
    engine.create_node("acme_3", properties={"alt": "quokka"}, graph=GRAPH)
    second = IDX + "_alt"
    try:
        engine.bm25_build(second, ["alt"], graph=GRAPH)
        assert _ids(engine.bm25_search(second, "quokka", k=5)) == ["acme_3"]

        engine.erase_graph(GRAPH)

        assert engine.bm25_search(second, "quokka", k=5) == []
        assert _defined(engine, second, "tf", "quokka") == 0
        assert _defined(engine, second, "doc", "acme_3") == 0
    finally:
        engine._iris_obj().classMethodValue(CLS, "Drop", second)


def test_deleting_nodes_by_prefix_takes_only_their_documents_out(engine):
    _seed(engine)
    engine.create_node("drop_1", properties={"name": "zebra quagga"}, graph=GRAPH)
    engine.bm25_build(IDX, ["name"], graph=GRAPH)
    assert sorted(_ids(engine.bm25_search(IDX, "zebra", k=5))) == ["acme_1", "drop_1"]

    assert engine.delete_nodes_by_prefix("drop_", graph=GRAPH) == 1

    assert _ids(engine.bm25_search(IDX, "zebra", k=5)) == ["acme_1"]
    assert engine.bm25_search(IDX, "quagga", k=5) == []
    assert _defined(engine, IDX, "tf", "quagga") == 0
    assert _defined(engine, IDX, "doc", "drop_1") == 0
    assert _defined(engine, IDX, "len", "drop_1") == 0
    # the bookkeeping follows the documents: two left, "zebra" in one of them
    assert int(_g(engine, IDX, "cfg", "N")) == 2
    assert int(_g(engine, IDX, "df", "zebra")) == 1
    # another graph's index holding the same word is untouched
    assert _ids(engine.bm25_search(OTHER_IDX, "zebra", k=5)) == ["globex_1"]


def test_deleting_a_node_id_takes_it_out_of_the_index_of_its_own_graph_only(engine):
    """A node id lives in several graphs since the re-key; erasing it from one leaves the other's document."""
    engine.create_node("twin", properties={"name": "zebra"}, graph=GRAPH)
    engine.create_node("twin", properties={"name": "zebra"}, graph=OTHER)
    engine.bm25_build(IDX, ["name"], graph=GRAPH)
    engine.bm25_build(OTHER_IDX, ["name"], graph=OTHER)

    engine.delete_nodes_by_prefix("twin", graph=GRAPH)

    assert engine.bm25_search(IDX, "zebra", k=5) == []
    assert _ids(engine.bm25_search(OTHER_IDX, "zebra", k=5)) == ["twin"]


def test_erase_all_empties_every_index(engine):
    _seed(engine)

    engine.erase_all()

    for name in (IDX, OTHER_IDX):
        assert engine.bm25_search(name, "zebra", k=5) == []
        assert _defined(engine, name, "tf", "zebra") == 0
        assert int(_g(engine, name, "cfg", "N")) == 0


def test_a_schema_1_index_loses_the_erased_nodes_postings(engine):
    """An index built before ADR-0007 has no per-document token record. Its postings are found by walking the
    vocabulary; the erase must not refuse it or leave the node in it."""
    engine.create_node("old_1", properties={"name": "zebra"}, graph=GRAPH)
    engine.create_node("old_2", properties={"name": "giraffe"}, graph=GRAPH)
    n = engine._iris_obj()
    for doc, term in (("old_1", "zebra"), ("old_2", "giraffe")):
        n.set(1, "BM25Idx", IDX, "tf", term, doc)
        n.set(1, "BM25Idx", IDX, "len", doc)
        n.set(0.7, "BM25Idx", IDX, "idf", term)
    n.set(2, "BM25Idx", IDX, "cfg", "N")
    n.set(2, "BM25Idx", IDX, "cfg", "totalLen")
    n.set(1, "BM25Idx", IDX, "cfg", "avgdl")
    n.set(GRAPH, "BM25Idx", IDX, "cfg", "graph")
    n.set(1.5, "BM25Idx", IDX, "cfg", "k1")
    n.set(0.75, "BM25Idx", IDX, "cfg", "b")
    assert _ids(engine.bm25_search(IDX, "zebra", k=5)) == ["old_1"]

    engine.delete_nodes_by_prefix("old_1", graph=GRAPH)

    assert engine.bm25_search(IDX, "zebra", k=5) == []
    assert _defined(engine, IDX, "tf", "zebra", "old_1") == 0
    assert _defined(engine, IDX, "len", "old_1") == 0
    assert int(_g(engine, IDX, "cfg", "N")) == 1
    assert _defined(engine, IDX, "tf", "giraffe", "old_2") > 0


def test_delete_node_and_bulk_delete_nodes_take_the_documents_out(engine):
    _seed(engine)
    engine.create_node("acme_3", properties={"name": "zebra foal"}, graph=GRAPH)
    engine.bm25_build(IDX, ["name"], graph=GRAPH)
    assert sorted(_ids(engine.bm25_search(IDX, "zebra", k=5))) == ["acme_1", "acme_3"]

    assert engine.delete_node("acme_1", graph=GRAPH) is True
    assert _ids(engine.bm25_search(IDX, "zebra", k=5)) == ["acme_3"]
    assert _defined(engine, IDX, "doc", "acme_1") == 0

    engine.bulk_delete_nodes(["acme_3", "acme_2"], graph=GRAPH)
    assert engine.bm25_search(IDX, "zebra", k=5) == []
    assert engine.bm25_search(IDX, "giraffe", k=5) == []
    assert int(_g(engine, IDX, "cfg", "N")) == 0
    assert _defined(engine, IDX, "tf") == 0
