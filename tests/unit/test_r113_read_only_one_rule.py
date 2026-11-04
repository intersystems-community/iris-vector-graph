"""R113: one read-only rule on every route.

`execute_cypher(read_only=True)` decided "writes" with the parser (`CypherQuery.writes`,
every `;` statement checked), while the servers decided it with
`api_auth.is_mutation_cypher`, a keyword match over the raw text: cypher_api's
`/api/cypher`, its Neo4j transaction commit and Query API v2, api.main's `/api/cypher`
and GraphQL `executeCypher`, the `cypher` field in `iris_vector_graph.gql`, and Bolt's
RUN. The two disagreed: the keyword match refused a read that names `CREATE` inside a
string or a comment, and let through a query that does not parse (which the parser
cannot vouch for).

Now one function, `iris_vector_graph.cypher.writes_cypher`, decides for all of them.
Each case below is sent down every route under `IVG_READ_ONLY=true`; every route must
give the verdict in the table, and the engine's own `execute_cypher(read_only=True)`
must give it too. A refused request must run nothing.

No IRIS: engines and connections are MagicMocks.
"""
from __future__ import annotations

import asyncio
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from iris_vector_graph.result import IVGResult  # noqa: E402

REFUSED, ALLOWED = True, False

BROWSER_METADATA = (
    "CALL db.labels() YIELD label RETURN {name:'labels', data:COLLECT(label)[..1000]} AS result "
    "UNION ALL CALL db.relationshipTypes() YIELD relationshipType "
    "RETURN {name:'relationshipTypes', data:COLLECT(relationshipType)[..1000]} AS result "
    "UNION ALL CALL db.propertyKeys() YIELD propertyKey "
    "RETURN {name:'propertyKeys', data:COLLECT(propertyKey)[..1000]} AS result"
)

# name -> (query, verdict under read-only)
CASES = {
    # a write wherever it is written
    "UNION write branch": (
        "MATCH (n) RETURN n.id AS id UNION MATCH (m) CREATE (x:Probe {id:'u'}) RETURN x.id AS id", REFUSED),
    "UNION ALL write branch": ("MATCH (n) RETURN n.id AS id UNION ALL CREATE (x:Probe) RETURN x.id AS id", REFUSED),
    "CALL { CREATE } body": ("CALL { CREATE (x:Probe {id:'s'}) RETURN x } RETURN x", REFUSED),
    "CALL { WITH n CREATE } body": ("MATCH (n) CALL { WITH n CREATE (x:Probe {id:'s'}) } RETURN n", REFUSED),
    "; write last": ("CALL db.labels(); CREATE (x:Probe {id:'p'}) RETURN x", REFUSED),
    "; write first": ("CREATE (x:Probe {id:'p'}) RETURN x; CALL db.labels()", REFUSED),
    "; write in the middle": ("CALL db.labels(); MERGE (x:Probe {id:'p'}); CALL db.labels()", REFUSED),
    "; write after a MATCH, no CALL": ("MATCH (n) RETURN n; CREATE (x:Probe {id:'p'})", REFUSED),
    "lower case ; write": ("call db.labels(); create (x:Probe {id:'p'}) return x", REFUSED),
    "lower case SET after WITH": ("match (n) with n set n.p = 1 return n", REFUSED),
    "mixed case DETACH DELETE": ("Match (n) Detach Delete n", REFUSED),
    "MERGE after WITH": ("MATCH (n) WITH n MERGE (x:Probe {id:'m'}) RETURN x", REFUSED),
    "SET after WITH": ("MATCH (n) WITH n SET n.p = 1 RETURN n", REFUSED),
    "REMOVE after WITH": ("MATCH (n) WITH n REMOVE n.p RETURN n", REFUSED),
    "DETACH DELETE after WITH": ("MATCH (n) WITH n DETACH DELETE n", REFUSED),
    "FOREACH": ("UNWIND [1] AS i FOREACH (j IN [1] | CREATE (:Probe {id:'f'}))", REFUSED),
    "approx_count_distinct beside a CREATE": (
        "MATCH (n) CREATE (x:Probe {id:'a'}) RETURN approx_count_distinct(n) AS c", REFUSED),
    "schema DDL": ("CREATE INDEX FOR (n:Probe) ON (n.id)", REFUSED),
    # a query that does not parse: the parser cannot vouch for it
    "does not parse": ("MATC (n) RETRN n", REFUSED),
    "not Cypher at all": ("THIS IS NOT CYPHER", REFUSED),
    # reads, however they are spelled
    "plain read": ("MATCH (n) RETURN n.id AS id", ALLOWED),
    "lower case read": ("match (n) return n.id as id", ALLOWED),
    "CREATE inside a string": ("MATCH (n {name: 'CREATE'}) RETURN n.id AS id", ALLOWED),
    "SET and DELETE inside a string with a ;": (
        "MATCH (n) WHERE n.note = 'SET it; DELETE it' RETURN n.id AS id", ALLOWED),
    "CREATE in a line comment": ("MATCH (n) // CREATE (x)\nRETURN n.id AS id", ALLOWED),
    "MERGE in a block comment": ("MATCH (n) /* MERGE (x) */ RETURN n.id AS id", ALLOWED),
    "label named Remove": ("MATCH (n:Remove) RETURN n.id AS id", ALLOWED),
    "backquoted create": ("MATCH (n) RETURN n.`create` AS c", ALLOWED),
    "; reads only": ("CALL db.labels() YIELD label RETURN label; CALL db.labels() YIELD label RETURN label", ALLOWED),
    "UNION of reads": ("MATCH (n) RETURN n.id AS id UNION MATCH (m) RETURN m.id AS id", ALLOWED),
    "CALL { } read body": ("MATCH (n) CALL { WITH n MATCH (n)-->(m) RETURN m } RETURN m", ALLOWED),
    "procedure read": ("CALL ivg.ppr({startNodes:['x']}) YIELD nodeId RETURN nodeId", ALLOWED),
    # what execute_cypher answers without the parser, and Neo4j Browser sends
    "SHOW": ("SHOW INDEXES", ALLOWED),
    "EXPLAIN a read": ("EXPLAIN MATCH (n) RETURN n", ALLOWED),
    "Neo4j Browser metadata": (BROWSER_METADATA, ALLOWED),
}
IDS = list(CASES)
PARAMS = [pytest.param(q, v, id=name) for name, (q, v) in CASES.items()]


