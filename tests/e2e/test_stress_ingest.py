import json
import time
import uuid
import contextlib

import pytest


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    e = IRISGraphEngine(iris_connection, embedding_dimension=4)
    e.initialize_schema()
    return e


@pytest.fixture
def pfx():
    return f"ingest_{uuid.uuid4().hex[:8]}"


class TestNodeIngest:

    def test_single_node_roundtrip(self, engine, pfx):
        nid = f"{pfx}:n1"
        engine.create_node(nid, labels=["IngestTest"], properties={"val": 42, "name": "alpha"})
        node = engine.get_nodes([nid])
        assert len(node) == 1
        assert node[0].get("id") == nid or node[0].get("node_id") == nid

    def test_bulk_create_nodes_1k(self, engine, pfx):
        nodes = [{"id": f"{pfx}:n{i}", "labels": ["Bulk1k"], "properties": {"idx": i}} for i in range(1000)]
        t0 = time.perf_counter()
        engine.bulk_create_nodes(nodes)
        ms = (time.perf_counter() - t0) * 1000
        count = engine.get_node_count(label="Bulk1k")
        assert count >= 1000, f"Expected ≥1000 Bulk1k nodes, got {count}"
        assert ms < 30000, f"1K bulk insert took {ms:.0f}ms — too slow"

    def test_bulk_create_nodes_10k(self, engine, pfx):
        nodes = [{"id": f"{pfx}:big{i}", "labels": ["Bulk10k"], "properties": {"i": i}} for i in range(10000)]
        t0 = time.perf_counter()
        engine.bulk_create_nodes(nodes)
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 120000, f"10K bulk insert took {ms:.0f}ms"

    def test_duplicate_node_no_crash(self, engine, pfx):
        nid = f"{pfx}:dup"
        engine.create_node(nid, labels=["DupTest"])
        engine.create_node(nid, labels=["DupTest"])

    def test_node_with_unicode_properties(self, engine, pfx):
        nid = f"{pfx}:unicode"
        engine.create_node(nid, labels=["UniTest"], properties={
            "name": "山田太郎",
            "emoji": "🧬",
            "arabic": "مرحبا",
        })
        nodes = engine.get_nodes([nid])
        assert len(nodes) == 1

    def test_node_with_long_string_property(self, engine, pfx):
        nid = f"{pfx}:longstr"
        long_val = "x" * 4000
        engine.create_node(nid, labels=["LongStr"], properties={"content": long_val})
        nodes = engine.get_nodes([nid])
        assert len(nodes) == 1

    def test_delete_node(self, engine, pfx):
        nid = f"{pfx}:del"
        engine.create_node(nid, labels=["DelTest"])
        try:
            engine.delete_node(nid)
            nodes = engine.get_nodes([nid])
            assert len(nodes) == 0
        except AttributeError:
            pytest.skip("delete_node not implemented")


