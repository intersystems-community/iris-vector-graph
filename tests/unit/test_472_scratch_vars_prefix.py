"""4.7.2: `scratch_vars.requires` skips unless each variable names a scratch namespace, not only unless it is set.

4.7.0's mark checked that the variable was set. Files that use it write destructively where it points (DELETE FROM the
embedding registry, empty vector tables, DROP TABLE), so `IVG_R470_WR_NS=USER` on a manual run wiped USER. The helpers
that read IVG_NAMESPACE (ns463, ns460alt, reading_role460) already skip a name that is not one of the gate's scratch
prefixes (gate.SCRATCH_PREFIXES); this does too.
"""

from __future__ import annotations

import pytest

from tests.e2e import gate, scratch_vars

VAR = "IVG_R472_PROBE_NS"


@pytest.mark.parametrize("prefix", gate.SCRATCH_PREFIXES)
def test_a_name_with_a_gate_prefix_runs(monkeypatch, prefix):
    monkeypatch.setenv(VAR, f"{prefix}X1")
    mark = scratch_vars.requires(VAR)
    assert mark.args == (False,)


@pytest.mark.parametrize("name", ["USER", "%SYS", "IVGFIX", "IVGFHIR", "ivg470x", "IVG47", "SAMPLES", " IVG470X"])
def test_a_name_that_is_not_a_scratch_namespace_skips_and_says_why(monkeypatch, name):
    monkeypatch.setenv(VAR, name)
    mark = scratch_vars.requires(VAR)
    assert mark.args == (True,)
    reason = mark.kwargs["reason"]
    assert VAR in reason and repr(name) in reason, reason
    assert all(f"{p}*" in reason for p in gate.SCRATCH_PREFIXES), reason


def test_the_first_bad_variable_is_named(monkeypatch):
    monkeypatch.setenv("IVG_R472_A_NS", "IVG470A")
    monkeypatch.setenv("IVG_R472_B_NS", "USER")
    mark = scratch_vars.requires("IVG_R472_A_NS", "IVG_R472_B_NS")
    assert mark.args == (True,)
    assert "IVG_R472_B_NS" in mark.kwargs["reason"]


def test_an_unset_variable_still_says_set_it(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    mark = scratch_vars.requires(VAR)
    assert mark.args == (True,)
    assert mark.kwargs["reason"] == f"set {VAR} to a scratch namespace of this run"
