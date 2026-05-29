"""Spec 164 — Module-level state for k-hop seed-local fast path.

Holds the once-per-Python-process flag for the `^NKG`-missing RuntimeWarning
emitted by `engine.khop_seedlocal()` when falling back to `^KG` walk
(FR-164-008 / Q3 clarification). Mirrors the `engine._nkg_dirty` pattern at
engine.py:3389.
"""

_khop_nkg_warning_emitted: bool = False


def reset_khop_warnings() -> None:
    """Re-arm the per-process `^NKG`-missing warning flag.

    Called by tests (AS-164-4) that need to verify the warning fires without
    depending on a fresh Python process.
    """
    global _khop_nkg_warning_emitted
    _khop_nkg_warning_emitted = False
