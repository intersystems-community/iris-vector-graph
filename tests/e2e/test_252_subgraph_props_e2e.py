"""Spec 252, T024: a subgraph's node properties come from rdf_props, not ^KG("prop").

^KG("prop") was refilled only by BuildKG (and FHIRGraph's own writes), so a node created, or a property set or
removed, through the engine showed its old properties, or none, in kg_SUBGRAPH until the next sync(). The Owner
(Graph.KG.Subgraph) now reads the rows; nothing writes ^KG("prop").

Run against a scratch namespace: `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def pair(iris_master_cleanup, iris_connection):
    t = uuid.uuid4().hex[:8]
    return IRISGraphEngine(iris_connection), f"sp_{t}_a", f"sp_{t}_b"


def _props(engine, seed):
    sg = engine.kg_SUBGRAPH([seed], k_hops=1, edge_types=["R"])  # a filtered walk is the Owner's
    return sg.node_properties


def test_properties_written_after_the_last_sync_are_in_the_subgraph(pair):
    engine, a, b = pair
    engine.create_node(a, labels=["SP"], properties={"name": "alpha"})
    engine.create_node(b, labels=["SP"], properties={"name": "beta"})
    engine.create_edge(a, "R", b)
    props = _props(engine, a)
    assert props.get(a, {}).get("name") == "alpha"
    assert props.get(b, {}).get("name") == "beta"


def test_a_changed_property_is_the_new_value(pair):
    engine, a, b = pair
    engine.create_node(a, labels=["SP"], properties={"name": "alpha"})
    engine.create_node(b, labels=["SP"])
    engine.create_edge(a, "R", b)
    engine.sync()
    cur = engine.conn.cursor()
    cur.execute("UPDATE Graph_KG.rdf_props SET val = 'gamma' WHERE s = ? AND \"key\" = 'name'", [a])
    engine.conn.commit()
    assert _props(engine, a)[a]["name"] == "gamma"


def test_the_prop_store_is_not_written(pair):
    engine, a, b = pair
    engine.create_node(a, labels=["SP"], properties={"name": "alpha"})
    engine.create_node(b, labels=["SP"])
    engine.create_edge(a, "R", b)
    engine.sync()
    assert not int(engine._iris_obj().isDefined("^KG", "prop", 0, a))
