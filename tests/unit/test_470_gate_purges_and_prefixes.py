"""4.7.0 gate: the E2E files that purge cached queries or set table statistics get an exclusive namespace, and the
files that check IVG_NAMESPACE themselves read gate.SCRATCH_PREFIXES, which the gate still recognises.

tests/e2e/test_retire_plan_stats_e2e.py calls %SYSTEM.SQL.Purge and sets rdf_edges' extent size and selectivity, in
the session namespace (conftest's iris_connection): a statement another file prepares there meanwhile loses its cached
query class (the r308 family's failure, test_470_gate_r308_exclusive.py), and every plan there reads the statistics.
It gets the "purges" group, exclusive. (test_254_purge_e2e's purge is TemporalIndexMS.PurgeRawBefore of its own
graph's rows, no cached-query purge: it stays.) test_460_r306_algorithm_{fixes,oracle}_e2e and test_460_scc_rows_e2e
checked `NS.startswith(("IVG460", "IVG262"))`, missing IVG470* and IVG463*.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.e2e import gate

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"
PURGERS = {"test_retire_plan_stats_e2e.py"}
OWN_CHECKS = ["test_460_r306_algorithm_fixes_e2e.py", "test_460_r306_algorithm_oracle_e2e.py",
              "test_460_scc_rows_e2e.py"]


def test_the_purges_group_is_exclusive():
    assert gate.GROUPS["purges"].exclusive and gate.GROUPS["purges"].kind == "graph_kg"


def test_only_the_purging_files_read_its_namespace_and_they_read_no_other():
    readers = {p.name: gate.needs(p.read_text()) for p in sorted(E2E.glob("test_*.py"))}
    assert {n for n, g in readers.items() if "purges" in g} == PURGERS
    for n in PURGERS:
        assert readers[n] == {"purges"}, (n, readers[n])


def test_no_e2e_file_purges_cached_queries_outside_an_exclusive_group():
    """Every test file that calls %SYSTEM.SQL.Purge reads only exclusive groups' namespaces."""
    exclusive = {g for g, grp in gate.GROUPS.items() if grp.exclusive}
    for p in sorted(E2E.glob("test_*.py")):
        src = p.read_text()
        if '"%SYSTEM.SQL", "Purge"' in src:
            got = gate.needs(src)
            assert got and got <= exclusive, (p.name, got)


def test_its_per_run_name_is_no_other_groups():
    names = gate.GateNamespaces(REPO, run_id="AB12", env={}, available=lambda: True).names()
    assert [g for g, n in names.items() if n == names["purges"]] == ["purges"]


@pytest.mark.parametrize("src", [
    "import os\nfrom tests.e2e.gate import SCRATCH_PREFIXES\nNS = os.environ.get('IVG_NAMESPACE', '')\n"
    "ok = NS.startswith(SCRATCH_PREFIXES)\n",
    "import os\nfrom tests.e2e import gate\nNS = os.environ.get('IVG_NAMESPACE', '')\n"
    "ok = NS.startswith(gate.SCRATCH_PREFIXES)\n",
])
def test_a_check_against_the_gates_prefixes_needs_the_primary_namespace(src):
    assert gate.needs(src) == {"primary"}


@pytest.mark.parametrize("name", OWN_CHECKS)
def test_the_files_checking_ivg_namespace_read_the_gates_prefixes(name):
    tree = ast.parse((E2E / name).read_text())
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in ("IVG460", "IVG262")]
    assert literals == [], f"{name} lists its own prefixes: {literals}"
    assert "primary" in gate.needs((E2E / name).read_text())
