"""4.6.0 R309: the two plugins that keep an E2E run from looking green while files did not run.

`GateNamespaces`: the scratch namespaces the gated E2E files need, made for this run from this checkout.
    98 files skip unless `IVG_NAMESPACE` names an IVG460*/IVG262* scratch namespace, and read the variable when they
    are imported; the session's own namespace (tests/conftest.py, USER by default) must stay where it is. So the
    variable is set for those modules only, while each is collected and while each of its tests sets up, runs and
    tears down, and the helpers that read it at import (HELPERS) see the same name. The namespaces the collected (and
    not deselected) tests need are made together once collection ends, before the first test runs, and dropped after
    the last; never mid-run, never under --collect-only, never when no collected module needs one. Kinds (GROUPS):

    primary    IVG_NAMESPACE for the modules that check `startswith("IVG460...")` or use
               `reading_role460.requires_namespace` or `ns463.requires_namespace`: a namespace with IVG installed for
               `Graph_KG`
               (scripts/dev/make_scratch_namespace.py: `make` then `install_ivg`, this checkout's tables and classes)
    rag        IVG_NAMESPACE for the modules that use `ns460alt.requires_namespace`: IVG set up with
               `schema_prefix="RAG"` by this checkout's `initialize_schema()`
    secondary  IVG_SECONDARY_NAMESPACE: a second namespace with IVG installed for `Graph_KG`
    split      IVG_SPLIT_NAMESPACE: `make_scratch_namespace.py --split` (globals and routines apart)
    s236       IVG_NS_IVG236, the spec 236 scratch (no default, R287), for the modules that read it or use the
               `ivg_236_scratch` fixture: IVG installed for `Graph_KG` (the fixture deploys again over it)
    load470, setup470
               IVG_LOAD_NS, IVG_SETUP_NS: `make_scratch_namespace.py --no-install`, nothing installed (4.7.0 rows 2
               and 5 time and inspect the first setup)
    dry_run, r299a, r299b
               IVG_DRY_RUN_NS, IVG_NS_IVG460FX299A, IVG_NS_IVG460FX299B: a name only; those files make and drop the
               namespace themselves, and the name is this run's, so two runs never share one (R191)

    Names are `IVG460FXG<run id><suffix>`, upper case, which `make_scratch_namespace.may_drop` accepts. A variable the
    caller set is left alone (IVG_NAMESPACE when it already names an IVG470*/IVG463*/IVG460*/IVG262* namespace), and its namespace is
    neither made nor dropped. `IVG_GATE_NAMESPACES=0` turns this off (and with `SKIP_IRIS_TESTS=true`, or with the test
    container not running, there is nothing to make it on); `IVG_GATE_KEEP_NAMESPACES=1` leaves this run's namespaces
    in place at the end. Container and port: IVG_TEST_CONTAINER and IVG_PORT, as make_scratch_namespace reads them.

`GateSkips` (`IVG_GATE=1`, set by scripts/ci-parity.sh): a skipped test under tests/e2e fails unless a line of
    tests/e2e/gate_skips.txt allows it: `<node id fnmatch pattern> | <text the skip message contains> | <why>`. A
    module skipped at import (`allow_module_level`, `importorskip`) cannot be turned into a failure, so it is listed at
    the end and fails the run's exit status. xfail is not a skip and is left alone.
"""

from __future__ import annotations

import ast
import contextlib
import fnmatch
import importlib
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pytest

REPO = Path(__file__).resolve().parents[2]
#: Which helper's `requires_namespace` puts a module in which group.
_HELPER_GROUP = {"ns460alt": "rag", "reading_role460": "primary", "ns463": "primary"}
#: Modules under tests/e2e that read IVG_NAMESPACE at import into `NS`; patched to the module's namespace.
HELPERS = tuple(f"tests.e2e.{stem}" for stem in _HELPER_GROUP)
SCRATCH_PREFIXES = ("IVG470", "IVG463", "IVG460", "IVG262")
NAME_PREFIX = "IVG460FXG"


