#!/usr/bin/env python3
"""Freeze a snapshot written by an older iris-vector-graph release.

The upgrade tests need an artifact that a *previous* version of this library
actually produced. Hand-authoring one only encodes what I believe the old format
was; running the old code encodes what it is. So this script is run under the old
package, from a git worktree of its tag, against a scratch namespace holding that
tag's compiled ObjectScript:

    git worktree add /tmp/ivg-2.20.0 v2.20.0
    docker cp /tmp/ivg-2.20.0/iris_src/src/. <container>:/tmp/src220/
    docker exec <container> /usr/irissys/bin/irispython -c \
      "import iris; iris.cls('%SYSTEM.Process').SetNamespace('IVGFIX'); \
       iris.cls('%SYSTEM.OBJ').LoadDir('/tmp/src220','ck',None,1)"
    PYTHONPATH=/tmp/ivg-2.20.0 python scripts/fixtures/generate_old_snapshot.py \
        --tag v2.20.0 --namespace IVGFIX \
        --out tests/fixtures/snapshots/ivg-2.20.0

Nothing here may import the current package: the whole point is that every
`iris_vector_graph` name resolves to the old worktree.

Three files land next to each other:

  `.zip`             the archive the old release's own `save_snapshot` wrote
  `.globals.ndjson`  every ^KG/^NKG node in that release's on-disk layout
  `.expected.json`   a manifest naming what was seeded and what each file holds

The manifest is what the tests assert against, so a test says "this node was in
the old database" rather than re-deriving the seed from the artifact it is meant
to be checking. The two data files cover the two halves of an upgrade: restoring
an archive onto a new install, and migrating globals in place.

The seed is deliberately small and fixed — six nodes, four structural edges, one
named graph, three temporal edges, two embeddings carrying metadata — because the
fixture is committed. It is chosen to cross every shape boundary the upgrade path
touches: node identity, the `graph_id` spelling, the temporal globals, and the
embedding `metadata` column.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Fixed seed. Every value is literal so two runs of the same tag differ only in
# the snapshot's own created_ts/run_id.
NODES = [
    ("n1", ["Person"], {"name": "Ada", "role": "author"}),
    ("n2", ["Person"], {"name": "Bes", "role": "reviewer"}),
    ("n3", ["Person"], {"name": "Cyd"}),
    ("n4", ["Paper"], {"title": "On Upgrades"}),
    ("n5", ["Person"], {"name": "Dov"}),
    ("n6", ["Person"], {"name": "Eve"}),
]

DEFAULT_GRAPH_EDGES = [
    ("n1", "knows", "n2", 1.0),
    ("n2", "knows", "n3", 1.0),
    ("n3", "cites", "n4", 2.5),
]

NAMED_GRAPH = "acme"
NAMED_GRAPH_EDGES = [("n5", "knows", "n6", 1.0)]

# Timestamps are fixed and 300s apart so they land in three distinct 300-second
# buckets — the bucket tree is part of what a temporal migration has to move.
TEMPORAL_EDGES = [
    ("n1", "calls", "n2", 1700000000, 1.5),
    ("n1", "calls", "n3", 1700000300, 2.0),
    ("n2", "calls", "n3", 1700000600, 0.5),
]

EMBEDDING_DIM = 8
EMBEDDINGS = [
    ("n1", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], {"src": "fixture", "seq": "1"}),
    ("n2", [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1], {"src": "fixture", "seq": "2"}),
]

# The `.zip` an old release writes is not a full picture of its own database, so
# the in-place upgrade path — the one consumers actually take — needs the globals
# captured separately, in their old on-disk layout.
#
# Two independent reasons the archive is short:
#
#   1. v2.20's GLOBALS_EXPORT names only ^KG("out") and ^KG("in"), so every
#      temporal edge and every degree/label cache is simply not listed.
#   2. Its walker seeds $Order with `0`, and `0` is a subscript that collates
#      after `""` — so the seed skips it. Since spec-214 made the default graph
#      key the integer `0`, v2.20 cannot export the default graph *at all*. The
#      archive above holds four structural edges' worth of tables and exactly the
#      one named-graph edge in ^KG("out").
#
# Hence a correct walker here, seeded with `""`. Using the old release's own
# writer would reproduce its blind spot, and the fixture's job is to record the
# database as it stood, not to re-tell the export bug.
CAPTURED_GLOBALS = ["^KG", "^NKG"]


RESET_TABLES = [
    "Graph_KG.rdf_edges",
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
    "Graph_KG.rdf_reifications",
    "Graph_KG.kg_NodeEmbeddings",
    "Graph_KG.kg_EdgeEmbeddings",
    "Graph_KG.nodes",
]

# Data globals only. The scratch namespace keeps its compiled classes in the same
# database, so a blanket kill would delete the very ObjectScript this run needs.
RESET_GLOBALS = ["^KG", "^NKG", "^ArnoKG", "^EmbedQueue", "^IVG.Ledger"]


def _reset(engine) -> None:
    """Empty the seed stores so a re-run produces the same archive, not a bigger one."""
    cursor = engine.conn.cursor()
    for table in RESET_TABLES:
        try:
            cursor.execute(f"DELETE FROM {table}")
        except Exception as ex:
            print(f"reset: {table}: {ex}")
    engine.conn.commit()
    iris_obj = engine._iris_obj()
    for gname in RESET_GLOBALS:
        try:
            iris_obj.kill(gname)
        except Exception as ex:
            print(f"reset: {gname}: {ex}")


def _seed(engine) -> dict:
    """Write the seed, returning what actually landed.

    Old releases differ in what they accept — `graph=` on `create_node` arrived
    with spec 214, `mode=` on the temporal writers later still. Anything the old
    engine refuses is recorded as skipped rather than faked, so a manifest never
    claims data the archive does not contain.
    """
    written = {"nodes": [], "edges": [], "named_edges": [], "temporal": [], "embeddings": []}

    for node_id, labels, props in NODES:
        engine.create_node(node_id=node_id, labels=labels, properties=props)
        written["nodes"].append({"node_id": node_id, "labels": labels, "properties": props})

    for s, p, o, w in DEFAULT_GRAPH_EDGES:
        engine.create_edge(source_id=s, predicate=p, target_id=o, weight=w)
        written["edges"].append({"s": s, "p": p, "o_id": o, "weight": w})

    for s, p, o, w in NAMED_GRAPH_EDGES:
        try:
            engine.create_edge(
                source_id=s, predicate=p, target_id=o, weight=w, graph=NAMED_GRAPH
            )
            written["named_edges"].append(
                {"s": s, "p": p, "o_id": o, "weight": w, "graph_id": NAMED_GRAPH}
            )
        except TypeError as ex:
            written.setdefault("skipped", []).append(f"named edge {s}-{p}->{o}: {ex}")

    for s, p, o, ts, w in TEMPORAL_EDGES:
        engine.create_edge_temporal(s, p, o, timestamp=ts, weight=w)
        written["temporal"].append(
            {"source": s, "predicate": p, "target": o, "timestamp": ts, "weight": w}
        )

    for node_id, vec, meta in EMBEDDINGS:
        try:
            engine.store_embedding(node_id, vec, metadata=meta)
            written["embeddings"].append(
                {"node_id": node_id, "embedding": vec, "metadata": meta}
            )
        except Exception as ex:  # an old release may not have the metadata column
            written.setdefault("skipped", []).append(f"embedding {node_id}: {ex}")

    return written


def _walk(iris_obj, gname: str, subs: list, out: list) -> None:
    """Every node under ``^gname(*subs)``, depth first.

    ``""`` is the only start value that collates before every subscript, so it is
    the one `$Order`/`nextSubscript` takes to mean "from the beginning". Seeding
    with `0` asks for the subscript *after* `0` and can therefore never emit `0`
    itself — the exact defect that hides the default graph.
    """
    cur = ""
    while True:
        nxt = iris_obj.nextSubscript(False, gname, *subs, cur)
        if nxt is None or nxt == "":
            break
        child = subs + [nxt]
        val = iris_obj.get(gname, *child)
        if val is not None:
            out.append({"global": gname, "k": child, "v": str(val)})
        _walk(iris_obj, gname, child, out)
        cur = nxt


def _capture_globals(engine, path: str) -> dict:
    """Dump the globals in their old on-disk layout, subscript for subscript."""
    entries: list = []
    iris_obj = engine._iris_obj()
    for gname in CAPTURED_GLOBALS:
        _walk(iris_obj, gname, [], entries)

    counts: dict = {}
    for entry in entries:
        key = f"{entry['global']}(\"{entry['k'][0]}\")" if entry["k"] else entry["global"]
        counts[key] = counts.get(key, 0) + 1

    with open(path, "w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    print(f"wrote {path}: {len(entries)} node(s)")
    return counts


def _record_statements(conn, log: list) -> None:
    """Make every cursor this connection hands out append its SQL to ``log``.

    Patched onto the connection rather than wrapped around it: the old engine also
    hands the same connection to the Native API (`iris.createIRIS`), which wants the
    real driver object, not a proxy of it.

    Used to freeze an old release's *shape* as well as its content. A migration test
    has to start from the tables the old release declared, and the only unarguable
    source for those is the statements the old release itself executed — reading the
    catalog back and re-rendering CREATE TABLE from what I find there would encode my
    reading of the catalog, which is the thing under test.
    """
    original = conn.cursor

    def cursor(*args, **kwargs):
        return _RecordingCursor(original(*args, **kwargs), log)

    conn.cursor = cursor


class _RecordingCursor:
    def __init__(self, cursor, log: list):
        self._cursor = cursor
        self._log = log

    def execute(self, sql, *args, **kwargs):
        self._log.append(sql)
        return self._cursor.execute(sql, *args, **kwargs)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="the release that wrote this archive")
    ap.add_argument("--namespace", required=True, help="scratch namespace holding that tag's classes")
    ap.add_argument("--out", required=True, help="path stem; .zip and .expected.json are written")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=31972)
    ap.add_argument("--user", default="_SYSTEM")
    ap.add_argument("--password", default="SYS")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="empty the seed stores first, so a re-run reproduces the archive",
    )
    ap.add_argument(
        "--ddl",
        action="store_true",
        help="also freeze the CREATE/ALTER statements this release's initialize_schema "
        "issues, so a test can rebuild its table shapes (writes .ddl.json)",
    )
    args = ap.parse_args()

    import iris
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(iris_vector_graph.__file__)))
    print(f"old package: {iris_vector_graph.__file__}")
    if pkg_dir == os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
        print(
            "refusing to run: iris_vector_graph resolved to the current checkout, "
            "so this would freeze today's format under an old tag's name. "
            "Set PYTHONPATH to the old worktree.",
            file=sys.stderr,
        )
        return 2

    conn = iris.connect(args.host, args.port, args.namespace, args.user, args.password)
    issued: list = []
    if args.ddl:
        _record_statements(conn, issued)
    engine = IRISGraphEngine(
        conn, embedding_dimension=EMBEDDING_DIM, namespace=args.namespace
    )
    engine.initialize_schema()
    ddl = None
    if args.ddl:
        # Only the shape-declaring statements. The rest of what initialize_schema runs
        # is probes and procedure bodies, which a shape fixture has no use for — and a
        # statement is kept even when this run's namespace already had the table, since
        # what is being frozen is what the old release *declares*, not what it managed
        # to create against a database that already existed.
        ddl = [
            sql
            for sql in issued
            if sql.lstrip().upper().startswith(("CREATE TABLE", "ALTER TABLE"))
        ]
        ddl_path = f"{args.out}.ddl.json"
        os.makedirs(os.path.dirname(ddl_path) or ".", exist_ok=True)
        with open(ddl_path, "w") as fh:
            json.dump(
                {"tag": args.tag, "embedding_dimension": EMBEDDING_DIM, "statements": ddl},
                fh,
                indent=2,
            )
            fh.write("\n")
        print(f"wrote {ddl_path}: {len(ddl)} statement(s)")

    if args.reset:
        _reset(engine)

    written = _seed(engine)

    # A real consumer database has its adjacency built; an unsynced one would make
    # the fixture's ^KG("out") thinner than anything in the field.
    try:
        engine.sync()
    except Exception as ex:
        written.setdefault("skipped", []).append(f"sync(): {ex}")

    zip_path = f"{args.out}.zip"
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    result = engine.save_snapshot(zip_path)
    print(f"wrote {zip_path}: {result}")

    captured = _capture_globals(engine, f"{args.out}.globals.ndjson")

    manifest = {
        "tag": args.tag,
        "namespace": args.namespace,
        "embedding_dimension": EMBEDDING_DIM,
        "named_graph": NAMED_GRAPH,
        "save_snapshot_result": result,
        "captured_globals": captured,
        "seeded": written,
    }
    if ddl is not None:
        manifest["ddl_statements"] = len(ddl)
    manifest_path = f"{args.out}.expected.json"
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
