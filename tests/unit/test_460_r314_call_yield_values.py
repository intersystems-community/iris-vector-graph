"""4.6.0 R314 (part B): every column a CALL yields answers its own Python type, on every surface.

Measured at be6cd07c: `CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost RETURN path, totalCost`
answered `path` as its JSON text ('{"nodes":["alien","brazil"],...}'), and `RETURN path.nodes` too, where the 4.6.0
docs promise a list, map or path comes back as the Python list or dict. `apoc.meta.schema`'s `value` was an IVGResult
object, not a map.

The cases are generated from the signature registry (iris_vector_graph/cypher/procedure_signatures.py), so a procedure
added to the translator's or the engine's registry has no case and fails `test_every_procedure_has_a_case` until it
gets one. Each case drives the procedure's own handler or translation over fakes: the handler's inputs (a cursor's
rows, an engine method's answer, a class method's JSON) or the driver's raw row for a translated CALL. Every expected
value is worked out by hand from those inputs, never read from IVG's answer. Each case runs through execute_cypher
(engines with native_cypher on and off), REST /api/cypher, Bolt and GraphQL's `cypher` field. A LIST column answers a
list, a MAP or PATH column a dict (a PATH holds exactly the keys PATH_KEYS names), a scalar keeps its type, and a NODE
column comes back as `RETURN n` answers a node (three columns; Bolt: a Node). The live answers are in
tests/e2e/test_460_r314_call_yield_values_e2e.py.
"""

from __future__ import annotations

import asyncio
import json
import struct
from contextlib import ExitStack
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from iris_vector_graph import engine as engine_module
from iris_vector_graph._engine import query as query_module
from iris_vector_graph.cypher.procedure_signatures import CONTAINER_TYPES, PROCEDURE_SIGNATURES, path_keys, signature
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeCursor:
    """A cursor answering each statement with the rows of the first rule whose text it contains, else none."""

    def __init__(self, rules):
        self.rules = rules
        self.rows: list = []
        self.description = [("c",)]

    def execute(self, sql, params=None):
        self.rows = next((list(rows) for text, rows in self.rules if text in sql), [])

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchmany(self, n=None):
        return list(self.rows)

    def close(self):
        pass


def _engine(native: bool, rules=()) -> IRISGraphEngine:
    conn = MagicMock()
    conn.iris = None
    conn._connection = conn
    eng = IRISGraphEngine(conn, native_cypher=native)
    if rules:
        conn.cursor.side_effect = lambda *a, **k: FakeCursor(list(rules))
    return eng


def _stub_graph_reads(stack: ExitStack, eng) -> None:
    """The namespace reads a CALL makes before it runs: one graph with edges (the default one), no named graph's, no
    vector route registry, the default vector table, and indexes of the default graph."""
    cls = type(eng)
    stack.enter_context(patch.object(cls, "_graphs_with_edges", lambda self: [""]))
    stack.enter_context(patch.object(cls, "_named_graph_with_edges", lambda self: None))
    stack.enter_context(patch.object(cls, "_knn_routes", lambda self, model_key=None: None))
    stack.enter_context(patch.object(cls, "_knn_route", lambda self, graph, model_key=None: ("Graph_KG.kg_NodeEmbeddings", "DOUBLE")))
    stack.enter_context(patch.object(cls, "_index_graph", lambda self, name, kind: ""))


def _raw_sql_row(row: dict):
    """A setup that answers the translated statement with one raw driver row: `row` maps a SELECT alias to its raw
    value (a missing alias is NULL)."""

    def setup(stack, eng):
        seen = {}
        real = query_module.translate_to_sql

        def translate(*a, **k):
            seen["t"] = t = real(*a, **k)
            return t

        def execute_sql(sql, params, read_only=True):
            aliases = list(seen["t"].select_aliases)
            return IVGResult(columns=aliases, rows=[[row.get(a) for a in aliases]])

        stack.enter_context(patch.object(query_module, "translate_to_sql", translate))
        stack.enter_context(patch.object(eng._store, "execute_sql", execute_sql))

    return setup


def _patch_engine(**methods):
    def setup(stack, eng):
        for name, fn in methods.items():
            stack.enter_context(patch.object(eng, name, fn))

    return setup


