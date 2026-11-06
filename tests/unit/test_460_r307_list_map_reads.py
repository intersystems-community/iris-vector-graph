"""4.6.0 R307: a property holding a list or a map reads back as that list or map, on nodes and relationships, from
every writer, on both executors.

`rdf_props.val` is VARCHAR: every writer stores a node's list or map as its JSON text (`prop_text`, `[1, 2, 3]`). A
relationship's qualifiers hold a JSON list, which IVG's JSON functions hand back as text. `get_node()` decoded that
text, but `RETURN x.p`, WITH, collect, DISTINCT, a map projection, a whole node (`n_props`) and the variable-length
routes answered the string `'[1, 2, 3]'` (the R307 value matrix, tests/e2e/test_460_r307_value_matrix_e2e.py).

The fix reads, it does not write: the stored form is unchanged. A stored value whose text is a JSON list or map reads as
the list or map: `CY_JSON_LISTMAP(v)` answers that text (else NULL) where SQL builds JSON (n_props, collect, a map
projection, properties(n)), and `prop_values.decode_json_list_map` decodes it in a property column. The cost, in
docs/KNOWN_ISSUES.md: a string that spells a JSON list or map (`'[1, 2]'`) reads as one, as get_node already read it.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph._engine.query import (
    _decode_rel_prop_text_columns,
    _decode_typed_value_columns,
    _typed_rel_props,
)
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import properties_subquery, translate_to_sql
from iris_vector_graph.prop_values import decode_json_list_map, read_prop_value
from iris_vector_graph.schema import GraphSchema


def _q(q, params=None):
    return translate_to_sql(parse_query(q), params or {})


def _sql(q, params=None):
    r = _q(q, params)
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


def _routine(name):
    for s in GraphSchema.get_procedures_sql_list():
        if re.search(rf"FUNCTION\s+\w+\.{name}\(", s):
            return s
    raise AssertionError(f"no routine {name}")


def _same(got, want):
    """Equal with the same Python type all the way down (1, 1.0 and True are three answers)."""
    if type(got) is not type(want):
        return False
    if isinstance(want, list):
        return len(got) == len(want) and all(_same(g, w) for g, w in zip(got, want))
    if isinstance(want, dict):
        return got.keys() == want.keys() and all(_same(got[k], want[k]) for k in want)
    return got == want


class _Result:
    def __init__(self, columns, rows):
        self.columns, self.rows = columns, rows


class _Query:
    def __init__(self, **kw):
        self.typed_value_columns = kw.get("typed", [])
        self.rel_prop_text_columns = kw.get("rel", [])
        self.return_arity = kw.get("arity", 0)


class TestDecode:
    @pytest.mark.parametrize(
        "text, value",
        [
            ("[1, 2, 3]", [1, 2, 3]),
            ("[0.5, 1.0]", [0.5, 1.0]),
            ('[1, "a", 2.5, true]', [1, "a", 2.5, True]),
            ("[[1, 2], [3]]", [[1, 2], [3]]),
            ('["x", "P1D"]', ["x", "P1D"]),
            ("[]", []),
            ('{"a": 1, "b": "x"}', {"a": 1, "b": "x"}),
            ('{"a": {"b": [1, 2]}}', {"a": {"b": [1, 2]}}),
            ("[1,2,3]", [1, 2, 3]),  # a relationship's list, as IRIS's %ToJSON spells it
            ("[4611686018427387907]", [4611686018427387907]),
        ],
    )
    def test_list_or_map_text_reads_as_the_list_or_map(self, text, value):
        assert _same(decode_json_list_map(text), value)

    @pytest.mark.parametrize(
        "text",
        ["[a]", "[1, 2", "{a: 1}", " [1]", "[1] ", "x[1]", "", "true", "7", "0.95", "{", "[", "[1]x", '"[1]"'],
    )
    def test_any_other_text_is_unchanged(self, text):
        got = decode_json_list_map(text)
        assert got == text and type(got) is str

    @pytest.mark.parametrize("text", ["[NaN, 1]", "[Infinity]", "[-Infinity]", '{"a": NaN}'])
    def test_a_list_holding_nan_or_infinity_stays_text(self, text):
        # not JSON: CY_JSON_LISTMAP answers NULL for it, so every Cypher read keeps the text (docs/KNOWN_ISSUES.md)
        assert decode_json_list_map(text) == text

    @pytest.mark.parametrize("value", [None, 7, 0.95, True, [1], {"a": 1}])
    def test_a_value_that_is_not_text_is_unchanged(self, value):
        assert decode_json_list_map(value) is value

    @pytest.mark.parametrize(
        "raw, value",
        [
            ("[1, 2, 3]", [1, 2, 3]),
            ("\x01D.94999999999999995559", 0.95),
            ("\x01N7", 7),
            ("\x01S\x01[1]", "\x01[1]"),  # a string that began with the mark is a string, whatever follows
            ("\x01J[1, 2]", [1, 2]),
            ("0.95", "0.95"),
            (None, None),
        ],
    )
    def test_read_prop_value_is_the_typed_decode_then_the_list_map_decode(self, raw, value):
        assert _same(read_prop_value(raw), value)


class TestPropertyColumns:
    """The node property columns both executors' rows go through (`_finish_sql_result`)."""

    def test_a_typed_column_reads_a_list_and_a_map(self):
        r = _Result(["i", "v"], [["a", "[1, 2, 3]"], ["b", '{"a": 1}'], ["c", "\x01N7"], ["d", "x"], ["e", None]])
        _decode_typed_value_columns(r, _Query(typed=["v"]))
        assert [row[1] for row in r.rows] == [[1, 2, 3], {"a": 1}, 7, "x", None]

    def test_another_column_keeps_its_text(self):
        r = _Result(["i", "v"], [["[1, 2]", "[1, 2]"]])
        _decode_typed_value_columns(r, _Query(typed=["v"]))
        assert r.rows == [["[1, 2]", [1, 2]]]

    def test_a_relationship_property_column_reads_a_list_and_a_map(self):
        r = _Result(["i", "r.p"], [["a", "[1,2,3]"], ["b", '{"a":1,"b":"x"}'], ["c", "7"], ["d", "x"]])
        _decode_rel_prop_text_columns(r, _Query(rel=[1], arity=2))
        assert [row[1] for row in r.rows] == [[1, 2, 3], {"a": 1, "b": "x"}, 7, "x"]

    def test_a_relationship_list_read_by_the_variable_length_routes(self):
        # a string leaf that spells a list reads as one, as r.p reads it (the cost KNOWN_ISSUES states)
        got = _typed_rel_props('{"l": [1, 2], "s": "[1, 2]", "m": "{\\"a\\": 1}", "n": "7", "e": "\\u0000"}')
        assert _same(got, {"l": [1, 2], "s": [1, 2], "m": {"a": 1}, "n": 7, "e": ""})


