"""The spec 236 scratch-namespace world: a baseline graph and the Owners' answers."""

from __future__ import annotations

import json

PRED = "T"
SEEDS = json.dumps(["a"])


class World:
    """One scratch namespace, a baseline graph, and the Owners' answers to compare."""

    def __init__(self, scratch):
        self.scratch = scratch
        self._engine = None
        self.seen_tokens: list = []

    # -- handles ---------------------------------------------------------------
    def native(self):
        native = self.scratch.native()
        self.scratch.require_scratch(native)
        return native

    def engine(self):
        if self._engine is None:
            from iris_vector_graph import IRISGraphEngine

            self.scratch.require_scratch(self.scratch.native())
            self._engine = IRISGraphEngine(self.scratch.dbapi(), namespace="IVG236")
        return self._engine

    def renew(self):
        """Drop the engine: a sync can leave its connection in another namespace."""
        self._engine = None

    def commit(self):
        self.engine().conn.commit()

    # -- state -----------------------------------------------------------------
    def reset(self):
        self.renew()
        self.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
        e = self.engine()
        for s, o in (("a", "b"), ("b", "c"), ("c", "d")):
            e.create_edge(s, PRED, o)
        e.create_edge("x", PRED, "y", graph="g1")
        self.commit()

    def token(self) -> int:
        value = self.native().get("KG", "__version")
        return int(value) if value is not None else 0

    def answers(self, graph: str = "") -> dict:
        n = self.native()
        call = lambda cls, m, *a: json.loads(str(n.classMethodValue(cls, m, *a)) or "null")  # noqa: E731
        ranked = call("Graph.KG.PageRank", "PageRankGlobalJson", "0.85", "20")
        seeds = json.dumps(["x"]) if graph else SEEDS
        ppr = call("Graph.KG.PageRank", "RunJson", seeds, "0.85", "20", "0", "1.0", graph)
        return {
            "pagerank": sorted((r["id"], round(float(r["score"]), 9)) for r in ranked),
            "wcc": call("Graph.KG.Algorithms", "WCCJson"),
            "cdlp": call("Graph.KG.Algorithms", "CDLPJson", "10"),
            "ppr": sorted((r["id"], round(float(r["score"]), 9)) for r in ppr),
        }