def _patch_classmethod(answers: Dict[str, str]):
    """`_call_classmethod` answering each Graph.KG method named in `answers` with its JSON."""

    def setup(stack, eng):
        def call(conn, cls, method, *args):
            return answers[method]

        stack.enter_context(patch.object(engine_module, "_call_classmethod", call))

    return setup


# ---------------------------------------------------------------------------
# the cases: one or more per procedure, every expected value worked out by hand
# ---------------------------------------------------------------------------

NODE_RAW = {"node_id": "a", "node_labels": '["L"]', "node_props": "[]"}
WEIGHTED_JSON = '{"nodes":["alien","brazil"],"rels":["LIKES"],"costs":[1.5],"length":1,"totalCost":1.5}'
WEIGHTED = {"nodes": ["alien", "brazil"], "rels": ["LIKES"], "costs": [1.5], "length": 1, "totalCost": 1.5}


@dataclass
class Case:
    proc: str
    query: str
    #: yielded column -> the value of the first row (a NODE column: the node's ID)
    want: Dict[str, object]
    setup: Callable = lambda stack, eng: None
    rules: tuple = ()
    #: the returned columns, when they are not the yielded names (a renamed or projected RETURN)
    returned: Optional[Dict[str, str]] = None
    #: rows answered, when not one
    n_rows: int = 1
    #: a NODE column answered as the node's ID alone (the vector.search handler's form, open: see the report)
    node_as_id: bool = False
    raises: Optional[str] = None
    label: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def id(self):
        return f"{self.proc}{'-' + self.label if self.label else ''}"


SCHEMA_VIZ = {
    "nodes": [{"id": 0, "name": "Person", "labels": ["Person"], "properties": [{"name": "name", "type": "String"}]}],
    "relationships": [{"id": 0, "type": "KNOWS", "startNode": 0, "endNode": 0, "properties": []}],
}

