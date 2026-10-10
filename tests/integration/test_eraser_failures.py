"""Eraser failure paths: when one store refuses a delete, nothing at all is erased.

A BEFORE DELETE trigger that sets %ok=0 makes one table refuse its rows. The Eraser must then
throw an error naming that store, roll back every delete it already made in the same
transaction, and leave the graph exactly as it was. Scratch namespace IVG236 only.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import World

ER = "Graph.KG.Eraser"
G = "gx"
# table -> a statement that puts one row for graph G in it (after the edge below has edge_id 'EID')
ROWS = {
    "rdf_labels": "INSERT INTO Graph_KG.rdf_labels (s, label, graph_id) VALUES ('n1', 'L', 'gx')",
    "rdf_props": "INSERT INTO Graph_KG.rdf_props (s, \"key\", val, graph_id) VALUES ('n1', 'k', 'v', 'gx')",
    "docs": "INSERT INTO Graph_KG.docs (graph_id, id, \"text\") VALUES ('gx', 'n1', 't')",
    "kg_NodeEmbeddings": "INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id) VALUES ('gx', 'n1')",
    "kg_NodeEmbeddings_optimized": "INSERT INTO Graph_KG.kg_NodeEmbeddings_optimized (graph_id, node_id) VALUES ('gx', 'n1')",
    "kg_EdgeEmbeddings": "INSERT INTO Graph_KG.kg_EdgeEmbeddings (graph_id, s, p, o_id) VALUES ('gx', 'n1', 'R', 'n2')",
    "rdf_reifications": "INSERT INTO Graph_KG.rdf_reifications (reifier_id, edge_id) VALUES ('r1', EID)",
    "rdf_edges": None,  # the edge itself
    "nodes": None,  # the nodes themselves
}


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue(ER, "EraseAll")
    w.renew()
    yield w
    for table in ROWS:
        try:
            w.engine().conn.cursor().execute(f"DROP TRIGGER Graph_KG.ivg_block_{table}")
        except Exception:
            pass
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue(ER, "EraseAll")


def seed(world):
    e = world.engine()
    e.create_node("n1", graph=G)
    e.create_node("n2", graph=G)
    e.create_edge("n1", "R", "n2", graph=G)
    world.commit()
    cur = e.conn.cursor()
    cur.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'n1' AND graph_id = 'gx'")
    eid = cur.fetchone()[0]
    for table, sql in ROWS.items():
        if sql:
            cur.execute(sql.replace("EID", str(eid)))
    world.commit()


def count(world, table):
    cur = world.engine().conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{table}")
    return cur.fetchone()[0]


def block(world, table):
    cur = world.engine().conn.cursor()
    cur.execute(
        f"CREATE TRIGGER Graph_KG.ivg_block_{table} BEFORE DELETE ON Graph_KG.{table} "
        'LANGUAGE OBJECTSCRIPT { Set %ok=0 Set %msg="blocked" }'
    )
    world.commit()


def unblock(world, table):
    world.engine().conn.cursor().execute(f"DROP TRIGGER Graph_KG.ivg_block_{table}")
    world.commit()


OPERATIONS = {
    "graph": lambda n: n.classMethodValue(ER, "EraseGraph", G),
    "nodes": lambda n: n.classMethodValue(ER, "EraseNodes", G, "n"),
    "ids": lambda n: n.classMethodValue(ER, "EraseNodeIds", G, json.dumps(["n1", "n2"])),
}


@pytest.mark.parametrize("op", sorted(OPERATIONS))
@pytest.mark.parametrize("table", sorted(ROWS))
def test_a_refused_delete_erases_nothing(world, table, op):
    seed(world)
    before = {t: count(world, t) for t in ROWS}
    block(world, table)
    with pytest.raises(RuntimeError) as caught:
        OPERATIONS[op](world.native())
    world.scratch.require_scratch(world.native())
    assert f"erase failed on {table}" in str(caught.value) or "blocked" in str(caught.value)
    unblock(world, table)
    assert {t: count(world, t) for t in ROWS} == before, "a partial erase survived the failure"


def test_erase_all_stops_cleanly_when_one_store_refuses(world):
    seed(world)
    before = {t: count(world, t) for t in ROWS}
    block(world, "rdf_labels")
    with pytest.raises(RuntimeError):
        world.native().classMethodValue(ER, "EraseAll")
    world.scratch.require_scratch(world.native())
    unblock(world, "rdf_labels")
    assert {t: count(world, t) for t in ROWS} == before


def test_erase_edges_stops_cleanly_when_the_edge_table_refuses(world):
    seed(world)
    block(world, "rdf_edges")
    edges = json.dumps([{"s": "n1", "p": "R", "o": "n2"}])
    with pytest.raises(RuntimeError):
        world.native().classMethodValue(ER, "EraseEdges", G, edges)
    unblock(world, "rdf_edges")
    assert count(world, "rdf_edges") == 1


def test_erase_edges_rejects_something_that_is_not_an_edge_list(world):
    with pytest.raises(RuntimeError) as caught:
        world.native().classMethodValue(ER, "EraseEdges", G, '{"s": "n1"}')
    assert "EraseEdges expects a JSON array" in str(caught.value)
