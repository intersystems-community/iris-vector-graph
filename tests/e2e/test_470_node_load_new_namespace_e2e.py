"""4.7.0 row 2 (python-devx), live: a node load into a new namespace takes time in proportion to its size.

python-devx measured 74.7 s for 10,000 nodes and 29,992 edges on 4.6.2 against 41.7 s on 4.4.1. Their script
(dxlab/measure/measure_ivg.py) on ivg-iris-enterprise in new namespaces, interleaved: 16.5 to 21.4 s on 4.4.1,
37.8 to 42.8 s on release-4.7.0 at 9995d0d7. Repeated loads into a used namespace were 5 to 9 s on both, so the gap
was in the first load. The server node loader (Graph.KG.EdgeScan.BulkIngestNodesSQL) checks each label and property
row before inserting it, and on near-empty statistics those checks read idx_labels_label and idx_props_key: every
row of the label or key, more at each chunk. On 9995d0d7 the ten 1,000-node chunks took 0.5, 0.7 ... 4.2 s (21.9 s);
with the index hints they take about 0.1 s each (1.4 s), and the same script's 10,000-node load takes 9.8 to 10.4 s
against 4.4.1's 16.7 to 17.0 s. create_node, store_node, the bulk SQL templates and the Cypher writers' guards had
the same plan: `UNWIND $rows AS r CREATE (:L {uwk: r, name: 'x'})`, 1,000 rows a statement, took 2.3, 2.44 ... 8.35 s
for the ten statements on 9995d0d7, and 2.9 falling to 1.5 s with the hints.

Known answers: on the freshly set-up namespace, EXPLAIN of each per-row guard (the loader's two lookups, read out of
Graph.KG.EdgeScan's own source; the Python writers' guards, built by schema.guard_from; the Cypher writers' guards,
from translating CREATE, UNWIND CREATE, SET, MERGE and USE GRAPH CREATE) names idx_props_s / idx_labels_s, not the
index that leads with the key or the label (9995d0d7: idx_labels_label for the first). Then, ratios, robust to a
busy host: the tenth UNWIND CREATE statement takes under 2x the first (9995d0d7: 3.6x); 10,000 nodes, each with the
label and the `id` property (R089), load with the last 1,000-node chunk under 2x the first (9995d0d7: 11x). Where: `IVG_LOAD_NS`, a namespace with no data yet (`make_scratch_namespace.py --no-install`),
on `IVG_LOAD_PORT` (default 31972).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

PORT = int(os.environ.get("IVG_LOAD_PORT", "31972"))
NS = os.environ.get("IVG_LOAD_NS", "")

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not NS, reason="IVG_LOAD_NS must name a new namespace with no IVG data")]

N = 10000
EDGESCAN = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "EdgeScan.cls"


def _first_read(cur, sql):
    cur.execute("EXPLAIN " + sql)
    text = re.sub(r"\s+", " ", " ".join(str(r[0]) for r in cur.fetchall()))
    return re.findall(r"Read index map (\S+?),", text)[:1]


def _loader_lookups(schema):
    """The node loader's two embedded lookups, as dynamic SQL: host variables as markers, the schema as deployed."""
    body = EDGESCAN.read_text().split("ClassMethod BulkIngestNodesSQL", 1)[1].split("\n}\n", 1)[0]
    found = re.findall(r"&sql\((SELECT 1) INTO :\w+ (FROM .*?)\)\n", body)
    return [re.sub(r":\w+", "?", f"{a} {b}").replace("Graph_KG.", f"{schema}.") for a, b in found]


def _cypher_guards(schema):
    """The Cypher writers' per-node guards (tests/unit/test_470_node_loader_lookups.py), the outer row's id and graph
    as markers."""
    from tests.unit.test_470_node_loader_lookups import WRITES, cypher_row_guards

    out = []
    for q in WRITES:
        for frm, where in cypher_row_guards(q):
            where = re.sub(r"__GRAPH_\w+?__|\b[\w.]*\.node_id\b", "?", where)
            out.append(f"SELECT 1 FROM {frm} WHERE {where}".replace("Graph_KG.", f"{schema}."))
    return out


def _timed_unwind(engine, label, n_chunks=10, size=1000):
    out = []
    for c in range(n_chunks):
        t0 = time.perf_counter()
        engine.execute_cypher(f"UNWIND $rows AS r CREATE (:{label} {{uwk: r, name: 'x'}})",
                              {"rows": [f"{label}:{c}:{i}" for i in range(size)]}, raise_on_error=True)
        out.append(time.perf_counter() - t0)
    return out


def test_a_new_namespace_loads_10k_nodes_in_flat_chunks():
    import iris

    import iris_vector_graph.schema as schema_mod
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect("127.0.0.1", PORT, NS, "_SYSTEM", "SYS")
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=8)
        engine.initialize_schema()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {engine._ivg_schema}.nodes")
        assert cur.fetchone()[0] == 0, "the namespace holds nodes already: this case proves nothing"
        assert engine.capabilities.objectscript_deployed, "no server loader: this case proves nothing"
        from iris_vector_graph.schema import guard_from

        t_props, t_labels = f"{engine._ivg_schema}.rdf_props", f"{engine._ivg_schema}.rdf_labels"
        lookups = _loader_lookups(engine._ivg_schema)
        assert len(lookups) == 2, lookups
        guards = lookups + _cypher_guards(engine._ivg_schema) + [
            f'SELECT 1 FROM {guard_from(t_props)} WHERE graph_id = ? AND s = ? AND "key" = ?',
            f'SELECT 1 FROM {guard_from(t_props)} WHERE s = ? AND "key" = ?',
            f"SELECT 1 FROM {guard_from(t_labels)} WHERE s = ? AND label = ?",
        ]
        for sql in guards:
            assert _first_read(cur, sql) in ([f"{t_props}.idx_props_s"], [f"{t_labels}.idx_labels_s"]), sql
        unwind_s = _timed_unwind(engine, "T470Unwind")
        assert unwind_s[-1] < 2 * unwind_s[0], json.dumps([round(s, 2) for s in unwind_s])
        chunk_s = []
        real = schema_mod._call_classmethod_large

        def timed(c, cls, method, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return real(c, cls, method, *args, **kwargs)
            finally:
                if method == "BulkIngestNodesSQL":
                    chunk_s.append(time.perf_counter() - t0)

        schema_mod._call_classmethod_large = timed
        try:
            ids = [f"t470load:{i}" for i in range(N)]
            assert engine.bulk_create_nodes([{"id": n, "labels": ["T470Load"]} for n in ids]) == ids
        finally:
            schema_mod._call_classmethod_large = real
        assert len(chunk_s) >= 5, chunk_s
        assert chunk_s[-1] < 2 * chunk_s[0], json.dumps([round(s, 2) for s in chunk_s])
        cur.execute(f"SELECT COUNT(*) FROM {engine._ivg_schema}.rdf_labels WHERE label = 'T470Load'")
        assert cur.fetchone()[0] == N
        cur.execute(f"SELECT COUNT(*) FROM {engine._ivg_schema}.rdf_props WHERE \"key\" = 'id' AND val = s")
        assert cur.fetchone()[0] == N
    finally:
        conn.close()
