"""4.6.0 R248: every value the engine returns that holds a node's properties reads each one with its stored type.

R218 typed a returned property column (``RETURN n.f``). A whole node (``RETURN n``, ``collect(n)``, a node inside a
map, a CALL-yielded node, a path's nodes) carried its properties as the key/value list ``_props``, whose values the
SQL built as text on every driver: ``{"key":"f","value":".94999999999999995559"}`` for a Cypher-written 0.95, and
``"29"`` for 29. A map literal over a property, ``{a: n.f}``, spelled 0.95 ``.94999999999999995559``, which is not JSON.
A variable-length read's target nodes (``_fetch_props_json``) and a path's property values read ``val`` through the
driver, so they were 0.95 under intersystems-irispython 5.4.0 and the text under 5.4.1 (fixes30, Community 2026.1).

Now ``CY_JSON_NUM(v)`` answers the JSON number of a value stored as an IRIS number (the ``$LISTBUILD`` type test
``CY_TYPED`` makes, spelled as ``CY_TOSTRING`` spells it: ``0.95``, ``5.0`` for a ``$DOUBLE`` 5, ``29``) and NULL for a
string, so ``_props`` reads ``{"key":"f","value":0.95}`` and ``{"key":"i","value":29}``: the value ``RETURN n.f``
answers. A string, every API-written value among them, is the JSON string it was. Filters and sort keys are unchanged:
they never read ``_props``, and a node's sort key starts with its ``_id``. ``percentileDisc`` of a node value, whose
column is statically a float, reads IRIS's text ``'10'`` as 10.0.

The TCK harness's ``SqlHydrator`` read ``val`` through the driver too: 134 of the 137 scenarios that failed under
5.4.1 were its read, not the engine's answer. It now reads the value's type in the row with IRIS SQL's own
``$LISTBUILD`` (no IVG function), as 5.4.0 decoded it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import properties_subquery, translate_to_sql
from iris_vector_graph.schema import GraphSchema

REPO = Path(__file__).resolve().parents[2]


def _sql(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


def _routine(name):
    for s in GraphSchema.get_procedures_sql_list():
        if re.search(rf"FUNCTION\s+\w+\.{name}\(", s):
            return s
    raise AssertionError(f"no routine {name}")


class TestCyJsonNum:
    def test_setup_creates_it(self):
        body = _routine("CY_JSON_NUM")
        assert "RETURNS VARCHAR" in body and "LANGUAGE OBJECTSCRIPT" in body

    def test_its_body_has_no_apostrophe_or_question_mark(self):
        # the DB-API reads ' and ? inside DDL (iris_ddl_objectscript_apostrophe)
        body = _routine("CY_JSON_NUM").split("LANGUAGE OBJECTSCRIPT", 1)[1]
        assert "'" not in body and "?" not in body

    def test_it_reads_the_type_in_the_row_as_cy_typed_does(self):
        body = _routine("CY_JSON_NUM")
        assert "$LISTBUILD(v)" in body and "$Length(v)" in body  # the type byte, and the 40-character guard
        assert "CYTOSTRING" in body  # spelled as toString spells a number


class TestNodeValueProps:
    def test_props_list_reads_a_stored_number_as_a_json_number(self):
        sql = properties_subquery("n0.node_id")
        assert "CY_JSON_NUM(val)" in sql
        # a string is the JSON string it was, escaped as before
        assert "REPLACE(REPLACE(val, '\\', '\\\\'), '\"', '\\\"')" in sql
        assert sql.count("COALESCE(") == 1

    def test_props_list_has_no_text_only_value(self):
        assert '"value":"\' || REPLACE' not in properties_subquery("n0.node_id")

    def test_props_list_takes_the_schema_outside_a_translation(self):
        sql = properties_subquery("n.node_id", prefix="MySchema")
        assert "MySchema.rdf_props" in sql and "MySchema.CY_JSON_NUM(val)" in sql

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:L) RETURN n",
            "MATCH (n:L) WITH n RETURN n",
            "MATCH (n:L) RETURN collect(n) AS c",
            "MATCH (n:L) RETURN {m: n} AS mp",
            "MATCH (n:L) RETURN [n] AS l",
            "MATCH (a:L)-[:R]->(b) RETURN a, b",
            "OPTIONAL MATCH (n:L) RETURN n",
        ],
    )
    def test_every_node_value_reads_through_it(self, q):
        sql = _sql(q)
        assert "_props" in sql or "JSON_ARRAYAGG" in sql, sql
        assert "CY_JSON_NUM(val)" in sql, sql
        assert '"value":"\' || REPLACE' not in sql, sql

    def test_a_map_literal_spells_a_stored_number_as_json(self):
        sql = _sql("MATCH (n:L) RETURN {a: n.f} AS mp")
        # the stored number first; then the 4.5.x reading of text (number-shaped text a number, else a string)
        assert re.search(r"COALESCE\(\w+\.CY_JSON_NUM\(%EXACT\(p\d+\.val\)\), CASE WHEN", sql), sql

    def test_filters_and_sort_keys_do_not_change(self):
        sql = _sql("MATCH (n:L) WHERE n.f > 0.5 RETURN n ORDER BY n.f")
        where = sql.rsplit("\nWHERE ", 1)[1]
        assert "CY_JSON_NUM" not in where, where


class TestPercentileDisc:
    @pytest.mark.parametrize("raw, value", [("10", 10.0), ("30.5", 30.5), (".5", 0.5), ("-.5", -0.5), (10, 10.0)])
    def test_a_float_column_reads_irises_number_text(self, raw, value):
        from iris_vector_graph._engine.query import _float_expr_value

        got = _float_expr_value(raw)
        assert got == value and isinstance(got, float)

    @pytest.mark.parametrize("raw", ["abc", "", "1.2.3", None, True])
    def test_other_values_pass_through(self, raw):
        from iris_vector_graph._engine.query import _float_expr_value

        assert _float_expr_value(raw) is raw

    def test_percentile_disc_is_a_float_column(self):
        r = translate_to_sql(parse_query("MATCH (n) RETURN percentileDisc(n.price, 0.5) AS p"), {})
        assert "p" in [c.strip('"') for c in r.float_expr_columns]


class _Cursor:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, params=None):
        self.log.append(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class TestEngineSideReads:
    """The engine's own reads of node values outside the translation."""

    def _engine(self, rows):
        from iris_vector_graph.engine import IRISGraphEngine

        log: list = []
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        store = MagicMock()
        store._schema_prefix = "Graph_KG"
        store.conn.cursor.side_effect = lambda: _Cursor(rows, log)
        eng._store = store
        eng.conn = store.conn
        return eng, log

    def test_fetch_props_json_reads_the_stored_type(self):
        eng, log = self._engine([("v1", "f", "\x01D.94999999999999995559"), ("v1", "i", "\x01N29"), ("v1", "s", "29")])
        out = eng._fetch_props_json(["v1"])
        assert "CY_TYPED(val)" in log[0]
        assert json.loads(out["v1"]) == [
            {"key": "f", "value": 0.95},
            {"key": "i", "value": 29},
            {"key": "s", "value": "29"},
        ]

    def test_property_pairs_read_the_stored_type(self):
        eng, log = self._engine([("v1", "\x01D.5"), ("v2", "0.5")])
        assert eng.get_property_pairs("f") == [("v1", 0.5), ("v2", "0.5")]
        assert "CY_TYPED(val)" in log[0]
        log.clear()
        assert eng.get_property_pairs_like("f", "%5") == [("v1", 0.5), ("v2", "0.5")]
        assert "CY_TYPED(val)" in log[0] and "val LIKE ?" in log[0]


