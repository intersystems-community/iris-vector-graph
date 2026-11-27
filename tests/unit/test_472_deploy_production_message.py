"""4.7.2: `scripts/deploy_production.py` says what its class deploy did.

4.7.0 logged "ObjectScript classes deployed." when `deploy_packaged_classes` answered None (no classes ship) and when it
loaded nothing because every class was current; `--mode full` promises the classes, so the first is a failure.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "deploy_production.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Conn:
    def close(self):
        pass


def _deploy(monkeypatch, result, name):
    dp = _load(name)
    monkeypatch.setattr(dp, "_connect", lambda *a: _Conn())
    monkeypatch.setattr(dp, "_deploy_packaged_classes", lambda c: result)
    dp.deploy_classes("h", 1, "IVG470X", "_SYSTEM", "SYS")


def test_no_classes_shipped_is_a_failure_not_a_deploy(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="no ObjectScript classes.*nothing was deployed.*IVG470X"):
            _deploy(monkeypatch, None, "deploy_production_472a")
    assert not [r for r in caplog.records if "classes deployed" in r.getMessage()]


def test_classes_already_current_say_so(monkeypatch, caplog):
    from iris_vector_graph._engine.class_deploy import DeployResult

    with caplog.at_level(logging.INFO):
        _deploy(monkeypatch, DeployResult(unchanged=True), "deploy_production_472b")
    said = [r.getMessage() for r in caplog.records]
    assert any("already current" in m and "nothing was loaded" in m for m in said), said
    assert not any(m.endswith("classes deployed.") for m in said), said


def test_classes_compiled_say_how_many(monkeypatch, caplog):
    from iris_vector_graph._engine.class_deploy import DeployResult

    result = DeployResult(deployed=True, compiled=["Graph.KG.A", "Graph.KG.B"])
    with caplog.at_level(logging.INFO):
        _deploy(monkeypatch, result, "deploy_production_472c")
    said = [r.getMessage() for r in caplog.records]
    assert any("2 ObjectScript classes deployed and compiled" in m and "IVG470X" in m for m in said), said
