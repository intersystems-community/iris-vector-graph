"""4.6.5: the HTTP API's endpoints share one connection, and every one now takes the lock `_run_cypher` took.

Reported from a 4.2.2 deployment whose Docker health check probed every 15 s: 13 of 200 queries failed (SQLCODE -400
`<UNDEFINED>Server+484^%SYS.DBSRV.1`, the server lost the statement handle), 0 of 200 with the check off. Its probe
failed on every call there, and 4.2.2 rolled the shared connection back after a failed statement (4.6.4 no longer
does). On 4.6.5 with driver 5.4.0 the race did not reproduce, with or without the lock; the lock is there because the
driver's `threadsafety` is 1 (threads may not share a connection). tests/unit/test_465_api_engine_lock.py pins it:
it fails without the lock. Here, on IRIS, one thread runs one-hop queries through `/db/neo4j/query/v2` while another
probes `/health`, and every query must answer its known row and every probe 200.

The graph, a ring n0 -> n1 -> n2 -> n3 -> n4 -> n0: the one-hop query from n0 answers Node1, and nothing else.

    IVG_NAMESPACE=IVG463LCK pytest tests/e2e/test_465_api_engine_lock_e2e.py
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

QUERY = "MATCH (a {id: $id})-[:LINKS_TO]->(b) RETURN b.name AS name"


@pytest.fixture
def client(monkeypatch):
    import iris
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    conn = ns463.connect()
    eng = ns463.engines(conn)["default"]
    erase = lambda: iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")  # noqa: E731
    erase()
    ids = [f"n{i}" for i in range(5)]
    for i, node in enumerate(ids):
        eng.create_node(node, labels=["Node"], properties={"id": node, "name": f"Node{i}"})
    for i, node in enumerate(ids):
        eng.create_edge(node, "LINKS_TO", ids[(i + 1) % 5])
    assert ns463.rows(eng, QUERY, {"id": "n0"}) == [["Node1"]], "the ring did not load"
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    monkeypatch.setattr(cypher_api, "_engine_cache", eng)  # the app's one shared engine is this namespace's
    yield TestClient(cypher_api.app)
    erase()
    conn.close()


def test_a_health_probe_beside_queries_breaks_none_of_them(client):
    answers, probes = [], []

    def probe():
        for _ in range(100):
            probes.append(client.get("/health").status_code)
            time.sleep(0.01)

    def query():
        for _ in range(50):
            r = client.post("/db/neo4j/query/v2", json={"statement": QUERY, "parameters": {"id": "n0"}})
            answers.append((r.status_code, r.json().get("data", {}).get("values") if r.status_code == 200 else r.text))

    threads = [threading.Thread(target=probe), threading.Thread(target=query)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a request hung"
    assert [a for a in answers if a != (200, [["Node1"]])] == [], answers
    assert len(answers) == 50
    assert probes == [200] * 100
