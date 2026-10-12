"""IVG's operations on the IRIS it is running against, whichever host that is.

IVG runs in three hosts that look alike and are not: a client process over the Native API (``iris.createIRIS``), a
process inside IRIS (irispython, where ``createIRIS`` does not exist and classes are reached with ``iris.cls``), and
the wgproto Callin worker behind the MCP service (inside IRIS too, but a ``%`` method that really runs there
segfaults the worker). Callers used to build "a handle" at seven sites and speak the Native API's vocabulary on it, so
each chose host-sensitive calls itself. This module is the one place that knows.

The interface is what IVG does, not what the Native API offers:

* ``call`` / ``call_large``: run a class method; ``call_large`` also reassembles the ``CHUNKED:`` and ``SORTED:``
  replies the server stages when a result is too big for one return.
* ``get`` / ``set`` / ``kill`` / ``is_defined`` / ``next_subscript``: read and write globals.
* ``class_exists`` / ``method_exists``: ask the compiled dictionary, in SQL.

There is deliberately no way to call a ``%`` method through it (ADR-0008, architecture review candidate 2): existence
has its own operations, and a caller that needs a system method names the operation it wants. The client host keeps
looking up ``iris.createIRIS`` at the time it builds a handle, so a test's patch of it still applies.
"""

from __future__ import annotations

from typing import Any, Optional

from iris_vector_graph._dictionary import compiled_class_exists, compiled_method_exists
from iris_vector_graph.embedded import EmbeddedConnection


class HostError(RuntimeError):
    """The host cannot do what was asked, or the call is one IVG refuses to make there."""


_PERCENT = (
    "a '%' method is not part of the host interface: use class_exists / method_exists for existence, or name the "
    "operation you need. Through the wgproto worker a %-method that really runs segfaults it."
)


def _refuse_percent(cls: str, method: str) -> None:
    if method.startswith("%"):
        raise HostError(f"{cls}.{method}: {_PERCENT}")


# ---------------------------------------------------------------------------------------------------- large replies


def _read_chunks(call, cls: str, raw: str) -> str:
    _, tag, n_str = raw.split(":", 2)
    return "".join(str(call(cls, "ReadLargeOutChunk", tag, i)) for i in range(1, int(n_str) + 1))


#: The class that owns the staged BFS tree and the one that owns the staged k-hop frontier. The class that answered
#: with the marker is not the class to ask for the rows.
_SORTED_BFS_READER = ("Graph.KG.Traversal", "ReadBFSResults")
_SORTED_KHOP_READER = ("Graph.KG.NKGAccel", "ReadKHopResults")


def _decode_sorted(call, marker: str) -> str:
    """Turn a ``SORTED:`` marker into the JSON the caller expected.

    ``SORTED:<tag>`` is a BFS tree staged in ``^ArnoKG("bfs_r", ...)``; ``SORTED:<tag>:<total>:<seed>:<k>`` a k-hop
    frontier in ``^ArnoKG("khop_r", ...)``; ``SORTED:0`` means nothing was staged.
    """
    import json

    pieces = marker[len("SORTED:") :].split(":")
    tag = pieces[0]
    if tag in ("", "0"):
        return "[]"
    if len(pieces) >= 4:
        seed = ":".join(pieces[2:-1])
        reader_cls, reader = _SORTED_KHOP_READER
        rows = str(call(reader_cls, reader, tag))
        if rows.startswith("CHUNKED:"):
            rows = _read_chunks(call, reader_cls, rows)
        if rows.startswith("SORTED:"):
            rows = ""
        nodes = json.loads("[" + rows + "]") if rows.strip() else []
        try:
            hops = int(pieces[-1])
        except ValueError:
            hops = 0
        return json.dumps({"seed": seed, "hops": hops, "totalNodes": len(nodes), "nodes": nodes})
    reader_cls, reader = _SORTED_BFS_READER
    rows = str(call(reader_cls, reader, tag))
    if rows.startswith("CHUNKED:"):
        rows = _read_chunks(call, reader_cls, rows)
    if rows.startswith("SORTED:") or not rows:
        return "[]"
    return rows


# --------------------------------------------------------------------------------------------------------- hosts


