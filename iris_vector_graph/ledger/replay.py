"""Client-side replay: records → GraphState, diff, reconstruction, verification (spec 213, R9).

Everything here is pure Python over an iterator of :class:`MutationRecord`, so it is
unit-testable without IRIS. The SQL helpers at the bottom read the ledger tables and
the ``Graph_KG.ledger_records`` procedure through a DB-API connection.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .errors import ReconstructionTooLargeError

RESERVED_PROPERTIES = frozenset({"id", "__graph"})
RECORD_PAGE = 5_000
# Spec 256: the op of a tombstone, and the entity kind of a redaction revision's records.
REDACTED_OP = "redacted"
REVISION_KIND = "revision"
REDACTION_IN_PROGRESS = "redaction_in_progress"


def contributes(r: "MutationRecord") -> bool:
    """False for records that carry no graph content: tombstones and redaction-audit records."""
    return not (r.redacted or r.entity_kind == REVISION_KIND)


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
    # Spec 256: a tombstone has op "redacted", no id, attribute, values or graph, the
    # SHA-256 of the canonical record it replaced, and that record's op.
    digest: Optional[str] = None
    redacted_op: Optional[str] = None

    @property
    def redacted(self) -> bool:
        return self.op == REDACTED_OP

    @classmethod
    def from_row(cls, row) -> "MutationRecord":
        # 14 columns since spec 256 (digest, redacted_op); a server without it sends 12.
        (seq, ordinal, op, kind, eid, attr, prior, prior_null, new, new_null, graph, flags) = row[:12]
        digest, redacted_op = (row[12], row[13]) if len(row) >= 14 else (None, None)
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
            graph=_graph_key(graph),
            flags=flag_list,
            digest=str(digest) if digest else None,
            redacted_op=str(redacted_op) if redacted_op else None,
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
            digest=d.get("digest") or None,
            redacted_op=d.get("redacted_op") or None,
        )

    def to_wire(self) -> Dict[str, Any]:
        wire = {
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
        if self.redacted:
            wire["digest"] = self.digest
            wire["redacted_op"] = self.redacted_op
        return wire


# --------------------------------------------------------------------------- #
# graph state (reconstruction model)
# --------------------------------------------------------------------------- #


@dataclass
class NodeState:
    labels: Set[str] = field(default_factory=set)
    props: Dict[str, str] = field(default_factory=dict)
    # A default-graph node the replay made only because a ledger write put a label or property in the default graph
    # for a node that only a named graph holds (NodeExists reads every graph, the write is the default graph's).
    implicit: bool = field(default=False, compare=False, repr=False)


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


#: The default graph's spellings: None, "", and $Char(0), which the genesis writes and IRIS stores for SQL's ''.
_DEFAULT_GRAPH = (None, "", "\x00")


def _graph_key(g: Optional[str]) -> Optional[str]:
    """A statement's graph as the comparison keys it: None for the default graph, however it was spelled."""
    return None if g in _DEFAULT_GRAPH else str(g)


def _parse_tuple(text: Optional[str]) -> Tuple[str, str, str, Optional[str]]:
    d = json.loads(text or "{}")
    return (
        str(d.get("s", "")),
        str(d.get("p", "")),
        str(d.get("o", "")),
        _graph_key(d.get("graph")),
    )


