"""4.6.0 R202: ADMIN_GUIDE §3's reading role, as the guide writes it, end to end: right answers, refused writes, and
no warning (walkthrough 1 W5, P5).

The role is built from the guide's own table row ("| reading |"): its resources, with `<database resource>` looked up
for the namespace's database as the guide tells an operator to (the compose image's `USER` has `%DB_USER`, a database
made without one `%DB_%DEFAULT`), and its SQL grants. The walkthrough's role lacked `%Native_GlobalAccess:U`: every
engine warned that the schema-record check was skipped, and `status()` listed the refusals.

The answers were worked out by hand for the default graph a -> b -> c -> d (KNOWS, each node labelled P): one hop from
a reaches b; one or two reach b and c; the shortest a..d path has 3 edges; PageRank on a chain ranks the sink first
(d > c > b > a); b and c each lie on two shortest paths (a..c, a..d and a..d, b..d).

    IVG_NAMESPACE=IVG460FXO pytest tests/e2e/test_460_r202_reading_role_e2e.py
"""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R202"
GUIDE = Path(__file__).resolve().parents[2] / "docs" / "ADMIN_GUIDE.md"


def guide_row(label: str) -> tuple:
    """(resources, SQL statements) of the §3 table row `label`, as written."""
    for line in GUIDE.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] == label:
            resources = re.findall(r"`([^`]+:[RWU]+)`", cells[1])
            statements = re.findall(r"`(GRANT [^`]+)`", cells[2])
            return resources, statements
    raise AssertionError(f"ADMIN_GUIDE has no privileges row {label!r}")


def role_resources() -> str:
    resources, _ = guide_row("reading")
    return ",".join(r.replace("<database resource>", rr.database_resource()) for r in resources)


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
    """PageRank caches its answer in ^IVG.AlgoCache, which the database at R refuses: one WARNING per call before."""
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        for _ in range(2):
            assert [node for node, _ in reader.kg_PAGERANK()] == ["d", "c", "b", "a"]
        reader.khop("a", hops=2)
        reader.betweenness_centrality()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


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
