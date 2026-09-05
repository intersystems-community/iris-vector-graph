"""LedgerMixin — attaches the revision ledger to IRISGraphEngine (spec 213).

Adds two lazily-constructed attributes and nothing else:

- ``engine.ledger``        → :class:`iris_vector_graph.ledger.client.GraphLedger`
- ``engine._ledger_guard`` → :class:`iris_vector_graph.ledger.guard.LedgerGuard`

Both tolerate engines created via ``__new__`` in tests (no ``__init__`` ran).
"""

from __future__ import annotations


class LedgerMixin:
    @property
    def ledger(self):
        obj = self.__dict__.get("_ledger_obj")
        if obj is None:
            from ..ledger.client import GraphLedger

            obj = GraphLedger(self)
            self.__dict__["_ledger_obj"] = obj
        return obj

    @property
    def _ledger_guard(self):
        guard = self.__dict__.get("_ledger_guard_obj")
        if guard is None:
            from ..ledger.guard import LedgerGuard

            guard = LedgerGuard(self)
            self.__dict__["_ledger_guard_obj"] = guard
        return guard

    def _ledger_status(self):
        """Build a LedgerStatus for EngineStatus (FR-050). Never raises."""
        from ..status import LedgerStatus

        try:
            meta = self.ledger.meta()
        except Exception:
            return LedgerStatus()
        st = LedgerStatus(
            state=str(meta.get("state", "never-enabled")),
            strict=bool(meta.get("strict", False)),
            head_seq=int(meta.get("head_seq") or 0),
            head_id=meta.get("head_id") or None,
        )
        if st.state == "never-enabled":
            return st
        try:
            c = self.ledger.stats()
            st.commits_ok = c.commits_ok
            st.replays_idem = c.replays_idem
            st.rejections = dict(c.rejections)
            st.last_verify_ms = c.last_verify_ms
            st.last_verify_result = c.last_verify_result
        except Exception:
            pass
        return st


def ledger_check(obj, kind: str) -> None:
    """Strict-mode check that tolerates mixin-only or __new__-created objects (no guard → no-op)."""
    try:
        guard = obj._ledger_guard
    except Exception:
        return
    if guard is not None:
        guard.check_structural_write(kind)


def ledger_strict(obj) -> bool:
    try:
        guard = obj._ledger_guard
    except Exception:
        return False
    return bool(guard is not None and guard.is_strict())
