"""The two sides of the comparison and the guard that keeps the harness inside its own namespaces (spec 250)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
HOST = "localhost"
PORT = 31972
USER = "_SYSTEM"
PASSWORD = "SYS"
MARKER = ".alt-bench-side"

# The only namespaces this harness may create, write to or load data into.
OWN_NAMESPACES = ("IVGBENCHA", "IVGBENCHB", "IVGBENCHSMOKE", "KGBENCHA", "KGBENCHB", "SNBBENCHA", "SNBBENCHB", "SNB1A", "SNB1B", "SNBFASTA", "SNBFASTB", "ALGOA", "ALGOB", "SNBCLEANA", "SNBCLEANB", "SNB10A", "SNB10B", "SNBLDB")  # SNBLDB: scratch load timing

# Everything the harness keeps between steps (side trees, datasets, the comparison venv). Not /tmp: the benchmark host reboots and
# wipes it, and a step that finds its tree or dataset gone fails hours into a run.
BENCH_HOME = Path(os.environ.get("IVG_BENCH_HOME", str(Path.home() / ".cache" / "ivg-bench")))
COMP_PYTHON = str(BENCH_HOME / "comp-venv" / "bin" / "python")


def data_root(name):
    """Where a generated dataset lives (``snb01``, ``snb1``, ``snb10``): ``<BENCH_HOME>/<name>/out``, the datagen's own layout below."""
    return str(BENCH_HOME / name / "out")


BASELINE_REF = "6f17be0f"
DEFAULT_ALT_REF = "alt-integration"


@dataclass(frozen=True)
class Side:
    letter: str
    ref: str
    namespace: str
    tree: str


SIDES = {
    "A": Side("A", BASELINE_REF, "IVGBENCHA", str(BENCH_HOME / "ivg-src-A")),
    "B": Side("B", DEFAULT_ALT_REF, "IVGBENCHB", str(BENCH_HOME / "ivg-src-B")),
}


def with_alt_ref(ref: str) -> dict:
    """SIDES with side B pointed at another ref."""
    return {"A": SIDES["A"], "B": replace(SIDES["B"], ref=ref)}


def check_namespace(ns: str) -> str:
    if ns not in OWN_NAMESPACES:
        raise ValueError(f"namespace {ns!r} is not one of {OWN_NAMESPACES}; the harness never touches others")
    return ns
