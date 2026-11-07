"""The skip an E2E file takes when the scratch namespace it needs is not named: at setup, not at import.

The gate (tests/e2e/gate.py GROUPS) names a namespace per run in a variable such as `IVG_R174_NAMESPACE`, set while the
module is collected and while its tests run. Outside the gate the variable is unset and the file's tests skip, saying
which variable to set. That skip used to be `pytest.skip(..., allow_module_level=True)`; a module skipped at import
fails a gate run as a whole (`IVG_GATE=1`), the bare `pytest --collect-only` included. As a mark, the module imports
and each test skips at setup, which the gate fails test by test (a scratch namespace not provided is never an allowed
skip: tests/unit/test_460_r309_gate_skips.py).

    pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_R174_NAMESPACE")]

The variables are read when this is called, at the module's import, which is when the gate has them set.
"""

from __future__ import annotations

import os

import pytest


def requires(*variables: str) -> pytest.MarkDecorator:
    """A skipif mark: true when one of `variables` is unset or empty, naming the first such."""
    unset = [v for v in variables if not os.environ.get(v)]
    if not unset:
        return pytest.mark.skipif(False, reason="")
    return pytest.mark.skipif(True, reason=f"set {unset[0]} to a scratch namespace of this run")