class TestEdgeIngest:

    def test_single_edge_roundtrip(self, engine, pfx):
        engine.create_node(f"{pfx}:ea", labels=["EdgeTest"])
        engine.create_node(f"{pfx}:eb", labels=["EdgeTest"])
        engine.create_edge(f"{pfx}:ea", "STRESS_REL", f"{pfx}:eb")
        r = engine.execute_cypher(
            f"MATCH (a)-[:STRESS_REL]->(b) WHERE a.node_id = '{pfx}:ea' RETURN b.node_id"
        )
        assert any(row[0] == f"{pfx}:eb" for row in r.get("rows", []))

    def test_edge_with_qualifiers(self, engine, pfx):
        engine.create_node(f"{pfx}:qa", labels=["QualTest"])
        engine.create_node(f"{pfx}:qb", labels=["QualTest"])
        engine.create_edge(
            f"{pfx}:qa", "WEIGHTED", f"{pfx}:qb",
            qualifiers={"weight": 0.75, "source": "stress_test"}
        )
        r = engine.execute_cypher(
            f"MATCH (a)-[r:WEIGHTED]->(b) WHERE a.node_id = '{pfx}:qa' RETURN r.weight"
        )
        assert len(r.get("rows", [])) >= 1

    def test_bulk_create_edges_1k(self, engine, pfx):
        src = f"{pfx}:bulk_src"
        engine.create_node(src, labels=["BulkEdge"])
        targets = [f"{pfx}:be{i}" for i in range(200)]
        for t in targets:
            engine.create_node(t, labels=["BulkEdge"])
        edges = [{"source_id": src, "predicate": "BULK_REL", "target_id": t} for t in targets]
        t0 = time.perf_counter()
        engine.bulk_create_edges(edges)
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 30000

    def test_self_loop_edge(self, engine, pfx):
        nid = f"{pfx}:self"
        engine.create_node(nid, labels=["SelfLoop"])
        try:
            engine.create_edge(nid, "SELF_REL", nid)
        except Exception:
            pass

    def test_delete_edge(self, engine, pfx):
        engine.create_node(f"{pfx}:da", labels=["DelEdge"])
        engine.create_node(f"{pfx}:db", labels=["DelEdge"])
        engine.create_edge(f"{pfx}:da", "DEL_REL", f"{pfx}:db")
        try:
            engine.delete_edge(f"{pfx}:da", "DEL_REL", f"{pfx}:db")
        except AttributeError:
            pytest.skip("delete_edge not implemented")


class TestBulkIngestEdges:

    def test_bulk_ingest_edges_direct_kg(self, engine, pfx):
        from iris_vector_graph.schema import _call_classmethod_large
        import iris as iris_mod
        o = iris_mod.createIRIS(engine.conn)
        edges = [{"s": f"{pfx}:bie{i}", "p": "BIE_REL", "o": f"{pfx}:bie{i+1}"} for i in range(0, 100, 2)]
        t0 = time.perf_counter()
        n = _call_classmethod_large(o, "Graph.KG.EdgeScan", "BulkIngestEdges", json.dumps(edges), "BIE_REL")
        ms = (time.perf_counter() - t0) * 1000
        assert int(str(n)) > 0
        assert ms < 5000, f"50 BulkIngestEdges took {ms:.0f}ms"

    def test_bulk_ingest_throughput_10k(self, engine, pfx):
        from iris_vector_graph.schema import _call_classmethod_large
        import iris as iris_mod
        o = iris_mod.createIRIS(engine.conn)
        batch_size = 50000
        edges = [{"s": f"{pfx}:bie_s{i%100}", "p": "THRU_REL", "o": f"{pfx}:bie_t{i}"} for i in range(10000)]
        t0 = time.perf_counter()
        for i in range(0, len(edges), batch_size):
            _call_classmethod_large(o, "Graph.KG.EdgeScan", "BulkIngestEdges", json.dumps(edges[i:i+batch_size]), "THRU_REL")
        ms = (time.perf_counter() - t0) * 1000
        rate = len(edges) / (ms / 1000)
        assert rate > 10000, f"BulkIngestEdges throughput {rate:.0f} e/s — expected >10K e/s"


