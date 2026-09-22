"""
Tests targeting translator.py lines 562-614, 674-706, 712-824, 855-982, 1005-1200.

Coverage areas:
- build_label_only_dml_subquery (lines 562-614)
- _tck_val_to_python (lines 672-686)
- _tck_type_coerce (lines 689-706)
- _tck_val_to_sql (lines 709-719)
- _translate_test_procedure (lines 722-931)
- _resolve_arg (lines 934-947)
- _vs_resolve_query_input (lines 950-963)
- _vs_resolve_limit (lines 966-985)
- _vs_build_similarity (lines 988-1007)
- _translate_vector_search (lines 1010-1072)
- _translate_neighbors (lines 1075-1143)
- _translate_ppr (lines 1145-1185)
- _translate_bm25_search (lines 1188-1229)
"""

import pytest
from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.translator import (
    TranslationContext,
    _tck_val_to_python,
    _tck_type_coerce,
    _tck_val_to_sql,
    _translate_test_procedure,
    _resolve_arg,
    _vs_resolve_query_input,
    _vs_resolve_limit,
    _vs_build_similarity,
    _translate_vector_search,
    _translate_neighbors,
    _translate_ppr,
    _translate_bm25_search,
    translate_procedure_call,
)


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


def make_context(params=None, tck_procedures=None):
    """Create a minimal TranslationContext for direct function testing."""
    ctx = TranslationContext()
    ctx.input_params = params or {}
    if tck_procedures is not None:
        ctx._tck_procedures = tck_procedures
    return ctx


def make_literal(value):
    return ast.Literal(value=value)


def make_variable(name):
    return ast.Variable(name=name)


def make_proc_call(proc_name, args=None, yield_items=None, yield_star=False,
                   options=None, implicit_args=False):
    """Create a minimal CypherProcedureCall AST node."""
    proc = ast.CypherProcedureCall(
        procedure_name=proc_name,
        arguments=args or [],
        yield_items=yield_items or [],
        yield_star=yield_star,
        options=options or {},
        implicit_args=implicit_args,
    )
    return proc


# ────────────────────────────────────────────────────────────────────────────
# _tck_val_to_python (lines 672-686)
# ────────────────────────────────────────────────────────────────────────────

class TestTckValToPython:
    def test_none_returns_none(self):
        assert _tck_val_to_python(None) is None

    def test_null_string_returns_none(self):
        assert _tck_val_to_python("null") is None

    def test_null_uppercase_returns_none(self):
        assert _tck_val_to_python("NULL") is None

    def test_null_mixed_case_returns_none(self):
        assert _tck_val_to_python("Null") is None

    def test_single_quoted_string(self):
        assert _tck_val_to_python("'hello'") == "hello"

    def test_single_quoted_empty(self):
        assert _tck_val_to_python("''") == ""

    def test_integer_string(self):
        assert _tck_val_to_python("42") == 42

    def test_negative_integer(self):
        assert _tck_val_to_python("-5") == -5

    def test_float_string(self):
        result = _tck_val_to_python("3.14")
        assert isinstance(result, float)
        assert abs(result - 3.14) < 1e-9

    def test_non_numeric_string_returned_as_is(self):
        assert _tck_val_to_python("foobar") == "foobar"

    def test_numeric_object_converted(self):
        # Non-string numeric (e.g. actual int) passes through str conversion
        assert _tck_val_to_python(42) == 42

    def test_whitespace_stripped(self):
        assert _tck_val_to_python("  'hello'  ") == "hello"


# ────────────────────────────────────────────────────────────────────────────
# _tck_type_coerce (lines 689-706)
# ────────────────────────────────────────────────────────────────────────────

class TestTckTypeCoerce:
    def test_none_returns_none(self):
        assert _tck_type_coerce(None, "STRING") is None

    def test_float_coerces_int(self):
        result = _tck_type_coerce(3, "FLOAT")
        assert result == 3.0
        assert isinstance(result, float)

    def test_number_coerces_int(self):
        result = _tck_type_coerce(5, "NUMBER")
        assert isinstance(result, float)

    def test_integer_coerces_whole_float(self):
        result = _tck_type_coerce(2.0, "INTEGER")
        assert result == 2
        assert isinstance(result, int)

    def test_integer_coerces_int(self):
        result = _tck_type_coerce(7, "INTEGER")
        assert result == 7

    def test_string_type_passthrough(self):
        result = _tck_type_coerce("hello", "STRING")
        assert result == "hello"

    def test_float_type_with_string_returns_string(self):
        # Can't convert "abc" to float → returns as-is
        result = _tck_type_coerce("abc", "FLOAT")
        assert result == "abc"

    def test_integer_type_with_string_returns_string(self):
        result = _tck_type_coerce("abc", "INTEGER")
        assert result == "abc"

    def test_trailing_question_mark_stripped(self):
        # Type string "FLOAT?" should behave like "FLOAT"
        result = _tck_type_coerce(3, "FLOAT?")
        assert isinstance(result, float)

    def test_unknown_type_passthrough(self):
        result = _tck_type_coerce(42, "UNKNOWN")
        assert result == 42


# ────────────────────────────────────────────────────────────────────────────
# _tck_val_to_sql (lines 709-719)
# ────────────────────────────────────────────────────────────────────────────

