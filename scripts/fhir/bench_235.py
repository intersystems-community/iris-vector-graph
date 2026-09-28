"""Spec 235 bench: sync, PPR and report medians against the budgets (SC-005..SC-007).

    .venv/bin/python -m scripts.fhir.bench_235 --label baseline --repo ~/.cache/ivg-235/src_<sha> \
        --set fixture --out /tmp/b.json
    .venv/bin/python -m scripts.fhir.bench_235 compare baseline.json current.json

`--set fixture` loads the vendored 10-patient file; `--set scale` assembles the
~100-patient Synthea run in ~/.cache/ivg-235/synthea-100/fhir. Both load under a fixed
per-set prefix and are deleted afterwards. `--repo` puts that checkout's
`iris_vector_graph` first on sys.path; its ObjectScript must already be deployed.
`--label baseline` skips what 4.1.0 lacks: group-by PPR and the two reports.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import subprocess
import sys
import time

SYNC_LIMIT = 1.15
GROUP_LIMIT = 1.20
REPORT_MS = 2000.0
SCALE_DIR = os.path.expanduser("~/.cache/ivg-235/synthea-100/fhir")
PREFIXES = {"fixture": "bfx", "scale": "bsc"}
CONCEPT_GRAPH = "bench235:concepts"
CROSSWALK_SOURCE = "bench235"
N_CONCEPTS = 5
RUNS = {"sync_ms": 5, "ppr_ms": 7, "ppr_group_ms": 7, "coverage_ms": 5, "gaps_ms": 5}
MEASURES = ("sync_ms", "ppr_ms", "ppr_group_ms", "coverage_ms", "gaps_ms")


# ------------------------------------------------------------------ budgets


def compare(baseline: dict, current: dict) -> list[str]:
    """Budget breaches of `current` against `baseline`, one string each; [] passes.
    Group-by PPR and the reports are budgeted at scale only."""
    out = []
    if baseline.get("set") != current.get("set"):
        return [f"set mismatch: baseline {baseline.get('set')!r}, current {current.get('set')!r}"]
    scale = current.get("set") == "scale"

    def need(run, key, which):
        if run.get(key) is None:
            out.append(f"{which} measurement missing: {key}")
            return None
        return float(run[key])

    base_sync = need(baseline, "sync_ms", "baseline")
    cur_sync = need(current, "sync_ms", "current")
    if base_sync is not None and cur_sync is not None and cur_sync > base_sync * SYNC_LIMIT:
        out.append(f"sync {cur_sync:.1f} ms > {SYNC_LIMIT:.2f} x baseline {base_sync:.1f} ms")
    if not scale:
        return out
    base_ppr = need(baseline, "ppr_ms", "baseline")
    group = need(current, "ppr_group_ms", "current")
    if base_ppr is not None and group is not None and group > base_ppr * GROUP_LIMIT:
        out.append(f"group-by PPR {group:.1f} ms > {GROUP_LIMIT:.2f} x baseline PPR {base_ppr:.1f} ms")
    for key in ("coverage_ms", "gaps_ms"):
        v = need(current, key, "current")
        if v is not None and v >= REPORT_MS:
            out.append(f"{key.split('_')[0]} {v:.1f} ms >= {REPORT_MS:.0f} ms")
    return out


def _runs(data: dict) -> dict:
    """A results file is one run or {set: run}."""
    return {data["set"]: data} if "set" in data else data


def compare_files(baseline_path: str, current_path: str) -> list[str]:
    with open(baseline_path) as fh:
        base = _runs(json.load(fh))
    with open(current_path) as fh:
        cur = _runs(json.load(fh))
    out = []
    for name, run in sorted(base.items()):
        if name not in cur:
            out.append(f"set {name!r} missing from {current_path}")
            continue
        out += [f"[{name}] {b}" for b in compare(run, cur[name])]
    return out


# ------------------------------------------------------------------ measuring


def _median_ms(fn, n: int) -> float:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return round(statistics.median(times), 3)


def _resources(set_: str) -> list[dict]:
    from tests.e2e import interp_fixture as ifx

    if set_ == "fixture":
        return ifx.load_fixture()
    if not os.path.isdir(SCALE_DIR):
        raise SystemExit(f"{SCALE_DIR} missing: generate the scale set (research R16)")
    return ifx.assemble(*ifx.read_synthea(SCALE_DIR))[0]


def top_codes(resources: list[dict], n: int = N_CONCEPTS) -> list[tuple[str, str]]:
    """The n most frequent Condition.code (system, code) pairs, ties by value."""
    counts = collections.Counter(
        (c["system"], c["code"])
        for r in resources
        if r["resourceType"] == "Condition"
        for c in r.get("code", {}).get("coding", [])
        if c.get("system") and c.get("code")
    )
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _clear_concepts(conn, engine) -> None:
    engine.erase_graph(CONCEPT_GRAPH)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND source = ?",
            [CONCEPT_GRAPH, CROSSWALK_SOURCE],
        )
        conn.commit()
    finally:
        cur.close()


def measure(label: str, set_: str, commit: str) -> dict:
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    from tests.e2e.fhir_conftest import (
        GRAPH,
        SYNTHEA_BATCH,
        FhirLoader,
        connect,
        load_run,
        prefix_resources,
        teardown_run,
    )
    from tests.e2e.interp_fixture import load_order

    conn = connect()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    if not engine.fhir_graph_register(denylist=[]).get("rebuilt"):
        engine.fhir_graph_rebuild(GRAPH)
    loader = FhirLoader(conn)
    loader.prefix = PREFIXES[set_]
    source = _resources(set_)
    resources = load_run(conn, loader, load_order(prefix_resources(source, loader.prefix)), batch=SYNTHEA_BATCH)
    concepts = []
    try:
        _clear_concepts(conn, engine)
        for i, (system, code) in enumerate(top_codes(source)):
            iri = f"bench235:c{i}"
            engine.create_node(iri, labels=["Concept"], graph=CONCEPT_GRAPH)
            engine.code_crosswalk_add(
                system, code, iri, target_graph=CONCEPT_GRAPH, relation="exact", source=CROSSWALK_SOURCE
            )
            concepts.append(iri)
        run = {
            "label": label,
            "set": set_,
            "commit": commit,
            "ivg_path": os.path.dirname(iris_vector_graph.__file__),
            "image": _image(),
            "counts": dict(sorted(collections.Counter(r["resourceType"] for r in resources).items())),
            "concepts": len(concepts),
        }

        def ppr(**kw):
            return engine.fhir_concept_ppr(GRAPH, CONCEPT_GRAPH, concepts, hops=0, **kw)

        cur = conn.cursor()
        try:
            # The rebuild covers the whole repository: its size must match across runs.
            cur.execute('SELECT COUNT(*) FROM "HSFHIR_X0001_R".Rsrc WHERE Deleted = 0')
            run["repo_resources"] = int(cur.fetchone()[0])
        finally:
            cur.close()
        run["seeds"] = len(engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, concepts))
        run["sync_ms"] = _median_ms(lambda: engine.fhir_graph_rebuild(GRAPH), RUNS["sync_ms"])
        run["ppr_ms"] = _median_ms(ppr, RUNS["ppr_ms"])
        if label == "baseline":
            run.update(ppr_group_ms=None, coverage_ms=None, gaps_ms=None)
        else:
            run["ppr_group_ms"] = _median_ms(lambda: ppr(group_by="patient"), RUNS["ppr_group_ms"])
            run["coverage_ms"] = _median_ms(lambda: engine.fhir_coverage_report(GRAPH), RUNS["coverage_ms"])
            run["gaps_ms"] = _median_ms(
                lambda: engine.fhir_concept_gaps(GRAPH, params="clinical"), RUNS["gaps_ms"]
            )
        return run
    finally:
        _clear_concepts(conn, engine)
        teardown_run(conn, loader, resources, batch=SYNTHEA_BATCH)
        conn.close()


def _commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def _image() -> str:
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", container], capture_output=True, text=True
    )
    return out.stdout.strip() or "unknown"


# ------------------------------------------------------------------ CLI


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "compare":
        ap = argparse.ArgumentParser(prog="bench_235.py compare")
        ap.add_argument("baseline")
        ap.add_argument("current")
        args = ap.parse_args(argv[1:])
        breaches = compare_files(args.baseline, args.current)
        for b in breaches:
            print(b)
        print(f"{len(breaches)} budget breach(es)")
        return 1 if breaches else 0

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", choices=["baseline", "current"], required=True)
    ap.add_argument("--repo", help="checkout whose iris_vector_graph to import")
    ap.add_argument("--set", dest="set_", choices=sorted(PREFIXES), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--commit", help="commit the --repo archive was made from (default HEAD)")
    args = ap.parse_args(argv)
    if args.repo:
        sys.path.insert(0, os.path.abspath(os.path.expanduser(args.repo)))
    run = measure(args.label, args.set_, args.commit or _commit())
    with open(args.out, "w") as fh:
        json.dump(run, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: run[k] for k in ("set", "commit", *MEASURES, "seeds", "repo_resources")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
