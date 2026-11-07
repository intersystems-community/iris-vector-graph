"""A module's own scratch namespace and the data its tests assert on, loaded by the module itself (4.6.0 gate).

The demo and benchmark files (test_biomedical_demo, test_fraud_demo, test_lazy_node_resolution,
test_large_output_chunked) asked the session namespace for data someone else had to load, and skipped on a fresh
container ("No labeled entities found — load sample data first"): in the gate they never ran. Each now names a
namespace variable of its own, which the E2E gate makes per run with IVG installed (tests/e2e/gate.py GROUPS), and
loads its data there in a module fixture:

- the biomedical and fraud files with the repo's documented sample loaders, `scripts.setup.load_sample_data` and
  `load_fraud_data` (examples/demo_biomedical.py names the first). The biomedical file TRUNCATEs every IVG table,
  which is why neither may run in the session namespace;
- the two Arno BFS files with a tree whose answer is known by hand (`tree`).

Unset, a module's variable makes its tests skip at setup, naming the variable; the gate refuses that skip.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOST = os.environ.get("IRIS_HOST", "localhost")
PORT = int(os.environ.get("IVG_PORT", "31972"))


def connect(var: str):
    """A DB-API connection to the namespace `var` names; skips (at setup) when it is unset."""
    import iris

    ns = os.environ.get(var) or ""
    if not ns:
        pytest.skip(f"set {var} to a scratch namespace of this run (tests/e2e/gate.py GROUPS names one)")
    if ns.upper() == "USER":
        raise ValueError(f"{var}=USER: this module erases and loads data; name a scratch namespace")
    return iris.connect(HOST, PORT, ns, "_SYSTEM", "SYS"), ns


def sql_vector(sql_file: Path, node_id: str) -> list:
    """The vector a sample-data SQL file inserts for `node_id`, read from the file (the input, not IVG's answer)."""
    text = sql_file.read_text()
    m = re.search(r"\('" + re.escape(node_id) + r"',\s*TO_VECTOR\('(\[[^']*\])'", text)
    assert m, f"{sql_file.name} inserts no vector for {node_id}"
    return [float(x) for x in m.group(1).strip("[]").split(",")]


def tree(k: int, depth: int, prefix: str = "node_") -> list:
    """A k-ary tree, by hand: node i's children are k*i+1 .. k*i+k, down to `depth` levels below node 0.
    Returns its edges (parent, child, level of the child)."""
    edges, level = [], [0]
    for d in range(1, depth + 1):
        nxt = []
        for p in level:
            for c in range(k * p + 1, k * p + k + 1):
                edges.append((f"{prefix}{p}", f"{prefix}{c}", d))
                nxt.append(c)
        level = nxt
    return edges


def load_tree(engine, edges: list, predicate: str) -> None:
    """The tree's edges through the documented bulk API, then `sync()` (which builds ^NKG)."""
    n = engine.bulk_create_edges([{"source_id": s, "predicate": predicate, "target_id": o} for s, o, _ in edges])
    assert n == len(edges), (n, len(edges))
    engine.conn.commit()
    assert engine.sync()


def bfs_by_hand(edges: list, depth: int) -> set:
    """(s, o, step) of every hit a BFS from the tree's root reaches within `depth` hops: each edge down to that level,
    its step the child's level (a tree reaches each node one way)."""
    return {(s, o, lvl) for s, o, lvl in edges if lvl <= depth}
