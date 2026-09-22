"""Spec 230 — every `?` a statement carries has a parameter, in its own slot.

Found by the T074 gate. `MATCH (n:Drug) WHERE toBoolean(n.active) = 1 RETURN n.id`
reaches IRIS as

    JOIN Graph_KG.rdf_labels l1 ON ... AND l1.label = ?
    LEFT JOIN Graph_KG.rdf_props p2 ON ... AND p2."key" = ?
    WHERE ... CASE WHEN LOWER(CAST((SELECT val FROM Graph_KG.rdf_props
              WHERE s = n0.node_id AND "key" = ?) AS VARCHAR)) IN ('true',...)
              WHEN LOWER(CAST((SELECT val ... "key" = ?) AS VARCHAR)) IN ('false',...)

with three parameters for four markers, and in the wrong order: `['active',
'SprDrug', 'id']` binds the property name to `l1.label`. `toBoolean` renders its
argument's SQL into both arms of the `CASE`, which duplicates the marker but not
the parameter, and the argument's parameter went to `select_params` — ahead of the
join's — although its marker sits last.

The driver answers `<ARGUMENT ERROR> Bad argument given; Details: Incorrect number
of parameters`, and `execute_sql` logs it and returns zero rows, so the caller sees
an empty result set for a query that matches. The same shape reaches any inline
property reference rendered more than once.

The fix inlines the property key as an escaped literal in the two correlated-subquery
paths, exactly as `_structural_guard_sql` already does: a key repeated in the SQL then
costs no parameter at all, and nothing can order it wrongly. Keys come from the Cypher
text and are escaped on the way in.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _translate(cypher: str, params=None):
    return translate_to_sql(parse_query(cypher), params or {})


def _flat_sql(result) -> str:
    sql = result.sql
    return "\n".join(sql) if isinstance(sql, list) else str(sql)


def _params(result) -> list:
    """`SQLQuery.parameters` is per-statement; a read carries exactly one list."""
    flat: list = []
    for entry in result.parameters:
        if isinstance(entry, list):
            flat.extend(entry)
        else:
            flat.append(entry)
    return flat


def _marker_count(sql: str) -> int:
    return sql.count("?")


class TestAStatementBindsEveryMarkerItCarries:
    def test_toboolean_on_a_property_binds_its_markers(self):
        result = _translate("MATCH (n:Drug) WHERE toBoolean(n.active) = 1 RETURN n.id")
        sql = _flat_sql(result)
        assert _marker_count(sql) == len(_params(result)), (
            f"{_marker_count(sql)} markers, {len(_params(result))} params\n{sql}\n"
            f"{_params(result)}"
        )

    def test_the_label_parameter_is_the_label(self):
        """Arity alone is not enough: the order has to hold too.

        With the argument's parameter appended to `select_params`, the count could be
        made to match and `l1.label` would still be bound to `'active'`.
        """
        result = _translate("MATCH (n:Drug) WHERE toBoolean(n.active) = 1 RETURN n.id")
        sql = _flat_sql(result)
        before_label = sql.split("l1.label = ?")[0]
        index = _marker_count(before_label)
        assert _params(result)[index] == "Drug", (
            f"the marker for l1.label is parameter {index}, which is "
            f"{_params(result)[index]!r}\n{sql}\n{_params(result)}"
        )

    def test_the_property_key_is_not_a_parameter_in_the_case_arms(self):
        """Both arms name the key; neither spends a parameter on it."""
        result = _translate("MATCH (n:Drug) WHERE toBoolean(n.active) = 1 RETURN n.id")
        sql = _flat_sql(result)
        assert sql.count("\"key\" = 'active'") == 2, sql

    def test_toboolean_in_a_return_item_binds_its_markers(self):
        result = _translate("MATCH (n:Drug) RETURN toBoolean(n.active) AS flag")
        sql = _flat_sql(result)
        assert _marker_count(sql) == len(_params(result)), f"{sql}\n{_params(result)}"

    def test_an_order_by_property_still_binds_its_markers(self):
        """The inline path exists for `ORDER BY`; it must not regress."""
        result = _translate("MATCH (n:Drug) RETURN n.id AS id ORDER BY n.name")
        sql = _flat_sql(result)
        assert _marker_count(sql) == len(_params(result)), f"{sql}\n{_params(result)}"

    def test_a_key_with_a_quote_is_escaped_not_injected(self):
        """Inlining a key is only safe if the quote in it is doubled."""
        result = _translate(
            "MATCH (n:Drug) WHERE toBoolean(n.`o'clock`) = 1 RETURN n.id"
        )
        sql = _flat_sql(result)
        assert "o''clock" in sql, sql
        assert "'o'clock'" not in sql, sql
        assert _marker_count(sql) == len(_params(result)), f"{sql}\n{_params(result)}"
