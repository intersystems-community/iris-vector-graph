"""Spec 262 T016: every function the translator can emit is in the IVG Schema, and answers there (FR-001, FR-007).

The names come from the translator's own `_fn(...)` call sites, so a function it starts calling is checked without an
edit here. Runs in IVG262REC, created when absent and initialized by this module with the default schema: not
IVG262ALT, whose IVG Schema is `IVGALT` and which refuses a `Graph_KG` engine once recorded (FR-006).

The second half (4.6.0 R084) answers every SQL procedure IVG's classes project, by hand-worked value, in two
namespaces made anew: IVG262FPG (`Graph_KG`) and IVG262FPA (`IVGALT`).
Scratch namespaces: `IVG_NS_IVG262FPA`, `IVG_NS_IVG262FPG`, `IVG_NS_IVG262REC` (defaults IVG262FPA, IVG262FPG,
IVG262REC). The file drops and rebuilds them (4.6.0 R191): point each at a namespace no other run is using.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tests.e2e import ns262
from tests.unit.test_schema_classes import EXPECTED_SQL_NAMES

pytestmark = [pytest.mark.integration, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG262REC", "IVG262REC")
TRANSLATOR = Path(__file__).resolve().parents[2] / "iris_vector_graph" / "cypher" / "translator.py"
#: `_fn(...)` sites whose name is built, not literal.
BUILT = {"Percentile_PDISC", "Percentile_PCONT", "CY_VLP_ENDS", "CY_VLP_PATHS"}


def emitted() -> set:
    text = TRANSLATOR.read_text(encoding="utf-8")
    return {n for n in re.findall(r"""_fn\(\s*['"](\w+)['"]""", text) if not n.endswith("_")} | BUILT


@pytest.fixture(scope="module")
def setup():
    import make_scratch_namespace

    make_scratch_namespace.make(NS)  # no-op when it exists
    conn = ns262.connect(NS)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS)
    engine.initialize_schema(rewidth=True)
    yield conn, engine._schema_prefix
    conn.close()


def test_the_translator_emits_its_helpers_through_fn():
    assert len(emitted()) >= 30


def test_every_emitted_function_is_in_the_ivg_schema(setup):
    conn, schema = setup
    assert emitted() - ns262.routines(conn, schema) == set()


CALLS = [
    ("JSON_ARRAYLENGTH('[1,2,3]')", 3),
    ("JSON_ARRAYGET('[10,20,30]', 1)", "20"),
    ("JSON_VALUE('{\"a\":\"x\"}', '$.a')", "x"),
    ("LIST_TAIL('[7,8,9]')", "[8,9]"),
    ("LIST_CONCAT('[1]', '[2]')", "[1,2]"),
    ("STR_SPLIT('a,b', ',')", '["a","b"]'),
    ("REGEX_MATCH('abc', 'a.c')", 1),
    ("CY_REVERSE('abc')", "cba"),
    ("JSON_KEYS('{\"a\":1}')", '["a"]'),
    ("CypherFn_IVGRANGE(1, 3, 1)", "[1,2,3]"),
    ("Percentile_PDISC('[1,2,3]', 0.5)", "2"),
]


@pytest.mark.parametrize("call, expected", CALLS, ids=[c.split("(")[0] for c, _ in CALLS])
def test_a_direct_call_in_the_ivg_schema_answers(setup, call, expected):
    conn, schema = setup
    value = ns262.rows(conn, f"SELECT {schema}.{call}")[0][0]
    assert (int(value) if isinstance(expected, int) else str(value)) == expected


def test_rand_is_a_fraction(setup):
    conn, schema = setup
    value = float(ns262.rows(conn, f"SELECT {schema}.RAND()")[0][0])
    assert 0.0 <= value < 1.0


def test_every_direct_call_is_a_function_the_translator_emits():
    assert {c.split("(")[0] for c, _ in CALLS} - emitted() == set()


# --------------------------------------------------------------------------- R084: every SqlProc, in both schemas
#
# 4.6.0 R084: the SQL procedures IVG's classes project come from one forwarding class the deploy compiles into the
# IVG Schema's package (`Graph.KG.SqlProcs`, `IVGALT.SqlProcs`). Each answers a value worked by hand from its
# ObjectScript, in a `Graph_KG` namespace and an `IVGALT` one, both made anew here so the graph and temporal answers
# count only this module's rows.

PROC_NAMESPACES = [
    (os.environ.get("IVG_NS_IVG262FPG", "IVG262FPG"), "Graph_KG"),
    (os.environ.get("IVG_NS_IVG262FPA", "IVG262FPA"), "IVGALT"),
]