CASES: List[Case] = [
    # ---- translated (a CTE): the driver's raw row decoded --------------------------------------------------------
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost "
         "RETURN path, totalCost ORDER BY totalCost",
         {"path": WEIGHTED, "totalCost": 1.5}, _raw_sql_row({"path": WEIGHTED_JSON, "totalCost": 1.5}),
         label="translated"),
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path AS q, totalCost RETURN q, totalCost LIMIT 1",
         {"path": WEIGHTED, "totalCost": 1.5}, _raw_sql_row({"q": WEIGHTED_JSON, "totalCost": 1.5}),
         returned={"path": "q", "totalCost": "totalCost"}, label="yield-alias"),
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost "
         "WITH path, totalCost WHERE totalCost < 5 RETURN path, totalCost",
         {"path": WEIGHTED, "totalCost": 1.5}, _raw_sql_row({"path": WEIGHTED_JSON, "totalCost": 1.5}),
         label="with"),
    # path.nodes is JSON_VALUE(path, '$.nodes'): the list's JSON text; path.length the number's
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost "
         "RETURN path.nodes AS nodes, path.rels AS rels, path.costs AS costs, path.length AS length, totalCost",
         {"path.nodes": ["alien", "brazil"], "path.rels": ["LIKES"], "path.costs": [1.5], "path.length": 1,
          "totalCost": 1.5},
         _raw_sql_row({"nodes": '["alien","brazil"]', "rels": '["LIKES"]', "costs": "[1.5]", "length": "1",
                       "totalCost": 1.5}),
         returned={"path.nodes": "nodes", "path.rels": "rels", "path.costs": "costs", "path.length": "length",
                   "totalCost": "totalCost"},
         label="path-keys"),
    Case("ivg.vector.search",
         "CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 2) YIELD node, score RETURN node, score ORDER BY score DESC",
         {"node": "a", "score": 0.75}, _raw_sql_row({**NODE_RAW, "score": 0.75}), label="translated"),
    Case("ivg.neighbors", "CALL ivg.neighbors(['b'], 'R') YIELD neighbor RETURN neighbor", {"neighbor": "a"},
         _raw_sql_row({"neighbor_id": "a", "neighbor_labels": '["L"]', "neighbor_props": "[]"})),
    Case("ivg.ppr", "CALL ivg.ppr(['b']) YIELD node, score RETURN node, score", {"node": "a", "score": 0.5},
         _raw_sql_row({**NODE_RAW, "score": 0.5})),
    Case("ivg.bm25.search", "CALL ivg.bm25.search('idx', 'alpha', 2) YIELD node, score RETURN node, score",
         {"node": "a", "score": 2.25}, _raw_sql_row({**NODE_RAW, "score": 2.25})),
    Case("ivg.ivf.search", "CALL ivg.ivf.search('idx', [1.0, 0.0], 2, 1) YIELD node, score RETURN node, score",
         {"node": "a", "score": 0.5}, _raw_sql_row({**NODE_RAW, "score": 0.5})),
    Case("ivg.retrieve", "USE GRAPH '' CALL ivg.retrieve('alpha', 2) YIELD node, rrf_score RETURN node, rrf_score",
         {"node": "a", "rrf_score": 0.03125},
         lambda stack, eng: (_raw_sql_row({**NODE_RAW, "rrf_score": 0.03125})(stack, eng),
                             stack.enter_context(patch.object(eng, "embed_text", lambda text: [1.0, 0.0])))),
    Case("ivg.degreeCentrality",
         "CALL ivg.degreeCentrality({topK: 2}) YIELD node, score, degree RETURN node, score, degree",
         {"node": "a", "score": 0.5, "degree": 2}, _raw_sql_row({**NODE_RAW, "score": 0.5, "degree": 2})),
    Case("ivg.betweenness", "CALL ivg.betweenness({topK: 2}) YIELD node, score RETURN node, score",
         {"node": "a", "score": 3.0}, _raw_sql_row({**NODE_RAW, "score": 3.0})),
    Case("ivg.closeness", "CALL ivg.closeness({topK: 2}) YIELD node, score RETURN node, score",
         {"node": "a", "score": 1.5}, _raw_sql_row({**NODE_RAW, "score": 1.5})),
    Case("ivg.eigenvector", "CALL ivg.eigenvector({topK: 2}) YIELD node, score RETURN node, score",
         {"node": "a", "score": 0.25}, _raw_sql_row({**NODE_RAW, "score": 0.25})),
    Case("ivg.leiden", "CALL ivg.leiden({topK: 2}) YIELD node, community, size RETURN node, community, size",
         {"node": "a", "community": 1, "size": 3}, _raw_sql_row({**NODE_RAW, "community": 1, "size": 3})),
    Case("ivg.triangleCount",
         "CALL ivg.triangleCount({topK: 2}) YIELD node, triangles, lcc RETURN node, triangles, lcc",
         {"node": "a", "triangles": 1, "lcc": 1.0}, _raw_sql_row({**NODE_RAW, "triangles": 1, "lcc": 1.0})),
    Case("ivg.scc", "CALL ivg.scc({topK: 2}) YIELD node, component, size RETURN node, component, size",
         {"node": "a", "component": 0, "size": 2}, _raw_sql_row({**NODE_RAW, "component": 0, "size": 2})),
    Case("ivg.kcore", "CALL ivg.kcore({topK: 2}) YIELD node, coreness RETURN node, coreness",
         {"node": "a", "coreness": 2}, _raw_sql_row({**NODE_RAW, "coreness": 2})),
    # ---- answered by an engine handler -------------------------------------------------------------------------
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost RETURN path, totalCost",
         {"path": WEIGHTED, "totalCost": 1.5}, _patch_classmethod({"DijkstraJson": WEIGHTED_JSON}), label="handler"),
    Case("ivg.shortestPath.weighted",
         "CALL ivg.shortestPath.weighted('alien', 'brazil') YIELD path, totalCost",
         {"path": WEIGHTED, "totalCost": 1.5}, _patch_classmethod({"DijkstraJson": WEIGHTED_JSON}),
         label="handler-no-return"),
    Case("ivg.vector.search",
         "CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 2) YIELD node, score",
         {"node": "a", "score": 0.75},
         _patch_engine(_store=SimpleNamespace(
             execute_knn_vec=lambda vec, k, label, graph=None: IVGResult(columns=["id", "score"], rows=[["a", 0.75]]))),
         node_as_id=True, label="handler"),
    Case("db.labels", "CALL db.labels() YIELD label RETURN label", {"label": "Movie"}, n_rows=2,
         rules=(("DISTINCT label", [("Movie",), ("Person",)]),)),
    Case("db.relationshipTypes", "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
         {"relationshipType": "KNOWS"}, rules=(("DISTINCT p FROM", [("KNOWS",)]),)),
    Case("db.propertyKeys", "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey", {"propertyKey": "name"},
         rules=(('"key" FROM', [("name",)]),)),
    Case("db.schema.visualization", "CALL db.schema.visualization() YIELD nodes, relationships",
         {"nodes": SCHEMA_VIZ["nodes"], "relationships": SCHEMA_VIZ["relationships"]},
         _patch_engine(get_schema_visualization=lambda: json.loads(json.dumps(SCHEMA_VIZ)))),
    Case("db.schema.nodeTypeProperties",
         "CALL db.schema.nodeTypeProperties() YIELD nodeType, nodeLabels, propertyName, propertyTypes, mandatory",
         {"nodeType": ":`Person`", "nodeLabels": ["Person"], "propertyName": "name", "propertyTypes": ["String"],
          "mandatory": False},
         rules=(("TOP 1 rl.s", [("alice",)]), ("DISTINCT label", [("Person",)]), ('"key" FROM', [("name",)]))),
    Case("db.schema.relTypeProperties",
         "CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes, mandatory",
         # by hand: the sampled qualifiers' keys and `weight`, sorted
         {"relType": "KNOWS", "propertyName": "since", "propertyTypes": ["STRING"], "mandatory": False}, n_rows=2,
         rules=(("TOP 1 qualifiers", [('{"since": 2020}',)]), ("p IS NOT NULL", [("KNOWS",)]))),
    Case("dbms.components", "CALL dbms.components() YIELD name, versions, edition",
         {"name": "iris-vector-graph", "versions": ["5.0.0"], "edition": "community"}),
    Case("dbms.procedures",
         "CALL dbms.procedures() YIELD name, signature, description, mode, admin, option, defaultBuiltInRoles, "
         "isDeprecated, worksOnSystem, argumentDescription",
         {"name": "db.labels", "signature": "db.labels() :: (label :: STRING)", "description": "List all labels",
          "mode": "READ", "admin": False, "option": {}, "defaultBuiltInRoles": "neo4j", "isDeprecated": False,
          "worksOnSystem": True, "argumentDescription": []}, n_rows=11),
    Case("dbms.functions", "CALL dbms.functions()", {}, n_rows=0),
    Case("dbms.clientConfig", "CALL dbms.clientConfig() YIELD key, value",
         {"key": "browser.allow_outgoing_connections", "value": "false"}, n_rows=6),
    Case("dbms.security.showCurrentUser", "CALL dbms.security.showCurrentUser() YIELD username, roles, flags",
         {"username": "neo4j", "roles": [], "flags": []}),
    Case("dbms.showCurrentUser", "CALL dbms.showCurrentUser() YIELD username, roles, flags",
         {"username": "neo4j", "roles": [], "flags": []}),
    Case("dbms.queryJmx", "CALL dbms.queryJmx('*:*') YIELD name, description, attributes",
         # by hand: 3 nodes and 2 edges
         {"name": "org.neo4j:instance=kernel#0,name=Store file sizes", "description": "Store file sizes",
          "attributes": {"TotalStoreSize": {"value": 600}, "NodeStoreSize": {"value": 300},
                         "RelationshipStoreSize": {"value": 200}, "PropertyStoreSize": {"value": 150},
                         "StringStoreSize": {"value": 90}, "ArrayStoreSize": {"value": 0},
                         "IndexStoreSize": {"value": 0}, "LabelStoreSize": {"value": 30},
                         "SchemaStoreSize": {"value": 4096}}}, n_rows=6,
         rules=((".nodes", [(3,)]), (".rdf_edges", [(2,)]))),
    Case("apoc.meta.data",
         "CALL apoc.meta.data() YIELD label, property, type, elementType, unique, index, existence",
         {"label": "Person", "property": "name", "type": "STRING", "elementType": "node", "unique": False,
          "index": False, "existence": False}, n_rows=2,
         rules=(("DISTINCT label", [("Person",)]), ('"key" FROM', [("name",)]), ("DISTINCT p FROM", [("KNOWS",)]))),
    Case("apoc.meta.schema", "CALL apoc.meta.schema() YIELD value",
         # by hand: apoc.meta.data's two rows, a node label with one property and a relationship type
         {"value": {"Person": {"type": "node", "properties": {
             "name": {"type": "STRING", "indexed": False, "unique": False, "existence": False}}},
             "KNOWS": {"type": "relationship", "properties": {}}}},
         rules=(("DISTINCT label", [("Person",)]), ('"key" FROM', [("name",)]), ("DISTINCT p FROM", [("KNOWS",)]))),
    Case("gds.pageRank.stream", "CALL gds.pageRank.stream('g') YIELD nodeId, score RETURN nodeId, score",
         {"nodeId": "a", "score": 0.625}, n_rows=2,
         setup=_patch_engine(kg_PAGERANK=lambda damping, max_iterations: [("a", 0.625), ("b", 0.375)])),
    Case("gds.betweenness.stream", "CALL gds.betweenness.stream('g') YIELD nodeId, score",
         {"nodeId": "a", "score": 2.0},
         setup=_patch_engine(betweenness_centrality=lambda **kw: [{"id": "a", "score": 2}, {"progress": 1}])),
    # unweighted: every relationship costs 1, the costs accumulate from 0
    Case("gds.shortestPath.dijkstra.stream",
         "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) "
         "YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs",
         {"index": 0, "sourceNode": "a", "targetNode": "c", "totalCost": 2.0, "nodeIds": ["a", "b", "c"],
          "costs": [0.0, 1.0, 2.0]},
         _patch_classmethod({"ShortestPathJson": '[{"nodes":["a","b","c"],"rels":["R","R"]}]'})),
    Case("gds.shortestPath.dijkstra",
         "CALL gds.shortestPath.dijkstra('g', {sourceNode: 'a', targetNode: 'c', "
         "relationshipWeightProperty: 'weight'}) YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs",
         {"index": 0, "sourceNode": "a", "targetNode": "c", "totalCost": 2.5, "nodeIds": ["a", "c"],
          "costs": [0.0, 2.5]},
         _patch_classmethod({"DijkstraJson": '{"nodes":["a","c"],"rels":["R"],"costs":[2.5],"length":1,'
                                             '"totalCost":2.5}'})),
    # shims: called by the gds name, answered by the ivg procedure
    Case("gds.nodeSimilarity.stream",
         "CALL gds.nodeSimilarity.stream('L', 'emb', [1.0, 0.0], 2) YIELD node, score",
         {"node": "a", "score": 0.75},
         _patch_engine(_store=SimpleNamespace(
             execute_knn_vec=lambda vec, k, label, graph=None: IVGResult(columns=["id", "score"], rows=[["a", 0.75]]))),
         node_as_id=True),
    Case("gds.louvain.stream", "CALL gds.louvain.stream('g') YIELD nodeId, communityId", {},
         raises="Call ivg.leiden"),
]


