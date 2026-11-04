"""The record of the SQL functions and procedures IVG has created in a namespace (4.6.0 R133, R153).

``CREATE OR REPLACE`` recompiles a routine's class and discards the cached queries that call it, so IVG runs it only
for a routine that is missing or whose text changed. ``^IVG.Deploy("routines")`` holds ``{ROUTINE NAME: fingerprint}``
as JSON, the fingerprint the sha256 of the IVG Schema and the routine's DDL text. Setup's routines
(``SchemaMixin._install_procedures``) and the Arno bridge's wrappers (``stores/arno_bridge.py``) share the one record:
each entry is keyed by its own routine's name, and each writer keeps the entries it did not create.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

#: ``(global, subscript)`` holding the record; the classes' own marker is ``^IVG.Deploy("fingerprint")``.
ROUTINES_MARKER = ("^IVG.Deploy", "routines")

_ROUTINE_DDL = re.compile(r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+\w+\.(\w+)", re.IGNORECASE)


def routine_name(stmt: str) -> Optional[str]:
    """The upper-case name a ``CREATE OR REPLACE FUNCTION|PROCEDURE schema.name`` statement creates, else None."""
    m = _ROUTINE_DDL.search(stmt)
    return m.group(1).upper() if m else None


def declared_routine_name(stmt: str) -> Optional[str]:
    """The name as the statement spells it (``kg_RRF_FUSE``), for messages; else None."""
    m = _ROUTINE_DDL.search(stmt)
    return m.group(1) if m else None


def routine_fingerprint(stmt: str, schema: str) -> str:
    """The fingerprint of one routine as IVG creates it: its DDL text and the IVG Schema it is created in."""
    return hashlib.sha256(f"{schema}\0{stmt.strip()}".encode("utf-8")).hexdigest()


def read_record(native) -> Dict[str, str]:
    """The record, ``{}`` when there is none or it is not the JSON IVG writes (every routine is then created and the
    record rewritten)."""
    raw = native.get(*ROUTINES_MARKER)
    if raw in (None, ""):
        return {}
    try:
        record = json.loads(str(raw))
    except json.JSONDecodeError:
        logger.warning('^IVG.Deploy("routines") is not the JSON setup writes; every routine is re-created')
        return {}
    return record if isinstance(record, dict) else {}


class RoutineRecord:
    """One pass over a set of routines: which to skip, and what to record once each is created.

    ``native`` is anything with the Native API's ``get(name, *subs)`` / ``set(value, name, *subs)`` (an
    ``iris.createIRIS`` object, a :class:`iris_vector_graph.host.Host`), or None when the connection has none: then
    nothing is recorded and every routine is created, as before R133. ``force`` creates every routine.
    """

    def __init__(self, native, cursor, schema: str, force: bool = False):
        self.native, self.schema, self.force = native, schema, force
        self.record: Dict[str, Any] = read_record(native) if native is not None else {}
        self.present: Set[str] = set()
        if self.record and not force:
            cursor.execute("SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?", [schema])
            self.present = {str(r[0]).upper() for r in cursor.fetchall()}

    def unchanged(self, stmt: str) -> bool:
        """Whether ``stmt``'s routine is listed and recorded with this text in this schema, so need not run."""
        name = routine_name(stmt)
        if not name or self.force or name not in self.present:
            return False
        return self.record.get(name) == routine_fingerprint(stmt, self.schema)

    def forget(self, stmt: str) -> None:
        """Drop ``stmt``'s entry before running it: it is recorded again only once this text has been created."""
        name = routine_name(stmt)
        if name:
            self.record.pop(name, None)

    def created(self, stmt: str) -> Optional[str]:
        """Record ``stmt``'s routine as created with this text; its name."""
        name = routine_name(stmt)
        if name:
            self.record[name] = routine_fingerprint(stmt, self.schema)
        return name

    def save(self) -> None:
        if self.native is not None:
            self.native.set(json.dumps(self.record, sort_keys=True), *ROUTINES_MARKER)
