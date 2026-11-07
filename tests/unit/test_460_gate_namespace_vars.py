"""4.6.0: every namespace an E2E file reads from the environment is one the gate names per run (tests/e2e/gate.py).

R309's GROUPS held eight variables; the E2E files read about thirty. A run of the R308 differential took its default,
`IVG_NS_IVG460FXOC`, which is another agent's live namespace: its cached-query purges collided with that agent's run.
Each namespace variable an E2E file, helper or conftest reads (`os.environ.get(...)` / `os.environ[...]`, as the gate
reads them) must now be a GROUPS variable, so a run gets its own name, or be on gate.SESSION_NAMESPACES, the commented
list of the few a run cannot name per module. And no E2E default names a namespace: outside the gate a GROUPS
variable left unset is the empty string, and the file skips saying which variable to set.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.e2e import gate

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"
#: A variable naming a namespace: IVG_NAMESPACE, IVG_*_NAMESPACE, IVG_*_NS, IVG_NS_*, IVG262_*_NS
NAMESPACE_VAR = re.compile(r"^IVG\w*(NAMESPACE|_NS)$|^IVG_NS_\w+$")
#: Read by a tool's default port or container, not a namespace (a false match of the pattern would land here)
NOT_NAMESPACES: set = set()


def _sources():
    yield from sorted(E2E.rglob("*.py"))
    yield REPO / "tests" / "conftest.py"


def _namespace_reads() -> dict:
    out: dict = {}
    for path in _sources():
        for var in gate._env_reads(ast.parse(path.read_text())):
            if NAMESPACE_VAR.match(var) and var not in NOT_NAMESPACES:
                out.setdefault(var, set()).add(path.relative_to(REPO).as_posix())
    return out


def _defaults(var: str) -> list:
    """(file, default) of every `os.environ.get(var, <default>)` under tests/e2e."""
    out = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ"
                    and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == var):
                default = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
                out.append((path.relative_to(REPO).as_posix(), default))
    return out


def test_the_reads_are_found():
    # the scan sees the variables this change is about, so an empty answer cannot pass the next test
    reads = _namespace_reads()
    assert {"IVG_NAMESPACE", "IVG_NS_IVG460FXOC", "IVG_NS_IVG460FXOV", "IVG262_UPG_NS"} <= set(reads)


def test_every_namespace_variable_is_named_per_run_or_listed():
    grouped = {g.var for g in gate.GROUPS.values()}
    stray = {v: sorted(files) for v, files in _namespace_reads().items()
             if v not in grouped and v not in gate.SESSION_NAMESPACES}
    assert stray == {}, f"add a GROUPS entry (or a SESSION_NAMESPACES line saying why not): {stray}"


def test_the_listed_variables_are_read_and_say_why():
    reads = _namespace_reads()
    for var, why in gate.SESSION_NAMESPACES.items():
        assert var in reads, f"{var} is listed but no E2E file reads it"
        assert len(why) > 40, var


@pytest.mark.parametrize("var", sorted({g.var for g in gate.GROUPS.values()} - {"IVG_NAMESPACE"}))
def test_no_default_names_a_namespace(var):
    named = [(f, d) for f, d in _defaults(var) if d not in (None, "")]
    assert named == [], f"{var}: outside the gate an unset variable is '' and the file skips; these name one: {named}"


def test_each_group_has_its_own_suffix():
    suffixes = [g.suffix for g in gate.GROUPS.values()]
    assert len(suffixes) == len(set(suffixes))
    # no suffix is another's prefix-extended twin read back from a name (IVG460FXG<id><suffix>)
    assert all(re.fullmatch(r"[A-Z0-9]+", s) for s in suffixes)


@pytest.mark.parametrize("name, group", [
    ("test_460_r308_cypher_differential_e2e.py", "r308"),
    ("test_460_r308_comparison_threshold_e2e.py", "r308"),
    ("test_460_r307_value_matrix_e2e.py", "r307"),
    ("test_262_two_engines.py", "ns262alt"),
    ("test_262_upgrade_451.py", "upg262"),
    ("test_460_r173_refusal_snapshot_e2e.py", "r173"),
    ("test_degree_cache_live.py", "paros"),
    ("test_460_mcp_toolset_compile_order_e2e.py", "mcporder"),
])
def test_a_module_reading_one_needs_its_group(name, group):
    assert group in gate.needs((E2E / name).read_text())


def test_a_graph_kg_kind_is_made_and_a_name_kind_is_left_to_its_file():
    # the differential and the matrix need IVG installed (and an IVG460FX* name, which IVG460FXG<id><suffix> is)
    assert gate.GROUPS["r308"].kind == gate.GROUPS["r307"].kind == "graph_kg"
    assert gate.GROUPS["r173"].kind == "name"
    names = gate.GateNamespaces(REPO, run_id="AB12", env={}, available=lambda: True).names()
    assert names["r308"].startswith("IVG460FX") and names["r307"].startswith("IVG460FX")
