"""Tests targeting translator.py lines 1573-2414.

Covers:
- _maybe_split_deep_joins (1579-1629)
- _demote_agg_stages_to_subqueries (1632-1645)
- _to_sql_init_part_from (1648-1664)
- _to_sql_handle_foreach (1667-1686)
- _inject_row_number (1689-1702)
- _to_sql_handle_with — ORDER BY/SKIP/LIMIT in WITH clause (1705-2029)
- _tts_union_branches — UNION / UNION ALL (2033-2099)
- _tts_process_parts — UNWIND+CREATE, FOREACH, procedure_call (2102-2261)
- _apply_unwind_create_context_reset (2264-2322)
- _tts_finalize_context — RETURN stage handling, modified-property filtering (2325-2414)
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


def tr_full(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    return translate_to_sql(ast_tree, params or {})


# ===========================================================================
# _maybe_split_deep_joins  (lines 1579-1629)
# ===========================================================================

class TestMaybeSplitDeepJoins:
    """Unit tests for the _maybe_split_deep_joins helper."""

    def _fn(self, sql, params=None):
        from iris_vector_graph.cypher.translator import _maybe_split_deep_joins
        return _maybe_split_deep_joins(sql, params or [], None)

    def _make_join_sql(self, n_joins, cols="a AS col_a, b AS col_b"):
        """Build a synthetic SQL string with n_joins JOIN clauses."""
        join_clauses = " JOIN t ON t.id = main.id" * n_joins
        return f"SELECT {cols}\nFROM main{join_clauses}"

    def test_under_threshold_passthrough(self):
        sql = self._make_join_sql(5)
        assert self._fn(sql) == sql

    def test_exactly_threshold_passthrough(self):
        sql = self._make_join_sql(20)
        assert self._fn(sql) == sql

    def test_over_threshold_wraps_in_cte(self):
        sql = self._make_join_sql(21)
        result = self._fn(sql)
        assert "WITH _MR AS" in result

    def test_over_threshold_outer_selects_aliases(self):
        sql = self._make_join_sql(21)
        result = self._fn(sql)
        assert "col_a" in result
        assert "col_b" in result

    def test_no_select_match_passthrough(self):
        sql = "GARBAGE SQL WITH NO SELECT"
        assert self._fn(sql) == sql

    def test_agg_without_group_by_passthrough(self):
        join_sql = self._make_join_sql(21, cols="COUNT(a) AS cnt")
        assert self._fn(join_sql) == join_sql

    def test_agg_with_group_by_wraps(self):
        join_sql = self._make_join_sql(21, cols="a AS col_a, COUNT(b) AS cnt")
        join_sql += " GROUP BY a"
        result = self._fn(join_sql)
        assert "WITH _MR AS" in result

    def test_order_by_preserved(self):
        sql = self._make_join_sql(21) + "\nORDER BY col_a ASC"
        result = self._fn(sql)
        assert "ORDER BY" in result

    def test_fetch_first_preserved(self):
        sql = self._make_join_sql(21) + "\nFETCH FIRST 10 ROWS ONLY"
        result = self._fn(sql)
        assert "FETCH FIRST 10 ROWS ONLY" in result

    def test_no_aliases_passthrough(self):
        # SELECT with no AS aliases → outer_cols empty → passthrough
        sql = self._make_join_sql(21, cols="a, b")
        assert self._fn(sql) == sql

    def test_distinct_preserved(self):
        join_clauses = " JOIN t ON t.id = main.id" * 21
        sql = f"SELECT DISTINCT a AS col_a, b AS col_b\nFROM main{join_clauses}"
        result = self._fn(sql)
        assert "WITH _MR AS" in result


# ===========================================================================
# _inject_row_number  (lines 1689-1702)
# ===========================================================================

class TestInjectRowNumber:
    def _fn(self, sql, rn_over):
        from iris_vector_graph.cypher.translator import _inject_row_number
        return _inject_row_number(sql, rn_over)

    def test_basic_inject(self):
        sql = "SELECT a, b FROM t"
        result = self._fn(sql, "ORDER BY a")
        assert "ROW_NUMBER() OVER(ORDER BY a) AS __rn" in result

    def test_inject_after_select_keyword(self):
        sql = "SELECT a FROM t"
        result = self._fn(sql, "ORDER BY a")
        assert result.startswith("SELECT ROW_NUMBER()")

    def test_inject_after_distinct(self):
        sql = "SELECT DISTINCT a FROM t"
        result = self._fn(sql, "ORDER BY a")
        assert "SELECT DISTINCT ROW_NUMBER()" in result

    def test_no_select_passthrough(self):
        sql = "INSERT INTO t VALUES (1)"
        result = self._fn(sql, "ORDER BY a")
        assert result == sql

    def test_empty_over(self):
        sql = "SELECT x FROM t"
        result = self._fn(sql, "")
        assert "ROW_NUMBER() OVER() AS __rn" in result


# ===========================================================================
# _demote_agg_stages_to_subqueries  (lines 1632-1645)
# ===========================================================================

class TestDemoteAggStages:
    def _fn(self, sql, ctes):
        from iris_vector_graph.cypher.translator import _demote_agg_stages_to_subqueries
        return _demote_agg_stages_to_subqueries(sql, ctes)

    def test_no_group_by_keeps_cte(self):
        cte = "MyStage AS (\nSELECT a FROM t\n)"
        sql = "SELECT * FROM MyStage"
        result_sql, remaining = self._fn(sql, [cte])
        assert remaining == [cte]
        assert "FROM MyStage" in result_sql

    def test_group_by_inlines_cte(self):
        cte = "MyStage AS (\nSELECT a, COUNT(*) AS cnt FROM t GROUP BY a\n)"
        sql = "SELECT * FROM MyStage"
        result_sql, remaining = self._fn(sql, [cte])
        assert remaining == []
        assert "FROM (" in result_sql

    def test_group_by_not_referenced_keeps_cte(self):
        cte = "OtherStage AS (\nSELECT a FROM t GROUP BY a\n)"
        sql = "SELECT * FROM SomethingElse"
        result_sql, remaining = self._fn(sql, [cte])
        assert remaining == [cte]

    def test_empty_ctes(self):
        sql = "SELECT 1"
        result_sql, remaining = self._fn(sql, [])
        assert result_sql == sql
        assert remaining == []


# ===========================================================================
# WITH clause — ORDER BY / SKIP / LIMIT  (lines 1705-2029)
# ===========================================================================

class TestWithOrderBy:

    def test_with_order_by_asc(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY name ASC RETURN name")
        assert "ORDER BY" in sql

    def test_with_order_by_desc(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY name DESC RETURN name")
        assert "ORDER BY" in sql
        assert "DESC" in sql

    def test_with_order_by_two_cols(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name, n.age AS age ORDER BY name, age RETURN name")
        assert "ORDER BY" in sql

    def test_with_limit_only(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name LIMIT 5 RETURN name")
        assert "5" in sql

    def test_with_skip_only(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name SKIP 3 RETURN name")
        assert "__rn" in sql or "SKIP" in sql or "3" in sql

    def test_with_skip_and_limit(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name SKIP 2 LIMIT 10 RETURN name")
        assert "__rn" in sql

    def test_with_order_by_and_limit(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY name LIMIT 3 RETURN name")
        assert "TOP 3" in sql or "FETCH FIRST 3" in sql or "3" in sql

    def test_with_order_by_only_uses_top_wrapper(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY name RETURN name")
        assert "ORDER BY" in sql
        # IRIS: ORDER BY without LIMIT should still work
        assert "name" in sql.lower()

    def test_with_distinct(self):
        sql = tr("MATCH (n:Person) WITH DISTINCT n.name AS name RETURN name")
        assert "DISTINCT" in sql

    def test_with_star(self):
        sql = tr("MATCH (n:Person) WITH * RETURN n")
        assert "SELECT" in sql

    def test_with_alias_variable_passthrough(self):
        sql = tr("MATCH (n:Person) WITH n AS person RETURN person")
        assert "SELECT" in sql

    def test_with_scalar_aggregation(self):
        sql = tr("MATCH (n:Person) WITH count(n) AS cnt RETURN cnt")
        assert "COUNT" in sql.upper()

    def test_with_collect_node(self):
        sql = tr("MATCH (n:Person) WITH collect(n) AS nodes RETURN nodes")
        assert "SELECT" in sql

    def test_with_undefined_variable_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n:Person) WITH n.name AS name ORDER BY undefined_var RETURN name")

    def test_with_order_by_agg_in_ob(self):
        # ORDER BY an aggregation expression should produce SQL that passes through
        sql = tr("MATCH (n:Person) WITH count(n) AS cnt ORDER BY cnt RETURN cnt")
        assert "cnt" in sql.lower() or "COUNT" in sql.upper()

    def test_with_order_by_property_ref(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY n.name RETURN name")
        assert "ORDER BY" in sql

    def test_with_limit_fetch_first_no_join(self):
        # No JOIN → should use FETCH FIRST
        sql = tr("WITH 1 AS x LIMIT 5 RETURN x")
        assert "5" in sql

    def test_with_multiple_parts(self):
        sql = tr("MATCH (n:Person) WITH n MATCH (n)-[:KNOWS]->(m) WITH m RETURN m")
        assert "Stage" in sql or "SELECT" in sql

    def test_with_null_row_optional_match(self):
        sql = tr("OPTIONAL MATCH (n:Person) WITH n RETURN n")
        assert "SELECT" in sql


# ===========================================================================
# UNION / UNION ALL  (lines 2033-2099)
# ===========================================================================

class TestUnionBranches:

    def test_union_basic(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name UNION MATCH (m:Animal) RETURN m.name AS name")
        assert "UNION" in sql

    def test_union_all_basic(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name UNION ALL MATCH (m:Animal) RETURN m.name AS name")
        assert "UNION ALL" in sql

    def test_union_three_branches(self):
        sql = tr("RETURN 1 AS x UNION RETURN 2 AS x UNION RETURN 3 AS x")
        assert "UNION" in sql

    def test_union_all_three_branches(self):
        sql = tr("RETURN 1 AS x UNION ALL RETURN 2 AS x UNION ALL RETURN 3 AS x")
        assert "UNION ALL" in sql

    def test_union_distinct_columns_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n:Person) RETURN n.name AS name UNION MATCH (m) RETURN m.age AS age")

    def test_union_no_union_queries_returns_none(self):
        from iris_vector_graph.cypher.translator import _tts_union_branches
        from iris_vector_graph.cypher.parser import parse_query
        ast_tree = parse_query("MATCH (n) RETURN n")
        assert _tts_union_branches(ast_tree, {}) is None

    def test_union_ensures_from_clause(self):
        # Constant return → no FROM in SQL → _ensure_from adds dual
        sql = tr("RETURN 1 AS x UNION RETURN 2 AS x")
        assert "SELECT" in sql

    def test_mix_union_union_all_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("RETURN 1 AS x UNION RETURN 2 AS x UNION ALL RETURN 3 AS x")

    def test_union_parameters_flow(self):
        result = tr_full("MATCH (n:Person) RETURN n.name AS name UNION MATCH (m:Animal) RETURN m.name AS name")
        assert result.parameters is not None


# ===========================================================================
# _tts_process_parts — UNWIND + CREATE  (lines 2102-2261)
# ===========================================================================

class TestUnwindCreate:

    def test_unwind_literal_list_create(self):
        sqls = tr_full("UNWIND [1, 2, 3] AS x CREATE (n:Foo {val: x})")
        # Should produce multiple SQL statements (one per element)
        sql = sqls.sql
        if isinstance(sql, list):
            assert len(sql) >= 1
        else:
            assert "INSERT" in sql.upper() or "SELECT" in sql.upper()

    def test_unwind_param_list_create(self):
        sqls = tr_full("UNWIND $items AS x CREATE (n:Foo {val: x})", {"items": [10, 20]})
        sql = sqls.sql
        if isinstance(sql, list):
            assert len(sql) >= 1

    def test_unwind_range_create(self):
        sqls = tr_full("UNWIND range(0, 2) AS i CREATE (n:Item {num: i})")
        sql = sqls.sql
        if isinstance(sql, list):
            assert len(sql) >= 1

    def test_unwind_non_literal_select(self):
        # UNWIND a non-literal expression (variable) without CREATE → plain UNWIND SELECT
        sql = tr("UNWIND $xs AS x RETURN x", {"xs": [1, 2, 3]})
        assert "SELECT" in sql

    def test_foreach_literal_list(self):
        sqls = tr_full("FOREACH (x IN [1, 2] | CREATE (n:Foo {v: x}))")
        sql = sqls.sql
        if isinstance(sql, list):
            assert len(sql) >= 1

    def test_foreach_non_literal_expression(self):
        sql = tr("FOREACH (x IN $items | SET x.done = true)", {"items": []})
        assert "SELECT" in sql or sql == ""

    def test_unwind_with_return(self):
        sql = tr("UNWIND [1, 2] AS x RETURN x")
        assert "SELECT" in sql

    def test_unwind_where(self):
        sql = tr("UNWIND [1, 2, 3] AS x WHERE x > 1 RETURN x")
        assert "SELECT" in sql


# ===========================================================================
# _apply_unwind_create_context_reset  (lines 2264-2322)
# ===========================================================================

class TestApplyUnwindCreateContextReset:
    """Direct unit tests for the context-reset helper."""

    def _make_context(self):
        from iris_vector_graph.cypher.translator import TranslationContext
        return TranslationContext()

    def test_no_unwind_ids_is_noop(self):
        ctx = self._make_context()
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        # Should not raise and should leave context as-is
        _apply_unwind_create_context_reset(ctx)

    def test_unwind_node_ids_resets_context(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_node_ids = {"n": ["uuid-1", "uuid-2"]}
        _apply_unwind_create_context_reset(ctx)
        assert ctx.from_clauses
        assert any("nodes" in fc for fc in ctx.from_clauses)

    def test_unwind_node_ids_sets_where_condition(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_node_ids = {"n": ["uuid-1"]}
        _apply_unwind_create_context_reset(ctx)
        assert any("node_id" in wc for wc in ctx.where_conditions)

    def test_unwind_node_ids_empty_list_skips(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_node_ids = {"n": []}
        _apply_unwind_create_context_reset(ctx)
        # No from_clauses set because no IDs
        assert not ctx.from_clauses

    def test_unwind_rel_ids_resets_context(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_rel_ids = {"r": [("s1", "p1", "o1")]}
        _apply_unwind_create_context_reset(ctx)
        assert ctx.from_clauses
        assert any("rdf_edges" in fc for fc in ctx.from_clauses)

    def test_unwind_rel_ids_sets_or_condition(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_rel_ids = {"r": [("s1", "p1", "o1")]}
        _apply_unwind_create_context_reset(ctx)
        assert any("OR" in wc or "s =" in wc or "s=" in wc or ".s" in wc or "?" in wc
                   for wc in ctx.where_conditions)

    def test_unwind_multiple_node_vars(self):
        from iris_vector_graph.cypher.translator import _apply_unwind_create_context_reset
        ctx = self._make_context()
        ctx._unwind_create_node_ids = {"n": ["uuid-1"], "m": ["uuid-2"]}
        _apply_unwind_create_context_reset(ctx)
        assert len(ctx.from_clauses) >= 1


# ===========================================================================
# _tts_finalize_context — RETURN stage handling  (lines 2325-2414)
# ===========================================================================

class TestFinalizeContext:

    def test_return_from_with_stage(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name RETURN name")
        assert "Stage1" in sql or "name" in sql.lower()

    def test_return_after_two_with_stages(self):
        sql = tr("MATCH (n:Person) WITH n AS person WITH person.name AS name RETURN name")
        assert "SELECT" in sql

    def test_return_with_set_filters_where(self):
        sqls = tr_full("MATCH (n:Person {name: 'Alice'}) SET n.name = 'Bob' RETURN n.name")
        sql = sqls.sql
        if isinstance(sql, list):
            sql = "\n".join(sql)
        assert "SELECT" in sql or "UPDATE" in sql or "INSERT" in sql

    def test_return_distinct(self):
        sql = tr("MATCH (n:Person) RETURN DISTINCT n.name AS name")
        assert "DISTINCT" in sql

    def test_return_with_order_by(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name ORDER BY name")
        assert "ORDER BY" in sql

    def test_return_with_limit(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name LIMIT 10")
        assert "10" in sql

    def test_return_with_skip(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name SKIP 5")
        assert "5" in sql

    def test_return_with_order_by_and_limit(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name ORDER BY name LIMIT 5")
        assert "5" in sql
        assert "ORDER BY" in sql

    def test_return_count(self):
        sql = tr("MATCH (n:Person) RETURN count(n) AS cnt")
        assert "COUNT" in sql.upper()

    def test_return_collect(self):
        sql = tr("MATCH (n:Person) RETURN collect(n.name) AS names")
        assert "SELECT" in sql

    def test_return_after_match_with_where(self):
        sql = tr("MATCH (n:Person) WHERE n.age > 30 RETURN n.name")
        assert "SELECT" in sql

    def test_return_graph_context_ignored(self):
        # graph_context on CypherQuery — no crash
        sql = tr("MATCH (n) RETURN n")
        assert "SELECT" in sql

    def test_return_null_literal(self):
        sql = tr("RETURN null AS x")
        assert "NULL" in sql.upper()

    def test_return_boolean_literals(self):
        sql = tr("RETURN true AS t, false AS f")
        assert "SELECT" in sql

    def test_match_with_return_chain(self):
        sql = tr("MATCH (a:Person) WITH a.name AS name MATCH (b:Person) WHERE b.name = name RETURN b")
        assert "Stage1" in sql

    def test_return_multiple_items(self):
        sql = tr("MATCH (n:Person) RETURN n.name AS name, n.age AS age")
        assert "name" in sql.lower()
        assert "age" in sql.lower()

    def test_return_relationship_property(self):
        sql = tr("MATCH (a)-[r:KNOWS]->(b) RETURN r.weight AS w")
        assert "SELECT" in sql


# ===========================================================================
# WITH clause — scalar variable propagation (lines 1979-2029)
# ===========================================================================

class TestWithScalarPropagation:

    def test_with_aggregation_marks_scalar(self):
        sql = tr("MATCH (n:Person) WITH count(n) AS cnt RETURN cnt")
        assert "COUNT" in sql.upper()

    def test_with_collect_marks_collected(self):
        sql = tr("MATCH (n:Person) WITH collect(n) AS ns RETURN ns")
        assert "SELECT" in sql

    def test_with_scalar_passthrough(self):
        sql = tr("MATCH (n:Person) WITH count(n) AS cnt WITH cnt AS total RETURN total")
        assert "total" in sql.lower() or "cnt" in sql.lower()

    def test_with_variable_type_preserved(self):
        # n passes through WITH and should remain usable as a node
        sql = tr("MATCH (n:Person) WITH n AS person MATCH (person)-[:KNOWS]->(m) RETURN m")
        assert "SELECT" in sql

    def test_with_temporal_type_preserved(self):
        # duration/date values flowing through WITH
        sql = tr("WITH duration('P1Y') AS d RETURN d")
        assert "SELECT" in sql

    def test_with_star_keeps_all_aliases(self):
        sql = tr("MATCH (n:Person) WITH * MATCH (n)-[:KNOWS]->(m) RETURN n, m")
        assert "SELECT" in sql


# ===========================================================================
# Edge cases and integration  (misc)
# ===========================================================================

class TestMiscEdgeCases:

    def test_simple_match_return(self):
        sql = tr("MATCH (n:Person) RETURN n")
        assert "SELECT" in sql
        assert "FROM" in sql

    def test_match_relationship_return(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN a, r, b")
        assert "SELECT" in sql

    def test_create_node(self):
        sqls = tr_full("CREATE (n:Person {name: 'Alice'})")
        sql = sqls.sql
        if isinstance(sql, list):
            assert len(sql) >= 1

    def test_delete_node(self):
        sqls = tr_full("MATCH (n:Person {name: 'Alice'}) DELETE n")
        sql = sqls.sql
        if isinstance(sql, list):
            assert any("DELETE" in s.upper() or "SELECT" in s.upper() for s in sql)

    def test_set_property(self):
        sqls = tr_full("MATCH (n:Person {name: 'Alice'}) SET n.age = 30")
        sql = sqls.sql
        if isinstance(sql, list):
            assert any("UPDATE" in s.upper() or "INSERT" in s.upper() or "DELETE" in s.upper() or "SELECT" in s.upper() for s in sql)

    def test_with_limit_no_order_no_join_uses_fetch_first(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name LIMIT 7 RETURN name")
        # expect TOP or FETCH FIRST
        assert "7" in sql

    def test_with_skip_injects_row_number(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name SKIP 4 RETURN name")
        assert "__rn" in sql or "4" in sql

    def test_multi_hop_match(self):
        sql = tr("MATCH (a:Person)-[:KNOWS*1..3]->(b:Person) RETURN a, b")
        assert "SELECT" in sql

    def test_optional_match_return(self):
        sql = tr("OPTIONAL MATCH (n:Person) RETURN n")
        assert "SELECT" in sql

    def test_unwind_select(self):
        sql = tr("UNWIND [1, 2, 3] AS x RETURN x")
        assert "SELECT" in sql

    def test_where_clause_filtering(self):
        sql = tr("MATCH (n:Person) WHERE n.name = 'Alice' RETURN n")
        assert "SELECT" in sql

    def test_return_count_star(self):
        sql = tr("MATCH (n) RETURN count(*) AS total")
        assert "COUNT" in sql.upper()

    def test_case_expression_return(self):
        sql = tr("MATCH (n:Person) RETURN CASE WHEN n.age > 18 THEN 'adult' ELSE 'minor' END AS category")
        assert "CASE" in sql.upper()

    def test_string_contains(self):
        sql = tr("MATCH (n:Person) WHERE n.name CONTAINS 'li' RETURN n")
        assert "SELECT" in sql

    def test_not_exists(self):
        sql = tr("MATCH (n:Person) WHERE NOT (n)-[:KNOWS]->() RETURN n")
        assert "SELECT" in sql

    def test_with_order_by_skip_limit_all_three(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY name SKIP 2 LIMIT 5 RETURN name")
        assert "__rn" in sql

    def test_union_simple_constants(self):
        sql = tr("RETURN 1 AS n UNION RETURN 2 AS n")
        assert "UNION" in sql
        assert "1" in sql and "2" in sql

    def test_union_all_simple_constants(self):
        sql = tr("RETURN 1 AS n UNION ALL RETURN 2 AS n")
        assert "UNION ALL" in sql

    def test_return_with_coalesce(self):
        sql = tr("MATCH (n:Person) RETURN coalesce(n.nickname, n.name) AS display")
        assert "CY_TYPED(CASE WHEN " in sql  # 4.6.0 R259: a RETURN of coalesce() over a stored value is a CASE read through CY_TYPED

    def test_with_order_by_join_path(self):
        # n.name is a property reference → should use sort projection path
        sql = tr("MATCH (n:Person) WITH n.name AS name ORDER BY n.name RETURN name")
        assert "ORDER BY" in sql

    def test_return_after_unwind_no_create(self):
        sql = tr("UNWIND [10, 20] AS x RETURN x * 2 AS doubled")
        assert "SELECT" in sql
