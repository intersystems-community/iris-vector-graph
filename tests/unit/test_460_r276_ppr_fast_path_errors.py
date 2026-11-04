"""4.6.0 R276: ``kg_PERSONALIZED_PAGERANK``'s ObjectScript fast path falls back to Python only when
``Graph.KG.PageRank.RunJson`` is not there to call; any other failure raises.

The fast path (reached when the store does not answer PPR) wrapped ``RunJson`` in ``except Exception``, logged, and
answered from the Python fallback. That hid every failure of the walk itself: a ``<PARAMETER>`` from a RunJson
older than the arguments sent (a namespace whose classes predate this IVG), an ``<UNDEFINED>`` inside the walk, a
lost connection. The store's route raises on the same errors (4.6.0 R127). Not there means the error names
RunJson or Graph.KG.PageRank as the thing missing at the call (``%SYS.DBSRV``'s ``<CLASS DOES NOT EXIST>`` /
``<METHOD DOES NOT EXIST>``): ``objectscript_deployed`` reads the class dictionary, which a class defined but not
compiled is in. The messages are the ones IRIS gives (probed on ivg-iris-enterprise, IVG460FY6).

The branch is pinned: the store reports no PPR, the capabilities say the classes are deployed and ^KG built, and
the Python fallback is patched to a sentinel (or to fail, where it must not run).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

NOT_COMPILED = "<CLASS DOES NOT EXIST>XDCall+11^%SYS.DBSRV.1 *Graph.KG.PageRank"
NO_RUNJSON = "<METHOD DOES NOT EXIST>XDCall+11^%SYS.DBSRV.1 *RunJson,Graph.KG.PageRank"
SENTINEL = {"from": 1.0}


def _engine(error):
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._store_capabilities = {**eng._store_capabilities, "ppr": False}
    eng.capabilities = MagicMock(objectscript_deployed=True, kg_built=True)
    native = MagicMock()
    native.classMethodValue.side_effect = RuntimeError(error)
    return eng, native


@pytest.mark.parametrize("error", [NOT_COMPILED, NO_RUNJSON])
def test_runjson_not_there_falls_back_to_python(error):
    eng, native = _engine(error)
    with patch.object(eng, "_iris_obj", return_value=native), patch.object(
        eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", return_value=SENTINEL
    ) as fallback:
        assert eng.kg_PERSONALIZED_PAGERANK(["a"]) is SENTINEL
    assert native.classMethodValue.call_args.args[:2] == ("Graph.KG.PageRank", "RunJson")
    fallback.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        "<PARAMETER>RunJson^Graph.KG.PageRank.1",
        "<UNDEFINED>Run+40^Graph.KG.PageRank.1 *ranks(\"a\")",
        # a class the walk calls is missing: not RunJson missing, a broken deploy
        "<CLASS DOES NOT EXIST>Run+3^Graph.KG.PageRank.1 *Graph.KG.GraphKey",
        "<METHOD DOES NOT EXIST>Run+3^Graph.KG.PageRank.1 *Key,Graph.KG.GraphKey",
        "Connection reset by peer",
    ],
)
def test_any_other_runjson_failure_raises(error):
    eng, native = _engine(error)
    with patch.object(eng, "_iris_obj", return_value=native), patch.object(
        eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ), pytest.raises(RuntimeError, match=r"RunJson.*" + error[:12].replace("<", r"\<").replace(">", r"\>")):
        eng.kg_PERSONALIZED_PAGERANK(["a"])


def test_parameter_error_says_the_classes_predate_this_ivg():
    """A custom tolerance sends RunJson's pTol (R270); a RunJson from before that answers <PARAMETER>."""
    eng, native = _engine("<PARAMETER>RunJson^Graph.KG.PageRank.1")
    with patch.object(eng, "_iris_obj", return_value=native), patch.object(
        eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ), pytest.raises(RuntimeError, match=r"initialize_schema"):
        eng.kg_PERSONALIZED_PAGERANK(["a"], tolerance=1e-9)


def test_an_unreadable_answer_raises():
    eng, native = _engine("unused")
    native.classMethodValue.side_effect = None
    native.classMethodValue.return_value = "not json"
    with patch.object(eng, "_iris_obj", return_value=native), patch.object(
        eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ), pytest.raises(RuntimeError, match=r"RunJson"):
        eng.kg_PERSONALIZED_PAGERANK(["a"])


def test_the_answer_still_comes_from_runjson():
    eng, native = _engine("unused")
    native.classMethodValue.side_effect = None
    native.classMethodValue.return_value = '[{"id": "a", "score": 0.6}, {"id": "b", "score": 0.4}]'
    with patch.object(eng, "_iris_obj", return_value=native), patch.object(
        eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ):
        assert eng.kg_PERSONALIZED_PAGERANK(["a"]) == {"a": 0.6, "b": 0.4}