class GraphState:
    """Logical structural graph: nodes (labels, props) and statements (tuple, quals).

    ``nodes`` holds the default graph's nodes by id. A named graph's node is ``graph_nodes[(graph, id)]``: the same id
    in two graphs is two nodes, as it is in the tables (4.7.2; before it the replay kept one node per id).
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, NodeState] = {}
        self.graph_nodes: Dict[Tuple[str, str], NodeState] = {}
        self.statements: Dict[str, StmtState] = {}
        self._stmt_by_tuple: Dict[Tuple[str, str, str, Optional[str]], str] = {}
        self._named_ids: Dict[str, int] = {}
        # (graph, id) of every endpoint of an edge row in a named graph (read_canonical) and, in a replay, of every
        # statement ever created there, deleted or not (apply): the node rows those edges needed. See compare().
        self.implied_endpoints: Set[Tuple[str, str]] = set()

    @property
    def entity_count(self) -> int:
        return len(self.nodes) + len(self.graph_nodes) + len(self.statements)

    def stmt_for_tuple(self, t: Tuple[str, str, str, Optional[str]]) -> Optional[str]:
        return self._stmt_by_tuple.get(t)

    def blind_nodes(self) -> Dict[str, NodeState]:
        """One node per id, the graphs merged: the default graph's value wins, a named graph adds what it has.

        What the export lists (its node lines carry no graph) and what ``nodes`` held before 4.7.2.
        """
        if not self.graph_nodes:
            return self.nodes
        out = {nid: NodeState(set(ns.labels), dict(ns.props)) for nid, ns in self.nodes.items()}
        for (_g, nid), ns in sorted(self.graph_nodes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            cur = out.setdefault(nid, NodeState())
            cur.labels |= ns.labels
            for k, v in ns.props.items():
                cur.props.setdefault(k, v)
        return out

    def _set_named(self, graph: str, nid: str, node: NodeState) -> None:
        if (graph, nid) not in self.graph_nodes:
            self._named_ids[nid] = self._named_ids.get(nid, 0) + 1
        self.graph_nodes[(graph, nid)] = node

    def _drop_named(self, graph: str, nid: str) -> None:
        if self.graph_nodes.pop((graph, nid), None) is not None:
            left = self._named_ids.get(nid, 1) - 1
            if left > 0:
                self._named_ids[nid] = left
            else:
                self._named_ids.pop(nid, None)

    def apply(self, r: MutationRecord) -> None:
        if not contributes(r):
            return
        op = r.op
        if r.entity_kind == "node":
            graph = _graph_key(r.graph)
            if op == "create_node":
                if graph is None:
                    self.nodes[r.entity_id] = NodeState()
                else:
                    self._set_named(graph, r.entity_id, NodeState())
            elif op == "delete_node":
                if graph is None:
                    self.nodes.pop(r.entity_id, None)
                else:
                    self._drop_named(graph, r.entity_id)
            else:
                node = self.nodes.get(r.entity_id) if graph is None else self.graph_nodes.get((graph, r.entity_id))
                if node is None and graph is None and r.entity_id in self._named_ids:
                    # a ledger write to the default graph of a node only a named graph holds: orphan rows
                    node = self.nodes[r.entity_id] = NodeState(implicit=True)
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
                if node.implicit and not node.labels and not node.props:
                    del self.nodes[r.entity_id]
            return
        # relationship
        if op == "create_rel":
            s, p, o, g = _parse_tuple(r.new)
            if g is not None:
                self.implied_endpoints.update(((g, s), (g, o)))
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
                **{("", nid): {"labels": set(n.labels), "props": dict(n.props)} for nid, n in self.nodes.items()},
                **{
                    (g, nid): {"labels": set(n.labels), "props": dict(n.props)}
                    for (g, nid), n in self.graph_nodes.items()
                },
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
    correlation_id: Optional[str] = None,
    source: Optional[str] = None,
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
    if correlation_id is not None:
        where.append("correlation_id = ?")
        params.append(correlation_id)
    if source is not None:
        where.append("source = ?")
        params.append(source)
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
    # A node's graph (4.7.2): None is the default graph. A relationship names its graph in its tuple, not here.
    graph: Optional[str] = None

    def inverse(self) -> "DiffEntry":
        return DiffEntry(self.entity_kind, self.entity_id, self.attr, self.after, self.before, self.graph)

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
        wire = {
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "attr": self.attr,
            "before": self.before,
            "after": self.after,
        }
        if self.graph is not None:
            wire["graph"] = self.graph
        return wire


def _sort_key(e: DiffEntry):
    return (0 if e.entity_kind == "node" else 1, e.entity_id, e.attr, e.graph or "")


@dataclass
class Diff:
    entries: List[DiffEntry] = field(default_factory=list)
    # Spec 256 FR-003: the (seq, ordinal) of every tombstone in the range. A tombstone
    # contributes no entry, and is listed here rather than silently skipped.
    redacted: List[Tuple[int, int]] = field(default_factory=list)

    def inverse(self) -> "Diff":
        return Diff(sorted((e.inverse() for e in self.entries), key=_sort_key), list(self.redacted))

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
    live: Dict[Tuple[str, str, Optional[str]], _Lifecycle] = {}

    def flush_closed(kind: str, eid: str, graph: Optional[str], lc: _Lifecycle) -> None:
        # deletion entry for a lifecycle that existed before the range
        if lc.existed_before:
            entries.append(
                DiffEntry(
                    kind,
                    eid,
                    "",
                    lc.existence_prior if lc.existence_prior is not None else "",
                    None,
                    graph,
                )
            )
        # a lifecycle created and deleted inside the range leaves no trace

    def attr_key(op: str, attr: str) -> str:
        if op in ("add_label", "remove_label"):
            return f"label:{attr}"
        if op in ("set_prop", "remove_prop"):
            return f"prop:{attr}"
        return f"qual:{attr}"

    redacted: List[Tuple[int, int]] = []
    for r in records:
        if r.redacted:
            redacted.append((r.seq, r.ordinal))
            continue
        if not contributes(r):
            continue
        # a node is per graph; a relationship's record graph is already part of its identity
        graph = _graph_key(r.graph) if r.entity_kind == "node" else None
        key = (r.entity_kind, r.entity_id, graph)
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
            flush_closed(r.entity_kind, r.entity_id, graph, lc)
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
    for (kind, eid, graph), lc in live.items():
        if lc.created_in_range:
            entries.append(
                DiffEntry(
                    kind, eid, "", None, lc.existence_new if lc.existence_new is not None else "", graph
                )
            )
            for ak, after in lc.attrs_after.items():
                if after is not None:
                    entries.append(DiffEntry(kind, eid, ak, None, after, graph))
        else:
            for ak, after in lc.attrs_after.items():
                before = lc.attrs_before.get(ak)
                if before != after:
                    entries.append(DiffEntry(kind, eid, ak, before, after, graph))
    entries.sort(key=_sort_key)
    return Diff(entries, redacted)


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
        for nid, ns in state.blind_nodes().items():
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
    result: str  # "equal" | "diverges" | "redaction_in_progress" (spec 256)
    differences: List[DiffEntry] = field(default_factory=list)
    classification: Optional[str] = None  # "unrecorded_writes" | "ledger_inconsistency"
    adoption_revision: Any = None

    @property
    def equal(self) -> bool:
        return self.result == "equal"


class _TableValue(str):
    """A qualifier's text as the tables hold it, with the JSON value it was read from.

    The genesis (ObjectScript) spells a JSON number the way IRIS does (``.8``, ``-.25``, ``1`` for 1.0, ``.00001``,
    ``10000000000000000000000`` for 1e22) and a nested value in IRIS's compact form; Python spells them ``0.8``,
    ``-0.25``, ``1.0``, ``1e-05``, ``1e+22``. The comparison asks :func:`_qual_same`, which reads the ledger's text
    as the value it names, so a graph bulk-loaded with a fractional weight does not diverge with nothing written.
    The text itself (what a difference reports, and what an adoption records) stays as Python spells it.
    """

    __slots__ = ("value",)

    def __new__(cls, text: str, value: Any):
        obj = super().__new__(cls, text)
        obj.value = value
        return obj


_NUMBER_TEXT = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _qualifier_text(v: Any) -> str:
    """A table qualifier's JSON value as the comparison's text (a boolean is ``true``/``false``, as genesis writes it)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return _TableValue(json.dumps(v, separators=(",", ":"), ensure_ascii=False), v)
    if isinstance(v, int) or (isinstance(v, float) and math.isfinite(v)):
        return _TableValue(str(v), v)
    return str(v)


