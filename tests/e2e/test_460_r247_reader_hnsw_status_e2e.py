"""4.6.0 R247, live: ``status()`` for ADMIN_GUIDE §3's reading role says whether the vector tables have an HNSW index.

The role may not read ``%Dictionary`` in SQL (SQLCODE -99), so ``status()`` listed "class lookup failed for
Graph_KG.kg_NodeEmbeddings" (and ``kg_NodeEmbeddings_optimized``) and reported no index whatever the tables held. It
now asks ``Graph.KG.Meta.HnswIndexes`` over the Native API. By hand: with ``idx_f29_ann``, an HNSW index on
``Graph_KG.kg_NodeEmbeddings(emb)``, made by the owner, the reader's and the owner's ``status()`` both read the HNSW
index as built, and the reader's lists no dictionary error; with the index dropped, both read it as not built
(setup makes no HNSW index on either table).

    IVG_NAMESPACE=IVG460FXT pytest tests/e2e/test_460_r247_reader_hnsw_status_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R247"
PREFIX = "FIXES29"
INDEX = "idx_f29_ann"


def _drop_index(conn):
    cur = conn.cursor()
    try:
        cur.execute(f"DROP INDEX {INDEX} ON {rr.SCHEMA}.kg_NodeEmbeddings")
    except Exception as exc:
        assert "-" in str(exc), exc  # not there
    conn.commit()


@pytest.fixture(scope="module")
def engines():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    owner.initialize_schema()  # deploys this checkout's Graph.KG.Meta
    _drop_index(sysconn)
    user = rr.make_reader(TAG, rr.reading_resources("R"), prefix=PREFIX)
    rconn = rr.connect(user, rr.PASSWORD)
    reader = IRISGraphEngine(rconn, embedding_dimension=rr.DIM)
    try:
        yield owner, reader, sysconn
    finally:
        rconn.close()
        rr.drop_reader(TAG, PREFIX)
        _drop_index(sysconn)
        sysconn.close()


def _dictionary_errors(status) -> list:
    return [e for e in status.errors if e.startswith(("class lookup failed", "HNSW index read failed"))]


def test_with_an_hnsw_index_the_reader_reads_it_built(engines):
    owner, reader, sysconn = engines
    cur = sysconn.cursor()
    cur.execute(f"CREATE INDEX {INDEX} ON {rr.SCHEMA}.kg_NodeEmbeddings (emb) AS HNSW(Distance='Cosine')")
    sysconn.commit()
    status = reader.status()
    assert _dictionary_errors(status) == [], status.errors
    assert status.indexes.hnsw_built is True
    assert owner.status().indexes.hnsw_built is True


def test_without_one_the_reader_reads_it_not_built(engines):
    owner, reader, sysconn = engines
    _drop_index(sysconn)
    status = reader.status()
    assert _dictionary_errors(status) == [], status.errors
    assert status.indexes.hnsw_built is False
    assert owner.status().indexes.hnsw_built is False
