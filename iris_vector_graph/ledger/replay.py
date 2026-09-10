"""Client-side replay: records → GraphState, diff, reconstruction, verification (spec 213, R9).

Everything here is pure Python over an iterator of :class:`MutationRecord`, so it is
unit-testable without IRIS. The SQL helpers at the bottom read the ledger tables and
the ``Graph_KG.ledger_records`` procedure through a DB-API connection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .errors import ReconstructionTooLargeError

RESERVED_PROPERTIES = frozenset({"id", "__graph"})
RECORD_PAGE = 5_000

# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MutationRecord:
    seq: int
    ordinal: int
    op: str
    entity_kind: str
    entity_id: str
    attr: str
    prior: Optional[str]
    new: Optional[str]
    graph: Optional[str]
    flags: List[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row) -> "MutationRecord":
        (seq, ordinal, op, kind, eid, attr, prior, prior_null, new, new_null, graph, flags) = row
        flag_list = [f for f in str(flags or "").split(",") if f]
        return cls(
            seq=int(seq),
            ordinal=int(ordinal),
            op=str(op),
            entity_kind=str(kind),
            entity_id=str(eid),
            attr=str(attr or ""),
            prior=None if int(prior_null or 0) else str(prior if prior is not None else ""),
            new=None if int(new_null or 0) else str(new if new is not None else ""),
            graph=None if graph in (None, "") else str(graph),
            flags=flag_list,
        )

    @classmethod
    def from_wire(cls, d: Dict[str, Any]) -> "MutationRecord":
        return cls(
            seq=int(d["seq"]),
            ordinal=int(d["ordinal"]),
            op=str(d["op"]),
            entity_kind=str(d["entity_kind"]),
            entity_id=str(d["entity_id"]),
            attr=str(d.get("attr") or ""),
            prior=d.get("prior"),
            new=d.get("new"),
            graph=d.get("graph") or None,
            flags=list(d.get("flags") or []),
        )

    def to_wire(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ordinal": self.ordinal,
            "op": self.op,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "attr": self.attr,
            "prior": self.prior,
            "new": self.new,
            "graph": self.graph,
            "flags": list(self.flags),
        }


# --------------------------------------------------------------------------- #
# graph state (reconstruction model)
# --------------------------------------------------------------------------- #


@dataclass
class NodeState:
    labels: Set[str] = field(default_factory=set)
    props: Dict[str, str] = field(default_factory=dict)


@dataclass
class StmtState:
    s: str
    p: str
    o: str
    graph: Optional[str]
    quals: Dict[str, str] = field(default_factory=dict)

    @property
    def tuple(self) -> Tuple[str, str, str, Optional[str]]:
        return (self.s, self.p, self.o, self.graph)


def _parse_tuple(text: Optional[str]) -> Tuple[str, str, str, Optional[str]]:
    d = json.loads(text or "{}")
    g = d.get("graph")
    return (
        str(d.get("s", "")),
        str(d.get("p", "")),
        str(d.get("o", "")),
        None if g in (None, "") else str(g),
    )


class GraphState:
    """Logical structural graph: nodes (labels, props) and statements (tuple, quals)."""

    def __init__(self) -> None:
        self.nodes: Dict[str, NodeState] = {}
        self.statements: Dict[str, StmtState] = {}
        self._stmt_by_tuple: Dict[Tuple[str, str, str, Optional[str]], str] = {}

    @property
    def entity_count(self) -> int:
        return len(self.nodes) + len(self.statements)

    def stmt_for_tuple(self, t: Tuple[str, str, str, Optional[str]]) -> Optional[str]:
        return self._stmt_by_tuple.get(t)

    def apply(self, r: MutationRecord) -> None:
        op = r.op
        if r.entity_kind == "node":
            if op == "create_node":
                self.nodes[r.entity_id] = NodeState()
            elif op == "delete_node":
                self.nodes.pop(r.entity_id, None)
            else:
                node = self.nodes.get(r.entity_id)
                if node is None:
                    return
                if op == "add_label":
                    node.labels.add(r.attr)
                elif op == "remove_label":
                    node.labels.discard(r.attr)
                elif op == "set_prop":
                    node.props[r.attr] = r.new if r.new is not None else ""
                elif op == "remove_prop":
                    node.props.pop(r.attr, None)
            return
        # relationship
        if op == "create_rel":
            s, p, o, g = _parse_tuple(r.new)
            st = StmtState(s, p, o, g)
            self.statements[r.entity_id] = st
            self._stmt_by_tuple[st.tuple] = r.entity_id
        elif op == "delete_rel":
            st = self.statements.pop(r.entity_id, None)
            if st is not None:
                self._stmt_by_tuple.pop(st.tuple, None)
        else:
            st = self.statements.get(r.entity_id)
            if st is None:
                return
            if op == "set_qual":
                st.quals[r.attr] = r.new if r.new is not None else ""
            elif op == "remove_qual":
                st.quals.pop(r.attr, None)

    # -- comparison helpers --------------------------------------------------

    def as_comparable(self) -> Dict[str, Any]:
        return {
            "nodes": {
                nid: {"labels": set(n.labels), "props": dict(n.props)}
                for nid, n in self.nodes.items()
            },
            "rels": {
                st.tuple: {"quals": dict(st.quals), "stmt_id": sid}
                for sid, st in self.statements.items()
            },
        }


# --------------------------------------------------------------------------- #
# record iteration / paging
# --------------------------------------------------------------------------- #


def iter_records(
    fetch_page: Callable[[int, int], List[Tuple]], seq_from: int, seq_to: int
) -> Iterator[MutationRecord]:
    """Yield records in (seq, ordinal) order across pages.

    ``fetch_page(seq, ordinal_from)`` returns the next page of raw rows starting at
    that position (bounded by seq_to on the server side); an empty page ends iteration.
    """
    seq, ord_from = seq_from, 1
    while True:
        rows = fetch_page(seq, ord_from)
        if not rows:
            return
        for row in rows:
            rec = MutationRecord.from_row(row)
            if rec.seq > seq_to:
                return
            yield rec
        seq, ord_from = rec.seq, rec.ordinal + 1


def iter_records_sql(
    conn, schema: str, seq_from: int, seq_to: int, page_size: int = RECORD_PAGE
) -> Iterator[MutationRecord]:
    if seq_to < seq_from:
        return iter(())

    def fetch_page(seq: int, ord_from: int) -> List[Tuple]:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT * FROM {schema}.ledger_records(?, ?, ?, ?)",
                [int(seq), int(seq_to), int(ord_from), int(page_size)],
            )
            return [tuple(r) for r in cur.fetchall()]
        finally:
            cur.close()

    return iter_records(fetch_page, seq_from, seq_to)


# --------------------------------------------------------------------------- #
# history (SQL over ledger_revisions)
# --------------------------------------------------------------------------- #

_REV_COLS = (
    "seq, revision_id, parent_id, kind, actor, actor_type, conn_user, committed_ms, "
    "message, source, correlation_id, idempotency_key, op_count"
)


def _rev_from_row(row):
    from .client import RevisionInfo

    keys = [c.strip() for c in _REV_COLS.split(",")]
    return RevisionInfo.from_wire(dict(zip(keys, row)))


def fetch_revision_info(conn, schema: str, seq: int):
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_REV_COLS} FROM {schema}.ledger_revisions WHERE seq = ?", [int(seq)])
        row = cur.fetchone()
        return _rev_from_row(row) if row else None
    finally:
        cur.close()


def fetch_history(
    conn,
    schema: str,
    *,
    after_seq: int = 0,
    limit: int = 100,
    actor: Optional[str] = None,
    actor_type: Optional[str] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    descending: bool = False,
):
    from .client import HistoryPage

    where = []
    params: List[Any] = []
    if descending:
        if after_seq:
            where.append("seq < ?")
            params.append(int(after_seq))
    else:
        where.append("seq > ?")
        params.append(int(after_seq))
    if actor is not None:
        where.append("actor = ?")
        params.append(actor)
    if actor_type is not None:
        where.append("actor_type = ?")
        params.append(actor_type)
    if since_ms is not None:
        where.append("committed_ms >= ?")
        params.append(int(since_ms))
    if until_ms is not None:
        where.append("committed_ms <= ?")
        params.append(int(until_ms))
    sql = f"SELECT TOP {int(limit)} {_REV_COLS} FROM {schema}.ledger_revisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY seq " + ("DESC" if descending else "ASC")
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
    revs = [_rev_from_row(r) for r in rows]
    nxt = revs[-1].seq if len(revs) == int(limit) and revs else None
    return HistoryPage(revisions=revs, next_after_seq=nxt)


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DiffEntry:
    entity_kind: str
    entity_id: str
    attr: str  # "" existence; "label:<name>"; "prop:<key>"; "qual:<key>"
    before: Optional[str]
    after: Optional[str]

    def inverse(self) -> "DiffEntry":
        return DiffEntry(self.entity_kind, self.entity_id, self.attr, self.after, self.before)

    @property
    def rel_info(self) -> Optional[Dict[str, Any]]:
        """For relationship diff entries, return the parsed (s, p, o, graph) tuple.

        ``entity_id`` for ``entity_kind == "rel"`` entries is an opaque numeric
        statement-identity string (``^IVG.Ledger("stmt", N)``).  The human-readable
        subject, predicate, object and named graph are encoded in ``after`` for
        create_rel records and in ``before`` for delete_rel records as a JSON string
        of the form ``{"s": "...", "p": "...", "o": "...", "graph": "..."}``.

        Returns ``None`` for non-relationship entries or when both ``after`` and
        ``before`` are ``None``.
        """
        if self.entity_kind != "rel":
            return None
        src = self.after if self.after is not None else self.before
        if src is None:
            return None
        try:
            return json.loads(src)
        except Exception:
            return None

    def to_wire(self) -> Dict[str, Any]:
        return {
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "attr": self.attr,
            "before": self.before,
            "after": self.after,
        }


def _sort_key(e: DiffEntry):
    return (0 if e.entity_kind == "node" else 1, e.entity_id, e.attr)


@dataclass
class Diff:
    entries: List[DiffEntry] = field(default_factory=list)

    def inverse(self) -> "Diff":
        return Diff(sorted((e.inverse() for e in self.entries), key=_sort_key))

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_wire() for e in self.entries]

    def __len__(self) -> int:
        return len(self.entries)

    def __eq__(self, other) -> bool:
        return isinstance(other, Diff) and self.entries == other.entries


class _Lifecycle:
    """Per-entity delta tracking with lifecycle awareness (FR-029, FR-029a)."""

    __slots__ = (
        "existed_before",
        "exists_after",
        "attrs_before",
        "attrs_after",
        "created_in_range",
        "existence_prior",
        "existence_new",
    )

    def __init__(self) -> None:
        self.existed_before: Optional[bool] = None  # unknown until first record
        self.exists_after: bool = True
        self.attrs_before: Dict[str, Optional[str]] = {}
        self.attrs_after: Dict[str, Optional[str]] = {}
        self.created_in_range = False
        self.existence_prior: Optional[str] = None
        self.existence_new: Optional[str] = None


def compute_diff(records: Iterable[MutationRecord]) -> Diff:
    """Net, lifecycle-aware, totally ordered change over a record range.

    A delete followed by a re-create inside the range yields a deletion entry for the
    first lifecycle and a creation entry for the second (never a silent no-op).
    """
    entries: List[DiffEntry] = []
    # active lifecycle per (kind, id); closed lifecycles are flushed immediately
    live: Dict[Tuple[str, str], _Lifecycle] = {}

    def flush_closed(kind: str, eid: str, lc: _Lifecycle) -> None:
        # deletion entry for a lifecycle that existed before the range
        if lc.existed_before:
            entries.append(
                DiffEntry(
                    kind,
                    eid,
                    "",
                    lc.existence_prior if lc.existence_prior is not None else "",
                    None,
                )
            )
        # a lifecycle created and deleted inside the range leaves no trace

    def attr_key(op: str, attr: str) -> str:
        if op in ("add_label", "remove_label"):
            return f"label:{attr}"
        if op in ("set_prop", "remove_prop"):
            return f"prop:{attr}"
        return f"qual:{attr}"

    for r in records:
        key = (r.entity_kind, r.entity_id)
        if r.op in ("create_node", "create_rel"):
            lc = _Lifecycle()
            lc.existed_before = False
            lc.created_in_range = True
            lc.existence_new = r.new if r.new is not None else ""
            live[key] = lc
            continue
        if r.op in ("delete_node", "delete_rel"):
            lc = live.pop(key, None)
            if lc is None:
                lc = _Lifecycle()
                lc.existed_before = True
            lc.existence_prior = r.prior
            lc.exists_after = False
            flush_closed(r.entity_kind, r.entity_id, lc)
            continue
        # attribute change
        lc = live.get(key)
        if lc is None:
            lc = _Lifecycle()
            lc.existed_before = True
            live[key] = lc
        ak = attr_key(r.op, r.attr)
        if ak not in lc.attrs_before:
            lc.attrs_before[ak] = r.prior if lc.existed_before else None
        lc.attrs_after[ak] = r.new

    # flush live lifecycles
    for (kind, eid), lc in live.items():
        if lc.created_in_range:
            entries.append(
                DiffEntry(
                    kind, eid, "", None, lc.existence_new if lc.existence_new is not None else ""
                )
            )
            for ak, after in lc.attrs_after.items():
                if after is not None:
                    entries.append(DiffEntry(kind, eid, ak, None, after))
        else:
            for ak, after in lc.attrs_after.items():
                before = lc.attrs_before.get(ak)
                if before != after:
                    entries.append(DiffEntry(kind, eid, ak, before, after))
    entries.sort(key=_sort_key)
    return Diff(entries)


# --------------------------------------------------------------------------- #
# reconstruction
# --------------------------------------------------------------------------- #


def reconstruct(records: Iterable[MutationRecord], bound: Optional[int], stream: bool = False):
    """Fold records into a GraphState. Enforces the entity bound (FR-036).

    stream=False → returns GraphState, raising ReconstructionTooLargeError above the bound.
    stream=True  → returns an iterator of ("node", id, NodeState) / ("rel", stmt_id, StmtState).
    """
    state = GraphState()
    peak = 0
    for r in records:
        state.apply(r)
        peak = max(peak, state.entity_count)
        if bound is not None and not stream and state.entity_count > bound:
            raise ReconstructionTooLargeError(bound=bound, estimate=state.entity_count)
    if not stream:
        return state

    def _gen():
        for nid, ns in state.nodes.items():
            yield ("node", nid, ns)
        for sid, st in state.statements.items():
            yield ("rel", sid, st)

    return _gen()


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #


@dataclass
class VerificationReport:
    verified_head: str
    verified_seq: int
    result: str  # "equal" | "diverges"
    differences: List[DiffEntry] = field(default_factory=list)
    classification: Optional[str] = None  # "unrecorded_writes" | "ledger_inconsistency"
    adoption_revision: Any = None

    @property
    def equal(self) -> bool:
        return self.result == "equal"


def read_canonical(
    conn, schema: str, tuple_stmt_lookup: Optional[Callable[[Tuple], Optional[str]]] = None
) -> GraphState:
    """Read the governed graph from the canonical tables (reserved props excluded, FR-014b).

    Statement ids come from the ledger's tuple index when a lookup is supplied; live
    edges without an identity get a synthetic key ``tuple:<json>``.
    """
    st = GraphState()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT node_id FROM {schema}.nodes")
        for (nid,) in cur.fetchall():
            st.nodes[nid] = NodeState()
        cur.execute(f"SELECT s, label FROM {schema}.rdf_labels")
        for s, label in cur.fetchall():
            st.nodes.setdefault(s, NodeState()).labels.add(label)
        cur.execute(f'SELECT s, "key", val FROM {schema}.rdf_props')
        for s, key, val in cur.fetchall():
            if key in RESERVED_PROPERTIES:
                continue
            st.nodes.setdefault(s, NodeState()).props[key] = "" if val is None else str(val)
        cur.execute(f"SELECT s, p, o_id, graph_id, qualifiers FROM {schema}.rdf_edges")
        for s, p, o, g, quals in cur.fetchall():
            g = None if g in (None, "") else str(g)
            q: Dict[str, str] = {}
            if quals:
                try:
                    parsed = json.loads(quals) if isinstance(quals, str) else dict(quals)
                    for k, v in parsed.items():
                        if v is None:
                            continue
                        q[str(k)] = (
                            ("true" if v else "false")
                            if isinstance(v, bool)
                            else (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                        )
                except Exception:
                    q = {}
            t = (s, p, o, g)
            sid = tuple_stmt_lookup(t) if tuple_stmt_lookup else None
            if not sid:
                sid = "tuple:" + json.dumps(
                    {"graph": g, "o": o, "p": p, "s": s}, sort_keys=True, separators=(",", ":")
                )
            stx = StmtState(s, p, o, g, q)
            st.statements[sid] = stx
            st._stmt_by_tuple[t] = sid
    finally:
        cur.close()
    return st


def compare(replayed: GraphState, canonical: GraphState) -> Tuple[List[DiffEntry], Optional[str]]:
    """Differences from replayed → canonical, with a classification.

    Anything present in the canonical tables that the ledger did not record (or absent
    though the ledger says it exists) is an unrecorded write. Structural disagreements
    inside the ledger itself are reported by the caller as ledger_inconsistency.
    """
    entries: List[DiffEntry] = []
    a, b = replayed.as_comparable(), canonical.as_comparable()
    for nid in sorted(set(a["nodes"]) | set(b["nodes"])):
        na, nb = a["nodes"].get(nid), b["nodes"].get(nid)
        if na is None:
            entries.append(DiffEntry("node", nid, "", None, ""))
            for lbl in sorted(nb["labels"]):
                entries.append(DiffEntry("node", nid, f"label:{lbl}", None, ""))
            for k, v in sorted(nb["props"].items()):
                entries.append(DiffEntry("node", nid, f"prop:{k}", None, v))
            continue
        if nb is None:
            entries.append(DiffEntry("node", nid, "", "", None))
            continue
        for lbl in sorted(na["labels"] ^ nb["labels"]):
            entries.append(
                DiffEntry(
                    "node",
                    nid,
                    f"label:{lbl}",
                    "" if lbl in na["labels"] else None,
                    "" if lbl in nb["labels"] else None,
                )
            )
        for k in sorted(set(na["props"]) | set(nb["props"])):
            va, vb = na["props"].get(k), nb["props"].get(k)
            if va != vb:
                entries.append(DiffEntry("node", nid, f"prop:{k}", va, vb))
    for t in sorted(set(a["rels"]) | set(b["rels"]), key=lambda x: (x[0], x[1], x[2], x[3] or "")):
        ra, rb = a["rels"].get(t), b["rels"].get(t)
        tid = json.dumps(
            {"graph": t[3], "o": t[2], "p": t[1], "s": t[0]}, sort_keys=True, separators=(",", ":")
        )
        if ra is None:
            entries.append(DiffEntry("rel", tid, "", None, tid))
            for k, v in sorted(rb["quals"].items()):
                entries.append(DiffEntry("rel", tid, f"qual:{k}", None, v))
            continue
        if rb is None:
            entries.append(DiffEntry("rel", ra["stmt_id"], "", tid, None))
            continue
        for k in sorted(set(ra["quals"]) | set(rb["quals"])):
            va, vb = ra["quals"].get(k), rb["quals"].get(k)
            if va != vb:
                entries.append(DiffEntry("rel", ra["stmt_id"], f"qual:{k}", va, vb))
    entries.sort(key=_sort_key)
    return entries, ("unrecorded_writes" if entries else None)


def _adoption_items(entries: List[DiffEntry], replayed: GraphState) -> List[Dict[str, Any]]:
    """Translate compare() entries into Graph.KG.LedgerGenesis.Adopt items."""
    items: List[Dict[str, Any]] = []
    for e in entries:
        if e.entity_kind == "node":
            if e.attr == "":
                op = "create_node" if e.before is None else "delete_node"
                items.append(
                    {
                        "op": op,
                        "entity_kind": "node",
                        "entity_id": e.entity_id,
                        "attr": "",
                        "prior": e.before,
                        "new": e.after,
                        "graph": None,
                    }
                )
            elif e.attr.startswith("label:"):
                op = "add_label" if e.after is not None else "remove_label"
                items.append(
                    {
                        "op": op,
                        "entity_kind": "node",
                        "entity_id": e.entity_id,
                        "attr": e.attr[6:],
                        "prior": e.before,
                        "new": e.after,
                        "graph": None,
                    }
                )
            elif e.attr.startswith("prop:"):
                op = "set_prop" if e.after is not None else "remove_prop"
                items.append(
                    {
                        "op": op,
                        "entity_kind": "node",
                        "entity_id": e.entity_id,
                        "attr": e.attr[5:],
                        "prior": e.before,
                        "new": e.after,
                        "graph": None,
                    }
                )
        else:
            if e.attr == "":
                if (
                    e.before is None
                ):  # unrecorded live relationship → create_rel with tuple JSON in "new"
                    g = json.loads(e.after).get("graph")
                    items.append(
                        {
                            "op": "create_rel",
                            "entity_kind": "rel",
                            "entity_id": "",
                            "attr": "",
                            "prior": None,
                            "new": e.after,
                            "graph": g,
                        }
                    )
                else:  # ledger statement whose row disappeared
                    st = replayed.statements.get(e.entity_id)
                    items.append(
                        {
                            "op": "delete_rel",
                            "entity_kind": "rel",
                            "entity_id": e.entity_id,
                            "attr": "",
                            "prior": e.before,
                            "new": None,
                            "graph": st.graph if st else None,
                        }
                    )
            elif e.attr.startswith("qual:"):
                op = "set_qual" if e.after is not None else "remove_qual"
                st = replayed.statements.get(e.entity_id)
                items.append(
                    {
                        "op": op,
                        "entity_kind": "rel",
                        "entity_id": e.entity_id,
                        "attr": e.attr[5:],
                        "prior": e.before,
                        "new": e.after,
                        "graph": st.graph if st else None,
                    }
                )
    return items


def verify(
    ledger, *, adopt: bool = False, actor: str = "system:ledger-verify"
) -> VerificationReport:
    from .changeset import Changeset

    head = ledger.head()
    conn = ledger._engine.conn
    schema = ledger._schema()
    replayed = reconstruct(iter_records_sql(conn, schema, 1, head.seq), bound=None, stream=False)

    def lookup(t):
        s, p, o, g = t
        try:
            v = ledger._iris().classMethodValue("Graph.KG.Ledger", "StmtForTuple", s, p, o, g or "")
            return str(v) if str(v) else None
        except Exception:
            return None

    canonical = read_canonical(conn, schema, lookup)
    entries, classification = compare(replayed, canonical)
    result = "equal" if not entries else "diverges"
    try:
        ledger._call("RecordVerify", result)
    except Exception:
        pass
    report = VerificationReport(
        verified_head=head.revision_id,
        verified_seq=head.seq,
        result=result,
        differences=entries,
        classification=classification,
    )
    if adopt and entries:
        cs = Changeset(
            actor=actor,
            actor_type="system",
            message=f"adoption of {len(entries)} unrecorded change(s) observed at {head.revision_id}",
        )
        cs.ops = _adoption_items(entries, replayed)
        res = ledger.commit(cs, kind="adoption")
        report.adoption_revision = res.revision
    return report


__all__ = [
    "MutationRecord",
    "NodeState",
    "StmtState",
    "GraphState",
    "iter_records",
    "iter_records_sql",
    "fetch_history",
    "fetch_revision_info",
    "DiffEntry",
    "Diff",
    "compute_diff",
    "reconstruct",
    "VerificationReport",
    "read_canonical",
    "compare",
    "verify",
]
