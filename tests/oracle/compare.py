"""Comparing an IVG answer with the reference's (4.6.0 R306). Each function returns a list of problems, empty when the
answer is the documented one; the E2E test prints them with the seed and the graph."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from tests.oracle.reference import TIE, collation_key, ranked, tied


def _fmt(pairs) -> str:
    return "[" + ", ".join(f"({i!r}, {s:.6g})" for i, s in pairs) + "]"


def compare_ranked(
    got: Sequence[Tuple[str, float]],
    expected: Mapping[str, float],
    top_k: int = 0,
    tol: float = 1e-6,
    omit_zero: bool = False,
) -> List[str]:
    """``got`` (``(id, score)`` in IVG's order) against the reference's ranking of ``expected``: the same ids, each
    score within ``tol``, highest first with ties by id in IRIS collation, and the same ``top_k`` cut. ``omit_zero``:
    nodes scoring 0 are left out (betweenness). Scores closer than ``tol`` may come in either order unless the
    reference ties them (`tied`), and then they go by id."""
    full = {k: v for k, v in expected.items() if not (omit_zero and abs(v) <= TIE)}
    want = ranked(full, top_k)
    problems: List[str] = []
    got = [(str(i), float(s)) for i, s in got]
    got_ids = [i for i, _ in got]
    if len(set(got_ids)) != len(got_ids):
        problems.append(f"duplicate ids: {sorted({i for i in got_ids if got_ids.count(i) > 1})}")
    want_ids = {i for i, _ in want}
    missing, extra = want_ids - set(got_ids), set(got_ids) - want_ids
    if top_k and (missing or extra):
        # a near-tie at the cut may fall either way
        edge = want[-1][1] if want else 0.0
        missing = {i for i in missing if abs(full[i] - edge) > tol}
        extra = {i for i in extra if i not in full or abs(full[i] - edge) > tol}
    if missing:
        problems.append(f"missing ids {sorted(missing, key=collation_key)}")
    if extra:
        problems.append(f"unexpected ids {sorted(extra, key=collation_key)}")
    bad = [(i, s, full[i]) for i, s in got if i in full and abs(s - full[i]) > tol]
    if bad:
        problems.append("scores off: " + ", ".join(f"{i!r}: IVG {s:.9g} vs {r:.9g}" for i, s, r in bad[:8]))
    for (a, _), (b, _) in zip(got, got[1:]):
        if a not in full or b not in full:
            continue
        ra, rb = full[a], full[b]
        if rb - ra > tol:
            problems.append(f"order: {a!r} ({ra:.6g}) before {b!r} ({rb:.6g})")
        elif tied(ra, rb) and collation_key(b) < collation_key(a):
            problems.append(f"tie order: {a!r} before {b!r} (tied at {ra:.6g}; ties go by id in IRIS collation)")
    if problems:
        problems.append(f"IVG:       {_fmt(got)}")
        problems.append(f"reference: {_fmt(want)}")
    return problems


def compare_values(got: Mapping[str, object], expected: Mapping[str, object], tol: float = 1e-9) -> List[str]:
    """Two ``{id: value}`` maps: the same ids and values (numbers within ``tol``)."""
    problems = []
    missing, extra = set(expected) - set(got), set(got) - set(expected)
    if missing:
        problems.append(f"missing ids {sorted(missing, key=collation_key)}")
    if extra:
        problems.append(f"unexpected ids {sorted(extra, key=collation_key)}")
    for k in sorted(set(got) & set(expected), key=collation_key):
        a, b = got[k], expected[k]
        same = abs(float(a) - float(b)) <= tol if isinstance(b, (int, float)) and isinstance(a, (int, float)) else a == b
        if not same:
            problems.append(f"{k!r}: IVG {a!r} vs {b!r}")
    if problems:
        problems.append(f"IVG:       {dict(sorted(got.items(), key=lambda kv: collation_key(kv[0])))}")
        problems.append(f"reference: {dict(sorted(expected.items(), key=lambda kv: collation_key(kv[0])))}")
    return problems


def compare_partition(got: Mapping[str, object], expected: Iterable[Iterable[str]]) -> List[str]:
    """``{id: group}`` against the expected sets of ids, labels ignored."""
    by: Dict[object, set] = {}
    for k, v in got.items():
        by.setdefault(v, set()).add(k)
    mine = sorted((sorted(s, key=collation_key) for s in by.values()), key=lambda s: collation_key(s[0]))
    want = sorted((sorted(s, key=collation_key) for s in expected), key=lambda s: collation_key(s[0]))
    return [] if mine == want else [f"IVG groups {mine}", f"reference   {want}"]


def compare_order(got_ids: Sequence[str], want_ids: Sequence[str]) -> List[str]:
    return [] if list(got_ids) == list(want_ids) else [f"IVG order       {list(got_ids)}", f"reference order {list(want_ids)}"]
