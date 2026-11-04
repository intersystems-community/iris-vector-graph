"""4.6.0 R218: a node property's value reads back with the type it is stored with, on the SQL path and from
``get_node`` / ``get_property_values``, whatever the driver's patch version.

``rdf_props.val`` is VARCHAR. A Cypher ``CREATE`` or ``SET`` stores a number there as an IRIS number (a ``$DOUBLE``
for 0.95, an integer for 29); the API stores the text ``'0.95'``. intersystems-irispython 5.4.0 decodes a value by its
own type, so the SQL path read the Cypher-written 0.95 as the float 0.95; 5.4.1, the version a fresh install gets,
decodes by the column's type and reads it as the IRIS text ``'.94999999999999995559'`` (``'.5'``, ``'29'``, and
``'5'`` for ``2.5 * 2``). The native executor reads 0.95 on either. Measured on Community 2026.1 with the same rows
through both drivers (walkthrough 2 W6, W7).

The SQL path now reads such a column through ``CY_TYPED(v)``, which reads the value's type in the row (the
``$LISTBUILD`` test R182's ``CY_TOSTRING`` uses) and marks a number (``\\x01D`` a ``$DOUBLE``, ``\\x01N`` an integer or
decimal) before its text; the engine decodes the mark, so the column reads the same under both drivers and matches
the native path. A string, every API-written value among them, comes back as it is. Filters, ORDER BY and GROUP BY
keep reading the plain column, so no match set or order changes. The function body is checked live
(tests/e2e/test_460_r218_typed_node_reads_e2e.py).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.prop_values import decode_typed_text


def _q(q, params=None):
    return translate_to_sql(parse_query(q), params or {})


def _sql(q, params=None):
    r = _q(q, params)
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


def _select_list(sql):
    """The final SELECT's items, before its FROM: the last SELECT that starts a line (a subquery does not)."""
    last = sql[max(0, sql.rfind("\nSELECT ")) :]
    return last.split("\nFROM", 1)[0]


class TestDecode:
    @pytest.mark.parametrize(
        "text, value",
        [
            ("\x01D.94999999999999995559", 0.95),
            ("\x01D.5", 0.5),
            ("\x01D-.5", -0.5),
            ("\x01D5", 5.0),
            ("\x01D2.5", 2.5),
            ("\x01N29", 29),
            ("\x01N-3", -3),
            ("\x01N2.5", 2.5),
            ("\x01N12345678901234567890", 12345678901234567890),
            ("\x01S\x01x", "\x01x"),
            ("0.95", "0.95"),
            (".95", ".95"),
            ("29", "29"),
            ("", ""),
            (None, None),
            (0.95, 0.95),
            (29, 29),
        ],
    )
    def test_decode(self, text, value):
        got = decode_typed_text(text)
        assert got == value and type(got) is type(value)

    def test_a_double_is_a_float_even_when_whole(self):
        assert isinstance(decode_typed_text("\x01D5"), float)

    def test_an_integer_is_an_int(self):
        assert isinstance(decode_typed_text("\x01N5"), int)


