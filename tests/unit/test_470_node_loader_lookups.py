"""4.7.0 row 2: the server node loader's per-row lookups read the node's own rows, not every row of a label or key.

Graph.KG.EdgeScan.BulkIngestNodesSQL checks each label and property row before it inserts it. In a new namespace (the
tables' statistics taken near empty) the optimizer answered `... WHERE graph_id = '' AND s = :id AND "key" = :key`
from idx_props_key, every node holding the key, and the label check from idx_labels_label, every node of the label
(measured with EXPLAIN on 2026.3): each 1,000-node chunk took longer than the last, and 10,000 label-only nodes took
21.9 s where repeated loads took 1 s (R089's `id` row on every node doubled it). `%IGNOREINDEX` takes those two out
of the plan; the answers are in tests/e2e/test_470_node_load_new_namespace_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "EdgeScan.cls"


def _method(name: str) -> str:
    text = SRC.read_text()
    start = text.index(f"ClassMethod {name}(")
    return text[start:text.index("\n}\n", start)]


def test_each_lookup_ignores_the_non_selective_index():
    body = _method("BulkIngestNodesSQL")
    lookups = re.findall(r"&sql\(SELECT 1 INTO :\w+ FROM ([^)]*?) WHERE", body)
    assert lookups == [
        "%IGNOREINDEX Graph_KG.rdf_labels.idx_labels_label Graph_KG.rdf_labels",
        "%IGNOREINDEX Graph_KG.rdf_props.idx_props_key Graph_KG.rdf_props",
    ], lookups
