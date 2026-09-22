"""Every `translate_to_sql` call inside the package has to hand over the engine.

`context._engine` is not decoration. It decides whether a `LIMIT` becomes `TOP n` or
`FETCH FIRST n ROWS ONLY`, and on the IRIS AI builds this project tests against the second
form SIGSEGVs in `%qaqpre` when it sits on a multi-table JOIN over VARCHAR keys — an
uncatchable native fault that takes the whole pytest process with it, not an exception a
test can report. It also drives label-to-table mapping and the child-graph scoping.

So a caller that omits the engine does not degrade politely; it re-arms a crash the product
already knows how to avoid. Two such callers live inside the package, and both are pinned
here.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

#: The shape that killed a full-suite run: five joined tables, VARCHAR keys, and a LIMIT.
MULTI_JOIN_LIMIT = (
    "MATCH (t:Transaction)-[r:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) "
    "RETURN t.node_id, r LIMIT 5"
)

UNION_WITH_LIMITS = (
    "MATCH (a:Account) RETURN a.node_id AS x LIMIT 5 "
    "UNION "
    "MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(b:Account) RETURN t.node_id AS x LIMIT 5"
)


def _unsafe_engine():
    """An engine on a build where `FETCH FIRST` + JOIN crashes."""
    engine = MagicMock()
    engine._fetch_first_unsafe = True
    engine._schema_prefix = "Graph_KG"
    engine.get_table_mapping.return_value = None
    return engine


def test_a_multi_join_limit_becomes_top_when_the_engine_is_passed():
    sql = translate_to_sql(parse_query(MULTI_JOIN_LIMIT), {}, engine=_unsafe_engine()).sql
    assert "TOP 5" in sql
    assert "FETCH FIRST" not in sql


def test_the_same_query_without_an_engine_emits_the_crashing_form():
    """Not an endorsement — the trap, recorded.

    A caller who reaches past the engine straight into the translator gets the shape
    that faults. `tests/integration/conftest.py`'s `execute_cypher` did exactly this.
    """
    sql = translate_to_sql(parse_query(MULTI_JOIN_LIMIT), {}).sql
    assert "FETCH FIRST 5 ROWS ONLY" in sql


def test_every_union_branch_is_translated_with_the_engine():
    """`_tts_union_branches` rebuilds each branch and re-enters `translate_to_sql`.

    It dropped `engine` on the way in, so a `UNION` whose branch carries a `LIMIT` over
    joined tables emitted the crashing form even when the caller did pass an engine.
    """
    sql = translate_to_sql(parse_query(UNION_WITH_LIMITS), {}, engine=_unsafe_engine()).sql
    assert "FETCH FIRST" not in sql, (
        "a UNION branch was translated without the engine, so it re-armed the "
        f"%qaqpre SIGSEGV:\n{sql}"
    )
    assert sql.count("TOP 5") == 2


def test_approx_count_distinct_hands_the_engine_to_the_translator(monkeypatch):
    """The engine calling the translator without naming itself is the same defect indoors."""
    from iris_vector_graph.cypher import translator as translator_module
    from iris_vector_graph.engine import IRISGraphEngine

    seen = {}
    real = translator_module.translate_to_sql

    def spy(cypher_query, params=None, engine=None, procedures=None):
        seen["engine"] = engine
        result = real(cypher_query, params, engine=engine, procedures=procedures)
        result.var_length_paths = []  # short-circuit the caller; we only want the kwargs
        return result

    monkeypatch.setattr(translator_module, "translate_to_sql", spy)

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._schema_prefix = "Graph_KG"
    match = MagicMock()
    match.group.return_value = "c"

    engine._execute_approx_count_distinct(
        "MATCH (n:Account)-[*1..2]->(m) RETURN approx_count_distinct(m) AS c",
        {},
        match,
    )

    assert seen["engine"] is engine


@pytest.mark.parametrize(
    "cypher",
    [
        MULTI_JOIN_LIMIT,
        UNION_WITH_LIMITS,
    ],
)
def test_the_guard_leaves_a_safe_build_alone(cypher):
    """`TOP` is the workaround, not the goal: a build that does not crash keeps `FETCH FIRST`."""
    engine = _unsafe_engine()
    engine._fetch_first_unsafe = False
    sql = translate_to_sql(parse_query(cypher), {}, engine=engine).sql
    assert "FETCH FIRST" in sql
