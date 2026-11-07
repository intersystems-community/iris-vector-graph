"""4.6.0 R202: ADMIN_GUIDE §3's reading role, as the guide writes it, end to end: right answers, refused writes, and
no warning (walkthrough 1 W5, P5).

The role is built from the guide's own table row ("| reading |"): its resources, with `<database resource>` looked up
for the namespace's database as the guide tells an operator to (the compose image's `USER` has `%DB_USER`, a database
made without one `%DB_%DEFAULT`), and its SQL grants. The walkthrough's role lacked `%Native_GlobalAccess:U`: every
engine warned that the schema-record check was skipped, and `status()` listed the refusals.

The answers were worked out by hand for the default graph a -> b -> c -> d (KNOWS, each node labelled P): one hop from
a reaches b; one or two reach b and c; the shortest a..d path has 3 edges; PageRank on a chain ranks the sink first
(d > c > b > a); b and c each lie on two shortest paths (a..c, a..d and a..d, b..d).

4.6.0 R050 (expert walkthrough W1): on a namespace whose routines are in a database of their own, the row as it read
then (globals database at `R`, nothing on the routines database) got `<PROTECT>` from PageRank: every IVG class method
the engine calls over the Native API runs code from the routines database, and IRIS's Native API server refuses it
without `R` on that database's resource. The row now names `<routines database resource>:R`; the split-database tests
build the role from it on `IVG_SPLIT_NAMESPACE` (made with `make_scratch_namespace.py --split` if absent, never
dropped here), and show which reads the old row lost.

    IVG_NAMESPACE=IVG460FXO IVG_SPLIT_NAMESPACE=IVG460FXSPLIT pytest tests/e2e/test_460_r202_reading_role_e2e.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import warnings
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R202"
REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "ADMIN_GUIDE.md"
#: A namespace with its globals in `<NS>D` and its routines in `<NS>R`, each database on a resource of its own (R050).
SPLIT_NS = os.environ.get("IVG_SPLIT_NAMESPACE", "")
if not SPLIT_NS:  # the E2E gate names one per run (tests/e2e/gate.py GROUPS); never a fixed default
    pytest.skip("set IVG_SPLIT_NAMESPACE to a scratch namespace of this run", allow_module_level=True)


def guide_row(label: str) -> tuple:
    """(resources, SQL statements) of the §3 table row `label`, as written."""
    for line in GUIDE.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] == label:
            resources = re.findall(r"`([^`]+:[RWU]+)`", cells[1])
            statements = re.findall(r"`(GRANT [^`]+)`", cells[2])
            return resources, statements
    raise AssertionError(f"ADMIN_GUIDE has no privileges row {label!r}")


def role_resources(namespace: str = "", label: str = "reading") -> str:
    """The row's resources for `namespace`, each placeholder looked up as the guide says."""
    resources, _ = guide_row(label)
    looked_up = {"<database resource>": rr.database_resource(namespace),
                 "<routines database resource>": rr.routines_resource(namespace)}
    out = []
    for r in resources:
        name, _, privilege = r.partition(":")
        out.append(f"{looked_up.get(name, name)}:{privilege}")
    return rr.resources(*out)


def role_privileges() -> tuple:
    _, statements = guide_row("reading")
    privileges = []
    for statement in statements:
        found = re.match(r"GRANT (\w+) ON SCHEMA Graph_KG TO role$", statement)
        assert found, statement
        privileges.append(found.group(1))
    return tuple(privileges)


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()  # ^NKG, which betweenness's ObjectScript tier reads; a fresh namespace has none (R050)
    user = rr.make_reader(TAG, role_resources(), sql_privileges=role_privileges())
    rconn = rr.connect(user, rr.PASSWORD)
    try:
        yield sysconn, rconn
    finally:
        rconn.close()
        rr.drop_reader(TAG)
        owner.erase_graph(None)
        sysconn.close()


@pytest.fixture(scope="module")
def reader(setup):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(setup[1], embedding_dimension=rr.DIM)


def test_the_guide_lists_global_access_for_reading():
    resources, statements = guide_row("reading")
    assert "%Native_GlobalAccess:U" in resources
    assert "<database resource>:R" in resources
    assert role_privileges() == ("SELECT", "EXECUTE")


@pytest.mark.parametrize("label", ["reading", "reading, fully", "writing"])
def test_every_row_reads_the_routines_database(label):
    """R050: every row runs IVG's classes, so every row reads the routines database; none writes it."""
    assert "<routines database resource>:R" in guide_row(label)[0]


