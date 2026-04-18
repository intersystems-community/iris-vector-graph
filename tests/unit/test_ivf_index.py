import base64
import json
import math
import os
import struct
from unittest.mock import MagicMock

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    return engine


def _encode_vec(floats):
    return base64.b64encode(struct.pack(f"{len(floats)}f", *floats)).decode()


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class TestIVFIndexUnit:

    def test_ivf_build_calls_classmethod(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"nlist":2,"dim":3,"metric":"cosine","indexed":2}'
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [
            ("n1", _encode_vec([0.1, 0.2, 0.3])),
            ("n2", _encode_vec([0.4, 0.5, 0.6])),
        ]
        engine.conn.cursor.return_value = cursor_mock

        engine.ivf_build("test46a", nlist=2)

        calls = iris_mock.classMethodValue.call_args_list
        build_calls = [c for c in calls if len(c.args) > 1 and c.args[1] == "Build"]
        assert build_calls, "ivf_build must call Graph.KG.IVFIndex.Build"
        assert build_calls[0].args[0] == "Graph.KG.IVFIndex"
        assert build_calls[0].args[2] == "test46a"

    def test_ivf_build_returns_dict(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"nlist":2,"dim":3,"metric":"cosine","indexed":2}'
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [
            ("n1", _encode_vec([0.1, 0.2, 0.3])),
            ("n2", _encode_vec([0.4, 0.5, 0.6])),
        ]
        engine.conn.cursor.return_value = cursor_mock

        result = engine.ivf_build("test46a", nlist=2)
        assert isinstance(result, dict)
        assert "nlist" in result
        assert "indexed" in result
        assert "dim" in result

    def test_ivf_build_idempotent_unit(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"nlist":2,"dim":3,"metric":"cosine","indexed":1}'
        engine._iris_obj = lambda: iris_mock

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [
            ("n1", _encode_vec([0.1, 0.2, 0.3])),
        ]
        engine.conn.cursor.return_value = cursor_mock

        engine.ivf_build("test46a", nlist=2)
        engine.ivf_build("test46a", nlist=2)

        build_calls = [c for c in iris_mock.classMethodValue.call_args_list
                       if len(c.args) > 1 and c.args[1] == "Build"]
        assert len(build_calls) == 2

    def test_ivf_search_returns_sorted_tuples(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '[{"id":"A","score":0.95},{"id":"B","score":0.82}]'
        engine._iris_obj = lambda: iris_mock

        result = engine.ivf_search("test46a", [0.1, 0.2, 0.3], k=2, nprobe=2)
        assert result == [("A", 0.95), ("B", 0.82)]
        assert result[0][1] >= result[1][1]

    def test_ivf_search_empty_index_returns_empty(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "[]"
        engine._iris_obj = lambda: iris_mock

        result = engine.ivf_search("test46_noexist", [0.1, 0.2], k=5, nprobe=2)
        assert result == []

    def test_ivf_drop_calls_classmethod(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        engine._iris_obj = lambda: iris_mock

        engine.ivf_drop("test46x")
        calls = iris_mock.classMethodVoid.call_args_list
        assert any(c.args[1] == "Drop" and c.args[2] == "test46x" for c in calls)

    def test_ivf_info_returns_dict(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"nlist":4,"dim":3,"metric":"cosine","indexed":5}'
        engine._iris_obj = lambda: iris_mock

        result = engine.ivf_info("test46a")
        assert isinstance(result, dict)
        assert result["nlist"] == 4
        assert result["indexed"] == 5

    def test_ivf_info_missing_returns_empty(self):
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "{}"
        engine._iris_obj = lambda: iris_mock

        result = engine.ivf_info("test46_missing")
        assert result == {}

    def test_ivf_cypher_translation_produces_cte(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "CALL ivg.ivf.search('myidx', [0.1, 0.2, 0.3], 5, 2) YIELD node, score RETURN node, score ORDER BY score DESC"
        parsed = parse_query(q)
        sql_obj = translate_to_sql(parsed, {})
        sql = sql_obj.sql if isinstance(sql_obj.sql, str) else "\n".join(sql_obj.sql)
        assert "IVF AS (" in sql or "IVF" in sql
        assert "Graph_KG.kg_IVF" in sql or "kg_IVF" in sql

    def test_ivf_cypher_rejects_wrong_argcount(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query

        q = "CALL ivg.ivf.search('myidx', [0.1]) YIELD node, score RETURN node, score"
        parsed = parse_query(q)
        with pytest.raises(ValueError, match="ivg.ivf.search"):
            translate_to_sql(parsed, {})


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestIVFIndexE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        import uuid
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
        self._run = uuid.uuid4().hex[:8]
        yield
        for idx in [f"test46a_{self._run}", f"test46b_{self._run}",
                    f"test46c_{self._run}", f"test46d_{self._run}",
                    f"test46e_{self._run}"]:
            try:
                self.engine.ivf_drop(idx)
            except Exception:
                pass

    def _make_nodes_with_embeddings(self, n: int, dim: int = 4):
        import random
        rng = random.Random(42)
        nodes = []
        for i in range(n):
            nid = f"ivf_n{i}_{self._run}"
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            self.engine.conn.cursor().execute(
                "INSERT OR IGNORE INTO Graph_KG.kg_NodeEmbeddings (id, embedding) VALUES (?, ?)",
                [nid, _encode_vec(vec)]
            )
            nodes.append((nid, vec))
        try:
            self.engine.conn.commit()
        except Exception:
            pass
        return nodes

    def _insert_embeddings(self, nodes):
        cursor = self.engine.conn.cursor()
        for nid, vec in nodes:
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, embedding) VALUES (?, ?)",
                    [nid, _encode_vec(vec)]
                )
            except Exception:
                try:
                    cursor.execute(
                        "UPDATE Graph_KG.kg_NodeEmbeddings SET embedding = ? WHERE id = ?",
                        [_encode_vec(vec), nid]
                    )
                except Exception:
                    pass
        try:
            self.engine.conn.commit()
        except Exception:
            pass

    def _cleanup_nodes(self, node_ids):
        cursor = self.engine.conn.cursor()
        for nid in node_ids:
            try:
                cursor.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?", [nid])
            except Exception:
                pass
        try:
            self.engine.conn.commit()
        except Exception:
            pass

    def test_build_indexes_nodes(self):
        import random
        rng = random.Random(7)
        dim = 4
        nodes = []
        for i in range(5):
            nid = f"ivfbuild_{i}_{self._run}"
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            nodes.append((nid, vec))
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46a_{self._run}"
        result = self.engine.ivf_build(idx, nlist=2)
        assert isinstance(result, dict)
        assert result.get("indexed", 0) >= 5
        assert result.get("nlist") == 2

        info = self.engine.ivf_info(idx)
        assert info.get("indexed", 0) >= 5
        self._cleanup_nodes(node_ids)

    def test_build_idempotent(self):
        import random
        rng = random.Random(8)
        dim = 4
        nodes = [(f"ivfidm_{i}_{self._run}", [rng.gauss(0, 1) for _ in range(dim)]) for i in range(4)]
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46b_{self._run}"
        r1 = self.engine.ivf_build(idx, nlist=2)
        r2 = self.engine.ivf_build(idx, nlist=2)
        assert r1["indexed"] == r2["indexed"]
        self._cleanup_nodes(node_ids)

    def test_search_returns_results(self):
        import random
        rng = random.Random(9)
        dim = 4
        nodes = []
        for i in range(20):
            nid = f"ivfsearch_{i}_{self._run}"
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            nodes.append((nid, vec))
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46c_{self._run}"
        self.engine.ivf_build(idx, nlist=4)
        query = nodes[0][1]
        results = self.engine.ivf_search(idx, query, k=5, nprobe=4)
        assert len(results) > 0
        assert results[0][1] >= results[-1][1]
        self._cleanup_nodes(node_ids)

    def test_nprobe_exact_matches_brute_force(self):
        import random
        rng = random.Random(10)
        dim = 4
        nodes = []
        for i in range(20):
            nid = f"ivfexact_{i}_{self._run}"
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            nodes.append((nid, vec))
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46d_{self._run}"
        self.engine.ivf_build(idx, nlist=4)
        query = nodes[3][1]
        exact = self.engine.ivf_search(idx, query, k=1, nprobe=4)
        brute_top = max(nodes, key=lambda n: _cosine(query, n[1]))
        assert len(exact) > 0
        assert exact[0][0] == brute_top[0]
        self._cleanup_nodes(node_ids)

    def test_search_empty_index_returns_empty(self):
        result = self.engine.ivf_search(f"ivf_noexist_{self._run}", [0.1, 0.2, 0.3, 0.4], k=5, nprobe=2)
        assert result == []

    def test_drop_removes_index(self):
        import random
        rng = random.Random(11)
        dim = 4
        nodes = [(f"ivfdrop_{i}_{self._run}", [rng.gauss(0, 1) for _ in range(dim)]) for i in range(4)]
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46a_{self._run}"
        self.engine.ivf_build(idx, nlist=2)
        self.engine.ivf_drop(idx)
        info = self.engine.ivf_info(idx)
        assert info == {}
        self._cleanup_nodes(node_ids)

    def test_info_returns_cfg(self):
        import random
        rng = random.Random(12)
        dim = 4
        nodes = [(f"ivfinfo_{i}_{self._run}", [rng.gauss(0, 1) for _ in range(dim)]) for i in range(4)]
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46b_{self._run}"
        self.engine.ivf_build(idx, nlist=2)
        info = self.engine.ivf_info(idx)
        assert info.get("nlist") == 2
        assert info.get("indexed", 0) > 0
        self._cleanup_nodes(node_ids)

    def test_ivf_cypher_end_to_end(self):
        import random
        rng = random.Random(13)
        dim = 4
        nodes = []
        for i in range(10):
            nid = f"ivfcyph_{i}_{self._run}"
            vec = [rng.gauss(0, 1) for _ in range(dim)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            nodes.append((nid, vec))
        self._insert_embeddings(nodes)
        node_ids = [n[0] for n in nodes]

        idx = f"test46e_{self._run}"
        self.engine.ivf_build(idx, nlist=2)
        query = nodes[0][1]
        vec_str = ", ".join(str(v) for v in query)
        cypher = (
            f"CALL ivg.ivf.search('{idx}', [{vec_str}], 3, 2) "
            f"YIELD node, score RETURN node, score ORDER BY score DESC"
        )
        result = self.engine.execute_cypher(cypher)
        assert "columns" in result
        assert "node" in result["columns"] or "score" in result["columns"]
        assert len(result.get("rows", [])) > 0
        self._cleanup_nodes(node_ids)
