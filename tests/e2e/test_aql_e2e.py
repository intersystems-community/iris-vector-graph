import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.cypher.aql import translate_aql

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


class TestAQLBasicTraversal:

    def test_execute_aql_returns_ivgresult(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        result = engine.execute_aql(
            "FOR v IN 1..2 OUTBOUND @s GRAPH 'g' RETURN v._key",
            bind_vars={"s": "mesh:D003924"}
        )
        from iris_vector_graph.result import IVGResult
        assert isinstance(result, IVGResult)

    def test_execute_aql_no_error_on_empty_result(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        pfx = f"aql_test_{uuid.uuid4().hex[:8]}"
        result = engine.execute_aql(
            "FOR v IN 1..1 OUTBOUND @s GRAPH 'g' RETURN v._key",
            bind_vars={"s": pfx}
        )
        assert result.error is None or result.rows is not None

    def test_execute_aql_with_collection_list(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        result = engine.execute_aql(
            "FOR v IN 1..1 OUTBOUND @s rdf_edges RETURN v._key",
            bind_vars={"s": "mesh:D003924"}
        )
        from iris_vector_graph.result import IVGResult
        assert isinstance(result, IVGResult)


class TestTranslateAQL:

    def test_translate_aql_returns_cypher_string(self):
        cypher, params = translate_aql(
            "FOR v IN 1..3 OUTBOUND @start GRAPH 'proteins' RETURN v._key",
            bind_vars={"start": "proteins/TP53"}
        )
        assert "MATCH" in cypher
        assert params["start"] == "proteins/TP53"

    def test_translate_aql_passthrough_id_format(self):
        cypher, params = translate_aql(
            "FOR v IN 1..1 OUTBOUND @s g RETURN v",
            bind_vars={"s": "collection/key123"}
        )
        assert params["s"] == "collection/key123"
