"""4.6.0 R050 round 2 (expert walkthrough W2, W4), live: what a role granted SELECT on the IVG Schema and no EXECUTE can
read before and after setup, how a refused read is reported, and whom setup grants the Cypher read path to.

The role holds the reading row's resources (ADMIN_GUIDE §3) and ``GRANT SELECT ON SCHEMA Graph_KG`` only; its user
holds the role and nothing of its own. The graph is reading_role460's a -> b -> c -> d (KNOWS, each node P), synced.
By hand: one hop from a reaches b; one to three hops reach b, c, d; an optional hop from d finds nothing (one row, d and
null); closeness (harmonic, outbound) ranks a first: 1 + 1/2 + 1/3.

W2: the walkthrough (Community 2026.1) saw the SQL-path reads answer ``[]`` with ``result.error`` set before setup, real
rows after it, and ``CALL ivg.closeness`` keep answering ``[]`` after it: the ``kg_*`` algorithm functions are not on
the read path (R278), so the role needs EXECUTE on them. W4: setup granted the read path to the role and again to each
member user, since SQLAllPrivileges lists the role's SELECT under its members too.

    IVG_NAMESPACE=IVG460FX61A pytest tests/e2e/test_460_r050_select_only_role_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "IVG460FX61"
ONE_HOP = "MATCH (a {node_id: 'a'})-[:KNOWS]->(b) RETURN b.node_id"
VARLEN = "MATCH (a {node_id: 'a'})-[:KNOWS*1..3]->(x) RETURN x.node_id ORDER BY x.node_id"
OPTIONAL = "MATCH (d {node_id: 'd'}) OPTIONAL MATCH (d)-[:KNOWS]->(x) RETURN d.node_id, x.node_id"
CLOSENESS = "CALL ivg.closeness({topK: 1}) YIELD node, score RETURN node"


def direct_grants(grantee: str) -> int:
    """Rows of Security.SQLPrivileges granted to `grantee` itself in the namespace."""
    out = rr.session([
        'set rs=##class(%SQL.Statement).%ExecDirect(,"SELECT COUNT(*) FROM Security.SQLPrivileges WHERE Grantee = ? '
        f'AND Namespace = ?","{grantee}","{rr.NS}")',
        'do rs.%Next() write "<<N:",rs.%GetData(1),":N>>",!'])
    return int(rr.marked(out, "N"))


@pytest.fixture(scope="module")
def world():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()
    user = rr.make_reader("SEL", rr.reading_resources("R"), sql_privileges=("SELECT",), prefix=PREFIX)
    role, _ = rr.names("SEL", PREFIX)
    conn = rr.connect(user, rr.PASSWORD)
    reader = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    before = {q: reader.execute_cypher(q) for q in (ONE_HOP, VARLEN, OPTIONAL, CLOSENESS)}
    setup = owner.initialize_schema()
    after = {q: reader.execute_cypher(q) for q in (ONE_HOP, VARLEN, OPTIONAL, CLOSENESS)}
    try:
        yield {"owner": owner, "reader": reader, "role": role, "user": user, "before": before, "after": after,
               "setup": setup}
    finally:
        conn.close()
        rr.drop_reader("SEL", PREFIX)
        owner.erase_graph(None)
        sysconn.close()


@pytest.mark.parametrize("query", [VARLEN, OPTIONAL])
def test_before_setup_a_sql_path_read_answers_no_rows_and_sets_error(world, query):
    result = world["before"][query]
    assert result.rows == [] and result.error and "-99" in result.error, (result.rows, result.error)


def test_before_setup_a_read_the_native_executor_takes_answers(world):
    """It reads ^KG under the database resource, not through SQL (ADMIN_GUIDE §3's boundary): measured on 2026.3.0AI
    for this unlabelled one-hop read. Which reads it takes depends on the query's shape."""
    result = world["before"][ONE_HOP]
    assert result.error is None and result.rows == [["b"]], (result.rows, result.error)


@pytest.mark.parametrize("query, rows", [
    (ONE_HOP, [["b"]]),
    (VARLEN, [["b"], ["c"], ["d"]]),
    (OPTIONAL, [["d", None]]),
])
def test_after_setup_the_cypher_reads_answer(world, query, rows):
    result = world["after"][query]
    assert result.error is None and result.rows == rows, (result.rows, result.error)


def test_after_setup_a_procedure_is_still_refused_and_says_so_in_error(world):
    result = world["after"][CLOSENESS]
    assert result.rows == [] and "-99" in (result.error or ""), (result.rows, result.error)


def test_raise_on_error_raises_the_refusal(world):
    with pytest.raises(RuntimeError, match="-99"):
        world["reader"].execute_cypher(CLOSENESS, raise_on_error=True)


def test_execute_on_the_schema_lets_the_procedure_answer(world):
    conn = rr.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"GRANT EXECUTE ON SCHEMA {rr.SCHEMA} TO {world['role']}")
        conn.commit()
    finally:
        conn.close()
    assert [row[0] for row in world["reader"].execute_cypher(CLOSENESS).rows] == ["a"]


def test_setup_grants_the_role_and_not_its_user(world):
    subjects = {g["subject"] for g in world["setup"]["read_path_grants"]}
    assert world["role"] in subjects and world["user"] not in subjects, subjects
    assert direct_grants(world["user"]) == 0
    assert direct_grants(world["role"]) > 1  # the schema SELECT and the read-path functions
