import os
import time
import statistics
import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_ENT_PORT = int(os.environ.get("IRIS_ENT_PORT", "4972"))
IRIS_COM_PORT = int(os.environ.get("IRIS_COM_PORT", "1972"))
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def community_engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        conn = iris.connect(IRIS_HOST, IRIS_COM_PORT, "USER", "_SYSTEM", "SYS")
        yield IRISGraphEngine(conn), iris.createIRIS(conn)
        conn.close()
    except Exception as e:
        pytest.skip(f"Community IRIS unavailable: {e}")


@pytest.fixture(scope="module")
def enterprise_engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        conn = iris.connect(IRIS_HOST, IRIS_ENT_PORT, "USER", "_SYSTEM", "SYS")
        o = iris.createIRIS(conn)
        o.classMethodValue("Graph.KG.ArnoAccel", "Load")
        eng = IRISGraphEngine(conn)
        eng._detect_arno()
        if not eng._arno_capabilities.get("rust_callout") or \
           "bfs" not in eng._arno_capabilities.get("rust_algorithms", []):
            pytest.skip("Arno Rust BFS not available on enterprise container")
        cur = conn.cursor()
        cur.execute("SELECT TOP 1 s FROM Graph_KG.rdf_edges WHERE p = ?", [PRED])
        row = cur.fetchone()
        if not row:
            pytest.skip("No KNOWS edges in enterprise — LDBC data not loaded")
        yield eng, o, str(row[0])
        conn.close()
    except Exception as e:
        pytest.skip(f"Enterprise IRIS unavailable or Rust BFS inactive: {e}")


@pytest.fixture(scope="module")
def bfs_test_graph(community_engine):
    eng, o = community_engine
    cur = eng.conn.cursor()
    nodes = ["bfs_hub", "bfs_a", "bfs_b", "bfs_c"]
    for n in nodes:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [n])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    eng.conn.commit()
    from iris_vector_graph.schema import _call_classmethod
    for s, t in [("bfs_hub", "bfs_a"), ("bfs_hub", "bfs_b"), ("bfs_a", "bfs_c")]:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, "BFS_R", t])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, "BFS_R", t])
    eng.conn.commit()
    _call_classmethod(eng.conn, "Graph.KG.Traversal", "BuildKG")
    yield
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [n, n])
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    eng.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_nkgaccel_bfs_json_output_format_is_sorted(enterprise_engine):
    eng, o, seed = enterprise_engine
    result = str(o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, '["KNOWS"]', 2, 100))
    assert result.startswith("SORTED:"), \
        f"NKGAccel.BFSJson must return 'SORTED:tag' after spec 153, got: {result[:40]}"


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_rust_bfs_result_matches_objectscript(enterprise_engine):
    eng, o, seed = enterprise_engine
    from iris_vector_graph.schema import _call_classmethod_large
    import json

    rust_raw = str(o.classMethodString("Graph.KG.NKGAccel", "BFSJson", seed, '["KNOWS"]', 2, 0))
    if rust_raw.startswith("SORTED:"):
        tag = rust_raw.split(":")[1]
        rust_results = json.loads(str(o.classMethodString("Graph.KG.Traversal", "ReadBFSResults", tag)))
    else:
        rust_results = json.loads(rust_raw)

    obj_results = json.loads(_call_classmethod_large(o, "Graph.KG.Traversal", "BFSFastJson", seed, "KNOWS", 2))
    assert len(rust_results) > 0
    assert abs(len(rust_results) - len(obj_results)) <= 1, \
        f"Rust ({len(rust_results)}) and ObjectScript ({len(obj_results)}) BFS counts differ by >1"


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_engine_execute_cypher_bfs_via_rust(enterprise_engine):
    eng, o, seed = enterprise_engine
    result = eng.execute_cypher(
        "MATCH (s {node_id:$id})-[:KNOWS*1..2]->(n) RETURN n.node_id LIMIT 100",
        {"id": seed}
    )
    assert result, "execute_cypher must succeed"
    assert len(result.rows) > 0, "Must return results"
    assert len(result.rows) <= 100


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_engine_chunked_branch_removed():
    import iris_vector_graph.engine as eng_mod
    import inspect
    src = inspect.getsource(eng_mod.IRISGraphEngine._execute_var_length_cypher)
    assert "BFSFastJsonChunked" not in src, \
        "Legacy BFSFastJsonChunked branch must be removed from _execute_var_length_cypher"


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_objectscript_bfs_still_works_after_change(community_engine, bfs_test_graph):
    eng, o = community_engine
    result = eng.execute_cypher(
        "MATCH (s {node_id:$id})-[:BFS_R*1..2]->(n) RETURN n.node_id",
        {"id": "bfs_hub"}
    )
    assert result
    ids = {r[0] for r in result.rows}
    assert "bfs_a" in ids
    assert "bfs_b" in ids
    assert "bfs_c" in ids