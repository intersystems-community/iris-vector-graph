"""Unit tests for the container-status guard in tests/conftest.py (spec 226, T003).

Why this exists: `IRISContainer.attach` succeeds on a container whose Docker state is
`exited`. The session fixture then deploys ObjectScript into a dead container, every
connection falls through to `localhost:1972`, and the suite reports green against whatever
happens to be listening there. That has voided two measurements in this project.

Gate 1 says a missing container must fail, not skip. A *stopped* container is the same
defect wearing a disguise, so it must fail the same way.

No IRIS connection and no Docker daemon is involved here: the state string is the input.
"""

import pytest

from tests.conftest import container_state_is_running


class TestContainerStateIsRunning:
    def test_running_is_accepted(self):
        assert container_state_is_running("running") is True

    @pytest.mark.parametrize(
        "state",
        [
            "exited",
            "created",
            "paused",
            "restarting",
            "removing",
            "dead",
        ],
    )
    def test_every_other_docker_state_is_rejected(self, state):
        """Docker's state vocabulary is closed; `running` is the only usable member."""
        assert container_state_is_running(state) is False

    def test_absent_state_is_rejected(self):
        """`docker inspect` on a container that no longer exists yields nothing."""
        assert container_state_is_running(None) is False
        assert container_state_is_running("") is False

    def test_whitespace_and_case_are_tolerated(self):
        """`docker inspect --format` output arrives with a trailing newline."""
        assert container_state_is_running("running\n") is True
        assert container_state_is_running("  running  ") is True
        assert container_state_is_running("Running") is True

    def test_substring_of_running_is_not_running(self):
        """A guard written with `in` instead of `==` would pass `not-running`."""
        assert container_state_is_running("not-running") is False
        assert container_state_is_running("running-ish") is False