#: (call, hand-worked answer): the procedures that read nothing but their arguments.
PURE = [
    ("CypherFn_IVGREGEXMATCH('abc', 'a.c')", 1),
    ("CypherFn_IVGJSONARRAYLENGTH('[1,2,3]')", 3),
    ("CypherFn_IVGJSONVALUE('{\"a\":\"x\"}', '$.a')", "x"),
    ("CypherFn_IVGJSONKEYS('{\"a\":1,\"b\":2}')", '["a","b"]'),
    ("CypherFn_IVGLISTREVERSE('[1,2,3]')", "[3,2,1]"),
    ("CypherFn_IVGSTRSPLIT('a;b;c', ';')", '["a","b","c"]'),
    ("CypherFn_IVGLISTHEAD('[7,8,9]')", "7"),
    ("CypherFn_IVGLISTLAST('[7,8,9]')", "9"),
    ("CypherFn_IVGLISTTAIL('[7,8,9]')", "[8,9]"),
    ("CypherFn_IVGLISTGET('[10,20,30]', '-1')", "30"),
    ("CypherFn_IVGMAPGET('{\"a\":5}', 'a')", "5"),
    ("CypherFn_IVGJSONSET('{\"a\":1}', 'b', '2')", '{"a":1,"b":2}'),
    ("CypherFn_IVGJSONREMOVE('{\"a\":1,\"b\":2}', 'a')", '{"b":2}'),
    ("CypherFn_IVGRANGE(10, 0, -5)", "[10,5,0]"),
    ("JSON_KEYS('{\"k\":1}')", '["k"]'),
    ("Percentile_PDISC('[5,1,3]', 0.5)", "3"),  # sorted 1,3,5; index (3-1)*0.5 = 1
    ("Percentile_PCONT('[1,2,3,4]', 0.5)", 2.5),  # position 1.5, halfway from 2 to 3
]


@pytest.fixture(scope="module", params=PROC_NAMESPACES, ids=[s for _, s in PROC_NAMESPACES])
def world(request):
    """A namespace on its own database, set up for one IVG Schema, holding: fp:a -R-> fp:b -R-> fp:c (weights 1, 1)
    beside a direct fp:a -R-> fp:c (weight 5); fp:a -R-> fp:z in graph r084g; two temporal triples (t:1 -PING-> t:2
    at two timestamps, t:2 -PING-> t:3 once); a BM25 index over the fp nodes' names; an IVF index of three
    hand-placed vectors; and one ledger commit."""
    ns, schema = request.param
    ns262.recreate(ns)
    conn = ns262.connect(ns)
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.ledger import Changeset

    e = IRISGraphEngine(conn, embedding_dimension=4, namespace=ns, schema_prefix=schema, native_cypher=False)
    status = e.initialize_schema(rewidth=True)
    assert [w for w in status["warnings"] if "deploy error" in w] == [], status["warnings"]
    for node, name in (("fp:a", "red apple"), ("fp:b", "green pear"), ("fp:c", "red cherry")):
        e.create_node(node, labels=["Fruit"], properties={"name": name})
    e.create_edge("fp:a", "R", "fp:b", weight=1.0)
    e.create_edge("fp:b", "R", "fp:c", weight=1.0)
    e.create_edge("fp:a", "R", "fp:c", weight=5.0)
    e.create_node("fp:a", graph="r084g")
    e.create_node("fp:z", graph="r084g")
    e.create_edge("fp:a", "R", "fp:z", graph="r084g")
    e.create_edge_temporal("t:1", "PING", "t:2", 1700000000)
    e.create_edge_temporal("t:1", "PING", "t:2", 1700000600)
    e.create_edge_temporal("t:2", "PING", "t:3", 1700000000)
    e.bm25_build("r084bm", ["name"])
    ns262.native(conn).classMethodValue(
        "Graph.KG.IVFIndex", "Build", "r084ivf", "2", '"dot"', "[[1,0],[0,1]]",
        '[{"nodeId":"fp:a","cellIdx":0,"vec":[1,0]},{"nodeId":"fp:b","cellIdx":1,"vec":[0,1]},'
        '{"nodeId":"fp:c","cellIdx":0,"vec":[0.6,0.8]}]')
    e.ledger.enable()
    cs = Changeset(actor="r084", actor_type="system")
    cs.create_node("r084:l", labels=["R84"])
    committed = e.ledger.commit(cs)
    conn.commit()
    yield {"conn": conn, "engine": e, "schema": schema, "seq": committed.revision.seq}
    e.ledger.disable()
    conn.close()


def _value(world, call):
    return ns262.rows(world["conn"], f"SELECT {world['schema']}.{call}")[0][0]


@pytest.mark.parametrize("call, expected", PURE, ids=[c.split("(")[0] for c, _ in PURE])
def test_a_pure_procedure_answers_its_hand_worked_value(world, call, expected):
    value = _value(world, call)
    if isinstance(expected, int):
        assert int(value) == expected
    elif isinstance(expected, float):
        assert float(value) == pytest.approx(expected)
    else:
        assert str(value) == expected


