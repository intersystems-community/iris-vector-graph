"""4.6.0 R235, live: upgrading a 4.5.1 namespace compiles Graph.KG.SqlProcs, and every procedure it projects answers.

R084 moved every `[ SqlProc ]` out of the code classes into `Graph.KG.SqlProcs`. In a namespace 4.5.1 set up, its own
`Graph.KG.Traversal`, `TemporalIndex`, `Ledger` and the rest still project those names (`DijkstraPath`,
`backfill_temporal_rows`, ...), so SqlProcs compiled before them fails `ERROR #5083: Stored procedure name is not
unique`. The test session's deploy loaded classes in name order and hit exactly that in the FHIR fixture (fhirfix);
this proves the product's own upgrade, `initialize_schema()` (`deploy_packaged_classes`: the forwarder in a last
batch), does not.

The namespace (IVG262UPG34, overridable by IVG262_R235_NS) is built by v4.5.1's own code and seed
(`scripts/fixtures/build_release_namespace.py`), upgraded by this checkout's `initialize_schema()`, checked, and dropped.
Each procedure is called through SQL with an answer worked out by hand from the seed (n1 Ada -knows-> n2 Bes -knows->
n3 Cyd in the default graph, n5 -knows-> n6 in `acme`, three temporal `calls` edges, n1's and n2's 8-wide vectors) or
from the function's definition; the set of names called is the set SqlProcs projects, so a new one fails here until
it has an answer.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from tests.e2e import ns262, scratch_vars

NS = os.environ.get("IVG262_R235_NS", "")

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG262_R235_NS")]

N1_VEC = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
GUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$", re.I)


def _projecting(conn, sql_name: str) -> list:
    return sorted(r[0] for r in ns262.rows(
        conn, "SELECT parent FROM %Dictionary.CompiledMethod WHERE SqlProc = 1 AND SqlName = ?", [sql_name]))


@pytest.fixture(scope="module")
def upg():
    if not (NS.startswith(("IVG262", "IVG460FX")) and NS.upper() == NS):
        pytest.fail(f"refusing {NS}: an IVG262* or IVG460FX* scratch namespace only")
    from build_release_namespace import build

    import make_scratch_namespace

    build("v4.5.1", NS)
    conn = ns262.connect(NS)
    before = {"DijkstraPath": _projecting(conn, "DijkstraPath"),
              "backfill_temporal_rows": _projecting(conn, "backfill_temporal_rows"),
              "sqlprocs": ns262.rows(conn, "SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE ID = ?",
                                     ["Graph.KG.SqlProcs"])[0][0]}
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS)
    status = engine.initialize_schema()
    deploy = GraphSchema.last_deploy
    try:
        yield {"conn": conn, "engine": engine, "before": before, "status": status, "deploy": deploy}
    finally:
        conn.close()
        make_scratch_namespace.drop(NS)


def test_the_fixture_is_a_451_namespace_whose_code_classes_project_the_procedures(upg):
    assert upg["before"]["sqlprocs"] == 0
    assert upg["before"]["DijkstraPath"] == ["Graph.KG.Traversal"]
    assert upg["before"]["backfill_temporal_rows"] == ["Graph.KG.TemporalIndex"]


def test_the_upgrade_compiles_sql_procs_without_a_collision(upg):
    deploy = upg["deploy"]
    assert deploy is not None and deploy.deployed and deploy.errors == [], deploy.errors
    assert not [w for w in upg["status"]["warnings"] if "ObjectScript deploy error" in w], upg["status"]["warnings"]
    conn = upg["conn"]
    assert ns262.rows(conn, "SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE ID = ?",
                      ["Graph.KG.SqlProcs"])[0][0] == 1
    assert _projecting(conn, "DijkstraPath") == ["Graph.KG.SqlProcs"]


def _one(conn, sql, params=None):
    return ns262.rows(conn, sql, params or [])[0][0]


def _ids(text) -> set:
    """Every string value in a JSON answer (the ids a procedure names)."""
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(json.loads(text))
    return found


def _calls(engine, conn) -> dict:
    """SQL name -> a check that calls it and asserts its hand-worked answer."""
    s = "Graph_KG."

    def bm25():
        engine.bm25_build("r235t", ["name"])
        hits = json.loads(_one(conn, f"SELECT {s}kg_BM25(?, ?, ?)", ["r235t", "Ada", 5]))
        assert [h["id"] for h in hits] == ["n1"], hits

    def ivf():
        engine.ivf_build("r235v", nlist=1)
        hits = json.loads(_one(conn, f"SELECT {s}kg_IVF(?, ?, ?, ?)", ["r235v", json.dumps(N1_VEC), 2, 1]))
        assert [h["id"] for h in hits][0] == "n1" and {h["id"] for h in hits} == {"n1", "n2"}, hits

    def ledger():
        # as the ledger client reads it (ledger/replay.py); the ledger was never enabled: no history
        assert ns262.rows(conn, f"SELECT * FROM {s}ledger_records(?, ?, ?, ?)", [1, 100, 1, 5000]) == []

    def type_error():
        with pytest.raises(Exception, match="r235boom"):
            _one(conn, f"SELECT {s}CypherFn_IVGTYPEERROR(?)", ["r235boom"])

    def newid():
        a, b = _one(conn, f"SELECT {s}CypherFn_IVGNEWID()"), _one(conn, f"SELECT {s}CypherFn_IVGNEWID()")
        assert GUID.match(a) and GUID.match(b) and a != b, (a, b)

    def rand():
        v = float(_one(conn, f"SELECT {s}CypherFn_IVGRAND()"))
        assert 0.0 <= v < 1.0

    def eq(sql, params, want, cast=str):
        def check():
            got = _one(conn, f"SELECT {s}{sql}", params)
            assert cast(got) == want, (sql, got)
        return check

    def js(sql, params, want):
        def check():
            assert json.loads(_one(conn, f"SELECT {s}{sql}", params)) == want, sql
        return check

    def edges(sql, params, want):
        def check():
            assert want <= _ids(_one(conn, f"SELECT {s}{sql}", params)), sql
        return check

    def dijkstra():
        got = json.loads(_one(conn, f"SELECT {s}DijkstraPath(?, ?)", ["n1", "n3"]))
        text = json.dumps(got)
        assert text.index('"n1"') < text.index('"n2"') < text.index('"n3"'), got

    return {
        "CypherFn_IVGREGEXMATCH": eq("CypherFn_IVGREGEXMATCH(?, ?)", ["abc", "a.c"], 1, int),
        "CypherFn_IVGJSONARRAYLENGTH": eq("CypherFn_IVGJSONARRAYLENGTH(?)", ["[1,2,3]"], 3, int),
        "CypherFn_IVGJSONVALUE": eq("CypherFn_IVGJSONVALUE(?, ?)", ['{"a":5}', "$.a"], "5"),
        "CypherFn_IVGJSONKEYS": js("CypherFn_IVGJSONKEYS(?)", ['{"a":1,"b":2}'], ["a", "b"]),
        "CypherFn_IVGLISTREVERSE": js("CypherFn_IVGLISTREVERSE(?)", ["[1,2,3]"], [3, 2, 1]),
        "CypherFn_IVGSTRSPLIT": js("CypherFn_IVGSTRSPLIT(?, ?)", ["a,b,c", ","], ["a", "b", "c"]),
        "CypherFn_IVGNEWID": newid,
        "CypherFn_IVGRAND": rand,
        "CypherFn_IVGLISTHEAD": eq("CypherFn_IVGLISTHEAD(?)", ["[1,2,3]"], "1"),
        "CypherFn_IVGLISTLAST": eq("CypherFn_IVGLISTLAST(?)", ["[1,2,3]"], "3"),
        "CypherFn_IVGLISTTAIL": js("CypherFn_IVGLISTTAIL(?)", ["[1,2,3]"], [2, 3]),
        "CypherFn_IVGLISTGET": eq("CypherFn_IVGLISTGET(?, ?)", ["[1,2,3]", "1"], "2"),
        "CypherFn_IVGTYPEERROR": type_error,
        "CypherFn_IVGMAPGET": eq("CypherFn_IVGMAPGET(?, ?)", ['{"a":7}', "a"], "7"),
        "CypherFn_IVGJSONSET": js("CypherFn_IVGJSONSET(?, ?, ?, ?)", ['{"a":1}', "b", "2", "number"],
                                  {"a": 1, "b": 2}),
        "CypherFn_IVGJSONREMOVE": js("CypherFn_IVGJSONREMOVE(?, ?)", ['{"a":1,"b":2}', "a"], {"b": 2}),
        "CypherFn_IVGRANGE": js("CypherFn_IVGRANGE(?, ?, ?)", [1, 3, 1], [1, 2, 3]),
        "JSON_KEYS": js("JSON_KEYS(?)", ['{"a":1,"b":2}'], ["a", "b"]),
        # percentileDisc of 1..4 at 0.5: index floor(3 * 0.5) = 1, the value 2; percentileCont: 2 + 0.5 * (3 - 2)
        "Percentile_PDISC": eq("Percentile_PDISC(?, ?)", ["[4,1,3,2]", 0.5], "2"),
        "Percentile_PCONT": eq("Percentile_PCONT(?, ?)", ["[4,1,3,2]", 0.5], 2.5, float),
        "kg_BM25": bm25,
        "kg_IVF": ivf,
        # as the translator calls it: the graph argument left at its default, 0, the default graph's key
        "MatchEdges": edges("MatchEdges(?, ?, ?)", ["n1", "knows", 0], {"n2"}),
        "MatchEdgesAllGraphs": edges("MatchEdgesAllGraphs(?, ?, ?)", ["n5", "knows", 0], {"n6"}),
        "DijkstraPath": dijkstra,
        # the seed's three temporal edges are three distinct triples, each asserted
        "backfill_temporal_rows": eq("backfill_temporal_rows()", [], 3, int),
        # nodes, rdf_labels, rdf_props, rdf_edges
        "rebuild_edge_indices": eq("rebuild_edge_indices(?)", ["Graph_KG"], 4, int),
        "ledger_records": ledger,
    }


def test_every_procedure_sql_procs_projects_answers(upg):
    conn, engine = upg["conn"], upg["engine"]
    projected = {r[0] for r in ns262.rows(
        conn, "SELECT SqlName FROM %Dictionary.CompiledMethod WHERE parent = 'Graph.KG.SqlProcs' AND SqlProc = 1")}
    projected |= {r[0] for r in ns262.rows(
        conn, "SELECT SqlName FROM %Dictionary.CompiledQuery WHERE parent = 'Graph.KG.SqlProcs' AND SqlProc = 1")}
    calls = _calls(engine, conn)
    assert projected == set(calls)
    failed = {}
    for name in sorted(calls):
        try:
            calls[name]()
        except Exception as exc:  # collected, so one run names every procedure that does not answer
            failed[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
    assert failed == {}
