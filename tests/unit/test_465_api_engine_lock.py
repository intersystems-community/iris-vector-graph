"""4.6.5: every endpoint of cypher_api reaches the shared engine with `_engine_lock` held.

A fake engine counts the threads inside it at once while TestClient threads call /health, /db/neo4j/query/v2 and
other endpoints together: before 4.6.5 two were inside together (only `_run_cypher` took the lock). The source check
fails when a new endpoint calls `_get_engine()` itself.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from iris_vector_graph.cypher_api import app


class ConcurrencyTrackingEngine:
    """Fake engine recording maximum concurrent callers."""

    def __init__(self):
        self.active_count = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()
        # Mock methods to track
        self.conn = MagicMock()

    def _enter(self):
        with self._lock:
            self.active_count += 1
            self.max_concurrent = max(self.max_concurrent, self.active_count)
        time.sleep(0.05)  # widen race window

    def _exit(self):
        with self._lock:
            self.active_count -= 1

    def execute_cypher(self, query: str, parameters: dict | None = None):
        self._enter()
        try:
            # Mock result (simplified - no import needed)
            result = MagicMock()
            result.error = None
            result.columns = ["c"]
            result.rows = [[42]]
            return result
        finally:
            self._exit()

    def get_labels(self):
        self._enter()
        try:
            return ["Label1"]
        finally:
            self._exit()

    def get_relationship_types(self):
        self._enter()
        try:
            return ["REL_TYPE"]
        finally:
            self._exit()

    def get_property_keys(self):
        self._enter()
        try:
            return ["prop1"]
        finally:
            self._exit()

    def get_node_count(self):
        self._enter()
        try:
            return 100
        finally:
            self._exit()

    def get_edge_count(self):
        self._enter()
        try:
            return 50
        finally:
            self._exit()

    def get_label_distribution(self):
        self._enter()
        try:
            return {"Label1": 100}
        finally:
            self._exit()

    def status(self):
        self._enter()
        try:
            # Mock status result
            st = MagicMock()
            st.tables.nodes = 100
            st.tables.edges = 50
            st.tables.labels = 1
            st.tables.node_embeddings = 10
            st.adjacency.kg_populated = True
            st.adjacency.nkg_populated = False
            st.adjacency.bfs_path = "arno"
            st.objectscript.deployed = True
            st.arno.loaded = False
            st.probe_ms = 5
            st.errors = []
            return st
        finally:
            self._exit()

    def fhir_patient_anchors(self, patient_id: str, graph: str | None = None):
        self._enter()
        try:
            return {"graphs": [], "anchors": []}
        finally:
            self._exit()


@pytest.fixture
def fake_engine():
    """Patch _get_engine to return a concurrency-tracking fake."""
    engine = ConcurrencyTrackingEngine()
    with patch("iris_vector_graph.cypher_api._get_engine", return_value=engine):
        with patch("iris_vector_graph.cypher_api._make_engine", return_value=engine):
            yield engine


def test_health_and_query_concurrency(fake_engine):
    """Concurrent /health and /db/neo4j/query/v2 must serialize through the lock."""
    client = TestClient(app)
    errors = []

    def health_poller():
        for _ in range(10):
            try:
                resp = client.get("/health")
                assert resp.status_code == 200
            except Exception as e:
                errors.append(e)
            time.sleep(0.01)

    def query_runner():
        for _ in range(5):
            try:
                resp = client.post(
                    "/db/neo4j/query/v2",
                    json={"statement": "MATCH (n) RETURN n.id AS id LIMIT 10"}
                )
                assert resp.status_code == 200
            except Exception as e:
                errors.append(e)
            time.sleep(0.02)

    t1 = threading.Thread(target=health_poller, daemon=True)
    t2 = threading.Thread(target=query_runner, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"Concurrent calls raised: {errors}"
    assert fake_engine.max_concurrent == 1, (
        f"Expected max_concurrent=1 (serialized), got {fake_engine.max_concurrent}"
    )


def test_multiple_schema_endpoints_concurrency(fake_engine):
    """Multiple schema endpoints called concurrently must serialize."""
    client = TestClient(app)
    errors = []
    results = []

    def call_schema():
        try:
            resp = client.get("/schema")
            assert resp.status_code == 200
            results.append(("schema", resp.json()))
        except Exception as e:
            errors.append(e)

    def call_indexes():
        try:
            resp = client.get("/indexes")
            assert resp.status_code == 200
            results.append(("indexes", resp.json()))
        except Exception as e:
            errors.append(e)

    def call_server():
        try:
            resp = client.get("/server")
            assert resp.status_code == 200
            results.append(("server", resp.json()))
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=call_schema, daemon=True),
        threading.Thread(target=call_indexes, daemon=True),
        threading.Thread(target=call_server, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Concurrent calls raised: {errors}"
    assert fake_engine.max_concurrent == 1, (
        f"Expected max_concurrent=1 (serialized), got {fake_engine.max_concurrent}"
    )


def test_only_the_locked_ways_in_touch_the_cached_engine():
    """Read from the module's loaded code, so a new endpoint that calls ``_get_engine()`` itself fails here: only
    ``_engine()`` and ``_run_cypher`` (which holds ``_engine_lock``) may get or reset the shared engine."""
    import types

    from iris_vector_graph import cypher_api
    from tests.unit._code import names

    callers = {
        name for name, fn in vars(cypher_api).items()
        if isinstance(fn, types.FunctionType) and fn.__module__ == cypher_api.__name__
        and names(fn) & {"_get_engine", "_reset_engine"} and name not in ("_get_engine", "_reset_engine")
    }
    assert callers == {"_engine", "_run_cypher"}, callers
    assert "_engine_lock" in names(cypher_api._run_cypher)