def _json_equal(a: Any, b: Any) -> bool:
    """JSON equality: 1 equals 1.0, true is not 1, and key order does not matter."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _qual_same(ledger: Optional[str], table: Optional[str]) -> bool:
    """Whether the ledger's qualifier text and the tables' name the same value."""
    if ledger == table:
        return True
    if ledger is None or not isinstance(table, _TableValue):
        return False
    value = table.value
    if isinstance(value, (dict, list)):
        try:
            return _json_equal(json.loads(ledger), value)
        except ValueError:
            return False
    if not _NUMBER_TEXT.fullmatch(ledger):
        return False
    try:
        return Decimal(ledger) == Decimal(str(value)) or float(ledger) == float(value)
    except (InvalidOperation, OverflowError, ValueError):
        return False


def _plain(text: Optional[str]) -> Optional[str]:
    """A difference reports plain text, not the comparison's :class:`_TableValue`."""
    return None if text is None else str(text)


def read_canonical(
    conn, schema: str, tuple_stmt_lookup: Optional[Callable[[Tuple], Optional[str]]] = None
) -> GraphState:
    """Read the governed graph from the canonical tables (reserved props excluded, FR-014b).

    Statement ids come from the ledger's tuple index when a lookup is supplied; live
    edges without an identity get a synthetic key ``tuple:<json>``.
    """
    st = GraphState()

    def node_of(graph_id, nid) -> NodeState:
        g = _graph_key(graph_id)
        if g is None:
            return st.nodes.setdefault(nid, NodeState())
        return st.graph_nodes.setdefault((g, nid), NodeState())

    cur = conn.cursor()
    try:
        # A node is per graph (4.7.2): the same id in two graphs is two nodes, and a row of a named graph is not
        # the default graph's.
        cur.execute(f"SELECT node_id, graph_id FROM {schema}.nodes")
        for nid, graph_id in cur.fetchall():
            node_of(graph_id, nid)
        cur.execute(f"SELECT s, label, graph_id FROM {schema}.rdf_labels")
        for s, label, graph_id in cur.fetchall():
            node_of(graph_id, s).labels.add(label)
        cur.execute(f'SELECT s, "key", val, graph_id FROM {schema}.rdf_props')
        for s, key, val, graph_id in cur.fetchall():
            if key in RESERVED_PROPERTIES:
                continue
            node_of(graph_id, s).props[key] = "" if val is None else str(val)
        cur.execute(f"SELECT s, p, o_id, graph_id, qualifiers FROM {schema}.rdf_edges")
        for s, p, o, g, quals in cur.fetchall():
            g = _graph_key(g)
            if g is not None:
                # every edge row counts, a temporal edge's marked row too: it needs its endpoints' rows in its graph
                st.implied_endpoints.update(((g, s), (g, o)))
            q: Dict[str, str] = {}
            if quals:
                try:
                    parsed = json.loads(quals) if isinstance(quals, str) else dict(quals)
                    if parsed.get("temporal") is True:
                        # A temporal edge's structural row (ADR-0009) is derived from the temporal store, like
                        # ^KG, and is not a governed statement: temporal writes create no revisions (spec 213 US11).
                        continue
                    for k, v in parsed.items():
                        if v is None:
                            continue
                        q[str(k)] = _qualifier_text(v)
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
    # An edge in a named graph needs a node row for each endpoint in that graph (rdf_edges' foreign keys, spec 227).
    # A ledger commit registers them without a record (LedgerApply.EnsureEndpoint) and a temporal write makes them
    # too, so a node with no label and no property that an edge row of its own graph names is part of that edge, not
    # a separate node: left out of both sides, so neither a recorded nor an unrecorded one is a difference. The row
    # stays when the edge goes (delete_rel does not remove it), so the ledger's statements, deleted ones too, count
    # with the edge rows the tables hold.
    implied = canonical.implied_endpoints | replayed.implied_endpoints
    for side in (a, b):
        side["nodes"] = {
            k: n
            for k, n in side["nodes"].items()
            if not (k[0] and k in implied and not n["labels"] and not n["props"])
        }
    for key in sorted(set(a["nodes"]) | set(b["nodes"])):
        # a node is per graph: ("", id) is the default graph's, (name, id) a named graph's
        g, nid = key
        graph = g or None
        na, nb = a["nodes"].get(key), b["nodes"].get(key)
        if na is None:
            entries.append(DiffEntry("node", nid, "", None, "", graph))
            for lbl in sorted(nb["labels"]):
                entries.append(DiffEntry("node", nid, f"label:{lbl}", None, "", graph))
            for k, v in sorted(nb["props"].items()):
                entries.append(DiffEntry("node", nid, f"prop:{k}", None, v, graph))
            continue
        if nb is None:
            entries.append(DiffEntry("node", nid, "", "", None, graph))
            continue
        for lbl in sorted(na["labels"] ^ nb["labels"]):
            entries.append(
                DiffEntry(
                    "node",
                    nid,
                    f"label:{lbl}",
                    "" if lbl in na["labels"] else None,
                    "" if lbl in nb["labels"] else None,
                    graph,
                )
            )
        for k in sorted(set(na["props"]) | set(nb["props"])):
            va, vb = na["props"].get(k), nb["props"].get(k)
            if va != vb:
                entries.append(DiffEntry("node", nid, f"prop:{k}", va, vb, graph))
    for t in sorted(set(a["rels"]) | set(b["rels"]), key=lambda x: (x[0], x[1], x[2], x[3] or "")):
        ra, rb = a["rels"].get(t), b["rels"].get(t)
        tid = json.dumps(
            {"graph": t[3], "o": t[2], "p": t[1], "s": t[0]}, sort_keys=True, separators=(",", ":")
        )
        if ra is None:
            entries.append(DiffEntry("rel", tid, "", None, tid))
            for k, v in sorted(rb["quals"].items()):
                entries.append(DiffEntry("rel", tid, f"qual:{k}", None, _plain(v)))
            continue
        if rb is None:
            entries.append(DiffEntry("rel", ra["stmt_id"], "", tid, None))
            continue
        for k in sorted(set(ra["quals"]) | set(rb["quals"])):
            va, vb = ra["quals"].get(k), rb["quals"].get(k)
            if not _qual_same(va, vb):
                entries.append(DiffEntry("rel", ra["stmt_id"], f"qual:{k}", va, _plain(vb)))
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
                        "graph": e.graph,
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
                        "graph": e.graph,
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
                        "graph": e.graph,
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