@dataclass(frozen=True)
class Group:
    var: str
    suffix: str
    #: graph_kg (make + install_ivg), rag (make + initialize_schema RAG), split (make --split), empty (make, nothing
    #: installed: `--no-install`), name (nothing)
    kind: str


GROUPS: Dict[str, Group] = {
    "primary": Group("IVG_NAMESPACE", "P", "graph_kg"),
    "rag": Group("IVG_NAMESPACE", "R", "rag"),
    "secondary": Group("IVG_SECONDARY_NAMESPACE", "S", "graph_kg"),
    "split": Group("IVG_SPLIT_NAMESPACE", "X", "split"),
    "s236": Group("IVG_NS_IVG236", "236", "graph_kg"),
    "dry_run": Group("IVG_DRY_RUN_NS", "DRY", "name"),
    "r299a": Group("IVG_NS_IVG460FX299A", "299A", "name"),
    "r299b": Group("IVG_NS_IVG460FX299B", "299B", "name"),
    # 4.6.0: every other namespace an E2E file reads (tests/unit/test_460_gate_namespace_vars.py). Their defaults named
    # fixed namespaces, one of them another agent's live one (IVG460FXOC); outside the gate they are '' and the file
    # skips. graph_kg: the file needs IVG installed (the differential and the value matrix also need an IVG460FX* name,
    # which these are); name: the file makes (or replays a release into) and drops its own.
    "r308": Group("IVG_NS_IVG460FXOC", "OC", "graph_kg"),
    "r307": Group("IVG_NS_IVG460FXOV", "OV", "graph_kg"),
    "r314": Group("IVG_NS_IVG460FXGV", "GV", "graph_kg"),
    "fhirless": Group("IVG_FHIRLESS_NAMESPACE", "NOF", "graph_kg"),
    "paros": Group("IVG_PAROS_NAMESPACE", "PAR", "graph_kg"),
    "edges320": Group("IVG262_EDGES320_NS", "E320", "name"),
    "loader320": Group("IVG262_LOADER320_NS", "L320", "name"),
    "notupg": Group("IVG262_NOTUPG_NS", "NUP", "name"),
    "r235": Group("IVG262_R235_NS", "235", "name"),
    "r293": Group("IVG262_R293_NS", "293", "name"),
    "upg262": Group("IVG262_UPG_NS", "UPG", "name"),
    "via262": Group("IVG262_VIA_NS", "VIA", "name"),
    "inventory": Group("IVG_INVENTORY_NS", "INV", "name"),
    "legacy": Group("IVG_LEGACY_NAMESPACE", "LEG", "name"),
    "new252": Group("IVG_NS_IVG252NEW", "252", "name"),
    "ns262alt": Group("IVG_NS_IVG262ALT", "ALT", "name"),
    "fx8": Group("IVG_NS_IVG262FX8", "FX8", "name"),
    "fx8d": Group("IVG_NS_IVG262FX8D", "FX8D", "name"),
    "rcd262": Group("IVG_NS_IVG262RCD", "RCD", "name"),
    "ns262upg": Group("IVG_NS_IVG262UPG", "U262", "name"),
    "legacy463": Group("IVG_NS_IVG463L", "463L", "name"),
    "fx24b": Group("IVG_NS_IVG460FX24B", "24B", "name"),
    "r173": Group("IVG_R173_NAMESPACE", "173", "name"),
    "r174": Group("IVG_R174_NAMESPACE", "174", "name"),
    "r175": Group("IVG_R175_NAMESPACE", "175", "name"),
    "r303": Group("IVG_R303_NAMESPACE", "303", "name"),
    "nullgraph": Group("IVG_UPGRADE_NULL_NS", "NUL", "name"),
    "mcporder": Group("IVG_MCP_ORDER_NAMESPACE", "MCP", "name"),
    "r316upg": Group("IVG_R316_UPG_NS", "316U", "name"),
    # R265's BFSJson case is about a namespace other than USER: it read IVG_NAMESPACE with the default USER, where it
    # skips, and nothing gave it another (gate run d004e79e). graph_kg: it writes Graph_KG rows and loads Arno there.
    "r265bfs": Group("IVG_R265_NAMESPACE", "265", "graph_kg"),
    # The demo and benchmark files load their own data (tests/e2e/own_data_ns.py); the biomedical sample loader
    # TRUNCATEs every IVG table, so none of them may load into the session namespace. graph_kg: IVG installed.
    "biodemo": Group("IVG_BIODEMO_NAMESPACE", "BIO", "graph_kg"),
    "frauddemo": Group("IVG_FRAUDDEMO_NAMESPACE", "FRD", "graph_kg"),
    "lazynodes": Group("IVG_LAZY_NODES_NAMESPACE", "LAZ", "graph_kg"),
    "chunked": Group("IVG_CHUNKED_NAMESPACE", "CHK", "graph_kg"),
    # test_253_degree_tally: its cached queries failed <SYNTAX> in USER when another module recompiled classes there
    "degtally": Group("IVG_DEGREE_TALLY_NAMESPACE", "DTL", "graph_kg"),
    # 4.6.3: test_463_half_setup_writes_e2e takes its namespace back to a half-finished setup (tables, a class and the
    # record removed), so it needs one of its own; test_463_refused_reads_e2e makes a user without SELECT there.
    "r463half": Group("IVG_R463_HALF_NS", "463H", "graph_kg"),
    "r463priv": Group("IVG_R463_PRIV_NS", "463P", "graph_kg"),
    # 4.6.4: test_464_caller_txn_e2e makes a table of its own (the caller's rows) beside IVG's and opens transactions
    # on several connections at once, so it has a namespace to itself.
    "r464txn": Group("IVG_R464_TXN_NS", "464T", "graph_kg"),
    # 4.7.0: test_470_caller_txn_e2e is 4.6.4's pattern for the writers 4.6.4 left out (a caller table of its own);
    # test_470_ledger_caller_txn_e2e enables the ledger and verifies it against the whole graph, so no other file may
    # write there.
    "r470txn": Group("IVG_R470_TXN_NS", "470T", "graph_kg"),
    "r470ledger": Group("IVG_R470_LEDGER_NS", "470L", "graph_kg"),
    # 4.7.0 wave 2 (writers): test_470_silent_writers_e2e and the W2-WR files rebuild ^KG and change indexes on
    # rdf_edges, so they have a namespace of their own.
    "r470wr": Group("IVG_R470_WR_NS", "470W", "graph_kg"),
    # 4.6.5: test_465_write_schema_error_e2e deletes Txn_Begin from the classes of a namespace of its own (and puts
    # them back); test_465_stale_error_text_e2e makes a user holding the reading role there and runs failing writes.
    "r465legacy": Group("IVG_R465_LEGACY_NS", "465L", "name"),
    "r465role": Group("IVG_R465_ROLE_NS", "465R", "graph_kg"),
    # 4.7.0 rows 2 and 5: test_470_node_load_new_namespace_e2e times the first load into a namespace and
    # test_470_setup_compiles_every_class_e2e checks what the first initialize_schema() compiles, so each needs one
    # with nothing installed yet.
    "load470": Group("IVG_LOAD_NS", "470LD", "empty"),
    "setup470": Group("IVG_SETUP_NS", "470SU", "empty"),
}
#: Namespace variables an E2E module cannot be given per run, and why (tests/unit/test_460_gate_namespace_vars.py).
SESSION_NAMESPACES: Dict[str, str] = {
    "IVG_NS_IVG262FHA": (
        "a FHIR-capable namespace set up under the IVG Schema IVGALT (tests/e2e/test_262_alt_schema_fhir.py): only "
        "the CONTRIBUTING.md recipe makes a FHIR server, which the gate cannot, and the FHIR session namespace "
        "holds Graph_KG; unset, the file skips"
    ),
    "IVG_NS_IVGFHIR": (
        "the FHIR session namespace: tests/conftest.py imports tests/e2e/fhir_conftest.py at startup, before this "
        "plugin can scope a variable to a module, and its session fixtures drop and rebuild it (setup_namespace) "
        "for every run, so a run never reads another's; that namespace is the FHIR suite's own"
    ),
    "IVG_R314_NAMESPACE": (
        "a namespace on another server (IVG_R314_HOST and IVG_R314_PORT, tests/e2e/test_460_r314_call_yield_values_e2e"
        ".py), set up there by whoever runs it, so that both builds can be run against one file; unset, the file skips"
    ),
    "IVG_OLD_NAMESPACE": (
        "a namespace set up by an older release's checkout (4.6.5: `scripts/dev/make_scratch_namespace.py` run from a "
        "worktree at that tag), never by this one's initialize_schema(), so that tests/e2e/test_470_ppr_old_namespace"
        "_e2e.py reads this release's engine against the older functions and classes; the gate builds only this "
        "release's namespaces, so whoever runs the file sets one up; unset, the file skips"
    ),
    "IVG_ISSUE13_NS": (
        "USER on a throwaway IRIS Community 2026.1 container, the build GitHub #13 failed on "
        "(tests/e2e/test_470_issue13_count_distinct_limit_e2e.py), which the gate cannot make. On a free port P: "
        "`docker run -d --name ivg-c261 -p P:1972 containers.intersystems.com/intersystems/iris-community:2026.1`; "
        "once healthy, `docker exec -i ivg-c261 iris session IRIS -U %SYS` and "
        "`Do ##class(Security.Users).UnExpireUserPasswords(\"*\")` (the password stays SYS); then IVG_ISSUE13_NS=USER "
        "IVG_ISSUE13_PORT=P and IVG_GATE_NAMESPACES=0, one connection at a time (Community), and `docker rm -f "
        "ivg-c261` after. On the enterprise container it runs in an IVG470* scratch namespace; unset, the file skips"
    ),
}
#: The variables read only by the files they name, so a module that reads one needs that group.
_BY_VAR = {g.var: name for name, g in GROUPS.items() if g.var != "IVG_NAMESPACE"}


