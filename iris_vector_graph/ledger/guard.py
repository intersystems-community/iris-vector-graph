"""LedgerGuard — strict-mode enforcement for non-ledger structural writes (spec 213, R11).

Reads ``Graph.KG.Ledger.Meta()`` at most once per ``TTL_SECONDS`` and raises
:class:`LedgerStrictModeError` when the ledger is enabled in strict mode.
Fails open (treats the ledger as absent) whenever the probe cannot run, so
engines built against mocks or pre-deployment containers behave as before.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from .errors import LedgerStrictModeError

LEDGER_CLASS = "Graph.KG.Ledger"
TTL_SECONDS = 5.0
_ABSENT: Dict[str, Any] = {"state": "never-enabled", "strict": False}


class LedgerGuard:
    def __init__(self, engine, ttl: float = TTL_SECONDS):
        self._engine = engine
        self._ttl = ttl
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0
        self._class_present: Optional[bool] = None

    # -- probe -----------------------------------------------------------------

    def refresh(self) -> None:
        self._cached = None
        self._cached_at = 0.0

    def _probe(self) -> Dict[str, Any]:
        now = time.monotonic()
        if self._cached is not None and (now - self._cached_at) < self._ttl:
            return self._cached
        meta: Dict[str, Any] = _ABSENT
        try:
            iris_obj = self._engine._iris_obj()
            if self._class_present is None:
                # %SYSTEM.OBJ.Exists is not reachable through the Native API on all builds;
                # %Dictionary.CompiledClass.%ExistsId is.
                self._class_present = bool(
                    int(
                        str(
                            iris_obj.classMethodValue(
                                "%Dictionary.CompiledClass", "%ExistsId", LEDGER_CLASS
                            )
                        )
                        or 0
                    )
                )
            if self._class_present:
                raw = str(iris_obj.classMethodValue(LEDGER_CLASS, "Meta"))
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict) and parsed:
                    meta = parsed
        except Exception:
            meta = _ABSENT
        self._cached = meta
        self._cached_at = now
        return meta

    # -- queries ---------------------------------------------------------------

    def state(self) -> str:
        return str(self._probe().get("state", "never-enabled"))

    def is_enabled(self) -> bool:
        return self.state() == "enabled"

    def is_strict(self) -> bool:
        m = self._probe()
        return str(m.get("state")) == "enabled" and bool(m.get("strict", False))

    # -- enforcement -----------------------------------------------------------

    def check_structural_write(self, kind: str = "") -> None:
        if self.is_strict():
            self.count_rejection("rej_strict_block")
            raise LedgerStrictModeError(
                f"ledger strict mode: structural write '{kind}' rejected; commit a changeset via engine.ledger"
            )

    def count_rejection(self, kind: str) -> None:
        try:
            self._engine._iris_obj().classMethodValue(LEDGER_CLASS, "Count", kind)
        except Exception:
            pass


__all__ = ["LedgerGuard", "TTL_SECONDS"]