def test_newid_is_a_guid_and_rand_a_fraction(world):
    assert re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
                        str(_value(world, "CypherFn_IVGNEWID()")).upper())
    assert 0.0 <= float(_value(world, "CypherFn_IVGRAND()")) < 1.0


@pytest.mark.parametrize("call, text", [
    ("CypherFn_IVGTYPEERROR('r084 boom')", "r084 boom"),
    ("Percentile_PDISC('[1]', 2)", "NumberOutOfRange"),
], ids=["IVGTYPEERROR", "PDISC_out_of_range"])
def test_an_error_raised_behind_the_forwarder_reaches_sql(world, call, text):
    with pytest.raises(Exception, match=text):
        _value(world, call)


def test_match_edges_reads_one_graph_and_all_graphs(world):
    one = json.loads(_value(world, "MatchEdges('fp:a', 'R', 0, 0)"))
    every = json.loads(_value(world, "MatchEdgesAllGraphs('fp:a', 'R', 0)"))
    assert sorted((d["o"], d["g"]) for d in one) == [("fp:b", ""), ("fp:c", "")]
    assert sorted((d["o"], d["g"]) for d in every) == [("fp:b", ""), ("fp:c", ""), ("fp:z", "r084g")]


def test_dijkstra_takes_the_cheaper_chain(world):
    path = json.loads(_value(world, "DijkstraPath('fp:a', 'fp:c', '', 9999, 10, 'out')"))
    assert path["nodes"] == ["fp:a", "fp:b", "fp:c"] and float(path["totalCost"]) == 2.0


def test_bm25_finds_the_only_pear(world):
    hits = json.loads(_value(world, "kg_BM25('r084bm', 'pear', 3)"))
    assert [h["id"] for h in hits] == ["fp:b"]


def test_ivf_ranks_by_dot_product(world):
    hits = json.loads(_value(world, "kg_IVF('r084ivf', '[1,0]', 2, 2)"))
    assert [(h["id"], round(float(h["score"]), 6)) for h in hits] == [("fp:a", 1.0), ("fp:c", 0.6)]


def test_backfill_asserts_each_distinct_temporal_triple(world):
    assert int(_value(world, "backfill_temporal_rows()")) == 2


def test_ledger_records_lists_the_commit(world):
    rows = ns262.rows(world["conn"], f"SELECT * FROM {world['schema']}.ledger_records(?, ?, 1, 100)",
                      [world["seq"], world["seq"]])
    # an ObjectScript "" reaches SQL as NULL: create_node names no attribute
    assert [r[2:6] for r in rows] == [("create_node", "node", "r084:l", None), ("add_label", "node", "r084:l", "R84")]


def test_rebuild_edge_indices_rebuilds_the_four_structural_tables(world):
    assert int(_value(world, "rebuild_edge_indices()")) == 4
    one = json.loads(_value(world, "MatchEdges('fp:a', 'R', 0, 0)"))
    assert sorted(d["o"] for d in one) == ["fp:b", "fp:c"]


def test_every_projected_procedure_comes_from_the_schemas_forwarder(world):
    from iris_vector_graph.schema_name import schema_package

    forwarder = f"{schema_package(world['schema'])}.SqlProcs"
    projected = {str(r[0]) for r in ns262.rows(
        world["conn"], "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ? "
                       "AND CLASSNAME = ?", [world["schema"], forwarder])}
    assert projected == EXPECTED_SQL_NAMES


def test_the_txn_procedures_open_and_close_ivgs_levels(world):
    """4.6.4's Txn_Begin / Txn_End / Txn_Undo (Graph.KG.Txn), each answering $TLEVEL after: Begin opens one level
    (two with pHold at level 0), Undo(n) and End(n) close level n and those above it, never one below. A
    connection of its own, so the world's is left at level 0."""
    ns = [n for n, s in PROC_NAMESPACES if s == world["schema"]][0]
    conn = ns262.connect(ns)
    try:
        def level(call):
            return int(ns262.rows(conn, f"SELECT {world['schema']}.{call}")[0][0])

        assert [level("Txn_Begin()"), level("Txn_Begin()"), level("Txn_Undo(2)"), level("Txn_End(1)")] == [1, 2, 1, 0]
        assert [level("Txn_Begin(1)"), level("Txn_End(1)"), level("Txn_End(0)")] == [2, 0, 0]
    finally:
        conn.close()


def test_this_module_answers_every_projected_procedure():
    covered = {c.split("(")[0] for c, _ in PURE} | {
        "CypherFn_IVGNEWID", "CypherFn_IVGRAND", "CypherFn_IVGTYPEERROR", "MatchEdges", "MatchEdgesAllGraphs",
        "DijkstraPath", "kg_BM25", "kg_IVF", "backfill_temporal_rows", "ledger_records", "rebuild_edge_indices",
        "Txn_Begin", "Txn_End", "Txn_Undo"}  # 4.6.4: test_the_txn_procedures_open_and_close_ivgs_levels
    assert covered == EXPECTED_SQL_NAMES
