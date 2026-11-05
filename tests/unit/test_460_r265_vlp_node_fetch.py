"""4.6.0 R265: the var-length path projection fetches only the path's nodes.

`_execute_var_length_labeled_path_funcs` called `self._store.query_nodes()` (every node in the namespace) before its
targeted fetch, and dropped the answer. The call cost a scan of `nodes` per query, and inside the `try` its failure
skipped the targeted fetch too: every node of every path came back with no labels and no properties, quietly.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _engine(cursor):
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.result import IVGResult

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._arno_available = False
    eng._arno_capabilities = {}

    unscoped_scans: list = []

    def query_nodes(label_filter=None, **kw):
        if label_filter is None:
            unscoped_scans.append(kw)
            raise RuntimeError("a scan of every node: what the projection must not ask for")
        return IVGResult(columns=["node_id"], rows=[["n1"]])

    store = MagicMock()
    store.query_nodes.side_effect = query_nodes
    store.execute_bfs.return_value = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    store.conn = conn
    store._schema_prefix = "Graph_KG"
    eng._store = store
    eng._store_capabilities = {"native_sql": False}
    return eng, unscoped_scans


def test_the_path_nodes_keep_their_labels_without_a_full_scan():
    from iris_vector_graph.cypher.translator import QueryMetadata

    cursor = MagicMock()
    cursor.fetchall.return_value = [("n1", '["Person"]', None), ("n2", '["City"]', None)]
    eng, unscoped_scans = _engine(cursor)
    vl0 = {
        "source_labels": ["Person"], "target_labels": [], "types": [], "min_hops": 1, "max_hops": 2,
        "direction": "out", "source_var": "a", "target_var": "b", "source_alias": "", "target_alias": "",
        "return_path_funcs": ["length"], "path_named_var": None,
    }  # fmt: skip
    sq = MagicMock()
    sq.var_length_paths = [vl0]
    sq.sql = "SELECT a_id FROM Graph_KG.nodes a"
    sq.parameters = [[]]
    sq.query_metadata = QueryMetadata()
    sq.graph_context = ""  # the default graph (a MagicMock attribute is no graph)
    sq.column_name_map = {"a": "a", "b": "b", "l": "length(p)"}

    result = eng._execute_var_length_labeled_path_funcs(sq, {}, vl0)

    assert unscoped_scans == []
    fetches = [c.args for c in cursor.execute.call_args_list if "JSON_ARRAYAGG(label)" in c.args[0]]
    # the graph first (4.6.0 R282: the fetch reads each node in its path's graph), then the path's nodes
    assert len(fetches) == 1 and fetches[0][1][0] == "" and sorted(fetches[0][1][1:]) == ["n1", "n2"]
    flat = repr(result.rows)
    assert "City" in flat and "Person" in flat, result.rows
