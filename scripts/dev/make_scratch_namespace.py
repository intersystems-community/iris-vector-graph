"""Create a scratch namespace with its own database on the test container, then put IVG in it.

    python scripts/dev/make_scratch_namespace.py IVG262ALT [IVG262UPG ...]
    python scripts/dev/make_scratch_namespace.py --no-install IVG460FXA      (the namespace and its database only)
    python scripts/dev/make_scratch_namespace.py --split IVG460FXS    (globals in IVG460FXSD, routines in IVG460FXSR)
    python scripts/dev/make_scratch_namespace.py --drop IVG262UPG     (IVG262*, IVG460INV*, IVG460FX*, IVG460DC*, IVG463*, IVG470* only)

Only names starting IVG262, IVG460, IVG463 or IVG470 (4.6.3's and 4.7.0's scratch work) are accepted, so this cannot touch a namespace another suite or project uses. A
namespace that already exists is left as it is. A namespace with its own database is the only kind a parallel run can
use: one mapped to USER shares ^KG and the routines with every suite that uses USER.

IVG is installed from the checkout this script is in: the tables by a child interpreter with that checkout first on
PYTHONPATH (which checks where it imported `iris_vector_graph` from), the classes by that checkout's
`scripts/enterprise-container.sh tcp-deploy` run in that checkout. The venv's editable install may point elsewhere.

Container and port come from IVG_TEST_CONTAINER and IVG_PORT (tests/conftest.py's defaults: ivg-iris-enterprise, 31972).
The namespace and its databases are made and dropped over TCP on 127.0.0.1:IVG_PORT (scripts/dev/tcp_setup.py), never
through `iris session`, which Cortex XDR kills; the container name only reaches the install (IVG_ARNO_CONTAINER).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_DEV_DIR = str(Path(__file__).resolve().parent)
if _DEV_DIR not in sys.path:
    sys.path.insert(0, _DEV_DIR)
import tcp_setup  # noqa: E402  (scripts/dev/tcp_setup.py: namespaces and databases over TCP, no `iris session`)

#: The checkout this script belongs to: what `install_ivg` installs.
REPO = Path(__file__).resolve().parents[2]
ALLOWED = ("IVG262", "IVG460", "IVG463", "IVG470")
#: The namespaces `drop` may delete: spec 262's; the inventory test's (tests/e2e/test_262_inventory.py), which needs a
#: namespace nothing has touched; and the per-worker scratch names (IVG460FX* for fix work, IVG460DC* for parallel doc
#: runs through IVG_DOCTEST_NAMESPACE); IVG463* for 4.6.3 fix work, IVG470* for 4.7.0's. Other IVG460* namespaces are never dropped: IVG460DOC (tests/docs/docs.toml)
#: holds the doc harness's baseline, IVG460ALT is tests/e2e/ns460alt.py's default.
DROPPABLE = ("IVG262", "IVG460INV", "IVG460FX", "IVG460DC", "IVG463", "IVG470")
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def _upper_starting(ns: str, prefixes: tuple[str, ...]) -> bool:
    return bool(ns) and ns.upper() == ns and ns.startswith(prefixes)


def may_create(ns: str) -> bool:
    """An upper-case name starting one of ALLOWED."""
    return _upper_starting(ns, ALLOWED)


def may_drop(ns: str) -> bool:
    """An upper-case name starting one of DROPPABLE."""
    return _upper_starting(ns, DROPPABLE)


def split_databases(ns: str) -> dict:
    """`--split`'s two databases of `ns`, each with a resource of its own: {"globals"|"routines": (name, resource)}."""
    return {"globals": (f"{ns}D", f"%DB_{ns}D"), "routines": (f"{ns}R", f"%DB_{ns}R")}


def make(ns: str, split: bool = False) -> bool:
    """Create `ns` with a database of the same name; False when it already existed. `split`: its globals in `<ns>D`
    and its routines in `<ns>R` instead, each database on a resource of its own (`%DB_<ns>D`, `%DB_<ns>R`).
    Over TCP (scripts/dev/tcp_setup.py), as _SYSTEM in %SYS on 127.0.0.1:IVG_PORT."""
    with tcp_setup.sys_native(PORT) as native:
        if not split:
            return tcp_setup.make_namespace(native, ns)
        dbs = split_databases(ns)
        return tcp_setup.make_namespace(
            native, ns, globals_db=dbs["globals"][0], routines_db=dbs["routines"][0], resources=dict(dbs.values()),
        )


