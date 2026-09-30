"""load_umls_bridges.py logs in with IRIS_USERNAME/IRIS_PASSWORD, not test/test.

iris-devtester 1.20 no longer creates the `test` user, so the old hard-coded login
fails on a fresh container.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest" / "load_umls_bridges.py"


def _load():
    spec = importlib.util.spec_from_file_location("load_umls_bridges", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path, monkeypatch, env):
    for key in ("IRIS_USERNAME", "IRIS_PASSWORD", "IRIS_NAMESPACE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    mrconso = tmp_path / "MRCONSO.RRF"
    mrconso.write_text("")
    module = _load()
    container = mock.Mock()
    container.get_exposed_port.return_value = 41972
    get_connection = mock.Mock()
    monkeypatch.setattr(sys, "argv", ["load_umls_bridges.py", "--mrconso", str(mrconso)])
    with mock.patch("iris_devtester.IRISContainer.attach", return_value=container), mock.patch(
        "iris_devtester.utils.dbapi_compat.get_connection", get_connection
    ), mock.patch.object(module, "load_to_iris"):
        module.main()
    return get_connection.call_args.args


def test_credentials_come_from_environment(tmp_path, monkeypatch):
    args = _run(
        tmp_path,
        monkeypatch,
        {"IRIS_USERNAME": "alice", "IRIS_PASSWORD": "pw", "IRIS_NAMESPACE": "IVGTEST"},
    )
    assert args == ("localhost", 41972, "IVGTEST", "alice", "pw")


def test_defaults_are_not_the_removed_test_user(tmp_path, monkeypatch):
    args = _run(tmp_path, monkeypatch, {})
    assert args == ("localhost", 41972, "USER", "_SYSTEM", "SYS")
