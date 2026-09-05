"""NDJSON export of a reconstruction (spec 213 FR-037, research R10).

Line shapes follow ``contracts/changeset.schema.json`` → ``ExportLine`` and are
accepted by ``IRISGraphEngine.import_graph_ndjson`` (which also still accepts the
legacy ``kind: node|edge|temporal_edge`` lines)::

    {"type":"node","id":"pump-7","labels":["Equipment"],"props":{"status":"ok"}}
    {"type":"rel","s":"pump-7","p":"FEEDS","o":"tank-2","graph":null,"quals":{"weight":"1.0"},"stmt_id":"42"}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Union

from .replay import GraphState, NodeState, StmtState


@dataclass
class ExportSummary:
    path: str
    nodes: int
    rels: int


def _iter_entities(state_or_iter: Union[GraphState, Iterable]):
    if isinstance(state_or_iter, GraphState):
        for nid, ns in sorted(state_or_iter.nodes.items()):
            yield ("node", nid, ns)
        for sid, st in sorted(
            state_or_iter.statements.items(),
            key=lambda kv: (kv[1].s, kv[1].p, kv[1].o, kv[1].graph or "", kv[0]),
        ):
            yield ("rel", sid, st)
    else:
        yield from state_or_iter


def export_ndjson(state_or_iter: Union[GraphState, Iterable], path: str) -> ExportSummary:
    nodes = rels = 0
    with open(path, "w", encoding="utf-8") as f:
        for kind, eid, ent in _iter_entities(state_or_iter):
            if kind == "node":
                ns: NodeState = ent
                line = {
                    "type": "node",
                    "id": eid,
                    "labels": sorted(ns.labels),
                    "props": dict(sorted(ns.props.items())),
                }
                nodes += 1
            else:
                st: StmtState = ent
                line = {
                    "type": "rel",
                    "s": st.s,
                    "p": st.p,
                    "o": st.o,
                    "graph": st.graph,
                    "quals": dict(sorted(st.quals.items())),
                    "stmt_id": eid,
                }
                rels += 1
            f.write(json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
    return ExportSummary(path=path, nodes=nodes, rels=rels)


__all__ = ["export_ndjson", "ExportSummary"]