def test_an_engine_built_by_the_reader_warns_nothing(setup, caplog):
    from iris_vector_graph import IRISGraphEngine

    with warnings.catch_warnings(record=True) as raised, caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        warnings.simplefilter("always")
        engine = IRISGraphEngine(setup[1], embedding_dimension=rr.DIM)
        assert engine.execute_cypher("MATCH (n:P) RETURN count(n)").rows == [[4]]
    assert [str(w.message) for w in raised] == []
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("query, rows", [
    ("MATCH (a:P {node_id: 'a'})-[:KNOWS]->(b) RETURN b.node_id", [["b"]]),
    ("MATCH (a {node_id: 'a'})-[:KNOWS*1..2]->(x) RETURN x.node_id ORDER BY x.node_id", [["b"], ["c"]]),
    ("MATCH p = shortestPath((a {node_id: 'a'})-[*..5]->(d {node_id: 'd'})) RETURN length(p)", [[3]]),
    ("MATCH (n:P) RETURN n.name ORDER BY n.name", [["A"], ["B"], ["C"], ["D"]]),
])
def test_cypher_reads_answer_right(reader, query, rows):
    assert reader.execute_cypher(query).rows == rows


def test_khop_answers_the_neighbourhood(reader):
    assert reader.khop("a", hops=2)["nodes"] == ["a", "b", "c"]


def test_pagerank_ranks_the_chain_from_its_sink(reader):
    assert [node for node, _ in reader.kg_PAGERANK()] == ["d", "c", "b", "a"]


def test_reads_that_cannot_cache_warn_nothing(reader, caplog):
    """PageRank caches its answer in ^IVG.AlgoCache, which the database at R refuses: one WARNING per call before.
    Betweenness is not one of these: ADMIN_GUIDE §3 says its ObjectScript tier cannot write its work area at R and a
    WARNING says so (next test). R050: betweenness was in this list, and the test failed on every fresh namespace."""
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        for _ in range(2):
            assert [node for node, _ in reader.kg_PAGERANK()] == ["d", "c", "b", "a"]
        reader.khop("a", hops=2)
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_betweenness_at_r_says_the_python_walk_answered(reader, caplog):
    """ADMIN_GUIDE §3: "At R its ObjectScript tier cannot write its work area, so the slower Python walk answers, and a
    WARNING says so"; the answer is the same (next test)."""
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        reader.betweenness_centrality()
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(said) == 1, said
    assert "<PROTECT>" in said[0] and "answering from the Python walk" in said[0], said


def test_betweenness_counts_the_paths_through_b_and_c(reader):
    scores = {row["id"]: row["score"] for row in reader.betweenness_centrality()}
    assert scores["b"] == 2.0 and scores["c"] == 2.0
    assert scores.get("a", 0.0) == 0.0 and scores.get("d", 0.0) == 0.0


def test_every_write_is_refused_and_nothing_changes(setup, reader):
    from iris_vector_graph import GraphWriteError

    sysconn, rconn = setup
    before = rr.state(sysconn)
    with pytest.raises(GraphWriteError):
        reader.create_node("z", labels=["P"])
    with pytest.raises(Exception, match="-99"):
        reader.execute_cypher("CREATE (:P {name: 'Q'})")
    with pytest.raises(Exception, match="-99"):
        reader.execute_cypher("MATCH (a {node_id: 'a'}) SET a.name = 'Z'")
    cur = rconn.cursor()
    with pytest.raises(Exception, match="-99"):
        cur.execute(f"INSERT INTO {rr.SCHEMA}.nodes (node_id, graph_id) VALUES ('q', '')")
    with pytest.raises(Exception, match="-99"):
        cur.execute(f"DELETE FROM {rr.SCHEMA}.rdf_edges WHERE s = 'a'")
    assert rr.state(sysconn) == before
    cur = sysconn.cursor()
    cur.execute(f"SELECT val FROM {rr.SCHEMA}.rdf_props WHERE s = 'a' AND \"key\" = 'name'")
    assert [tuple(r) for r in cur.fetchall()] == [("A",)]


def test_status_reports_no_privilege_refusal(reader):
    """The probes this role can answer; what the class dictionary alone holds (the vector tables' ANN indexes) is the
    one thing status() says it could not read (ADMIN_GUIDE §3)."""
    errors = reader.status().errors
    for text in errors:
        assert "%Native_GlobalAccess" not in text, errors
        assert not text.startswith(("class check failed", "schema record probe failed")), errors
    unexplained = [e for e in errors if "Privilege violation" in e and not e.startswith("class lookup failed")]
    assert unexplained == [], errors


