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


def _is_scratch(name: str) -> bool:
    """An upper-case name starting with one of the gate's scratch prefixes (`gate.SCRATCH_PREFIXES`), as the helpers that
    read IVG_NAMESPACE (ns463, ns460alt, reading_role460) take one."""
    from tests.e2e import gate

    return name.upper() == name and name.startswith(gate.SCRATCH_PREFIXES)


def requires(*variables: str) -> pytest.MarkDecorator:
    """A skipif mark: true when one of `variables` is unset or empty, naming the first such, or holds a name that is not
    a scratch namespace (4.7.2: files that use this write destructively where the variable points, and
    `IVG_R470_WR_NS=USER` on a manual run wiped USER), naming the first such."""
    from tests.e2e import gate

    unset = [v for v in variables if not os.environ.get(v)]
    if unset:
        return pytest.mark.skipif(True, reason=f"set {unset[0]} to a scratch namespace of this run")
    for variable in variables:
        name = os.environ[variable]
        if not _is_scratch(name):
            shown = ", ".join(f"{p}*" for p in gate.SCRATCH_PREFIXES)
            return pytest.mark.skipif(
                True, reason=f"{variable}={name!r} is not a scratch namespace (an upper-case {shown} name)"
            )
    return pytest.mark.skipif(False, reason="")
