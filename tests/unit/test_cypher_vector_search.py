"""
Unit tests for Cypher CALL ivg.vector.search(...) YIELD node, score

Tests cover:
- Lexer: CALL and YIELD tokens
- Parser: procedure call parsing, dotted names, options map
- Translator: VecSearch CTE SQL generation (Mode 1 and Mode 2)
- Error cases: bad arguments, unknown similarity, missing embedding_config

No IRIS connection required.
"""

import json
import pytest

from iris_vector_graph.cypher.lexer import Lexer, TokenType
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql, translate_procedure_call, TranslationContext, set_schema_prefix
from iris_vector_graph.cypher.ast import Literal, Variable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_prefix():
    set_schema_prefix("Graph_KG")


# ---------------------------------------------------------------------------
# Lexer tests
# ---------------------------------------------------------------------------

class TestLexer:
    def test_call_token(self):
        tokens = Lexer("CALL ivg.vector.search").tokens
        kinds = [t.kind for t in tokens]
        assert TokenType.CALL in kinds

    def test_yield_token(self):
        tokens = Lexer("YIELD node, score").tokens
        kinds = [t.kind for t in tokens]
        assert TokenType.YIELD in kinds

    def test_call_case_insensitive(self):
        tokens = Lexer("call ivg.vector.search").tokens
        assert tokens[0].kind == TokenType.CALL

    def test_yield_case_insensitive(self):
        tokens = Lexer("yield node").tokens
        assert tokens[0].kind == TokenType.YIELD


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_basic_call_parsed(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'embedding', [0.1, 0.2], 5) YIELD node, score"
        )
        assert q.procedure_call is not None
        pc = q.procedure_call
        assert pc.procedure_name == "ivg.vector.search"
        assert len(pc.arguments) == 4
        assert pc.yield_items == ["node", "score"]

    def test_dotted_procedure_name(self):
        q = parse_query(
            "CALL ivg.vector.search('Drug', 'emb', [0.5], 10) YIELD node, score"
        )
        assert q.procedure_call.procedure_name == "ivg.vector.search"

    def test_options_map_parsed(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5, {similarity: 'dot_product'}) YIELD node, score"
        )
        opts = q.procedure_call.options
        assert "similarity" in opts
        assert opts["similarity"].value == "dot_product"

    def test_no_options_defaults_empty(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5) YIELD node, score"
        )
        assert q.procedure_call.options == {}

    def test_call_with_subsequent_return(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5) YIELD node, score "
            "RETURN node, score"
        )
        assert q.procedure_call is not None
        assert q.return_clause is not None

    def test_vector_args_are_list(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1, 0.2, 0.3], 5) YIELD node, score"
        )
        vec_arg = q.procedure_call.arguments[2]
        assert isinstance(vec_arg, Literal)
        assert isinstance(vec_arg.value, list)


# ---------------------------------------------------------------------------
# Translator tests
# ---------------------------------------------------------------------------

class TestTranslator:
    def setup_method(self):
        _set_prefix()

    def test_mode1_produces_vecsearch_cte(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'embedding', [0.1, 0.2], 5) YIELD node, score "
            "RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        assert "VecSearch AS" in sql_q.sql
        assert "VECTOR_COSINE" in sql_q.sql
        assert "TO_VECTOR(?)" in sql_q.sql
        assert "kg_NodeEmbeddings" in sql_q.sql

    def test_mode1_label_filter_in_sql(self):
        q = parse_query(
            "CALL ivg.vector.search('Drug', 'embedding', [0.5], 3) YIELD node, score "
            "RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        params = sql_q.parameters[0]
        assert "Drug" in params

    def test_mode1_limit_in_cte(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 10) YIELD node, score "
            "RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        assert "SELECT TOP 10" in sql_q.sql

    def test_dot_product_similarity(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5, {similarity: 'dot_product'}) "
            "YIELD node, score RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        assert "VECTOR_DOT_PRODUCT" in sql_q.sql
        assert "VECTOR_COSINE" not in sql_q.sql

    def test_score_selected_as_scalar(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5) YIELD node, score "
            "RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        # score should be selected as VecSearch.score, not expanded as a node
        assert "VecSearch.score AS score" in sql_q.sql
        # node should expand with labels/props
        assert "node_labels" in sql_q.sql

    def test_order_by_score_desc_in_cte(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1], 5) YIELD node, score "
            "RETURN node, score"
        )
        sql_q = translate_to_sql(q)
        # Extract the VecSearch CTE body — it ends at the matching closing paren
        # The CTE is the first WITH item before the final SELECT
        cte_start = sql_q.sql.index("VecSearch AS (") + len("VecSearch AS (")
        cte_body = sql_q.sql[cte_start:sql_q.sql.index("\n)", cte_start)]
        assert "ORDER BY score DESC" in cte_body

    def test_unknown_similarity_raises(self):
        # Options are resolved in translate_procedure_call — build it directly
        from iris_vector_graph.cypher.ast import CypherProcedureCall, Literal
        proc = CypherProcedureCall(
            procedure_name="ivg.vector.search",
            arguments=[Literal("Gene"), Literal("emb"), Literal([Literal(0.1)]), Literal(5)],
            yield_items=["node", "score"],
            options={"similarity": Literal("bad_value")},
        )
        ctx = TranslationContext()
        with pytest.raises(ValueError, match="similarity"):
            translate_procedure_call(proc, ctx)

    def test_too_few_args_raises(self):
        from iris_vector_graph.cypher.ast import CypherProcedureCall, Literal
        proc = CypherProcedureCall(
            procedure_name="ivg.vector.search",
            arguments=[Literal("Gene"), Literal("emb"), Literal([])],  # only 3 args
            yield_items=["node", "score"],
        )
        ctx = TranslationContext()
        with pytest.raises(ValueError, match="at least 4 arguments"):
            translate_procedure_call(proc, ctx)

    def test_mode2_missing_embedding_config_raises(self):
        from iris_vector_graph.cypher.ast import CypherProcedureCall, Literal
        proc = CypherProcedureCall(
            procedure_name="ivg.vector.search",
            arguments=[Literal("Gene"), Literal("emb"), Literal("some text"), Literal(5)],
            yield_items=["node", "score"],
            options={},  # no embedding_config
        )
        ctx = TranslationContext()
        with pytest.raises(ValueError, match="embedding_config"):
            translate_procedure_call(proc, ctx)

    def test_mode2_sql_uses_embedding_function(self):
        from iris_vector_graph.cypher.ast import CypherProcedureCall, Literal
        proc = CypherProcedureCall(
            procedure_name="ivg.vector.search",
            arguments=[Literal("Gene"), Literal("emb"), Literal("flu symptoms"), Literal(5)],
            yield_items=["node", "score"],
            options={"embedding_config": Literal("my_config")},
        )
        ctx = TranslationContext()
        translate_procedure_call(proc, ctx)
        cte_sql = ctx.stages[0]
        assert "EMBEDDING(?, ?)" in cte_sql
        assert "flu symptoms" in ctx.all_stage_params
        assert "my_config" in ctx.all_stage_params
