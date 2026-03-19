"""Unit tests for Cypher ivg.neighbors, ivg.ppr, and ivg.vector.search Mode 3 (node ID)."""

import json
import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix


def _set_prefix():
    set_schema_prefix("Graph_KG")


class TestVectorSearchNodeId:

    def test_string_without_embedding_config_is_mode3(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', $query, 10) YIELD node, score "
            "RETURN node, score"
        )
        result = translate_to_sql(q, params={"query": "PMID:630"})
        sql = result.sql
        assert "SELECT e2.emb FROM" in sql
        assert "e2.id = ?" in sql
        assert "e.id != ?" in sql

    def test_string_with_embedding_config_is_mode2(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', $query, 10, {embedding_config: 'my-model'}) "
            "YIELD node, score RETURN node, score"
        )
        result = translate_to_sql(q, params={"query": "cancer immunotherapy"})
        sql = result.sql
        assert "EMBEDDING(?, ?)" in sql
        assert "e.id != ?" not in sql

    def test_list_is_still_mode1(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'emb', [0.1, 0.2], 5) YIELD node, score "
            "RETURN node, score"
        )
        result = translate_to_sql(q)
        sql = result.sql
        assert "TO_VECTOR(?, DOUBLE)" in sql


class TestNeighborsProcedure:

    def test_parse_basic(self):
        q = parse_query(
            "CALL ivg.neighbors($sources, 'MENTIONS', 'out') YIELD neighbor "
            "RETURN neighbor"
        )
        assert q.procedure_call is not None
        assert q.procedure_call.procedure_name == "ivg.neighbors"
        assert q.procedure_call.yield_items == ["neighbor"]

    def test_translate_out(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors($src, 'MENTIONS', 'out') YIELD neighbor RETURN neighbor"
        )
        result = translate_to_sql(q, params={"src": ["PMID:1", "PMID:2"]})
        sql = result.sql
        assert "Neighbors AS" in sql
        assert "e.s IN" in sql
        assert "e.o_id AS neighbor" in sql
        assert "e.p = ?" in sql
        assert "PMID:1" in result.parameters[0]

    def test_translate_in(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors($src, 'CITES', 'in') YIELD neighbor RETURN neighbor"
        )
        result = translate_to_sql(q, params={"src": ["PAPER:1"]})
        sql = result.sql
        assert "e.o_id IN" in sql
        assert "e.s AS neighbor" in sql

    def test_translate_both(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors($src, 'REL', 'both') YIELD neighbor RETURN neighbor"
        )
        result = translate_to_sql(q, params={"src": ["A"]})
        sql = result.sql
        assert "UNION" in sql

    def test_no_predicate(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors($src) YIELD neighbor RETURN neighbor"
        )
        result = translate_to_sql(q, params={"src": ["A"]})
        sql = result.sql
        assert "e.p = ?" not in sql

    def test_single_string_source(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors('PMID:630', 'MENTIONS') YIELD neighbor RETURN neighbor"
        )
        result = translate_to_sql(q)
        sql = result.sql
        assert "Neighbors AS" in sql

    def test_invalid_direction_raises(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.neighbors($src, 'REL', 'sideways') YIELD neighbor RETURN neighbor"
        )
        with pytest.raises(ValueError, match="direction"):
            translate_to_sql(q, params={"src": ["A"]})


class TestPPRProcedure:

    def test_parse_basic(self):
        q = parse_query(
            "CALL ivg.ppr($seeds, 0.85, 20) YIELD node, score RETURN node, score"
        )
        assert q.procedure_call.procedure_name == "ivg.ppr"
        assert q.procedure_call.yield_items == ["node", "score"]

    def test_translate_generates_json_table(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.ppr($seeds, 0.85, 20) YIELD node, score "
            "RETURN node, score ORDER BY score DESC LIMIT 10"
        )
        result = translate_to_sql(q, params={"seeds": ["ENT:A", "ENT:B"]})
        sql = result.sql
        assert "PPR AS" in sql
        assert "JSON_TABLE" in sql
        assert "kg_PPR" in sql
        assert "$.id" in sql
        assert "$.score" in sql
        params = result.parameters[0]
        seed_json = json.dumps(["ENT:A", "ENT:B"])
        assert seed_json in params

    def test_defaults_alpha_and_maxiter(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.ppr($seeds) YIELD node, score RETURN node, score"
        )
        result = translate_to_sql(q, params={"seeds": ["A"]})
        params = result.parameters[0]
        assert 0.85 in params
        assert 20 in params

    def test_score_marked_scalar(self):
        _set_prefix()
        q = parse_query(
            "CALL ivg.ppr($seeds, 0.85) YIELD node, score RETURN node, score"
        )
        # This verifies score doesn't get node-expanded in RETURN
        result = translate_to_sql(q, params={"seeds": ["A"]})
        assert result.sql is not None


class TestUnknownProcedure:

    def test_unknown_raises(self):
        q = parse_query(
            "CALL ivg.unknown_proc('arg') YIELD x RETURN x"
        )
        with pytest.raises(ValueError, match="Unknown procedure"):
            translate_to_sql(q)
