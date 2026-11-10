"""4.7.0: the E2E helpers that check IVG_NAMESPACE themselves accept every scratch prefix the gate does.

tests/e2e/ns460alt.py and tests/e2e/reading_role460.py checked `NS.startswith(("IVG460", "IVG262"))`, their own
list, while the gate (tests/e2e/gate.py, 96799b74) honours IVG470* and IVG463* too. In a run with IVG_NAMESPACE=IVG470*
the seven modules that use them (test_460_r202_reading_role_e2e, r282, r291, r294, r298, r311_translator_injection,
test_462_shortest_path_named_graph_e2e) skipped every test: 62 skips in one 4.7.0 run. They read gate.SCRATCH_PREFIXES.
"""

from __future__ import annotations

import pytest

from tests.e2e import gate, ns460alt, reading_role460


@pytest.mark.parametrize("helper", [ns460alt, reading_role460], ids=["ns460alt", "reading_role460"])
@pytest.mark.parametrize("prefix", gate.SCRATCH_PREFIXES)
def test_every_gate_prefix_is_a_usable_namespace(helper, prefix, monkeypatch):
    monkeypatch.setattr(helper, "NS", f"{prefix}X")
    assert helper._usable_namespace()


@pytest.mark.parametrize("helper", [ns460alt, reading_role460], ids=["ns460alt", "reading_role460"])
@pytest.mark.parametrize("ns", ["USER", "IVGFIX", "ivg470x", ""])
def test_anything_else_is_not(helper, ns, monkeypatch):
    monkeypatch.setattr(helper, "NS", ns)
    assert not helper._usable_namespace()


@pytest.mark.parametrize("helper", [ns460alt, reading_role460], ids=["ns460alt", "reading_role460"])
def test_the_skip_reason_names_the_gates_prefixes(helper):
    reason = helper.requires_namespace[0].kwargs["reason"]
    assert all(f"{p}*" in reason for p in gate.SCRATCH_PREFIXES), reason
