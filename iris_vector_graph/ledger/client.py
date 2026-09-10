"""GraphLedger — thin Python client over Graph.KG.Ledger (spec 213, R3/R6).

The server method owns the transaction; this module serializes changesets,
stages large payloads, maps wire errors to exceptions, logs outcomes, and
exposes history/diff/reconstruction/verification built on ``replay``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .changeset import Changeset
from .errors import (
    LedgerError,
    UnknownRevisionError,
    from_commit_error,
)

logger = logging.getLogger("iris_vector_graph.ledger")

LEDGER_CLASS = "Graph.KG.Ledger"
CHUNK_CHARS = 1_000_000
RECORD_PAGE = 5_000


# --------------------------------------------------------------------------- #
# value objects
# --------------------------------------------------------------------------- #


@dataclass
class RevisionInfo:
    seq: int
    revision_id: str
    parent_id: Optional[str]
    kind: str
    actor: str
    actor_type: str
    conn_user: str
    committed_ms: int
    message: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    op_count: int = 0

    @classmethod
    def from_wire(cls, d: Dict[str, Any]) -> "RevisionInfo":
        src = d.get("source")
        if isinstance(src, str) and src:
            try:
                src = json.loads(src)
            except Exception:
                src = {"raw": src}
        return cls(
            seq=int(d["seq"]),
            revision_id=str(d["revision_id"]),
            parent_id=(d.get("parent_id") or None),
            kind=str(d.get("kind", "changeset")),
            actor=str(d.get("actor", "")),
            actor_type=str(d.get("actor_type", "")),
            conn_user=str(d.get("conn_user", "")),
            committed_ms=int(d.get("committed_ms") or 0),
            message=d.get("message") or None,
            source=src or None,
            correlation_id=d.get("correlation_id") or None,
            idempotency_key=d.get("idempotency_key") or None,
            op_count=int(d.get("op_count") or 0),
        )


@dataclass
class CommitResult:
    """Result of a successful :meth:`GraphLedger.commit` call.

    Attributes:
        revision: The assigned or replayed :class:`RevisionInfo`.
        replayed: ``True`` when this commit was a replay of an earlier identical
            changeset (same ``actor``, ``actor_type``, and ``ops`` fingerprint).
            **Note**: ``replayed=True`` can occur even when ``expected_head`` differs
            from the original commit — the fingerprint does *not* include
            ``expected_head``.  See :meth:`Changeset.fingerprint` for the exact
            scope of the idempotency hash.
        stmt_ids: Mapping of op index → assigned statement identity for new
            relationship operations.
    """

    revision: RevisionInfo
    replayed: bool = False
    stmt_ids: Dict[int, str] = field(default_factory=dict)


@dataclass
class HistoryPage:
    revisions: List[RevisionInfo]
    next_after_seq: Optional[int]


@dataclass
class Revision:
    info: RevisionInfo
    records: list  # List[MutationRecord]


@dataclass
class LedgerCounters:
    state: str = "never-enabled"
    strict: bool = False
    head_seq: int = 0
    commits_ok: int = 0
    rejections: Dict[str, int] = field(default_factory=dict)
    replays_idem: int = 0
    last_verify_ms: Optional[int] = None
    last_verify_result: Optional[str] = None
    enabled_ms: Optional[int] = None
    disabled_ms: Optional[int] = None


_REJECTION_COLUMNS = (
    "rej_stale_head",
    "rej_unknown_head",
    "rej_idem_conflict",
    "rej_failed_op",
    "rej_strict_block",
    "rej_size_limit",
    "rej_disabled",
    "rej_lock_timeout",
)


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #


class GraphLedger:
    """Per-engine ledger client. Obtain via ``engine.ledger``."""

    def __init__(self, engine):
        self._engine = engine
        self._hooks: List[Callable[[Dict[str, Any]], None]] = []

    # -- transport -------------------------------------------------------------

    def _iris(self):
        return self._engine._iris_obj()

    def _schema(self) -> str:
        return getattr(self._engine, "_schema_prefix", "Graph_KG") or "Graph_KG"

    def _call(self, method: str, *args) -> str:
        iris_obj = self._iris()
        raw = str(iris_obj.classMethodValue(LEDGER_CLASS, method, *args))
        if raw.startswith("CHUNKED:"):
            _, tag, n_str = raw.split(":", 2)
            n = int(n_str)
            raw = "".join(
                str(iris_obj.classMethodValue(LEDGER_CLASS, "ReadLargeOutChunk", tag, i))
                for i in range(1, n + 1)
            )
        return raw

    def _call_json(self, method: str, *args) -> Dict[str, Any]:
        raw = self._call(method, *args)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise LedgerError(f"{LEDGER_CLASS}.{method} returned non-JSON: {raw[:200]!r}") from e

    def _refresh_guard(self) -> None:
        guard = getattr(self._engine, "_ledger_guard", None)
        if guard is not None and hasattr(guard, "refresh"):
            guard.refresh()

    # -- lifecycle -------------------------------------------------------------

    def enable(
        self, *, strict: bool = False, max_ops: int = 50_000, recon_bound: int = 250_000
    ) -> RevisionInfo:
        resp = self._call_json("Enable", 1 if strict else 0, int(max_ops), int(recon_bound))
        self._refresh_guard()
        if not resp.get("ok", False):
            raise from_commit_error(resp)
        info = RevisionInfo.from_wire(resp["revision"])
        if resp.get("adoption_needed"):
            report = self.verify(adopt=True)
            adopted = getattr(report, "adoption_revision", None)
            if adopted is not None:
                info = adopted
        return info

    def disable(self) -> None:
        resp = self._call_json("Disable")
        self._refresh_guard()
        if not resp.get("ok", True):
            raise from_commit_error(resp)

    def set_strict(self, flag: bool) -> None:
        resp = self._call_json("SetStrict", 1 if flag else 0)
        self._refresh_guard()
        if not resp.get("ok", False):
            raise from_commit_error(resp)

    def meta(self) -> Dict[str, Any]:
        return self._call_json("Meta")

    def head(self) -> RevisionInfo:
        resp = self._call_json("Head")
        if not resp.get("ok", False):
            raise from_commit_error(resp)
        return RevisionInfo.from_wire(resp["revision"])

    # -- commit ----------------------------------------------------------------

    def commit(self, changeset: Changeset, *, kind: str = "changeset") -> CommitResult:
        changeset.validate()
        payload = changeset.canonical_json()
        meta = {
            "fingerprint": changeset.fingerprint(),
            "expected_head": changeset.expected_head,
            "idempotency_key": changeset.idempotency_key,
            "kind": kind,
            "schema": self._schema(),
            "staged": False,
        }
        t0 = time.perf_counter()
        if len(payload) <= CHUNK_CHARS:
            raw = self._call("Commit", payload, json.dumps(meta))
        else:
            token = uuid.uuid4().hex
            iris_obj = self._iris()
            idx = 0
            for start in range(0, len(payload), CHUNK_CHARS):
                idx += 1
                iris_obj.classMethodValue(
                    LEDGER_CLASS, "StageChunk", token, idx, payload[start : start + CHUNK_CHARS]
                )
            meta["staged"] = True
            raw = self._call("Commit", token, json.dumps(meta))
        duration_ms = (time.perf_counter() - t0) * 1000.0
        try:
            resp = (
                json.loads(raw)
                if raw
                else {"ok": False, "error": "inconsistent", "reason": "empty response"}
            )
        except json.JSONDecodeError:
            resp = {
                "ok": False,
                "error": "inconsistent",
                "reason": f"non-JSON response: {raw[:200]!r}",
            }

        if not resp.get("ok", False):
            self._emit(
                resp.get("error", "error"), None, changeset, duration_ms, reason=resp.get("reason")
            )
            # Check for node_not_found before generic error mapping
            reason = resp.get("reason") or ""
            if resp.get("error") == "failed_op" and reason.startswith("node_not_found: '"):
                missing = reason[len("node_not_found: '"):-1]
                from .errors import NodeNotFoundError
                raise NodeNotFoundError(missing_node=missing)
            raise from_commit_error(resp)

        info = RevisionInfo.from_wire(resp["revision"])
        replayed = bool(resp.get("replayed", False))
        stmt_ids = {int(k): str(v) for k, v in (resp.get("stmt_ids") or {}).items()}
        self._emit("replayed" if replayed else "success", info, changeset, duration_ms)

        if not replayed and any(
            op["op"] in ("create_rel", "upsert_rel", "delete_rel", "delete_node")
            for op in changeset.ops
        ):
            self._mark_nkg_dirty_if_needed()
        return CommitResult(revision=info, replayed=replayed, stmt_ids=stmt_ids)

    def _mark_nkg_dirty_if_needed(self) -> None:
        """R15: when the incremental ^NKG skeleton is absent, flag staleness."""
        try:
            present = str(
                self._iris().classMethodValue("Graph.KG.Meta", "GetNKG", "$meta", "nodeCount")
            )
        except Exception:
            present = ""
        if present == "":
            try:
                self._engine._nkg_dirty = True
            except Exception:
                pass

    # -- observability ---------------------------------------------------------

    def register_metrics_hook(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        self._hooks.append(fn)

    def _emit(
        self,
        outcome: str,
        info: Optional[RevisionInfo],
        changeset: Changeset,
        duration_ms: float,
        reason: Optional[str] = None,
    ) -> None:
        event = {
            "outcome": outcome,
            "revision_id": info.revision_id if info else None,
            "seq": info.seq if info else None,
            "actor": changeset.actor,
            "actor_type": changeset.actor_type,
            "op_count": len(changeset.ops),
            "duration_ms": round(duration_ms, 3),
            "reason": reason,
        }
        logger.info(
            "ledger.commit outcome=%s revision_id=%s actor=%s op_count=%d duration_ms=%.3f%s",
            outcome,
            event["revision_id"],
            changeset.actor,
            len(changeset.ops),
            duration_ms,
            f" reason={reason}" if reason else "",
            extra={"ledger_event": event},
        )
        for hook in list(self._hooks):
            try:
                hook(dict(event))
            except Exception as e:  # hooks must never break commits
                logger.warning("ledger metrics hook failed: %s", e)

    def stats(self) -> LedgerCounters:
        m = self.meta()
        counters = LedgerCounters(
            state=str(m.get("state", "never-enabled")),
            strict=bool(m.get("strict", False)),
            head_seq=int(m.get("head_seq") or 0),
        )
        try:
            cur = self._engine.conn.cursor()
            try:
                cur.execute(
                    f"SELECT commits_ok, replays_idem, {', '.join(_REJECTION_COLUMNS)}, "
                    f"last_verify_ms, last_verify_result, enabled_ms, disabled_ms "
                    f"FROM {self._schema()}.ledger_stats"
                )
                fetched = cur.fetchone()
                # materialize: the IRIS DB-API DataRow is lazy and dies with the cursor
                row = tuple(fetched) if fetched is not None else None
            finally:
                cur.close()
        except Exception:
            row = None
        if row:
            counters.commits_ok = int(row[0] or 0)
            counters.replays_idem = int(row[1] or 0)
            counters.rejections = {
                col: int(row[2 + i] or 0) for i, col in enumerate(_REJECTION_COLUMNS)
            }
            base = 2 + len(_REJECTION_COLUMNS)
            counters.last_verify_ms = int(row[base]) if row[base] not in (None, "") else None
            counters.last_verify_result = row[base + 1] or None
            counters.enabled_ms = int(row[base + 2]) if row[base + 2] not in (None, "") else None
            counters.disabled_ms = int(row[base + 3]) if row[base + 3] not in (None, "") else None
        return counters

    # -- read side (US6–US9) ---------------------------------------------------

    def _seq_of(self, revision_id: str) -> int:
        resp = self._call_json("SeqOf", str(revision_id))
        if not resp.get("ok", False):
            raise UnknownRevisionError(f"unknown revision {revision_id}")
        return int(resp["seq"])

    def history(
        self,
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
    ) -> HistoryPage:
        """Return a page of ledger revisions, newest or oldest first.

        All filter parameters are pushed to SQL (not applied in Python).

        Args:
            after_seq: Pagination cursor — return revisions after this seq.
            limit: Maximum revisions to return.
            actor: Filter to revisions by a specific actor string.
            actor_type: Filter to revisions by actor type (e.g. "ingest").
            since_ms: Only revisions committed at or after this epoch-ms.
            until_ms: Only revisions committed at or before this epoch-ms.
            descending: Return newest-first when True.
            correlation_id: Filter to revisions with this exact correlation_id.
                Useful for per-tenant history in multi-tenant graphs where
                correlation_id is set to e.g. "acme-health|iris-acme-health".
            source: Filter to revisions tagged with this source string.
        """
        from .replay import fetch_history

        return fetch_history(
            self._engine.conn,
            self._schema(),
            after_seq=after_seq,
            limit=limit,
            actor=actor,
            actor_type=actor_type,
            since_ms=since_ms,
            until_ms=until_ms,
            descending=descending,
            correlation_id=correlation_id,
            source=source,
        )

    def get_revision(self, revision_id: str) -> Revision:
        from .replay import fetch_revision_info, iter_records_sql

        seq = self._seq_of(revision_id)
        info = fetch_revision_info(self._engine.conn, self._schema(), seq)
        if info is None:
            raise UnknownRevisionError(f"unknown revision {revision_id}")
        records = list(iter_records_sql(self._engine.conn, self._schema(), seq, seq))
        return Revision(info=info, records=records)

    def diff(self, from_id: str, to_id: str):
        from .replay import compute_diff, iter_records_sql

        a = self._seq_of(from_id)
        b = self._seq_of(to_id)
        lo, hi = (a, b) if a <= b else (b, a)
        forward = (
            compute_diff(iter_records_sql(self._engine.conn, self._schema(), lo + 1, hi))
            if hi > lo
            else compute_diff([])
        )
        return forward if a <= b else forward.inverse()

    def reconstruct(self, revision_id: str, *, stream: bool = False):
        from .replay import iter_records_sql, reconstruct

        seq = self._seq_of(revision_id)
        bound = int(self.meta().get("recon_bound") or 250_000)
        return reconstruct(
            iter_records_sql(self._engine.conn, self._schema(), 1, seq), bound=bound, stream=stream
        )

    def export_reconstruction(self, revision_id: str, path: str):
        from .export import export_ndjson
        from .replay import iter_records_sql, reconstruct

        seq = self._seq_of(revision_id)
        state = reconstruct(
            iter_records_sql(self._engine.conn, self._schema(), 1, seq), bound=None, stream=False
        )
        return export_ndjson(state, path)

    def verify(self, *, adopt: bool = False, actor: str = "system:ledger-verify"):
        from .replay import verify as _verify

        return _verify(self, adopt=adopt, actor=actor)


__all__ = [
    "GraphLedger",
    "RevisionInfo",
    "CommitResult",
    "HistoryPage",
    "Revision",
    "LedgerCounters",
    "CHUNK_CHARS",
    "LEDGER_CLASS",
]
