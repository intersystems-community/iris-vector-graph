"""4.7.0 TCK regressions from rows 21 and 22, pinned against the translation (no container).

The TCK on 2026.3 fell from 3895 to 3857, all at Prepare:

* Precedence1 [25]-[28] (row 21, af2fd993, and row 22): `<LIST>LoadTableFunction+10^%qTable` (measured live on
  2026.3.0AI.113.0):
  - row 21 listed a collect() of a boolean expression as `'[' || LIST(NULLIF(CASE (expr) WHEN 1 THEN 'true' ..`.
    Every LIST() form failed some of the 23 Precedence1 [21], [25]-[28] statements (without the NULLIF, [21]); in a
    WITH, any expression around LIST() of an operand holding a correlated JSON_TABLE subquery (`b IN c`) fails. 4.6.5's
    COALESCE(JSON_ARRAYAGG(expr), '[]') prepared in all 23, so the SQL lists 1 / 0 again, and the engine answers a
    RETURN column of it as booleans (bool_list_expr_columns).
  - row 22 sent `UNWIND [[], [true], ..] AS c` through CY_LIST_KINDS; a literal of lists only (or maps only) is one
    kind of row and is read by JSON_TABLE alone again.
* Quantifier9-12 invariants (row 22, 9af1a20d): a literal holding maps went through CY_LIST_KINDS, whose side columns
  were written `"input"__k` for the reserved alias `input`: -1 "`)` expected, VARCHAR found". The side columns
  (`__k`, `__j`, row 22's `__t`, R273's `__b`) are now delimited whole.

The answers are proven live in tests/e2e/test_470_tck_unwind_shapes_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _unwrap_typed, translate_to_sql, unwind_by_index

PRECEDENCE = """UNWIND [true, false, null] AS a
UNWIND [true, false, null] AS b
WITH collect((a OR b IS NULL) = (a OR (b IS NULL))) AS eq,
     collect((a OR b IS NULL) <> ((a OR b) IS NULL)) AS neq
RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"""

QUANTIFIER = """UNWIND [{list: [2], fixed: true}, {list: [6], fixed: true}, {list: [1, 2, 3], fixed: false}] AS input
WITH CASE WHEN input.fixed THEN input.list ELSE null END AS fixedList,
     CASE WHEN NOT input.fixed THEN input.list ELSE [1] END AS inputList