class TestGuard:
    """No value-returning read can skip the typed read: these are the only ways a node's property values leave IRIS.

    A new reader that builds a node's ``_props`` list, or returns ``rdf_props.val``, must go through
    ``properties_subquery`` (CY_JSON_NUM) or ``CY_TYPED`` and ``decode_typed_text``; a filter or a sort key is listed
    here by name with the reason it keeps the plain column."""

    #: A text-only `_props` value: `'","value":"' || <val> || '"'`, in SQL inside Python source.
    TEXT_VALUE = re.compile(r"""value\\{0,4}":\\{0,4}"' \|\| REPLACE\(REPLACE\(val""")
    #: `SELECT ... val FROM ...rdf_props` in the engine's Python (not the translator, whose RETURN columns R218 types).
    RAW_SELECT = re.compile(r"SELECT\s+(?:TOP\s+\S+\s+|\{top\})?(?:[\w.\"]+,\s*)*val\s+FROM\s+\{[^}]*rdf_props", re.I)
    #: (file, the line's text) of reads that are not returned values.
    ALLOWED = {
        # a filter: the stored value compared with the query's, never returned
        ("iris_vector_graph/stores/iris_sql_store.py", "SELECT val FROM {self._ivg_schema}.rdf_props WHERE s = ?"),
        # (embed_nodes's text read was listed here until R264 typed it)
    }

    def _py_files(self):
        root = REPO / "iris_vector_graph"
        return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]

    def test_no_props_list_is_built_from_text_alone(self):
        offenders = []
        for p in self._py_files():
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if self.TEXT_VALUE.search(line):
                    offenders.append(f"{p.relative_to(REPO)}:{i}")
        assert offenders == []

    def test_every_props_list_comes_from_properties_subquery(self):
        """`"_props":` in the translator is always followed by properties_subquery(...)."""
        text = (REPO / "iris_vector_graph/cypher/translator.py").read_text()
        for m in re.finditer(r"""\\"_props\\":' \|\| COALESCE\(\{([a-z_]+)\(""", text):
            assert m.group(1) == "properties_subquery", text[m.start() : m.start() + 120]
        sites = re.findall(r"""\\"_props\\":'""", text)
        assert len(sites) == len(re.findall(r"""\\"_props\\":' \|\| COALESCE\(\{properties_subquery\(""", text))

    def test_engine_reads_of_val_are_typed_or_listed(self):
        offenders = []
        for p in self._py_files():
            rel = str(p.relative_to(REPO))
            if rel.endswith("cypher/translator.py") or "/migrations/" in rel or rel.endswith("schema.py"):
                continue
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if not self.RAW_SELECT.search(line):
                    continue
                if any(rel == f and frag in line for f, frag in self.ALLOWED):
                    continue
                offenders.append(f"{rel}:{i}: {line.strip()}")
        assert offenders == []


