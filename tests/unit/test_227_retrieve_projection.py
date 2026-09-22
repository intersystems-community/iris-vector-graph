"""Spec 227 sweep — `ivg.retrieve` projects the wrong CTE, and its score as a node.

With the `JSON_TABLE` binds inlined (tests/unit/test_227_json_table_literal_args.py)
and the vector CTE's score cast (tests/unit/test_227_cte_vector_order_by.py), the
hybrid procedure finally reaches IRIS's parser — and is rejected there:

    SQLCODE -29 [Location: <Prepare>]
    Field 'RRF_SCORE' not found in the applicable tables

Two defects, both in `_translate_retrieve`, and each one alone is fatal:

1. The outer query reads `FROM BM25_Retrieve`. The procedure builds three CTEs and
   records `Retrieve` (the RRF fusion) as the alias for its yield items, but the
   outer `FROM` is taken from `context.stages[0]`, and the three `stages.insert(0, ...)`
   calls leave `BM25_Retrieve` there. So the statement selects the fusion's columns
   out of the BM25 arm — and if a column of that name had happened to exist, it would
   have returned BM25 rows while claiming to be a hybrid result.

2. `rrf_score` is never marked scalar (`if "score" in proc.yield_items` does not match
   `rrf_score`), so the projection treats it as a *node* variable and emits
   `rrf_score AS rrf_score_id` plus `JSON_ARRAYAGG` label and property subqueries
   keyed on the score value. A float is not a node ID; those subqueries can only
   return empty.

Neither is visible from Python: both are in the generated text, and the procedure's
only other tests assert on the shape of the CTEs rather than on what the statement
finally selects.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import set_schema_prefix, translate_to_sql

RETRIEVE = "CALL ivg.retrieve('aspirin', 5) YIELD node, rrf_score RETURN node, rrf_score"


def _sql(cypher: str = RETRIEVE) -> str:
    set_schema_prefix("Graph_KG")
    translated = translate_to_sql(parse_query(cypher), None)
    sql = translated.sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def _outer_source(sql: str) -> str:
    """What the final `SELECT` reads — a CTE name, or an inlined derived table.

    Not simply the last `FROM` in the statement: an aggregating stage is demoted to a
    derived table (`_demote_agg_stages_to_subqueries`), so the outer source can itself
    contain `FROM` clauses. The outer query begins where the `WITH` list closes.
    """
    outer = sql.rsplit(")\nSELECT ", 1)[-1]
    return outer.split("\nFROM ", 1)[1].strip()


def _cte_names(sql: str) -> list:
    return re.findall(r"(\w+) AS \(\n", sql)


def _projection(sql: str) -> str:
    """The outer `SELECT` list.

    Split on `)\\nSELECT `, which only the outer query matches: the scalar subqueries
    inside the projection open with `(SELECT`, and the CTE bodies' `SELECT`s are all
    inside the `WITH` list that this delimiter closes.
    """
    return sql.rsplit(")\nSELECT ", 1)[-1].rsplit("FROM", 1)[0]


def test_the_outer_query_reads_the_fusion():
    sql = _sql()
    source = _outer_source(sql)

    assert not source.startswith(("BM25_Retrieve", "Vec_Retrieve")), (
        f"the hybrid result is selected out of {source.splitlines()[0]}, not the RRF "
        "fusion, so the statement returns one arm's rows under the fusion's column names"
    )
    # Named `Retrieve` either way: as a CTE, or as the alias of the derived table it is
    # demoted to once its `GROUP BY` is inlined.
    assert source.rstrip().endswith("Retrieve") or source == "Retrieve", source
    assert "SUM(rrf_score)" in source or "Retrieve" in _cte_names(sql), source


def test_both_arms_are_defined_before_the_fusion_reads_them():
    """Fixing the outer `FROM` by reordering the stages would have put the fusion ahead
    of the two CTEs its body references, which is a forward reference in a `WITH` list
    — a different failure, not a fix. Naming the result stage leaves the order alone."""
    sql = _sql()
    names = _cte_names(sql)
    fusion_at = sql.index("SUM(rrf_score)")

    for arm in ("BM25_Retrieve", "Vec_Retrieve"):
        assert arm in names, f"{arm} is not a CTE: {names}"
        assert sql.index(f"{arm} AS (") < fusion_at, (
            f"the fusion reads {arm} before it is defined"
        )


def test_the_fusion_limits_with_top_rather_than_fetch_first():
    """A third rejection at Prepare, once the statement got that far:

        SQLCODE -1 [Location: <Prepare>]  ) expected, IDENTIFIER (ORDER) found

    IRIS allows `ORDER BY` inside a CTE or derived table only when `TOP` accompanies it,
    and the fusion ordered by `rrf_score` with `FETCH FIRST 5 ROWS ONLY` instead.
    Measured on the container: `GROUP BY ... ORDER BY` in a CTE fails, with
    `FETCH FIRST` too, in a derived table too; the same body with `TOP 5` works in both.

    `TOP` also keeps the limit where `FETCH FIRST` had it — this is the fusion's own
    `k`, not an outer paging clause.
    """
    sql = _sql()
    fusion = sql[sql.index("SUM(rrf_score)") - 200 : sql.index("GROUP BY node") + 200]

    assert "FETCH FIRST" not in fusion, fusion
    assert re.search(r"SELECT TOP 5 node, SUM\(rrf_score\)", sql), fusion
    assert "ORDER BY rrf_score DESC" in fusion, fusion


def test_the_rrf_score_is_projected_as_a_scalar():
    sql = _sql()

    assert "rrf_score AS rrf_score_id" not in sql, (
        "rrf_score is projected as a node variable, so its value is used as a node ID"
    )
    assert "rrf_score_labels" not in sql and "rrf_score_props" not in sql, (
        "the projection asks for the labels and properties of a similarity score"
    )


def test_the_node_is_still_projected_as_a_node():
    """The narrow fix would be to treat every yield item as a scalar, which would take
    `node`'s labels and properties away with it."""
    sql = _sql()

    assert "node_labels" in sql and "node_props" in sql, sql[-400:]


@pytest.mark.parametrize("column", ["node", "rrf_score"])
def test_both_yielded_columns_survive_to_the_result(column):
    sql = _sql()

    assert re.search(rf"\b{column}\b", _projection(sql)), (
        f"{column} is yielded but does not appear in the final projection: "
        f"{_projection(sql)}"
    )
