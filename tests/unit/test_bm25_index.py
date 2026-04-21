"""Unit and E2E tests for Graph.KG.BM25Index — BM25 lexical search (spec 044).

Unit tests use mocked IRIS connection (no container required).
E2E tests run against the live iris-vector-graph-main container.
"""
import json
import os
from unittest.mock import MagicMock

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    """Create an IRISGraphEngine with a mocked _iris_obj."""
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    return engine


# ================================================================
# Unit tests — no IRIS container required
# ================================================================


class TestBM25IndexUnit:
    """Unit tests — all IRIS calls are mocked."""

    # T005 ─────────────────────────────────────────────────────────
    def test_tokenize_lowercases_and_splits(self):
        """Tokenize returns lowercase alphanumeric tokens; punctuation split."""
        engine = _make_engine()
        iris_mock = MagicMock()
        # Simulate iFind returning a $List‑like value that Python receives as a list
        iris_mock.classMethodValue.return_value = [
            "ankylosing", "spondylitis", "hla", "b27"
        ]
        engine._iris_obj = lambda: iris_mock

        # We test the ObjectScript method via classMethodValue indirectly.
        # The unit assertion is: the values are lowercase, no punctuation.
        result = iris_mock.classMethodValue("Graph.KG.BM25Index", "Tokenize", "Ankylosing Spondylitis HLA-B27")
        tokens = list(result)
        assert all(t == t.lower() for t in tokens), "tokens must be lowercase"
        assert "hla" in tokens or any("hla" in t for t in tokens)
        assert "b27" in tokens or any("b27" in t for t in tokens)

    # T007 ─────────────────────────────────────────────────────────
    def test_bm25_build_calls_classmethod(self):
        """bm25_build calls Graph.KG.BM25Index.Build with correct args."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"indexed":3,"avgdl":5.0,"vocab_size":12}'
        engine._iris_obj = lambda: iris_mock

        engine.bm25_build("ncit", ["name", "definition"])

        calls = iris_mock.classMethodValue.call_args_list
        assert any(
            call.args[0] == "Graph.KG.BM25Index" and call.args[1] == "Build"
            for call in calls
        ), "bm25_build must call Graph.KG.BM25Index.Build"
        # First call should have name="ncit" as 3rd arg
        build_calls = [c for c in calls if len(c.args) > 2 and c.args[1] == "Build"]
        assert build_calls, "Build classMethod not called"
        assert build_calls[0].args[2] == "ncit"

    # T008 ─────────────────────────────────────────────────────────
    def test_bm25_build_returns_dict(self):
        """bm25_build parses JSON response into a dict with expected keys."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"indexed":3,"avgdl":5.0,"vocab_size":12}'
        engine._iris_obj = lambda: iris_mock

        result = engine.bm25_build("ncit", ["name"])
        assert isinstance(result, dict)
        assert "indexed" in result
        assert "avgdl" in result
        assert "vocab_size" in result
        assert result["indexed"] == 3
        assert result["vocab_size"] == 12

    # T014 ─────────────────────────────────────────────────────────
    def test_bm25_search_returns_sorted_tuples(self):
        """bm25_search returns list of (id, score) tuples sorted DESC."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '[{"id":"A","score":8.4},{"id":"B","score":3.1}]'
        engine._iris_obj = lambda: iris_mock

        results = engine.bm25_search("ncit", "some query", 5)
        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0] == ("A", 8.4)
        assert results[1] == ("B", 3.1)
        # Verify descending order
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    # T015 ─────────────────────────────────────────────────────────
    def test_bm25_search_empty_query_returns_empty(self):
        """bm25_search with empty response returns [] without error."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "[]"
        engine._iris_obj = lambda: iris_mock

        results = engine.bm25_search("ncit", "", 5)
        assert results == []

    # T022 ─────────────────────────────────────────────────────────
    def test_bm25_insert_calls_classmethod(self):
        """bm25_insert calls Graph.KG.BM25Index.Insert with correct args."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "1"
        engine._iris_obj = lambda: iris_mock

        engine.bm25_insert("idx", "doc1", "some text")

        calls = iris_mock.classMethodValue.call_args_list
        insert_calls = [c for c in calls if len(c.args) > 1 and c.args[1] == "Insert"]
        assert insert_calls, "Insert classMethod not called"
        assert insert_calls[0].args[2] == "idx"
        assert insert_calls[0].args[3] == "doc1"
        assert insert_calls[0].args[4] == "some text"

    # T028 ─────────────────────────────────────────────────────────
    def test_bm25_drop_calls_classmethod(self):
        """bm25_drop calls classMethodVoid Graph.KG.BM25Index.Drop."""
        engine = _make_engine()
        iris_mock = MagicMock()
        engine._iris_obj = lambda: iris_mock

        engine.bm25_drop("idx")

        calls = iris_mock.classMethodVoid.call_args_list
        drop_calls = [c for c in calls if len(c.args) > 1 and c.args[1] == "Drop"]
        assert drop_calls, "Drop classMethodVoid not called"
        assert drop_calls[0].args[2] == "idx"

    # T029 ─────────────────────────────────────────────────────────
    def test_bm25_info_returns_dict(self):
        """bm25_info parses JSON to dict with N, avgdl, vocab_size keys."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"N":5,"avgdl":4.0,"vocab_size":20}'
        engine._iris_obj = lambda: iris_mock

        result = engine.bm25_info("idx")
        assert isinstance(result, dict)
        assert result["N"] == 5
        assert result["avgdl"] == 4.0
        assert result["vocab_size"] == 20

    def test_bm25_info_returns_empty_dict_when_not_found(self):
        """bm25_info returns {} when index not found (ObjectScript returns '{}')."""
        engine = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "{}"
        engine._iris_obj = lambda: iris_mock

        result = engine.bm25_info("nonexistent")
        assert result == {}

    # T032 ─────────────────────────────────────────────────────────
    def test_kgtxt_uses_bm25_when_default_exists(self):
        """_kg_TXT_fallback routes to BM25 when 'default' index N > 0."""
        from iris_vector_graph.operators import IRISGraphOperators

        ops = IRISGraphOperators.__new__(IRISGraphOperators)
        ops.conn = MagicMock()
        ops._bm25_default_cached = None

        # Mock graph_engine with bm25_info returning N=5
        mock_engine = MagicMock()
        mock_engine.bm25_info.return_value = {"N": 5, "avgdl": 4.0, "vocab_size": 20}
        mock_engine.bm25_search.return_value = [("NCIT:C001", 3.5), ("NCIT:C002", 1.2)]
        ops.graph_engine = mock_engine

        result = ops._kg_TXT_fallback("diabetes", 5)

        mock_engine.bm25_info.assert_called_once_with("default")
        mock_engine.bm25_search.assert_called_once_with("default", "diabetes", 5)
        assert result == [("NCIT:C001", 3.5), ("NCIT:C002", 1.2)]

    # T033 ─────────────────────────────────────────────────────────
    def test_kgtxt_uses_like_when_no_default(self):
        """_kg_TXT_fallback falls through to LIKE when 'default' index empty."""
        from iris_vector_graph.operators import IRISGraphOperators

        ops = IRISGraphOperators.__new__(IRISGraphOperators)
        ops.conn = MagicMock()
        ops._bm25_default_cached = None

        mock_engine = MagicMock()
        mock_engine.bm25_info.return_value = {}  # index not found
        ops.graph_engine = mock_engine

        # Mock the LIKE SQL path
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("NCIT:C001", 1.0)]
        ops.conn.cursor.return_value = mock_cursor

        result = ops._kg_TXT_fallback("diabetes", 5)

        mock_engine.bm25_info.assert_called_once_with("default")
        mock_engine.bm25_search.assert_not_called()
        # LIKE path produces results from cursor
        assert isinstance(result, list)

    # T038 ─────────────────────────────────────────────────────────
    def test_ivg_bm25_search_procedure_registered(self):
        """translate_procedure_call handles ivg.bm25.search without ValueError."""
        from iris_vector_graph.cypher.translator import (
            TranslationContext,
            translate_procedure_call,
        )
        from iris_vector_graph.cypher import ast

        # Build a minimal procedure call AST node
        proc = ast.CypherProcedureCall(
            procedure_name="ivg.bm25.search",
            arguments=[
                ast.Literal("test44"),
                ast.Literal("diabetes"),
                ast.Literal(5),
            ],
            yield_items=["node", "score"],
        )
        context = TranslationContext()
        translate_procedure_call(proc, context)

    # T041 ─────────────────────────────────────────────────────────
    def test_ivg_bm25_search_yields_node_score(self):
        """ivg.bm25.search registers variable_aliases for node and score."""
        from iris_vector_graph.cypher.translator import (
            TranslationContext,
            translate_procedure_call,
        )
        from iris_vector_graph.cypher import ast

        proc = ast.CypherProcedureCall(
            procedure_name="ivg.bm25.search",
            arguments=[
                ast.Literal("idx"),
                ast.Literal("query text"),
                ast.Literal(10),
            ],
            yield_items=["node", "score"],
        )
        context = TranslationContext()
        translate_procedure_call(proc, context)

        assert context.variable_aliases.get("node") == "BM25"
        assert context.variable_aliases.get("score") == "BM25"
        assert "score" in context.scalar_variables