def test_every_procedure_has_a_case():
    """A procedure in the signature registry with no case here is a procedure whose values nothing pins."""
    assert sorted({c.proc for c in CASES}) == sorted(PROCEDURE_SIGNATURES)


def test_each_case_reads_every_column_its_procedure_yields():
    for c in CASES:
        if c.raises or c.n_rows == 0:
            continue
        sig = [col for col, _ in signature(c.proc)]
        named = {k.split(".")[0] for k in c.want}
        assert named == set(sig), (c.id, named, sig)


# ---------------------------------------------------------------------------
# the surfaces: (column names, rows) as each client reads them
# ---------------------------------------------------------------------------


def _via_cypher(eng, q):
    r = eng.execute_cypher(q, {}, raise_on_error=True)
    assert not r.error, r.error
    return list(r.columns), [list(row) for row in r.rows], "columns"


def _via_rest(eng, q):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    with patch.object(cypher_api, "_get_engine", return_value=eng):
        resp = TestClient(cypher_api.app, raise_server_exceptions=True).post("/api/cypher", json={"query": q})
    if resp.status_code != 200:
        raise ValueError(resp.text)
    body = resp.json()
    return body["columns"], body["rows"], "columns"


def _via_graphql(eng, q):
    from iris_vector_graph.gql.resolvers import resolve_cypher

    result = asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": eng}), q, None))
    # the JSON scalar goes over HTTP as JSON
    return list(result.columns), json.loads(json.dumps(result.rows)), "columns"


