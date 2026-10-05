"""Equivalence rules and the proof writer for the Native Reader evidence gate (spec 236).

Pure functions over already-parsed answers, so the rules are tested without a
container. ``scripts/arno_reader_gate.py`` runs the readers and feeds them in.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REL_TOL = 1e-6
ABS_TOL = 1e-12


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:  # `assert verdict` reads naturally
        return self.ok


def _pass() -> Verdict:
    return Verdict(True)


def _fail(reason: str) -> Verdict:
    return Verdict(False, reason)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(REL_TOL * max(abs(a), abs(b)), ABS_TOL)


def _as_map(rows: Iterable[Sequence[Any]]) -> Dict[str, Any]:
    return {str(r[0]): r[1] for r in rows}


def scores_equivalent(native: Sequence[Sequence[Any]], owner: Sequence[Sequence[Any]]) -> Verdict:
    """Same node set, scores within 1e-6 relative, ranking agrees wherever not tied."""
    n, o = _as_map(native), _as_map(owner)
    if set(n) != set(o):
        return _fail(f"node sets differ: native-only {sorted(set(n) - set(o))[:5]}, owner-only {sorted(set(o) - set(n))[:5]}")
    for node, score in o.items():
        if not _close(float(n[node]), float(score)):
            return _fail(f"score for {node}: native {n[node]} vs owner {score}")
    ordered = sorted(o.items(), key=lambda kv: -float(kv[1]))
    for i, (a, sa) in enumerate(ordered):
        for b, sb in ordered[i + 1 :]:
            if _close(float(sa), float(sb)):
                continue  # tied in the owner: either order is right
            if float(n[a]) < float(n[b]):
                return _fail(f"ranking: owner has {a} above {b}, native has them reversed")
    return _pass()


def _groups(rows: Iterable[Sequence[Any]]) -> Dict[str, frozenset]:
    by_label: Dict[Any, set] = {}
    for r in rows:
        by_label.setdefault(json.dumps(r[1], sort_keys=True, default=str), set()).add(str(r[0]))
    return {k: frozenset(v) for k, v in by_label.items()}


def partitions_equal(native: Sequence[Sequence[Any]], owner: Sequence[Sequence[Any]]) -> Verdict:
    """The same grouping of the same nodes, whatever the groups are called."""
    if set(_as_map(native)) != set(_as_map(owner)):
        return _fail("node sets differ")
    if set(_groups(native).values()) != set(_groups(owner).values()):
        return _fail("partitions differ")
    return _pass()


def _edge(e: Any) -> Tuple[str, str, str]:
    if isinstance(e, Mapping):
        if "s" in e:
            return (str(e["s"]), str(e.get("p", "")), str(e["o"]))
        return (str(e.get("src")), str(e.get("type", e.get("p", ""))), str(e.get("dst")))
    s, p, o = e
    return (str(s), str(p), str(o))


def subgraph_equal(native: Mapping[str, Any], owner: Mapping[str, Any]) -> Verdict:
    """Node set and edge set exactly equal; an edge includes its predicate."""
    if {str(x) for x in native.get("nodes", [])} != {str(x) for x in owner.get("nodes", [])}:
        return _fail("node sets differ")
    if {_edge(e) for e in native.get("edges", [])} != {_edge(e) for e in owner.get("edges", [])}:
        return _fail("edge sets differ")
    return _pass()


def partition_similarity(a: Sequence[Sequence[Any]], b: Sequence[Sequence[Any]]) -> float:
    """Jaccard over the pairs of nodes each partition puts together; 1.0 when neither has any."""

    def pairs(rows: Sequence[Sequence[Any]]) -> set:
        out = set()
        for members in _groups(rows).values():
            ms = sorted(members)
            out.update((x, y) for i, x in enumerate(ms) for y in ms[i + 1 :])
        return out

    pa, pb = pairs(a), pairs(b)
    union = pa | pb
    return 1.0 if not union else len(pa & pb) / len(union)


def _canon(rows: Sequence[Sequence[Any]]) -> str:
    return json.dumps(sorted([str(r[0]), r[1]] for r in rows), sort_keys=True, default=str)


def repeats_identical(answers: Sequence[Sequence[Sequence[Any]]]) -> Verdict:
    """Every repeated native answer equal to the first; row order is not identity."""
    first = _canon(answers[0])
    for i, a in enumerate(answers[1:], start=2):
        if _canon(a) != first:
            return _fail(f"call {i} differs from call 1")
    return _pass()


def cdlp_equivalent(
    repeats: Sequence[Sequence[Sequence[Any]]], owner: Sequence[Sequence[Any]], threshold: float
) -> Verdict:
    rep = repeats_identical(repeats)
    if not rep:
        return rep
    sim = partition_similarity(repeats[0], owner)
    if sim < threshold:
        return _fail(f"partition similarity {sim:.3f} below {threshold}")
    return _pass()


# ------------------------------------------------------------------ the proof file


def build_proof(
    *,
    previous: Optional[Mapping[str, Any]],
    version: str,
    sha256: Mapping[str, str],
    results: Mapping[str, Mapping[str, Any]],
    proven_at: str,
) -> Dict[str, Any]:
    """The proof document after a gate run.

    ``results[name]`` carries ``passed``, ``revision`` (the revision the library reported
    when the reader passed), ``fixtures``, ``repeats``, ``dropped`` and, for cdlp,
    ``cdlp_similarity``. Only passing algorithms are recorded; a failing one is removed,
    even if an earlier run proved it, because that record is for another build. An
    algorithm absent from ``results`` keeps its earlier record. ``previous`` is not
    modified.
    """
    algorithms: Dict[str, Any] = {}
    if previous:
        algorithms = copy.deepcopy(dict(previous.get("algorithms", {})))
    for name, res in results.items():
        algorithms.pop(name, None)
        if not res.get("passed"):
            continue
        entry: Dict[str, Any] = {
            "min_revision": int(res["revision"]),
            "fixtures": list(res["fixtures"]),
            "repeats": int(res["repeats"]),
        }
        if res.get("dropped"):
            entry["dropped"] = [dict(d) for d in res["dropped"]]
        if "cdlp_similarity" in res:
            entry["cdlp_similarity"] = res["cdlp_similarity"]
        algorithms[name] = entry
    return {
        "library": {"version": version, "sha256": dict(sha256)},
        "proven_at": proven_at,
        "algorithms": algorithms,
    }


def check_proof_against_report(doc: Mapping[str, Any], readers: Mapping[str, int]) -> List[str]:
    """Problems where the proof claims more than the loaded library reports."""
    problems = []
    for name, entry in doc.get("algorithms", {}).items():
        have = readers.get(name, 0)
        want = int(entry["min_revision"])
        if have < want:
            problems.append(f"{name}: proof requires revision {want}, library reports {have}")
    return problems


def missing_library_action(environ: Mapping[str, str]) -> str:
    """``"fail"`` when ``IVG_REQUIRE_ARNO=1`` says the library should be here, else ``"skip"``.

    FR-010: the gate must not skip because the library is absent on a machine that is
    supposed to have it; the pre-release step sets the switch.
    """
    return "fail" if environ.get("IVG_REQUIRE_ARNO", "").strip() == "1" else "skip"
