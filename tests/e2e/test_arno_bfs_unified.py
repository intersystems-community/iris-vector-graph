import inspect
import uuid
import pytest

PREFIX = f"abfsuni_{uuid.uuid4().hex[:8]}"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def bfs_test_graph(engine, iris_connection):
    o = engine._iris_obj()
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_hub", f"{PREFIX}_a", f"{PREFIX}_b", f"{PREFIX}_c"]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    edges = [
        (nodes[0], "BFS_R", nodes[1]),
        (nodes[0], "BFS_R", nodes[2]),
        (nodes[1], "BFS_R", nodes[3]),
    ]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()
    yield engine, o, nodes
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


@pytest.mark.e2e
def test_nkgaccel_bfs_json_output_format_is_sorted(bfs_test_graph):
    eng, o, nodes = bfs_test_graph
    result = str(o.classMethodValue(
        "Graph.KG.NKGAccel", "BFSJson", nodes[0], '["BFS_R"]', 2, 100))
    assert result.startswith("SORTED:"), \
        f"NKGAccel.BFSJson must return 'SORTED:tag', got: {result[:40]}"


@pytest.mark.e2e
def test_rust_bfs_result_matches_objectscript(bfs_test_graph):
    import json
    eng, o, nodes = bfs_test_graph
    raw = str(o.classMethodValue(
        "Graph.KG.NKGAccel", "BFSJson", nodes[0], '["BFS_R"]', 2, 0))
    if raw.startswith("SORTED:") and raw != "SORTED:0":
        tag = raw.split(":")[1]
        results = json.loads(str(o.classMethodValue(
            "Graph.KG.Traversal", "ReadBFSResults", tag)))
    else:
        results = json.loads(raw)
    assert len(results) > 0
    assert all("o" in r and "step" in r for r in results)


@pytest.mark.e2e
def test_engine_execute_cypher_bfs_via_rust(bfs_test_graph):
    eng, o, nodes = bfs_test_graph
    result = eng.execute_cypher(
        "MATCH (s {node_id:$id})-[:BFS_R*1..2]->(n) RETURN n.node_id LIMIT 100",
        {"id": nodes[0]}
    )
    assert result
    assert len(result.rows) > 0
    assert len(result.rows) <= 100


@pytest.mark.e2e
def test_engine_chunked_branch_removed():
    import iris_vector_graph.engine as eng_mod
    src = inspect.getsource(eng_mod.IRISGraphEngine._execute_var_length_cypher)
    assert "BFSFastJsonChunked" not in src, \
        "Legacy BFSFastJsonChunked branch must be removed"


@pytest.mark.e2e
def test_objectscript_bfs_still_works_after_change(bfs_test_graph):
    eng, o, nodes = bfs_test_graph
    result = eng.execute_cypher(
        "MATCH (s {node_id:$id})-[:BFS_R*1..2]->(n) RETURN n.node_id",
        {"id": nodes[0]}
    )
    assert result
    ids = {r[0] for r in result.rows}
    assert nodes[1] in ids
    assert nodes[2] in ids
    assert nodes[3] in ids
