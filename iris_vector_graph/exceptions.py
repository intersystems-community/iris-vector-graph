"""
IVG-specific exception and warning types.
"""

__all__ = [
    "NamespaceMismatchWarning",
    "NamespaceConsistencyError",
    "EmbeddingIdentityConflict",
    "BulkLoadError",
]


class NamespaceMismatchWarning(UserWarning):
    """Raised when the engine's configured namespace does not match the live
    IRIS connection's namespace, or when ^KG globals are not accessible in the
    connected namespace.

    Message always includes: actual namespace, expected namespace, and a fix hint.
    Upgrade to a hard error by setting IVG_STRICT_NAMESPACE=1.
    Suppress entirely with IVG_IGNORE_NAMESPACE_CHECK=1.
    """

    def __init__(self, actual_ns: str, expected_ns: str, hint: str = ""):
        if not hint:
            hint = (
                f"Set namespace='{expected_ns}' or map ^KG globals for '{actual_ns}'"
                " in the CPF file. See README 'Non-USER namespace deployment'."
            )
        self.actual_ns = actual_ns
        self.expected_ns = expected_ns
        self.hint = hint
        super().__init__(
            f"Namespace mismatch: connected to '{actual_ns}', expected '{expected_ns}'. {hint}"
        )


class NamespaceConsistencyError(ValueError):
    """Raised when two IVG components (e.g. engine and vector store) are
    configured for different IRIS namespaces, which would cause silent
    cross-namespace read/write asymmetry.

    Intended for use by IRISGraphRAGBridge (opsreview repo) at construction
    time, once engine.namespace is available (spec 212).
    """

    def __init__(self, actual_ns: str, expected_ns: str, hint: str = ""):
        if not hint:
            hint = (
                f"Set namespace='{expected_ns}' or map ^KG globals for '{actual_ns}'"
                " in the CPF file. See README 'Non-USER namespace deployment'."
            )
        self.actual_ns = actual_ns
        self.expected_ns = expected_ns
        self.hint = hint
        super().__init__(
            f"Namespace inconsistency: engine namespace '{actual_ns}' does not match"
            f" expected '{expected_ns}'. {hint}"
        )


class EmbeddingIdentityConflict(ValueError):
    """Raised when a write's declared embedding identity disagrees with the one recorded
    for the table in ``Graph_KG.embedding_registry`` (spec 226, FR-010).

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers around
    ``store_embedding`` — which already raises ``ValueError`` on a width mismatch — keep
    behaving sensibly.

    **Invariant**: when this is raised, no vector has been written and the registry row is
    unchanged.

    The decisive case is same width, different model: IRIS accepts the INSERT, distances
    compute, and the rankings are meaningless. Nothing below the registry can detect it.

    Spec 227 adds ``graph_id``. A routed table is named after a hash of
    ``(graph, model)``, so "conflict on kg_emb_3f2a1c…" tells an operator nothing
    about which graph refused the write. ``graph_id=None`` means the caller did not
    say, and the message omits the graph rather than claiming the default one.
    """

    def __init__(self, table_name: str, recorded, offered, reason: str, graph_id=None):
        self.table_name = table_name
        self.recorded = recorded
        self.offered = offered
        self.reason = reason
        self.graph_id = graph_id
        where = table_name
        if graph_id is not None:
            # An empty graph ID spliced into a sentence reads as a missing word.
            named = "the default graph" if graph_id == "" else f"graph {graph_id!r}"
            where = f"{table_name} ({named})"
        super().__init__(
            f"Embedding identity conflict on {where}: {reason}\n"
            f"  recorded: {recorded.describe()}\n"
            f"  offered:  {offered.describe()}"
        )


class BulkLoadError(RuntimeError):
    """Raised when a bulk-load batch is rejected for anything but a duplicate row.

    `BulkLoader._executemany_batched` used to log the rejection at ERROR level and
    return only the number of rows that did go in, so `load_edges` reported
    ``{'edges': 0, ...}`` and no exception when every row was refused — measured on
    the enterprise container as ``SQLCODE -121 ... Foreign Key Constraint
    'fk_edges_dest'``. A caller cannot tell that from "nothing new to load".

    The failed batch is rolled back before this is raised, so the database keeps no
    trace of it: `inserted` and `failed` are the only record of how far the load got.
    Duplicates stay tolerated — a re-load of the same rows is idempotent by design —
    and are reported as `skipped` on the successful return instead.
    """

    def __init__(self, phase: str, inserted: int, failed: int, cause: str):
        self.phase = phase
        self.inserted = inserted
        self.failed = failed
        self.cause = cause
        super().__init__(
            f"{phase}: {failed} row(s) rejected after {inserted} inserted, "
            f"and the batch was rolled back: {cause}"
        )