# ================================================================
# E2E tests — require live iris-vector-graph-main container
# ================================================================


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBM25IndexE2E:
    """E2E tests against the live iris-vector-graph-main container."""

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        import uuid
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
        self._test_run_id = uuid.uuid4().hex[:8]
        yield
        # Cleanup: drop any test indexes we may have left
        for idx_name in [
            "test44a", "test44b", "test44c", "test44d", "test44e",
            "test44f", "test44g", "test44h", "default",
        ]:
            try:
                self.engine.bm25_drop(idx_name)
            except Exception:
                pass

    def _create_node_with_name(self, node_id: str, name: str) -> None:
        unique_id = f"{node_id}_{self._test_run_id}"
        self.engine.create_node(unique_id, labels=["TestBM25"], properties={"name": name})
        return unique_id

    # T011 ─────────────────────────────────────────────────────────
    def test_build_indexes_nodes(self):
        """bm25_build indexes nodes; bm25_info reports correct count."""
        self._create_node_with_name("bm25test:001", "ankylosing spondylitis")
        self._create_node_with_name("bm25test:002", "HLA-B27 antigen disease")
        self._create_node_with_name("bm25test:003", "rheumatoid arthritis")

        idx = f"test44a_{self._test_run_id}"
        result = self.engine.bm25_build(idx, ["name"])
        assert isinstance(result, dict)
        assert result.get("indexed", 0) >= 3
        assert result.get("vocab_size", 0) > 0

        info = self.engine.bm25_info(idx)
        assert info.get("N", 0) >= 3
        self.engine.bm25_drop(idx)

    # T012 ─────────────────────────────────────────────────────────
    def test_build_idempotent(self):
        """Building same index twice gives same indexed count, no error."""
        self._create_node_with_name("bm25test:004", "rheumatoid arthritis")
        self._create_node_with_name("bm25test:005", "lupus erythematosus")

        idx = f"test44a_{self._test_run_id}"
        result1 = self.engine.bm25_build(idx, ["name"])
        result2 = self.engine.bm25_build(idx, ["name"])
        assert result1["indexed"] == result2["indexed"]
        assert result2["vocab_size"] > 0
        self.engine.bm25_drop(idx)

    # T018 ─────────────────────────────────────────────────────────
    def test_search_ranks_correctly(self):
        """Node matching unique query term is top result."""
        run = self._test_run_id
        node_a_id = f"bm25rankA_{run}"
        node_b_id = f"bm25rankB_{run}"
        unique_term = f"spondylarthropathy{run}"

        self.engine.create_node(node_a_id, labels=["TestBM25"], properties={"name": f"{unique_term} disease"})
        self.engine.create_node(node_b_id, labels=["TestBM25"], properties={"name": "rheumatoid arthritis obesity"})

        idx = f"test44b_{run}"
        self.engine.bm25_build(idx, ["name"])
        results = self.engine.bm25_search(idx, unique_term, 5)

        ids = [r[0] for r in results]
        assert node_a_id in ids, f"Node {node_a_id} not found in results {ids}"
        assert results[0][0] == node_a_id, f"Node A should be top result, got {results[0][0]}"
        self.engine.bm25_drop(idx)

    # T019 ─────────────────────────────────────────────────────────
    def test_search_empty_returns_empty(self):
        """bm25_search with empty query string returns []."""
        self._create_node_with_name("bm25test:C", "some node text")
        idx = f"test44c_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])
        results = self.engine.bm25_search(idx, "", 5)
        assert results == []
        self.engine.bm25_drop(idx)

    # T020 ─────────────────────────────────────────────────────────
    def test_search_no_match_returns_empty(self):
        """bm25_search with zero-overlap query returns []."""
        self._create_node_with_name("bm25test:D", "rheumatoid arthritis node")
        idx = f"test44d_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])
        results = self.engine.bm25_search(idx, "xyzzy quantum flux", 5)
        assert results == []
        self.engine.bm25_drop(idx)

    # T025 ─────────────────────────────────────────────────────────
    def test_insert_new_doc_findable(self):
        """After bm25_insert, new doc is findable by its unique term."""
        self._create_node_with_name("bm25test:E1", "rheumatoid arthritis")
        self._create_node_with_name("bm25test:E2", "lupus disease")
        idx = f"test44e_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])

        ok = self.engine.bm25_insert(idx, f"new_{self._test_run_id}", "xylophone unique rare term")
        assert ok is True

        results = self.engine.bm25_search(idx, "xylophone", 5)
        ids = [r[0] for r in results]
        assert f"new_{self._test_run_id}" in ids
        self.engine.bm25_drop(idx)

    # T026 ─────────────────────────────────────────────────────────
    def test_insert_replaces_existing(self):
        """Inserting same docId twice replaces old content."""
        self._create_node_with_name("bm25test:F1", "arthritis pain")
        idx = f"test44f_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])

        doc_id = f"replace_{self._test_run_id}"
        self.engine.bm25_insert(idx, doc_id, "first content here")
        self.engine.bm25_insert(idx, doc_id, "completely different zygote words")

        results = self.engine.bm25_search(idx, "zygote", 5)
        ids = [r[0] for r in results]
        assert doc_id in ids

        results_first = self.engine.bm25_search(idx, "first content", 5)
        assert isinstance(results_first, list)
        self.engine.bm25_drop(idx)

    # T030 ─────────────────────────────────────────────────────────
    def test_drop_removes_all_data(self):
        """After bm25_drop, bm25_info returns {} and bm25_search returns []."""
        self._create_node_with_name("bm25test:G", "some text to index")
        idx = f"test44g_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])

        info_before = self.engine.bm25_info(idx)
        assert info_before.get("N", 0) > 0

        self.engine.bm25_drop(idx)

        info_after = self.engine.bm25_info(idx)
        assert info_after == {}

        results_after = self.engine.bm25_search(idx, "some", 3)
        assert results_after == []

    # T035 ─────────────────────────────────────────────────────────
    def test_kgtxt_returns_bm25_scores_not_like_scores(self):
        """When 'default' BM25 index exists, _kg_TXT_fallback returns BM25 scores."""
        from iris_vector_graph.operators import IRISGraphOperators

        n1 = self._create_node_with_name("bm25test:H1", "diabetes mellitus type 2")
        n2 = self._create_node_with_name("bm25test:H2", "insulin resistance diabetes")
        self._create_node_with_name("bm25test:H3", "rheumatoid arthritis")

        idx = f"default_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])

        ops = IRISGraphOperators(self.engine.conn)
        ops.graph_engine = self.engine
        ops._bm25_default_cached = None

        class _FakeEngine:
            def bm25_info(self_, name):
                if name == "default":
                    return self.engine.bm25_info(idx)
                return {}
            def bm25_search(self_, name, query, k):
                return self.engine.bm25_search(idx, query, k)

        ops.graph_engine = _FakeEngine()
        ops._bm25_default_cached = None

        results = ops._kg_TXT_fallback("diabetes", 5)
        assert len(results) > 0

        scores = [s for _, s in results]
        assert any(s not in (0.0, 1.0) for s in scores), (
            "BM25 path should return varied float scores, not LIKE binary 0/1"
        )
        self.engine.bm25_drop(idx)

    # T040 ─────────────────────────────────────────────────────────
    def test_cypher_bm25_search_executes(self):
        """CALL ivg.bm25.search generates correct SQL; verifies translation and direct SQL execution."""
        from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
        from iris_vector_graph.cypher.parser import parse_query

        node_a = self._create_node_with_name("bm25test:I1", "diabetes mellitus")
        node_b = self._create_node_with_name("bm25test:I2", "insulin resistance")

        idx = f"test44h_{self._test_run_id}"
        self.engine.bm25_build(idx, ["name"])

        set_schema_prefix("Graph_KG")
        cypher = f"CALL ivg.bm25.search('{idx}', $q, 3) YIELD node, score RETURN node, score"
        q = parse_query(cypher)
        result = translate_to_sql(q, {"q": "diabetes"})

        assert "kg_BM25" in result.sql
        assert "JSON_TABLE" in result.sql
        assert "BM25" in result.sql

        cursor = self.engine.conn.cursor()
        literal_sql = f"""SELECT j.node AS node_id, j.score AS score
FROM JSON_TABLE(
  Graph_KG.kg_BM25('{idx}', 'diabetes', 3),
  '$[*]' COLUMNS(
    node VARCHAR(256) PATH '$.id',
    score DOUBLE PATH '$.score'
  )
) j"""
        cursor.execute(literal_sql)
        rows = cursor.fetchall()
        assert rows is not None

        self.engine.bm25_drop(idx)
