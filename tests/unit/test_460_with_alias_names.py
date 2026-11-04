"""4.6.0 R188: a WITH alias named like an SQL word or an IVG column reads its own stage column.

Two causes, both in the final SELECT over the WITH stage:

- IRIS refuses a CTE's own name as a qualifier once the statement has an ORDER BY (SQLCODE -23, "Label 'STAGE1' is not
  listed"), and an alias IRIS reserves is selected qualified and quoted (`Stage1."id"`), which the existing workaround
  (dropping `Stage1.` when FROM is the one stage) did not match. FROM now names the stage with an alias of its own name.
- A carried node variable was read bare (`s`), which beside `rdf_labels` or a joined `rdf_props` row is that table's
  column: `WITH n AS s RETURN labels(s)` read `WHERE s = s` (every label in the namespace), `RETURN s.k` was ambiguous
  (-27). A name that is also a column of IVG's tables, or the row id, is read qualified.

tests/e2e/test_460_with_alias_names_e2e.py runs every alias through each shape live.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _final(q):
    r = translate_to_sql(parse_query(q), {})
    sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
    sql = _SCHEMA_QUALIFIED.sub(r"\1", sql)
    return sql.split("\n)\n", 1)[1] if sql.startswith("WITH Stage1 AS (") else sql


@pytest.mark.parametrize("alias", ["id", "value", "count", "key", "label", "name", "type"])
def test_a_quoted_alias_orders_through_an_aliased_stage(alias):
    final = _final(f"MATCH (n:X) WITH n.k AS {alias} RETURN {alias} ORDER BY {alias}")
    assert f'Stage1."{alias}" AS "{alias}"' in final or f"Stage1.{alias} AS {alias}" in final, final
    assert "\nFROM Stage1 Stage1\n" in final, final


@pytest.mark.parametrize("alias", ["s", "key", "val", "graph_id", "id"])
def test_a_store_column_alias_keeps_its_qualifier_in_order_by(alias):
    final = _final(f"MATCH (n:X) WITH n, n.k AS {alias} RETURN n.k, {alias} ORDER BY {alias} DESC")
    col = f'"{alias}"' if alias in ("id", "key") else alias
    assert re.search(rf"ORDER BY %EXACT\(Graph_KG\.CY_SORT_KEY\(Stage1\.{re.escape(col)}, 'x'\)\) DESC", final), final
    assert "\nFROM Stage1 Stage1\n" in final, final


@pytest.mark.parametrize("alias", ["s", "label", "key", "val", "graph_id", "node_id", "id"])
def test_a_renamed_node_reads_its_stage_column(alias):
    col = f'Stage1."{alias}"' if alias in ("id", "key", "label") else f"Stage1.{alias}"
    final = _final(f"MATCH (n:X) WITH n AS {alias} RETURN labels({alias})")
    assert f"FROM rdf_labels WHERE s = {col})" in final, final
    final = _final(f"MATCH (n:X) WITH n AS {alias} RETURN {alias}.k")
    assert f"ON p2.s = {col} AND" in final, final


def test_an_ordinary_alias_is_unchanged():
    final = _final("MATCH (n:X) WITH n.k AS idx RETURN idx ORDER BY idx")
    assert "SELECT idx AS idx\nFROM Stage1\nORDER BY" in final, final
    final = _final("MATCH (n:X) WITH n AS m RETURN labels(m)")
    assert "FROM rdf_labels WHERE s = m)" in final, final
