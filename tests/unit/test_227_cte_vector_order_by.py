"""Spec 227 sweep — a CTE may not `ORDER BY` a bare vector-function alias.

`CALL ivg.vector.search(...)` translated to

    WITH VecSearch AS (
    SELECT TOP 3 e.node_id AS node, VECTOR_COSINE(e.emb, TO_VECTOR(?, DOUBLE)) AS score
    FROM Graph_KG.kg_NodeEmbeddings e
    JOIN Graph_KG.rdf_labels lbl ON lbl.s = e.node_id AND lbl.label = ?
    ORDER BY score DESC
    )
    SELECT ... FROM VecSearch

and every one of `tests/integration/test_cypher_vector_search.py`'s five SQL tests
failed against `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`) with

    SQLCODE -400 [Location: <ServerLoop - Query Open()>]
    <UNDEFINED>%C0o+24^%sqlcq.USER.cls13510.HkWkib98msx.1

Measured on the container, one variable at a time:

- the same statement *without* the CTE wrapper: works
- inside a CTE, `ORDER BY` removed: works
- inside a CTE, ordering by a non-vector alias (`1 AS score`): works
- inside a CTE, the JOIN removed: **still fails**
- inside a CTE, a literal vector instead of a `?`: **still fails**
- `ORDER BY 2 DESC` instead of the alias: **still fails**

So the fault is the server's code generation for ordering a CTE by an alias whose
expression is a vector function, and it does not care about the JOIN, the bind or
the alias form. Two shapes it accepts: hoisting `TOP`/`ORDER BY` into a second CTE
over a plain column, or `CAST(VECTOR_COSINE(...) AS DOUBLE) AS score` in place.

The cast is what the translator emits. It keeps `TOP n` inside the CTE, so the
limit still applies before anything downstream joins to the result — hoisting the
limit outward would change which rows a composed query returns.

This file pins the emitted text. The live gate is
`tests/integration/test_cypher_vector_search.py`, which cannot pass without it.
"""

import json
import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import set_schema_prefix, translate_to_sql


def _sql(cypher: str) -> str:
    set_schema_prefix("Graph_KG")
    translated = translate_to_sql(parse_query(cypher), None)
    sql = translated.sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def _ctes(sql: str) -> dict:
    """`{cte_name: body}` for each `WITH`/`,` stage in the statement.

    Written by hand rather than with a SQL parser because the assertion is about
    *text* the server chokes on: what matters is which expression sits between
    `SELECT` and `ORDER BY` in one stage, not the statement's meaning.
    """
    out = {}
    for match in re.finditer(r"(\w+) AS \(\n(.*?)\n\)(?=,\n|\n)", sql, re.DOTALL):
        out[match.group(1)] = match.group(2)
    return out


VECTOR_FNS = ("VECTOR_COSINE", "VECTOR_DOT_PRODUCT")


def _orders_by_a_bare_vector_score(body: str) -> bool:
    """True when this CTE body scores with a vector function and then orders by it
    without a cast — the shape that fails at Query Open."""
    if "ORDER BY" not in body.upper():
        return False
    for fn in VECTOR_FNS:
        if re.search(rf"(?<!CAST\(){fn}\([^\n]*\) AS score", body):
            return True
    return False


SEARCH = (
    "CALL ivg.vector.search('Gene', 'embedding', "
    + json.dumps([1.0, 0.0, 0.0])
    + ", 3) YIELD node, score RETURN node, score"
)

RETRIEVE = "CALL ivg.retrieve('aspirin', 5) YIELD node, rrf_score RETURN node, rrf_score"


def test_the_vector_search_cte_casts_its_score_before_ordering_by_it():
    body = _ctes(_sql(SEARCH))["VecSearch"]

    assert "ORDER BY score DESC" in body, body
    assert re.search(r"CAST\(VECTOR_COSINE\(.*\) AS DOUBLE\) AS score", body), body


def test_the_vector_search_cte_still_limits_inside_itself():
    """The other fix for the same fault — hoisting `TOP`/`ORDER BY` out of the CTE —
    would apply the limit after a composed query's joins, so a `MATCH` on top of
    `VecSearch` would get a different set of rows. The cast leaves the limit put."""
    body = _ctes(_sql(SEARCH))["VecSearch"]

    assert body.startswith("SELECT TOP 3 "), body


def test_the_hybrid_retrieve_vector_cte_casts_its_score_too():
    """`ivg.retrieve` builds the same shape for its vector arm, and one unfixed arm
    is a whole failed statement."""
    body = _ctes(_sql(RETRIEVE))["Vec_Retrieve"]

    assert "ORDER BY score DESC" in body, body
    assert re.search(r"CAST\(VECTOR_COSINE\(.*\) AS DOUBLE\) AS score", body), body


@pytest.mark.parametrize("cypher", [SEARCH, RETRIEVE], ids=["search", "retrieve"])
def test_no_cte_orders_by_an_uncast_vector_score(cypher):
    """The general form, so a third emission site added later is caught here rather
    than by a -400 from a customer's query."""
    offenders = {
        name: body
        for name, body in _ctes(_sql(cypher)).items()
        if _orders_by_a_bare_vector_score(body)
    }

    assert offenders == {}, (
        "these CTEs order by an uncast vector expression, which IRIS 2026.3 answers "
        f"with SQLCODE -400 <UNDEFINED> at Query Open: {offenders}"
    )


def test_the_detector_would_catch_the_shape_it_is_written_for():
    """A negative control: the check above is only worth having if it fires on the
    text that failed. This is that text, verbatim from the pre-fix translation."""
    broken = (
        "SELECT TOP 3 e.node_id AS node, VECTOR_COSINE(e.emb, TO_VECTOR(?, DOUBLE)) AS score\n"
        "FROM Graph_KG.kg_NodeEmbeddings e\n"
        "ORDER BY score DESC"
    )

    assert _orders_by_a_bare_vector_score(broken)