RETURN fixedList, inputList"""

#: A delimited identifier with more name glued on after its closing quote: not one identifier (IRIS reads `"input"`,
#: then a stray `__k`). The lookbehind skips a doubled quote inside a string literal.
_GLUED = re.compile(r'"[A-Za-z_][\w ]*"\w')


def _sql(q: str, params=None) -> str:
    s = translate_to_sql(parse_query(q), params or {}).sql
    return s if isinstance(s, str) else "\n".join(s)


class TestPrecedenceShape:
    """A boolean collect() is 4.6.5's JSON_ARRAYAGG of the 1 / 0 operand in SQL: the only form that prepared in all 23
    Precedence1 [21], [25]-[28] statements. Every LIST() form of row 21 failed some of them."""

    @pytest.mark.parametrize("q", [
        PRECEDENCE,
        "MATCH (n) RETURN collect(exists(n.p)) AS l",
        "MATCH (n) RETURN collect(n.k > 1) AS l",
        "MATCH (n) RETURN collect(DISTINCT n.k > 1) AS l",
        "UNWIND [true, false, null] AS a UNWIND [true, false, null] AS b UNWIND [true, false, null] AS c "
        "WITH collect((a OR b = c) = (a OR (b = c))) AS eq RETURN eq",
    ])
    def test_a_boolean_collect_is_json_arrayagg(self, q):
        sql = _sql(q)
        assert "LIST(" not in sql and "NULLIF(CASE (" not in sql and "COALESCE(JSON_ARRAYAGG(" in sql, sql

    def test_no_inline_listbuild(self):
        assert "$LISTBUILD" not in _sql(PRECEDENCE)

    @pytest.mark.parametrize("q, cols", [
        ("MATCH (n) RETURN collect(n.k > 1) AS l", ["l"]),
        ("MATCH (n) RETURN collect(exists(n.p)) AS l, count(*) AS c", ["l"]),
        ("MATCH (n) RETURN collect(n.k) AS l", []),
        ("MATCH (n) RETURN n.k > 1 AS b", []),
        ("MATCH (n) WITH collect(n.k > 1) AS l RETURN l", ["l"]),
        ("MATCH (n) WITH collect(n.k > 1) AS l WITH l AS m RETURN m", ["m"]),
        ("MATCH (n) WITH n, collect(n.k > 1) AS l WITH collect(n.k) AS l RETURN l", []),
    ])
    def test_a_returned_boolean_collect_is_named_a_boolean_list(self, q, cols):
        """Row 21's answer kept: the engine reads such a column's 1 / 0 as true / false (bool_list_expr_columns)."""
        assert translate_to_sql(parse_query(q), {}).bool_list_expr_columns == cols


@pytest.mark.parametrize("raw, want", [
    ("[1,0,1]", "[true,false,true]"),
    ("[0]", "[false]"),
    ("[]", "[]"),
    ("[1,2]", "[1,2]"),  # not a list of booleans' 1 / 0: left alone
    ("[1,0.5]", "[1,0.5]"),
    (None, None),
    ([1, 0], [True, False]),
    ([True, None], [True, None]),
])
def test_the_engine_reads_a_boolean_lists_1_0(raw, want):
    from iris_vector_graph._engine.query import _bool_list_expr_value

    assert _bool_list_expr_value(raw) == want


LIST_PRED = (
    "UNWIND [true, false, null] AS a UNWIND [true, false, null] AS b "
    "UNWIND [[], [true], [false], [null], [true, false], [true, false, null]] AS c "
    "WITH collect((a {op} b IN c) = (a {op} (b IN c))) AS eq, collect((a {op} b IN c) <> ((a {op} b) IN c)) AS neq "
    "RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"
)


class TestListPredicateShape:
    """Precedence1 [26], [28]: the operand holds `b IN c`, a correlated JSON_TABLE subquery. In a WITH, IRIS 2026.3
    refuses any expression around LIST() of such an operand ('[' || LIST(..) || ']' included); JSON_ARRAYAGG of it
    prepares (measured over all 16 Precedence1 [25]-[28] statements)."""

    @pytest.mark.parametrize("op", ["=", "<>", "OR", "XOR", "AND"])
    def test_a_correlated_json_table_operand_is_listed_by_json_arrayagg(self, op):
        sql = _sql(LIST_PRED.format(op=op))
        assert sql.count("COALESCE(JSON_ARRAYAGG(") == 2 and "LIST(" not in sql, sql

    def test_the_list_of_lists_is_read_by_json_table_alone(self):
        """A literal of lists only is one kind of row: not through CY_LIST_KINDS (which also failed [26], [28])."""
        sql = _sql(LIST_PRED.format(op="XOR"))
        assert "CY_LIST_KINDS" not in sql and "c__k" not in sql, sql

    @pytest.mark.parametrize("q", [
        "UNWIND [[1], [2, 3]] AS e RETURN e < 3 AS r",
        "UNWIND [{a: 1}, {a: 2}] AS m RETURN m.a AS a",
        "UNWIND [[1], null, [2]] AS e RETURN e",
    ])
    def test_a_literal_of_one_nested_kind_skips_cy_list_kinds(self, q):
        assert "CY_LIST_KINDS" not in _sql(q)

    @pytest.mark.parametrize("q", [
        "UNWIND ['1', true, [2]] AS e RETURN e",
        "UNWIND [[2], '[2]'] AS e RETURN e",
        "UNWIND [[1], 2] AS e RETURN e",
        "UNWIND [[1], {a: 1}] AS e RETURN e",
    ])
    def test_a_literal_of_mixed_kinds_keeps_cy_list_kinds(self, q):
        assert "CY_LIST_KINDS(" in _sql(q)


class TestQuantifierShape:
    def test_side_columns_are_whole_identifiers(self):
        sql = _sql(QUANTIFIER)
        assert not _GLUED.search(sql), _GLUED.search(sql).group(0)

    def test_a_literal_of_maps_only_skips_cy_list_kinds(self):
        assert "CY_LIST_KINDS" not in _sql(QUANTIFIER)

    def test_side_columns_are_delimited_whole(self):
        """A literal of mixed kinds still goes through CY_LIST_KINDS (row 22), its side columns named whole."""
        sql = _sql("UNWIND [{list: [2], fixed: true}, 'a', true] AS input RETURN input")
        assert '"input__k" VARCHAR(1) PATH \'$.k\'' in sql and '"input__j" VARCHAR(32000) PATH \'$.j\'' in sql, sql

    def test_no_inline_listbuild(self):
        assert "$LISTBUILD" not in _sql(QUANTIFIER)

    @pytest.mark.parametrize("q", [
        # row 21: a run-time list under a reserved alias
        "UNWIND [1, 2] AS e WITH collect(e) AS l UNWIND l AS input RETURN input",
        "UNWIND [1, 'b'] AS e WITH collect(e) AS l UNWIND l AS input RETURN input < 'c' AS r",
        # row 22: a literal holding a nested value
        "UNWIND ['1', true, [2]] AS input RETURN input",
        # row 22: a mixed-kind literal's kind column
        "UNWIND [1, 'b'] AS input RETURN input < 'c' AS r",
        # 4.6.0 R273: a boolean among other values, its flag column
        "UNWIND [1, true] AS input RETURN input",
        "UNWIND [1, true] AS input RETURN collect(input) AS l",
    ])
    def test_a_reserved_alias_keeps_its_side_columns_whole(self, q):
        sql = _sql(q)
        m = _GLUED.search(sql)
        assert m is None, (m.group(0), sql)

    def test_the_by_position_fallback_too(self):
        used: list = []
        with unwind_by_index(used):
            sql = _sql("MATCH (n:L) WITH collect(n.v) AS vs UNWIND vs AS input RETURN input")
        assert used, "the fallback applies"
        m = _GLUED.search(sql)
        assert m is None, (m.group(0), sql)


def test_unwrap_typed_reads_a_delimited_run_time_row():
    """A sort key reads the row, not its J-marked JSON text (row 21), under a reserved alias as well."""
    assert _unwrap_typed("CHAR(1) || 'J' || u0.x__j") == "u0.x"
    assert _unwrap_typed("CHAR(1) || 'J' || u0.\"input__j\"") == 'u0."input"'


class TestRunTimeRowThroughAWith:
    """Quantifier10 [4]: a run-time list's row carried by a WITH projected its flag and its JSON text, two columns per
    WITH, and the statement failed at Prepare on 2026.3 (<LIST>LoadTableFunction); either column alone prepared. The
    flag is read from the text now: a boolean's JSON text is true / false (a string's is quoted)."""

    Q = "UNWIND [1, true] AS e WITH collect(e) AS l UNWIND l AS x WITH x RETURN x"

    def test_the_with_carries_the_text_only(self):
        sql = _sql(self.Q)
        assert " AS x__j" in sql and " AS x__b" not in sql, sql

    def test_the_flag_is_read_from_the_text(self):
        """x = true is true for the boolean only: its flag, now read from the stage's JSON text."""
        assert "Stage2.x__j IN ('true', 'false')" in _sql(self.Q.replace("RETURN x", "RETURN x = true AS r"))


