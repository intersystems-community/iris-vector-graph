"""Comprehensive translator coverage test — drives all major code paths without IRIS."""
import pytest
from unittest.mock import MagicMock


def _translate(cypher, params=None):
    from iris_vector_graph.cypher.parser import Parser
    from iris_vector_graph.cypher.lexer import Lexer
    from iris_vector_graph.cypher.translator import translate_to_sql
    parsed = Parser(Lexer(cypher)).parse()
    return translate_to_sql(parsed, params or {})


class TestTranslatorCoverage:

    def test_simple_match_return(self):
        r = _translate("MATCH (n) RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_match_with_label(self):
        r = _translate("MATCH (n:Gene) RETURN n.node_id")
        assert "Gene" in r.sql or "?" in r.sql

    def test_match_two_labels_or(self):
        r = _translate("MATCH (n:Gene|Disease) RETURN n.node_id LIMIT 10")
        assert "SELECT" in r.sql

    def test_match_with_property_filter(self):
        r = _translate("MATCH (n {id: 'x'}) RETURN n.node_id")
        assert "x" in r.sql or "?" in r.sql

    def test_where_equals(self):
        r = _translate("MATCH (n) WHERE n.name = 'TP53' RETURN n.node_id")
        assert "TP53" in r.sql or "?" in r.sql

    def test_where_not_equals(self):
        r = _translate("MATCH (n) WHERE n.name <> 'TP53' RETURN n.node_id")
        assert "<>" in r.sql

    def test_where_and(self):
        r = _translate("MATCH (n) WHERE n.name = 'A' AND n.type = 'B' RETURN n.node_id")
        assert "AND" in r.sql

    def test_where_or(self):
        r = _translate("MATCH (n) WHERE n.name = 'A' OR n.name = 'B' RETURN n.node_id")
        assert "OR" in r.sql

    def test_where_not(self):
        r = _translate("MATCH (n) WHERE NOT n.name = 'A' RETURN n.node_id")
        assert "NOT" in r.sql or "!" in r.sql or "<>" in r.sql

    def test_where_is_null(self):
        r = _translate("MATCH (n) WHERE n.name IS NULL RETURN n.node_id")
        assert "IS NULL" in r.sql

    def test_where_is_not_null(self):
        r = _translate("MATCH (n) WHERE n.name IS NOT NULL RETURN n.node_id")
        assert "IS NOT NULL" in r.sql

    def test_where_starts_with(self):
        r = _translate("MATCH (n) WHERE n.name STARTS WITH 'TP' RETURN n.node_id")
        assert "LIKE" in r.sql

    def test_where_ends_with(self):
        r = _translate("MATCH (n) WHERE n.name ENDS WITH 'P53' RETURN n.node_id")
        assert "LIKE" in r.sql

    def test_where_contains(self):
        r = _translate("MATCH (n) WHERE n.name CONTAINS '53' RETURN n.node_id")
        assert "LIKE" in r.sql

    def test_where_regex(self):
        r = _translate("MATCH (n) WHERE n.name =~ 'TP.*' RETURN n.node_id")
        assert "REGEX" in r.sql.upper() or "regex" in r.sql.lower()

    def test_where_in_integers(self):
        r = _translate("MATCH (n) WHERE n.score IN [1, 2, 3] RETURN n.node_id")
        assert "IN" in r.sql

    def test_where_in_strings(self):
        r = _translate('MATCH (n) WHERE n.pmid IN ["a", "b"] RETURN n.node_id')
        assert "IN" in r.sql

    def test_where_gt_lt(self):
        r = _translate("MATCH (n) WHERE n.score > 0.5 AND n.score < 1.0 RETURN n.node_id")
        assert ">" in r.sql
        assert "<" in r.sql

    def test_order_by_asc(self):
        r = _translate("MATCH (n) RETURN n.node_id ORDER BY n.node_id ASC")
        assert "ORDER BY" in r.sql

    def test_order_by_desc(self):
        r = _translate("MATCH (n) RETURN n.node_id ORDER BY n.node_id DESC")
        assert "DESC" in r.sql

    def test_limit(self):
        r = _translate("MATCH (n) RETURN n.node_id LIMIT 10")
        assert "10" in r.sql

    def test_skip(self):
        r = _translate("MATCH (n) RETURN n.node_id SKIP 5 LIMIT 10")
        assert "5" in r.sql

    def test_distinct(self):
        r = _translate("MATCH (n) RETURN DISTINCT n.node_id")
        assert "DISTINCT" in r.sql

    def test_count_aggregate(self):
        r = _translate("MATCH (n) RETURN count(n) AS cnt")
        assert "COUNT" in r.sql.upper()

    def test_sum_aggregate(self):
        r = _translate("MATCH (n) RETURN sum(n.score) AS total")
        assert "SUM" in r.sql.upper()

    def test_min_max_aggregate(self):
        r = _translate("MATCH (n) RETURN min(n.score) AS mn, max(n.score) AS mx")
        assert "MIN" in r.sql.upper()
        assert "MAX" in r.sql.upper()

    def test_collect_aggregate(self):
        r = _translate("MATCH (n) RETURN collect(n.node_id) AS ids")
        assert "ARRAYAGG" in r.sql.upper() or "collect" in r.sql.lower()

    def test_one_hop_outbound(self):
        r = _translate("MATCH (a)-[r]->(b) RETURN a.node_id, b.node_id")
        assert "JOIN" in r.sql

    def test_one_hop_inbound(self):
        r = _translate("MATCH (a)<-[r]-(b) RETURN a.node_id, b.node_id")
        assert "JOIN" in r.sql

    def test_one_hop_undirected(self):
        r = _translate("MATCH (a)-[r]-(b) RETURN a.node_id, b.node_id")
        assert "JOIN" in r.sql

    def test_relationship_type_filter(self):
        r = _translate("MATCH (a)-[r:INTERACTS]->(b) RETURN a.node_id, b.node_id")
        assert "JOIN" in r.sql and ("INTERACTS" in r.sql or "?" in r.sql)

    def test_two_hop(self):
        r = _translate("MATCH (a)-[r1]->(b)-[r2]->(c) RETURN a.node_id, c.node_id")
        assert "JOIN" in r.sql

    def test_var_length_path(self):
        r = _translate("MATCH (a)-[*1..3]->(b) RETURN a.node_id, b.node_id LIMIT 10")
        assert "SELECT" in r.sql

    def test_named_path(self):
        r = _translate("MATCH p = (a)-[r]->(b) RETURN p")
        assert "SELECT" in r.sql

    def test_length_of_named_path(self):
        r = _translate("MATCH p = (a)-[r]->(b) RETURN length(p)")
        assert "1" in r.sql

    def test_case_when(self):
        r = _translate("MATCH (n) RETURN CASE WHEN n.score > 0.5 THEN 'high' ELSE 'low' END AS tier")
        assert "CASE" in r.sql.upper()

    def test_with_clause(self):
        r = _translate("MATCH (n) WITH n.node_id AS id RETURN id")
        assert "SELECT" in r.sql

    def test_with_where(self):
        r = _translate("MATCH (n)-[r]->() WITH n.node_id AS id, count(r) AS deg WHERE deg > 1 RETURN id, deg")
        assert "SELECT" in r.sql

    def test_unwind(self):
        r = _translate("UNWIND [1, 2, 3] AS x RETURN x")
        assert "SELECT" in r.sql

    def test_list_comprehension(self):
        r = _translate("MATCH (n) RETURN [x IN collect(n.node_id) WHERE x STARTS WITH 'mesh'] AS filtered")
        assert "SELECT" in r.sql

    def test_exists_subquery(self):
        r = _translate("MATCH (n) WHERE EXISTS { MATCH (n)-[:INTERACTS]->(m) } RETURN n.node_id")
        assert "EXISTS" in r.sql.upper()

    def test_string_functions(self):
        r = _translate("MATCH (n) RETURN toUpper(n.name), toLower(n.name), trim(n.name)")
        assert "UPPER" in r.sql.upper() or "toUpper" in r.sql

    def test_numeric_functions(self):
        r = _translate("MATCH (n) RETURN abs(n.score), round(n.score), sqrt(n.score)")
        assert "ABS" in r.sql.upper() or "abs" in r.sql.lower()

    def test_type_conversion(self):
        r = _translate("MATCH (n) RETURN toInteger(n.count), toFloat(n.score), toString(n.id)")
        assert "SELECT" in r.sql

    def test_labels_function(self):
        r = _translate("MATCH (n) RETURN labels(n)")
        assert "SELECT" in r.sql

    def test_size_function(self):
        r = _translate("MATCH (n) RETURN size(n.name)")
        assert "SELECT" in r.sql

    def test_node_id_function(self):
        r = _translate("MATCH (n) RETURN id(n)")
        assert "SELECT" in r.sql

    def test_type_function(self):
        r = _translate("MATCH (a)-[r]->(b) RETURN type(r)")
        assert "SELECT" in r.sql

    def test_union(self):
        r = _translate("MATCH (n:Gene) RETURN n.node_id UNION MATCH (n:Disease) RETURN n.node_id")
        assert "UNION" in r.sql

    def test_union_all(self):
        r = _translate("MATCH (n:Gene) RETURN n.node_id UNION ALL MATCH (n:Disease) RETURN n.node_id")
        assert "UNION ALL" in r.sql

    def test_params_substituted(self):
        r = _translate("MATCH (n) WHERE n.node_id = $id RETURN n.node_id", {"id": "mesh:D003924"})
        assert "?" in r.sql or "mesh:D003924" in r.sql

    def test_list_param_in(self):
        r = _translate("MATCH (n) WHERE n.pmid IN $ids RETURN n.node_id", {"ids": ["a", "b"]})
        assert "IN" in r.sql

    def test_optional_match(self):
        r = _translate("OPTIONAL MATCH (n)-[r]->(m) RETURN n.node_id, m.node_id")
        assert "LEFT" in r.sql or "OUTER" in r.sql

    def test_create_node(self):
        r = _translate("CREATE (n:Gene {id: 'test', name: 'TP53'})")
        sql_text = r.sql if isinstance(r.sql, str) else " ".join(r.sql)
        assert "INSERT" in sql_text.upper() or "SELECT" in sql_text

    def test_set_property(self):
        r = _translate("MATCH (n) WHERE n.node_id = 'x' SET n.score = 0.9")
        sql_text = r.sql if isinstance(r.sql, str) else " ".join(r.sql)
        assert "SELECT" in sql_text or "UPDATE" in sql_text.upper()

    def test_delete_node(self):
        r = _translate("MATCH (n) WHERE n.node_id = 'x' DELETE n")
        sql_text = r.sql if isinstance(r.sql, str) else " ".join(r.sql)
        assert "SELECT" in sql_text or "DELETE" in sql_text.upper()

    def test_merge(self):
        r = _translate("MERGE (n:Gene {id: 'TP53'}) ON CREATE SET n.source = 'test'")
        sql_text = r.sql if isinstance(r.sql, str) else " ".join(r.sql)
        assert "INSERT" in sql_text.upper() or "SELECT" in sql_text

    def test_where_xor(self):
        r = _translate("MATCH (n) WHERE n.name = 'A' XOR n.name = 'B' RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_arithmetic_add(self):
        r = _translate("MATCH (n) RETURN n.score + 1.0 AS boosted")
        assert "+" in r.sql

    def test_arithmetic_multiply(self):
        r = _translate("MATCH (n) RETURN n.score * 2.0 AS doubled")
        assert "*" in r.sql

    def test_string_concat(self):
        r = _translate("MATCH (n) RETURN n.name + ':' + n.node_id AS full_id")
        assert "SELECT" in r.sql

    def test_map_literal(self):
        r = _translate("MATCH (n) RETURN {id: n.node_id, score: 0.9} AS info")
        assert "SELECT" in r.sql

    def test_call_vector_search(self):
        r = _translate("CALL ivg.vector.search('Gene', 'emb', $vec, 10) YIELD node, score RETURN node, score",
                       {"vec": [0.1, 0.2]})
        assert "VecSearch" in r.sql or "VECTOR" in r.sql.upper()

    def test_call_neighbors(self):
        r = _translate("CALL ivg.neighbors($ids, 'INTERACTS', 'out') YIELD neighbor RETURN neighbor",
                       {"ids": ["n1"]})
        assert "SELECT" in r.sql

    def test_call_bm25_search(self):
        r = _translate("CALL ivg.bm25.search('idx', 'test', 5) YIELD node, score RETURN node, score")
        assert "BM25" in r.sql

    def test_call_ppr(self):
        r = _translate("CALL ivg.ppr(['seed1'], 0.85, 10) YIELD node, score RETURN node, score")
        assert "PPR" in r.sql

    def test_where_params(self):
        r = _translate("MATCH (n) WHERE n.score > $threshold RETURN n.node_id", {"threshold": 0.7})
        assert "?" in r.sql

    def test_reduce_expression(self):
        r = _translate("MATCH (n) RETURN reduce(s = 0, x IN [1,2,3] | s + x) AS total")
        assert "SELECT" in r.sql

    def test_case_when_no_else(self):
        r = _translate("MATCH (n) RETURN CASE n.type WHEN 'Gene' THEN 1 WHEN 'Disease' THEN 2 END AS code")
        assert "CASE" in r.sql.upper()

    def test_where_relationship_property(self):
        r = _translate("MATCH (a)-[r:INTERACTS]->(b) WHERE r.weight > 0.5 RETURN a.node_id, b.node_id")
        assert "0.5" in r.sql or "?" in r.sql

    def test_all_list_predicate(self):
        r = _translate("MATCH (n) WHERE ALL(x IN [1, 2, 3] WHERE x > 0) RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_any_list_predicate(self):
        r = _translate("MATCH (n) WHERE ANY(x IN [1, 2, 3] WHERE x > 2) RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_none_list_predicate(self):
        r = _translate("MATCH (n) WHERE NONE(x IN [1, 2, 3] WHERE x > 5) RETURN n.node_id")
        assert "SELECT" in r.sql


class TestTranslatorEdgeCases:

    def test_empty_where_params_list(self):
        r = _translate("MATCH (n) WHERE n.id IN [] RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_null_literal(self):
        r = _translate("MATCH (n) WHERE n.name IS NULL RETURN n.node_id")
        assert "NULL" in r.sql

    def test_boolean_literal_true(self):
        r = _translate("MATCH (n) WHERE n.active = true RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_boolean_literal_false(self):
        r = _translate("MATCH (n) WHERE n.deleted = false RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_integer_literal(self):
        r = _translate("MATCH (n) WHERE n.count = 42 RETURN n.node_id")
        assert "42" in r.sql or "?" in r.sql

    def test_float_literal(self):
        r = _translate("MATCH (n) WHERE n.score = 3.14 RETURN n.node_id")
        assert "SELECT" in r.sql

    def test_multiple_return_items(self):
        r = _translate("MATCH (n) RETURN n.node_id, n.name, n.score")
        assert "SELECT" in r.sql

    def test_alias_in_return(self):
        r = _translate("MATCH (n) RETURN n.node_id AS id, n.name AS nm")
        assert "AS" in r.sql

    def test_group_by_with_agg(self):
        r = _translate("MATCH (n)-[r]->() RETURN n.node_id, count(r) AS deg ORDER BY deg DESC LIMIT 5")
        assert "COUNT" in r.sql.upper()
        assert "ORDER BY" in r.sql

    def test_shortest_path_call(self):
        r = _translate("CALL ivg.shortestPath.weighted('n1', 'n2', 'weight', 5) YIELD path, totalCost RETURN path, totalCost")
        assert "SELECT" in r.sql

    def test_multi_part_with_agg(self):
        r = _translate("MATCH (n)-[r]->() WITH n, count(r) AS deg WHERE deg > 1 RETURN n.node_id, deg")
        assert "SELECT" in r.sql
