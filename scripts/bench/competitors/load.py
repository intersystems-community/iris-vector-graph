#!/usr/bin/env python3
"""Load a dataset into Neo4j and/or Memgraph, then read it back and compare with the generator.

    python load.py --engine neo4j --dataset S
    python load.py --engine both  --dataset M --verify-only

Graph model: (:<label> {<node_prop>: id})-[:<rel_type>]->(:<label>), with a unique constraint (Neo4j) or an index
(Memgraph) on the property. For DRKG every node also carries its entity-type label and every edge its relation name
as the relationship type.

The database must be started on the dataset's own volume first (competitors.sh start <engine> <DATASET>).
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import datasets
import engines
from engines import Engine, q
from jsonl import make_record, write_record

EXACT_COMPARE_MAX_EDGES = 1_000_000


def _batches(seq: List[Any], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def node_groups(graph: datasets.Graph) -> Dict[str, List[str]]:
    """Extra label -> node ids. DRKG nodes carry their entity type; every other dataset has none."""
    if graph.name == "DRKG":
        groups: Dict[str, List[str]] = collections.defaultdict(list)
        for n in graph.nodes:
            groups[datasets.drkg_node_type(n)].append(n)
        return dict(groups)
    return {"": list(graph.nodes)}


def edge_groups(graph: datasets.Graph) -> Dict[str, List[List[str]]]:
    groups: Dict[str, List[List[str]]] = collections.defaultdict(list)
    for i, (s, o) in enumerate(graph.edges):
        groups[graph.type_of(i)].append([s, o])
    return dict(groups)


def prepare_schema(eng: Engine, graph: datasets.Graph) -> None:
    lab, prop = q(graph.label), q(graph.node_prop)
    if eng.name == "neo4j":
        eng.run(f"CREATE CONSTRAINT ivg_{graph.label}_{graph.node_prop} IF NOT EXISTS FOR (n:{lab}) REQUIRE n.{prop} IS UNIQUE")
        eng.run("CALL db.awaitIndexes(300)")
    else:
        try:
            eng.run(f"CREATE INDEX ON :{lab}({prop})")
        except Exception as exc:  # already exists on a re-run
            if "already exists" not in str(exc).lower():
                raise


def clear(eng: Engine) -> None:
    if eng.name == "neo4j":
        while eng.scalar("MATCH (n) WITH n LIMIT 20000 DETACH DELETE n RETURN count(*)"):
            pass
    else:
        eng.run("MATCH (n) DETACH DELETE n")


def load_graph(eng: Engine, graph: datasets.Graph, node_batch: int = 20_000, edge_batch: int = 20_000, log=print) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"dataset": graph.name, "engine": eng.name}
    lab, prop = q(graph.label), q(graph.node_prop)
    t0 = time.perf_counter()

    prepare_schema(eng, graph)
    stats["schema_s"] = round(time.perf_counter() - t0, 2)

    t1 = time.perf_counter()
    done = 0
    for extra, ids in node_groups(graph).items():
        labels = f"{lab}:{q(extra)}" if extra else lab
        for chunk in _batches(ids, node_batch):
            eng.run(f"UNWIND $ids AS i CREATE (n:{labels} {{{prop}: i}})", {"ids": chunk})
            done += len(chunk)
    stats["nodes_s"] = round(time.perf_counter() - t1, 2)
    log(f"  [{eng.name}] {done:,} nodes in {stats['nodes_s']}s")

    t2 = time.perf_counter()
    done = 0
    next_report = 1_000_000
    for rel, rows in edge_groups(graph).items():
        rt = q(rel)
        cypher = (
            f"UNWIND $rows AS r MATCH (a:{lab} {{{prop}: r[0]}}) MATCH (b:{lab} {{{prop}: r[1]}}) "
            f"CREATE (a)-[:{rt}]->(b)"
        )
        for chunk in _batches(rows, edge_batch):
            eng.run(cypher, {"rows": chunk})
            done += len(chunk)
            if done >= next_report:
                log(f"  [{eng.name}] {done:,} edges, {time.perf_counter() - t2:.0f}s")
                next_report += 1_000_000
    stats["edges_s"] = round(time.perf_counter() - t2, 2)
    log(f"  [{eng.name}] {done:,} edges in {stats['edges_s']}s")

    if eng.name == "memgraph":
        try:
            eng.run("CREATE SNAPSHOT")
        except Exception as exc:  # the snapshot on exit is the backstop
            log(f"  [memgraph] CREATE SNAPSHOT failed ({str(exc)[:80]}); relying on snapshot-on-exit")
    stats["total_s"] = round(time.perf_counter() - t0, 2)
    stats["nodes"], stats["edges"] = len(graph.nodes), len(graph.edges)
    return stats


def read_degrees(eng: Engine, graph: datasets.Graph):
    lab, prop = q(graph.label), q(graph.node_prop)
    out_deg = {r[0]: int(r[1]) for r in eng.run(f"MATCH (n:{lab})-[r]->(:{lab}) RETURN n.{prop}, count(r)")}
    in_deg = {r[0]: int(r[1]) for r in eng.run(f"MATCH (:{lab})-[r]->(n:{lab}) RETURN n.{prop}, count(r)")}
    nodes = [r[0] for r in eng.run(f"MATCH (n:{lab}) RETURN n.{prop}")]
    return nodes, out_deg, in_deg


def verify_graph(eng: Engine, graph: datasets.Graph, exact: Optional[bool] = None) -> Dict[str, Any]:
    """Compare node count, edge count, the degree checksum and (small graphs) the whole edge multiset."""
    lab, prop = q(graph.label), q(graph.node_prop)
    nodes_db = eng.node_count(graph.label)
    edges_db = eng.edge_count(graph.label)
    db_nodes, out_deg, in_deg = read_degrees(eng, graph)
    expect_out, expect_in = datasets.degrees(graph)
    res: Dict[str, Any] = {
        "dataset": graph.name,
        "engine": eng.name,
        "nodes_expected": len(graph.nodes),
        "nodes_db": nodes_db,
        "edges_expected": len(graph.edges),
        "edges_db": edges_db,
        "checksum_expected": datasets.checksum_from_degrees(graph.nodes, expect_out, expect_in),
        "checksum_db": datasets.checksum_from_degrees(db_nodes, out_deg, in_deg),
    }
    do_exact = exact if exact is not None else len(graph.edges) <= EXACT_COMPARE_MAX_EDGES
    if do_exact:
        rows = eng.run(f"MATCH (a:{lab})-[r]->(b:{lab}) RETURN a.{prop}, b.{prop}, type(r)")
        got = collections.Counter((r[0], r[1], r[2]) for r in rows)
        want = collections.Counter((s, o, graph.type_of(i)) for i, (s, o) in enumerate(graph.edges))
        res["edge_multiset_equal"] = got == want
    res["ok"] = (
        res["nodes_expected"] == res["nodes_db"]
        and res["edges_expected"] == res["edges_db"]
        and res["checksum_expected"] == res["checksum_db"]
        and res.get("edge_multiset_equal", True)
    )
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["neo4j", "memgraph", "both"], required=True)
    ap.add_argument("--dataset", required=True, help=f"one of: {', '.join(datasets.BUILDERS)}")
    ap.add_argument("--verify-only", action="store_true", help="do not load; read back and compare")
    ap.add_argument("--keep", action="store_true", help="do not clear the database before loading")
    ap.add_argument("--no-exact", action="store_true", help="skip the whole-edge-set comparison")
    ap.add_argument("--node-batch", type=int, default=20_000)
    ap.add_argument("--edge-batch", type=int, default=20_000)
    ap.add_argument("--out", type=Path, help="append a load record (a single value in values_ms) to this .jsonl")
    ap.add_argument("--neo4j-uri")
    ap.add_argument("--memgraph-uri")
    args = ap.parse_args(argv)

    graph = datasets.get_graph(args.dataset)
    print(json.dumps(datasets.summary(graph)))
    rc = 0
    for name in (["neo4j", "memgraph"] if args.engine == "both" else [args.engine]):
        eng = engines.connect(name, uri=args.neo4j_uri if name == "neo4j" else args.memgraph_uri)
        try:
            print(f"{name}: {eng.version()}")
            if not args.verify_only:
                if not args.keep:
                    clear(eng)
                st = load_graph(eng, graph, args.node_batch, args.edge_batch)
                print(json.dumps(st))
                if args.out:
                    write_record(
                        args.out,
                        make_record(name, "load", f"load_{graph.name}", [st["total_s"] * 1000.0], {"dataset": graph.name, **st}),
                    )
            res = verify_graph(eng, graph, exact=False if args.no_exact else None)
            print(json.dumps(res))
            if not res["ok"]:
                rc = 1
        finally:
            eng.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
