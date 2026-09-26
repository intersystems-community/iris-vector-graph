"""An OPTIONAL var-length match with no target answers one null row under the RETURN
clause's columns (Match7 [19]): the BFS route named it after the target variable,
so `RETURN p` came back as column `c`, and `RETURN p, c` lost `p`."""
from types import SimpleNamespace

from iris_vector_graph._engine.query import _optional_null_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

Q = "MATCH (a {name: 'A'}) OPTIONAL MATCH p = (a)-->(b)-[*]->(c) RETURN "


def _sq(ret):
    return translate_to_sql(parse_query(Q + ret), {})


def test_translator_records_final_select_aliases():
    assert _sq("p").select_aliases == ["p"]
    assert _sq("p, c").select_aliases == ["p", "c_id", "c_labels", "c_props"]


def test_null_columns_follow_the_select():
    assert _optional_null_columns(_sq("p"), ["c"]) == ["p"]
    assert _optional_null_columns(_sq("p, c"), ["c_id"]) == ["p", "c_id", "c_labels", "c_props"]


def test_null_columns_use_cypher_names():
    sq = SimpleNamespace(select_aliases=["c_name"], column_name_map={"c_name": "c.name"})
    assert _optional_null_columns(sq, ["x"]) == ["c.name"]


def test_internal_sort_columns_left_out():
    sq = SimpleNamespace(select_aliases=["p", "__sort0"], column_name_map={})
    assert _optional_null_columns(sq, ["c"]) == ["p"]


def test_fallback_without_aliases():
    sq = SimpleNamespace(select_aliases=[], column_name_map={})
    assert _optional_null_columns(sq, ["c"]) == ["c"]
