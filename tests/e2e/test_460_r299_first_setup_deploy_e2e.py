"""4.6.0 R299, live: the first 4.6.0 setup on an older namespace compiles every class, and the percentile functions
answer.

On IVGFIX a setup logged "ObjectScript deploy error: IVG.Percentile: ERROR #5083: Stored procedure name is not unique:
Percentile_PCONT". 4.5.1's `IVG.Percentile` projects `PDISC`/`PCONT` itself (`[ SqlProc ]`, SQL schema `IVG`); spec
262 before R084 (commit ac3dd6ae, 35cc70c6^) projected them into `Graph_KG` by mapping package IVG's SQL name there;
4.6.0's projects nothing and `Graph.KG.SqlProcs` projects `Graph_KG.Percentile_PCONT` / `Percentile_PDISC`.

Measured (fixes49): the first 4.6.0 setup is clean from both. The error text is what the pre-R084 code's own setup
says when it runs over a namespace 4.6.0 already set up (its `IVG.Percentile`, and nine Graph.KG classes, collide
with SqlProcs); the next 4.6.0 setup recompiles them and is clean again. Each route is pinned here.

Scratch namespaces: `IVG_NS_IVG460FX299A` (default IVG460FX299A), built by v4.5.1's own code and seed, and
`IVG_NS_IVG460FX299B` (default IVG460FX299B), set up by ac3dd6ae's own code (no seed). Both are built by
`scripts/fixtures/build_release_namespace.py`, which drops and recreates them, and are dropped at the end.

Expected answers, worked from the method bodies in `iris_src/src/IVG/Percentile.cls` over `[1,2,3,4]` at 0.5:
PCONT: pos = (4-1)*0.5 = 1.5, lower = 1, frac = 0.5, so sorted[1] + 0.5*(sorted[2]-sorted[1]) = 2 + 0.5 = 2.5.
PDISC: i = (4-1)*0.5 \\ 1 = 1, so sorted[1] = 2.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

NS_451 = os.environ.get("IVG_NS_IVG460FX299A", "IVG460FX299A")
NS_PRE = os.environ.get("IVG_NS_IVG460FX299B", "IVG460FX299B")
#: spec 262 before R084 (35cc70c6^): IVG.Percentile [ SqlProc ] with package IVG's SQL name mapped to Graph_KG
PRE_R084 = "ac3dd6ae"

pytestmark = [pytest.mark.e2e, ns262.requires_container]

PCONT = "SELECT Graph_KG.Percentile_PCONT('[1,2,3,4]', 0.5)"
PDISC = "SELECT Graph_KG.Percentile_PDISC('[1,2,3,4]', 0.5)"


def _setup(ns: str) -> dict:
    """This checkout's `initialize_schema()` in `ns`: the reply, the deploy result, and what the functions answer."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    conn = ns262.connect(ns)
    try:
        status = IRISGraphEngine(conn, embedding_dimension=8, namespace=ns).initialize_schema()
        deploy = GraphSchema.last_deploy
        return {"status": status, "deploy": deploy,
                "pcont": ns262.rows(conn, PCONT)[0][0], "pdisc": ns262.rows(conn, PDISC)[0][0],
                "projecting": sorted(r[0] for r in ns262.rows(
                    conn, "SELECT parent FROM %Dictionary.CompiledMethod WHERE SqlProc = 1 AND SqlName = ?",
                    ["Percentile_PCONT"]))}
    finally:
        conn.close()


def _assert_clean(run: dict) -> None:
    deploy, status = run["deploy"], run["status"]
    assert deploy is not None and deploy.deployed and deploy.errors == [], deploy.errors
    assert status["objectscript_failed"] == {}, status["objectscript_failed"]
    assert not [w for w in status["warnings"] if w.startswith("ObjectScript deploy")], status["warnings"]
    assert float(run["pcont"]) == 2.5
    assert int(run["pdisc"]) == 2
    assert run["projecting"] == ["Graph.KG.SqlProcs"]


@pytest.fixture(scope="module")
def from_451():
    from build_release_namespace import build

    import make_scratch_namespace

    build("v4.5.1", NS_451)
    try:
        yield _setup(NS_451)
    finally:
        make_scratch_namespace.drop(NS_451)


@pytest.fixture(scope="module")
def from_pre_r084():
    from build_release_namespace import build

    import make_scratch_namespace

    build(PRE_R084, NS_PRE, seed=False)
    try:
        first = _setup(NS_PRE)
        # the old code's setup again, over what 4.6.0 just compiled: the IVGFIX error, from the old code
        build(PRE_R084, NS_PRE, keep=True, seed=False)
        again = _setup(NS_PRE)
        yield {"first": first, "again": again}
    finally:
        make_scratch_namespace.drop(NS_PRE)


def test_the_first_setup_on_a_451_namespace_compiles_every_class(from_451):
    _assert_clean(from_451)


def test_the_first_setup_on_a_pre_r084_namespace_compiles_every_class(from_pre_r084):
    _assert_clean(from_pre_r084["first"])


def test_setup_after_the_pre_r084_code_ran_over_460_compiles_every_class(from_pre_r084):
    _assert_clean(from_pre_r084["again"])
