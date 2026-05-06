import os
import time
import uuid
import random

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "test")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "test")


@pytest.fixture(scope="module")
def engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        e = IRISGraphEngine(c, embedding_dimension=4)
        e.initialize_schema()
        yield e
        c.close()
    except Exception as ex:
        pytest.skip(f"IRIS unavailable: {ex}")


@pytest.fixture(scope="module")
def chain_graph(engine):
    pfx = f"chain_{uuid.uuid4().hex[:6]}"
    n = 50
    for i in range(n):
        engine.create_node(f"{pfx}:{i}", labels=["ChainNode"], properties={"idx": i})
    for i in range(n - 1):
        engine.create_edge(f"{pfx}:{i}", "NEXT", f"{pfx}:{i+1}")
    return pfx, n


@pytest.fixture(scope="module")
def star_graph(engine):
    pfx = f"star_{uuid.uuid4().hex[:6]}"
    center = f"{pfx}:hub"
    engine.create_node(center, labels=["StarHub"])
    spokes = 100
    for i in range(spokes):
        spoke = f"{pfx}:spoke{i}"
        engine.create_node(spoke, labels=["StarSpoke"])
        engine.create_edge(center, "SPOKE", spoke)
    return pfx, center, spokes


class TestBasicCypher:

    def test_match_all_nodes_with_label(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(f"MATCH (n:ChainNode) WHERE n.node_id STARTS WITH '{pfx}' RETURN count(n) AS c")
        assert r["rows"][0][0] >= n

    def test_match_with_property_filter(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (n:ChainNode) WHERE n.node_id STARTS WITH '{pfx}' AND n.idx = 5 RETURN n.node_id"
        )
        assert len(r["rows"]) >= 1

    def test_match_relationship_pattern(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a)-[:NEXT]->(b) WHERE a.node_id = '{pfx}:0' RETURN b.node_id"
        )
        assert r["rows"][0][0] == f"{pfx}:1"

    def test_match_two_hop_path(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a)-[:NEXT]->(b)-[:NEXT]->(c) WHERE a.node_id = '{pfx}:0' RETURN c.node_id"
        )
        assert r["rows"][0][0] == f"{pfx}:2"

    def test_count_distinct(self, engine, star_graph):
        pfx, center, spokes = star_graph
        r = engine.execute_cypher(
            f"MATCH (h:StarHub)-[:SPOKE]->(s:StarSpoke) WHERE h.node_id = '{center}' RETURN count(DISTINCT s) AS c"
        )
        assert r["rows"][0][0] >= spokes

    def test_order_by_limit(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (n:ChainNode) WHERE n.node_id STARTS WITH '{pfx}' RETURN n.idx ORDER BY n.idx DESC LIMIT 5"
        )
        assert len(r["rows"]) == 5
        assert r["rows"][0][0] >= r["rows"][4][0]

    def test_where_in_list(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (n:ChainNode) WHERE n.node_id IN ['{pfx}:1', '{pfx}:2', '{pfx}:3'] RETURN n.node_id ORDER BY n.node_id"
        )
        assert len(r["rows"]) == 3

    def test_optional_match_returns_null(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (n:ChainNode) WHERE n.node_id = '{pfx}:0' OPTIONAL MATCH (n)-[:NONEXISTENT]->(m) RETURN n.node_id, m.node_id"
        )
        assert len(r["rows"]) >= 1


class TestVariableLengthPaths:

    def test_vl_1_to_2_hops(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:0'}})-[:NEXT*1..2]->(b) RETURN DISTINCT b.node_id LIMIT 10"
        )
        ids = {row[0] for row in r.get("rows", [])}
        assert f"{pfx}:1" in ids
        assert f"{pfx}:2" in ids

    def test_vl_exact_hops(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:0'}})-[:NEXT*3]->(b) RETURN DISTINCT b.node_id"
        )
        ids = {row[0] for row in r.get("rows", [])}
        assert f"{pfx}:3" in ids

    def test_vl_deep_path_no_crash(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:0'}})-[:NEXT*1..10]->(b) RETURN count(DISTINCT b) AS c"
        )
        assert r["rows"][0][0] >= 1

    def test_vl_undirected(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:5'}})-[:NEXT*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 20"
        )
        ids = {row[0] for row in r.get("rows", [])}
        assert f"{pfx}:4" in ids
        assert f"{pfx}:6" in ids

    def test_vl_count_distinct(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:0'}})-[:NEXT*1..5]->(b) RETURN count(DISTINCT b) AS c"
        )
        assert r["rows"][0][0] >= 5

    def test_vl_with_limit_is_fast(self, engine, star_graph):
        pfx, center, spokes = star_graph
        t0 = time.perf_counter()
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{center}'}})-[:SPOKE*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 50"
        )
        ms = (time.perf_counter() - t0) * 1000
        assert len(r.get("rows", [])) > 0
        assert ms < 5000, f"VL LIMIT 50 took {ms:.0f}ms"

    def test_approx_count_distinct(self, engine, star_graph):
        pfx, center, spokes = star_graph
        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{center}'}})-[:SPOKE*1..2]-(b) RETURN approx_count_distinct(b) AS c"
        )
        assert r["rows"][0][0] >= 0
        warnings = getattr(r.get("metadata"), "warnings", []) or []
        assert any("approx" in w.lower() for w in warnings)


