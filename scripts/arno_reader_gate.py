#!/usr/bin/env python3
"""Evidence gate for the Arno Native Readers (spec 236, Story 2).

Runs each in-scope Native Reader against its ObjectScript Owner on five fixed graphs
and writes what passed to ``docs/arno-reader-proof.json``. Routing reads minimum
revisions from that file and from nowhere else.

    python scripts/arno_reader_gate.py                 # run, print, write nothing
    python scripts/arno_reader_gate.py --write-proof   # run, then update the proof file
    python scripts/arno_reader_gate.py --check         # proof vs the loaded library; writes nothing

Container: ``IVG_TEST_CONTAINER`` (default ``ivg-iris-enterprise``), namespace USER.
The library's readers walk USER's ``^KG`` whatever namespace the caller is in (measured
on 1.1.0), so this is the only namespace they can be compared in. Each fixture starts
with ``Graph.KG.Eraser.EraseAll`` in USER, which is test-only on this container.
``IVG_REQUIRE_ARNO=1`` turns "library absent" into a failure instead of a skip.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_vector_graph import _arno_gate as gate  # noqa: E402
from iris_vector_graph import arno_readers  # noqa: E402
from tests.fixtures.arno_reader_graphs import GRAPHS  # noqa: E402

PROOF_PATH = ROOT / "docs" / "arno-reader-proof.json"
LIBRARY_PATH = ROOT / "docker" / "enterprise" / "libarno_callout.so"
REPEATS = 5
#: Minimum partition similarity for CDLP against its Owner. Recorded in the proof file;
#: the observed minimum is printed with every run so the choice can be revisited.
CDLP_THRESHOLD = 0.8
ARCH = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}


def _container() -> str:
    return os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def _endpoint() -> tuple:
    try:
        return socket.gethostbyname(f"{_container()}.orb.local"), 1972
    except socket.gaierror:
        return "localhost", int(os.environ.get("IVG_PORT", "31972"))


def connect():
    """``(engine, native)`` in USER on the enterprise container."""
    import iris

    from iris_vector_graph import IRISGraphEngine

    host, port = _endpoint()
    dbapi = iris.dbapi.connect(hostname=host, port=port, namespace="USER", username="_SYSTEM", password="SYS")
    native = iris.createIRIS(iris.connect(host, port, "USER", "_SYSTEM", "SYS"))
    if str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) != "USER":
        raise RuntimeError("gate must run in USER")
    return IRISGraphEngine(dbapi), native, dbapi


def load_fixture(engine, dbapi, native, name: str) -> None:
    """Replace USER's graph with fixture ``name``."""
    g = GRAPHS[name]
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    for node in g["nodes"]:
        engine.create_node(node)
    for s, p, o in g["edges"]:
        engine.create_edge(s, p, o)
    dbapi.commit()
    engine._store._detect_arno()


# One entry per algorithm: how to call the reader, how to call the Owner, how to parse.
def _rows_scored(raw: str) -> list:
    from iris_vector_graph.stores.iris_sql_store import _scored_node_rows

    return _scored_node_rows(json.loads(raw))


def _rows_members(value_key: str) -> Callable[[str], list]:
    def parse(raw: str) -> list:
        from iris_vector_graph.stores.iris_sql_store import _membership_rows

        return _membership_rows(json.loads(raw), value_key)

    return parse


def _subgraph(raw: str) -> dict:
    return json.loads(raw)


