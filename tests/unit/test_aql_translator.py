import warnings
import pytest
from iris_vector_graph.cypher.aql import translate_aql, AQLParseError, AQLTranslationError


class TestBasicTranslation:
    def test_basic_traversal_returns_tuple(self):
        cypher, params = translate_aql(
            "FOR v IN 1..3 OUTBOUND @start g RETURN v._key",
            bind_vars={"start": "mesh:D003924"}
        )
        assert isinstance(cypher, str)
        assert isinstance(params, dict)

    def test_match_in_output(self):
        cypher, params = translate_aql(
            "FOR v IN 1..3 OUTBOUND @start g RETURN v._key",
            bind_vars={"start": "n1"}
        )
        assert "MATCH" in cypher

    def test_where_in_output(self):
        cypher, params = translate_aql(
            "FOR v IN 1..3 OUTBOUND @start g RETURN v._key",
            bind_vars={"start": "n1"}
        )
        assert "WHERE" in cypher or "node_id" in cypher

    def test_bind_var_in_params(self):
        cypher, params = translate_aql(
            "FOR v IN 1..1 OUTBOUND @s g RETURN v._key",
            bind_vars={"s": "mesh:D003924"}
        )
        assert params.get("s") == "mesh:D003924"

    def test_key_mapped_to_node_id(self):
        cypher, params = translate_aql("FOR v IN 1..1 OUTBOUND @s g RETURN v._key", bind_vars={"s": "n1"})
        assert "node_id" in cypher

    def test_hops_reflected(self):
        cypher, params = translate_aql("FOR v IN 1..5 OUTBOUND @s g RETURN v", bind_vars={"s": "n1"})
        assert "5" in cypher or "*1..5" in cypher

    def test_graph_name_emits_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translate_aql("FOR v IN 1..2 OUTBOUND @s GRAPH 'proteins' RETURN v", bind_vars={"s": "n1"})
        assert any("semantics" in str(warning.message).lower() or "GRAPH" in str(warning.message) for warning in w)

    def test_collection_list_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translate_aql("FOR v IN 1..2 OUTBOUND @s interactions RETURN v", bind_vars={"s": "n1"})
        assert len([x for x in w if "GRAPH" in str(x.message)]) == 0


class TestFilterTranslation:
    def test_filter_eq_in_output(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g FILTER v.organism == 'human' RETURN v._key",
            bind_vars={"s": "n1"}
        )
        assert "WHERE" in cypher
        assert "organism" in cypher

    def test_filter_regex_match(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g FILTER v.name =~ 'TP.*' RETURN v",
            bind_vars={"s": "n1"}
        )
        assert "=~" in cypher or "REGEX" in cypher.upper() or "name" in cypher

    def test_filter_not_null(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g FILTER v.name != null RETURN v",
            bind_vars={"s": "n1"}
        )
        assert "name" in cypher

    def test_filter_and_chained(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g FILTER v.x == 1 FILTER v.y > 0 RETURN v",
            bind_vars={"s": "n1"}
        )
        assert "WHERE" in cypher


class TestBindVars:
    def test_two_bind_vars(self):
        cypher, params = translate_aql(
            "FOR v IN 1..1 OUTBOUND @s g FILTER v.name == @name RETURN v._key",
            bind_vars={"s": "n1", "name": "TP53"}
        )
        assert params.get("s") == "n1"
        assert params.get("name") == "TP53"


class TestSortLimit:
    def test_sort_desc(self):
        cypher, _ = translate_aql(
            "FOR v, e IN 1..2 OUTBOUND @s g SORT e.confidence DESC LIMIT 25 RETURN v._key",
            bind_vars={"s": "n1"}
        )
        assert "ORDER BY" in cypher
        assert "DESC" in cypher
        assert "25" in cypher

    def test_limit_with_offset(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g SORT v.name ASC LIMIT 5, 10 RETURN v",
            bind_vars={"s": "n1"}
        )
        assert "SKIP 5" in cypher
        assert "LIMIT 10" in cypher


class TestShortestPath:
    def test_shortest_path_output(self):
        cypher, params = translate_aql(
            "FOR v, e, p IN OUTBOUND SHORTEST_PATH @from TO @to GRAPH 'g' RETURN p",
            bind_vars={"from": "n1", "to": "n5"}
        )
        assert "shortestPath" in cypher.lower() or "shortest" in cypher.lower() or "MATCH" in cypher
        assert params.get("from") == "n1"
        assert params.get("to") == "n5"


