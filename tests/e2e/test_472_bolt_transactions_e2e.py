"""4.7.2, live: Bolt BEGIN / COMMIT / ROLLBACK are a transaction, read with the Neo4j driver against IRIS.

Until 4.7.2 each answered SUCCESS and did nothing: a `CREATE` run after BEGIN was committed as it ran, so a ROLLBACK, or
a managed `execute_write` whose function raised, kept the node. Each case runs a TCP Bolt server whose engine factory
makes a connection per session (as `_make_engine` does in production), drives it with the Neo4j driver, and reads the
database from a second connection: what is there, not what was sent.

    IVG_R472_NS=IVG470W3 pytest tests/e2e/test_472_bolt_transactions_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R472_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R472_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def observer():
    conn = _connect()
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def bolt_uri():
    """A TCP Bolt server on a thread of its own; every session it accepts gets a new engine on a new connection."""
    from iris_vector_graph.bolt_server import start_tcp_bolt_server
    from iris_vector_graph.engine import IRISGraphEngine

    made = []

    def factory():
        # the server's own kind of connection (`_make_engine`: iris.dbapi.connect), one per session
        import iris

        conn = iris.dbapi.connect(hostname="localhost", port=rr.PORT, namespace=NS, username="_SYSTEM",
                                  password="SYS")
        made.append(conn)
        return IRISGraphEngine(conn, embedding_dimension=rr.DIM)

    port, loop, ready = _free_port(), asyncio.new_event_loop(), threading.Event()

    async def serve():
        await start_tcp_bolt_server(factory, host="127.0.0.1", port=port)
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(15), "the Bolt server did not start"
    yield f"bolt://127.0.0.1:{port}"
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture
def driver(bolt_uri):
    import neo4j
    d = neo4j.GraphDatabase.driver(bolt_uri, auth=("u", "p"))
    yield d
    d.close()


def _tagged(obs, tag: str) -> int:
    """How many nodes carry property tag=<tag>, read from the table."""
    cur = obs.cursor()
    cur.execute('SELECT COUNT(DISTINCT s) FROM Graph_KG.rdf_props WHERE "key" = ? AND val = ?', ["tag", tag])
    return int(cur.fetchone()[0])


def _node_rows(obs, tag: str) -> int:
    cur = obs.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes n WHERE EXISTS "
                '(SELECT 1 FROM Graph_KG.rdf_props p WHERE p.s = n.node_id AND p."key" = ? AND p.val = ?)',
                ["tag", tag])
    return int(cur.fetchone()[0])


def _create(tx, tag: str, nid: str):
    return tx.run("CREATE (n:T472 {id: $id, tag: $tag})", id=nid, tag=tag).consume()


def test_a_rolled_back_create_is_not_there(driver, observer):
    tag = f"rb{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}n")
        assert tx.run("MATCH (n:T472 {tag: $tag}) RETURN count(n) AS c", tag=tag).single()["c"] == 1, \
            "the transaction reads its own write"
        tx.rollback()
    assert _tagged(observer, tag) == 0 and _node_rows(observer, tag) == 0


def test_a_committed_create_stays(driver, observer):
    tag = f"cm{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}n")
        tx.commit()
    assert _tagged(observer, tag) == 1 and _node_rows(observer, tag) == 1


def test_two_writes_in_one_transaction_commit_together_and_roll_back_together(driver, observer):
    tag = f"two{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}a")
        _create(tx, tag, f"{tag}b")
        tx.rollback()
    assert _tagged(observer, tag) == 0
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}a")
        _create(tx, tag, f"{tag}b")
        tx.commit()
    assert _tagged(observer, tag) == 2


def test_autocommit_runs_are_unchanged(driver, observer):
    tag = f"ac{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        s.run("CREATE (n:T472 {id: $id, tag: $tag})", id=f"{tag}n", tag=tag).consume()
    assert _tagged(observer, tag) == 1


def test_execute_write_commits_and_a_raising_function_rolls_back(driver, observer):
    good, bad = f"ew{uuid.uuid4().hex[:8]}", f"ex{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        s.execute_write(_create, good, f"{good}n")

        def boom(tx):
            _create(tx, bad, f"{bad}n")
            raise RuntimeError("the application changed its mind")

        with pytest.raises(RuntimeError, match="changed its mind"):
            s.execute_write(boom)
    assert _tagged(observer, good) == 1
    assert _tagged(observer, bad) == 0


def test_a_failed_statement_takes_the_whole_transaction_with_it(driver, observer):
    import neo4j
    tag = f"fl{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}n")
        with pytest.raises(neo4j.exceptions.Neo4jError):
            tx.run("RETURN 1 / 0 AS v").consume()
        tx.close()
    assert _tagged(observer, tag) == 0


def test_a_connection_dropped_in_a_transaction_rolls_it_back(bolt_uri, observer):
    import neo4j
    tag = f"dr{uuid.uuid4().hex[:8]}"
    d = neo4j.GraphDatabase.driver(bolt_uri, auth=("u", "p"))
    s = d.session()
    tx = s.begin_transaction()
    _create(tx, tag, f"{tag}n")
    # the session ends with the transaction open: no COMMIT, no ROLLBACK, no GOODBYE of the application's own
    d.close()
    import time

    for _ in range(50):
        if _tagged(observer, tag) == 0:
            break
        time.sleep(0.1)
    assert _tagged(observer, tag) == 0


def test_a_session_after_a_rollback_writes_normally(driver, observer):
    tag = f"nx{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        tx = s.begin_transaction()
        _create(tx, tag, f"{tag}a")
        tx.rollback()
        s.run("CREATE (n:T472 {id: $id, tag: $tag})", id=f"{tag}b", tag=tag).consume()
    assert _tagged(observer, tag) == 1


def _prop(obs, nid: str, key: str):
    cur = obs.cursor()
    cur.execute('SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?', [nid, key])
    row = cur.fetchone()
    return None if row is None else row[0]


def _edges(obs, a: str, b: str) -> int:
    cur = obs.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", [a, b])
    return int(cur.fetchone()[0])


def test_every_kind_of_write_a_transaction_rolls_back_leaves_the_database_as_it_was(driver, observer):
    """MERGE, SET, a relationship CREATE and a DELETE, each on committed data, in one transaction that rolls back."""
    tag = f"all{uuid.uuid4().hex[:8]}"
    a, b, c = f"{tag}a", f"{tag}b", f"{tag}c"
    with driver.session() as s:
        s.run("CREATE (n:T472 {id: $a, tag: $tag, v: 'one'}), (m:T472 {id: $b, tag: $tag})",
              a=a, b=b, tag=tag).consume()
    assert _tagged(observer, tag) == 2 and _prop(observer, a, "v") == "one"

    with driver.session() as s:
        tx = s.begin_transaction()
        tx.run("MATCH (n:T472 {id: $a}) SET n.v = 'two'", a=a).consume()
        tx.run("MATCH (n:T472 {id: $a}), (m:T472 {id: $b}) CREATE (n)-[:R472]->(m)", a=a, b=b).consume()
        tx.run("MERGE (n:T472 {id: $c, tag: $tag})", c=c, tag=tag).consume()
        tx.run("MATCH (m:T472 {id: $b}) DETACH DELETE m", b=b).consume()
        tx.rollback()

    assert _prop(observer, a, "v") == "one"
    assert _edges(observer, a, b) == 0
    assert _tagged(observer, tag) == 2, "the MERGE's node is gone and the deleted node is back"
    assert _node_rows(observer, tag) == 2