class TestCyJsonListMap:
    def test_setup_creates_it(self):
        body = _routine("CY_JSON_LISTMAP")
        assert "RETURNS VARCHAR(32000)" in body and "LANGUAGE OBJECTSCRIPT" in body

    def test_its_body_has_no_apostrophe_or_question_mark(self):
        # the DB-API reads ' and ? inside DDL (iris_ddl_objectscript_apostrophe)
        body = _routine("CY_JSON_LISTMAP").split("LANGUAGE OBJECTSCRIPT", 1)[1]
        assert "'" not in body and "?" not in body

    def test_it_answers_the_stored_text_only_when_it_parses(self):
        body = _routine("CY_JSON_LISTMAP")
        assert "%FromJSON(v)" in body and "Catch" in body and "Quit v" in body

    @pytest.mark.parametrize("name", ["CY_PROPS_MAP", "CY_RETYPE_MAP"])
    def test_the_map_builders_embed_a_list_or_map_text_as_json(self, name):
        body = _routine(name).split("LANGUAGE OBJECTSCRIPT", 1)[1]
        assert "CYJSONLISTMAP" in body, body
        assert "'" not in body and "?" not in body


class TestWhereSqlBuildsJson:
    def test_n_props_embeds_a_stored_list_or_map_as_json(self):
        sql = properties_subquery("n0.node_id")
        assert "WHEN Graph_KG.CY_JSON_LISTMAP(val) IS NOT NULL THEN Graph_KG.CY_JSON_LISTMAP(val)" in sql
        # one CASE, after the number and the boolean, before '' and the string (a COALESCE evaluates every argument)
        assert sql.index("CY_JSON_NUM(val)") < sql.index("CY_JSON_LISTMAP(val)") < sql.index("JSON_ARRAY(val)")

    def test_n_props_takes_the_schema_outside_a_translation(self):
        assert "MySchema.CY_JSON_LISTMAP(val)" in properties_subquery("n.node_id", prefix="MySchema")

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:L) RETURN collect(n.p) AS c",
            "MATCH (n:L) RETURN [n.p] AS l",
            "MATCH (n:L) RETURN n {.p} AS m",
            "MATCH (n:L) RETURN {a: n.p} AS m",
        ],
    )
    def test_a_node_property_in_a_list_or_map_is_its_json(self, q):
        assert re.search(r"CY_JSON_LISTMAP\((%EXACT\()?p\d+\.val", _sql(q)), _sql(q)

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH ()-[r:R]->() RETURN collect(r.p) AS c",
            "MATCH ()-[r:R]->() RETURN [r.p] AS l",
            "MATCH ()-[r:R]->() RETURN r {.p} AS m",
        ],
    )
    def test_a_relationship_property_in_a_list_or_map_is_its_json(self, q):
        assert "CY_JSON_LISTMAP(CASE WHEN e" in _sql(q), _sql(q)

    def test_a_leaf_with_a_parameter_is_not_copied_again(self):
        from iris_vector_graph.cypher.translator import _json_leaf_text

        assert "CY_JSON_LISTMAP" not in _json_leaf_text("JSON_VALUE(?, '$.p')")
        assert _json_leaf_text("JSON_VALUE(?, '$.p')").count("JSON_VALUE(?") == 3
        assert "CY_JSON_LISTMAP(x.v)" in _json_leaf_text("x.v")

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:L) WHERE n.p = [1, 2] RETURN n.node_id ORDER BY n.p",
            "MATCH ()-[r:R]->() WHERE r.p = [1, 2] RETURN r.w ORDER BY r.p",
        ],
    )
    def test_filters_and_sort_keys_do_not_change(self, q):
        sql = _sql(q)
        head, _, tail = sql.rpartition("\nWHERE ")
        assert "CY_JSON_LISTMAP" not in tail, tail