def _spec(algorithm: str, g: dict) -> dict:
    seeds = json.dumps(g["seeds"])
    aa = "Graph.KG.ArnoAccel"
    return {
        "pagerank": dict(
            native=lambda s: s._arno_call(aa, "PageRankNativeJson", "0.85", "20"),
            owner=lambda s: s._call_classmethod("Graph.KG.PageRank", "PageRankGlobalJson", "0.85", "20"),
            parse=_rows_scored,
            compare=lambda reps, own: gate.scores_equivalent(reps[0], own),
        ),
        "ppr": dict(
            native=lambda s: s._arno_call(aa, "PPRNativeJson", seeds, "0.85", "20"),
            owner=lambda s: s._call_classmethod(
                "Graph.KG.PageRank", "RunJson", seeds, "0.85", "20", "0", "1.0", ""
            ),
            parse=_rows_scored,
            compare=lambda reps, own: gate.scores_equivalent(reps[0], own),
        ),
        "wcc": dict(
            native=lambda s: s._arno_call(aa, "WCCNativeJson"),
            owner=lambda s: s._call_classmethod("Graph.KG.Algorithms", "WCCJson"),
            parse=_rows_members("component_id"),
            compare=lambda reps, own: gate.partitions_equal(reps[0], own),
        ),
        "cdlp": dict(
            native=lambda s: s._arno_call(aa, "CDLPNativeJson", "10"),
            owner=lambda s: s._call_classmethod("Graph.KG.Algorithms", "CDLPJson", "10"),
            parse=_rows_members("community_id"),
            compare=lambda reps, own: gate.cdlp_equivalent(reps, own, CDLP_THRESHOLD),
        ),
        "subgraph": dict(
            native=lambda s: s._arno_call(aa, "SubgraphNativeJson", seeds, "2", "100"),
            owner=lambda s: s._call_classmethod("Graph.KG.Subgraph", "SubgraphJson", seeds, "2", "", "100"),
            parse=_subgraph,
            compare=lambda reps, own: gate.subgraph_equal(reps[0], own),
        ),
    }[algorithm]


def compare_one(store, algorithm: str, fixture: str) -> Dict[str, Any]:
    """Run one algorithm on the loaded fixture. ``{"ok": bool, "reason": str, ...}``."""
    spec = _spec(algorithm, GRAPHS[fixture])
    out: Dict[str, Any] = {"fixture": fixture, "ok": False, "reason": "", "dropped": False}
    try:
        reps_raw = [spec["native"](store) for _ in range(REPEATS)]
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"native call raised: {exc}"
        return out
    try:
        reps = [spec["parse"](r) for r in reps_raw]
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"native answer unreadable: {exc}; first 120 chars: {reps_raw[0][:120]!r}"
        return out
    if algorithm != "subgraph":
        rep = gate.repeats_identical(reps)
        if not rep:
            out["reason"] = f"repeats differ: {rep.reason}"
            return out
    else:
        for i, later in enumerate(reps[1:], start=2):
            if not gate.subgraph_equal(later, reps[0]):
                out["reason"] = f"repeats differ: call {i} differs from call 1"
                return out
    try:
        owner_raws = [spec["owner"](store) for _ in range(3 if algorithm == "cdlp" else 1)]
        owners = [spec["parse"](r) for r in owner_raws]
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"owner call failed: {exc}"
        return out
    if algorithm == "cdlp" and len({gate._canon(o) for o in owners}) != 1:
        out.update(dropped=True, reason="the Owner is itself unstable on this graph")
        return out
    verdict = spec["compare"](reps, owners[0])
    out["ok"], out["reason"] = verdict.ok, verdict.reason
    if algorithm == "cdlp":
        out["similarity"] = gate.partition_similarity(reps[0], owners[0])
    return out


def run_gate(engine, dbapi, native, algorithms=arno_readers.IN_SCOPE) -> Dict[str, Dict[str, Any]]:
    """``{algorithm: {"passed", "revision", "fixtures", "dropped", "repeats", "detail"}}``."""
    store = engine._store
    readers = arno_readers.read_reader_report(store)
    results: Dict[str, Dict[str, Any]] = {
        a: {"details": [], "revision": readers.get(a, 0)} for a in algorithms
    }
    for fixture in GRAPHS:
        load_fixture(engine, dbapi, native, fixture)
        readers = arno_readers.read_reader_report(store)
        for a in algorithms:
            if readers.get(a, 0) == 0:
                results[a]["details"].append(
                    {"fixture": fixture, "ok": False, "reason": "library reports no reader", "dropped": False}
                )
                continue
            results[a]["details"].append(compare_one(store, a, fixture))
            results[a]["revision"] = readers[a]
    for a, res in results.items():
        details = res["details"]
        kept = [d for d in details if not d["dropped"]]
        res["passed"] = bool(kept) and all(d["ok"] for d in kept) and res["revision"] >= 1
        res["fixtures"] = list(GRAPHS)
        res["repeats"] = REPEATS
        res["dropped"] = [{"fixture": d["fixture"], "reason": d["reason"]} for d in details if d["dropped"]]
        if a == "cdlp":
            res["cdlp_similarity"] = CDLP_THRESHOLD
    return results


