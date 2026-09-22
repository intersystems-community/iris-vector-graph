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