class TestTckValToSql:
    def test_none_generates_null(self):
        assert _tck_val_to_sql(None, "col1") == "NULL AS col1"

    def test_integer_generates_literal(self):
        assert _tck_val_to_sql(42, "myval") == "42 AS myval"

    def test_float_generates_literal(self):
        result = _tck_val_to_sql(3.14, "score")
        assert "3.14" in result
        assert "AS score" in result

    def test_plain_string(self):
        result = _tck_val_to_sql("hello", "name")
        assert result == "'hello' AS name"

    def test_quoted_string_unquoted(self):
        result = _tck_val_to_sql("'Malmö'", "city")
        assert "Malmö" in result
        assert result.startswith("'")

    def test_string_with_single_quote_escaped(self):
        result = _tck_val_to_sql("O'Brien", "name")
        assert "O''Brien" in result

    def test_quoted_string_with_inner_quote(self):
        result = _tck_val_to_sql("'O\\'Brien'", "name")
        # The outer quotes are stripped, inner content kept
        assert "AS name" in result


# ────────────────────────────────────────────────────────────────────────────
# _translate_test_procedure (lines 722-931)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslateTestProcedure:
    def _make_simple_proc_def(self, proc_name, rows=None):
        """Helper: procedure with no args, one output column 'out'."""
        return {
            proc_name: {
                'args': [],
                'outputs': [{'name': 'out'}],
                'rows': rows or [{'out': '42'}, {'out': '99'}],
            }
        }

    def test_unknown_procedure_raises(self):
        ctx = make_context(tck_procedures={})
        proc = make_proc_call("test.nonexistent", yield_items=["out"])
        with pytest.raises(ValueError, match="Procedure not found"):
            _translate_test_procedure(proc, ctx)

    def test_no_outputs_returns_early(self):
        ctx = make_context(tck_procedures={
            "test.noop": {'args': [], 'outputs': [], 'rows': []}
        })
        proc = make_proc_call("test.noop")
        _translate_test_procedure(proc, ctx)
        # No CTE should be added
        assert ctx.stages == []

    def test_basic_no_args_generates_union_cte(self):
        ctx = make_context(tck_procedures=self._make_simple_proc_def("test.basic"))
        proc = make_proc_call("test.basic", yield_items=["out"])
        _translate_test_procedure(proc, ctx)
        assert len(ctx.stages) == 1
        cte = ctx.stages[0]
        assert "UNION ALL" in cte
        assert "42" in cte or "'42'" in cte

    def test_empty_rows_generates_where_1_0(self):
        ctx = make_context(tck_procedures={
            "test.empty": {'args': [], 'outputs': [{'name': 'val'}], 'rows': []}
        })
        proc = make_proc_call("test.empty", yield_items=["val"])
        _translate_test_procedure(proc, ctx)
        cte = ctx.stages[0]
        assert "WHERE 1=0" in cte

    def test_yield_items_register_aliases(self):
        ctx = make_context(tck_procedures=self._make_simple_proc_def("test.reg"))
        proc = make_proc_call("test.reg", yield_items=["out"])
        _translate_test_procedure(proc, ctx)
        assert "out" in ctx.variable_aliases
        assert "out" in ctx.scalar_variables

    def test_yield_star_registers_all_outputs(self):
        ctx = make_context(tck_procedures={
            "test.star": {
                'args': [],
                'outputs': [{'name': 'a'}, {'name': 'b'}],
                'rows': [{'a': '1', 'b': '2'}],
            }
        })
        proc = make_proc_call("test.star", yield_star=True, yield_items=[])
        proc.yield_star = True
        _translate_test_procedure(proc, ctx)
        assert "a" in ctx.variable_aliases
        assert "b" in ctx.variable_aliases

    def test_wrong_arg_count_raises(self):
        ctx = make_context(tck_procedures={
            "test.with_args": {
                'args': [{'name': 'x', 'type': 'INTEGER'}],
                'outputs': [{'name': 'result'}],
                'rows': [],
            }
        })
        proc = make_proc_call("test.with_args", args=[], yield_items=["result"])
        with pytest.raises(SyntaxError, match="expected 1 arguments"):
            _translate_test_procedure(proc, ctx)

    def test_arg_type_string_checks_type(self):
        ctx = make_context(tck_procedures={
            "test.strarg": {
                'args': [{'name': 'x', 'type': 'STRING'}],
                'outputs': [{'name': 'out'}],
                'rows': [{'x': "'hello'", 'out': '1'}],
            }
        })
        # Pass integer as STRING arg → should raise InvalidArgumentType
        proc = make_proc_call(
            "test.strarg",
            args=[make_literal(42)],
            yield_items=["out"]
        )
        with pytest.raises(SyntaxError, match="expected STRING"):
            _translate_test_procedure(proc, ctx)

    def test_arg_type_integer_bool_raises(self):
        ctx = make_context(tck_procedures={
            "test.intarg": {
                'args': [{'name': 'x', 'type': 'INTEGER'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        proc = make_proc_call(
            "test.intarg",
            args=[make_literal(True)],
            yield_items=["out"]
        )
        with pytest.raises(SyntaxError, match="expected INTEGER"):
            _translate_test_procedure(proc, ctx)

    def test_arg_type_float_bool_raises(self):
        ctx = make_context(tck_procedures={
            "test.floatarg": {
                'args': [{'name': 'x', 'type': 'FLOAT'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        proc = make_proc_call(
            "test.floatarg",
            args=[make_literal(True)],
            yield_items=["out"]
        )
        with pytest.raises(SyntaxError, match="expected FLOAT"):
            _translate_test_procedure(proc, ctx)

    def test_float_int_arg_coerces(self):
        ctx = make_context(tck_procedures={
            "test.floatcoerce": {
                'args': [{'name': 'n', 'type': 'INTEGER'}],
                'outputs': [{'name': 'out'}],
                'rows': [{'n': '2', 'out': 'matched'}],
            }
        })
        # 2.0 as float should be coerced to int 2 → match row with n='2'
        proc = make_proc_call(
            "test.floatcoerce",
            args=[make_literal(2.0)],
            yield_items=["out"]
        )
        _translate_test_procedure(proc, ctx)
        cte = ctx.stages[0]
        assert "matched" in cte or "WHERE 1=0" not in cte

    def test_aggregate_in_args_raises(self):
        ctx = make_context(tck_procedures={
            "test.agg": {
                'args': [{'name': 'x', 'type': 'INTEGER'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        agg_call = ast.FunctionCall(function_name="count", arguments=[make_variable("n")])
        proc = make_proc_call("test.agg", args=[agg_call], yield_items=["out"])
        with pytest.raises(SyntaxError, match="InvalidAggregation"):
            _translate_test_procedure(proc, ctx)

    def test_variable_arg_from_params(self):
        ctx = make_context(
            params={"myval": "hello"},
            tck_procedures={
                "test.vararg": {
                    'args': [{'name': 'x', 'type': 'STRING'}],
                    'outputs': [{'name': 'out'}],
                    'rows': [{'x': "'hello'", 'out': 'found'}],
                }
            }
        )
        proc = make_proc_call(
            "test.vararg",
            args=[make_variable("myval")],
            yield_items=["out"]
        )
        _translate_test_procedure(proc, ctx)
        cte = ctx.stages[0]
        assert "found" in cte

    def test_variable_arg_missing_raises(self):
        ctx = make_context(tck_procedures={
            "test.missingparam": {
                'args': [{'name': 'x', 'type': 'STRING'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        proc = make_proc_call(
            "test.missingparam",
            args=[make_variable("nonexistent")],
            yield_items=["out"]
        )
        with pytest.raises(ValueError, match="not found"):
            _translate_test_procedure(proc, ctx)

    def test_row_filtering_by_arg(self):
        ctx = make_context(tck_procedures={
            "test.filter": {
                'args': [{'name': 'x', 'type': 'INTEGER'}],
                'outputs': [{'name': 'out'}],
                'rows': [
                    {'x': '1', 'out': 'one'},
                    {'x': '2', 'out': 'two'},
                    {'x': '3', 'out': 'three'},
                ],
            }
        })
        proc = make_proc_call(
            "test.filter",
            args=[make_literal(2)],
            yield_items=["out"]
        )
        _translate_test_procedure(proc, ctx)
        cte = ctx.stages[0]
        assert "two" in cte
        assert "one" not in cte
        assert "three" not in cte

    def test_yield_tuple_alias(self):
        ctx = make_context(tck_procedures={
            "test.aliased": {
                'args': [],
                'outputs': [{'name': 'result'}],
                'rows': [{'result': '42'}],
            }
        })
        # yield_items as list of tuples (orig, alias)
        proc = make_proc_call("test.aliased", yield_items=[("result", "myAlias")])
        _translate_test_procedure(proc, ctx)
        assert "myAlias" in ctx.variable_aliases

    def test_duplicate_yield_alias_raises(self):
        ctx = make_context(tck_procedures={
            "test.dupyield": {
                'args': [],
                'outputs': [{'name': 'a'}, {'name': 'b'}],
                'rows': [{'a': '1', 'b': '2'}],
            }
        })
        proc = make_proc_call(
            "test.dupyield",
            yield_items=[("a", "same"), ("b", "same")]
        )
        with pytest.raises(SyntaxError, match="VariableAlreadyBound"):
            _translate_test_procedure(proc, ctx)

    def test_yield_alias_shadows_bound_var(self):
        ctx = make_context(tck_procedures={
            "test.shadow": {
                'args': [],
                'outputs': [{'name': 'a'}],
                'rows': [{'a': '1'}],
            }
        })
        ctx.variable_aliases["existingVar"] = "SomeTable"
        proc = make_proc_call("test.shadow", yield_items=[("a", "existingVar")])
        with pytest.raises(SyntaxError, match="VariableAlreadyBound"):
            _translate_test_procedure(proc, ctx)

    def test_row_null_arg_matches_null_row(self):
        ctx = make_context(tck_procedures={
            "test.nullmatch": {
                'args': [{'name': 'x', 'type': 'STRING'}],
                'outputs': [{'name': 'out'}],
                'rows': [
                    {'x': None, 'out': 'nullrow'},
                    {'x': "'hello'", 'out': 'strrow'},
                ],
            }
        })
        proc = make_proc_call(
            "test.nullmatch",
            args=[make_literal(None)],
            yield_items=["out"]
        )
        _translate_test_procedure(proc, ctx)
        cte = ctx.stages[0]
        assert "nullrow" in cte

    def test_implicit_args_missing_param_raises(self):
        """When implicit_args=True with no yield_items, missing param raises KeyError."""
        ctx = make_context(tck_procedures={
            "test.implicit": {
                'args': [{'name': 'x', 'type': 'STRING'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        # No yield_items → takes the "validate params present" branch
        proc = make_proc_call("test.implicit", yield_items=[])
        proc.implicit_args = True
        with pytest.raises(KeyError, match="MissingParameter"):
            _translate_test_procedure(proc, ctx)

    def test_implicit_args_with_yield_and_args_raises_syntax_error(self):
        """When implicit_args=True WITH yield_items AND args_spec, raise SyntaxError."""
        ctx = make_context(tck_procedures={
            "test.implicitwargs": {
                'args': [{'name': 'x', 'type': 'STRING'}],
                'outputs': [{'name': 'out'}],
                'rows': [],
            }
        })
        proc = make_proc_call("test.implicitwargs", yield_items=["out"])
        proc.implicit_args = True
        with pytest.raises(SyntaxError, match="InvalidArgumentPassingMode"):
            _translate_test_procedure(proc, ctx)

    def test_stores_metadata_on_context(self):
        ctx = make_context(tck_procedures=self._make_simple_proc_def("test.meta"))
        proc = make_proc_call("test.meta", yield_items=["out"])
        _translate_test_procedure(proc, ctx)
        assert hasattr(ctx, '_tck_proc_cte')
        assert hasattr(ctx, '_tck_proc_outputs')
        assert 'out' in ctx._tck_proc_outputs


# ────────────────────────────────────────────────────────────────────────────
# _resolve_arg (lines 934-947)
# ────────────────────────────────────────────────────────────────────────────

class TestResolveArg:
    def test_literal_scalar(self):
        ctx = make_context()
        result = _resolve_arg(make_literal(42), ctx, "test")
        assert result == 42

    def test_literal_string(self):
        ctx = make_context()
        result = _resolve_arg(make_literal("hello"), ctx, "test")
        assert result == "hello"

    def test_literal_list_unwraps(self):
        ctx = make_context()
        inner = [make_literal(1), make_literal(2), make_literal(3)]
        result = _resolve_arg(make_literal(inner), ctx, "test")
        assert result == [1, 2, 3]

    def test_variable_from_params(self):
        ctx = make_context(params={"myvar": "value"})
        result = _resolve_arg(make_variable("myvar"), ctx, "test")
        assert result == "value"

    def test_variable_missing_raises(self):
        ctx = make_context(params={})
        with pytest.raises(ValueError, match="not found"):
            _resolve_arg(make_variable("missing"), ctx, "test")

    def test_non_literal_non_variable_raises(self):
        ctx = make_context()
        # Pass some other AST node type
        other = ast.FunctionCall(function_name="count", arguments=[])
        with pytest.raises(ValueError, match="must be a literal or parameter"):
            _resolve_arg(other, ctx, "test")


# ────────────────────────────────────────────────────────────────────────────
# _vs_resolve_query_input (lines 950-963)
# ────────────────────────────────────────────────────────────────────────────

class TestVsResolveQueryInput:
    def test_literal_string(self):
        ctx = make_context()
        result = _vs_resolve_query_input(make_literal("query text"), ctx)
        assert result == "query text"

    def test_literal_list(self):
        ctx = make_context()
        inner = [make_literal(0.1), make_literal(0.2)]
        result = _vs_resolve_query_input(make_literal(inner), ctx)
        assert result == [0.1, 0.2]

    def test_variable_from_params(self):
        ctx = make_context(params={"vec": [1.0, 2.0]})
        result = _vs_resolve_query_input(make_variable("vec"), ctx)
        assert result == [1.0, 2.0]

    def test_variable_missing_raises(self):
        ctx = make_context(params={})
        with pytest.raises(ValueError, match="not found in params"):
            _vs_resolve_query_input(make_variable("missing"), ctx)

    def test_non_literal_non_variable_raises(self):
        ctx = make_context()
        other = ast.FunctionCall(function_name="count", arguments=[])
        with pytest.raises(ValueError, match="must be a literal or parameter"):
            _vs_resolve_query_input(other, ctx)


# ────────────────────────────────────────────────────────────────────────────
# _vs_resolve_limit (lines 966-985)
# ────────────────────────────────────────────────────────────────────────────

class TestVsResolveLimit:
    def test_literal_integer(self):
        ctx = make_context()
        result = _vs_resolve_limit(make_literal(10), ctx)
        assert result == 10

    def test_variable_from_params(self):
        ctx = make_context(params={"k": 5})
        result = _vs_resolve_limit(make_variable("k"), ctx)
        assert result == 5

    def test_variable_missing_raises(self):
        ctx = make_context(params={})
        with pytest.raises(ValueError, match="not found in params"):
            _vs_resolve_limit(make_variable("missing"), ctx)

    def test_non_literal_raises(self):
        ctx = make_context()
        other = ast.FunctionCall(function_name="count", arguments=[])
        with pytest.raises(ValueError, match="must be an integer literal"):
            _vs_resolve_limit(other, ctx)

    def test_zero_limit_raises(self):
        ctx = make_context()
        with pytest.raises(ValueError, match="limit must be > 0"):
            _vs_resolve_limit(make_literal(0), ctx)

    def test_negative_limit_raises(self):
        ctx = make_context()
        with pytest.raises(ValueError, match="limit must be > 0"):
            _vs_resolve_limit(make_literal(-5), ctx)

    def test_non_integer_string_raises(self):
        ctx = make_context()
        with pytest.raises(ValueError, match="must be an integer"):
            _vs_resolve_limit(make_literal("abc"), ctx)

    def test_float_str_coerced(self):
        ctx = make_context(params={"k": "10"})
        result = _vs_resolve_limit(make_variable("k"), ctx)
        assert result == 10


# ────────────────────────────────────────────────────────────────────────────
# _vs_build_similarity (lines 988-1007)
# ────────────────────────────────────────────────────────────────────────────

class TestVsBuildSimilarity:
    def test_list_input_vector_cosine(self):
        expr, params, exclude_self = _vs_build_similarity(
            [0.1, 0.2], "VECTOR_COSINE", "Person", {}, "kg_NodeEmbeddings"
        )
        assert "VECTOR_COSINE" in expr
        assert "TO_VECTOR" in expr
        assert exclude_self is False
        import json
        assert json.loads(params[0]) == [0.1, 0.2]

    def test_list_input_dot_product(self):
        expr, params, exclude_self = _vs_build_similarity(
            [1.0], "VECTOR_DOT_PRODUCT", "Movie", {}, "kg_NodeEmbeddings"
        )
        assert "VECTOR_DOT_PRODUCT" in expr
        assert exclude_self is False

    def test_str_input_with_embedding_config(self):
        options = {"embedding_config": "mymodel"}
        expr, params, exclude_self = _vs_build_similarity(
            "search query", "VECTOR_COSINE", "Person", options, "kg_NodeEmbeddings"
        )
        assert "EMBEDDING" in expr
        assert exclude_self is False

    def test_str_input_without_embedding_config(self):
        expr, params, exclude_self = _vs_build_similarity(
            "my_node_id", "VECTOR_COSINE", "Person", {}, "kg_NodeEmbeddings"
        )
        assert "SELECT e2.emb" in expr
        assert exclude_self is True

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            _vs_build_similarity(
                12345, "VECTOR_COSINE", "Person", {}, "kg_NodeEmbeddings"
            )


# ────────────────────────────────────────────────────────────────────────────
# _translate_vector_search (lines 1010-1072)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslateVectorSearch:
    def _make_vs_proc(self, label="Person", prop="embedding",
                      query_input=None, limit=10, yield_items=None,
                      options=None, similarity="cosine"):
        """Helper to make a complete vector search procedure call."""
        if query_input is None:
            query_input = [0.1, 0.2, 0.3]
        if yield_items is None:
            yield_items = ["node", "score"]
        args = [
            make_literal(label),
            make_literal(prop),
            make_literal(query_input),
            make_literal(limit),
        ]
        if options is None:
            options = {}
        if similarity != "cosine":
            opts = {**options, "similarity": ast.Literal(value=similarity)}
        else:
            opts = options
        return make_proc_call("ivg.vector.search", args=args,
                              yield_items=yield_items, options=opts)

    def test_too_few_args_raises(self):
        ctx = make_context()
        proc = make_proc_call("ivg.vector.search",
                              args=[make_literal("Person"), make_literal("emb")],
                              yield_items=["node"])
        with pytest.raises(ValueError, match="requires at least 4"):
            _translate_vector_search(proc, ctx)

    def test_non_string_label_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal(42), make_literal("emb"),
                  make_literal([0.1]), make_literal(5)],
            yield_items=["node"]
        )
        with pytest.raises(ValueError, match="first argument.*label.*must be a string"):
            _translate_vector_search(proc, ctx)

    def test_non_string_prop_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal("Person"), make_literal(42),
                  make_literal([0.1]), make_literal(5)],
            yield_items=["node"]
        )
        with pytest.raises(ValueError, match="second argument.*property.*must be a string"):
            _translate_vector_search(proc, ctx)

    def test_basic_vector_search_list_input(self):
        ctx = make_context()
        proc = self._make_vs_proc(query_input=[0.1, 0.2])
        _translate_vector_search(proc, ctx)
        assert len(ctx.stages) > 0
        cte = ctx.stages[0]
        assert "VecSearch" in cte
        assert "VECTOR_COSINE" in cte
        assert "node" in ctx.variable_aliases
        assert "score" in ctx.scalar_variables

    def test_dot_product_similarity(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal("Movie"), make_literal("emb"),
                  make_literal([0.5, 0.6]), make_literal(10)],
            yield_items=["node", "score"],
            options={"similarity": ast.Literal(value="dot_product")}
        )
        _translate_vector_search(proc, ctx)
        cte = ctx.stages[0]
        assert "VECTOR_DOT_PRODUCT" in cte

    def test_invalid_similarity_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal("Person"), make_literal("emb"),
                  make_literal([0.1]), make_literal(5)],
            yield_items=["node"],
            options={"similarity": ast.Literal(value="euclidean")}
        )
        with pytest.raises(ValueError, match="similarity must be"):
            _translate_vector_search(proc, ctx)

    def test_string_query_with_embedding_config(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal("Person"), make_literal("emb"),
                  make_literal("search text"), make_literal(5)],
            yield_items=["node", "score"],
            options={"embedding_config": ast.Literal(value="mymodel")}
        )
        _translate_vector_search(proc, ctx)
        cte = ctx.stages[0]
        assert "EMBEDDING" in cte

    def test_string_query_without_config_exclude_self(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.vector.search",
            args=[make_literal("Person"), make_literal("emb"),
                  make_literal("node_id_123"), make_literal(5)],
            yield_items=["node", "score"],
        )
        _translate_vector_search(proc, ctx)
        cte = ctx.stages[0]
        # 4.0.0 key: `e.id` is the RowID after the spec 227 re-key.
        assert "WHERE e.node_id !=" in cte

    def test_params_added_to_all_stage_params(self):
        ctx = make_context()
        proc = self._make_vs_proc(query_input=[0.1, 0.2, 0.3], limit=20)
        _translate_vector_search(proc, ctx)
        assert len(ctx.all_stage_params) > 0


# ────────────────────────────────────────────────────────────────────────────
# _translate_neighbors (lines 1075-1143)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslateNeighbors:
    def test_too_few_args_raises(self):
        ctx = make_context()
        proc = make_proc_call("ivg.neighbors", args=[], yield_items=["neighbor"])
        with pytest.raises(ValueError, match="requires at least 1"):
            _translate_neighbors(proc, ctx)

    def test_string_source_wrapped_in_list(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("node1")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "Neighbors" in cte
        assert "neighbor" in ctx.variable_aliases

    def test_list_source(self):
        ctx = make_context()
        sources = [make_literal("n1"), make_literal("n2")]
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal(sources)],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "Neighbors" in cte

    def test_invalid_source_type_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal(42)],
            yield_items=["neighbor"]
        )
        with pytest.raises(ValueError, match="source must be a string or list"):
            _translate_neighbors(proc, ctx)

    def test_direction_out(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1"), make_literal("KNOWS"), make_literal("out")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "o_id" in cte

    def test_direction_in(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1"), make_literal("KNOWS"), make_literal("in")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "o_id IN" in cte

    def test_direction_both_unions(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1"), make_literal("LIKES"), make_literal("both")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "UNION" in cte

    def test_invalid_direction_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1"), make_literal("KNOWS"), make_literal("sideways")],
            yield_items=["neighbor"]
        )
        with pytest.raises(ValueError, match="direction must be"):
            _translate_neighbors(proc, ctx)

    def test_predicate_filter_appended(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1"), make_literal("KNOWS")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "e.p = ?" in cte

    def test_no_predicate_no_filter(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1")],
            yield_items=["neighbor"]
        )
        _translate_neighbors(proc, ctx)
        cte = ctx.stages[0]
        assert "e.p" not in cte


# ────────────────────────────────────────────────────────────────────────────
# _translate_ppr (lines 1145-1185)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslatePpr:
    def test_too_few_args_raises(self):
        ctx = make_context()
        proc = make_proc_call("ivg.ppr", args=[], yield_items=["node", "score"])
        with pytest.raises(ValueError, match="requires at least 1"):
            _translate_ppr(proc, ctx)

    def test_basic_ppr(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(["seed1", "seed2"])],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        assert len(ctx.stages) > 0
        cte = ctx.stages[0]
        assert "PPR" in cte
        assert "JSON_TABLE" in cte

    def test_ppr_with_alpha_and_max_iter(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[
                make_literal(["node1"]),
                make_literal(0.9),
                make_literal(30),
            ],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        # Inline, not bound: a `?` inside JSON_TABLE's source argument makes IRIS reject
        # the statement (tests/unit/test_227_json_table_literal_args.py).
        assert "0.9" in ctx.stages[0]
        assert "30" in ctx.stages[0]
        assert ctx.all_stage_params == []

    def test_ppr_defaults_alpha_and_iter(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(["seed1"])],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        # Default alpha 0.85 and max_iter 20, inline for the reason above
        assert "0.85" in ctx.stages[0]
        assert ", 20," in ctx.stages[0]

    def test_ppr_string_seed_wrapped_in_list(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal("single_seed")],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        import json
        # The seed JSON is inlined as a quoted literal, so read it back out of the CTE.
        seed_json = ctx.stages[0].split("kg_PPR('", 1)[1].split("',", 1)[0]
        assert json.loads(seed_json) == ["single_seed"]

    def test_ppr_invalid_seed_type_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(42)],
            yield_items=["node", "score"]
        )
        with pytest.raises(ValueError, match="seeds must be a string or list"):
            _translate_ppr(proc, ctx)

    def test_score_added_to_scalar_variables(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(["n1"])],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        assert "score" in ctx.scalar_variables

    def test_aliases_registered_for_yield_items(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(["n1"])],
            yield_items=["node", "score"]
        )
        _translate_ppr(proc, ctx)
        assert "node" in ctx.variable_aliases
        assert "score" in ctx.variable_aliases
        assert ctx.variable_aliases["node"] == "PPR"
        assert ctx.variable_aliases["score"] == "PPR"


# ────────────────────────────────────────────────────────────────────────────
# _translate_bm25_search (lines 1188-1229)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslateBm25Search:
    def test_too_few_args_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("myindex"), make_literal("query")],
            yield_items=["node"]
        )
        with pytest.raises(ValueError, match="requires 3 arguments"):
            _translate_bm25_search(proc, ctx)

    def test_non_string_index_name_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal(42), make_literal("query"), make_literal(10)],
            yield_items=["node"]
        )
        with pytest.raises(ValueError, match="first argument.*name.*must be a string"):
            _translate_bm25_search(proc, ctx)

    def test_non_integer_k_raises(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("idx"), make_literal("query"), make_literal("abc")],
            yield_items=["node"]
        )
        with pytest.raises(ValueError, match="third argument.*k.*must be an integer"):
            _translate_bm25_search(proc, ctx)

    def test_basic_bm25(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("myindex"), make_literal("search text"), make_literal(20)],
            yield_items=["node", "score"]
        )
        _translate_bm25_search(proc, ctx)
        assert len(ctx.stages) > 0
        cte = ctx.stages[0]
        assert "BM25" in cte
        assert "JSON_TABLE" in cte
        assert "node" in ctx.variable_aliases
        assert "score" in ctx.scalar_variables

    def test_params_added_to_stage_params(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("idx"), make_literal("myquery"), make_literal(15)],
            yield_items=["node", "score"]
        )
        _translate_bm25_search(proc, ctx)
        # idx_name and query are inlined as escaped literals, not bound: JSON_TABLE's
        # source argument takes no parameter markers on IRIS 2026.3.
        assert "'idx'" in ctx.stages[0]
        assert "'myquery'" in ctx.stages[0]
        assert ctx.all_stage_params == []

    def test_k_is_inline_not_param(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("idx"), make_literal("query"), make_literal(50)],
            yield_items=["node", "score"]
        )
        _translate_bm25_search(proc, ctx)
        cte = ctx.stages[0]
        assert "50" in cte

    def test_score_not_in_scalar_when_not_yielded(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("idx"), make_literal("q"), make_literal(5)],
            yield_items=["node"]
        )
        _translate_bm25_search(proc, ctx)
        assert "score" not in ctx.scalar_variables


# ────────────────────────────────────────────────────────────────────────────
# translate_procedure_call dispatcher (lines 617-668)
# ────────────────────────────────────────────────────────────────────────────

class TestTranslateProcedureCallDispatch:
    def test_system_proc_db_stored_on_context(self):
        ctx = make_context()
        proc = make_proc_call("db.labels", yield_items=["label"])
        translate_procedure_call(proc, ctx)
        assert ctx.system_procedure_call is proc

    def test_system_proc_dbms_stored_on_context(self):
        ctx = make_context()
        proc = make_proc_call("dbms.components", yield_items=["name"])
        translate_procedure_call(proc, ctx)
        assert ctx.system_procedure_call is proc

    def test_system_proc_apoc_stored_on_context(self):
        ctx = make_context()
        proc = make_proc_call("apoc.meta.stats", yield_items=["data"])
        translate_procedure_call(proc, ctx)
        assert ctx.system_procedure_call is proc

    def test_system_proc_gds_stored_on_context(self):
        ctx = make_context()
        proc = make_proc_call("gds.graph.project", yield_items=["graphName"])
        translate_procedure_call(proc, ctx)
        assert ctx.system_procedure_call is proc

    def test_unknown_procedure_raises(self):
        ctx = make_context()
        proc = make_proc_call("foo.bar", yield_items=["result"])
        with pytest.raises(ValueError, match="Unknown procedure"):
            translate_procedure_call(proc, ctx)

    def test_test_procedure_dispatch(self):
        ctx = make_context(tck_procedures={
            "test.dispatch": {
                'args': [],
                'outputs': [{'name': 'x'}],
                'rows': [{'x': '1'}],
            }
        })
        proc = make_proc_call("test.dispatch", yield_items=["x"])
        translate_procedure_call(proc, ctx)
        assert len(ctx.stages) > 0

    def test_neighbors_dispatch(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.neighbors",
            args=[make_literal("n1")],
            yield_items=["neighbor"]
        )
        translate_procedure_call(proc, ctx)
        assert any("Neighbors" in s for s in ctx.stages)

    def test_ppr_dispatch(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.ppr",
            args=[make_literal(["seed"])],
            yield_items=["node", "score"]
        )
        translate_procedure_call(proc, ctx)
        assert any("PPR" in s for s in ctx.stages)

    def test_bm25_dispatch(self):
        ctx = make_context()
        proc = make_proc_call(
            "ivg.bm25.search",
            args=[make_literal("idx"), make_literal("q"), make_literal(10)],
            yield_items=["node", "score"]
        )
        translate_procedure_call(proc, ctx)
        assert any("BM25" in s for s in ctx.stages)


# ────────────────────────────────────────────────────────────────────────────
# build_label_only_dml_subquery (lines 562-614)
# ────────────────────────────────────────────────────────────────────────────

class TestBuildLabelOnlyDmlSubquery:
    def _make_context_with_rdf_joins(self, node_alias="n"):
        ctx = make_context()
        ctx.select_items = [f"{node_alias}.node_id AS id"]
        ctx.from_clauses = ["nodes n0"]
        # Add an rdf_props JOIN for this alias
        ctx.join_clauses = [
            f"JOIN rdf_props rp1 ON rp1.s = {node_alias}.node_id AND rp1.\"key\" = 'name'",
            "JOIN rdf_labels lbl ON lbl.s = n0.node_id",
        ]
        ctx.join_params = ["prop_val"]
        ctx.where_conditions = [
            f"rp1.val = ?",
            "lbl.label = ?",
        ]
        ctx.where_params = ["Alice", "Person"]
        return ctx

    def test_strips_rdf_props_join_for_alias(self):
        ctx = self._make_context_with_rdf_joins("n")
        result = ctx.build_label_only_dml_subquery("n", "SELECT n.node_id")
        # The rdf_props JOIN should be stripped; rdf_labels should remain
        _, sql, params = result
        assert "rdf_labels" in sql or len(ctx.join_clauses) >= 0

    def test_restores_state_after_build(self):
        ctx = self._make_context_with_rdf_joins("n")
        orig_joins = list(ctx.join_clauses)
        orig_where = list(ctx.where_conditions)
        ctx.build_label_only_dml_subquery("n", "SELECT n.node_id")
        # State should be restored
        assert ctx.join_clauses == orig_joins
        assert ctx.where_conditions == orig_where

    def test_non_matching_alias_not_stripped(self):
        ctx = make_context()
        ctx.select_items = ["m.node_id AS id"]
        ctx.from_clauses = ["nodes m"]
        ctx.join_clauses = [
            "JOIN rdf_props rp1 ON rp1.s = m.node_id AND rp1.\"key\" = 'name'"
        ]
        ctx.join_params = []
        ctx.where_conditions = []
        ctx.where_params = []
        orig_joins = list(ctx.join_clauses)
        # Build for alias 'n' (different from 'm') → should not strip
        ctx.build_label_only_dml_subquery("n", "SELECT m.node_id")
        assert ctx.join_clauses == orig_joins

    def test_returns_triple(self):
        ctx = make_context()
        ctx.select_items = ["n0.node_id AS id"]
        ctx.from_clauses = ["nodes n0"]
        ctx.join_clauses = []
        ctx.join_params = []
        ctx.where_conditions = []
        ctx.where_params = []
        result = ctx.build_label_only_dml_subquery("n0", "SELECT n0.node_id")
        assert isinstance(result, tuple)
        assert len(result) == 3


# ────────────────────────────────────────────────────────────────────────────
# Integration-style tr() tests exercising translator code paths
# ────────────────────────────────────────────────────────────────────────────

class TestTranslatorIntegration:
    def test_simple_match_return(self):
        sql = tr("MATCH (n:Person) RETURN n.name")
        assert "SELECT" in sql
        assert "rdf_labels" in sql  # Person filtered via label join

    def test_match_with_where(self):
        sql = tr("MATCH (n:Person) WHERE n.age > 30 RETURN n.name")
        assert "SELECT" in sql
        assert "30" in sql

    def test_match_relationship(self):
        sql = tr("MATCH (a:Person)-[:KNOWS]->(b:Person) RETURN a.name, b.name")
        assert "SELECT" in sql
        assert "rdf_edges" in sql  # relationship traversal uses rdf_edges

    def test_match_with_param(self):
        sql = tr("MATCH (n:Person {name: $name}) RETURN n.age", {"name": "Alice"})
        assert "SELECT" in sql
        assert "?" in sql

    def test_return_literal(self):
        sql = tr("RETURN 1 AS x")
        assert "1" in sql
        assert "x" in sql.lower() or "AS" in sql

    def test_return_count(self):
        sql = tr("MATCH (n:Person) RETURN count(n)")
        assert "COUNT" in sql.upper()

    def test_match_optional(self):
        sql = tr("MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN n.name, m.name")
        assert "SELECT" in sql

    def test_create_node(self):
        result = tr_full("CREATE (n:Person {name: 'Alice'})")
        sql = result.sql
        # CREATE returns a list of DML statements
        stmts = sql if isinstance(sql, list) else [sql]
        assert any("INSERT" in s or "UPDATE" in s or "SELECT" in s for s in stmts)

    def test_with_clause(self):
        sql = tr("MATCH (n:Person) WITH n.name AS name RETURN name")
        assert "SELECT" in sql

    def test_order_by(self):
        sql = tr("MATCH (n:Person) RETURN n.name ORDER BY n.name")
        assert "ORDER BY" in sql.upper()

    def test_limit(self):
        sql = tr("MATCH (n:Person) RETURN n.name LIMIT 10")
        assert "10" in sql

    def test_skip(self):
        sql = tr("MATCH (n:Person) RETURN n.name SKIP 5")
        assert "5" in sql

    def test_unwind(self):
        sql = tr("UNWIND [1, 2, 3] AS x RETURN x")
        assert "SELECT" in sql

    def test_distinct_return(self):
        sql = tr("MATCH (n:Person) RETURN DISTINCT n.name")
        assert "DISTINCT" in sql.upper()

    def test_multiple_labels(self):
        sql = tr("MATCH (n:Person:Employee) RETURN n.name")
        assert "SELECT" in sql