def _via_bolt(eng, q):
    from iris_vector_graph.bolt_server import (
        TAG_FAILURE, TAG_GOODBYE, TAG_HELLO, TAG_PULL, TAG_RECORD, TAG_RUN, TAG_SUCCESS, BoltSession, PackStream,
        decode_messages, encode_message,
    )

    def msg(tag, *fields):
        return encode_message(bytes([0xB0 | len(fields), tag]) + b"".join(PackStream.pack(f) for f in fields))

    ws = AsyncMock()
    ws.receive_bytes = AsyncMock(side_effect=[
        b"\x60\x60\xb0\x17" + struct.pack(">IIII", 0x0404, 0, 0, 0),
        msg(TAG_HELLO, {"user_agent": "t"}), msg(TAG_RUN, q, {}, {}), msg(TAG_PULL, {"n": -1}), msg(TAG_GOODBYE),
        Exception("end"),
    ])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    asyncio.run(BoltSession(ws, lambda: eng).run())
    fields, records = None, []
    for sent in ws.send_bytes.call_args_list[1:]:
        for m in decode_messages(sent[0][0]):
            body = PackStream.unpack(m, 2)[0]
            if m[1] == TAG_FAILURE:
                raise ValueError(body.get("message"))
            if m[1] == TAG_SUCCESS and isinstance(body, dict) and "fields" in body:
                fields = body["fields"]
            elif m[1] == TAG_RECORD:
                records.append(body)
    return fields, records, "bolt"


