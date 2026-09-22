"""Spec 227 sweep — `JSON_TABLE(fn(...))` cannot take `?` inside its source argument.

`ivg.ivf.search` already knows this; the comment at its emission site says so:

    # IRIS does not support ? parameters inside JSON_TABLE(stored_proc(...)) —
    # embed idx_name and query_json as safe SQL literals directly.

Three other procedures never got the same treatment and bind their string arguments
there anyway, so on `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`) the driver
refuses the statement before it runs:

    CALL ivg.bm25.search('default', 'aspirin', 5)
      → <ARGUMENT ERROR> Bad argument given; Details: Incorrect number of parameters
    SELECT ... FROM JSON_TABLE(Graph_KG.kg_BM25(?, ?, 10), ...)
      → <PARAMETER ERROR>; Details: Invalid number of parameters.

The placeholder count matches the parameter count (4 and 4 for `ivg.retrieve`), so
this is not an off-by-one in the translator: IRIS does not count a `?` inside that
argument as a parameter marker at all, and then finds one too many supplied.

Measured on the container, so the fix is inlining and nothing more:

- `JSON_TABLE(kg_BM25('default', 'aspirin', 10), ...)` in a plain SELECT: works
- the same inside a CTE: works
- the same inside a CTE with a `?` in the *outer* query: works
- `SELECT kg_BM25(?, ?, 10)` on its own, binds and all: works

So the function takes binds, `JSON_TABLE`'s source argument does not, and a CTE over
it is fine. Affected: `ivg.bm25.search`, `ivg.ppr`, and `ivg.retrieve`'s BM25 arm —
each one dead on this build, not degraded.

`ivg.retrieve`'s `EMBEDDING(?, ?)` binds sit in a plain CTE rather than inside
`JSON_TABLE`, so they stay as parameters. That is the line this file draws: inline
what IRIS cannot bind, and not one value more.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import set_schema_prefix, translate_to_sql

#: `ivg.bm25.search` and `ivg.retrieve` take the same user-controlled query text, and
#: it is the argument being moved out of a bind and into the statement, so every case
#: here carries a quote in it.
QUOTED = "o'brien"

#: The same text as Cypher source. A quote inside a Cypher string literal is written
#: `\'`, so this is what the caller types and `QUOTED` is what the parser hands the
#: translator — keeping them separate is what makes the escaping assertions meaningful.
QUOTED_CYPHER = r"o\'brien"

CALLS = {
    "bm25.search": (
        f"CALL ivg.bm25.search('default', '{QUOTED_CYPHER}', 5) YIELD node, score "
        "RETURN node, score"
    ),
    "ppr": "CALL ivg.ppr(['ivg:a', 'ivg:b'], 0.85, 20) YIELD node, score RETURN node, score",
    "retrieve": (
        f"CALL ivg.retrieve('{QUOTED_CYPHER}', 5) YIELD node, rrf_score "
        "RETURN node, rrf_score"
    ),
}


def _translate(cypher: str):
    set_schema_prefix("Graph_KG")
    translated = translate_to_sql(parse_query(cypher), None)
    sql = translated.sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    params = translated.parameters or []
    if len(params) == 1 and isinstance(params[0], (list, tuple)):
        params = list(params[0])
    return sql, list(params)


def _json_table_sources(sql: str) -> list:
    """The source argument of each `JSON_TABLE(...)` — the text between the opening
    paren and the `'$[*]'` row pattern that follows it.

    That span is exactly where IRIS stops recognising parameter markers, so it is the
    span the assertions are about; the `COLUMNS(...)` clause after it is irrelevant.
    """
    return [
        match.group(1)
        for match in re.finditer(r"JSON_TABLE\((.*?),\s*'\$\[\*\]'", sql, re.DOTALL)
    ]


@pytest.mark.parametrize("name", sorted(CALLS))
def test_no_placeholder_sits_inside_a_json_table_source(name):
    sql, _ = _translate(CALLS[name])
    sources = _json_table_sources(sql)

    assert sources, f"{name} emitted no JSON_TABLE, so this file is testing the wrong shape"
    offenders = [src for src in sources if "?" in src]
    assert offenders == [], (
        f"{name} binds inside JSON_TABLE's source argument, which IRIS 2026.3 answers "
        f"with 'Incorrect number of parameters' before running anything: {offenders}"
    )


def test_bm25_search_inlines_its_index_and_query_as_escaped_literals():
    sql, params = _translate(CALLS["bm25.search"])

    (source,) = _json_table_sources(sql)
    assert "Graph_KG.kg_BM25('default', 'o''brien', 5)" in source, source
    # And the values are gone from the parameter list rather than inlined *and* bound,
    # which would leave the count right and the values off by two positions.
    assert QUOTED not in params, params
    assert "default" not in params, params


def test_ppr_inlines_its_seed_json():
    """The seed list is JSON, so it is the argument most likely to grow a quote by
    accident — a node ID containing `'` would end the literal early."""
    sql, _ = _translate(CALLS["ppr"])

    (source,) = _json_table_sources(sql)
    assert '["ivg:a", "ivg:b"]' in source, source
    assert "0.85" in source and "20" in source, source


def test_retrieve_inlines_the_bm25_arm_and_keeps_the_embedding_binds():
    """The two arms differ, and the difference is the point: one is inside
    `JSON_TABLE` and cannot be bound, the other is a plain CTE and can."""
    sql, params = _translate(CALLS["retrieve"])

    (source,) = _json_table_sources(sql)
    assert "'o''brien'" in source, source

    assert "EMBEDDING(?, ?)" in sql, "the vector arm lost its binds along with the BM25 arm"
    assert QUOTED in params, (
        f"the query text must still be bound for EMBEDDING(), got {params}"
    )


@pytest.mark.parametrize("name", sorted(CALLS))
def test_the_placeholder_count_still_matches_the_parameter_count(name):
    """Inlining removes binds from two places at once — the statement and the list —
    and a mismatch here is the failure mode of doing only one of them."""
    sql, params = _translate(CALLS[name])

    assert sql.count("?") == len(params), (
        f"{name}: {sql.count('?')} placeholders for {len(params)} parameters: {params}"
    )


def test_a_quote_in_the_query_is_doubled_exactly_once():
    """An inlined literal is only safe if the escaping is right, and double-escaping
    is as wrong as none: `o''''brien` searches for a different string."""
    sql, _ = _translate(CALLS["bm25.search"])

    assert "'o''brien'" in sql
    assert "o''''brien" not in sql
