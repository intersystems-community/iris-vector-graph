"""Pure helpers for graph-scoped embedding routes (specs 227 and 230).

Names and a predicate, no connection, no state. They live outside ``_engine/``
because a route name and a graph predicate are facts about the naming scheme, not
about a particular engine instance — the migration, the CLI and the test suite all
need them without opening a database.

What is *not* here: finding an existing route. That is a registry read
(``resolve_route`` in :mod:`iris_vector_graph._engine.embeddings`), because IRIS
may have derived a class name the table name does not predict, and the registry row
is the only authority on what a route is actually called.
"""

import hashlib
from typing import Optional

from .constants import EDGE_ROUTE_TABLE_PREFIX, ROUTE_TABLE_PREFIX

#: Characters of the hex digest that go into a table name. 16 hex characters is
#: 64 bits: enough that a collision across the measured ceiling of 100 routes per
#: namespace is not a practical concern, and short enough that the derived class
#: name stays far below IRIS's 220-character limit (research R4).
_HASH_CHARS = 16

#: Separates the graph from the model inside the hashed string. A NUL cannot occur
#: in either value, so ``("a", "bc")`` and ``("ab", "c")`` cannot collide. Without
#: it both hash "abc" and two graphs would share one physical table — at two
#: declared widths, which IRIS refuses at INSERT with SQLCODE -104.
_SEP = "\0"


def route_table_name(graph_id: str, model_key: Optional[str]) -> str:
    """Name of the routed embedding table for ``(graph_id, model_key)``.

    ``kg_emb_`` + the first 16 hex characters of
    ``sha256(graph_id + "\\0" + (model_key or ""))``. Deterministic across
    processes and restarts — ``sha256``, not ``hash()``, which is salted per
    interpreter.

    ``model_key=None`` is spec 226's undeclared identity and hashes the same as
    ``""``: a caller that never named a model has one identity, not two.

    The name is deliberately opaque. A readable ``kg_emb_<graph>_<model>`` breaks
    on two IRIS rules at once — DDL strips underscores when deriving the class name
    and de-duplicates collisions with a numeric suffix, and the derived class name
    has a 220-character ceiling. A hash sidesteps both, and keeps a caller-supplied
    graph ID out of an identifier entirely.

    Never call this to *find* a route; read the registry row instead.
    """
    return _route_name(ROUTE_TABLE_PREFIX, graph_id, model_key)


def edge_route_table_name(graph_id: str, model_key: Optional[str]) -> str:
    """Name of the routed *edge* embedding table for ``(graph_id, model_key)`` (FR-006).

    ``kg_eemb_`` + the same 16 hex characters :func:`route_table_name` derives, so
    every property documented there holds here: stable across processes, opaque,
    ``None`` model key equal to ``""``, ``None`` graph refused.

    Only the prefix differs, and it has to: the node route for a pair holds
    ``(graph_id, node_id)`` rows with an FK to ``nodes``, the edge route holds
    ``(graph_id, s, p, o_id)``. Sharing one table would put edge triples behind a
    node FK. Sharing one *prefix* would be just as bad more quietly — the admin row
    count and the migration's table scan classify a table by its prefix.

    Never call this to *find* a route; read the registry row instead. Its ``kind``
    column is what separates the two kinds there.
    """
    return _route_name(EDGE_ROUTE_TABLE_PREFIX, graph_id, model_key)


def _route_name(prefix: str, graph_id: str, model_key: Optional[str]) -> str:
    """The shared derivation. One body, so the two kinds cannot drift apart."""
    if graph_id is None:
        raise TypeError("graph_id is a string; the default graph is '', not None")
    payload = f"{graph_id}{_SEP}{model_key or ''}".encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()[:_HASH_CHARS]


def graph_scope_predicate(column: str = "graph_id", placeholder: str = "?") -> str:
    """A WHERE fragment matching one graph, with both sides ``COALESCE``d.

    Returns ``(COALESCE(<column>, '') = COALESCE(<placeholder>, ''))``.

    Both sides, because both can be ``NULL`` and both ``NULL``\\s mean the default
    graph:

    * **Left.** ``graph_id`` is nullable in every table that gained it by
      ``ADD COLUMN``. ``create_edge`` writes ``''`` explicitly; an INSERT that omits
      the column leaves ``NULL``.
    * **Right.** In IRIS embedded SQL an empty host variable binds as SQL ``NULL``,
      so ``graph_id = :tName`` for the default graph compiles to ``graph_id = NULL``
      and matches nothing — quietly, with ``SQLCODE 100``.

    Either omission turns a default-graph query into a zero-row answer that reads
    as an empty graph. ``Graph.KG.Eraser`` carries the same rule in ObjectScript.

    The graph value is bound, never interpolated: it is caller-supplied (FR-032).
    """
    if not isinstance(column, str):
        raise TypeError(f"column must be a string, got {type(column).__name__}")
    if not column.strip():
        raise ValueError("column must name a column; an empty name generates COALESCE(, '')")
    if not isinstance(placeholder, str) or not placeholder.strip():
        raise ValueError("placeholder must be a non-empty parameter marker")
    return f"(COALESCE({column.strip()}, '') = COALESCE({placeholder.strip()}, ''))"
