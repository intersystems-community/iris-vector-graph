"""Whether a compiled class or method exists, asked in SQL.

The Native API calls these used (``%Dictionary.CompiledClass.%ExistsId``) cannot be reached the same way from
every host: under irispython a ``%`` method is spelled ``_ExistsId``, and through the wgproto Callin worker the
call, once spelled that way, segfaults the worker (signal 11, measured on Build 154). A SELECT works from all of
them.

A SELECT on the class dictionary needs a privilege ADMIN_GUIDE §3's reading and writing roles do not hold (SQLCODE
-99). Refused that, and given a native handle, these ask the dictionary classes' ``IDKEYExists``, an index method
with no ``%`` that ``%Native_ClassExecution`` lets any user call (4.6.0 R206). Before, the refusal reached the
callers as an error or as "absent": a reader's engine found no IVG classes and ``khop`` answered ``[]``, and a
writer's ledger guard never saw strict mode.
"""

from __future__ import annotations


def _count(conn, sql: str, params) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def sql_refused(exc: BaseException) -> bool:
    """A statement IRIS refused for the user's privileges (SQLCODE -99)."""
    text = str(exc)
    return "-99>" in text or "SQLCODE: -99" in text or "SQLCODE -99" in text or "Privilege violation" in text


def _ask_native(native, cls: str, *args) -> bool:
    """``IDKEYExists`` through a host (``iris_vector_graph.host.Host``) or a native object (``classMethodValue``)."""
    from iris_vector_graph.host import Host

    if isinstance(native, Host):
        raw = native.call(cls, "IDKEYExists", *args)
    else:
        raw = native.classMethodValue(cls, "IDKEYExists", *args)
    return bool(int(str(raw or 0)))


def _dictionary_has(conn, native, cls: str, where: str, ids: list) -> bool:
    """Whether dictionary class ``cls`` holds the row ``ids`` names: in SQL, else (refused, with ``native``) by
    ``IDKEYExists``."""
    try:
        return bool(_count(conn, f"SELECT COUNT(*) FROM {cls} WHERE {where}", ids))
    except Exception as exc:
        if native is None or not sql_refused(exc):
            raise
        return _ask_native(native, cls, *ids)


def compiled_class_exists(conn, name: str, native=None) -> bool:
    return _dictionary_has(conn, native, "%Dictionary.CompiledClass", "Name = ?", [name])


def compiled_method_exists(conn, class_name: str, method: str, native=None) -> bool:
    return _dictionary_has(conn, native, "%Dictionary.CompiledMethod", "Parent = ? AND Name = ?", [class_name, method])
