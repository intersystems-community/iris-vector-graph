#!/usr/bin/env python3
"""Deploy this repo's ObjectScript classes, IVG schema and CY_* UDFs into one TCK namespace.

usage: deploy_namespace.py <NS> [--repo DIR]   (run through scripts/tck/setup_namespace.sh)

Connection: IVG_TEST_CONTAINER (default ivg-iris-enterprise). If
``<container>.orb.local`` resolves (OrbStack) the container's own port 1972 is
used, else localhost:IVG_PORT (default 31972). Credentials: IRIS_USERNAME /
IRIS_PASSWORD, default _SYSTEM / SYS.

Classes are sent over TCP and loaded from inside the container, then compiled as
one list, so load order does not matter. Refuses USER.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import socket
import sys
import time
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[2]


def _endpoint() -> tuple[str, int]:
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    try:
        return socket.gethostbyname(f"{container}.orb.local"), 1972
    except socket.gaierror:
        return "localhost", int(os.environ.get("IVG_PORT", "31972"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("namespace")
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    a = ap.parse_args(argv)
    ns, repo = a.namespace.upper(), os.path.abspath(a.repo)
    if ns == "USER":
        sys.exit("deploy_namespace: refusing to deploy into USER")
    sys.path.insert(0, repo)
    os.environ.setdefault("IVG_IGNORE_NAMESPACE_CHECK", "1")
    t0 = time.time()

    import iris

    host, port = _endpoint()
    user = os.environ.get("IRIS_USERNAME", "_SYSTEM")
    pw = os.environ.get("IRIS_PASSWORD", "SYS")
    conn = iris.connect(hostname=host, port=port, namespace=ns, username=user, password=pw)
    irisobj = iris.createIRIS(conn)
    actual = irisobj.classMethodValue("%SYSTEM.SYS", "NameSpace")
    if actual != ns:
        sys.exit(f"connected to wrong namespace {actual}")

    # 1. classes
    src = os.path.join(repo, "iris_src", "src")
    names = []
    for path in sorted(glob.glob(os.path.join(src, "**", "*.cls"), recursive=True)):
        text = open(path).read()
        m = re.search(r"^Class\s+([\w.%]+)", text, re.M)
        if not m:
            continue
        names.append(m.group(1))
        dest = f"/tmp/tcpsrc-{ns}/{os.path.relpath(path, src)}"
        irisobj.classMethodValue("%File", "CreateDirectoryChain", os.path.dirname(dest))
        stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
        stream.invokeVoid("LinkToFile", dest)
        stream.invokeVoid("Write", text)
        stream.invokeVoid("%Save")
        st = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "-d")
        if not irisobj.classMethodValue("%SYSTEM.Status", "IsOK", st):
            print("LOAD ERR", path, irisobj.classMethodValue("%SYSTEM.Status", "GetOneErrorText", st))
    st = irisobj.classMethodValue("%SYSTEM.OBJ", "CompileList", ",".join(n + ".cls" for n in names), "ck-d")
    ok = irisobj.classMethodValue("%SYSTEM.Status", "IsOK", st)
    if not ok:
        err = irisobj.classMethodValue("%SYSTEM.Status", "GetErrorText", st)
        # Graph.KG.MCPToolSet needs Graph.KG.MCPTools, which is not in iris_src: known, harmless.
        if all("MCPTool" in line or "%AI.ToolSet" in line for line in err.splitlines() if "ERROR" in line):
            ok = True
            print("compile: only the known Graph.KG.MCPToolSet error")
        else:
            print("COMPILE ERR", err[:2000])
    print(f"classes: {len(names)} loaded, compile {'ok' if ok else 'FAILED'}")

    # 2. schema, the way the TCK harness initialises it
    from iris_vector_graph.engine import IRISGraphEngine

    conn2 = iris.dbapi.connect(hostname=host, port=port, namespace=ns, username=user, password=pw)
    try:
        IRISGraphEngine(conn2, embedding_dimension=768, namespace=ns).initialize_schema(
            auto_deploy_objectscript=False
        )
        print("schema: ok")
    except Exception as e:  # noqa: BLE001
        print("schema:", str(e)[:300])

    # 3. CY_* / LIST_CONCAT / STR_SPLIT UDFs
    from iris_vector_graph.schema import GraphSchema

    c = conn2.cursor()
    good = bad = 0
    for s in GraphSchema.get_procedures_sql_list():
        if "CREATE OR REPLACE FUNCTION SQLUser.CY_" not in s and "LIST_CONCAT" not in s and "STR_SPLIT" not in s:
            continue
        try:
            c.execute(s.strip().rstrip(";"))
            good += 1
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("UDF ERR", s[:60].replace("\n", " "), str(e)[:150])
    print(f"udfs: deployed {good} failed {bad}")
    c.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name %STARTSWITH 'Graph.KG.'")
    print("Graph.KG.* compiled:", c.fetchone()[0])
    print(f"{ns} deploy from {repo} in {time.time() - t0:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
