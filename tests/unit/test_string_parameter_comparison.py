"""A string-valued $parameter in an ordering comparison is a string comparison.

`WHERE m.creationDate < $maxDate` with an ISO date string must not cast the
property column to DOUBLE (an ISO date would collapse to its leading number).
Numeric parameters keep the numeric cast.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

ISO = "2012-08-01T10:49:25.489+00:00"
BASE = "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WHERE {where} RETURN m.sid"


def tr(where: str, params: dict | None = None):
    return translate_to_sql(parse_query(BASE.format(where=where)), params or {})


def where_sql(q) -> str:
    sql = q.sql if isinstance(q.sql, str) else "\n".join(q.sql)
    return sql[sql.upper().index("WHERE"):]


def bound(q) -> list:
    out = []
    for p in q.parameters:
        out.extend(p if isinstance(p, (list, tuple)) else [p])
    return out


class TestStringParameterNotCast:
    @pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
    def test_param_on_right_has_no_cast(self, op):
        q = tr(f"m.creationDate {op} $maxDate", {"maxDate": ISO})
        w = where_sql(q)
        assert "CAST(" not in w
        if op in ("<", ">"):
            assert f".val {op} ?" in w
        else:  # <= and >= are emitted as (a < b) OR (a = b)
            assert ".val" in w and " ?" in w
        assert ISO in bound(q)

    @pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
    def test_param_on_left_has_no_cast(self, op):
        q = tr(f"$d {op} m.creationDate", {"d": ISO})
        w = where_sql(q)
        assert "CAST(" not in w
        # <= and >= are emitted as (a < b) OR (a = b); the property stays uncast either way.
        assert "? " in w and ".val" in w
        assert ISO in bound(q)


class TestUnchangedBehaviour:
    def test_int_param_still_casts(self):
        assert "AS DOUBLE" in where_sql(tr("m.length < $n", {"n": 5}))

    def test_float_param_still_casts(self):
        assert "AS DOUBLE" in where_sql(tr("m.length < $n", {"n": 5.5}))

    def test_bool_param_still_casts(self):
        assert "AS DOUBLE" in where_sql(tr("m.length < $n", {"n": True}))

    def test_string_literal_unchanged(self):
        w = where_sql(tr(f"m.creationDate < '{ISO}'"))
        assert "AS DOUBLE" not in w
        assert f".val < '{ISO}'" in w

    def test_int_literal_still_casts(self):
        assert "AS DOUBLE" in where_sql(tr("m.length < 5"))

    def test_missing_param_unchanged(self):
        # An unbound parameter is still an undefined variable, as before.
        with pytest.raises(SyntaxError, match="Undefined variable: n"):
            tr("m.length < $n", {})
