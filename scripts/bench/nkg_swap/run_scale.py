"""Spec 260 D-NKG-SWAP at scale: the pre-fix BuildNKG against the in-place rewrite on ~1.9M edges.

Runs in a THROWAWAY container (default ivg-nkgswap-lab-a, created from ivg-iris-enterprise's
image and removed at the end unless --keep). Never point it at a product container: GenKG
kills ^KG and ^NKG of the lab namespace.

Per pair (the order alternates): each builder timed with no reader, then again with a reader
job on the node interned last ("n99999"); the rewrite is also timed after 1 % drift and from
an empty ^NKG. Host load (uptime) is recorded before each build. Raw JSON goes to
~/.cache/ivg-bench/nkg-swap/scale_<stamp>.json.

    python scripts/bench/nkg_swap/run_scale.py [--nodes 190000 --deg 10 --pairs 3 --port 41900 --keep]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = Path.home() / ".cache" / "ivg-bench" / "nkg-swap"
SOURCE = "ivg-iris-enterprise"


def sh(*args, **kw) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def session(name: str, script: str, timeout: int = 3600) -> list[str]:
    out = subprocess.run(
        ["docker", "exec", "-i", name, "iris", "session", "IRIS", "-U", "USER"],
        input=script + "\nHalt\n", capture_output=True, text=True, timeout=timeout,
    ).stdout
    return out.splitlines()


def results(lines: list[str]) -> list[dict]:
    return [json.loads(ln.split("RESULT ", 1)[1]) for ln in lines if "RESULT {" in ln]


def load_avg() -> str:
    return sh("uptime").strip().split("load averages:")[-1].strip()


def create(name: str, port: int) -> None:
    image = sh("docker", "inspect", SOURCE, "--format", "{{.Config.Image}}").strip()
    # the licence key, CPF merge and callout library the source container mounts (untracked files of the main checkout)
    mounts = {m["Destination"]: m["Source"] for m in json.loads(sh("docker", "inspect", SOURCE, "--format", "{{json .Mounts}}"))}
    key = mounts["/usr/irissys/mgr/iris.key"]
    cpf = mounts["/home/irisowner/merge.cpf"]
    so = mounts["/tmp/libarno_callout.so"]
    sh("docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:1972",
       "-e", "ISC_CPF_MERGE_FILE=/home/irisowner/merge.cpf",
       "-v", f"{key}:/usr/irissys/mgr/iris.key:ro", "-v", f"{cpf}:/home/irisowner/merge.cpf:ro",
       "-v", f"{so}:/tmp/libarno_callout.so:ro", image)
    for _ in range(120):
        probe = subprocess.run(["docker", "exec", "-i", name, "iris", "session", "IRIS", "-U", "USER"],
                               input='Write "READY",!\nHalt\n', capture_output=True, text=True, timeout=60)
        if "READY" in probe.stdout:
            return
        time.sleep(5)
    raise SystemExit(f"{name} did not become healthy")


def deploy(name: str, port: int) -> None:
    # the IVG schema first: Graph.KG.* compiles against the Graph_KG tables
    sys.path.insert(0, str(REPO))
    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(hostname="127.0.0.1", port=port, namespace="USER", username="_SYSTEM", password="SYS")
    try:
        IRISGraphEngine(conn, embedding_dimension=8).initialize_schema(auto_deploy_objectscript=False)
    finally:
        conn.close()
    sh("docker", "exec", name, "mkdir", "-p", "/tmp/nkgsrc")
    sh("docker", "cp", f"{REPO}/iris_src/src/.", f"{name}:/tmp/nkgsrc/")
    sh("docker", "exec", name, "mkdir", "-p", "/tmp/nkgsrc/IVGLab")
    sh("docker", "cp", f"{HERE}/IVGLab/.", f"{name}:/tmp/nkgsrc/IVGLab/")
    lines = session(name, 'Set tSC=$system.OBJ.LoadDir("/tmp/nkgsrc","ck-d",.err,1) Write "RESULT ",{"ok":($system.Status.IsOK(tSC))}.%ToJSON(),!')
    print("deploy:", results(lines) or lines[-5:])


def step(name: str, label: str, call: str, raw: list) -> dict:
    load = load_avg()
    lines = session(name, f'Write "RESULT ",{call},!')
    res = results(lines)
    if not res:
        print("\n".join(lines[-10:]))
        raise SystemExit(f"{label} failed")
    row = dict(res[0], step=label, host_load=load)
    raw.append(row)
    print(json.dumps(row))
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="ivg-nkgswap-lab-a")
    ap.add_argument("--port", type=int, default=41900)
    ap.add_argument("--nodes", type=int, default=190000)
    ap.add_argument("--deg", type=int, default=10)
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="the lab container exists and is deployed")
    a = ap.parse_args()
    if not a.name.startswith("ivg-nkgswap-lab"):
        raise SystemExit("refusing: the lab container's name must start with ivg-nkgswap-lab")
    OUT.mkdir(parents=True, exist_ok=True)
    raw: list = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = OUT / f"scale_{stamp}.json"
    try:
        if not a.reuse:
            create(a.name, a.port)
            deploy(a.name, a.port)
        g = 'IVGLab.NkgScale'
        step(a.name, "gen", f'{{"edges":(##class({g}).GenKG({a.nodes},{a.deg})),"warm":(##class(Graph.KG.Adj).WarmCache())}}.%ToJSON()', raw)
        step(a.name, "initial_new", f'##class({g}).Run("new","n99999",0)', raw)
        for pair in range(1, a.pairs + 1):
            order = ("old", "new") if pair % 2 else ("new", "old")
            for which in order:
                step(a.name, f"p{pair}_{which}_quiet", f'##class({g}).Run("{which}","n99999",0)', raw)
                step(a.name, f"p{pair}_{which}_reader", f'##class({g}).Run("{which}","n99999",1)', raw)
            step(a.name, f"p{pair}_drift", f'{{"changes":(##class({g}).Drift({a.nodes},1))}}.%ToJSON()', raw)
            step(a.name, f"p{pair}_new_after_drift_reader", f'##class({g}).Run("new","n99999",1)', raw)
            step(a.name, f"p{pair}_new_unchanged_quiet", f'##class({g}).Run("new","n99999",0)', raw)
            session(a.name, 'Do ##class(Graph.KG.GraphIndex).DropNKG()')
            step(a.name, f"p{pair}_new_from_empty_quiet", f'##class({g}).Run("new","n99999",0)', raw)
            path.write_text(json.dumps(raw, indent=1))
    finally:
        path.write_text(json.dumps(raw, indent=1))
        print("raw:", path)
        if not a.keep:
            subprocess.run(["docker", "rm", "-f", a.name], capture_output=True)
            print("removed", a.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