@pytest.fixture(autouse=True)
def _read_only(monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")


# ---------------------------------------------------------------------------
# the rule itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,verdict", PARAMS)
def test_writes_cypher(query, verdict):
    from iris_vector_graph.cypher import writes_cypher

    assert writes_cypher(query) is verdict


@pytest.mark.parametrize("query,verdict", PARAMS)
def test_is_mutation_cypher_is_the_same_rule(query, verdict):
    from iris_vector_graph.api_auth import is_mutation_cypher

    assert is_mutation_cypher(query) is verdict


# ---------------------------------------------------------------------------
# the engine: execute_cypher(read_only=True)
# ---------------------------------------------------------------------------

def _stub_engine():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    ran = []

    def _record(parsed, parameters, procedures=None):
        ran.append(parsed)
        return IVGResult(columns=["x"], rows=[])

    eng._execute_parsed = _record
    return eng, ran, cursor


@pytest.mark.parametrize("query,verdict", PARAMS)
def test_execute_cypher_read_only(query, verdict):
    eng, ran, cursor = _stub_engine()
    if verdict is REFUSED:
        with pytest.raises(PermissionError, match="Read-only mode"):
            eng.execute_cypher(query, {}, read_only=True)
        assert ran == [], f"ran {ran} before refusing"
    else:
        eng.execute_cypher(query, {}, read_only=True)


# ---------------------------------------------------------------------------
# cypher_api: /api/cypher, /db/neo4j/tx/commit, /db/{db}/query/v2
# ---------------------------------------------------------------------------

@pytest.fixture
def cypher_api():
    from iris_vector_graph import cypher_api as mod

    eng = MagicMock(name="engine")
    eng.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[1]])
    with patch.object(mod, "_make_engine", return_value=eng), \
         patch.object(mod, "_get_engine", return_value=eng), \
         patch.object(mod, "_engine_cache", eng):
        yield TestClient(mod.app, raise_server_exceptions=False), eng


CYPHER_API_ROUTES = {
    "/api/cypher": lambda q: {"query": q},
    "/db/neo4j/tx/commit": lambda q: {"statements": [{"statement": q}]},
    "/db/neo4j/query/v2": lambda q: {"statement": q},
}


@pytest.mark.parametrize("path", list(CYPHER_API_ROUTES))
@pytest.mark.parametrize("query,verdict", PARAMS)
def test_cypher_api(cypher_api, path, query, verdict):
    client, eng = cypher_api
    resp = client.post(path, json=CYPHER_API_ROUTES[path](query))
    refused = resp.status_code == 403
    assert refused is verdict, (resp.status_code, resp.text[:300])
    assert eng.execute_cypher.called is (not verdict)