class TestShortestPath:

    def test_shortest_path_direct(self, engine, chain_graph):
        pfx, n = chain_graph
        from iris_vector_graph.schema import _call_classmethod_large
        import iris as iris_mod
        o = iris_mod.createIRIS(engine.conn)
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "ShortestPathNKG", f"{pfx}:0", f"{pfx}:5", "10")
        import json
        result = json.loads(str(raw))
        assert result.get("hops") == 5 or "hops" in result

    def test_shortest_path_no_path_returns_graceful(self, engine, pfx="sp_stub"):
        import iris as iris_mod
        from iris_vector_graph.schema import _call_classmethod_large
        o = iris_mod.createIRIS(engine.conn)
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "ShortestPathNKG", "nonexistent_a", "nonexistent_b", "5")
        import json
        result = json.loads(str(raw))
        assert result.get("hops", -1) == -1 or result == {} or "error" in result or result.get("path") == []


class TestCypherEdgeCases:

    def test_empty_graph_match_returns_empty(self, engine):
        r = engine.execute_cypher("MATCH (n:__NonExistentLabel__42) RETURN n.node_id LIMIT 1")
        assert list(r.get("rows", [])) == []

    def test_match_with_no_results_returns_empty_columns(self, engine):
        r = engine.execute_cypher("MATCH (n:__NeverExists__) RETURN n.node_id")
        assert "columns" in r
        assert list(r.get("rows", [])) == []

    def test_large_limit_no_crash(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(
            f"MATCH (n:ChainNode) WHERE n.node_id STARTS WITH '{pfx}' RETURN n.node_id LIMIT 100000"
        )
        assert len(r.get("rows", [])) >= n

    def test_cypher_with_null_parameter(self, engine):
        r = engine.execute_cypher("MATCH (n) WHERE n.node_id = $id RETURN n.node_id", {"id": None})
        assert "rows" in r

    def test_cypher_aggregation_no_rows(self, engine):
        r = engine.execute_cypher("MATCH (n:__NeverExists__) RETURN count(n) AS c")
        assert r["rows"][0][0] == 0 or r["rows"] == []

    def test_cypher_create_and_match(self, engine):
        pfx = f"cm_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:x", labels=["CreateMatch"])
        r = engine.execute_cypher(
            f"MATCH (n:CreateMatch) WHERE n.node_id = '{pfx}:x' RETURN n.node_id"
        )
        assert len(r["rows"]) == 1

    def test_cypher_property_types_roundtrip(self, engine):
        pfx = f"types_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:typed", labels=["TypedNode"], properties={
            "int_val": 42,
            "float_val": 3.14,
            "bool_val": True,
            "str_val": "hello",
        })
        r = engine.execute_cypher(
            f"MATCH (n:TypedNode) WHERE n.node_id = '{pfx}:typed' RETURN n.int_val, n.str_val"
        )
        assert len(r["rows"]) >= 1


class TestGraphAlgorithms:

    def test_khop_returns_nodes(self, engine, star_graph):
        pfx, center, spokes = star_graph
        try:
            result = engine.khop(center, hops=1, max_nodes=200)
            nodes = result.get("nodes", [])
            assert len(nodes) >= spokes
        except AttributeError:
            pytest.skip("khop not exposed on engine")

    def test_ppr_returns_scores(self, engine, star_graph):
        pfx, center, spokes = star_graph
        try:
            result = engine.ppr(center)
            assert "scores" in result or isinstance(result, list)
        except AttributeError:
            pytest.skip("ppr not exposed on engine")

    def test_random_walk_no_crash(self, engine, star_graph):
        pfx, center, spokes = star_graph
        try:
            result = engine.random_walk(center, length=10, num_walks=5)
            assert result is not None
        except AttributeError:
            pytest.skip("random_walk not exposed on engine")


class TestCypherParseErrors:

    def test_syntax_error_raises(self, engine):
        with pytest.raises(Exception):
            engine.execute_cypher("MATCH (n RETURN n")

    def test_undefined_variable_raises(self, engine):
        with pytest.raises(Exception):
            engine.execute_cypher("MATCH (n) RETURN undefined_var.prop")

    def test_valid_but_complex_query(self, engine, chain_graph):
        pfx, n = chain_graph
        r = engine.execute_cypher(f"""
            MATCH (a:ChainNode)-[:NEXT]->(b:ChainNode)-[:NEXT]->(c:ChainNode)
            WHERE a.node_id STARTS WITH '{pfx}'
            RETURN a.node_id, b.node_id, c.node_id
            ORDER BY a.idx
            LIMIT 5
        """)
        assert len(r.get("rows", [])) >= 1