def _drop_temporal_marked(replayed: GraphState) -> None:
    """Leave out of the replay the statements whose qualifier ``temporal`` is ``"true"``: the rule
    :func:`read_canonical` applies to the tables, applied to the ledger's side too.

    A genesis before 4.6.0 (R094) recorded a temporal edge's marked row as a statement, with the
    marker as a qualifier (``set_qual temporal "true"``); verify's read of the tables leaves that row
    out, so every such ledger diverged by those statements, before an upgrade and after it (R114).
    """
    for sid in [k for k, st in replayed.statements.items() if st.quals.get("temporal") == "true"]:
        st = replayed.statements.pop(sid)
        if replayed._stmt_by_tuple.get(st.tuple) == sid:
            del replayed._stmt_by_tuple[st.tuple]


def verify(
    ledger, *, adopt: bool = False, actor: str = "system:ledger-verify"
) -> VerificationReport:
    from .changeset import Changeset

    head = ledger.head()
    if ledger.redaction_in_progress() is not None:
        # Spec 256 Decision 3: half the target is tombstoned, so replay and store disagree by
        # construction. Neither "equal" nor "diverges" is true, and adopting the difference
        # would write the erased content back.
        try:
            ledger._call("RecordVerify", REDACTION_IN_PROGRESS)
        except Exception:
            pass
        return VerificationReport(
            verified_head=head.revision_id, verified_seq=head.seq, result=REDACTION_IN_PROGRESS
        )
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
    _drop_temporal_marked(replayed)
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
