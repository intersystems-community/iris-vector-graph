"""scripts/load_drkg.py loads into the namespace it is given (USER by default, which the drkg-load suites redirect).

DRKG queried against Neo4j and Memgraph needs a namespace of its own: USER is the tests' and the loader wipes nothing
but writes 5.87M edges.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "load_drkg.py"


def _module():
    spec = importlib.util.spec_from_file_location("load_drkg_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("kwargs,ns", [({}, "USER"), ({"namespace": "DRKGB"}, "DRKGB")])
def test_the_loader_connects_to_its_namespace(kwargs, ns, monkeypatch):
    mod = _module()
    fake_iris = MagicMock()
    fake_iris.connect.side_effect = RuntimeError("stop after connect")
    monkeypatch.setitem(sys.modules, "iris", fake_iris)
    with pytest.raises(RuntimeError, match="stop after connect"):
        mod.load_drkg("h", 1, **kwargs)
    assert fake_iris.connect.call_args.args[:3] == ("h", 1, ns)
