"""4.6.0 R155: a weighted-path search that fails is an error (`result.error`, or raised with `raise_on_error=True`);
it does not answer "no path".

The handler caught any exception from `DijkstraJson`, logged it and answered no rows, and an answer that was not JSON
was no rows too: a failed search read as "there is no path". The store's `execute_weighted_shortest_path` did the
same, and sent `max_hops` where `DijkstraJson` takes `maxCost`; nothing has called it since R140 (the translation is
a CTE over `DijkstraPath`), so it is gone, with the engine's route to it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import engine as engine_module
from iris_vector_graph._engine import query as query_module
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

CALL = "CALL ivg.shortestPath.weighted('a', 'd') YIELD path, totalCost"


def _run(reply, **opts):
    def fake(conn, cls, method, *args):
        if isinstance(reply, Exception):
            raise reply
        return reply

    with patch.object(engine_module, "_call_classmethod", fake):
        return IRISGraphEngine(MagicMock()).execute_cypher(CALL, {}, **opts)


def test_a_failed_search_is_the_results_error_not_no_path():
    """Nothing is swallowed: the failure is `result.error`, with no rows, as for any failed read (USER_GUIDE §3)."""
    r = _run(RuntimeError("<STORE> DijkstraJson+12^Graph.KG.TraversalPaths.1"))
    assert r.rows == [] and "<STORE>" in r.error


def test_a_failed_search_raises_with_raise_on_error():
    with pytest.raises(RuntimeError, match="<STORE>"):
        _run(RuntimeError("<STORE> DijkstraJson+12^Graph.KG.TraversalPaths.1"), raise_on_error=True)


def test_an_answer_that_is_not_json_raises():
    with pytest.raises(ValueError):
        _run("ERROR: not a path")


def test_no_path_is_still_no_row():
    assert _run("{}").rows == []


def test_the_dead_store_route_is_gone():
    assert not hasattr(IRISGraphStore, "execute_weighted_shortest_path")
    assert not hasattr(query_module.QueryMixin, "_execute_weighted_shortest_path")
