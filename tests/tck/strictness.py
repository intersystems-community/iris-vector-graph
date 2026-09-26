"""The TCK harness's escape hatch back to its pre-spec-229 scoring.

``IVG_TCK_LENIENT=1`` restores the old behaviour so earlier baselines (e.g. the
3894/3897 result-shape figure) stay reproducible: an error counts as an empty
result, and the side-effect steps pass without measuring. It is read on every call
so a test can flip it.
"""
import os


def lenient() -> bool:
    return os.environ.get("IVG_TCK_LENIENT", "").strip().lower() in ("1", "true", "yes")
