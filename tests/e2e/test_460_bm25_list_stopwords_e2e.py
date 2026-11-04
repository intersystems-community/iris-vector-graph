"""4.6.0 R165: after a BM25 build, `status()` and `SHOW INDEXES` list that index and no `__stop__`.

The first BM25 build in a namespace writes the stopword list under `^BM25Idx("__stop__")`, beside one subscript per
index, and `Graph.KG.BM25Index.List()` walked every first subscript: both reports listed a BM25 index named
`__stop__`.

Worked by hand: one index built here over two nodes' `text`. The BM25 indexes listed afterwards are the ones listed
before plus that one (in a fresh scratch namespace, exactly that one); the stopword list is there, and not listed.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def built(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    name = f"b165_{run}"
    nodes = [f"st165_{run}_{i}" for i in range(2)]
    for i, nid in enumerate(nodes):
        assert e.create_node(nid, labels=["St165"], properties={"text": f"the alpha beta {i}"})
    before = _bm25(e)
    e.bm25_build(name, ["text"])
    yield e, name, before
    e.bm25_drop(name)
    for nid in nodes:
        e.delete_node(nid)


def _bm25(engine) -> list:
    s = engine.status()
    assert not [x for x in s.errors if "index list" in x], s.errors
    return sorted(s.indexes.bm25_indexes)


def _shown(engine) -> list:
    shown = engine.execute_cypher("SHOW INDEXES", raise_on_error=True)
    name_col, type_col = shown.columns.index("name"), shown.columns.index("type")
    return sorted(r[name_col] for r in shown.rows if r[type_col] == "FULLTEXT(BM25)")


def test_the_stopword_list_is_there_and_not_an_index(built, iris_connection):
    import iris

    e, name, before = built
    assert iris.createIRIS(iris_connection).isDefined("^BM25Idx", "__stop__", "the"), "no stopword list was written"
    assert "__stop__" not in before
    assert _bm25(e) == sorted(set(before) | {name})
    assert _shown(e) == sorted(set(before) | {name})
