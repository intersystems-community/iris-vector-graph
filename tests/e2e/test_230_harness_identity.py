"""Spec 230 US5 (SC-009) — the identity assertion works against the live instance.

The unit tests in `tests/unit/test_230_container_identity.py` prove the comparison and
the probe's connection discipline. This file proves the two facts only a running
container can establish:

1. The Native API route actually returns a hostname on this build, and it is the one
   Docker reports for the named container. If IRIS ever stops answering
   `%SYSTEM.INetInfo::LocalHostName`, `probe_instance_hostname` returns `None`, the
   comparison fails closed, and every container test fails — so this test is the thing
   that tells us *why*.
2. A crossed name fails. Measured 2026-09-21: naming `careconnect-ivg-iris` while the
   connection reached `ivg-iris-enterprise` on the socat path produced six setup
   failures where, before this gate, it produced `6 passed` — the socat path had no
   identity check of any kind, and the old negative probe only looked for a class
   belonging to one specific impostor.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import (
    container_hostname_matches,
    docker_container_hostname,
    docker_container_id,
    probe_instance_hostname,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def _other_running_iris_containers() -> list[str]:
    """Running IRIS containers other than ours, discovered rather than hardcoded.

    `pytest_collect_file` in `tests/conftest.py` forbids other projects' container names
    from appearing in a test file — crossing containers corrupts their data — so this
    reads the live list instead of naming any.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        name
        for name in result.stdout.split()
        if "iris" in name.lower() and name != _CONTAINER
    ]


def test_the_instance_reports_the_named_containers_hostname(iris_connection):
    """The probe reads a hostname, and it is the named container's."""
    reported = probe_instance_hostname(iris_connection)
    assert reported, (
        "the instance returned no hostname. The Native API route "
        "(%SYSTEM.INetInfo::LocalHostName) is the only one that works on this build — "
        "no SQL spelling of it parses, and %SYS.ProcessQuery blocks. If this fails, "
        "every container test now fails closed, which is intended but needs a new route."
    )
    assert container_hostname_matches(
        reported,
        docker_container_hostname(_CONTAINER),
        docker_container_id(_CONTAINER),
    )


def test_another_running_containers_identity_is_rejected(iris_connection):
    """A name that does not match the answering instance fails — SC-009.

    Uses whichever other IRIS container happens to be running, rather than a synthetic
    ID, so the rejection is measured against a real competing identity of the same kind.
    """
    reported = probe_instance_hostname(iris_connection)
    candidates = _other_running_iris_containers()
    if not candidates:
        pytest.skip("no second IRIS container is running to cross-check against")

    other = candidates[0]
    assert not container_hostname_matches(
        reported, docker_container_hostname(other), docker_container_id(other)
    ), (
        f"the harness accepted '{other}' as the identity of the instance that actually "
        f"answered ({reported}). A green suite would then measure the wrong database."
    )
