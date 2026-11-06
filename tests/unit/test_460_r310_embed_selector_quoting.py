"""The embed selectors quote every value they put in SQL (sec-audit's fix on release-4.6.0, pinned again here by
fixes64, R310, for the edge selector and the LIKE text too).

`build_node_where` / `build_edge_where` build a WHERE body, not a parameterised statement: a node id, label, predicate
or exclude pattern holding a quote must stay a literal. `embed_nodes` selecting many node ids a run at a time (IRIS
refuses 1000 literals in a statement) is pinned live in tests/e2e/test_460_r310_long_id_lists_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where, build_node_where

EVIL = "x') OR 1=1 --"


def _closed(sql: str) -> bool:
    """Every quote is paired: no literal is left open or closed early."""
    return sql.count("'") % 2 == 0 and "'x'') OR 1=1 --'" in sql


def test_a_node_id_with_a_quote_stays_a_literal():
    sql = build_node_where(EmbedSelector(node_ids=[EVIL, "b"]))
    assert "node_id IN ('x'') OR 1=1 --', 'b')" in sql, sql


def test_a_label_with_a_quote_stays_a_literal():
    sql = build_node_where(EmbedSelector(label=EVIL))
    assert _closed(sql), sql


def test_an_exclude_pattern_with_a_quote_stays_a_literal():
    sql = build_node_where(EmbedSelector(exclude_pattern="a'b*"))
    assert "node_id NOT LIKE 'a''b%'" in sql, sql


def test_edge_selector_values_with_quotes_stay_literals():
    sql = build_edge_where(EmbedSelector(predicate=EVIL, source_label=EVIL, target_label=EVIL, exclude_pattern="a'b"))
    assert sql.count("'x'') OR 1=1 --'") == 3, sql
    assert "s NOT LIKE 'a''b' AND o_id NOT LIKE 'a''b'" in sql, sql
