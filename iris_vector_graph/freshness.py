"""Is a Rebuilt store still current? Answered from the Graph-Version Token and per-store stamps.

A Rebuilt store (CONTEXT.md; ADR-0008) is derived from the authoritative tables and rebuilt on demand instead of being
kept current on every write: ``^NKG``, the two-hop statistics, the Arno snapshot, cached algorithm results. Each one
records the Graph-Version Token it was built from, ``^KG("__version")``, and is current while that equals the live
token. Every write path that can change an answer bumps the token (``$Increment``), so a stamp taken before a write is
never matched after it.

Two rules follow from how the stamp is used:

* ``stamp`` reads the token when it is called, so a rebuild must call it BEFORE it reads the source rows. A stamp taken
  after the build finishes can claim a write that the build did not see is included. A write during the build bumps
  the token past the stamp, which is safe: the store reads as stale.
* A store that was never stamped is not current. The token moving past a stamp makes it stale; nothing moves it back.
  A rolled-back write may leave the token bumped with no content change (``$Increment`` is not undone by a rollback),
  which only costs a rebuild.

The module uses the five host operations only (``iris_vector_graph.host.Host``), so it works the same in a client
process and inside IRIS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, List, Optional

if TYPE_CHECKING:  # the host is passed in; this module does not import it at runtime
    from iris_vector_graph.host import Host

#: The Rebuilt store of the interned adjacency snapshot (``^NKG``).
NKG = "NKG"
#: The Rebuilt store of the two-hop statistics (``deg2p``, ``deg2p_exact``).
DEG2P = "deg2p"

_VERSION_NAME = "^KG"
_VERSION_SUB = "__version"
_STAMP_NAME = "^IVG.BuiltFrom"


class Freshness:
    """Answers "is this store built from the current token?" for any number of Rebuilt stores."""

    def __init__(self, host: "Host"):
        self._host = host

    def _read_int(self, name: str, sub: str) -> Optional[int]:
        """The integer at ``name(sub)``, or None while it is unset or empty."""
        if not (int(self._host.is_defined(name, sub)) & 1):
            return None
        value = self._host.get(name, sub)
        if value is None or value == "":
            return None
        return int(value)

    def token(self) -> int:
        """The current Graph-Version Token: ``^KG("__version")``, or 0 while it is unset."""
        value = self._read_int(_VERSION_NAME, _VERSION_SUB)
        return 0 if value is None else value

    def stamp(self, store: str) -> None:
        """Record that ``store`` is being built from the current token: ``^IVG.BuiltFrom(store) = token``.

        Call this before the rebuild reads its source (see the module docstring).
        """
        self._host.set(self.token(), _STAMP_NAME, store)

    def built_from(self, store: str) -> Optional[int]:
        """The token ``store`` was last stamped with, or None if it was never stamped."""
        return self._read_int(_STAMP_NAME, store)

    def is_current(self, store: str) -> bool:
        """True only when ``store`` was stamped and the stamp equals the live token."""
        return self.built_from(store) == self.token()

    def stale_stores(self, stores: Iterable[str]) -> List[str]:
        """The stores among ``stores`` that are not current, in the order they were given."""
        token = self.token()
        return [store for store in stores if self.built_from(store) != token]