def drop(ns: str) -> bool:
    """Delete `ns`, its Task Manager tasks and the databases it maps (DROPPABLE only): the one wipe that leaves nothing
    behind, classes and globals included. Only a database `make` names for `ns` is deleted (`ns`, or `--split`'s
    `<ns>D` and `<ns>R`, with their resources), read from the namespace's configuration. False when it did not exist.
    Over TCP, as `make`."""
    if not may_drop(ns):
        raise ValueError(f"refusing to drop {ns!r}: only upper-case {'* or '.join(DROPPABLE)}* namespaces are dropped")
    with tcp_setup.sys_native(PORT) as native:
        return tcp_setup.drop_namespace(native, ns, own=(ns, f"{ns}D", f"{ns}R"))


def recreate(ns: str) -> None:
    """An empty `ns` with a new database of its own: nothing of IVG's or anyone's in it."""
    drop(ns)
    make(ns)


#: Run by the child interpreter: argv = repo, namespace, port. Refuses a package imported from anywhere else.
_SETUP = """
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
IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
conn.close()
"""


def _env(ns: str) -> dict:
    """This checkout first on PYTHONPATH; this interpreter's directory first on PATH (tcp-deploy runs `python3`,
    which needs the driver)."""
    pythonpath = os.environ.get("PYTHONPATH")
    return dict(
        os.environ,
        PYTHONPATH=str(REPO) + (os.pathsep + pythonpath if pythonpath else ""),
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        IVG_NAMESPACE=ns,
        IVG_PORT=str(PORT),
        IVG_ARNO_CONTAINER=CONTAINER,
    )


def install_command(ns: str) -> tuple[list[str], dict, str]:
    """The child that creates the tables: (argv, env, cwd)."""
    return [sys.executable, "-c", _SETUP, str(REPO), ns, str(PORT)], _env(ns), str(REPO)


def deploy_command(ns: str) -> tuple[list[str], dict, str]:
    """This checkout's tcp-deploy, run in this checkout (it globs iris_src/src relative to its directory)."""
    return [str(REPO / "scripts" / "enterprise-container.sh"), "tcp-deploy"], _env(ns), str(REPO)


def install_ivg(ns: str) -> None:
    """Tables first (the classes compile against them), then the classes over TCP, both from this checkout."""
    for argv, env, cwd in (install_command(ns), deploy_command(ns)):
        subprocess.run(argv, check=True, env=env, cwd=cwd)


USAGE = (
    "usage: make_scratch_namespace.py [--no-install] [--split] NS...   (upper case, starting " + " or ".join(ALLOWED) + ")\n"
    "       make_scratch_namespace.py --drop NS...           (upper case, starting " + ", ".join(DROPPABLE) + ")"
)


def main(argv: list[str]) -> int:
    """Every name is checked before anything is created or dropped."""
    flags = {a for a in argv if a.startswith("--")}
    names = [a for a in argv if not a.startswith("--")]
    dropping = "--drop" in flags
    rule = may_drop if dropping else may_create
    unknown = sorted(flags - {"--drop", "--no-install", "--split"})
    refused: list[str] | str
    if unknown:
        refused = unknown
    elif not names:
        refused = "no names"
    elif dropping and flags & {"--no-install", "--split"}:
        refused = "--drop with " + " and ".join(sorted(flags & {"--no-install", "--split"}))
    else:
        refused = [n for n in names if not rule(n)]
    if refused:
        print(f"{USAGE}\nrefused: {refused}")
        return 2
    if dropping:
        for ns in names:
            print(f"{ns}: {'dropped' if drop(ns) else 'absent'}")
        return 0
    for ns in names:
        created = make(ns, split=True) if "--split" in flags else make(ns)
        print(f"{ns}: {'created' if created else 'already exists'}")
        if created and "--no-install" not in flags:
            install_ivg(ns)
            print(f"{ns}: IVG installed from {REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
