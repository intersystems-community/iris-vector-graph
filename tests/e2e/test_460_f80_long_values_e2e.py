"""4.6.0 fixes80, live: no expression cuts a value short (tests/unit/test_460_f80_unsized_varchar_cast.py).

IRIS makes `CAST(x AS VARCHAR)` 30 characters; the translator wrote that cast in the map-literal builder and 12 other
places, and VARCHAR(4096) in string `+`, toString() and coalesce(). Before (measured 2026-11-07 at be6cd07c, 2026.3
and Community 2026.1): `WITH n.title + '-abcdefghijklmnopqrstuvwxyz0123456789' AS s RETURN {a: s}` answered 30
characters of the value, a list in a map was cut mid-element, and `n.t + <5,000 characters>` answered 4,101.

The node, by hand: (:F315 {node_id: 'f315', a31: 31 'a's, b5k: 5,000 'b's}). Each expected value is written from it.
Runs in IVG_NS_IVG460FXGV (an IVG460FX* scratch namespace with IVG set up) on the enterprise test container, or the
IRIS IVG_R314_HOST / IVG_R314_PORT name, under whichever
driver runs it, through both query routes.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NS_IVG460FXGV", "")
#: the enterprise test container by default (tests/e2e/ns460alt.py); IVG_R314_HOST / IVG_R314_PORT name another IRIS
HOST = os.environ.get("IVG_R314_HOST", "localhost")
PORT = int(os.environ.get("IVG_R314_PORT", str(alt.PORT)))
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NS_IVG460FXGV must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(HOST == "localhost" and not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

A31, B5K = "a" * 31, "b" * 5000
N = "MATCH (n:F315 {node_id: 'f315'}) "

CASES = [
    # map literal
    ("map-of-property-31", N + "RETURN {a: n.a31} AS v", {"a": A31}),
    ("map-of-property-5000", N + "RETURN {a: n.b5k} AS v", {"a": B5K}),
    ("map-of-with-string-31", N + "WITH n.a31 + '' AS s RETURN {a: s} AS v", {"a": A31}),
    ("map-of-with-string-5001", N + "WITH n.b5k + 'x' AS s RETURN {a: s} AS v", {"a": B5K + "x"}),
    ("map-of-with-list", N + "WITH [n.a31, n.a31] AS l RETURN {a: l} AS v", {"a": [A31, A31]}),
    ("map-of-nested-map", N + "RETURN {a: {b: n.a31}} AS v", {"a": {"b": A31}}),
    ("map-of-list-literal", N + "RETURN {a: [n.a31, n.b5k]} AS v", {"a": [A31, B5K]}),
    # list literal
    ("list-of-property-5000", N + "RETURN [n.b5k] AS v", [B5K]),
    ("list-of-with-string-5001", N + "WITH n.b5k + 'x' AS s RETURN [s] AS v", [B5K + "x"]),
    ("list-of-with-map", N + "WITH {k: n.a31} AS m RETURN [m] AS v", [{"k": A31}]),
    # collect
    ("collect-5000", N + "RETURN collect(n.b5k) AS v", [B5K]),
    ("collect-in-map", N + "WITH collect(n.a31) AS l RETURN {a: l} AS v", {"a": [A31]}),
    # CASE
    ("case-5000", N + "RETURN CASE WHEN n.b5k IS NOT NULL THEN n.b5k ELSE '' END AS v", B5K),
    ("case-concat-32", N + "RETURN CASE WHEN n.a31 IS NOT NULL THEN n.a31 + 'x' ELSE '' END AS v", A31 + "x"),
    # coalesce
    ("coalesce-5000", N + "RETURN coalesce(n.missing, n.b5k) AS v", B5K),
    ("coalesce-concat-32", N + "RETURN coalesce(n.missing, n.a31 + 'x') AS v", A31 + "x"),
    # toString
    ("tostring-5000", N + "RETURN toString(n.b5k) AS v", B5K),
    ("tostring-concat-5001", N + "RETURN toString(n.b5k + 'x') AS v", B5K + "x"),
    # string concatenation
    ("concat-62", N + "RETURN n.a31 + n.a31 AS v", A31 + A31),
    ("concat-5001", N + "RETURN n.b5k + 'x' AS v", B5K + "x"),
    ("concat-literal-5001", N + "RETURN 'x' + n.b5k AS v", "x" + B5K),
]


@pytest.fixture(scope="module", params=[False, True], ids=["sql", "native"])
def eng(request):
    import iris

    conn = iris.dbapi.connect(hostname=HOST, port=PORT, namespace=NS, username="_SYSTEM", password="SYS")
    e = IRISGraphEngine(conn, embedding_dimension=8, native_cypher=request.param)
    e.execute_cypher("MATCH (n:F315) DETACH DELETE n", raise_on_error=True)
    e.execute_cypher("CREATE (:F315 {node_id: 'f315', a31: $a, b5k: $b})", {"a": A31, "b": B5K}, raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:F315) DETACH DELETE n")
        conn.close()


def _lengths(v):
    """The value with each string as its length, so a failure shows where it was cut."""
    if isinstance(v, str):
        return len(v)
    if isinstance(v, list):
        return [_lengths(x) for x in v]
    if isinstance(v, dict):
        return {k: _lengths(x) for k, x in v.items()}
    return v


@pytest.mark.parametrize("q, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_the_value_comes_back_whole(eng, q, want):
    r = eng.execute_cypher(q, raise_on_error=True)
    assert r.columns == ["v"], r.columns
    [[v]] = r.rows
    assert _lengths(v) == _lengths(want)
    assert v == want
