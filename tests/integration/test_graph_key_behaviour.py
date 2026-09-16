"""Graph.KG.GraphKey's two derivations, and the two graph names it refuses.

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_graph_key_behaviour.py

Two names cannot be represented and must be rejected rather than silently
aliased onto the default graph:

`"0"` — IRIS canonicalizes the subscript `"0"` to the integer `0`, so
`^KG("out", "0", ...)` and `^KG("out", 0, ...)` are the same node. A graph
literally named `"0"` would share the default graph's adjacency (ADR-0001).

`$Char(1)` — the ledger's sentinel for the default graph
(`^IVG.Ledger("tuple", s, p, o, $Char(1))`). More generally, any name made
entirely of control characters strips to the empty string, which is the default
graph. Accepting it would make a named graph's tuples indistinguishable from the
default graph's.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

LEDGER_DEFAULT = chr(1)


def _iris_obj(conn):
    import iris as _iris

    return _iris.createIRIS(conn)


def _call(conn, method: str, graph: str) -> str:
    return str(_iris_obj(conn).classMethodValue("Graph.KG.GraphKey", method, graph))


# --- the index derivation: default graph is 0 -------------------------------


def test_for_index_maps_the_default_graph_to_zero(iris_connection):
    assert _call(iris_connection, "ForIndex", "") == "0"


def test_for_index_passes_a_named_graph_through(iris_connection):
    assert _call(iris_connection, "ForIndex", "acme") == "acme"


def test_for_index_strips_control_padding(iris_connection):
    """IRIS SQL yields $Char(0) for an empty VARCHAR, which means default graph."""
    assert _call(iris_connection, "ForIndex", chr(0)) == "0"


# --- the ledger derivation: default graph is $Char(1) -----------------------


def test_for_ledger_maps_the_default_graph_to_its_sentinel(iris_connection):
    assert _call(iris_connection, "ForLedger", "") == LEDGER_DEFAULT


def test_for_ledger_passes_a_named_graph_through(iris_connection):
    assert _call(iris_connection, "ForLedger", "acme") == "acme"


def test_for_ledger_strips_control_padding(iris_connection):
    """The drift ForLedger exists to remove: GKey() never called $ZStrip."""
    assert _call(iris_connection, "ForLedger", chr(0)) == LEDGER_DEFAULT


# --- the two names that cannot be represented ------------------------------


@pytest.mark.parametrize("method", ["ForIndex", "ForLedger"])
def test_the_graph_named_zero_is_rejected(iris_connection, method):
    with pytest.raises(Exception) as excinfo:
        _call(iris_connection, method, "0")
    assert "0" in str(excinfo.value)


@pytest.mark.parametrize("method", ["ForIndex", "ForLedger"])
def test_a_control_character_graph_name_is_rejected(iris_connection, method):
    """$Char(1) is the ledger's default-graph sentinel."""
    with pytest.raises(Exception):
        _call(iris_connection, method, chr(1))


def test_validate_accepts_an_ordinary_name(iris_connection):
    """Validate returns a %Status; 1 is $$$OK."""
    assert str(
        _iris_obj(iris_connection).classMethodValue(
            "Graph.KG.GraphKey", "Validate", "acme"
        )
    ) == "1"


# --- Ledger.GKey keeps its contract while delegating -----------------------


def test_ledger_gkey_still_returns_the_sentinel_for_the_default_graph(iris_connection):
    assert str(
        _iris_obj(iris_connection).classMethodValue("Graph.KG.Ledger", "GKey", "")
    ) == LEDGER_DEFAULT


def test_ledger_gkey_now_rejects_the_graph_named_zero(iris_connection):
    with pytest.raises(Exception):
        _iris_obj(iris_connection).classMethodValue("Graph.KG.Ledger", "GKey", "0")
