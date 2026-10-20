"""The shadow comparator that holds the native Cypher executor to the SQL path (spec 253, FR-006, FR-013).

A native answer must equal the SQL path's: the same columns, and the same rows in order where the query orders them
completely, as a multiset otherwise. Where a LIMIT or SKIP keeps rows the query does not order completely, the two
paths may legitimately keep different rows; then the counts must match and every native row must be a row of the SQL
result taken without the LIMIT/SKIP.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, List, NoReturn, Optional, Sequence


class NativeMismatch(AssertionError):
    """The native executor's answer differs from the SQL path's."""


def _key(row: Sequence[Any]) -> tuple:
    # type is part of the value: 1 and "1" are different answers
    return tuple((type(v).__name__, v) for v in row)


def _fail(query: str, params: dict, what: str, row: Optional[Iterable[Any]] = None) -> NoReturn:
    detail = f"; first differing row: {list(row)!r}" if row is not None else ""
    raise NativeMismatch(f"native and SQL answers differ ({what}) for {query!r} with {params!r}{detail}")


def compare(
    query: str,
    params: dict,
    sql_columns: List[str],
    sql_rows: List[Sequence[Any]],
    native_columns: List[str],
    native_rows: List[Sequence[Any]],
    *,
    ordered: bool,
    limited: bool = False,
    sql_unlimited_rows: Optional[List[Sequence[Any]]] = None,
) -> None:
    """Raise NativeMismatch unless the native answer equals the SQL answer under FR-013."""
    if list(sql_columns) != list(native_columns):
        _fail(query, params, f"columns {list(sql_columns)!r} vs {list(native_columns)!r}")
    if limited and not ordered:
        if len(sql_rows) != len(native_rows):
            _fail(query, params, f"row count {len(sql_rows)} vs {len(native_rows)}")
        returnable = sql_unlimited_rows if sql_unlimited_rows is not None else sql_rows
        allowed = Counter(_key(r) for r in returnable)
        for row in native_rows:
            k = _key(row)
            if allowed[k] <= 0:
                _fail(query, params, "a row the query cannot return", row)
            allowed[k] -= 1
        return
    if ordered:
        for i, (s, n) in enumerate(zip(sql_rows, native_rows)):
            if _key(s) != _key(n):
                _fail(query, params, f"row {i}", n)
        if len(sql_rows) != len(native_rows):
            longer = native_rows if len(native_rows) > len(sql_rows) else sql_rows
            extra = longer[min(len(sql_rows), len(native_rows))]
            _fail(query, params, f"row count {len(sql_rows)} vs {len(native_rows)}", extra)
        return
    sql_bag, native_bag = Counter(_key(r) for r in sql_rows), Counter(_key(r) for r in native_rows)
    if sql_bag != native_bag:
        missing = sql_bag - native_bag
        extra = native_bag - sql_bag
        first = next(iter(missing or extra))
        _fail(query, params, "missing from native" if missing else "extra in native", [v for _, v in first])
