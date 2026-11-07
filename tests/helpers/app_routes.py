"""Every route an app serves, as it serves it, on FastAPI before and after 0.137.

Up to 0.136, `app.include_router(router, prefix=...)` copied the router's routes into `app.routes` with the prefix
applied. From 0.137 it adds one entry that has no `path` or `methods` and keeps the routes on the router. Tests that
walked `app.routes` and skipped an entry without a path (R097, R107, R109, R112) found no `/graphql`, `/api/cypher`
or `/fhir-event/` on FastAPI 0.162, and passed without trying them. `fastapi.routing.iter_route_contexts` (0.137.2+)
yields the served routes, prefix applied; before 0.137 `app.routes` already is that list. An entry neither reads
(0.137.0 and 0.137.1 have no public way in) raises, so a route is never left out without a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional

try:
    from fastapi.routing import iter_route_contexts
except ImportError:  # FastAPI < 0.137.2: app.routes holds every served route itself
    iter_route_contexts = None


@dataclass(frozen=True)
class Served:
    #: the route as declared: a Mount stays a Mount (its sub-app's routes are `route.app.routes`)
    route: object
    #: the path the router matches, the include's prefix applied
    path: str
    #: None for a WebSocket route or a mount
    methods: Optional[FrozenSet[str]]


def served(routes: Iterable) -> List[Served]:
    """The routes `routes` (an app's or a router's `.routes`) serve, in order. Raises TypeError on an entry whose
    path cannot be read."""
    out = []
    for r in routes:
        if iter_route_contexts is None:
            pairs = [(r, r)]
        else:
            pairs = [(c.original_route, c) for c in iter_route_contexts([r])]
        for declared, view in pairs:
            path = getattr(view, "path", None)
            if path is None:
                raise TypeError(f"{type(declared).__name__} {declared!r} has no path to enumerate (FastAPI "
                                f"0.137.0/0.137.1 included routers have no public route list: use another FastAPI)")
            methods = getattr(view, "methods", None)
            out.append(Served(declared, path, frozenset(methods) if methods else None))
    return out
