"""Create a scratch namespace with its own database on the test container, then put IVG in it.

    python scripts/dev/make_scratch_namespace.py IVG262ALT [IVG262UPG ...]

Only names starting IVG262 or IVG460 are accepted, so this cannot touch a namespace another suite or project uses. A
namespace that already exists is left as it is. A namespace with its own database is the only kind a parallel run can
use: one mapped to USER shares ^KG and the routines with every suite that uses USER.

Container and port come from IVG_TEST_CONTAINER and IVG_PORT (tests/conftest.py's defaults: ivg-iris-enterprise, 31972).
"""

from __future__ import annotations

import os
import subprocess
import sys

import iris

ALLOWED = ("IVG262", "IVG460")
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


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


def install_ivg(ns: str) -> None:
    """Tables first (the classes compile against them), then the classes over TCP."""
    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")
    IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    conn.close()
    env = dict(os.environ, IVG_NAMESPACE=ns, IVG_PORT=str(PORT), IVG_ARNO_CONTAINER=CONTAINER)
    subprocess.run(["scripts/enterprise-container.sh", "tcp-deploy"], check=True, env=env)


def main(names: list[str]) -> int:
    bad = [n for n in names if not n.upper().startswith(ALLOWED) or n.upper() != n]
    if not names or bad:
        print(f"usage: make_scratch_namespace.py NS... (upper case, starting {' or '.join(ALLOWED)}); refused: {bad}")
        return 2
    for ns in names:
        created = make(ns)
        print(f"{ns}: {'created' if created else 'already exists'}")
        if created:
            install_ivg(ns)
            print(f"{ns}: IVG installed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
