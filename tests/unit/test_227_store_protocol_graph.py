"""Spec 227 T023 — the store boundary carries a graph, and every side of it agrees.

Gate 4 (constitution VIII) catches a protocol member the mock never grew, by name.
It cannot see a *signature* that drifted: adding `graph` to `GraphStore` while the
mock and the real store keep the 3.2.0 arity leaves `isinstance` happy and every
routing test green, and the graph is silently dropped at the seam.

So these tests compare signatures across all three: the protocol declaration, the
mock, and `IRISGraphStore`. `graph` and `model_key` are keyword-only (contracts/
python-api.md §0) — a positional call written for 3.2.0 must fail loudly rather
than bind a graph to `label_filter`.
"""

import inspect

import pytest

from iris_vector_graph.store_protocol import GraphStore
from tests.unit.test_store_protocol import MockGraphStore

#: The store-level members spec 227 makes graph-aware, and the keyword-only
#: parameters each one gains. `execute_knn_vec` is the only embedding member on
#: this seam: storage goes through the engine's `store_embedding`, which is not a
#: store-protocol member and is covered by its own contract tests.
GRAPH_AWARE = {
    "execute_knn_vec": ("graph", "model_key"),
}

IMPLEMENTATIONS = ["protocol", "mock", "store"]


def _sig(which: str, method: str) -> inspect.Signature:
    if which == "protocol":
        return inspect.signature(getattr(GraphStore, method))
    if which == "mock":
        return inspect.signature(getattr(MockGraphStore, method))
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    return inspect.signature(getattr(IRISGraphStore, method))


@pytest.mark.parametrize("which", IMPLEMENTATIONS)
@pytest.mark.parametrize("method,params", GRAPH_AWARE.items())
def test_graph_aware_member_accepts_the_new_parameters(which, method, params):
    sig = _sig(which, method)
    for name in params:
        assert name in sig.parameters, (
            f"{which}.{method} has no {name!r} parameter; the graph is dropped at "
            f"this seam even though every other side passes it"
        )


@pytest.mark.parametrize("which", IMPLEMENTATIONS)
@pytest.mark.parametrize("method,params", GRAPH_AWARE.items())
def test_the_new_parameters_are_keyword_only(which, method, params):
    """A positional 3.2.0 call must fail, not bind a graph to `label_filter`."""
    sig = _sig(which, method)
    for name in params:
        p = sig.parameters[name]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{which}.{method}({name}=...) is {p.kind.description}; a caller that "
            f"passes it positionally would silently reach the wrong argument"
        )


@pytest.mark.parametrize("method,params", GRAPH_AWARE.items())
def test_default_is_none_meaning_the_default_graph(method, params):
    """`None` is the default graph `''`, not "every graph" (FR-004)."""
    sig = _sig("protocol", method)
    for name in params:
        assert sig.parameters[name].default is None, (
            f"GraphStore.{method}'s {name} must default to None; a non-None default "
            f"would make an omitted graph mean something the caller never wrote"
        )


@pytest.mark.parametrize("method", sorted(GRAPH_AWARE))
def test_the_three_signatures_are_identical(method):
    """The mock is only useful if it is wrong in the same ways as the real store."""
    names = {which: [p for p in _sig(which, method).parameters] for which in IMPLEMENTATIONS}
    assert names["protocol"] == names["mock"] == names["store"], names


def test_the_mock_passes_the_graph_on():
    """A mock that accepts `graph` and drops it hides exactly the bug Gate 4 exists for."""
    store = MockGraphStore()
    store.execute_knn_vec([0.1, 0.2], 5, None, graph="g:a", model_key="m")
    assert store.last_call["graph"] == "g:a"
    assert store.last_call["model_key"] == "m"


# --- T034: the store's own fallback statement -----------------------------------


def test_the_client_side_fallback_selects_node_id_not_id():
    """`IRISGraphStore.execute_knn_vec` falls back to SQL when the deployed
    ObjectScript has the 3.2.0 arity. That fallback selected `id`, which the
    re-keyed embedding table no longer has — so on exactly the installs the
    fallback exists to rescue, it would fail with SQLCODE -29 and return an empty
    result that reads like "no neighbours" rather than "wrong column"."""
    import re
    from unittest.mock import MagicMock

    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    store = IRISGraphStore(conn)
    # Force the fallback: the ObjectScript call must fail.
    store._call_classmethod = MagicMock(side_effect=RuntimeError("3.2.0 arity"))

    store.execute_knn_vec([0.1, 0.2], 5, None, graph="ivg227-A")

    statements = [
        " ".join(str(c.args[0]).split())
        for c in cursor.execute.call_args_list
        if c.args and "VECTOR_COSINE" in str(c.args[0])
    ]
    assert statements, "the fallback issued no statement"
    for sql in statements:
        assert not re.search(r"\bTOP \d+ id\b", sql), (
            f"the fallback still projects the removed `id` column: {sql}"
        )
        assert "node_id" in sql, sql
        assert "COALESCE" in sql, f"the fallback went out unscoped: {sql}"


def test_the_two_fallback_stubs_accept_a_graph():
    """`_kg_KNN_VEC_python_optimized` and `_kg_KNN_VEC_client_side` are the names the
    engine mixin calls. The store overrides them as empty stubs; a stub that rejects
    `graph=` turns a scoped call into a TypeError only on the fallback path, which
    is the path nobody exercises until it matters."""
    from unittest.mock import MagicMock

    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore(MagicMock())
    assert store._kg_KNN_VEC_python_optimized("[0.1]", 5, None, graph="g").rows == []
    assert store._kg_KNN_VEC_client_side("[0.1]", 5, None, graph="g").rows == []
