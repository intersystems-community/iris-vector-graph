"""Keys and admission rules for the shared algorithm result cache (spec 236, Story 4).

The storage is ``Graph.KG.AlgoCache`` (``^IVG.AlgoCache``); this module decides what may
go in and names it. The graph is passed by name: ``Graph.KG.GraphKey`` owns the subscript
derivation (ADR-0003), so nothing here re-derives it.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, List, Mapping, Optional

#: Graph-wide answers the token covers. ``subgraph`` reads live SQL ``rdf_labels``, which
#: no token moves for, so it is never cached (research-token-audit.md, self-bypass).
CACHEABLE = frozenset({"pagerank", "ppr", "wcc", "cdlp"})

#: A payload above this is computed every time. Keeps one entry well inside IRIS's
#: string limit and a hit cheap.
MAX_PAYLOAD_CHARS = 1_000_000


#: The process-private layer in front of the shared cache only holds results at least this
#: large: below it a shared hit costs within 1.3x of the token check alone (measured), so
#: the layer would add a round trip on every miss for nothing. FR-021.
FRONT_MIN_CHARS = 20_000
FRONT_MAX_ENTRIES = 8


def enabled() -> bool:
    return os.environ.get("IVG_DISABLE_ALGO_CACHE", "").strip() != "1"


def graph_name(graph: Optional[str]) -> str:
    return graph or ""


def _canonical(value: Any) -> Any:
    """Numbers compare by value (``20`` and ``20.0`` are one parameter); strings stay strings."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def param_hash(params: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of ``params`` (sorted keys, no whitespace)."""
    text = json.dumps(_canonical(params), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seed_key(seeds: Iterable[str]) -> List[str]:
    """Seeds are a set to every algorithm here, so the key sorts and de-duplicates them."""
    return sorted(set(seeds))


def cacheable(algorithm: str, payload: str) -> bool:
    return enabled() and algorithm in CACHEABLE and len(payload) <= MAX_PAYLOAD_CHARS
