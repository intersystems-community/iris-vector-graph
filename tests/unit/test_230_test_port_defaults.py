"""Spec 230 US5 — no test module defaults its connection to a port this repo does not own.

Found by the T074 gate, where it was producing two failure clusters that both
read as something else.

`tests/e2e/test_large_output_chunked.py` and `test_lazy_node_resolution.py` read
`IVG_TEST_PORT` — a name nothing in this repo exports — defaulting to `2972`,
which belongs to another project's container. The file connected there, found no
`Graph.KG` deployment, and reported

    AssertionError: StoreLargeOut must be compiled into NKGAccel   assert '0' == '1'
    [SQLCODE: <-30>] Table 'SQLUSER.RDF_EDGES' not found

as though this tree were broken. Both claims were false. Probed directly against
`ivg-iris-enterprise`, `%Dictionary.CompiledMethod.%ExistsId` answers `1` for
`Graph.KG.NKGAccel||StoreLargeOut` and `||ReadLargeOutChunk`, and
`SQLUser.rdf_edges` is a real view over `Graph_KG.rdf_edges` — same row count,
`INFORMATION_SCHEMA.VIEWS` lists it — so that spelling was never the problem.

`test_stress_setup.py`, `test_stress_api.py` and `test_untested_methods.py` read
`IRIS_PORT` defaulting to `1972`, and **nothing is listening on host 1972**. A
connection there fails with

    <COMMUNICATION LINK ERROR> Failed to connect to server

which was read as a driver or licensed-connection problem for as long as it went
undiagnosed. It is a refused connection to a port no container publishes.

Reading an unexported variable is the whole defect: the default is then the only
value that ever applies, no matter what the run exports. `IVG_PORT` is what
`tests/conftest.py` and every other direct-connect module reads, and it defaults
to `31972`.

The stakes are higher than a wrong number. A port in `FOREIGN_PORTS` belongs to
another project, and a test that writes through one corrupts data it does not own.
"""

from __future__ import annotations

import re
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
_SELF = Path(__file__).resolve()

#: Named by project, not by container: `tests/conftest.py` refuses to collect any
#: test module that spells another project's container name.
FOREIGN_PORTS = {
    "1972": "no container publishes it; a connection is refused",
    "2972": "the iris-pgwire project",
    "4972": "no container publishes it",
    "11972": "the productivity-framework project",
    "11975": "the objectscript-mcp project",
    "12972": "the posos project",
    "19720": "the careconnect project",
    "51972": "the pyprod project",
}

#: Ports this repo's own containers publish. `31972` is the enterprise container's
#: SuperServer and the default everywhere; `31971` is that same container's second
#: mapping, which the Arno fixtures use; `21972` is Community, kept for the two-core
#: machines that still run it.
OWN_PORTS = {"31972", "31971", "21972"}

#: The variables the harness actually exports. A module may read either name, but
#: `IVG_PORT` is the convention and `IVG_ARNO_PORT` is the one documented exception.
EXPORTED_PORT_VARS = {"IVG_PORT", "IVG_ARNO_PORT"}

_PORT_DEFAULT = re.compile(
    r"""os\.environ(?:\.get\(\s*|\[)["'](?P<var>[A-Z_]*PORT)["']\s*"""
    r"""(?:,\s*["'](?P<default>\d+)["']\s*)?\)""",
)


def _port_defaults():
    """Every `os.environ.get("…PORT", …)` in a module pytest collects.

    Scoped to `test_*.py` plus `conftest.py` deliberately. `tests/benchmarks/`
    holds hand-run scripts that read the documented `IRIS_PORT`; they are a
    separate question from what the suite connects to, and they are recorded in
    `docs/KNOWN_ISSUES.md` rather than guarded here.
    """
    found = []
    candidates = sorted(_TESTS_DIR.rglob("test_*.py")) + sorted(_TESTS_DIR.rglob("conftest.py"))
    for path in candidates:
        if "__pycache__" in path.parts or path.resolve() == _SELF:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            for m in _PORT_DEFAULT.finditer(line):
                found.append((path, lineno, m.group("var"), m.group("default")))
    return found


def _rel(path: Path) -> str:
    return path.relative_to(_TESTS_DIR).as_posix()


def test_the_scan_finds_the_port_defaults_it_is_meant_to_check():
    """A guard that matches nothing passes for the wrong reason."""
    assert _port_defaults(), (
        'no `os.environ.get("…PORT", …)` found in a collected module — the regex is wrong'
    )


def test_no_test_defaults_to_a_port_this_repo_does_not_own():
    offenders = [
        (_rel(path), lineno, var, default)
        for path, lineno, var, default in _port_defaults()
        if default in FOREIGN_PORTS
    ]
    assert not offenders, "\n".join(
        f"tests/{p}:{ln} reads {var} defaulting to {d} — {FOREIGN_PORTS[d]}"
        for p, ln, var, d in offenders
    )


def test_every_port_default_is_one_of_this_repos_containers():
    """A default outside `OWN_PORTS` is either foreign or a typo; both are wrong."""
    offenders = [
        (_rel(path), lineno, var, default)
        for path, lineno, var, default in _port_defaults()
        if default is not None and default not in OWN_PORTS
    ]
    assert not offenders, "\n".join(
        f"tests/{p}:{ln} reads {var} defaulting to {d}; expected one of {sorted(OWN_PORTS)}"
        for p, ln, var, d in offenders
    )


def test_every_port_default_reads_a_variable_the_harness_exports():
    """An unexported variable means the default is the only value that ever applies."""
    offenders = [
        (_rel(path), lineno, var)
        for path, lineno, var, _default in _port_defaults()
        if var not in EXPORTED_PORT_VARS
    ]
    assert not offenders, "\n".join(
        f"tests/{p}:{ln} reads {var}, which nothing exports — use IVG_PORT"
        for p, ln, var in offenders
    )
