"""4.6.0 R022: one container recipe, and it does not collide with an IRIS the reader already runs.

The newcomer walkthrough (NR4, NR5) hit a fixed `container_name: iris_vector_graph` and fixed host ports 1972/52773
on a machine that already ran IRIS. The recipe now names no container (compose names it `<project>-iris-1`; `docker
compose ps` shows it) and takes its host ports from `IVG_IRIS_PORT` / `IVG_WEB_PORT`, defaulting to IRIS's own.
README and QUICKSTART say both. The doc-vs-compose agreement for every reader doc is R043's consistency test.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"


def _active(text: str) -> str:
    return "\n".join(line.split(" #", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_no_fixed_container_name():
    assert not re.search(r"(?m)^\s*container_name\s*:", _active(COMPOSE.read_text()))


def test_host_ports_come_from_ivg_iris_port_and_ivg_web_port():
    ports = re.findall(r"""(?m)^\s*-\s*["']([^"']+)["']""", _active(COMPOSE.read_text()))
    assert "${IVG_IRIS_PORT:-1972}:1972" in ports, ports
    assert "${IVG_WEB_PORT:-52773}:52773" in ports, ports
    assert not any(p in ("1972:1972", "52773:52773") for p in ports), ports


def test_the_service_is_named_iris():
    """The name `docker compose exec iris ...` and the demo/test discovery by label rely on."""
    assert re.search(r"(?m)^services:\s*\n  iris:\s*$", COMPOSE.read_text())


def test_readme_and_quickstart_say_how_to_change_the_ports_and_find_the_container():
    for doc in ("README.md", "docs/setup/QUICKSTART.md"):
        text = (ROOT / doc).read_text()
        assert "IVG_IRIS_PORT" in text and "IVG_WEB_PORT" in text, doc
        assert "docker compose ps" in text, doc
        assert "iris_vector_graph shows" not in text, doc  # the old fixed name
