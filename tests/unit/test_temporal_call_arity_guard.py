"""Every temporal classmethod call passes a graph key first (spec 223).

Spec 223 put `graphId` at the front of sixteen `Graph.KG.TemporalIndex` methods
(plus `TemporalIndexMS.GetBucketCount`). Callers that were not migrated do not
fail loudly — they shift every remaining argument one slot left, so a timestamp
arrives where a predicate belongs and the last parameter is never bound. The
symptoms are a bare `<UNDEFINED> *tsEnd`, or worse, silence: `prov.py` swallowed
its own crash in an `except Exception` and returned an empty edge list for months.

Thirty-nine call sites had been missed: 38 in `tests/`, one in shipping code
(`_engine/prov.py`), one in `scripts/bench/`. A signature change with the
arguments passed positionally cannot be caught by any single test, so the guard
is a scan: it reads the real `.cls` signatures and checks every call site in the
repository against them.

The scan understands only positional calls, which is all the bridges
(`classMethodValue`, `classMethodVoid`, `_call_classmethod`) accept.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# The bridges take the class and method names as the first two positional
# arguments; everything after is the ObjectScript argument list.
_CALL = re.compile(r'"Graph\.KG\.TemporalIndex(?:MS)?"\s*,\s*"(\w+)"\s*,?')

_SCANNED_ROOTS = ("iris_vector_graph", "tests", "scripts")


def _signatures() -> dict[str, list[str]]:
    """Parameter lists as written in the ObjectScript source."""
    sigs: dict[str, list[str]] = {}
    for name in ("TemporalIndex", "TemporalIndexMS"):
        src = (REPO / "iris_src/src/Graph/KG" / f"{name}.cls").read_text()
        for m in re.finditer(r"^ClassMethod (\w+)\(([^)]*)\)", src, re.M):
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            sigs.setdefault(m.group(1), params)
    return sigs


def _split_args(src: str, start: int) -> list[str]:
    """Top-level arguments from `start` to the call's closing paren."""
    args: list[str] = []
    cur = ""
    depth = 0
    i = start
    while i < len(src):
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            i += 1
            continue
        cur += c
        i += 1
    if cur.strip():
        args.append(cur.strip())
    return args


def _looks_like_a_graph_key(arg: str) -> bool:
    """A graph key is `""`, or a name that says so."""
    return (
        arg in ('""', "''")
        or "graph" in arg.lower()
        or arg.upper().startswith(("GRAPH", "_G"))
    )


def _stale_calls(text: str, sigs: dict[str, list[str]]) -> list[tuple[int, str, str]]:
    out = []
    for m in _CALL.finditer(text):
        method = m.group(1)
        params = sigs.get(method)
        if not params or params[0].split()[0] != "graphId":
            continue
        args = _split_args(text, m.end())
        # `graphId` carries a default in ObjectScript, but a positional caller may
        # not lean on it: omitting it shifts everything after. So it counts as
        # required here even though the signature makes it optional. Without this,
        # `QueryWindow("", "", ts_start, ts_end)` — the exact prov.py defect — has
        # a plausible argument count and a first argument that looks like a graph
        # key, and no scan can tell it from a correct four-argument call.
        required = 1 + sum(1 for p in params[1:] if "=" not in p)
        first = args[0] if args else "<no arguments>"
        if len(args) < required or not _looks_like_a_graph_key(first):
            out.append((text[: m.start()].count("\n") + 1, method, first))
    return out


def _python_files():
    for root in _SCANNED_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            yield path


@pytest.fixture(scope="module")
def sigs():
    return _signatures()


def test_the_scan_can_see_the_signatures(sigs):
    """If the `.cls` parse breaks, the guard below silently passes forever."""
    assert sigs["QueryWindow"][0].startswith("graphId")
    assert sigs["InsertEdge"][0].startswith("graphId")
    assert sigs["GetBucketCount"][0].startswith("graphId")


def test_the_scan_still_recognises_a_stale_call(sigs):
    """The exact shape prov.py had: four arguments, no graph key."""
    stale = _stale_calls(
        'x.classMethodValue("Graph.KG.TemporalIndex", "QueryWindow", "", "", 0, 99)',
        sigs,
    )

    assert stale, "the scan no longer detects the defect it exists to prevent"


def test_the_scan_accepts_a_migrated_call(sigs):
    assert not _stale_calls(
        'x.classMethodValue("Graph.KG.TemporalIndex", "QueryWindow", "", "", "", 0, 99)',
        sigs,
    )


def test_the_scan_reaches_the_files_that_had_the_defect():
    """A scan that resolves no paths is a guard that guards nothing."""
    scanned = {p.relative_to(REPO).as_posix() for p in _python_files()}

    assert "iris_vector_graph/_engine/prov.py" in scanned
    assert "tests/integration/test_temporal_json_safety.py" in scanned


def test_no_caller_omits_the_graph_key(sigs):
    findings = []
    for path in _python_files():
        if path.name == Path(__file__).name:
            continue
        for line, method, first in _stale_calls(path.read_text(), sigs):
            rel = path.relative_to(REPO).as_posix()
            findings.append(f"{rel}:{line} {method}(...) first argument is {first}")

    assert not findings, (
        "spec 223 put graphId first on these methods; a positional call that "
        "omits it shifts every later argument:\n  " + "\n  ".join(findings)
    )
