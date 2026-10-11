"""Whether a compiled class or method exists, asked in SQL.

The Native API calls these used (``%Dictionary.CompiledClass.%ExistsId``) cannot be reached the same way from
every host: under irispython a ``%`` method is spelled ``_ExistsId``, and through the wgproto Callin worker the
call, once spelled that way, segfaults the worker (signal 11, measured on Build 154). A SELECT works from all of
them.
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


def compiled_class_exists(conn, name: str) -> bool:
    return bool(_count(conn, "SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = ?", [name]))


def compiled_method_exists(conn, class_name: str, method: str) -> bool:
    return bool(
        _count(
            conn,
            "SELECT COUNT(*) FROM %Dictionary.CompiledMethod WHERE Parent = ? AND Name = ?",
            [class_name, method],
        )
    )
