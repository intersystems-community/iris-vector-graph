"""`docker compose up` has to start IRIS on the image the quickstart names.

`intersystemsdc/iris-community:latest-em` moved to a 2026-08 rebuild of
2026.1.0.234.1com that ships irissqlcli 0.6.0 without the `intersystems_iris`
driver. Its entrypoint creates the namespace through irissqlcli, which calls
`iris.dbapi.connect`, and under that image's `irispython` the name resolves to
an ObjectScript package wrapper:

    [ERROR] Cannot call an iris.package wrapper. ... Given name was: dbapi.connect
    [FATAL] Error executing post-startup command

and the container exits (reproduced on 2026-09-30; the June build of the same
tag had the driver). The official image has no such entrypoint step, `USER`
exists out of the box, and the post-start `UnExpireUserPasswords` keeps
`_SYSTEM`/`SYS`. `initialize_schema()`, a write and a Cypher read were checked
against it the same day.
"""

from __future__ import annotations

from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _iris_service() -> dict:
    """The keys this file checks, read as text: PyYAML is not a dependency."""
    service: dict = {"environment": []}
    section = None
    for raw in COMPOSE.read_text().splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 4 and ":" in stripped:
            key, _, value = stripped.partition(":")
            section = key
            if value.strip():
                service[key] = value.strip()
        elif indent == 6 and section == "environment" and stripped.startswith("- "):
            service["environment"].append(stripped[2:])
        elif indent == 0:
            section = None
    return service


def test_the_image_is_the_official_one():
    image = _iris_service()["image"]
    assert image.startswith("containers.intersystems.com/intersystems/iris-community:"), image


def test_the_tag_is_a_pinned_release_not_a_moving_one():
    tag = _iris_service()["image"].rsplit(":", 1)[1]
    assert not tag.startswith("latest"), tag
    assert tag[:4].isdigit(), tag


def test_no_environment_the_official_image_ignores():
    """`IRISNAMESPACE`/`ISC_DEFAULT_PASSWORD` were the community image's, and the
    official one reads neither; left in, they claim a configuration nothing applies."""
    env = _iris_service().get("environment") or []
    names = {e.split("=", 1)[0] for e in env}
    assert not names & {"IRISNAMESPACE", "ISC_DEFAULT_PASSWORD"}, names


def test_passwords_are_unexpired_after_start():
    assert "UnExpireUserPasswords" in _iris_service()["command"]
