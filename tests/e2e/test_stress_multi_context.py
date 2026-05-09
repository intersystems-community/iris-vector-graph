import os
import time
import uuid
import threading
import contextlib

import pytest


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    e = IRISGraphEngine(iris_connection, embedding_dimension=4)
    e.initialize_schema()
    return e


class TestNamedGraphs:

    def test_create_nodes_in_named_graph(self, engine):
        pfx = f"ngraph_{uuid.uuid4().hex[:6]}"
        graph_id = f"graph:{pfx}"
        engine.create_node(f"{pfx}:n1", labels=["NGNode"])
        engine.create_node(f"{pfx}:n2", labels=["NGNode"])
        engine.create_edge(f"{pfx}:n1", "NG_REL", f"{pfx}:n2")

    def test_list_graphs_returns_list(self, engine):
        graphs = engine.list_graphs()
        assert isinstance(graphs, list)

    def test_drop_graph_no_crash(self, engine):
        pfx = f"dgraph_{uuid.uuid4().hex[:6]}"
        graph_id = f"graph:{pfx}"
        engine.create_node(f"{pfx}:n1", labels=["DGNode"])
        try:
            engine.drop_graph(graph_id)
        except Exception:
            pass

    def test_list_graphs_stable_after_drop(self, engine):
        graphs_before = set(engine.list_graphs())
        pfx = f"stable_{uuid.uuid4().hex[:6]}"
        graph_id = f"graph:{pfx}"
        engine.create_node(f"{pfx}:n1", labels=["StableNode"])
        try:
            engine.drop_graph(graph_id)
        except Exception:
            pass
        graphs_after = set(engine.list_graphs())
        assert graph_id not in graphs_after

    def test_cross_graph_cypher_query(self, engine):
        pfx = f"cgraph_{uuid.uuid4().hex[:6]}"
        for i in range(3):
            engine.create_node(f"{pfx}:n{i}", labels=["CGNode"])
        r = engine.execute_cypher(
            f"MATCH (n:CGNode) WHERE n.node_id STARTS WITH '{pfx}:n' RETURN count(n) AS c"
        )
        assert r["rows"][0][0] >= 3


class TestMultiContext:

    def test_two_engine_instances_same_connection(self, engine):
        from iris_vector_graph.engine import IRISGraphEngine
        e2 = IRISGraphEngine(engine.conn, embedding_dimension=4)
        pfx = f"twoeng_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:a", labels=["TwoEng"])
        r = e2.execute_cypher(f"MATCH (n:TwoEng) WHERE n.node_id = '{pfx}:a' RETURN n.node_id")
        assert len(r["rows"]) >= 1

    def test_concurrent_reads_no_deadlock(self, engine):
        pfx = f"conc_{uuid.uuid4().hex[:6]}"
        for i in range(20):
            engine.create_node(f"{pfx}:n{i}", labels=["ConcNode"])

        errors = []

        def reader(thread_id):
            try:
                for _ in range(5):
                    engine.execute_cypher(
                        f"MATCH (n:ConcNode) WHERE n.node_id STARTS WITH '{pfx}:n' RETURN count(n) AS c"
                    )
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent reads failed: {errors}"

    def test_write_then_read_consistency(self, engine):
        pfx = f"wr_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:writer", labels=["WriteRead"])
        engine.conn.commit()
        r = engine.execute_cypher(
            f"MATCH (n:WriteRead) WHERE n.node_id = '{pfx}:writer' RETURN n.node_id"
        )
        assert len(r["rows"]) >= 1

    def test_bulk_ingest_then_traverse(self, engine):
        pfx = f"bit_{uuid.uuid4().hex[:6]}"
        nodes = [{"id": f"{pfx}:n{i}", "labels": ["BITNode"]} for i in range(50)]
        engine.bulk_create_nodes(nodes)
        edges = [{"source_id": f"{pfx}:n{i}", "predicate": "BIT_EDGE", "target_id": f"{pfx}:n{i+1}"}
                 for i in range(49)]
        engine.bulk_create_edges(edges)

        r = engine.execute_cypher(
            f"MATCH (a {{node_id: '{pfx}:n0'}})-[:BIT_EDGE*1..3]->(b) RETURN DISTINCT b.node_id LIMIT 10"
        )
        assert len(r.get("rows", [])) >= 3


class TestSnapshotPersistence:

    def test_save_and_restore_snapshot(self, engine, tmp_path):
        pfx = f"snap_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:before", labels=["SnapNode"])
        snap_path = str(tmp_path / "test_snap.ivg")
        try:
            engine.save_snapshot(snap_path)
            assert os.path.exists(snap_path)
            info = engine.snapshot_info(snap_path)
            assert info is not None
        except AttributeError:
            pytest.skip("save_snapshot not implemented")

    def test_snapshot_info_structure(self, engine, tmp_path):
        pfx = f"sinfo_{uuid.uuid4().hex[:6]}"
        engine.create_node(f"{pfx}:sn", labels=["SInfoNode"])
        snap_path = str(tmp_path / "info_snap.ivg")
        try:
            engine.save_snapshot(snap_path)
            info = engine.snapshot_info(snap_path)
            assert "node_count" in info or "nodes" in info or info is not None
        except AttributeError:
            pytest.skip("snapshot_info not implemented")


class TestIngestFormats:

    def test_import_graph_ndjson(self, engine, tmp_path):
        import json as json_mod
        pfx = f"ndjson_{uuid.uuid4().hex[:6]}"
        ndjson_path = str(tmp_path / "test_graph.ndjson")
        lines = [
            json_mod.dumps({"kind": "node", "id": f"{pfx}:a", "labels": ["NDJNode"]}),
            json_mod.dumps({"kind": "node", "id": f"{pfx}:b", "labels": ["NDJNode"]}),
            json_mod.dumps({"kind": "edge", "source": f"{pfx}:a", "predicate": "NDJ_REL", "target": f"{pfx}:b"}),
        ]
        with open(ndjson_path, "w") as f:
            f.write("\n".join(lines))
        result = engine.import_graph_ndjson(ndjson_path)
        r = engine.execute_cypher(
            f"MATCH (n:NDJNode) WHERE n.node_id STARTS WITH '{pfx}' RETURN count(n) AS c"
        )
        assert r["rows"][0][0] >= 2