SURFACES = {
    "cypher-native": (True, _via_cypher),
    "cypher-sql": (False, _via_cypher),
    "rest": (False, _via_rest),
    "graphql": (False, _via_graphql),
    "bolt": (False, _via_bolt),
}


def _typed(x):
    if isinstance(x, (list, tuple)):
        return ("list", [_typed(e) for e in x])
    if isinstance(x, dict):
        return ("map", {k: _typed(e) for k, e in x.items()})
    return (type(x).__name__, x)


_SCALAR = {"STRING": str, "INTEGER": int, "FLOAT": float, "BOOLEAN": bool}


def _check_type(kind, value, where):
    if value is None:
        return
    if kind == "LIST":
        assert isinstance(value, list), (where, value)
    elif kind in ("MAP", "PATH"):
        assert isinstance(value, dict), (where, value)
    elif kind in _SCALAR:
        assert type(value) is _SCALAR[kind], (where, kind, value)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_a_yielded_column_answers_its_own_type(case, surface):
    native, via = SURFACES[surface]
    eng = _engine(native, case.rules)
    with ExitStack() as stack:
        _stub_graph_reads(stack, eng)
        case.setup(stack, eng)
        if case.raises:
            with pytest.raises(Exception, match=case.raises):
                via(eng, case.query)
            return
        columns, rows, form = via(eng, case.query)
    assert len(rows) == case.n_rows, (columns, rows)
    if not rows:
        if form == "columns":
            assert columns == [c for c, _ in signature(case.proc)]
        return
    row = rows[0]
    types = dict(signature(case.proc))
    for name, want in case.want.items():
        column, _, key = name.partition(".")
        kind = types[column] if not key else path_keys(case.proc, column)[key]
        out = (case.returned or {}).get(name, name)
        if kind == "NODE" and not case.node_as_id:
            if form == "bolt":
                node = row[columns.index(out)]  # a Node: [id, labels, properties]
                assert isinstance(node, list) and node[2].get("id") == want, (columns, row)
            else:
                at = columns.index(f"{out}_id")
                assert columns[at:at + 3] == [f"{out}_id", f"{out}_labels", f"{out}_props"], columns
                assert row[at] == want, (columns, row)
            continue
        value = row[columns.index(out)]
        _check_type(kind, value, (case.id, surface, name))
        assert _typed(value) == _typed(want), (case.id, surface, name, value)
        if kind == "PATH":
            assert set(value) == set(path_keys(case.proc, column)), value


@pytest.mark.parametrize("proc", sorted(p for p, sig in PROCEDURE_SIGNATURES.items()
                                       if any(t in CONTAINER_TYPES for _, t in sig)))
def test_a_procedure_yielding_a_list_map_or_path_has_a_case_reading_it(proc):
    """The procedures whose values this change is about: each LIST, MAP or PATH column is read by some case."""
    read = {k.split(".")[0] for c in CASES if c.proc == proc for k in c.want}
    wanted = {col for col, t in signature(proc) if t in CONTAINER_TYPES}
    if proc == "dbms.functions":
        assert all(c.n_rows == 0 for c in CASES if c.proc == proc)  # it lists no function: nothing to read
        return
    assert wanted <= read, (proc, wanted - read)
