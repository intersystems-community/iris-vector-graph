"""4.6.0 R134: `engine.status().indexes` reports a BM25, IVF or PLAID index once it is built, and not before.

It read the names from `kg_BM25Meta`, `kg_IVFMeta` and `kg_PlaidMeta`, tables nothing creates, and swallowed the
error, so every list was empty whatever had been built. The names are now read where the classes record them (each
class's `List()`, as `SHOW INDEXES` reads them since R103).

Built here, one of each kind under a name made for this run; before the build it is not listed, after the build it is,
and after the drop it is gone again.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import SESSION_EMBEDDING_DIM

pytestmark = [pytest.mark.e2e]


def _vec(i: int) -> list:
    v = [0.0] * SESSION_EMBEDDING_DIM
    v[i] = 1.0
    return v


@pytest.fixture
def engine(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
    run = uuid.uuid4().hex[:8]
    names = {"bm25": f"b134_{run}", "ivf": f"i134_{run}", "plaid": f"p134_{run}"}
    nodes = [f"st134_{run}_{i}" for i in range(4)]
    for i, nid in enumerate(nodes):
        e.create_node(nid, labels=["St134"], properties={"text": f"alpha beta {i}"})
        e.store_embedding(nid, _vec(i))
    yield e, names, nodes
    for drop, kind in ((e.bm25_drop, "bm25"), (e.ivf_drop, "ivf"), (e.plaid_drop, "plaid")):
        drop(names[kind])
    for nid in nodes:
        e.delete_node(nid)


def _listed(engine) -> dict:
    s = engine.status()
    assert not [x for x in s.errors if "index list" in x], s.errors
    return {"bm25": s.indexes.bm25_indexes, "ivf": s.indexes.ivf_indexes, "plaid": s.indexes.plaid_indexes}


def test_status_reports_an_index_once_it_is_built(engine):
    e, names, nodes = engine
    before = _listed(e)
    assert not any(names[k] in before[k] for k in names), before

    e.bm25_build(names["bm25"], ["text"])
    after_bm25 = _listed(e)
    assert names["bm25"] in after_bm25["bm25"]
    assert names["ivf"] not in after_bm25["ivf"] and names["plaid"] not in after_bm25["plaid"]

    e.ivf_build(names["ivf"], nlist=2)
    e.plaid_build(names["plaid"], [{"id": nodes[0], "tokens": [[1.0, 0.0], [0.0, 1.0]]},
                                   {"id": nodes[1], "tokens": [[1.0, 1.0]]}], n_clusters=2, dim=2)
    built = _listed(e)
    assert all(names[k] in built[k] for k in names), built

    for drop, kind in ((e.bm25_drop, "bm25"), (e.ivf_drop, "ivf"), (e.plaid_drop, "plaid")):
        drop(names[kind])
    gone = _listed(e)
    assert not any(names[k] in gone[k] for k in names), gone