class TestGetNodeUsesTheSameRule:
    def test_get_node_reads_a_list_or_map_by_the_cypher_rule(self):
        from tests.unit.test_460_r218_typed_node_reads import _engine_with_rows

        eng, _ = _engine_with_rows(
            [("g1", "l", "[1, 2]"), ("g1", "m", '{"a": 1}'), ("g1", "nan", "[NaN, 1]"), ("g1", "b", "true"),
             ("g1", "x", "[a]"), ("g1", "n", "\x01N7")]
        )
        node = eng.get_node("g1")
        assert node["l"] == [1, 2] and node["m"] == {"a": 1}
        assert node["nan"] == "[NaN, 1]"  # not JSON: text, as every Cypher read answers it
        assert node["b"] is True and node["x"] == "[a]" and node["n"] == 7


class TestStoreGetNodesUsesTheSameRule:
    """IRISGraphStore.get_nodes json.loads'ed every value, so '7' read as 7: a third rule beside get_node's and Cypher's.
    It now reads through CY_TYPED and prop_values.read_node_value, as get_node does."""

    def _store(self, prop_rows):
        from unittest.mock import MagicMock

        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        conn = MagicMock()
        cur = conn.cursor.return_value
        executed = []

        def execute(sql, params=None):
            executed.append(sql)
            cur.fetchall.return_value = prop_rows if "rdf_props" in sql else []

        cur.execute.side_effect = execute
        store.conn = conn
        store._schema = "Graph_KG"
        return store, executed

    def test_values_read_as_get_node_reads_them(self):
        store, executed = self._store(
            [("n1", "s7", "7"), ("n1", "b", "true"), ("n1", "l", "[1, 2]"), ("n1", "f", "\x01D.94999999999999995559"),
             ("n1", "i", "\x01N29"), ("n1", "nan", "[NaN]"), ("n1", "e", "")]
        )
        r = store.get_nodes(["n1"], properties=["s7", "b", "l", "f", "i", "nan", "e", "absent"])
        row = dict(zip(r.columns, r.rows[0]))
        assert any("CY_TYPED(" in s and "rdf_props" in s for s in executed), executed
        assert row["s7"] == "7" and isinstance(row["s7"], str)
        assert row["b"] is True
        assert row["l"] == [1, 2]
        assert row["f"] == 0.95 and isinstance(row["f"], float)
        assert row["i"] == 29 and isinstance(row["i"], int)
        assert row["nan"] == "[NaN]"
        assert row["absent"] is None

    @pytest.mark.parametrize(
        "raw, value",
        [("7", "7"), ("true", True), ("false", False), ("[1, 2]", [1, 2]), ("\x01N7", 7), ("\x01S\x01true", "\x01true"),
         (None, None)],
    )
    def test_read_node_value(self, raw, value):
        from iris_vector_graph.prop_values import read_node_value

        got = read_node_value(raw)
        assert got == value and type(got) is type(value)