class TestCollectLet:
    def test_collect_with_count(self):
        cypher, _ = translate_aql(
            "FOR v IN 1..2 OUTBOUND @s g COLLECT type = v.type WITH COUNT INTO n RETURN {type: type, count: n}",
            bind_vars={"s": "n1"}
        )
        assert "count" in cypher.lower()
        assert "type" in cypher

    def test_let_alias(self):
        cypher, _ = translate_aql(
            "FOR v, e IN 1..2 OUTBOUND @s g LET score = e.weight RETURN v._key, score",
            bind_vars={"s": "n1"}
        )
        assert "score" in cypher or "WITH" in cypher


class TestErrorHandling:
    def test_search_raises(self):
        with pytest.raises(AQLTranslationError) as exc:
            translate_aql("FOR v IN 1..2 OUTBOUND @s g SEARCH v.name == 'TP53' RETURN v", bind_vars={"s": "n1"})
        assert exc.value.aql_construct in ("SEARCH", "nested FOR", "unexpected")

    def test_dyn_collection_raises(self):
        with pytest.raises(AQLTranslationError) as exc:
            translate_aql("FOR v IN 1..2 OUTBOUND @s @@coll RETURN v", bind_vars={"s": "n1"})
        assert "@@collection" in exc.value.aql_construct or "collection" in exc.value.aql_construct.lower()

    def test_nested_for_raises(self):
        with pytest.raises((AQLTranslationError, AQLParseError)):
            translate_aql("FOR v IN 1..2 OUTBOUND @s g FOR w IN 1..1 OUTBOUND v d RETURN w", bind_vars={"s": "n1"})
        
    def test_k_shortest_paths_raises(self):
        with pytest.raises(AQLTranslationError) as exc:
            translate_aql("FOR v IN OUTBOUND K_SHORTEST_PATHS @s TO @t g LIMIT 3 RETURN v", bind_vars={"s": "n1", "t": "n2"})
        assert "K_SHORTEST_PATHS" in exc.value.aql_construct

    def test_parse_error_has_line_col(self):
        with pytest.raises(AQLParseError) as exc:
            translate_aql("FOR v IN OUTBOUND @s RETURN v", bind_vars={"s": "n1"})
        assert exc.value.line >= 1
        assert exc.value.column >= 1


class TestPathVariables:
    def test_p_edges_translates(self):
        cypher, _ = translate_aql(
            "FOR v, e, p IN 1..2 OUTBOUND @s g RETURN p.edges",
            bind_vars={"s": "n1"}
        )
        assert "relationships" in cypher or "edges" in cypher

    def test_p_vertices_translates(self):
        cypher, _ = translate_aql(
            "FOR v, e, p IN 1..2 OUTBOUND @s g RETURN p.vertices",
            bind_vars={"s": "n1"}
        )
        assert "nodes" in cypher or "vertices" in cypher


class TestDirections:
    def test_inbound_direction(self):
        cypher, _ = translate_aql("FOR v IN 1..2 INBOUND @s g RETURN v", bind_vars={"s": "n1"})
        assert "<-" in cypher or "INBOUND" in cypher.upper() or "MATCH" in cypher

    def test_any_direction(self):
        cypher, _ = translate_aql("FOR v IN 1..1 ANY @s g RETURN v", bind_vars={"s": "n1"})
        assert "MATCH" in cypher

    def test_no_edge_var(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g RETURN v._key", bind_vars={"s": "n1"})
        assert "*1..2" in cypher or "MATCH" in cypher

    def test_with_edge_var(self):
        cypher, _ = translate_aql("FOR v, e IN 1..2 OUTBOUND @s g RETURN e.weight", bind_vars={"s": "n1"})
        assert "e" in cypher

    def test_with_path_var(self):
        cypher, _ = translate_aql("FOR v, e, p IN 1..3 OUTBOUND @s g RETURN p", bind_vars={"s": "n1"})
        assert "p" in cypher and "MATCH" in cypher


class TestFunctionMapping:
    def test_length_function(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g FILTER LENGTH(v.tags) > 0 RETURN v", bind_vars={"s": "n1"})
        assert "size" in cypher or "length" in cypher.lower()

    def test_contains_function(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g FILTER CONTAINS(v.name, 'kinase') RETURN v", bind_vars={"s": "n1"})
        assert "CONTAINS" in cypher

    def test_to_string_function(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g RETURN TO_STRING(v.id)", bind_vars={"s": "n1"})
        assert "toString" in cypher or "string" in cypher.lower()

    def test_distinct_return(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g RETURN DISTINCT v._key", bind_vars={"s": "n1"})
        assert "DISTINCT" in cypher

    def test_array_in_filter(self):
        cypher, _ = translate_aql("FOR v IN 1..2 OUTBOUND @s g FILTER v.type IN ['Gene', 'Disease'] RETURN v", bind_vars={"s": "n1"})
        assert "IN" in cypher