def library_identity(store) -> tuple:
    """``(version string, {platform: sha256})`` of what is loaded and installed."""
    version = str(store._call_classmethod("Graph.KG.ArnoAccel", "LibraryVersion") or "")
    arch = subprocess.run(
        ["docker", "exec", _container(), "uname", "-m"], capture_output=True, text=True
    ).stdout.strip()
    digest = hashlib.sha256(LIBRARY_PATH.read_bytes()).hexdigest()
    return version, {ARCH.get(arch, arch): digest}


def print_report(results: Dict[str, Dict[str, Any]]) -> None:
    for a, res in results.items():
        print(f"{a:9s} {'PASS' if res['passed'] else 'FAIL'}  revision {res['revision']}")
        for d in res["details"]:
            mark = "ok " if d["ok"] else ("drop" if d["dropped"] else "FAIL")
            extra = f"  similarity {d['similarity']:.3f}" if "similarity" in d else ""
            print(f"    {d['fixture']:16s} {mark}{extra}  {d['reason']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-proof", action="store_true", help="update docs/arno-reader-proof.json")
    ap.add_argument("--check", action="store_true", help="compare the proof file with the loaded library")
    args = ap.parse_args(argv)

    try:
        engine, native, dbapi = connect()
    except Exception as exc:  # noqa: BLE001
        if gate.missing_library_action(os.environ) == "fail":
            print(f"arno_reader_gate: cannot reach {_container()}: {exc}", file=sys.stderr)
            return 2
        print(f"arno_reader_gate: skipped, {_container()} unreachable ({exc})")
        return 0
    store = engine._store
    if not store._detect_arno():
        if gate.missing_library_action(os.environ) == "fail":
            print("arno_reader_gate: the Arno library is not loadable here", file=sys.stderr)
            return 2
        print("arno_reader_gate: skipped, no Arno library")
        return 0
    readers = arno_readers.read_reader_report(store)

    if args.check:
        doc = json.loads(PROOF_PATH.read_text())
        problems = arno_readers.validate_proof(doc) + gate.check_proof_against_report(doc, readers)
        for p in problems:
            print(f"arno_reader_gate --check: {p}", file=sys.stderr)
        print("arno_reader_gate --check:", "FAILED" if problems else f"ok ({len(doc['algorithms'])} proven)")
        return 1 if problems else 0

    results = run_gate(engine, dbapi, native)
    print_report(results)
    sims = [d["similarity"] for d in results["cdlp"]["details"] if "similarity" in d]
    if sims:
        print(f"cdlp observed similarity: min {min(sims):.3f} max {max(sims):.3f} (threshold {CDLP_THRESHOLD})")
    if args.write_proof:
        version, sha = library_identity(store)
        previous = json.loads(PROOF_PATH.read_text()) if PROOF_PATH.exists() else None
        if previous and arno_readers.validate_proof(previous):
            previous = None
        doc = gate.build_proof(
            previous=previous,
            version=version or "arno-callout 0.0.0+000000000000",
            sha256=sha,
            results=results,
            proven_at=datetime.date.today().isoformat(),
        )
        problems = arno_readers.validate_proof(doc)
        if problems:
            print(f"arno_reader_gate: refusing to write an invalid proof: {problems}", file=sys.stderr)
            return 1
        PROOF_PATH.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"wrote {PROOF_PATH.relative_to(ROOT)}: proven {sorted(doc['algorithms'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
