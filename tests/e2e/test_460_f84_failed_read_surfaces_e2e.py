"""4.6.0 fixes84, live: a read that fails in IRIS is an error on every surface, never no rows and no error.

`RETURN 1 / 0` fails in IRIS (an integer divided by zero raises, 4.6.0), and `execute_cypher` answers it in
`result.error`; REST answers 400. Before (measured 2026-11-07 on 2026.3 at 3c1a85c7): the GraphQL `cypher` field
answered `rows: []` and Bolt zero records and SUCCESS. ivg.eigenvector's raise on a graph with no cycle is the same on
each surface (tests/e2e/test_460_r314_call_yield_values_e2e.py). Same variables as that file: IVG_R314_HOST,
IVG_R314_PORT, IVG_R314_NAMESPACE (a set-up namespace), under whichever driver runs it.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

HOST = os.environ.get("IVG_R314_HOST")
NAMESPACE = os.environ.get("IVG_R314_NAMESPACE", "")
pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not (HOST and NAMESPACE), reason="IVG_R314_HOST or IVG_R314_NAMESPACE is not set")]

FAILS = "RETURN 1 / 0 AS v"


@pytest.fixture(scope="module")
def eng():
    import iris

    conn = iris.dbapi.connect(hostname=HOST, port=int(os.environ.get("IVG_R314_PORT", "1972")), namespace=NAMESPACE,
                              username="_SYSTEM", password="SYS")
    try:
        yield IRISGraphEngine(conn, embedding_dimension=8, native_cypher=False)
    finally:
        conn.close()


def test_execute_cypher_answers_the_failure_in_result_error(eng):
    r = eng.execute_cypher(FAILS)
    assert r.rows == [] and r.error and "SQL" in r.error, (r.rows, r.error)


def test_rest_answers_400(eng):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    with patch.object(cypher_api, "_get_engine", return_value=eng):
        resp = TestClient(cypher_api.app).post("/api/cypher", json={"query": FAILS, "parameters": {}})
    assert resp.status_code == 400 and "SQL" in resp.text, resp.text


def test_graphql_answers_a_graphql_error(eng):
    from iris_vector_graph.gql.resolvers import resolve_cypher

    with pytest.raises(Exception, match="SQL"):
        asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": eng}), FAILS))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_bolt_sends_failure(eng):
    neo4j = pytest.importorskip("neo4j")
    from iris_vector_graph.bolt_server import start_tcp_bolt_server

    port, loop, ready = _free_port(), asyncio.new_event_loop(), threading.Event()

    async def serve():
        await start_tcp_bolt_server(lambda: eng, host="127.0.0.1", port=port)
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10), "the Bolt server did not start"
    driver = neo4j.GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("u", "p"))
    try:
        with driver.session() as s, pytest.raises(neo4j.exceptions.Neo4jError, match="SQL"):
            list(s.run(FAILS))
    finally:
        driver.close()
        loop.call_soon_threadsafe(loop.stop)
