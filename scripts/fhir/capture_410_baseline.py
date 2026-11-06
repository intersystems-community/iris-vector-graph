"""Spec 235 T003: the 4.1.0 behaviour baseline, captured before any engine change.

Loads the 233 genomics fixture and one 233 model-result trio under the fixed prefix
`b410`, the 233 concept graph and crosswalk, and the 231 bulk concept pipeline, then
records the default-argument PPR score maps, the graph status counts, the link report
and the provenance-seed PPR, for comparing one version's answers with another's
(`--compare`). The loading and the calls live in tests/e2e/fhir235_load.py. No test
takes its expected answers from the file (4.6.0 R309): test_235_patient_ranking_e2e.py
checks the same calls against a walk over the rows.

    .venv/bin/python -m scripts.fhir.capture_410_baseline --out specs/235-fhir-graph-interpretation/baseline_410.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from tests.e2e.fhir235_load import INPUTS_233, PARAMS_233, PREFIX, _key, load, teardown  # noqa: F401
from tests.e2e.fhir235_load import calls as capture

WHOLE_GRAPH = ("status_edges", "last_counts", "link_report")


def diff(baseline: dict, current: dict, tol: float = 1e-12, whole_graph: bool = True) -> list[str]:
    """Differences between two captures: score maps within `tol`, everything else exact.
    `whole_graph=False` skips WHOLE_GRAPH: those count every run in the repository, so
    they drift with whatever earlier E2E runs left behind (231's sync test keeps 509
    resources per run)."""
    out = []

    def scores(name, a, b):
        for k in sorted(set(a) | set(b)):
            if abs(a.get(k, 0.0) - b.get(k, 0.0)) > tol:
                out.append(f"{name}[{k}]: {a.get(k)} -> {b.get(k)}")

    for group in ("ppr_231", "ppr_233"):
        for seed in sorted(set(baseline[group]) | set(current[group])):
            scores(f"{group}.{seed}", baseline[group].get(seed, {}), current[group].get(seed, {}))
    scores("ppr_provenance", baseline["ppr_provenance"], current["ppr_provenance"])
    for key in WHOLE_GRAPH if whole_graph else ():
        if baseline[key] != current[key]:
            out.append(f"{key}: {json.dumps(baseline[key], sort_keys=True)} -> {json.dumps(current[key], sort_keys=True)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--out")
    group.add_argument("--compare", help="baseline file to compare a fresh capture with")
    ap.add_argument("--keep", action="store_true", help="leave the b410 run loaded")
    args = ap.parse_args(argv)

    from iris_vector_graph.engine import IRISGraphEngine

    from tests.e2e.fhir_conftest import connect

    conn = connect()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    handles = load(conn, engine)
    try:
        result = capture(engine, handles)
    finally:
        if not args.keep:
            teardown(conn, engine, handles)
    if args.compare:
        with open(args.compare) as fh:
            baseline = json.load(fh)
        # JSON round-trip so both sides have the same key and number types.
        found = diff(baseline, json.loads(json.dumps(result)))
        for line in found:
            print(line)
        print(f"{len(found)} difference(s) from {args.compare} (captured at {baseline['commit'][:7]})")
        return 1 if found else 0
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    result = {"commit": sha, "prefix": PREFIX, **result}
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
    print(
        f"wrote {args.out}: commit {sha[:7]}, ppr_231 {[len(v) for v in result['ppr_231'].values()]}, "
        f"ppr_233 {[len(v) for v in result['ppr_233'].values()]}, edges {result['status_edges']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