# ---------------------------------------------------------------------------
# api.main: /api/cypher router and GraphQL executeCypher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,verdict", PARAMS)
def test_api_main_cypher_router(query, verdict):
    from api.dependencies import get_db_connection, get_engine_optional
    from api.routers.cypher import router

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.description = [("id",)]
    cursor.fetchall.return_value = []
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_connection] = lambda: conn
    app.dependency_overrides[get_engine_optional] = lambda: SimpleNamespace(
        _fetch_first_unsafe=True, get_table_mapping=lambda label: None
    )
    resp = TestClient(app, raise_server_exceptions=False).post("/api/cypher", json={"query": query})
    refused = resp.status_code == 403
    assert refused is verdict, (resp.status_code, resp.text[:300])
    if verdict is REFUSED:
        cursor.execute.assert_not_called()


@pytest.mark.parametrize("query,verdict", PARAMS)
def test_api_main_graphql_execute_cypher(query, verdict):
    from api.gql.schema import schema

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.description = None
    cursor.fetchall.return_value = []
    document = "query($q: String!) { executeCypher(query: $q) }"
    result = asyncio.run(
        schema.execute(document, variable_values={"q": query}, context_value={"db_connection": conn})
    )
    assert result.errors is None, result.errors
    answer = result.data["executeCypher"]
    answer = json.loads(answer) if isinstance(answer, str) else answer
    refused = isinstance(answer, dict) and "read-only" in str(answer.get("error", "")).lower()
    assert refused is verdict, answer
    if verdict is REFUSED:
        cursor.execute.assert_not_called()


# ---------------------------------------------------------------------------
# iris_vector_graph.gql: the cypher field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,verdict", PARAMS)
def test_ivg_graphql_cypher_field(query, verdict):
    from iris_vector_graph.gql.resolvers import resolve_cypher

    eng = MagicMock(name="engine")
    eng.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[1]])
    info = SimpleNamespace(context={"engine": eng})
    try:
        asyncio.run(resolve_cypher(info, query=query, parameters=None))
        refused = False
    except PermissionError:
        refused = True
    assert refused is verdict
    assert eng.execute_cypher.called is (not verdict)


# ---------------------------------------------------------------------------
# Bolt RUN
# ---------------------------------------------------------------------------

def _bolt(query: str):
    from iris_vector_graph.bolt_server import (
        TAG_FAILURE, TAG_GOODBYE, TAG_HELLO, TAG_PULL, TAG_RUN, BoltSession, PackStream,
        decode_messages, encode_message,
    )

    def msg(tag, *fields):
        body = b"".join(PackStream.pack(f) for f in fields)
        return encode_message(bytes([0xB0 | len(fields), tag]) + body)

    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", 0x0404, 0, 0, 0)
    ws.receive_bytes = AsyncMock(side_effect=[
        handshake,
        msg(TAG_HELLO, {"user_agent": "t"}),
        msg(TAG_RUN, query, {}, {}),
        msg(TAG_PULL, {"n": -1}),
        msg(TAG_GOODBYE),
        Exception("end"),
    ])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = MagicMock()
    engine.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[1]])
    asyncio.run(BoltSession(ws, lambda: engine).run())
    failures = []
    for call in ws.send_bytes.call_args_list[1:]:
        for m in decode_messages(call[0][0]):
            if m[1] == TAG_FAILURE:
                failures.append(PackStream.unpack(m, 2)[0])
    return engine, failures


@pytest.mark.parametrize("query,verdict", PARAMS)
def test_bolt_run(query, verdict):
    engine, failures = _bolt(query)
    refused = any(f.get("code") == "Neo.ClientError.Statement.AccessMode" for f in failures)
    assert refused is verdict, failures
    assert engine.execute_cypher.called is (not verdict)


# ---------------------------------------------------------------------------
# the parse cache: a query run once is judged the same the second time
# ---------------------------------------------------------------------------

def test_running_a_chained_query_leaves_its_cached_ast_whole():
    """execute_cypher set `subsequent_queries = []` on the AST parse_query caches, so after one run of a chained
    query (`... RETURN ... MATCH ... CREATE ...`) the cached AST had lost its second part: the next run silently
    ran the first part only, and writes_cypher, which reads the same cache, called the CREATE a read."""
    from iris_vector_graph.cypher import writes_cypher

    query = "MATCH (n) RETURN n.id AS id MATCH (m) CREATE (x:Probe {id:'c'}) RETURN x"
    assert writes_cypher(query) is True
    eng, ran, _ = _stub_engine()
    eng.execute_cypher(query, {})
    assert len(ran) == 2
    assert writes_cypher(query) is True, "the first run emptied the cached AST's subsequent_queries"
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, {}, read_only=True)
    ran.clear()
    eng.execute_cypher(query, {})
    assert len(ran) == 2, "the second run dropped the chained part"