class TestWhichColumnsReadTyped:
    @pytest.mark.parametrize(
        "q, alias",
        [
            ("MATCH (n:T) RETURN n.f", "n_f"),
            ("MATCH (n {node_id: 'x'}) RETURN n.f AS f", "f"),
            ("MATCH (n:T) RETURN max(n.f) AS m", "m"),
            ("MATCH (n:T) RETURN min(n.f) AS m", "m"),
            ("MATCH (n:T) WITH n.f AS x RETURN x", "x"),
            ("MATCH (n:T) RETURN n.f ORDER BY n.f", "n_f"),
            ("MATCH (n:T) RETURN DISTINCT n.f AS f", "f"),
            ("MATCH (n:T) RETURN n.f AS f, count(*) AS c", "f"),
        ],
    )
    def test_arithmetic_over_a_node_property_reads_through_cy_arith(self):
        """R240 replaced R218's CY_TYPED wrapper on arithmetic: CY_ARITH computes on the stored value's own type."""
        r = _q("MATCH (n:T) RETURN n.s * 2 AS d")
        sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
        assert re.search(r"CY_ARITH\(.*\) END AS d\b", _select_list(sql)), sql

    def test_a_node_property_read_goes_through_cy_typed(self, q, alias):
        r = _q(q)
        sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
        assert re.search(rf"CY_TYPED\(.*\) AS {alias}\b", _select_list(sql)), sql
        assert alias in r.typed_value_columns

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH ()-[r:R]->() RETURN r.f AS f",
            "MATCH (n:T) RETURN n AS f",
            "MATCH (n:T) RETURN count(*) AS f",
            "MATCH (n:T) RETURN collect(n.f) AS f",
            "MATCH (n:T) RETURN toString(n.f) AS f",
            "MATCH (n:T) RETURN properties(n) AS f",
            "RETURN 1 + 2 AS f",
            "WITH {a: 1} AS m RETURN m.a AS f",
        ],
    )
    def test_other_columns_do_not(self, q):
        r = _q(q)
        sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
        assert "CY_TYPED(" not in sql, sql
        assert "f" not in r.typed_value_columns

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:T) WHERE n.f = 0.95 RETURN n.f AS f",
            "MATCH (n:T {f: 0.95}) RETURN n.f AS f",
            "MATCH (n:T) WHERE n.f <> $v RETURN n.f AS f",
        ],
    )
    def test_a_filter_reads_the_plain_column(self, q):
        sql = _sql(q, {"v": 0.95})
        tail = sql.split("\nFROM", 1)[1]
        assert "CY_TYPED(" not in tail, sql
        # the filter is R168's, unchanged: IN the number's spellings, or = the double
        assert "CY_TYPED(" in _select_list(sql)

    def test_order_by_and_group_by_read_the_plain_column(self):
        sql = _sql("MATCH (n:T) RETURN n.f AS f, count(*) AS c ORDER BY f")
        tail = sql.split("\nFROM", 1)[1]
        assert "CY_TYPED(" not in tail, sql


class TestEngineDecodes:
    def test_finish_decodes_the_typed_columns(self):
        from iris_vector_graph._engine.query import _finish_sql_result
        from iris_vector_graph.result import IVGResult

        sq = _q("MATCH (n:T) RETURN n.f AS f, n.g AS g, n.name AS name")
        res = IVGResult(columns=["f", "g", "name"], rows=[["\x01D.94999999999999995559", "\x01N29", "0.95"]])
        out = _finish_sql_result(res, sq)
        assert out.rows == [[0.95, 29, "0.95"]]
        assert [type(v) for v in out.rows[0]] == [float, int, str]

    def test_a_native_row_is_left_as_it_is(self):
        from iris_vector_graph._engine.query import _finish_sql_result
        from iris_vector_graph.result import IVGResult

        sq = _q("MATCH (n:T) RETURN n.f AS f")
        out = _finish_sql_result(IVGResult(columns=["f"], rows=[[0.95], ["0.95"]]), sq)
        assert out.rows == [[0.95], ["0.95"]]


def _engine_with_rows(prop_rows):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = conn.cursor.return_value
    executed = []

    def execute(sql, params=None):
        executed.append(sql)
        cur.fetchall.return_value = prop_rows if "rdf_props" in sql else []

    cur.execute.side_effect = execute
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    return eng, executed


class TestGetNode:
    def test_get_nodes_reads_values_typed(self):
        eng, executed = _engine_with_rows(
            [("m2", "score", "\x01D.94999999999999995559"), ("m2", "n", "\x01N29"), ("m2", "t", "0.95")]
        )
        node = eng.get_node("m2")
        assert any("CY_TYPED(" in s and "rdf_props" in s for s in executed), executed
        assert node["score"] == 0.95 and isinstance(node["score"], float)
        assert node["n"] == 29 and isinstance(node["n"], int)
        assert node["t"] == "0.95"

    def test_get_property_values_reads_values_typed(self):
        eng, executed = _engine_with_rows([("\x01D.5",), ("0.5",), ("\x01N29",)])
        assert eng.get_property_values("p") == [0.5, "0.5", 29]
        assert any("CY_TYPED(" in s for s in executed), executed
