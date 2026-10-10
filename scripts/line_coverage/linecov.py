"""Merge IRIS line-monitor dumps and check them against the ObjectScript coverage floor.

A dump is what ``Cov.cls`` writes: ``L|<routine>|<line>|<count>|<text>`` per routine line, then
``DONE``. A line counts as covered when ANY dump shows a count above zero for the same routine, line
number and text, so dumps from separate runs (separate namespaces, restarts) merge into one figure.

"Adjusted" leaves out lines no test can execute by design: the entry line of every compiled
method, the generated embedded-SQL stub subroutines and their cursor variables, the compiler-generated
``%`` methods, and comment and brace-only lines. The floor file (docs/objectscript-coverage-floor.json) is stated on that basis.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
FLOOR_PATH = ROOT / "docs" / "objectscript-coverage-floor.json"

_GENERATED = re.compile(r"%c\d{6,}|%ivg\w*\d{6,}|%sqlcq\.|%xxsql|BuildQuery\^%SYS\.SQLSRV")


def routine_names(src_root: Path = ROOT / "iris_src" / "src") -> List[str]:
    """Every shipped class as its compiled routine name: ``Package.Class.1``."""
    return sorted(
        str(p.relative_to(src_root))[: -len(".cls")].replace("/", ".") + ".1"
        for p in src_root.rglob("*.cls")
    )


def executable(text: str, adjusted: bool = True) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith((";", "//", "/*", "*", "{", "}")):
        return False
    if adjusted:
        if re.match(r"^\w+(\(.*\))?\s+methodimpl\b", text) or re.match(r"^%0dsql\w*", text):
            return False
        if _GENERATED.search(text) or stripped in ("q", "return"):
            return False
        if re.match(r"^%sqllistUCQ", text) or re.match(r'^\s*s t\("%0dsql', text):
            return False
    return True


#: Methods the class compiler writes for every ``%Query`` (``<Name>FetchRows``, ``<Name>SendODBC``,
#: ``<Name>GetInfo``): the ODBC / JDBC entry points, not the query's own Execute / Fetch / Close.
_QUERY_PLUMBING = re.compile(r"^\w+(?:FetchRows|SendODBC|GetInfo)$")
_LABEL = re.compile(r"^(%?\w+)(?:\(|\s|$)")


def generated_free(lines):
    """Drop the lines of compiler-generated methods: labels that start with ``%``.

    ``%NormalizeObject``, ``%ValidateObject``, ``%LoadFromMemory`` and the like are written by the class
    compiler for every property and run only on ``%Save`` or validation, which a ``%RegisteredObject``
    never does. They are not in the ``.cls`` file and no test of the repo's code can reach them.
    ``%Construct`` is generated too, but runs on every ``%New`` and is kept out as well for consistency.
    """
    kept = set()
    label = None
    for key in sorted(lines):
        m = _LABEL.match(key[1]) if key[1] and not key[1][0] in " \t;{}" else None
        if m:
            label = m.group(1)
        if label is not None and (label.startswith("%") or _QUERY_PLUMBING.match(label)):
            continue
        kept.add(key)
    return kept


Dump = Dict[str, Dict[Tuple[int, str], int]]


def load(path: Path) -> Dump:
    out: Dump = collections.defaultdict(dict)
    with open(path, errors="replace") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("|", 4)
            if len(parts) < 5 or parts[0] != "L":
                continue
            out[parts[1]][(int(parts[2]), parts[4])] = int(parts[3])
    return out


def merge(dumps: Iterable[Dump], adjusted: bool = True, skip: Iterable[str] = ()) -> Dict[str, Tuple[int, int]]:
    """``{routine: (covered, total)}`` over the union of the dumps."""
    dumps = list(dumps)
    skipped = set(skip)
    result: Dict[str, Tuple[int, int]] = {}
    for routine in set().union(*(d.keys() for d in dumps)) if dumps else set():
        if routine in skipped:
            continue
        lines = set().union(*(d[routine].keys() for d in dumps if routine in d))
        if adjusted:
            lines = generated_free(lines)
        lines = {key for key in lines if executable(key[1], adjusted)}
        covered = sum(1 for key in lines if max(d.get(routine, {}).get(key, 0) for d in dumps) > 0)
        result[routine] = (covered, len(lines))
    return result


def overall(per_class: Dict[str, Tuple[int, int]]) -> Tuple[int, int, float]:
    covered = sum(c for c, _ in per_class.values())
    total = sum(t for _, t in per_class.values())
    return covered, total, (100.0 * covered / total if total else 0.0)


def load_floor(path: Path = FLOOR_PATH) -> dict:
    return json.loads(Path(path).read_text())


def check(dumps: Iterable[Dump], floor: dict) -> Tuple[bool, List[str], float]:
    """Apply the floor file. Returns ``(passed, messages, adjusted_percent)``."""
    dumps = list(dumps)
    excluded = set(floor.get("excluded", {}))
    allow_zero = set(floor.get("allow_zero", {}))
    per_class = merge(dumps, adjusted=True, skip=excluded)
    covered, total, pct = overall(per_class)
    messages: List[str] = []
    ok = True
    if pct < float(floor["adjusted_percent_min"]):
        ok = False
        messages.append(
            f"adjusted coverage {pct:.1f}% ({covered}/{total}) is under the floor "
            f"{floor['adjusted_percent_min']}%"
        )
    missing = [r for r in floor.get("expected_routines", []) if r not in excluded and r not in per_class]
    if missing:
        ok = False
        messages.append("not measured at all (no dump has them): " + ", ".join(sorted(missing)))
    untested = sorted(r for r, (c, t) in per_class.items() if t and c == 0 and r not in allow_zero)
    if untested:
        ok = False
        messages.append(
            "classes with no executed line and no entry in allow_zero: " + ", ".join(untested)
        )
    stale = sorted(r for r in allow_zero if r in per_class and per_class[r][0] > 0)
    if stale:
        messages.append("allow_zero entries that now have coverage (remove them): " + ", ".join(stale))
        ok = False
    return ok, messages, pct