# --------------------------------------------------------------------------- R050: globals and routines apart


def _msn():
    sys.path.insert(0, str(REPO / "scripts" / "dev"))
    import make_scratch_namespace

    return make_scratch_namespace


@pytest.fixture(scope="module")
def split():
    """SPLIT_NS set up by `_SYSTEM` (the namespace made first when absent), holding a -> b -> c -> d with `^NKG`
    built, and two readers: the guide's row, and the row as it read before R050 (no routines database)."""
    from iris_vector_graph import IRISGraphEngine

    _msn().make(SPLIT_NS, split=True)
    sysconn = rr.connect(namespace=SPLIT_NS)
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    owner.initialize_schema()
    rr.seed(owner)
    owner.sync()
    guide_user = rr.make_reader("R050S", role_resources(SPLIT_NS), sql_privileges=role_privileges(),
                                namespace=SPLIT_NS)
    old_row = rr.resources(f"{rr.database_resource(SPLIT_NS)}:R", "%Native_ClassExecution:U",
                           "%Native_GlobalAccess:U")
    old_user = rr.make_reader("R050O", old_row, sql_privileges=role_privileges(), namespace=SPLIT_NS)
    conns = {"guide": rr.connect(guide_user, rr.PASSWORD, SPLIT_NS), "old": rr.connect(old_user, rr.PASSWORD, SPLIT_NS)}
    try:
        yield sysconn, {k: IRISGraphEngine(c, embedding_dimension=rr.DIM) for k, c in conns.items()}, conns
    finally:
        for c in conns.values():
            c.close()
        rr.drop_reader("R050S", namespace=SPLIT_NS)
        rr.drop_reader("R050O", namespace=SPLIT_NS)
        sysconn.close()


def test_the_split_namespace_has_two_database_resources(split):
    assert rr.database_resource(SPLIT_NS) == f"%DB_{SPLIT_NS}D"
    assert rr.routines_resource(SPLIT_NS) == f"%DB_{SPLIT_NS}R"


def test_on_a_split_namespace_the_guides_role_answers_pagerank_and_the_reads(split):
    _, readers, _ = split
    reader = readers["guide"]
    assert [node for node, _ in reader.kg_PAGERANK()] == ["d", "c", "b", "a"]
    assert reader.khop("a", hops=2)["nodes"] == ["a", "b", "c"]
    assert reader.execute_cypher(
        "MATCH p = shortestPath((a {node_id: 'a'})-[*..5]->(d {node_id: 'd'})) RETURN length(p)").rows == [[3]]
    assert reader.execute_cypher("MATCH (a:P {node_id: 'a'})-[:KNOWS]->(b) RETURN b.node_id").rows == [["b"]]
    scores = {row["id"]: row["score"] for row in reader.betweenness_centrality()}
    assert scores["b"] == 2.0 and scores["c"] == 2.0


def test_on_a_split_namespace_the_guides_role_writes_nothing(split):
    from iris_vector_graph import GraphWriteError

    sysconn, readers, conns = split
    reader = readers["guide"]
    before = rr.state(sysconn)
    with pytest.raises(GraphWriteError):
        reader.create_node("z", labels=["P"])
    with pytest.raises(Exception, match="-99"):
        reader.execute_cypher("CREATE (:P {name: 'Q'})")
    with pytest.raises(Exception, match="-99"):
        reader.execute_cypher("MATCH (a {node_id: 'a'}) SET a.name = 'Z'")
    cur = conns["guide"].cursor()
    with pytest.raises(Exception, match="-99"):
        cur.execute(f"INSERT INTO {rr.SCHEMA}.nodes (node_id, graph_id) VALUES ('q', '')")
    with pytest.raises(RuntimeError, match="PROTECT"):
        reader.sync()
    assert rr.state(sysconn) == before


def test_without_the_routines_database_the_class_methods_are_refused_and_sql_still_answers(split):
    """What the row lacked before R050, measured: IVG's class methods over the Native API raise `<PROTECT>` naming the
    routines database (PageRank); SQL, IVG's SQL functions included, answers."""
    _, readers, conns = split
    reader = readers["old"]
    routines_dir = f"/usr/irissys/mgr/{SPLIT_NS.lower()}r/"
    with pytest.raises(RuntimeError, match=re.escape(routines_dir)) as raised:
        reader.kg_PAGERANK()
    assert "<PROTECT>" in str(raised.value)
    cur = conns["old"].cursor()
    cur.execute(f"SELECT {rr.SCHEMA}.kg_PPR('[\"a\"]', 0.85, 20, 0, 1.0, '')")
    assert '"id":"a"' in cur.fetchone()[0]


