"""`khop` answers through the ObjectScript walk whether or not Arno is loaded (4.6.0 R095).

The library's `KHopNeighbors` answers `{"seed", "hops", "totalNodes", "nodes": [{"id", "dist"}]}` with no edges, so
routing hops above 1 to it changed the shape of the answer. The live answer is pinned in
tests/e2e/test_460_khop_shape_e2e.py, with and without the library.
"""

from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._arno_capabilities = {"algorithms": ["khop"]}
    return eng


@pytest.mark.parametrize("hops", [1, 2, 3])
@pytest.mark.parametrize("arno", [True, False])
def test_khop_never_calls_the_library(hops, arno):
    eng = _engine()
    with patch.object(eng, "_detect_arno", return_value=arno), \
         patch.object(eng, "_arno_call") as library, \
         patch.object(eng, "_khop_fallback", return_value={"nodes": ["seed"], "edges": []}) as walk:
        assert eng.khop("seed", hops=hops) == {"nodes": ["seed"], "edges": []}
    library.assert_not_called()
    walk.assert_called_once_with("seed", hops, 500)
