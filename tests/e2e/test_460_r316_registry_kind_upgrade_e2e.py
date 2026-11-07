"""4.6.0 R316, live: a 4.5.1 install's registry row for `kg_EdgeEmbeddings` says `node`; 4.6.0's setup makes it `edge`,
and `initialize_schema(dry_run=True)` changes nothing.

4.5.1's setup adopted every pre-registry vector table into `embedding_registry` at the column's default kind, `node`,
`kg_EdgeEmbeddings` (keyed `(s, p, o_id)`) among them. The default graph then had three node routes, read in no order
by the REST routes' `Graph.KG.PyOps.getVectorTable` and `getExpectedDimension`, so a search could take the width of
the edge table, or of `kg_NodeEmbeddings_optimized`, which the `/kg` role may not read. This builds the namespace with
4.5.1's own code (scripts/fixtures/build_release_namespace.py), checks the row is `node` there, runs a dry run (the
registry rows and the edge row are as before), then a setup (the edge row is `edge`, the node rows are unchanged), and
asks PyOps for the default graph's table: `kg_NodeEmbeddings`.

Namespace: IVG_R316_UPG_NS (the gate names one per run), dropped and built anew.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_R316_UPG_NS", "")
if not NS:  # the E2E gate names one per run (tests/e2e/gate.py GROUPS); never a fixed default
    pytest.skip("set IVG_R316_UPG_NS to a scratch namespace of this run", allow_module_level=True)
RELEASE = "v4.5.1"

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "fixtures"))


def _registry(conn) -> list:
    return sorted(ns262.rows(conn, "SELECT table_name, graph_id, COALESCE(kind, 'node'), COALESCE(model_key, ''), "
                                   "dimension FROM Graph_KG.embedding_registry"))


@pytest.fixture(scope="module")
def built():
    from build_release_namespace import build

    if not NS.startswith(("IVG460FX", "IVG262")):
        pytest.fail(f"refusing {NS}: this file rebuilds its namespace, an IVG460FX*/IVG262* scratch namespace only")
    build(RELEASE, NS)
    conn = ns262.connect(NS)
    yield conn
    conn.close()


def test_the_451_install_records_kg_edge_embeddings_as_a_node_route(built):
    # the starting point the defect needs, made by 4.5.1's own setup
    kinds = {t: k for t, _, k, _, _ in _registry(built) if t.lower() == "kg_edgeembeddings"}
    assert kinds == {"kg_EdgeEmbeddings": "node"}, _registry(built)


def test_a_dry_run_changes_no_registry_row(built):
    from iris_vector_graph import IRISGraphEngine

    before = _registry(built)
    IRISGraphEngine(built, embedding_dimension=8, namespace=NS).initialize_schema(dry_run=True)
    assert _registry(built) == before


def test_setup_records_the_edge_table_as_an_edge_route_and_leaves_the_rest(built):
    from iris_vector_graph import IRISGraphEngine

    before = {r[0]: r for r in _registry(built)}
    IRISGraphEngine(built, embedding_dimension=8, namespace=NS).initialize_schema()
    after = {r[0]: r for r in _registry(built)}
    assert after["kg_EdgeEmbeddings"][2] == "edge"
    assert {t: r for t, r in after.items() if t != "kg_EdgeEmbeddings" and t in before} == {
        t: r for t, r in before.items() if t != "kg_EdgeEmbeddings"}


def test_the_rest_routes_read_kg_node_embeddings_for_the_default_graph(built):
    import iris

    native = iris.createIRIS(built)
    assert native.classMethodValue("Graph.KG.PyOps", "getVectorTable", "", "") == "Graph_KG.kg_NodeEmbeddings"


def test_a_second_setup_changes_nothing(built):
    from iris_vector_graph import IRISGraphEngine

    before = _registry(built)
    IRISGraphEngine(built, embedding_dimension=8, namespace=NS).initialize_schema()
    assert _registry(built) == before