class TestArithmeticColumns:
    """R240's CY_ARITH answers a $DOUBLE or an integer in a VARCHAR column, which 5.4.1 reads as text ('4' for 2.0 * 2,
    so the mixed decode answered the integer 4). A RETURN column of it, or of a WITH column of it, reads through CY_TYPED
    as a node property does (R218); toString() of it is text already. Relationship properties join it: `r.w * 3` with w
    1.5 answered 4 (the int default R240 left for a relationship), and now goes through CY_ARITH, which reads a
    relationship's text "1.5" as the float (fixes28)."""

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:L) RETURN n.g * 2 AS d",
            "MATCH (n:L) RETURN n.i / 2 AS d",
            "MATCH (n:L) WITH n.g * 2 AS d RETURN d",
            "MATCH ()-[r:R]->() RETURN r.w * 3 AS d",
            "MATCH (n:L)-[r:R]->() RETURN n.g * r.w AS d",
        ],
    )
    def test_the_column_reads_through_cy_typed(self, q):
        r = translate_to_sql(parse_query(q), {})
        sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
        final = sql[sql.rfind("SELECT ") :]
        assert re.search(r"CY_TYPED\((?:CASE WHEN .*CY_ARITH\(|Stage1\.d\))", final), final
        assert "d" in r.typed_value_columns, r.typed_value_columns
        assert "d" not in [c.lower() for c in r.int_expr_columns]

    def test_tostring_of_it_is_not_wrapped(self):
        r = translate_to_sql(parse_query("MATCH (n:L) RETURN toString(n.g * 2) AS t"), {})
        assert "CY_TYPED(" not in r.sql and "t" not in r.typed_value_columns

    def test_the_sort_key_reads_the_plain_value(self):
        sql = _sql("MATCH (n:L) RETURN n.g * 2 AS d ORDER BY d")
        assert "CY_TYPED" not in sql.rsplit("ORDER BY", 1)[1]

    def test_a_relationship_property_in_a_filter_is_as_before(self):
        sql = _sql("MATCH ()-[r:R]->() WHERE r.w * 3 > 4 RETURN r.w")
        where = sql.rsplit("\nWHERE ", 1)[1]
        assert "CY_ARITH" not in where

    def test_a_set_of_a_relationship_from_its_own_arithmetic_still_translates(self):
        # TCK Set6 [19]-[21]: CY_ARITH is a RETURN's, not a SET's, for a relationship property
        r = translate_to_sql(parse_query("MATCH ()-[r:R]->() SET r.num = r.num * 2 RETURN r.num AS num"), {})
        assert r.sql
