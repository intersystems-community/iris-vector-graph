"""Eigenvector centrality held to networkx on a fixture's own graph (4.6.0, round-3 walkthrough).

IVG iterates as networkx's ``eigenvector_centrality`` does and raises `PowerIterationFailedConvergence` where it
does, so a test that runs IVG on a known graph asks networkx for the answer rather than checking only the shape. A
chain (a DAG) converges only polynomially under networkx's shift: it raises below about 1000 rounds.
"""

from __future__ import annotations

from typing import Callable, Iterable, Tuple

import networkx as nx
import pytest

from iris_vector_graph import PowerIterationFailedConvergence


def check(run: Callable[[], list], edges: Iterable[Tuple[str, str]], max_iter: int, tol: float) -> list:
    """``run()`` (rows of ``(id, score)``) against ``nx.eigenvector_centrality`` of ``edges``: the same scores for
    the rows it returns, or `PowerIterationFailedConvergence` where networkx raises. Returns the rows."""
    G = nx.DiGraph(list(edges))
    try:
        want = nx.eigenvector_centrality(G, max_iter=max_iter, tol=tol)
    except nx.PowerIterationFailedConvergence:
        with pytest.raises(PowerIterationFailedConvergence):
            run()
        return []
    rows = [(str(r[0]), float(r[1])) for r in run()]
    assert rows, "no rows"
    assert dict(rows) == pytest.approx({i: want[i] for i, _ in rows}, abs=1e-6)
    return rows


def chain(prefix: str, n: int):
    return [(f"{prefix}{i}", f"{prefix}{i + 1}") for i in range(n - 1)]