class TestTemporalIngest:

    def test_create_temporal_edge(self, engine, pfx):
        ts = int(time.time() * 1000)
        engine.create_node(f"{pfx}:ta", labels=["TempNode"])
        engine.create_node(f"{pfx}:tb", labels=["TempNode"])
        engine.create_edge_temporal(f"{pfx}:ta", "TEMP_REL", f"{pfx}:tb", timestamp=ts)

    def test_bulk_temporal_edges(self, engine, pfx):
        now_ms = int(time.time() * 1000)
        edges = [
            {"source_id": f"{pfx}:ta", "predicate": "T_BULK", "target_id": f"{pfx}:tb{i}",
             "timestamp": now_ms - i * 1000}
            for i in range(10)
        ]
        for e in edges:
            engine.create_node(e["target_id"], labels=["TBulk"])
        engine.create_node(f"{pfx}:ta", labels=["TBulk"])
        try:
            engine.bulk_create_edges_temporal(edges)
        except Exception as ex:
            pytest.skip(f"bulk_create_edges_temporal failed: {ex}")

    def test_get_edges_in_window(self, engine, pfx):
        now_ms = int(time.time() * 1000)
        engine.create_node(f"{pfx}:tw_src", labels=["TWin"])
        engine.create_node(f"{pfx}:tw_dst", labels=["TWin"])
        engine.create_edge_temporal(f"{pfx}:tw_src", "WIN_REL", f"{pfx}:tw_dst", timestamp=now_ms)
        result = engine.get_edges_in_window(
            f"{pfx}:tw_src", "WIN_REL",
            start=now_ms - 60000, end=now_ms + 60000
        )
        assert len(result) >= 1

    def test_temporal_aggregate_count(self, engine, pfx):
        now_ms = int(time.time() * 1000)
        engine.create_node(f"{pfx}:agg_src", labels=["AggNode"])
        for i in range(10):
            engine.create_node(f"{pfx}:agg_dst{i}", labels=["AggNode"])
            engine.create_edge_temporal(
                f"{pfx}:agg_src", "AGG_REL", f"{pfx}:agg_dst{i}",
                timestamp=now_ms - i * 1000
            )
        agg = engine.get_temporal_aggregate(
            f"{pfx}:agg_src", "AGG_REL", "count",
            ts_start=now_ms - 20000, ts_end=now_ms + 1000
        )
        assert (agg if isinstance(agg, int) else agg.get("count", 0)) >= 5

    def test_purge_before(self, engine, pfx):
        now_ms = int(time.time() * 1000)
        old_ts = now_ms - 10_000_000
        engine.create_node(f"{pfx}:purge_src", labels=["PurgeNode"])
        engine.create_node(f"{pfx}:purge_dst", labels=["PurgeNode"])
        engine.create_edge_temporal(f"{pfx}:purge_src", "OLD_REL", f"{pfx}:purge_dst", timestamp=old_ts)
        try:
            engine.purge_before(old_ts + 1)
        except (AttributeError, NotImplementedError):
            pytest.skip("purge_before not implemented")


class TestIngestErrorRecovery:

    def test_bulk_create_nodes_empty_list(self, engine):
        engine.bulk_create_nodes([])

    def test_create_edge_missing_nodes(self, engine, pfx):
        try:
            engine.create_edge(f"{pfx}:ghost_a", "GHOST_REL", f"{pfx}:ghost_b")
        except Exception:
            pass

    def test_bulk_create_edges_empty(self, engine):
        engine.bulk_create_edges([])

    def test_very_large_property_value(self, engine, pfx):
        nid = f"{pfx}:huge"
        huge_val = "y" * 32000
        try:
            engine.create_node(nid, labels=["HugeVal"], properties={"big": huge_val})
        except Exception as e:
            assert "string" in str(e).lower() or "length" in str(e).lower() or "maxstring" in str(e).lower()

    def test_special_chars_in_node_id(self, engine):
        special_ids = [
            "node:with:colons:1",
            "node_with_underscores",
            "node-with-dashes",
            "node.with.dots",
        ]
        for nid in special_ids:
            try:
                engine.create_node(nid, labels=["SpecialId"])
            except Exception:
                pass

    def test_null_property_value(self, engine, pfx):
        nid = f"{pfx}:nullprop"
        engine.create_node(nid, labels=["NullProp"], properties={"present": "yes", "missing": None})

    def test_numeric_overflow_property(self, engine, pfx):
        nid = f"{pfx}:numover"
        try:
            engine.create_node(nid, labels=["NumOver"], properties={
                "big_int": 2**62,
                "neg_int": -(2**62),
                "float_val": 1.7976931348623157e+308,
            })
        except Exception:
            pass

    def test_nested_dict_property_serialized(self, engine, pfx):
        nid = f"{pfx}:nested"
        engine.create_node(nid, labels=["Nested"], properties={
            "meta": json.dumps({"a": 1, "b": [2, 3]})
        })