def _in_order(groups: Iterable[str]) -> List[str]:
    return sorted(groups, key=list(GROUPS).index)


# --------------------------------------------------------------------------- which modules need which namespace


def _env_reads(tree: ast.AST) -> set:
    """Every `os.environ.get("X", ...)` / `os.environ["X"]` variable name."""
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ"
                and node.args and isinstance(node.args[0], ast.Constant)):
            out.add(node.args[0].value)
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
              and node.value.attr == "environ" and isinstance(node.slice, ast.Constant)):
            out.add(node.slice.value)
    return out


def _checks_a_scratch_prefix(tree: ast.AST) -> bool:
    """A `.startswith(...)` whose argument is a scratch prefix or a tuple holding one."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            for arg in node.args:
                consts = arg.elts if isinstance(arg, ast.Tuple) else [arg]
                if any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                       and c.value.startswith(SCRATCH_PREFIXES) for c in consts):
                    return True
    return False


def _helper_aliases(tree: ast.AST) -> Dict[str, str]:
    """{local name: helper module stem} for `from tests.e2e import ns460alt as alt` and the like."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("tests.e2e", None):
            for a in node.names:
                if a.name in _HELPER_GROUP:
                    out[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                stem = a.name.rsplit(".", 1)[-1]
                if stem in _HELPER_GROUP and a.asname:
                    out[a.asname] = stem
    return out


def needs(source: str) -> set:
    """The groups (GROUPS keys) a test module's source needs."""
    tree = ast.parse(source)
    reads = _env_reads(tree)
    # a variable named as a string anywhere (`os.environ.get(v)` over a tuple of names counts too)
    named = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    out = {_BY_VAR[v] for v in named if v in _BY_VAR}
    if "ivg_236_scratch" in source:  # tests/conftest.py's fixture reads IVG_NS_IVG236 when it runs
        out.add("s236")
    aliases = _helper_aliases(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "requires_namespace"
                and isinstance(node.value, ast.Name) and node.value.id in aliases):
            out.add(_HELPER_GROUP[aliases[node.value.id]])
    if "IVG_NAMESPACE" in reads and _checks_a_scratch_prefix(tree):
        out.add("primary")
    return out


# --------------------------------------------------------------------------- namespaces


def _msn():
    path = str(REPO / "scripts" / "dev")
    if path not in sys.path:
        sys.path.insert(0, path)
    import make_scratch_namespace

    return make_scratch_namespace


#: The child interpreter that sets a namespace up for `RAG`: argv = repo, namespace, port. Like make_scratch_namespace's
#: own child, it refuses a package imported from anywhere but this checkout.
_RAG_SETUP = """
import sys
from pathlib import Path

import iris
import iris_vector_graph
from iris_vector_graph import IRISGraphEngine

repo = Path(sys.argv[1]).resolve()
origin = Path(iris_vector_graph.__file__).resolve()
if repo not in origin.parents:
    sys.exit(f"iris_vector_graph imported from {origin}, not from {repo}")
conn = iris.connect("localhost", int(sys.argv[3]), sys.argv[2], "_SYSTEM", "SYS")
IRISGraphEngine(conn, embedding_dimension=8, namespace=sys.argv[2], schema_prefix="RAG").initialize_schema()
conn.close()
"""


def default_setup(kind: str, ns: str) -> None:
    """Make `ns` as `kind` says, from this checkout."""
    msn = _msn()
    if kind == "graph_kg":
        msn.make(ns)
        msn.install_ivg(ns)
    elif kind == "rag":
        msn.make(ns)
        argv, env, cwd = msn.install_command(ns)
        subprocess.run([argv[0], "-c", _RAG_SETUP, str(REPO), ns, str(msn.PORT)], check=True, env=env, cwd=cwd)
    elif kind == "split":
        msn.make(ns, split=True)
    elif kind == "empty":
        msn.make(ns)
    elif kind != "name":
        raise ValueError(f"unknown namespace kind {kind!r}")


def default_drop(ns: str) -> bool:
    return _msn().drop(ns)


def container_running() -> bool:
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "true"


class GateNamespaces:
    """See the module docstring. `setup`, `drop` and `available` are replaceable for the unit tests."""

    def __init__(self, root: Path, *, run_id: Optional[str] = None, env: Optional[dict] = None,
                 setup: Callable[[str, str], None] = default_setup, drop: Callable[[str], bool] = default_drop,
                 available: Callable[[], bool] = container_running, helpers: Iterable[str] = HELPERS):
        self.root = Path(root)
        self.run_id = run_id or uuid.uuid4().hex[:4].upper()
        self._environ = os.environ if env is None else env
        self._setup, self._drop, self._available = setup, drop, available
        self._helpers = tuple(helpers)
        self._on: Optional[bool] = None
        self._made: Dict[str, Optional[BaseException]] = {}
        self._used: set = set()
        self._needs: Dict[Path, set] = {}
        self._helpers_loaded = False
        self.messages: List[str] = []
        #: The groups whose variable the caller set. Read once, before this plugin sets anything: its own overrides
        #: are not the caller's.
        self._caller: set = {g for g, grp in GROUPS.items() if self._caller_named(grp)}

    # -- the plan

    def names(self) -> Dict[str, str]:
        return {g: f"{NAME_PREFIX}{self.run_id}{grp.suffix}" for g, grp in GROUPS.items()}

    def _caller_named(self, group: Group) -> bool:
        value = self._environ.get(group.var, "")
        if group.var == "IVG_NAMESPACE":
            return value.startswith(SCRATCH_PREFIXES)
        return bool(value)

    def on(self) -> bool:
        if self._on is None:
            self._on = (self._environ.get("IVG_GATE_NAMESPACES", "1") != "0"
                        and self._environ.get("SKIP_IRIS_TESTS", "false").lower() != "true"
                        and self._available())
        return self._on

    def owned(self) -> List[str]:
        """The names this run makes or hands out, and so drops."""
        if not self.on():
            return []
        names = self.names()
        return [names[g] for g in GROUPS if g not in self._caller]

    def env_for(self, groups: Iterable[str]) -> Dict[str, str]:
        groups = set(groups)
        if not groups or not self.on():
            return {}
        if {"primary", "rag"} <= groups:
            raise ValueError("a module cannot need both a Graph_KG and a RAG namespace in IVG_NAMESPACE")
        names = self.names()
        return {GROUPS[g].var: names[g] for g in sorted(groups - self._caller)}

    def prepare(self, groups: Iterable[str]) -> None:
        """Make every group's namespace, all at once before the first test (never mid-run: namespace creation and
        drops overlapping heavy test writes preceded an ivg-iris-enterprise freeze, 2026-11-05). A failure is
        recorded and raised by `check` for each test that needs that namespace; the other groups are still made."""
        groups = set(groups)
        if not groups or not self.on():
            return
        names = self.names()
        for g in _in_order(groups - self._caller):
            if g in self._made:
                continue
            self._used.add(g)
            self._made[g] = None
            kind = GROUPS[g].kind
            if kind != "name":  # a name-only group's files make it: the name is all this run provides
                try:
                    self._setup(kind, names[g])
                except Exception as exc:  # recorded and raised for each test that needs it
                    self._made[g] = exc

    def check(self, groups: Iterable[str]) -> None:
        """Raise for a test whose namespace failed, or was not made at session start; never make one."""
        groups = set(groups)
        if not groups or not self.on():
            return
        names = self.names()
        for g in _in_order(groups - self._caller):
            if g not in self._made:
                raise RuntimeError(f"R309 gate: {names[g]} ({g}) was not made at session start")
            err = self._made[g]
            if err is not None:
                raise RuntimeError(f"R309 gate: could not make {names[g]} ({g}): {type(err).__name__}: {err}") from err

    def finish(self) -> None:
        if not self._used:
            return
        names = self.names()
        if self._environ.get("IVG_GATE_KEEP_NAMESPACES") == "1":
            self.messages.append(f"R309 gate: kept {sorted(names[g] for g in self._used)} (IVG_GATE_KEEP_NAMESPACES=1)")
            return
        for g in _in_order(self._used):
            try:
                self._drop(names[g])
            except Exception as exc:  # reported in the terminal summary, never silent
                self.messages.append(f"R309 gate: could not drop {names[g]}: {type(exc).__name__}: {exc}")

    # -- per module

    def _groups(self, path) -> set:
        p = Path(str(path))
        if p not in self._needs:
            e2e_test = (p.suffix == ".py" and p.name.startswith("test_")
                        and p.resolve().is_relative_to(self.root.resolve() / "tests" / "e2e"))
            self._needs[p] = needs(p.read_text()) if e2e_test else set()
        return self._needs[p]

    @contextlib.contextmanager
    def _scoped(self, env: Dict[str, str]):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        ns = env.get("IVG_NAMESPACE")
        patched = []
        if ns is not None:
            for name in self._helpers:
                mod = sys.modules.get(name)
                if mod is not None and hasattr(mod, "NS"):
                    patched.append((mod, mod.NS))
                    mod.NS = ns
        try:
            yield
        finally:
            for mod, old in patched:
                mod.NS = old
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def _load_helpers(self) -> None:
        """Import each helper under its group's name, so its import-time marks see a usable namespace."""
        if self._helpers_loaded:
            return
        self._helpers_loaded = True
        for name in self._helpers:
            group = _HELPER_GROUP.get(name.rsplit(".", 1)[-1])
            env = self.env_for({group}) if group else {}
            if not env:
                continue
            with self._scoped(env):
                if name in sys.modules:
                    importlib.reload(sys.modules[name])
                else:
                    importlib.import_module(name)

    # -- hooks

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(self, collector):
        env = {}
        if isinstance(collector, pytest.Module):
            groups = self._groups(collector.path)
            if groups & {"primary", "rag"}:
                self._load_helpers()
            env = self.env_for(groups)
        with self._scoped(env):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        with self._scoped(self.env_for(self._groups(item.path))):
            yield

    def pytest_collection_finish(self, session):
        """After collection and deselection, before the first test: the namespaces the collected items need."""
        if session.config.option.collectonly:
            return
        self.prepare(set().union(*(self._groups(item.path) for item in session.items)))

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        self.check(self._groups(item.path))

    def pytest_sessionfinish(self, session):
        self.finish()

    def pytest_terminal_summary(self, terminalreporter):
        for m in self.messages:
            terminalreporter.write_line(m)


# --------------------------------------------------------------------------- skips


@dataclass
class Allowed:
    pattern: str
    message: str
    why: str
    line: int
    used: int = 0


def parse_allow_list(text: str) -> List[Allowed]:
    """`pattern | message | why` per line; `#` comments and blank lines ignored. Raises ValueError naming the line."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"line {n}: want '<node id pattern> | <skip message text> | <why>': {raw!r}")
        pattern, message, why = parts
        if ".py" not in pattern:
            raise ValueError(f"line {n}: {pattern!r} names no test file; an entry is for a file or its tests")
        out.append(Allowed(pattern, message, why, n))
    return out


def match(entries: List[Allowed], nodeid: str, message: str) -> Optional[Allowed]:
    for e in entries:
        if fnmatch.fnmatchcase(nodeid, e.pattern) and e.message in message:
            return e
    return None


def _skip_message(report) -> str:
    lr = report.longrepr
    if isinstance(lr, tuple) and len(lr) == 3:
        return str(lr[2])
    return str(lr)


class GateSkips:
    SCOPE = "tests/e2e/"

    def __init__(self, entries: List[Allowed], source: str):
        self.entries = entries
        self.source = source
        self.failed: List[tuple] = []
        self.modules: List[tuple] = []

    def _check(self, nodeid: str, message: str) -> bool:
        e = match(self.entries, nodeid, message)
        if e is not None:
            e.used += 1
            return True
        return False

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        rep = outcome.get_result()
        if not rep.skipped or hasattr(rep, "wasxfail") or not rep.nodeid.startswith(self.SCOPE):
            return
        message = _skip_message(rep)
        if self._check(rep.nodeid, message):
            return
        rep.outcome = "failed"
        rep.longrepr = (f"IVG_GATE=1: this test skipped and {self.source} allows no such skip.\n"
                        f"  skip: {message}\n"
                        f"  Make it run, or add a reviewed line '<pattern> | <message text> | <why>'.")
        self.failed.append((rep.nodeid, message))

    def pytest_collectreport(self, report):
        if report.skipped and report.nodeid.startswith(self.SCOPE):
            message = _skip_message(report)
            if not self._check(report.nodeid, message):
                self.modules.append((report.nodeid, message))

    def pytest_sessionfinish(self, session):
        if self.modules and session.exitstatus == 0:
            session.exitstatus = 1

    def pytest_terminal_summary(self, terminalreporter):
        tr = terminalreporter
        if self.failed or self.modules:
            tr.section(f"IVG_GATE=1: skips {self.source} does not allow")
            for nodeid, message in self.failed + self.modules:
                tr.write_line(f"{nodeid}: {message}")
            if self.modules:
                tr.write_line(f"{len(self.modules)} module(s) skipped at import: the run fails")
        unused = [e for e in self.entries if not e.used]
        if unused and tr.config.getoption("verbose", 0) > 0:
            tr.section("IVG_GATE=1: allow-list lines no skip matched in this run")
            for e in unused:
                tr.write_line(f"line {e.line}: {e.pattern} | {e.message}")


def register_skips(config) -> None:
    """With IVG_GATE=1, the skip gate over `<rootdir>/tests/e2e/gate_skips.txt`; a list that does not parse stops
    the run."""
    if os.environ.get("IVG_GATE") != "1":
        return
    source = "tests/e2e/gate_skips.txt"
    path = Path(str(config.rootpath)) / source
    try:
        entries = parse_allow_list(path.read_text())
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"IVG_GATE=1: {path}: {exc}") from exc
    config.pluginmanager.register(GateSkips(entries, source), "ivg-gate-skips")


def register(config) -> None:
    """Both plugins, from tests/conftest.py's pytest_configure."""
    register_skips(config)
    if not config.pluginmanager.has_plugin("ivg-gate-namespaces"):
        config.pluginmanager.register(GateNamespaces(Path(str(config.rootpath))), "ivg-gate-namespaces")
