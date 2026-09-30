"""`scripts/setup_iris.py` starts the repo's compose service and initializes the schema.

It used to start `intersystemsdc/iris-community:latest-em` through iris-devtester and
run `sql/schema.sql` by hand. That image's entrypoint dies since its 2026-08 rebuild
(see `tests/unit/test_compose_image.py`), and the hand-run DDL is the "DDL-only
namespace" that has no `Graph.KG.*` classes behind it. One image, one schema path:
the compose file the README starts, and `initialize_schema()`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import setup_iris

ROOT = Path(__file__).resolve().parents[2]


def test_it_names_no_image_of_its_own():
    text = (ROOT / "scripts" / "setup_iris.py").read_text()
    assert "intersystemsdc" not in text
    assert "latest-em" not in text


def _run(argv, tmp_path):
    engine = MagicMock()
    with patch.object(setup_iris, "_connect") as connect, patch.object(
        setup_iris, "_engine", return_value=engine
    ), patch.object(setup_iris.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0)
        rc = setup_iris.main(argv + ["--env-file", str(tmp_path / ".env")])
    return rc, connect, engine, run


def test_it_starts_the_repo_compose_service(tmp_path):
    rc, _, _, run = _run([], tmp_path)
    assert rc == 0
    cmd = run.call_args_list[0].args[0]
    assert cmd[:2] == ["docker", "compose"]
    assert str(ROOT / "docker-compose.yml") in cmd
    assert cmd[-3:] == ["up", "-d", "--wait"]


def test_no_start_only_initializes(tmp_path):
    rc, connect, engine, run = _run(["--no-start", "--host", "10.1.2.3", "--port", "1972"], tmp_path)
    assert rc == 0
    run.assert_not_called()
    connect.assert_called_once_with("10.1.2.3", 1972, "USER", "_SYSTEM", "SYS")
    engine.initialize_schema.assert_called_once_with()


def test_it_writes_the_env_file(tmp_path):
    _run(["--no-start", "--port", "1999"], tmp_path)
    env = (tmp_path / ".env").read_text()
    assert "IRIS_HOST=localhost" in env
    assert "IRIS_PORT=1999" in env
    assert "IRIS_NAMESPACE=USER" in env


def test_a_failed_start_is_a_failed_setup(tmp_path):
    with patch.object(setup_iris.subprocess, "run", return_value=MagicMock(returncode=1)):
        with patch.object(setup_iris, "_connect") as connect:
            rc = setup_iris.main(["--env-file", str(tmp_path / ".env")])
    assert rc == 1
    connect.assert_not_called()
