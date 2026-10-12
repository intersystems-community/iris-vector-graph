"""A test oracle for the derived ``^KG`` stores, in both directions.

``Graph_KG.rdf_edges`` is authoritative. ``^KG("out")``, ``^KG("in")``, ``^KG("deg")`` and ``^KG("degp")`` are derived
from it. Earlier checks recounted from the rows and compared per source, so a store entry whose source had no rows left
was never visited. That is how the ghost counters of spike RISK R9 went unseen: a counter for a source with zero rows
looked like "0 mismatches". This oracle walks the derived stores themselves, so a key with no row behind it is a
violation in its own right, and it also looks for rows with no store entry.

Layouts (graph key ``g`` is the first subscript after the store name, ADR-0001; the default graph's key is ``0``):

    ^KG("out", g, s, p, o)        adjacency, outbound
    ^KG("in", g, o, p, s)         mirror, subscripted (o, p, s)
    ^KG("deg", g, s)              out-degree of s, counting rows
    ^KG("degp", g, s, p)          out-degree of s for predicate p, counting rows
    ^KG("out"|"in", g, s, p, o, ekey)  per-edge children of a multigraph triple; "#" is a live count, not an edge

The oracle is pure Python over data. It reads through an injected host (``get`` / ``next_subscript``, the interface of
``iris_vector_graph.host``) and an injected DB-API connection (``rows()`` only). Nothing here opens a connection.

Documented limits: subscripts are compared as strings (IRIS returns canonical numerics as numbers, and ``str`` of
either form is the same text); the multigraph ekey level is checked for ghosts only, not for missing children.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

GLOBAL = "^KG"

#: Every kind the oracle can report.
KINDS: FrozenSet[str] = frozenset(
    {
        "ghost_out",
        "ghost_in",
        "missing_out",
        "missing_in",
        "out_in_mismatch",
        "ghost_deg",
        "deg_mismatch",
        "degp_mismatch",
    }
)

#: The adjacency kinds. These must always be empty: ``out`` and ``in`` are written in the same transaction as the row.
ADJACENCY_KINDS: FrozenSet[str] = frozenset(
    {"ghost_out", "ghost_in", "missing_out", "missing_in", "out_in_mismatch"}
)

#: The advisory counters. ``$Increment`` is not undone by ``TROLLBACK`` (RISK R1), so these may drift. This is the
#: set a lenient run allows; a strict run allows nothing.
ADVISORY_KINDS: FrozenSet[str] = frozenset({"ghost_deg", "deg_mismatch", "degp_mismatch"})


@dataclass(frozen=True)
class Violation:
    """One disagreement between a derived store and the rows.

    ``kind`` is one of :data:`KINDS`; ``store`` is ``"out"``, ``"in"``, ``"deg"`` or ``"degp"``; ``key`` is the subscript
    tuple in that store's own layout (graph key first). For ``missing_*`` the key is the store entry that should exist.
    """

    kind: str
    store: str
    key: Tuple[Any, ...]


@dataclass
class Walk:
    """What the derived stores hold, read by walking them. Keys are strings; counters map key to stored value."""

    out: Set[Tuple[str, ...]] = field(default_factory=set)  # (g, s, p, o)
    in_: Set[Tuple[str, ...]] = field(default_factory=set)  # (g, o, p, s), the in layout
    out_ekeys: Set[Tuple[str, ...]] = field(default_factory=set)  # (g, s, p, o, ekey)
    in_ekeys: Set[Tuple[str, ...]] = field(default_factory=set)  # (g, o, p, s, ekey)
    deg: Dict[Tuple[str, ...], Any] = field(default_factory=dict)  # (g, s) -> value
    degp: Dict[Tuple[str, ...], Any] = field(default_factory=dict)  # (g, s, p) -> value


def graph_key(graph_id: Any) -> str:
    """The graph key the engine writes for a SQL ``graph_id``.

    NULL, the empty string and ``$Char(0)`` padding are the default graph, key ``0``
    (``Graph.KG.GraphKey.ForIndex``). Anything else is the name itself.
    """
    if graph_id is None:
        return "0"
    name = str(graph_id).replace("\x00", "")
    return "0" if name == "" else name


def _sub(value: Any) -> str:
    return str(value)


class DerivedOracle:
    """Compare the derived ``^KG`` stores with ``Graph_KG.rdf_edges``.

    ``host`` needs ``get(name, *subs)`` and ``next_subscript(name, *subs, reverse=False)``. ``conn`` needs ``cursor()``.
    ``multigraph`` reads ``ekey`` and checks the per-edge children. Off, an edge has no ekey level.
    """

    def __init__(self, host: Any, conn: Any, *, multigraph: bool = False) -> None:
        self._host = host
        self._conn = conn
        self._multigraph = multigraph
        self.last_sql: Optional[str] = None

    # ------------------------------------------------------------------ the rows

    def rows(self) -> List[Tuple[str, str, str, str, int]]:
        """Every edge row as ``(graph_key, s, p, o, ekey)``. ``ekey`` is 0 when the table is not multigraph."""
        ekey_sql = "ekey" if self._multigraph else "0"
        sql = (
            "SELECT COALESCE(graph_id, ''), s, p, o_id, " + ekey_sql + " FROM Graph_KG.rdf_edges"
        )
        self.last_sql = sql
        cursor = self._conn.cursor()
        cursor.execute(sql)
        out = []
        for graph_id, s, p, o, ekey in cursor.fetchall() or []:
            out.append((graph_key(graph_id), _sub(s), _sub(p), _sub(o), int(ekey or 0)))
        return out

    # ------------------------------------------------------------------ the stores

    def _children(self, *prefix: Any) -> Iterator[Any]:
        """Each child subscript under ``prefix``, in collation order, with its raw value (not stringified)."""
        last: Any = ""
        while True:
            nxt = self._host.next_subscript(GLOBAL, *prefix, last)
            if nxt is None or (isinstance(nxt, str) and nxt == ""):
                return
            yield nxt
            last = nxt

    def walk(self) -> Walk:
        """Read every derived store by walking it. Nothing is inferred from the rows."""
        w = Walk()
        for g in self._children("out"):
            for s in self._children("out", g):
                for p in self._children("out", g, s):
                    for o in self._children("out", g, s, p):
                        gk = _sub(g)
                        w.out.add((gk, _sub(s), _sub(p), _sub(o)))
                        for k in self._children("out", g, s, p, o):
                            if _sub(k) != "#":
                                w.out_ekeys.add((gk, _sub(s), _sub(p), _sub(o), _sub(k)))
        for g in self._children("in"):
            for o in self._children("in", g):
                for p in self._children("in", g, o):
                    for s in self._children("in", g, o, p):
                        gk = _sub(g)
                        w.in_.add((gk, _sub(o), _sub(p), _sub(s)))
                        for k in self._children("in", g, o, p, s):
                            if _sub(k) != "#":
                                w.in_ekeys.add((gk, _sub(o), _sub(p), _sub(s), _sub(k)))
        for g in self._children("deg"):
            for s in self._children("deg", g):
                w.deg[(_sub(g), _sub(s))] = self._host.get(GLOBAL, "deg", g, s)
        for g in self._children("degp"):
            for s in self._children("degp", g):
                for p in self._children("degp", g, s):
                    w.degp[(_sub(g), _sub(s), _sub(p))] = self._host.get(GLOBAL, "degp", g, s, p)
        return w

    # ------------------------------------------------------------- the comparison

    def violations(self) -> List[Violation]:
        """Every disagreement between the stores and the rows, in a stable order.

        One stray entry can be reported under more than one kind: an out entry with no row and no mirror is both
        ``ghost_out`` and ``out_in_mismatch``.
        """
        rows = self.rows()
        w = self.walk()

        exp_out: Set[Tuple[str, ...]] = set()
        exp_in: Set[Tuple[str, ...]] = set()
        exp_ekeys: Set[Tuple[str, ...]] = set()
        exp_deg: Counter = Counter()
        exp_degp: Counter = Counter()
        for g, s, p, o, ekey in rows:
            exp_out.add((g, s, p, o))
            exp_in.add((g, o, p, s))
            exp_ekeys.add((g, s, p, o, str(ekey)))
            exp_deg[(g, s)] += 1
            exp_degp[(g, s, p)] += 1

        out: List[Violation] = []
        for key in sorted(w.out - exp_out):
            out.append(Violation("ghost_out", "out", key))
        for key in sorted(exp_out - w.out):
            out.append(Violation("missing_out", "out", key))
        for key in sorted(w.in_ - exp_in):
            out.append(Violation("ghost_in", "in", key))
        for key in sorted(exp_in - w.in_):
            out.append(Violation("missing_in", "in", key))

        # Mirror disagreement, compared in the out layout. Reported on the side that holds the entry.
        in_as_out = {(g, s, p, o) for (g, o, p, s) in w.in_}
        out_only_key = sorted(w.out - in_as_out)
        in_only_key = sorted(in_as_out - w.out)
        for g, s, p, o in out_only_key:
            out.append(Violation("out_in_mismatch", "out", (g, s, p, o)))
        for g, s, p, o in in_only_key:
            out.append(Violation("out_in_mismatch", "in", (g, o, p, s)))

        if self._multigraph:
            for key in sorted(w.out_ekeys - exp_ekeys):
                out.append(Violation("ghost_out", "out", key))
            for g, o, p, s, ekey in sorted(w.in_ekeys):
                if (g, s, p, o, ekey) not in exp_ekeys:
                    out.append(Violation("ghost_in", "in", (g, o, p, s, ekey)))

        for key in sorted(w.deg.keys() - exp_deg.keys()):
            out.append(Violation("ghost_deg", "deg", key))
        for key in sorted(exp_deg):
            if key not in w.deg or _as_int(w.deg[key]) != exp_deg[key]:
                out.append(Violation("deg_mismatch", "deg", key))

        for key in sorted(w.degp.keys() - exp_degp.keys()):
            out.append(Violation("degp_mismatch", "degp", key))
        for key in sorted(exp_degp):
            if key not in w.degp or _as_int(w.degp[key]) != exp_degp[key]:
                out.append(Violation("degp_mismatch", "degp", key))

        return out


def _as_int(value: Any) -> Optional[int]:
    """A stored counter as an int. None when it is not a number (the oracle then reports a mismatch)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
