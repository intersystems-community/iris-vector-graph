"""Create a scratch namespace with its own database on the test container, then put IVG in it.

    python scripts/dev/make_scratch_namespace.py IVG262ALT [IVG262UPG ...]
    python scripts/dev/make_scratch_namespace.py --no-install IVG460FXA      (the namespace and its database only)
    python scripts/dev/make_scratch_namespace.py --split IVG460FXS    (globals in IVG460FXSD, routines in IVG460FXSR)
    python scripts/dev/make_scratch_namespace.py --drop IVG262UPG     (IVG262*, IVG460INV*, IVG460FX*, IVG460DC*, IVG463* only)

Only names starting IVG262, IVG460 or IVG463 (4.6.3's scratch work) are accepted, so this cannot touch a namespace another suite or project uses. A
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
ALLOWED = ("IVG262", "IVG460", "IVG463")
#: The namespaces `drop` may delete: spec 262's; the inventory test's (tests/e2e/test_262_inventory.py), which needs a
#: namespace nothing has touched; and the per-worker scratch names (IVG460FX* for fix work, IVG460DC* for parallel doc
#: runs through IVG_DOCTEST_NAMESPACE); IVG463* for 4.6.3 fix work. Other IVG460* namespaces are never dropped: IVG460DOC (tests/docs/docs.toml)
#: holds the doc harness's baseline, IVG460ALT is tests/e2e/ns460alt.py's default.
DROPPABLE = ("IVG262", "IVG460INV", "IVG460FX", "IVG460DC", "IVG463")
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


def split_databases(ns: str) -> dict:
    """`--split`'s two databases of `ns`, each with a resource of its own: {"globals"|"routines": (name, resource)}."""
    return {"globals": (f"{ns}D", f"%DB_{ns}D"), "routines": (f"{ns}R", f"%DB_{ns}R")}


def _database_lines(db: str, resource: str = "") -> list[str]:
    """Create database `db` in /usr/irissys/mgr/<db>/ and its configuration; with `resource`, that resource too, and
    the database on it (otherwise IRIS gives it %DB_%DEFAULT)."""
    directory = f"/usr/irissys/mgr/{db.lower()}/"
    lines = []
    on_resource = ""
    if resource:
        lines.append(f'if \'##class(Security.Resources).Exists("{resource}") set sc=##class(Security.Resources).Create("{resource}","scratch database {db}","","") if \'sc write "FAIL res ",$system.Status.GetErrorText(sc),! halt')
        on_resource = f', db.ResourceName="{resource}"'
    return lines + [
        f'do ##class(%File).CreateDirectoryChain("{directory}")',
        f'if \'##class(SYS.Database).%ExistsId("{directory}") set db=##class(SYS.Database).%New(), db.Directory="{directory}"{on_resource}, sc=db.%Save() if \'sc write "FAIL db ",$system.Status.GetErrorText(sc),! halt',
        f'kill p set p("Directory")="{directory}" if \'##class(Config.Databases).Exists("{db}") set sc=##class(Config.Databases).Create("{db}",.p) if \'sc write "FAIL cfgdb ",$system.Status.GetErrorText(sc),! halt',
    ]


def make(ns: str, split: bool = False) -> bool:
    """Create `ns` with a database of the same name; False when it already existed. `split`: its globals in `<ns>D`
    and its routines in `<ns>R` instead, each database on a resource of its own (`%DB_<ns>D`, `%DB_<ns>R`)."""
    if split:
        dbs = split_databases(ns)
        globals_db, routines_db = dbs["globals"][0], dbs["routines"][0]
        databases = [line for name, resource in dbs.values() for line in _database_lines(name, resource)]
    else:
        globals_db = routines_db = ns
        databases = _database_lines(ns)
    out = _objectscript([
        f'if ##class(Config.Namespaces).Exists("{ns}") write "EXISTS",! halt',
        *databases,
        f'kill p set p("Globals")="{globals_db}",p("Routines")="{routines_db}" set sc=##class(Config.Namespaces).Create("{ns}",.p) if \'sc write "FAIL ns ",$system.Status.GetErrorText(sc),! halt',
        'write "CREATED",!',
    ])
    if "EXISTS" in out:
        return False
    if "CREATED" not in out:
        raise RuntimeError(f"{ns}: {out.strip()[-400:]}")
    return True


def drop(ns: str) -> bool:
    """Delete `ns` and the databases it maps (DROPPABLE only): the one wipe that leaves nothing behind, classes and
    globals included. Only a database `make` names for `ns` is deleted (`ns`, or `--split`'s `<ns>D` and `<ns>R`, with
    their resources), read from the namespace's configuration. False when it did not exist."""
    if not may_drop(ns):
        raise ValueError(f"refusing to drop {ns!r}: only upper-case {'* or '.join(DROPPABLE)}* namespaces are dropped")
    out = _objectscript([
        f'if \'##class(Config.Namespaces).Exists("{ns}") write "ABSENT",! halt',
        f'kill p do ##class(Config.Namespaces).Get("{ns}",.p) set dbs=$lb(p("Globals"),p("Routines"))',
        f'set sc=##class(Config.Namespaces).Delete("{ns}") if \'sc write "FAIL ns ",$system.Status.GetErrorText(sc),! halt',
        # Braces throughout: a one-line `if` would govern everything after it on the line
        f'set ok=1 for i=1:1:2 {{ set db=$li(dbs,i) continue:\'$lf($lb("{ns}","{ns}D","{ns}R"),db)  '
        'continue:\'##class(Config.Databases).Exists(db)  kill d do ##class(Config.Databases).Get(db,.d) '
        'set sc=##class(Config.Databases).Delete(db) if \'sc { write "FAIL cfgdb ",$system.Status.GetErrorText(sc),! set ok=0 quit } '
        'set sc=##class(SYS.Database).DeleteDatabase(d("Directory")) if \'sc { write "FAIL db ",$system.Status.GetErrorText(sc),! set ok=0 quit } '
        f'if (db\'="{ns}"),##class(Security.Resources).Exists("%DB_"_db) {{ set sc=##class(Security.Resources).Delete("%DB_"_db) }} }}',
        'if ok write "DROPPED",!',
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
