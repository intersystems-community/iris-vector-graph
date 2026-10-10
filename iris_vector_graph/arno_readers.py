"""Which algorithms may be answered by a Rust Native Reader in the Arno callout.

Spec 236. The ObjectScript Owners are the reference and the fallback; a Native Reader
answers only when the loaded library reports a reader revision at or above the minimum
recorded in ``docs/arno-reader-proof.json`` for that algorithm. The proof file is
written by ``scripts/arno_reader_gate.py`` after the reader has been compared with its
Owner, never by hand, and routing takes its numbers from nowhere else.

Everything here is pure except :func:`read_reader_report` (one classmethod call) and
:func:`load_proof` (one file read), so :func:`route` is testable with no container.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: The algorithms this feature may send to a Native Reader. Betweenness is not here on
#: purpose: the library's is undirected by construction and the Owner is exact directed.
IN_SCOPE = ("pagerank", "ppr", "wcc", "cdlp", "subgraph")

NATIVE = "native"
OWNER = "owner"

#: ``IVG_ARNO_PROOF`` points at another proof file (tests, a site that re-proved).
PROOF_ENV = "IVG_ARNO_PROOF"
_DEFAULT_PROOF = Path(__file__).resolve().parent.parent / "docs" / "arno-reader-proof.json"

_SHA256_HEX = set("0123456789abcdef")


@dataclass(frozen=True)
class Proof:
    """The committed record of which readers were proven, and on which build."""

    library: Mapping[str, Any] = field(default_factory=dict)
    proven_at: str = ""
    algorithms: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Proof":
        return cls()

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "Proof":
        """Build from a parsed document. Raises ``ValueError`` when it is invalid."""
        problems = validate_proof(doc)
        if problems:
            raise ValueError("; ".join(problems))
        algorithms = {
            name: dict(entry)
            for name, entry in doc["algorithms"].items()
            # A name ivg does not route (betweenness) is dropped, not trusted.
            if name in IN_SCOPE
        }
        return cls(library=dict(doc["library"]), proven_at=doc["proven_at"], algorithms=algorithms)

    def min_revision(self, algorithm: str) -> Optional[int]:
        entry = self.algorithms.get(algorithm)
        return None if entry is None else int(entry["min_revision"])


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_proof(doc: Any) -> list:
    """Problems with ``doc`` against ``contracts/arno-reader-proof.schema.json``.

    Hand-written so routing needs no ``jsonschema``; a test keeps the two in step.
    """
    import datetime
    import re

    problems: list = []
    if not isinstance(doc, dict):
        return ["proof is not an object"]
    for key in ("library", "proven_at", "algorithms"):
        if key not in doc:
            problems.append(f"missing {key}")
    if problems:
        return problems
    lib = doc["library"]
    if not isinstance(lib, dict):
        problems.append("library is not an object")
    else:
        for key in ("version", "sha256"):
            if key not in lib:
                problems.append(f"library missing {key}")
        version = lib.get("version")
        if "version" in lib and not (
            isinstance(version, str)
            and re.fullmatch(r"arno-callout [0-9]+\.[0-9]+\.[0-9]+\+[0-9a-f]+", version)
        ):
            problems.append("library.version does not match 'arno-callout X.Y.Z+<sha>'")
        sums = lib.get("sha256")
        if "sha256" in lib:
            if not isinstance(sums, dict):
                problems.append("library.sha256 is not an object")
            else:
                for platform, digest in sums.items():
                    if not (
                        isinstance(digest, str)
                        and len(digest) == 64
                        and set(digest) <= _SHA256_HEX
                    ):
                        problems.append(f"library.sha256[{platform}] is not a sha256")
    proven_at = doc["proven_at"]
    try:
        if not isinstance(proven_at, str):
            raise ValueError
        datetime.date.fromisoformat(proven_at)
    except ValueError:
        problems.append("proven_at is not a date")
    algorithms = doc["algorithms"]
    if not isinstance(algorithms, dict):
        return problems + ["algorithms is not an object"]
    allowed = set(IN_SCOPE)
    for name, entry in algorithms.items():
        where = f"algorithms.{name}"
        if name not in allowed:
            problems.append(f"{where}: not a routed algorithm")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object")
            continue
        for key in ("min_revision", "fixtures", "repeats"):
            if key not in entry:
                problems.append(f"{where} missing {key}")
        rev = entry.get("min_revision")
        if "min_revision" in entry and not (_is_int(rev) and rev >= 1):
            problems.append(f"{where}.min_revision must be an integer >= 1")
        fixtures = entry.get("fixtures")
        if "fixtures" in entry and not (
            isinstance(fixtures, list)
            and len(fixtures) >= 5
            and all(isinstance(f, str) for f in fixtures)
        ):
            problems.append(f"{where}.fixtures must be a list of at least 5 names")
        repeats = entry.get("repeats")
        if "repeats" in entry and not (_is_int(repeats) and repeats >= 5):
            problems.append(f"{where}.repeats must be an integer >= 5")
        dropped = entry.get("dropped", [])
        if not (
            isinstance(dropped, list)
            and all(
                isinstance(d, dict)
                and isinstance(d.get("fixture"), str)
                and isinstance(d.get("reason"), str)
                for d in dropped
            )
        ):
            problems.append(f"{where}.dropped entries need fixture and reason")
        sim = entry.get("cdlp_similarity")
        if "cdlp_similarity" in entry and not (
            isinstance(sim, (int, float)) and not isinstance(sim, bool) and 0 <= sim <= 1
        ):
            problems.append(f"{where}.cdlp_similarity must be a number in [0, 1]")
    return problems


def _read_proof_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_proof(path: Optional[os.PathLike] = None) -> Proof:
    """Read the proof file. A missing, unparseable or invalid file is an empty proof.

    An empty proof routes every algorithm to its Owner, and the reason is logged.
    """
    target = Path(path) if path is not None else Path(os.environ.get(PROOF_ENV) or _DEFAULT_PROOF)
    try:
        text = _read_proof_file(target)
    except FileNotFoundError:
        logger.warning("Arno reader proof %s not found: every algorithm uses its Owner", target)
        return Proof.empty()
    except OSError as exc:
        logger.warning("Arno reader proof %s unreadable (%s): every algorithm uses its Owner", target, exc)
        return Proof.empty()
    try:
        return Proof.from_dict(json.loads(text))
    except (ValueError, TypeError) as exc:
        logger.warning("Arno reader proof %s invalid (%s): every algorithm uses its Owner", target, exc)
        return Proof.empty()


@functools.lru_cache(maxsize=None)
def _default_proof(env_path: str) -> Proof:
    return load_proof(env_path or None)


def default_proof() -> Proof:
    """The process-wide proof: loaded once, keyed on ``IVG_ARNO_PROOF`` so tests can repoint it."""
    return _default_proof(os.environ.get(PROOF_ENV, ""))


def parse_report(raw: Any) -> Dict[str, int]:
    """Reader revisions from the text the library (or ``ReaderRevisions``) returned.

    Accepts the full ``{"version": ..., "readers": {...}}`` report and the bare
    ``{name: revision}`` map. Anything else is "no Native Readers", with a diagnostic.
    Only positive integer revisions survive; the version string is not read.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        logger.warning("Arno capabilities report is not JSON (%.80r): no Native Readers", text)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Arno capabilities report is not an object (%.80r): no Native Readers", text)
        return {}
    readers = parsed["readers"] if "readers" in parsed else parsed
    if not isinstance(readers, dict):
        logger.warning("Arno capabilities report has no readers map (%.80r): no Native Readers", text)
        return {}
    return {
        str(name): rev
        for name, rev in readers.items()
        if _is_int(rev) and rev >= 1
    }


