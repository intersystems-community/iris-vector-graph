"""4.6.0 R103: `SHOW INDEXES` lists the BM25, IVF and PLAID indexes that exist.

It read their names from `kg_BM25Meta`, `kg_IVFMeta` and `kg_PlaidMeta`, tables nothing creates, and swallowed the
error, so those indexes never appeared. They are recorded where their classes keep them: `^BM25Idx`, `^IVF` and
`^PLAID`, one subscript per index name (`Graph.KG.BM25Index.List()` and its two siblings answer the names).

Built here: one index of each kind, named by hand; after dropping them, none is listed. The store's `list_indexes`
(the HTTP server's index report) reads the same.
"""

from __future__ import annotations

import pytest

from tests.conftest import SESSION_EMBEDDING_DIM

pytestmark = [pytest.mark.e2e]

EXPECTED = {("b103", "FULLTEXT(BM25)"), ("i103", "VECTOR(IVF)"), ("p103", "VECTOR(PLAID)")}


def _vec(i: int) -> list:
    v = [0.0] * SESSION_EMBEDDING_DIM
    v[i] = 1.0
    return v


def _listed(engine) -> set:
    shown = engine.execute_cypher("SHOW INDEXES", raise_on_error=True)
    name_col, type_col = shown.columns.index("name"), shown.columns.index("type")
    return {(r[name_col], r[type_col]) for r in shown.rows}


def _stored(engine) -> set:
    return {(r[0], r[1]) for r in engine._store.list_indexes().rows}


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
    for i, nid in enumerate(("d1", "d2", "d3", "d4")):
        e.create_node(nid, labels=["Doc"], properties={"text": f"alpha beta {nid}"})
        e.store_embedding(nid, _vec(i))
    yield e
    for drop, name in ((e.bm25_drop, "b103"), (e.ivf_drop, "i103"), (e.plaid_drop, "p103")):
        drop(name)


def test_show_indexes_lists_one_of_each_kind_and_forgets_them_when_dropped(engine):
    engine.bm25_build("b103", ["text"])
    engine.ivf_build("i103", nlist=2)
    engine.plaid_build("p103", [{"id": "d1", "tokens": [[1.0, 0.0], [0.0, 1.0]]},
                                {"id": "d2", "tokens": [[1.0, 1.0]]}], n_clusters=2, dim=2)
    assert EXPECTED <= _listed(engine)
    assert EXPECTED <= _stored(engine)

    engine.bm25_drop("b103")
    engine.ivf_drop("i103")
    engine.plaid_drop("p103")
    assert not EXPECTED & _listed(engine)
    assert not EXPECTED & _stored(engine)
