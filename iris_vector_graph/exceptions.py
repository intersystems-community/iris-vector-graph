"""
IVG-specific exception and warning types.
"""

__all__ = ["NamespaceMismatchWarning", "NamespaceConsistencyError"]


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