def read_reader_report(source: Any) -> Dict[str, int]:
    """Ask the loaded library for its reader revisions through ``Graph.KG.ArnoAccel``.

    ``source`` is anything with ``_call_classmethod(cls, method, *args)`` (a store or
    the engine) or a raw Native API object with ``classMethodValue``. A failing call is
    an empty map, logged: the caller answers from the Owners.
    """
    try:
        if hasattr(source, "_call_classmethod"):
            raw = source._call_classmethod("Graph.KG.ArnoAccel", "ReaderRevisions")
        else:
            raw = source.classMethodValue("Graph.KG.ArnoAccel", "ReaderRevisions")
    except Exception as exc:  # noqa: BLE001 - any failure means "no readers"
        logger.warning("Arno capabilities call failed (%s): no Native Readers", exc)
        return {}
    return parse_report(raw)


def route(
    algorithm: str,
    readers: Mapping[str, int],
    proof: Proof,
    graph: Optional[str] = "",
) -> str:
    """``"native"`` or ``"owner"`` for one call. Pure; no I/O.

    Native needs all of: the algorithm is one ivg routes; the call is on the default
    graph (the readers see one adjacency with no graph dimension, FR-014a); the proof
    file has an entry for it; and the library reports a revision at or above that
    entry's minimum.
    """
    if algorithm not in IN_SCOPE:
        return OWNER
    if graph:
        return OWNER
    minimum = proof.min_revision(algorithm)
    if minimum is None:
        return OWNER
    revision = readers.get(algorithm, 0)
    if _is_int(revision) and revision >= minimum:
        return NATIVE
    return OWNER
