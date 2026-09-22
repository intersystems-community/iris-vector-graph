"""`requires_running_container` skips on a *stopped* container, not just a missing one.

The e2e guards it replaces read `docker inspect <name>` and tested the exit code, which is
`0` for a container that exists in any state. `ivg-iris` exists on this machine and is
deliberately kept stopped (its `MaxServerConn=1` makes it unusable for the suite), so
`tests/e2e/test_embedded_wgproto_e2e.py::TestEmbeddedCommunityIRIS` ran its body and failed
with Docker's own `container ... is not running` — a red suite reporting an environment
fact.

The state string is the input here; no Docker daemon is involved.
"""

import pytest

from tests.conftest import requires_running_container


def _is_skipped(marker) -> bool:
    """A `pytest.mark.skipif` records its condition in `args[0]`."""
    return bool(marker.args[0])


def test_a_running_container_does_not_skip(monkeypatch):
    monkeypatch.setattr("tests.conftest.docker_container_state", lambda name: "running")
    assert _is_skipped(requires_running_container("whatever")) is False


@pytest.mark.parametrize("state", ["exited", "created", "paused", "dead", None])
def test_every_other_state_skips(state, monkeypatch):
    monkeypatch.setattr("tests.conftest.docker_container_state", lambda name: state)
    assert _is_skipped(requires_running_container("whatever")) is True


def test_the_reason_names_the_container_and_its_state(monkeypatch):
    """A skip that does not say which container is down sends the reader to Docker."""
    monkeypatch.setattr("tests.conftest.docker_container_state", lambda name: "exited")
    marker = requires_running_container("ivg-iris")
    reason = marker.kwargs["reason"]
    assert "ivg-iris" in reason
    assert "exited" in reason
