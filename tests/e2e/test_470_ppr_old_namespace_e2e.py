"""4.7.0 row 9, live: this IVG's ``CALL ivg.ppr ... LIMIT k`` on a namespace set up by an older IVG, never upgraded.

An engine accepts a namespace whose classes and SQL functions an earlier release deployed (ADMIN_GUIDE: run
``initialize_schema()`` after every upgrade; nothing makes you). There ``kg_PPR`` has six parameters, and the LIMIT
pushdown's seventh, ``rowLimit``, failed the query: ``<PARAMETER>zkgPPR^Graph.KG.funckgPPR.1`` (SQLCODE -149). The
engine now reads the function's parameters once (INFORMATION_SCHEMA.PARAMETERS) and sends six to one without
``rowLimit``, logging once that ``initialize_schema()`` brings it up to date: the old function's answer, cut by the
LIMIT in SQL. On a namespace set up by this IVG the seventh goes.

``IVG_OLD_NAMESPACE`` names the old namespace (made by ``scripts/dev/make_scratch_namespace.py`` from a checkout of
the older release, e.g. ``f1a442e7``, 4.6.5's code); ``IVG_NAMESPACE`` one set up by this checkout. On a -> b plus a
loner c from a: a = 1 / 1.85, b = 0.85 / 1.85; the old function has no row for c, this one answers it 0.0.

    IVG_NAMESPACE=IVG470ALG3 IVG_OLD_NAMESPACE=IVG470ALGO IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_ppr_old_namespace_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns463

OLD = os.environ.get("IVG_OLD_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    *ns463.requires_namespace,
    pytest.mark.skipif(not OLD.startswith("IVG470"), reason="IVG_OLD_NAMESPACE must name an IVG470* namespace set up "
                                                            "by an older IVG and never upgraded"),
]

A, B = 1 / 1.85, 0.85 / 1.85
LIMITED = ("CALL ivg.ppr(['a'], 0.85, 100) YIELD node, score RETURN node.node_id AS id, score "
           "ORDER BY score DESC LIMIT 5")
SIX = "CALL ivg.ppr(['a'], 0.85, 100) YIELD node, score RETURN node.node_id AS id, score"


def _engine(ns):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", ns463.PORT, ns, "_SYSTEM", "SYS")
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    eng = IRISGraphEngine(conn, namespace=ns, embedding_dimension=768)
    for n in ("a", "b", "c"):
        eng.create_node(n)
    assert eng.create_edge("a", "R", "b")
    eng.sync()
    return eng


@pytest.fixture(scope="module")
def old():
    eng = _engine(OLD)
    yield eng
    eng.conn.close()


@pytest.fixture(scope="module")
def new():
    eng = _engine(ns463.NS)
    yield eng
    eng.conn.close()


def _rows(eng, q):
    return {r[0]: r[1] for r in eng.execute_cypher(q, {}, raise_on_error=True).rows}


def test_the_old_namespace_is_old(old, new):
    assert old._kg_ppr_row_limit is False
    assert new._kg_ppr_row_limit is True


def test_a_limit_on_the_old_namespace_answers_the_old_function_cut_in_sql(old):
    got = _rows(old, LIMITED)
    assert got == pytest.approx({"a": A, "b": B}, abs=1e-4)
    assert got == pytest.approx(_rows(old, SIX), abs=1e-12)


def test_a_limit_on_a_namespace_set_up_by_this_ivg(new):
    assert _rows(new, LIMITED) == pytest.approx({"a": A, "b": B, "c": 0.0}, abs=1e-4)


def test_initialize_schema_brings_the_old_namespace_up(old):
    """The probe is read again after setup, which re-creates kg_PPR with rowLimit. Last: it upgrades the namespace."""
    old.initialize_schema()
    assert old._kg_ppr_row_limit is True
    assert _rows(old, LIMITED) == pytest.approx({"a": A, "b": B, "c": 0.0}, abs=1e-4)
