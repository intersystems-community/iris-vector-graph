"""4.6.0 R166: a centrality or community CALL over an unbuilt `^NKG` fails with the reason, not a row of NULLs.

`kg_KCore()` answered the text `ERROR:^NKG not built`, `JSON_TABLE` read the non-JSON text as one row of NULLs, and
`CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness` answered `[[None, None]]` with no error. The SQL
function now throws the text.

Worked by hand: `^NKG` is dropped (`Graph.KG.GraphIndex.DropNKG`, in this namespace only), so the procedure has
nothing to read. The answer is no rows and an error naming `^NKG not built`; `raise_on_error=True` raises it. kcore
(Graph.KG.Communities) and betweenness (Graph.KG.NKGAccelCentrality) stand for the two classes the seven raising
functions call.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

CALLS = {
    "kcore": "CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness",
    "betweenness": "CALL ivg.betweenness({}) YIELD node, score RETURN node, score",
}


@pytest.fixture(scope="module")
def engine(iris_connection):
    import iris

    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    native = iris.createIRIS(iris_connection)
    native.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    assert not native.isDefined("^NKG", "$NI"), "^NKG is still built"
    return e


@pytest.mark.parametrize("proc", sorted(CALLS))
def test_the_reason_is_the_error_and_there_are_no_rows(engine, proc):
    r = engine.execute_cypher(CALLS[proc])
    assert r.rows == [], r.rows
    assert r.error and "^NKG not built" in r.error, r.error


@pytest.mark.parametrize("proc", sorted(CALLS))
def test_raise_on_error_raises_the_reason(engine, proc):
    with pytest.raises(RuntimeError, match=r"\^NKG not built"):
        engine.execute_cypher(CALLS[proc], raise_on_error=True)
