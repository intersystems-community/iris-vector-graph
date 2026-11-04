"""4.6.0 R276, live: the calls whose ``except Exception:`` swallows now raise still give their answers when nothing
fails. Each is asked a question whose answer the test wrote itself.

    IVG_NAMESPACE=IVG460FY6 IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/e2e/test_460_r276_except_audit_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

#: kg_NodeEmbeddings' width in a namespace initialize_schema made (make_scratch_namespace.py's).
DIM = 768


def _v(i):
    """The unit vector along axis ``i``."""
    return [1.0 if j == i else 0.0 for j in range(DIM)]


@pytest.fixture
def eng(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=DIM)


@pytest.fixture
def prefix(eng, iris_connection):
    p = f"R276A_{uuid.uuid4().hex[:6]}_"
    yield p
    eng._store.delete_nodes_by_prefix(p)
    iris_connection.commit()


def test_a_vector_stored_twice_is_the_second_one_once(eng, prefix):
    """The DELETE before the INSERT runs unguarded now; the overwrite still replaces the vector."""
    a = prefix + "a"
    eng.create_node(a)
    before = eng.embedding_count()
    eng.store_embedding(a, _v(0))
    eng.store_embedding(a, _v(1))
    assert eng.embedding_count() == before + 1
    assert eng._subgraph_embeddings([a]) == {a: _v(1)}


def test_a_batch_overwrites_too(eng, prefix):
    a, b = prefix + "a", prefix + "b"
    eng.create_node(a)
    eng.create_node(b)
    eng.store_embeddings([{"node_id": a, "embedding": _v(0)}, {"node_id": b, "embedding": _v(2)}])
    eng.store_embeddings([{"node_id": a, "embedding": _v(3)}])
    assert eng._subgraph_embeddings([a, b, prefix + "none"]) == {a: _v(3), b: _v(2)}


def test_a_vector_for_a_missing_node_is_still_refused(eng, prefix):
    with pytest.raises(ValueError, match="Node does not exist"):
        eng.store_embedding(prefix + "ghost", _v(0))


def test_nodes_exist_answers_which_are_there(eng, prefix):
    a = prefix + "a"
    eng.create_node(a)
    assert eng.nodes_exist([a, prefix + "missing"]) == {a}


def test_delete_nodes_commits_and_erases(eng, prefix):
    a, b = prefix + "a", prefix + "b"
    eng.create_node(a)
    eng.create_node(b)
    assert eng._store.delete_nodes([a]).rows == [[1]]
    assert eng.nodes_exist([a, b]) == {b}


def test_list_namespaces_reads_what_was_registered(eng, prefix):
    eng.register_namespace(prefix.lower().rstrip("_"), f"http://example.org/{prefix}#")
    assert eng.list_namespaces()[prefix.lower().rstrip("_")] == f"http://example.org/{prefix}#"


def test_embed_queue_pending_counts_the_queue(eng):
    assert eng.embed_queue_pending() == 0
