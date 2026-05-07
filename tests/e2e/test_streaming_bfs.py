import os
from unittest.mock import patch, MagicMock
import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

HUB = "sbfs_hub"
SPOKES = 120
PRED = "SBFS_R"


@pytest.fixture(scope="module")
def engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        eng = IRISGraphEngine(c)
        yield eng
        c.close()
    except Exception as e:
        pytest.skip(f"IRIS unavailable: {e}")


@pytest.fixture(scope="module")
def hub_graph(engine):
    cur = engine.conn.cursor()

    def node_exists(nid):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        return cur.fetchone()[0] > 0

    if not node_exists(HUB):
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [HUB])
    spoke_ids = [f"sbfs_spoke_{i}" for i in range(SPOKES)]
    hop2_ids = [f"sbfs_hop2_{i}" for i in range(SPOKES)]

    for nid in spoke_ids + hop2_ids:
        if not node_exists(nid):
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])

    cur.execute(f"DELETE FROM Graph_KG.rdf_edges WHERE p = '{PRED}'")
    for spoke in spoke_ids:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?,?,?)", [HUB, PRED, spoke])
    for i, spoke in enumerate(spoke_ids):
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?,?,?)", [spoke, PRED, hop2_ids[i]])
    engine.conn.commit()

    from iris_vector_graph.schema import _call_classmethod
    _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")

    yield {"hub": HUB, "spokes": SPOKES, "expected_2hop": SPOKES * 2}

    cur.execute(f"DELETE FROM Graph_KG.rdf_edges WHERE p = '{PRED}'")
    for nid in [HUB] + spoke_ids + hop2_ids:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_unbounded_bfs_large_result_set_completes(engine, hub_graph):
    query = f"MATCH (s {{node_id:$id}})-[:{PRED}*1..2]->(n) RETURN n.node_id AS id"
    result = engine.execute_cypher(query, {"id": HUB})
    assert "error" not in result or not result.get("error"), f"Query error: {result.get('error')}"
    rows = result.get("rows", [])
    assert len(rows) == hub_graph["expected_2hop"], (
        f"Expected {hub_graph['expected_2hop']} results, got {len(rows)}"
    )


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_unbounded_bfs_empty_result_completes(engine):
    cur = engine.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id='sbfs_isolated'")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('sbfs_isolated')")
        engine.conn.commit()
    result = engine.execute_cypher(
        f"MATCH (s {{node_id:$id}})-[:{PRED}*1..2]->(n) RETURN n.node_id",
        {"id": "sbfs_isolated"}
    )
    assert "error" not in result or not result.get("error")
    assert len(result.get("rows", [])) == 0
    cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id='sbfs_isolated'")
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_bounded_bfs_limit_uses_fast_path(engine, hub_graph):
    result = engine.execute_cypher(
        f"MATCH (s {{node_id:$id}})-[:{PRED}*1..2]->(n) RETURN n.node_id LIMIT 10",
        {"id": HUB}
    )
    assert "error" not in result or not result.get("error")
    rows = result.get("rows", [])
    assert 0 < len(rows) <= 10


def _make_vl_sql_query(max_results_in_sql: bool):
    import json
    from iris_vector_graph.cypher.translator import SQLQuery, QueryMetadata
    sql = "SELECT n1.node_id FROM nodes n0 JOIN rdf_edges e1 ON e1.s = n0.node_id"
    if max_results_in_sql:
        sql += " LIMIT 10"
    mock = MagicMock()
    mock.sql = sql
    mock.parameters = [["hub_node"]]
    mock.var_length_paths = [
        {"types": ["R"], "min_hops": 1, "max_hops": 2,
         "src_id_param": None, "dst_id_param": None, "dst_label": "",
         "direction": "out", "properties": {}, "weighted": False,
         "shortest": False, "all_shortest": False, "return_path": False}
    ]
    mock.query_metadata = QueryMetadata()
    return mock


def test_unbounded_uses_stream_pages_not_read_bfs_results():
    import json
    import iris_vector_graph.engine as eng_mod

    read_bfs_called = []
    stream_called = []
    items = [{"s": "hub", "p": "R", "o": f"n{i}", "w": 1, "step": 1} for i in range(5)]

    def fake_call(conn, cls, method, *args):
        if method == "BFSFastJsonSorted":
            return "SORTED:tag1"
        if method == "ReadBFSResults":
            read_bfs_called.append(True)
            return json.dumps(items)
        return "[]"

    def fake_stream(conn, tag, page_size=500):
        stream_called.append(tag)
        return iter(items)

    with patch.object(eng_mod, "_call_classmethod", side_effect=fake_call), \
         patch.object(eng_mod, "_bfs_stream_pages", side_effect=fake_stream):

        from iris_vector_graph.engine import IRISGraphEngine
        mock_engine = object.__new__(IRISGraphEngine)
        mock_engine.conn = MagicMock()
        mock_engine._arno_available = False
        mock_engine._arno_capabilities = {}
        mock_engine._nkg_dirty = False

        sql_q = _make_vl_sql_query(max_results_in_sql=False)
        try:
            mock_engine._execute_var_length_cypher(sql_q, {"id": "hub_node"})
        except Exception:
            pass

    assert not read_bfs_called, "ReadBFSResults MUST NOT be called for unbounded (max_results=0)"
    assert stream_called, "_bfs_stream_pages MUST be called for unbounded queries"


def test_bounded_uses_read_bfs_results_not_stream():
    import json
    import iris_vector_graph.engine as eng_mod

    read_bfs_called = []
    stream_called = []
    bounded_items = [{"s": "hub", "p": "R", "o": f"n{i}", "w": 1, "step": 1} for i in range(5)]

    def fake_call(conn, cls, method, *args):
        if method == "BFSFastJsonSorted":
            return "SORTED:tag2"
        if method == "ReadBFSResults":
            read_bfs_called.append(True)
            return json.dumps(bounded_items)
        return "[]"

    def fake_stream(conn, tag, page_size=500):
        stream_called.append(tag)
        return iter([])

    with patch.object(eng_mod, "_call_classmethod", side_effect=fake_call), \
         patch.object(eng_mod, "_bfs_stream_pages", side_effect=fake_stream):

        from iris_vector_graph.engine import IRISGraphEngine
        mock_engine = object.__new__(IRISGraphEngine)
        mock_engine.conn = MagicMock()
        mock_engine._arno_available = False
        mock_engine._arno_capabilities = {}
        mock_engine._nkg_dirty = False

        sql_q = _make_vl_sql_query(max_results_in_sql=True)
        try:
            mock_engine._execute_var_length_cypher(sql_q, {"id": "hub_node"})
        except Exception:
            pass

    assert read_bfs_called, "ReadBFSResults MUST be called for bounded queries (LIMIT present)"
    assert not stream_called, "_bfs_stream_pages MUST NOT be called for bounded queries"