QUANT_HEAD = (
    "UNWIND [{list: [2], fixed: true}, {list: [1, 2, 3], fixed: false}] AS input "
    "WITH CASE WHEN input.fixed THEN input.list ELSE null END AS fixedList, "
    "CASE WHEN NOT input.fixed THEN input.list ELSE [1] END AS inputList "
    "UNWIND inputList AS x "
    "WITH fixedList, inputList, x, [y IN inputList WHERE rand() > 0.5 | y] AS list "
    "WITH fixedList, inputList, CASE WHEN rand() < 0.5 THEN reverse(list) ELSE list END + x AS list "
    "WITH coalesce(fixedList, list) AS list "
)


class TestUnreadJsonText:
    """Quantifier9 [5], Quantifier11 [3] / [6], Quantifier12 [5]: a WITH of a run-time list's row carried its JSON
    text (x__j) where no later clause read it, and IRIS 2026.3 failed the statement at Prepare
    (<LIST>LoadTableFunction); without the unread column each prepared. A JSON text column no clause reads is dropped."""

    def test_an_unread_json_text_is_not_projected(self):
        sql = _sql(QUANT_HEAD + "WITH none(x IN list WHERE x = 2) AS result, count(*) AS cnt RETURN result")
        assert "AS x__j" not in sql, sql

    @pytest.mark.parametrize("q", [
        "UNWIND [1, true] AS e WITH collect(e) AS l UNWIND l AS x WITH x RETURN x",
        "UNWIND [1, true] AS e WITH collect(e) AS l UNWIND l AS x WITH x RETURN collect(x) AS c",
        "UNWIND [1, true] AS e WITH collect(e) AS l UNWIND l AS x WITH x RETURN x = true AS r",
    ])
    def test_a_read_json_text_stays(self, q):
        sql = _sql(q)
        assert " AS x__j" in sql and "Stage2.x__j" in sql, sql


def test_an_unwind_of_a_carried_boolean_collect_reads_booleans():
    """`WITH collect(k > 1) AS l UNWIND l AS x RETURN x`: the stage holds 1 / 0 (JSON_ARRAYAGG); the UNWIND reads its
    text as JSON booleans, which CY_LIST_KINDS flags, so x answers true / false as on the release tip."""
    sql = _sql("UNWIND [1, 2, 3] AS k WITH collect(k > 1) AS l UNWIND l AS x RETURN x")
    assert "CY_LIST_KINDS(REPLACE(REPLACE(Stage1.l, '1', 'true'), '0', 'false'))" in sql, sql
