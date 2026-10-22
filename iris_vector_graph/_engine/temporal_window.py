"""The paged temporal window reader's Python half (spec 255): a cursor over Graph.KG.TemporalWindow.

The server keeps a resume key per open reader in a process-private global, so the cursor holds on to the one native
handle that opened it: a page asked for on another connection would find nothing open. A window that fits the first
page is answered and freed inside Open, and needs no further round trip.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Dict, Iterator, Optional

from iris_vector_graph.exceptions import TemporalWindowError

logger = logging.getLogger(__name__)

WINDOW_CLASS = "Graph.KG.TemporalWindow"

#: Sources sent per Latest call when the caller names them: the answer is one row per source, so this bounds both
#: the request and the reply.
LATEST_CHUNK = 500


def event(row: dict, with_attrs: bool) -> Dict[str, Any]:
    """A server row as an event: get_edges_in_window's short and long keys, plus ``attrs``."""
    e = {
        "s": row["s"], "p": row["p"], "o": row["o"], "ts": row["ts"], "w": row["w"],
        "source": row["s"], "predicate": row["p"], "target": row["o"], "timestamp": row["ts"], "weight": row["w"],
    }
    if with_attrs:
        e["attrs"] = row.get("a") or {}
    return e


def window_spec(start, end, *, source, sources, source_prefix, predicates, direction, attrs, page_size) -> dict:
    """The spec Graph.KG.TemporalWindow.Open reads, checked before any round trip."""
    if direction not in ("out", "in"):
        raise ValueError(f'direction is "out" or "in", not {direction!r}')
    if int(page_size) < 1:
        raise ValueError(f"page_size must be at least 1, got {page_size!r}")
    named = [n for n, v in (("source", source), ("source_prefix", source_prefix)) if v]
    if sources is not None:
        named.append("sources")
    if len(named) > 1:
        raise ValueError(f"name at most one of source, sources and source_prefix (got {', '.join(sorted(named))})")
    spec: dict = {"start": int(start), "end": int(end), "dir": direction, "attrs": 1 if attrs else 0,
                  "page": int(page_size)}
    if source:
        spec["source"] = str(source)
    if sources is not None:
        spec["sources"] = _str_list("sources", sources)
    if source_prefix:
        spec["prefix"] = str(source_prefix)
    if predicates is not None:
        spec["predicates"] = _str_list("predicates", predicates)
    return spec


def _str_list(name, values) -> list:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} is a list of strings, not one string ({values!r})")
    return [str(v) for v in values]


def _server_call(native, method: str, *args) -> dict:
    try:
        return json.loads(str(native.classMethodValue(WINDOW_CLASS, method, *args)))
    except Exception as exc:
        raise TemporalWindowError(f"{WINDOW_CLASS}.{method} failed: {exc}") from exc


class TemporalWindowCursor:
    """An iterator over one window's events, read from the server a page at a time.

    Closed when the last page has been read, on ``close()``, on leaving a ``with`` block, or when the cursor is
    garbage-collected; a page that fails raises ``TemporalWindowError`` and frees the server state. ``round_trips``
    counts the server calls made so far.
    """

    def __init__(self, native, graph: str, spec: dict):
        self.native = native
        self._attrs = bool(spec.get("attrs"))
        self._page = int(spec.get("page", 1000))
        self._rows: deque = deque()
        self.handle: Optional[int] = None
        self.closed = True
        self.round_trips = 0
        head = self._call("Open", graph, json.dumps(spec))
        self.handle = head.get("h")
        self.closed = bool(head.get("done"))
        self._rows.extend(head.get("rows") or [])

    def _call(self, method: str, *args) -> dict:
        self.round_trips += 1
        return _server_call(self.native, method, *args)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return self

    def __next__(self) -> Dict[str, Any]:
        while not self._rows:
            if self.closed:
                raise StopIteration
            try:
                page = self._call("Next", self.handle, self._page)
            except TemporalWindowError:
                self.close()
                raise
            self.closed = bool(page.get("done"))
            self._rows.extend(page.get("rows") or [])
        return event(self._rows.popleft(), self._attrs)

    def close(self) -> None:
        """Free the server's reader now. Safe to call twice, and after the last page."""
        self._rows.clear()
        if self.closed:
            return
        self.closed = True
        try:
            self.native.classMethodVoid(WINDOW_CLASS, "Close", self.handle)
        except Exception as exc:  # the connection is gone, and with it the process-private state
            logger.debug("Graph.KG.TemporalWindow.Close(%s) failed: %s", self.handle, exc)

    def __enter__(self) -> "TemporalWindowCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def latest(native, graph: str, *, sources, source_prefix, predicate, before, lookback, attrs) -> list:
    """Graph.KG.TemporalWindow.Latest, in chunks of named sources or in pages of the index's own."""
    if sources is not None and source_prefix:
        raise ValueError("name sources or source_prefix, not both")
    if lookback is not None and before is None:
        raise ValueError("lookback counts back from before: give both")
    base: dict = {"attrs": 1 if attrs else 0}
    if predicate:
        base["predicate"] = str(predicate)
    if before is not None:
        base["before"] = int(before)
    if lookback is not None:
        base["lookback"] = int(lookback)

    def call(spec):
        return _server_call(native, "Latest", graph, json.dumps(spec))

    rows: list = []
    if sources is not None:
        names = list(dict.fromkeys(_str_list("sources", sources)))
        for k in range(0, len(names), LATEST_CHUNK):
            rows.extend(call({**base, "sources": names[k : k + LATEST_CHUNK]})["rows"])
    else:
        spec = dict(base)
        if source_prefix:
            spec["prefix"] = str(source_prefix)
        while True:
            answer = call(spec)
            rows.extend(answer["rows"])
            if answer.get("next") is None:
                break
            spec = {**spec, "after": answer["next"]}
    return [event(r, bool(attrs)) for r in rows]
