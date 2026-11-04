"""4.6.0 R276, live: a namespace deployed from this checkout has no ``NKGAccel.PPRNative``/``PPRJson`` (nor the
``NKGAccelTraversal`` bodies).

The removed methods were the 2-hop approximation (dangling mass lost, no graph subscript on the legacy layout); R270
left nothing in IVG calling them. Deploy the checkout first (``scripts/enterprise-container.sh tcp-deploy`` with
``IVG_NAMESPACE``), then:

    IVG_NAMESPACE=IVG460FY6 IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/e2e/test_460_r276_nkg_ppr_removed_e2e.py
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

CLASSES = ("Graph.KG.NKGAccel", "Graph.KG.NKGAccelTraversal")


def test_the_compiled_classes_have_no_nkg_ppr(iris_connection):
    cur = iris_connection.cursor()
    try:
        cur.execute(
            "SELECT parent, Name FROM %Dictionary.CompiledMethod WHERE parent IN (?, ?) "
            "AND Name IN ('PPRNative', 'PPRJson')",
            list(CLASSES),
        )
        assert list(cur.fetchall()) == []
        # the classes themselves are there: the query above is not empty for want of them
        cur.execute(
            "SELECT COUNT(*) FROM %Dictionary.CompiledMethod WHERE parent IN (?, ?) AND Name = 'KHopNeighbors'",
            list(CLASSES),
        )
        assert int(cur.fetchone()[0]) == 2
    finally:
        cur.close()


def test_calling_it_is_method_does_not_exist(iris_connection):
    import iris

    native = iris.createIRIS(iris_connection)
    with pytest.raises(RuntimeError, match=r"METHOD DOES NOT EXIST.*PPRNative,Graph\.KG\.NKGAccel"):
        native.classMethodValue("Graph.KG.NKGAccel", "PPRNative", "x", 0.85, 5, 10)

