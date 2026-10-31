"""Spec 260 D-NKG-SWAP: do the ^NKG readers tolerate index gaps?

After a rewrite drops a node, $ND(i) is undefined for that index while i < nodeCount (indices
are stable and never reused). This builds one graph, removes the node at the middle index and
the node at the highest index, rewrites ^NKG (gaps), runs every reader (IVGLab.NkgGap.Probe:
the ObjectScript readers and the arno callout readers), then builds the same graph densely
from an empty ^NKG and runs them again, and compares the answers by node name.

The callout reads USER only, so this runs in USER of the THROWAWAY lab container
(ivg-nkgswap-lab-a; removed at the end unless --keep). Raw JSON goes to
~/.cache/ivg-bench/nkg-swap/gap_probe_<stamp>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from run_scale import OUT, create, deploy, results, session  # noqa: E402


def call(name: str, expr: str) -> str:
    lines = session(name, f'Write "RESULT ",{{"v":({expr})}}.%ToJSON(),!')
    res = results(lines)
    if not res:
        raise SystemExit("\n".join(lines[-10:]))
    return res[0]["v"]


INDEX_DEPENDENT = {"count_distinct"}  # an HLL estimate: the sketch hashes indices, so any renumbering moves it


def norm(text, reader=""):
    if not isinstance(text, str):
        return text
    if reader == "export_nodemap":
        return text.split(" ", 1)[1] if " " in text else text  # the header is the index bound, not a count
    body = text[3:] if text.startswith("OK:") else text
    body = re.sub(r'(":)(-?)\.', r"\g<1>\g<2>0.", body)  # ObjectScript writes .5 for 0.5
    if reader == "leiden":
        try:
            groups = {}
            for row in json.loads(body):
                groups.setdefault(row["community"], []).append(row["id"])
            return sorted(sorted(g) for g in groups.values())  # the partition; community numbers follow index order
        except Exception:
            return text
    try:
        data = json.loads(body)
    except Exception:
        return text
    return canon(data)


def canon(data):
    if isinstance(data, list):
        if data and all(isinstance(d, dict) and "id" in d for d in data):
            return {str(d["id"]): canon({k: v for k, v in d.items() if k != "id"}) for d in data}
        items = [canon(d) for d in data]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
        except Exception:
            return items
    if isinstance(data, dict):
        return {k: canon(v) for k, v in sorted(data.items()) if k not in ("elapsed_ms", "ms", "time_ms", "version")}
    if isinstance(data, float):
        return round(data, 9)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="ivg-nkgswap-lab-a")
    ap.add_argument("--port", type=int, default=41900)
    ap.add_argument("--reuse", action="store_true", help="the lab container exists and is deployed")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if not a.name.startswith("ivg-nkgswap-lab"):
        raise SystemExit("refusing: the lab container's name must start with ivg-nkgswap-lab")
    OUT.mkdir(parents=True, exist_ok=True)
    out = {}
    try:
        if not a.reuse:
            create(a.name, a.port)
            deploy(a.name, a.port)
        out["arno_load"] = call(a.name, '##class(Graph.KG.ArnoAccel).Load("/tmp/libarno_callout.so")')
        out["arno_version"] = call(a.name, "##class(Graph.KG.ArnoAccel).LibraryVersion()")
        out["edges"] = call(a.name, "##class(IVGLab.NkgGap).Gen(40)")
        call(a.name, "##class(Graph.KG.Traversal).BuildNKG()")
        out["dense_before_gaps"] = call(a.name, "##class(IVGLab.NkgGap).Gaps()")
        gone = call(a.name, "##class(IVGLab.NkgGap).GapNames()").split(",")
        out["removed"] = gone
        for g in gone:
            call(a.name, f'##class(Graph.KG.Adjacency).RemoveNode(0,"{g}")')
        # Arno configured (as Probe's Load leaves it): the rewrite keeps the dead entries, no gaps
        session(a.name, 'Set ^ArnoAccel("lib_path")="/tmp/libarno_callout.so"')
        call(a.name, "##class(Graph.KG.Traversal).BuildNKG()")
        out["kept_dictSwept"] = call(a.name, '+$Get(^NKG("$meta","dictSwept"))')
        out["kept_gaps"] = call(a.name, "##class(IVGLab.NkgGap).Gaps()")
        out["kept"] = json.loads(call(a.name, "##class(IVGLab.NkgGap).Probe()"))
        # no Arno configured at build time: the rewrite removes them and leaves gaps
        session(a.name, "Kill ^ArnoAccel")
        call(a.name, "##class(Graph.KG.Traversal).BuildNKG()")
        out["gapped_dictSwept"] = call(a.name, '+$Get(^NKG("$meta","dictSwept"))')
        out["gapped_gaps"] = call(a.name, "##class(IVGLab.NkgGap).Gaps()")
        out["gapped"] = json.loads(call(a.name, "##class(IVGLab.NkgGap).Probe()"))
        session(a.name, "Do ##class(Graph.KG.GraphIndex).DropNKG()")
        call(a.name, "##class(Graph.KG.Traversal).BuildNKG()")
        out["dense_gaps"] = call(a.name, "##class(IVGLab.NkgGap).Gaps()")
        out["dense"] = json.loads(call(a.name, "##class(IVGLab.NkgGap).Probe()"))
        for side in ("gapped", "kept"):
            verdict = {}
            for reader, g in out[side].items():
                d = out["dense"].get(reader)
                same = norm(g, reader) == norm(d, reader)
                verdict[reader] = "same" if same else ("index-dependent" if reader in INDEX_DEPENDENT else "DIFFERENT")
                if not same:
                    print(f"--- {side} {reader}\n {side}: {str(g)[:400]}\n dense:  {str(d)[:400]}")
            out["verdict_" + side] = verdict
            print(side, json.dumps(verdict, indent=1))
        print(json.dumps({k: out[k] for k in ("arno_load", "arno_version", "edges", "removed", "dense_before_gaps", "kept_dictSwept", "kept_gaps", "gapped_dictSwept", "gapped_gaps", "dense_gaps")}))
    finally:
        path = OUT / f"gap_probe_{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(out, indent=1))
        print("raw:", path)
        if not a.keep:
            subprocess.run(["docker", "rm", "-f", a.name], capture_output=True)
            print("removed", a.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
