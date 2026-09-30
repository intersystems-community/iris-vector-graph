"""`delete_node` / `bulk_delete_nodes` against live state (DEBT entry 10, bug 1).

4.1.0's `delete_node` deleted only from `kg_NodeEmbeddings`, so a node in a named
graph kept its vector in that graph's `kg_emb_<hash>` table; each statement
autocommitted, so a failure mid-way left the node row and vector behind with its
labels, props and edges gone; and `except Exception: return False` hid both. The
unit tests asserted the SQL sent to a mock cursor, deleted only in the default
graph, and their cleanup never looked at the return value. These tests look at the
rows left in the database.
"""

import uuid

import pytest

DIM = 768


@pytest.fixture
def eng(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=DIM)


@pytest.fixture
def graph(eng):
    name = f"ivgdel{uuid.uuid4().hex[:8]}"
    yield name
    eng.erase_graph(name)


def _count(eng, sql, *params):
    cur = eng.conn.cursor()
    try:
        cur.execute(sql, list(params))
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _vector_tables(eng, graph):
    cur = eng.conn.cursor()
    try:
        cur.execute(
            "SELECT table_name FROM Graph_KG.embedding_registry WHERE graph_id = ?", [graph]
        )
        tables = {r[0] for r in cur.fetchall() if r[0]}
    finally:
        cur.close()
    return tables | {"kg_NodeEmbeddings"}


def _left(eng, graph, node_id):
    """Every row still naming ``node_id`` in ``graph``."""
    left = {}
    for table, col in (
        ("nodes", "node_id"),
        ("rdf_labels", "s"),
        ("rdf_props", "s"),
        ("rdf_edges", "s"),
    ):
        left[table] = _count(
            eng,
            f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE {col} = ? AND COALESCE(graph_id, '') = ?",
            node_id,
            graph or "",
        )
    for table in _vector_tables(eng, graph):
        left[table] = _count(
            eng, f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE node_id = ?", node_id
        )
    return {k: v for k, v in left.items() if v}


def _seed(eng, node_id, graph):
    other = node_id + ":peer"
    eng.create_node(node_id, labels=["T"], properties={"k": "v"}, graph=graph)
    eng.create_node(other, graph=graph)
    eng.create_edge(node_id, "IVG_DEL_R", other, graph=graph)
    eng.store_embedding(node_id, [0.1] * DIM, graph=graph)
    assert _left(eng, graph, node_id), "seed wrote nothing"


def test_delete_node_in_a_named_graph_takes_its_vector(eng, graph):
    nid = f"{graph}:n1"
    _seed(eng, nid, graph)
    assert eng.delete_node(nid, graph=graph) is True
    assert _left(eng, graph, nid) == {}


def test_delete_node_without_a_graph_reaches_every_graph(eng, graph):
    # 4.1.0's delete_node was namespace-wide; None keeps that reach.
    nid = f"{graph}:n2"
    _seed(eng, nid, graph)
    assert eng.delete_node(nid) is True
    assert _left(eng, graph, nid) == {}


def test_delete_node_scoped_to_one_graph_leaves_the_other(eng, graph):
    nid = f"{graph}:n3"
    _seed(eng, nid, graph)
    eng.create_node(nid, labels=["T"])
    try:
        assert eng.delete_node(nid, graph=graph) is True
        assert _left(eng, graph, nid) == {}
        assert _count(
            eng,
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND COALESCE(graph_id, '') = ''",
            nid,
        ) == 1
    finally:
        eng.delete_node(nid, graph=None)


def test_delete_node_of_a_missing_node_is_false(eng, graph):
    assert eng.delete_node(f"{graph}:absent", graph=graph) is False


def test_bulk_delete_nodes_in_a_named_graph_takes_their_vectors(eng, graph):
    ids = [f"{graph}:b{i}" for i in range(3)]
    for nid in ids:
        _seed(eng, nid, graph)
    result = eng.bulk_delete_nodes(ids)
    assert (result.deleted, result.failed) == (len(ids), 0)
    for nid in ids:
        assert _left(eng, graph, nid) == {}
