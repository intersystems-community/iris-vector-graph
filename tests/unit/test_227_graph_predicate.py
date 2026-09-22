"""Spec 227 T010 — the graph predicate COALESCEs both sides (FR-005).

Two separate facts make a bare `graph_id = ?` wrong for the default graph:

1. **The left side has two spellings.** `graph_id` is nullable in tables that
   gained it by `ADD COLUMN`; `create_edge` writes `''` explicitly while an INSERT
   that omits the column leaves `NULL`. Both mean the default graph.
2. **The right side collapses.** In IRIS embedded SQL an empty host variable binds
   as SQL `NULL`, not as the empty string, so `graph_id = :tName` for the default
   graph compiles to `graph_id = NULL` and matches nothing — silently, with
   `SQLCODE 100`.

Either omission turns a default-graph query into a zero-row answer that looks like
an empty graph. `Graph.KG.Eraser` already carries this comment; the generated
Python-side SQL has to follow the same rule.
"""

import re

import pytest

from iris_vector_graph.routing import graph_scope_predicate


def test_both_sides_are_coalesced():
    sql = graph_scope_predicate()
    assert sql.count("COALESCE") == 2, (
        "both sides must be COALESCEd — one side is the bug this test exists for: "
        + sql
    )


def test_default_is_the_empty_string_not_null():
    sql = graph_scope_predicate()
    # Two COALESCE defaults, both '' — not NULL, not 0, not a literal 'default'.
    assert len(re.findall(r"COALESCE\([^)]*,\s*''\s*\)", sql)) == 2, sql


def test_placeholder_appears_exactly_once():
    """A duplicated `?` silently shifts every later parameter by one."""
    assert graph_scope_predicate().count("?") == 1


def test_column_is_qualified_when_a_table_alias_is_given():
    sql = graph_scope_predicate(column="e.graph_id")
    assert "e.graph_id" in sql
    assert re.search(r"(?<!\.)\bgraph_id\b", sql.replace("e.graph_id", "")) is None, (
        "an unqualified graph_id leaks into a join and IRIS reports it ambiguous: "
        + sql
    )


def test_default_column_is_unqualified_graph_id():
    assert "graph_id" in graph_scope_predicate()


def test_predicate_is_parenthesised_so_it_survives_an_or():
    """Callers concatenate this into a WHERE clause that may already hold an OR."""
    sql = graph_scope_predicate()
    assert sql.startswith("(") and sql.endswith(")"), (
        "an unparenthesised predicate binds wrong inside `a OR b AND <pred>`: " + sql
    )


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_column_name_is_refused(bad):
    """An empty column name would generate `COALESCE(, '')`, which is a syntax error
    discovered at execute time in whichever query happened to run first."""
    with pytest.raises((ValueError, TypeError)):
        graph_scope_predicate(column=bad)


def test_no_string_interpolation_of_the_graph_value():
    """The graph ID is a bound parameter, never inlined.

    A graph ID is caller-supplied (FR-032), so inlining it is an injection point
    as well as a plan-cache explosion.
    """
    sql = graph_scope_predicate()
    assert "'" not in sql.replace("''", ""), sql
