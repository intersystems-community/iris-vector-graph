"""Create a scratch namespace with its own database on the test container, then put IVG in it.

    python scripts/dev/make_scratch_namespace.py IVG262ALT [IVG262UPG ...]
    python scripts/dev/make_scratch_namespace.py --no-install IVG460FXA      (the namespace and its database only)
    python scripts/dev/make_scratch_namespace.py --drop IVG262UPG     (IVG262*, IVG460INV*, IVG460FX*, IVG460DC* only)

Only names starting IVG262 or IVG460 are accepted, so this cannot touch a namespace another suite or project uses. A
namespace that already exists is left as it is. A namespace with its own database is the only kind a parallel run can
use: one mapped to USER shares ^KG and the routines with every suite that uses USER.

IVG is installed from the checkout this script is in: the tables by a child interpreter with that checkout first on
PYTHONPATH (which checks where it imported `iris_vector_graph` from), the classes by that checkout's
`scripts/enterprise-container.sh tcp-deploy` run in that checkout. The venv's editable install may point elsewhere.

Container and port come from IVG_TEST_CONTAINER and IVG_PORT (tests/conftest.py's defaults: ivg-iris-enterprise, 31972).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: The checkout this script belongs to: what `install_ivg` installs.
REPO = Path(__file__).resolve().parents[2]
ALLOWED = ("IVG262", "IVG460")
#: The namespaces `drop` may delete: spec 262's; the inventory test's (tests/e2e/test_262_inventory.py), which needs a
#: namespace nothing has touched; and the per-worker scratch names (IVG460FX* for fix work, IVG460DC* for parallel doc
#: runs through IVG_DOCTEST_NAMESPACE). Other IVG460* namespaces are never dropped: IVG460DOC (tests/docs/docs.toml)
#: holds the doc harness's baseline, IVG460ALT is tests/e2e/ns460alt.py's default.
DROPPABLE = ("IVG262", "IVG460INV", "IVG460FX", "IVG460DC")
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


def _objectscript(lines: list[str]) -> str:
    """Run ObjectScript lines in %SYS through `iris session`: Config.* take their properties as a local array by
    reference, which the Native API cannot pass."""
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
        input=script, capture_output=True, text=True, timeout=120, check=True,
    )
    return out.stdout


def make(ns: str) -> bool:
    """Create `ns` with a database of the same name; False when it already existed."""
    directory = f"/usr/irissys/mgr/{ns.lower()}/"
    out = _objectscript([
        f'if ##class(Config.Namespaces).Exists("{ns}") write "EXISTS",! halt',
        f'do ##class(%File).CreateDirectoryChain("{directory}")',
        f'if \'##class(SYS.Database).%ExistsId("{directory}") set db=##class(SYS.Database).%New(), db.Directory="{directory}", sc=db.%Save() if \'sc write "FAIL db ",$system.Status.GetErrorText(sc),! halt',
        f'kill p set p("Directory")="{directory}" if \'##class(Config.Databases).Exists("{ns}") set sc=##class(Config.Databases).Create("{ns}",.p) if \'sc write "FAIL cfgdb ",$system.Status.GetErrorText(sc),! halt',
        f'kill p set p("Globals")="{ns}",p("Routines")="{ns}" set sc=##class(Config.Namespaces).Create("{ns}",.p) if \'sc write "FAIL ns ",$system.Status.GetErrorText(sc),! halt',
        'write "CREATED",!',
    ])
    if "EXISTS" in out:
        return False
    if "CREATED" not in out:
        raise RuntimeError(f"{ns}: {out.strip()[-400:]}")
    return True


def drop(ns: str) -> bool:
    """Delete `ns` and its database (DROPPABLE only): the one wipe that leaves nothing behind, classes and globals
    included. False when it did not exist."""
    if not may_drop(ns):
        raise ValueError(f"refusing to drop {ns!r}: only upper-case {'* or '.join(DROPPABLE)}* namespaces are dropped")
    directory = f"/usr/irissys/mgr/{ns.lower()}/"
    out = _objectscript([
        f'if \'##class(Config.Namespaces).Exists("{ns}") write "ABSENT",! halt',
        f'set sc=##class(Config.Namespaces).Delete("{ns}") if \'sc write "FAIL ns ",$system.Status.GetErrorText(sc),! halt',
        f'set sc=##class(Config.Databases).Delete("{ns}") if \'sc write "FAIL cfgdb ",$system.Status.GetErrorText(sc),! halt',
        f'set sc=##class(SYS.Database).DeleteDatabase("{directory}") if \'sc write "FAIL db ",$system.Status.GetErrorText(sc),! halt',
        'write "DROPPED",!',
    ])
    if "ABSENT" in out:
        return False
    if "DROPPED" not in out:
        raise RuntimeError(f"{ns}: {out.strip()[-400:]}")
    return True


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
    "usage: make_scratch_namespace.py [--no-install] NS...   (upper case, starting " + " or ".join(ALLOWED) + ")\n"
    "       make_scratch_namespace.py --drop NS...           (upper case, starting " + ", ".join(DROPPABLE) + ")"
)


def main(argv: list[str]) -> int:
    """Every name is checked before anything is created or dropped."""
    flags = {a for a in argv if a.startswith("--")}
    names = [a for a in argv if not a.startswith("--")]
    dropping = "--drop" in flags
    rule = may_drop if dropping else may_create
    unknown = sorted(flags - {"--drop", "--no-install"})
    refused: list[str] | str
    if unknown:
        refused = unknown
    elif not names:
        refused = "no names"
    elif dropping and "--no-install" in flags:
        refused = "--drop with --no-install"
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
        created = make(ns)
        print(f"{ns}: {'created' if created else 'already exists'}")
        if created and "--no-install" not in flags:
            install_ivg(ns)
            print(f"{ns}: IVG installed from {REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
