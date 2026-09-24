"""Spec 232: the json_links grammar for FHIR graphs (research R5).

A link entry names a link the repository's search index cannot see:

- ``Type.element``: a top-level canonical, or array of canonicals, on that type;
- ``extension:<url>``: every extension with that url at any depth, taking its
  ``valueCanonical`` or ``valueReference``.

``Graph.KG.FHIRGraph.ParseJsonLinks`` is authoritative. This module checks the same
grammar before the round trip, as ``_DENY_ENTRY`` does for the denylist, and both
are driven by ``tests/fixtures/fhir_json_links_cases.json``.
"""

from __future__ import annotations

import re
from typing import Any, List

DEFAULT_EXTENSIONS: tuple[str, ...] = (
    "extension:http://hl7.org/fhir/StructureDefinition/cqf-library",
)

_ELEMENT = re.compile(r"[A-Z][A-Za-z]+\.[a-z][A-Za-z0-9]*")
# An absolute http(s) or urn url with no whitespace whose last segment is non-empty:
# that segment is the edge predicate.
_EXTENSION = re.compile(r"extension:(?:https?://[^\s/]\S*|urn:\S+)")
_SEGMENT = re.compile(r"[^/:]+$")


def _bad(entry: str) -> ValueError:
    return ValueError(f"json link '{entry}' is not 'Type.element' or 'extension:<url>'")


def link_predicate(entry: str) -> str:
    """'PlanDefinition.library' -> 'library'; 'extension:http://x/y/cqf-library' -> 'cqf-library'."""
    if entry.startswith("extension:"):
        match = _SEGMENT.search(entry[len("extension:") :])
        if not match:
            raise _bad(entry)
        return match.group(0)
    return entry.split(".", 1)[1]


def parse_json_links(entries: Any) -> List[str]:
    """Validate link entries (research R5). Returns them unchanged and in order.

    Raises ValueError naming the first bad entry, the first duplicate, or the first
    pair that gives the same predicate. Two element entries on different types may
    share a predicate (``PlanDefinition.library``, ``Measure.library``); an extension
    applies to every type, so it may share a predicate with nothing.
    """
    if not isinstance(entries, (list, tuple)) or not all(isinstance(e, str) for e in entries):
        raise ValueError("json links must be a JSON array of strings")
    seen: set = set()
    by_predicate: dict = {}
    for entry in entries:
        is_ext = entry.startswith("extension:")
        pattern = _EXTENSION if is_ext else _ELEMENT
        if not pattern.fullmatch(entry) or (is_ext and not _SEGMENT.search(entry[10:])):
            raise _bad(entry)
        if entry in seen:
            raise ValueError(f"json link '{entry}' is listed twice")
        seen.add(entry)
        predicate = link_predicate(entry)
        for other, other_is_ext in by_predicate.get(predicate, []):
            if is_ext or other_is_ext:
                raise ValueError(
                    f"json links '{other}' and '{entry}' both give predicate '{predicate}'"
                )
        by_predicate.setdefault(predicate, []).append((entry, is_ext))
    return list(entries)