SHORTEST = "MATCH p = shortestPath((a {node_id: 'a'})-[*]->(d {node_id: 'd'})) RETURN [n IN nodes(p) | n.node_id]"


def test_without_the_routines_database_shortest_path_sets_the_error_instead_of_answering_no_rows(split):
    """Round-3 walkthrough: shortestPath, refused `<PROTECT>` on Graph.KG.Traversal, answered no rows with
    `result.error` None (a WARNING was the only sign). It now fails as the guide says the refused calls do: the error
    is the result's, `raise_on_error=True` raises it, and the guide's role answers the path."""
    _, readers, _ = split
    refused = readers["old"].execute_cypher(SHORTEST)
    assert refused.rows == [] and refused.error and "<PROTECT>" in str(refused.error), (refused.rows, refused.error)
    with pytest.raises(RuntimeError, match="PROTECT"):
        readers["old"].execute_cypher(SHORTEST, raise_on_error=True)
    assert readers["guide"].execute_cypher(SHORTEST, raise_on_error=True).rows == [[["a", "b", "c", "d"]]]


def test_without_the_routines_database_khop_raises_instead_of_answering_no_nodes(split):
    """R050, the lead's follow-up: the class dictionary is in the routines database too, and refused it answered that
    no class exists, so the engine took IVG for not deployed and khop() answered []. A refusal now raises, naming the
    database and the guide; status() reports it instead."""
    from iris_vector_graph import ClassesUnreadable, IRISGraphEngine

    _, _, conns = split
    reader = IRISGraphEngine(conns["old"], embedding_dimension=rr.DIM)
    routines_dir = f"/usr/irissys/mgr/{SPLIT_NS.lower()}r/"
    with pytest.raises(ClassesUnreadable, match=re.escape(routines_dir)) as raised:
        reader.khop("a", hops=2)
    assert "ADMIN_GUIDE §3" in str(raised.value)
    assert any(routines_dir in e and "ADMIN_GUIDE §3" in e for e in reader.status().errors)


# ------------------------------------------- R050 round 2 (expert walkthrough W9, W10): which reads the old row loses


def _cypher(engine, query):
    result = engine.execute_cypher(query)
    if result.error:
        raise RuntimeError(result.error)
    return result.rows


#: Measured on 2026.3.0AI (IVG460FX61S, 2026-11-06), and on Community 2026.1 by the expert walkthrough: the calls that
#: run an IVG class method over the Native API raise <PROTECT> naming the routines database; SQL, IVG's SQL functions,
#: the CALL procedures (which run them) and the Python walks answer.
REFUSED = {
    "kg_PAGERANK": lambda e: e.kg_PAGERANK(),
    "shortestPath": lambda e: _cypher(
        e, "MATCH p = shortestPath((a {node_id: 'a'})-[*..5]->(d {node_id: 'd'})) RETURN length(p)"),
    "degree_centrality": lambda e: e.degree_centrality(),
    "ppr": lambda e: e.ppr("a"),
    "sync": lambda e: e.sync(),
}
ANSWERED = {
    "betweenness_centrality": (lambda e: {r["id"]: r["score"] for r in e.betweenness_centrality()},
                               {"b": 2.0, "c": 2.0}),
    "closeness_centrality": (lambda e: e.closeness_centrality()[0]["id"], "a"),
    "CALL ivg.closeness": (lambda e: _cypher(e, "CALL ivg.closeness({topK: 1}) YIELD node, score RETURN node")[0][0],
                           "a"),
    "CALL ivg.ppr": (lambda e: _cypher(e, "CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score RETURN node")[0][0], "a"),
    "one hop": (lambda e: _cypher(e, "MATCH (a {node_id: 'a'})-[:KNOWS]->(b) RETURN b.node_id"), [["b"]]),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_without_the_routines_database_these_raise_naming_it(split, name):
    _, readers, _ = split
    with pytest.raises(RuntimeError, match=re.escape(f"/usr/irissys/mgr/{SPLIT_NS.lower()}r/")):
        REFUSED[name](readers["old"])


@pytest.mark.parametrize("name", sorted(ANSWERED))
def test_without_the_routines_database_these_answer(split, name):
    _, readers, _ = split
    call, expected = ANSWERED[name]
    assert call(readers["old"]) == expected


def test_without_the_routines_database_the_report_says_what_it_could_not_tell(split):
    _, readers, _ = split
    report = readers["old"].status().report()
    assert "Sync state:      unknown" in report and "in sync" not in report
    assert "<SYNTAX>" not in report
