"""Spec 227 — `ivf_build` reads the routed embedding table, scoped to one graph.

`ivf_build` was the one embedding reader routing skipped. It selected `id, emb`
from a hardcoded `kg_NodeEmbeddings`: `id` no longer exists on the 4.0.0 table, and
IRIS answers `WHERE id IN (<node ids>)` on a DDL table by comparing row IDs to
strings — zero rows, no error, and the caller is told "no vectors found" about a
table that holds them under a different key.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(route_table="kg_emb_abc123"):
    """An engine whose only live parts are `conn`, `_t` and the route lookup."""
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine.vector_dtype = "DOUBLE"
    engine._t = lambda name: f"Graph_KG.{name}"
    engine._route_for_read = MagicMock(return_value=(route_table, None))
    cursor = engine.conn.cursor.return_value
    cursor.fetchall.return_value = []
    return engine, cursor


def test_ivf_build_reads_the_routed_table():
    engine, cursor = _engine("kg_emb_abc123")

    with pytest.raises(ValueError) as exc:
        engine.ivf_build("idx", node_ids=["n1", "n2"], graph="g1")

    assert "kg_emb_abc123" in str(exc.value), (
        "ivf_build reported a table it did not read — it must name the route"
    )
    sql = cursor.execute.call_args[0][0]
    assert "kg_emb_abc123" in sql, f"ivf_build ignored the route: {sql}"
    assert "kg_NodeEmbeddings" not in sql, f"ivf_build fell back to the legacy table: {sql}"


def test_ivf_build_selects_node_id_not_the_removed_id_column():
    engine, cursor = _engine()

    with pytest.raises(ValueError):
        engine.ivf_build("idx", node_ids=["n1"], graph="g1")

    sql = cursor.execute.call_args[0][0]
    assert "node_id" in sql, f"ivf_build still reads the 4.0.0-removed `id` column: {sql}"
    assert "SELECT id," not in sql, f"ivf_build still reads `id`: {sql}"


def test_ivf_build_scopes_every_read_to_one_graph():
    engine, cursor = _engine()

    with pytest.raises(ValueError):
        engine.ivf_build("idx", node_ids=["n1"], graph="g1")

    sql, params = cursor.execute.call_args[0][0], cursor.execute.call_args[0][1]
    assert "graph_id" in sql, f"ivf_build reads every graph's vectors: {sql}"
    assert "g1" in params, f"the graph was never bound: {params}"


def test_ivf_build_scopes_a_full_table_build_too():
    """No `node_ids` is still one graph — the predicate is not carried by the IN list."""
    engine, cursor = _engine()

    with pytest.raises(ValueError):
        engine.ivf_build("idx", graph="g1")

    sql, params = cursor.execute.call_args[0][0], cursor.execute.call_args[0][1]
    assert "graph_id" in sql, f"an unfiltered ivf_build scans every graph: {sql}"
    assert "g1" in params, f"the graph was never bound: {params}"


def test_ivf_build_refuses_a_pair_with_no_route():
    """No route means no table holding these vectors — not the legacy table."""
    engine, cursor = _engine(route_table=None)

    with pytest.raises(ValueError) as exc:
        engine.ivf_build("idx", node_ids=["n1"], graph="g1", model_key="m2")

    msg = str(exc.value)
    assert "route" in msg.lower() or "no embedding table" in msg.lower(), (
        f"the refusal does not say why there is nothing to build from: {msg}"
    )
    assert not cursor.execute.called, "ivf_build queried a table it had not resolved"


def test_ivf_build_defaults_to_the_default_graph():
    """`graph=None` is the default graph `''`, never every graph (FR-004)."""
    engine, cursor = _engine()

    with pytest.raises(ValueError):
        engine.ivf_build("idx", node_ids=["n1"])

    assert engine._route_for_read.call_args[0][0] == "", (
        "ivf_build did not resolve the default graph for graph=None"
    )
    params = cursor.execute.call_args[0][1]
    assert "" in params, f"the default graph was never bound: {params}"
