"""Put the working tree and its pure-Python dependencies into IRIS's embedded Python path.

Used by the tests that run Python-language ObjectScript methods, which import iris_vector_graph
inside the IRIS process. Skips when the dependencies cannot be installed (no network).
"""

from __future__ import annotations

import os
import subprocess

import pytest

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
SITE = "/usr/irissys/mgr/python"
DEPENDENCIES = ("numpy", "networkx", "obonet")


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=300, **kw)


def install_package() -> None:
    # "dir/." copies the contents over whatever is there: a plain `docker cp dir dest` into an
    # existing destination nests the package one level down and leaves the old code importable.
    copied = sh("docker", "cp", "iris_vector_graph/.", f"{CONTAINER}:{SITE}/iris_vector_graph")
    assert copied.returncode == 0, copied.stderr
    for module in DEPENDENCIES:
        probe = sh("docker", "exec", CONTAINER, "python3", "-c", f"import sys; sys.path.insert(0,'{SITE}'); import {module}")
        if probe.returncode != 0:
            install = sh("docker", "exec", CONTAINER, "python3", "-m", "pip", "install", "--quiet", "--target", SITE, module)
            if install.returncode != 0:
                pytest.skip(f"{module} cannot be installed in the container: {install.stderr[-200:]}")