class Host:
    """The interface. Subclasses supply ``_call`` and the five global operations."""

    def __init__(self, conn=None):
        self._conn = conn

    # -- class methods
    def _call(self, cls: str, method: str, *args) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def call(self, cls: str, method: str, *args) -> Any:
        _refuse_percent(cls, method)
        return self._call(cls, method, *args)

    def call_large(self, cls: str, method: str, *args) -> str:
        _refuse_percent(cls, method)
        raw = str(self._call(cls, method, *args))
        if raw.startswith("CHUNKED:"):
            return _read_chunks(self._call, cls, raw)
        if raw.startswith("SORTED:"):
            return _decode_sorted(self._call, raw)
        return raw

    # -- globals
    def get(self, name: str, *subs) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def set(self, value: Any, name: str, *subs) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def kill(self, name: str, *subs) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def is_defined(self, name: str, *subs) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def next_subscript(self, name: str, *subs, reverse: bool = False) -> Optional[Any]:  # pragma: no cover
        raise NotImplementedError

    # -- the compiled dictionary, in SQL
    def class_exists(self, class_name: str) -> bool:
        return compiled_class_exists(self._require_conn(), class_name)

    def method_exists(self, class_name: str, method: str) -> bool:
        return compiled_method_exists(self._require_conn(), class_name, method)

    def _require_conn(self):
        if self._conn is None:
            raise HostError("this host was built without a connection, so it cannot ask the dictionary")
        return self._conn


class ClientHost(Host):
    """A client process: the Native API over ``iris.createIRIS(conn)``."""

    def __init__(self, conn, native=None):
        super().__init__(conn)
        self._native = native

    @property
    def native(self):
        if self._native is None:
            import iris  # looked up now, so a patched iris.createIRIS applies

            self._native = iris.createIRIS(self._conn)
        return self._native

    def _call(self, cls, method, *args):
        return self.native.classMethodValue(cls, method, *args)

    def get(self, name, *subs):
        return self.native.get(name, *subs)

    def set(self, value, name, *subs):
        self.native.set(value, name, *subs)

    def kill(self, name, *subs):
        self.native.kill(name, *subs)

    def is_defined(self, name, *subs):
        return int(self.native.isDefined(name, *subs))

    def next_subscript(self, name, *subs, reverse=False):
        return self.native.nextSubscript(reverse, name, *subs)


class InProcessHost(Host):
    """A process inside IRIS: classes through ``iris.cls``, globals through ``iris.gref``.

    No ``%`` method ever reaches ``iris.cls`` from here, so the spelling difference between irispython and the wgproto
    worker does not arise.
    """

    def __init__(self, conn=None, iris_module=None):
        super().__init__(conn)
        self._iris = iris_module

    def _mod(self):
        if self._iris is not None:
            return self._iris
        import iris  # only importable inside IRIS

        return iris

    def _call(self, cls, method, *args):
        return getattr(self._mod().cls(cls), method)(*args)

    @staticmethod
    def _key(subs):
        return subs[0] if len(subs) == 1 else tuple(subs)

    def get(self, name, *subs):
        return self._mod().gref(name)[self._key(subs)]

    def set(self, value, name, *subs):
        self._mod().gref(name)[self._key(subs)] = value

    def kill(self, name, *subs):
        self._mod().gref(name).kill(list(subs))

    def is_defined(self, name, *subs):
        return int(self._mod().gref(name).data(list(subs)))

    def next_subscript(self, name, *subs, reverse=False):
        return self._mod().gref(name).order(list(subs), -1 if reverse else 1)


def host_for(conn) -> Host:
    """The host for ``conn``.

    A native object already on the connection (``conn.iris``, as the tests' stand-ins and the dedicated-native wrappers
    carry) does the work, so ``createIRIS`` is not called on a connection that has run cursor DDL. Otherwise an
    ``EmbeddedConnection`` gets the in-process host, and any other connection a client over ``createIRIS``.
    """
    existing = getattr(conn, "iris", None)
    if existing is not None:
        return ClientHost(conn, native=existing)
    if isinstance(conn, EmbeddedConnection):
        return InProcessHost(conn)
    return ClientHost(conn)
