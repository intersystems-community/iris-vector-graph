"""A demo must reach this project's container, and prove it did.

Reported 2026-09-22 while writing `examples/demo_per_graph_embeddings.py` for the 4.0.0
documentation-parity gate. `DemoRunner.get_connection` called
`iris_devtester.connections.auto_detect_iris_host_and_port()`, which on this machine
answered `('localhost', 41972)` — **another project's container**, one this repo has no
business writing to (the workspace registry maps that host port to a different repo). The
demo failed on its first call (`<CLASS DOES NOT EXIST> … *Graph.KG.Eraser`, because that
namespace holds no IVG classes) so nothing was written, but the next demo along would
have created nodes there: `demo_fraud_detection.py`, `demo_fraud_detection_sql.py` and
every other script under `examples/` took the same route.

Auto-discovery is what the workspace rule forbids — each project owns exactly one named
IRIS container and crossing them corrupts data silently. So the resolution is by
**container name**, and the connection is checked against the instance that answered
before the demo runs a single statement.
"""

import pytest

from examples.demo_utils import DemoError, DemoRunner, resolve_demo_container


def test_the_default_container_is_this_projects_own():
    assert resolve_demo_container() == "ivg-iris-enterprise"


def test_the_environment_can_name_a_different_container(monkeypatch):
    """`IVG_TEST_CONTAINER` is the one knob, the same one the suite reads."""
    monkeypatch.setenv("IVG_TEST_CONTAINER", "ivg-iris")
    assert resolve_demo_container() == "ivg-iris"


def test_a_blank_setting_falls_back_rather_than_resolving_nothing(monkeypatch):
    monkeypatch.setenv("IVG_TEST_CONTAINER", "   ")
    assert resolve_demo_container() == "ivg-iris-enterprise"


def test_get_connection_never_calls_auto_detect(monkeypatch):
    """The defect itself: a demo that auto-detects can land on any container.

    Asserted by making auto-detection explode. A `get_connection` that still consults it
    fails this test even if it happens to reach the right instance on this machine.
    """
    import iris_devtester.connections as connections

    def _boom():  # pragma: no cover - the point is that it is never called
        raise AssertionError(
            "get_connection() called auto_detect_iris_host_and_port(); a demo must "
            "resolve its own container by name"
        )

    monkeypatch.setattr(
        connections, "auto_detect_iris_host_and_port", _boom, raising=False
    )
    # Resolution must fail on a container that does not exist, not fall through to
    # auto-detection.
    monkeypatch.setenv("IVG_TEST_CONTAINER", "ivg-no-such-container-for-this-test")
    runner = DemoRunner("connection target", total_steps=1)
    with pytest.raises(DemoError) as excinfo:
        runner.get_connection()
    assert "ivg-no-such-container-for-this-test" in str(excinfo.value.message)


def test_the_demo_error_names_the_container_and_how_to_start_it(monkeypatch):
    monkeypatch.setenv("IVG_TEST_CONTAINER", "ivg-no-such-container-for-this-test")
    runner = DemoRunner("connection target", total_steps=1)
    with pytest.raises(DemoError) as excinfo:
        runner.get_connection()
    steps = " ".join(excinfo.value.next_steps or [])
    assert "enterprise-container.sh" in steps or "test-container.sh" in steps


# 4.1.1 — the demos run from the wheel alone (scripts/quickstart_e2e.py, stage
# `examples`). A user who ran `pip install iris-vector-graph` and `docker compose up`
# has neither iris-devtester nor a container called ivg-iris-enterprise: every demo
# stopped at "iris-devtester package not installed", and demo_rdf_semantic_layer.py
# dialled localhost:21972 whatever was running.


def _no_devtester(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "iris_devtester", None)
    monkeypatch.setitem(sys.modules, "iris_devtester.utils", None)
    monkeypatch.setitem(sys.modules, "iris_devtester.utils.dbapi_compat", None)


def _fake_iris(monkeypatch, calls):
    from unittest.mock import MagicMock

    import iris

    def _connect(host, port, namespace, user, password):
        calls.append((host, port, namespace))
        return MagicMock()

    monkeypatch.setattr(iris, "connect", _connect, raising=False)


def test_without_iris_devtester_the_driver_connects(monkeypatch):
    import examples.demo_utils as du

    _no_devtester(monkeypatch)
    calls = []
    _fake_iris(monkeypatch, calls)
    monkeypatch.setattr(
        du, "_demo_connection_targets", lambda name: [("container IP", "10.0.0.5", 1972)]
    )
    DemoRunner("t", total_steps=1).get_connection()
    assert calls == [("10.0.0.5", 1972, "USER")]


def test_the_compose_container_is_tried_when_none_is_named(monkeypatch):
    import examples.demo_utils as du

    monkeypatch.delenv("IVG_TEST_CONTAINER", raising=False)
    _no_devtester(monkeypatch)
    calls = []
    _fake_iris(monkeypatch, calls)
    targets = {"iris_vector_graph": [("container IP", "10.0.0.9", 1972)]}
    monkeypatch.setattr(du, "_demo_connection_targets", lambda name: targets.get(name, []))
    DemoRunner("t", total_steps=1).get_connection()
    assert calls == [("10.0.0.9", 1972, "USER")]


def test_a_named_container_is_the_only_one_tried(monkeypatch):
    import examples.demo_utils as du

    monkeypatch.setenv("IVG_TEST_CONTAINER", "mine")
    seen = []
    monkeypatch.setattr(du, "_demo_connection_targets", lambda name: seen.append(name) or [])
    with pytest.raises(DemoError):
        DemoRunner("t", total_steps=1).get_connection()
    assert seen == ["mine"]


def test_the_rdf_demo_connects_by_container_name(monkeypatch):
    import examples.demo_rdf_semantic_layer as rdf
    import examples.demo_utils as du

    _no_devtester(monkeypatch)
    calls = []
    _fake_iris(monkeypatch, calls)
    monkeypatch.setenv("IVG_TEST_CONTAINER", "mine")
    monkeypatch.setattr(
        du, "_demo_connection_targets", lambda name: [("container IP", "10.0.0.7", 1972)]
    )
    rdf.connect()
    assert calls == [("10.0.0.7", 1972, "USER")]


def test_the_working_system_demo_sizes_its_vector_from_the_table():
    # scripts/quickstart_e2e.py stage `examples`: demo_rdf_semantic_layer.py had made
    # kg_NodeEmbeddings 4 wide, and a hardcoded 768-wide query vector failed on it.
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "examples/demo_working_system.py").read_text()
    assert "[0.1] * 768" not in src
    assert "get_embedding_dimension(cursor)" in src
