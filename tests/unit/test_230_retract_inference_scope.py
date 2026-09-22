"""Spec 230 US1 (FR-001, SC-001) — `retract_inference` deletes one graph's edges.

The release's only data-loss defect. `_engine/schema.py`'s `retract_inference` tested
`if graph:`, and its `else` branch issued

    DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '%"inferred":"true"%'

with no graph predicate at all. So `retract_inference()` *and*
`retract_inference(graph="")` both deleted every graph's inferred edges — including
graphs the caller has never heard of. Inferred edges are derived, but they are not
cheap to derive, and nothing tells the other graph's owner it happened.

The existing tests at `tests/unit/test_schema_unit.py:389` and
`tests/unit/test_schema_final_v10.py:914` call both forms and assert only the returned
row count, which is why this survived: a `DELETE` that is too broad returns a larger
count, and a larger count is still an `int`. These tests read the SQL.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.rowcount = 0
    return IRISGraphEngine(conn, embedding_dimension=4), cursor


def _delete_call(cursor):
    """The DELETE the method issued, as `(sql, params)`."""
    deletes = [
        c
        for c in cursor.execute.call_args_list
        if "DELETE" in str(c.args[0]).upper()
    ]
    assert len(deletes) == 1, f"expected exactly one DELETE, got {len(deletes)}"
    args = deletes[0].args
    return args[0], (args[1] if len(args) > 1 else [])


@pytest.mark.parametrize("graph", [None, ""])
def test_the_default_graph_form_carries_a_graph_predicate(graph):
    """Neither `retract_inference()` nor `retract_inference(graph="")` may be unscoped.

    An empty graph identifier means the default graph — the same meaning spec 214 and
    223 established, and the meaning `cypher/translator.py:163` already implements. It
    has never meant "every graph".
    """
    eng, cursor = _engine()
    eng.retract_inference() if graph is None else eng.retract_inference(graph=graph)
    sql, params = _delete_call(cursor)

    assert "graph_id" in sql, (
        f"retract_inference(graph={graph!r}) issued a DELETE with no graph predicate:\n"
        f"  {sql}\n"
        "Every graph in the namespace loses its inferred edges."
    )
    assert "COALESCE(graph_id, '') = ''" in sql, (
        "the default-graph predicate must match rows written as NULL and as '' — "
        f"got: {sql}"
    )
    assert not params, f"the default-graph form needs no bound graph, got {params}"


def test_a_named_graph_binds_that_graph():
    eng, cursor = _engine()
    eng.retract_inference(graph="g1")
    sql, params = _delete_call(cursor)

    assert "graph_id = ?" in sql, f"expected a bound graph predicate, got: {sql}"
    assert list(params) == ["g1"]
    assert "COALESCE" not in sql, (
        "a named graph must not also match the default graph's rows — "
        f"got: {sql}"
    )


def test_both_forms_still_only_delete_inferred_edges():
    """The scope fix must not widen what the predicate selects."""
    for kwargs in ({}, {"graph": "g1"}):
        eng, cursor = _engine()
        eng.retract_inference(**kwargs)
        sql, _ = _delete_call(cursor)
        assert 'inferred' in sql and 'qualifiers' in sql, (
            f"retract_inference({kwargs}) stopped restricting to inferred edges: {sql}"
        )


def test_the_row_count_is_still_returned():
    """The behaviour the old tests covered, kept."""
    eng, cursor = _engine()
    cursor.rowcount = 7
    assert eng.retract_inference() == 7


# ---------------------------------------------------------------------------
# The marker itself (spec 230 FR-001b)
# ---------------------------------------------------------------------------
#
# A second defect, found while measuring the first. `materialize_inference` marks its
# edges with `{"inferred":true}` — a bare JSON boolean. `retract_inference` looked for
# `"inferred":"true"` — a quoted string. The two never matched, so retraction removed
# nothing the inference pass had written, and still returned a row count of 0 as though
# there had been nothing to remove. `create_edge(qualifiers={"inferred": "true"})` is a
# third spelling again: `json.dumps` puts a space after the colon.
#
# One marker, matched on the key alone, is the only predicate all three writers satisfy
# — and it is already the predicate `materialize_inference` uses at
# `_fetch_edges` to *exclude* inferred edges from its own input.


def _like_matches(pattern: str, value: str) -> bool:
    """Whether SQL `value LIKE pattern` holds, for `%`-only patterns."""
    import re

    parts = [re.escape(p) for p in pattern.split("%")]
    return re.fullmatch(".*?".join(parts), value, re.DOTALL) is not None


def _like_pattern(sql: str) -> str:
    """The LIKE pattern in a statement, unescaped from its SQL literal."""
    import re

    match = re.search(r"LIKE\s+'((?:[^']|'')*)'", sql)
    assert match, f"no LIKE literal found in: {sql}"
    return match.group(1).replace("''", "'")


@pytest.mark.parametrize(
    "written",
    [
        pytest.param('{"inferred":true}', id="materialize_inference"),
        pytest.param('{"inferred": "true"}', id="create_edge-json.dumps"),
        pytest.param('{"inferred":"true"}', id="compact-string"),
        pytest.param('{"weight":2,"inferred":true}', id="alongside-another-qualifier"),
    ],
)
def test_the_predicate_matches_every_marker_a_writer_produces(written):
    eng, cursor = _engine()
    eng.retract_inference()
    sql, _ = _delete_call(cursor)
    pattern = _like_pattern(sql)

    assert _like_matches(pattern, written), (
        f"retract_inference's predicate {pattern!r} does not match {written!r}, which "
        "a writer in this codebase actually stores. Those edges can never be retracted."
    )


@pytest.mark.parametrize(
    "written",
    [
        pytest.param("{}", id="no-qualifiers"),
        pytest.param('{"weight":2}', id="an-unrelated-qualifier"),
        pytest.param('{"inferredBy":"rdfs"}', id="a-longer-key-with-the-same-prefix"),
    ],
)
def test_the_predicate_spares_an_edge_no_one_marked(written):
    """Matching the key alone must not widen into edges a user authored."""
    eng, cursor = _engine()
    eng.retract_inference()
    pattern = _like_pattern(_delete_call(cursor)[0])

    assert not _like_matches(pattern, written), (
        f"retract_inference's predicate {pattern!r} matches {written!r}, which no "
        "writer marked as inferred. A user's own edge would be deleted."
    )


def test_the_marker_is_declared_once_and_shared_by_writer_and_readers():
    """The writer and both readers must not be able to drift apart again."""
    from iris_vector_graph._engine import schema as _schema

    assert _like_matches(_schema.INFERRED_QUALIFIER_LIKE, _schema.INFERRED_QUALIFIER_JSON), (
        f"the module's own marker {_schema.INFERRED_QUALIFIER_JSON!r} does not satisfy "
        f"its own predicate {_schema.INFERRED_QUALIFIER_LIKE!r}"
    )

    eng, cursor = _engine()
    eng.retract_inference()
    assert _like_pattern(_delete_call(cursor)[0]) == _schema.INFERRED_QUALIFIER_LIKE, (
        "retract_inference spells its own pattern instead of using the shared constant"
    )
