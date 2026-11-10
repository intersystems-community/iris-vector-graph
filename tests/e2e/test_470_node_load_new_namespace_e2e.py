"""4.7.0 row 2 (python-devx), live: a node load into a new namespace takes time in proportion to its size.

python-devx measured 74.7 s for 10,000 nodes and 29,992 edges on 4.6.2 against 41.7 s on 4.4.1. Their script
(dxlab/measure/measure_ivg.py) on ivg-iris-enterprise in new namespaces, interleaved: 16.5 to 21.4 s on 4.4.1,
37.8 to 42.8 s on release-4.7.0 at 9995d0d7. Repeated loads into a used namespace were 5 to 9 s on both, so the gap
was in the first load. The server node loader (Graph.KG.EdgeScan.BulkIngestNodesSQL) checks each label and property
row before inserting it, and on near-empty statistics those checks read idx_labels_label and idx_props_key: every
row of the label or key, more at each chunk. On 9995d0d7 the ten 1,000-node chunks took 0.5, 0.7 ... 4.2 s (21.9 s);
with the index hints they take about 0.1 s each (1.4 s), and the same script's 10,000-node load takes 9.8 to 10.4 s
against 4.4.1's 16.7 to 17.0 s.

Known answers: 10,000 nodes, each with the label and the `id` property (R089), and no chunk of the load more than
3x the time of the second (the first chunk carries one-time setup). Where: `IVG_LOAD_NS`, a namespace with no data
yet (`make_scratch_namespace.py --no-install`), on `IVG_LOAD_PORT` (default 31972).
"""

from __future__ import annotations

import json
import os
import time

import pytest

PORT = int(os.environ.get("IVG_LOAD_PORT", "31972"))
NS = os.environ.get("IVG_LOAD_NS", "")

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not NS, reason="IVG_LOAD_NS must name a new namespace with no IVG data")]

N = 10000


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
        assert max(chunk_s[1:]) <= 3 * chunk_s[1], json.dumps([round(s, 2) for s in chunk_s])
        cur.execute(f"SELECT COUNT(*) FROM {engine._ivg_schema}.rdf_labels WHERE label = 'T470Load'")
        assert cur.fetchone()[0] == N
        cur.execute(f"SELECT COUNT(*) FROM {engine._ivg_schema}.rdf_props WHERE \"key\" = 'id' AND val = s")
        assert cur.fetchone()[0] == N
    finally:
        conn.close()
