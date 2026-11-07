#!/usr/bin/env python3
"""One side of the spec 262 speed gate (`schema_gate.py`): the IVG on PYTHONPATH, against one namespace.

Started by the gate with PYTHONPATH on the side's code (this checkout, or the `a42db224` worktree), so it uses only
the engine API both code states have. It answers one JSON line per request line on stdin:

    {"op": "setup", "paths": [...]}       -> {"ok": true, "version": "<code> <git rev>"}   seeds the read graph
    {"op": "run", "path": p, "run": i}    -> {"ok": true, "path": p, "seconds": s}         untimed set-up, timed body,
                                              or {"ok": false, "error": "..."}             untimed clean-up
    {"op": "quit"}

The namespace must be set up for the side's code and schema first (the base: `build_release_namespace.py a42db224
IVG262BASE`; the current build: `initialize_schema()`, with `schema_prefix="IVGALT"` for the alt side). The worker
writes only nodes and edges of its own labels and graphs (Gate*, gate_*) and the default graph's temporal edges of
predicate GATE_T*, and removes them after each run; the read graph (GateR) stays for the next run.

`nkg_first_read_after_erase` (R275, R268): the first `^NKG` read after a named graph's erase, its rebuild included.
The current build's readers rebuild `^NKG` themselves (`EnsureNKG`); `a42db224`'s do not, and its read after an erase
answers the erased nodes until a rebuild. So the base side times `BuildNKG` and the read, the current sides the read:
on each side, what an erase costs the first correct read.

Container and port: IVG_PORT (31972); login _SYSTEM / SYS.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback

SCALES = {
    # rows: row-at-a-time writes; bulk: one bulk call; read_*: the read graph; queries: reads per timed run
    "small": dict(rows=20, bulk=200, read_nodes=500, read_edges=1500, queries=20, erase=200, temporal=50, retire=50,
                  ledger=20, nkg=200),
    "full": dict(rows=500, bulk=10000, read_nodes=20000, read_edges=60000, queries=200, erase=10000, temporal=1000,
                 retire=1000, ledger=500, nkg=10000),
}
OLD_TS, LIVE_TS = 1_500_000_000, 1_700_000_000


class Side:
    def __init__(self, args):
        import iris

        from iris_vector_graph import IRISGraphEngine

        self.side, self.ns, self.schema = args.side, args.namespace, args.schema
        self.n = SCALES[args.scale]
        self.conn = iris.connect("localhost", int(os.environ.get("IVG_PORT", "31972")), self.ns, "_SYSTEM", "SYS")
        self.native = iris.createIRIS(self.conn)
        self.e = IRISGraphEngine(self.conn, schema_prefix=self.schema, namespace=self.ns)
        self.base = args.side == "base"

    # ---- helpers
    def cy(self, q, params=None):
        r = self.e.execute_cypher(q, params or {})
        if getattr(r, "error", None):
            raise RuntimeError(f"{q[:80]}: {r.error}")
        return r.rows

    #: each label's id prefix: clean-up deletes by prefix (Eraser.EraseNodes, in both code states), since
    #: a42db224's Cypher DETACH DELETE of a few hundred nodes fails to prepare (SQLCODE -202)
    PREFIX = {"GateR": "gr:", "GateW": "gw:", "GateB": "gb:", "GateL": "gl:", "GateT": "gt:", "GateE": "ge:",
              "GateN": "gn:"}

    def drop_label(self, label):
        self.e.delete_nodes_by_prefix(self.PREFIX[label])

    def graph_nodes(self, prefix, label, graph, run, k):
        """`k` nodes in a named graph, a row at a time (untimed): a42db224's bulk loader puts a node dict's "graph" in
        the default graph once the batch is large, so the two code states would erase different graphs."""
        for i in range(k):
            self.e.create_node(f"{prefix}:{run}:{i}", labels=[label], graph=graph)

    def settle(self):
        """Before a timed read: ^NKG and ^KG current (a clean-up's delete may have dropped or invalidated ^NKG, and the
        current build's readers would rebuild it inside the timing), untimed."""
        self.e.sync()

    def version(self):
        import iris_vector_graph

        code = os.path.dirname(os.path.dirname(os.path.abspath(iris_vector_graph.__file__)))
        rev = subprocess.run(["git", "-C", code, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        return f"{code} {rev.stdout.strip() or '?'}"

    def setup(self, paths):
        n = self.n
        for label in self.PREFIX:
            self.drop_label(label)
        for g in ("gate_e", "gate_n"):
            self.e.erase_graph(g)
        self.e.purge_before(LIVE_TS + 10_000_000)
        self.e.bulk_create_nodes([{"id": f"gr:{i}", "labels": ["GateR"], "properties": {"name": f"r{i}", "i": i}}
                                  for i in range(n["read_nodes"])])
        rn = n["read_nodes"]
        self.e.bulk_create_edges([{"source_id": f"gr:{i % rn}", "predicate": "GATE_R",
                                   "target_id": f"gr:{(i * 7 + 1) % rn}"} for i in range(n["read_edges"])])
        self.e.sync()
        self.tune()
        return {"ok": True, "version": self.version()}

    #: tuned on every side once the read graph is in: a plan follows the statistics, and a table setup tuned while
    #: empty plans an edge insert's existence probe as an index intersection (6 ms against 0.4 ms an edge), so
    #: without this the sides compare their statistics' histories, not their code
    TUNED = ("nodes", "rdf_edges", "rdf_labels", "rdf_props")

    def tune(self):
        cur = self.conn.cursor()
        for table in self.TUNED:
            cur.execute(f"TUNE TABLE {self.schema}.{table}")
        self.conn.commit()

    # ---- the paths: each returns (the timed body's seconds, an answer the gate compares across the sides)
    def timed(self, fn):
        t = time.perf_counter()
        result = fn()
        return time.perf_counter() - t, result

    def count(self, q, params=None):
        return int(self.cy(q, params)[0][0])

    def node_write_row(self, run):
        k = self.n["rows"]
        try:
            secs, _ = self.timed(lambda: [self.e.create_node(f"gw:{run}:{i}", labels=["GateW"], properties={"i": i})
                                          for i in range(k)])
            return secs, self.count("MATCH (n:GateW) RETURN count(n)")
        finally:
            self.drop_label("GateW")

    def edge_write_row(self, run):
        k = self.n["rows"]
        self.e.bulk_create_nodes([{"id": f"gw:{run}:{i}", "labels": ["GateW"]} for i in range(k + 1)])
        try:
            secs, _ = self.timed(lambda: [self.e.create_edge(f"gw:{run}:{i}", "GATE_W", f"gw:{run}:{i + 1}")
                                          for i in range(k)])
            return secs, self.count("MATCH (:GateW)-[r:GATE_W]->(:GateW) RETURN count(r)")
        finally:
            self.drop_label("GateW")

    def node_write_bulk(self, run):
        nodes = [{"id": f"gb:{run}:{i}", "labels": ["GateB"], "properties": {"i": i}} for i in range(self.n["bulk"])]
        try:
            secs, _ = self.timed(lambda: self.e.bulk_create_nodes(nodes))
            return secs, self.count("MATCH (n:GateB) RETURN count(n)")
        finally:
            self.drop_label("GateB")

    def edge_write_bulk(self, run):
        k = self.n["bulk"]
        self.e.bulk_create_nodes([{"id": f"gb:{run}:{i}", "labels": ["GateB"]} for i in range(k + 1)])
        edges = [{"source_id": f"gb:{run}:{i}", "predicate": "GATE_B", "target_id": f"gb:{run}:{i + 1}"}
                 for i in range(k)]
        try:
            secs, _ = self.timed(lambda: self.e.bulk_create_edges(edges))
            return secs, self.count("MATCH (:GateB)-[r:GATE_B]->(:GateB) RETURN count(r)")
        finally:
            self.drop_label("GateB")

    def bare_node_retire(self, run):
        k = self.n["retire"]
        for i in range(k):
            self.e.create_edge_temporal(f"gt:{run}:src", "GATE_T_OLD", f"gt:{run}:{i}", timestamp=OLD_TS + i)
        secs, _ = self.timed(lambda: self.e.purge_before(OLD_TS + k + 1, retire_bare_nodes=True))
        left = self.count("MATCH (n) WHERE n.id STARTS WITH $p RETURN count(n)", {"p": f"gt:{run}:"})
        self.drop_label("GateT")
        return secs, left

    def graph_erase(self, run):
        k = self.n["erase"]
        self.graph_nodes("ge", "GateE", "gate_e", run, k)
        self.e.bulk_create_edges([{"source_id": f"ge:{run}:{i}", "predicate": "GATE_E",
                                   "target_id": f"ge:{run}:{(i + 1) % k}"} for i in range(k)], graph="gate_e")
        seeded = self.count("USE GRAPH 'gate_e' MATCH (n:GateE) RETURN count(n)")
        secs, _ = self.timed(lambda: self.e.erase_graph("gate_e"))
        return secs, [seeded, self.count("USE GRAPH 'gate_e' MATCH (n:GateE) RETURN count(n)")]

    def temporal_edge_write(self, run):
        k = self.n["temporal"]
        t0 = LIVE_TS + run * k
        try:
            secs, _ = self.timed(lambda: [self.e.create_edge_temporal(f"gt:{run}:a", "GATE_T", f"gt:{run}:{i % 50}",
                                                                      timestamp=t0 + i, weight=1.0)
                                          for i in range(k)])
            return secs, len(self.e.get_edges_in_window(f"gt:{run}:a", "GATE_T", t0, t0 + k))
        finally:
            self.e.purge_before(LIVE_TS + 10_000_000)
            self.drop_label("GateT")

    def _reads(self, query, answer):
        self.settle()
        rn, q = self.n["read_nodes"], self.n["queries"]
        secs, rows = self.timed(lambda: [self.cy(query, {"id": f"gr:{(i * 37) % rn}"}) for i in range(q)])
        return secs, answer(rows)

    def cypher_one_hop(self, run):
        return self._reads("MATCH (a:GateR {id: $id})-[:GATE_R]->(b) RETURN b.name",
                           lambda rows: sum(len(r) for r in rows))

    def cypher_var_length(self, run):
        return self._reads("MATCH (a:GateR {id: $id})-[:GATE_R*1..3]->(b) RETURN count(DISTINCT b)",
                           lambda rows: sum(int(r[0][0]) for r in rows))

    def pagerank(self, run):
        # a repeat is served from the algorithm cache: each run computes, as a first call does
        self.settle()
        self.native.kill("^IVG.AlgoCache")
        secs, scores = self.timed(lambda: self.e.kg_PAGERANK())
        return secs, len(scores or [])

    def ledger_commit(self, run):
        from iris_vector_graph.ledger import Changeset

        k = self.n["ledger"]
        cs = Changeset(actor="gate", actor_type="system")
        for i in range(k):
            cs.create_node(f"gl:{run}:{i}", labels=["GateL"])
        for i in range(k - 1):
            cs.create_relationship(f"gl:{run}:{i}", "GATE_L", f"gl:{run}:{i + 1}")
        self.e.ledger.enable()
        try:
            secs, _ = self.timed(lambda: self.e.ledger.commit(cs))
            return secs, self.count("MATCH (:GateL)-[r:GATE_L]->() RETURN count(r)")
        finally:
            self.e.ledger.disable()
            self.drop_label("GateL")

    def collect_node_property(self, run):
        self.settle()
        secs, rows = self.timed(lambda: self.cy("MATCH (n:GateR) RETURN collect(n.name) AS names"))
        names = rows[0][0]
        return secs, len(json.loads(names) if isinstance(names, str) else names)

    def nkg_first_read_after_erase(self, run):
        k = self.n["nkg"]
        self.graph_nodes("gn", "GateN", "gate_n", run, k)
        self.e.bulk_create_edges([{"source_id": f"gn:{run}:{i}", "predicate": "GATE_N",
                                   "target_id": f"gn:{run}:{(i + 1) % k}"} for i in range(k)], graph="gate_n")
        self.e.sync()
        self.e.erase_graph("gate_n")

        def read():
            if self.base:
                self.native.classMethodValue("Graph.KG.TraversalBuild", "BuildNKG")
            out = str(self.native.classMethodValue("Graph.KG.Communities", "SCCJson", 100000))
            if out.startswith("ERROR"):
                raise RuntimeError(out)
            return out

        secs, out = self.timed(read)
        rows = json.loads(out[3:] if out.startswith("OK:") else out)
        # the erased graph's ring is gone: no component holds one of its nodes
        return secs, sum(1 for r in rows if str(r.get("id", r.get("node", ""))).startswith("gn:"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--side", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--scale", default="full", choices=sorted(SCALES))
    args, _ = p.parse_known_args()
    # the protocol owns stdout: anything IVG prints goes to stderr instead
    out, sys.stdout = sys.stdout, sys.stderr
    side = None
    for line in sys.stdin:
        req = json.loads(line)
        try:
            if req["op"] == "quit":
                break
            if req["op"] == "setup":
                side = Side(args)
                answer = side.setup(req.get("paths") or [])
            elif req["op"] == "run":
                seconds, result = getattr(side, req["path"])(req.get("run", 0))
                answer = {"ok": True, "path": req["path"], "seconds": seconds, "answer": result}
            else:
                answer = {"ok": False, "error": f"unknown op {req['op']!r}"}
        except Exception as e:  # reported to the gate, which records the run as failed (never as a timing)
            answer = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
        print(json.dumps(answer), file=out, flush=True)


if __name__ == "__main__":
    main()
