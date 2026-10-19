#!/usr/bin/env python3
"""Bulk loader for the LDBC SNB graph into one side's namespace, far faster than `bulk_load_session`.

The engine path (`snb_worker.py load`) sends JSON batches through `Graph.KG.EdgeScan.BulkIngest*SQL`, which asks before every
insert and writes `^KG`/`^NKG` per edge: 1.4-1.7K edges/s, four hours per side at SF1. This script writes the neutral stream of
`snb_model` as four TSV files (nodes, labels, props, edges), copies them into the container and loads them server-side with
embedded SQL `INSERT %NOCHECK %NOINDEX`, builds the indexes in parallel, then writes the adjacency (`^KG`, `^NKG`) by calling
the SIDE'S OWN `Graph.KG.EdgeScan.WriteAdjacency` per edge, from the same edge file and in the same order, and closes the way
the bulk-load session does (`WarmCache` where the class exists, `Build2HopStats`) and tunes the tables. The rows and globals it
leaves are the ones the engine path leaves (compare them with `compare`).

    python scripts/bench/alt/snb_bulk.py load    --side A --root /tmp/snb1/out --ns SNB1A
    python scripts/bench/alt/snb_bulk.py summary --side B --root /tmp/snb01/out --ns SNB1B --out s.json
    python scripts/bench/alt/snb_bulk.py compare --side B --root /tmp/snb01/out --ns SNB1B --against SNBBENCHB

Everything above the CLI is stdlib only; the engine is imported only by the query worker, which runs under a side's tree.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from scripts.bench.alt import snb_model as model
except ImportError:  # run as a script from another tree
    import snb_model as model

SEP = "\x1f"  # unit separator: the data holds tabs (SF1 post text), never this
FILES = ("nodes.tsv", "labels.tsv", "props.tsv", "edges.tsv")
IFIND_INDEX = "idx_props_val_ifind"  # SQL name; the class index is IFIND_CLASS_INDEX
IFIND_CLASS_INDEX = "idxpropsvalifind"

# --------------------------------------------------------------------------- row files


def tsv_line(*fields):
    """One row. The server-side reader splits on the separator and on newline, so a field holding either cannot be carried."""
    for f in fields:
        if SEP in f or "\n" in f or "\r" in f:
            raise ValueError(f"a field holds the separator or a line break and cannot be written: {f[:60]!r}")
    return SEP.join(fields) + "\n"


_LABEL_IDS = {}


def pair_key(a, b, extra=0):
    """Hashable identity of an (a, b[, extra]) triple. ``Label:digits`` ids pack into one int (a set of 8M of them is a few
    hundred MB); anything else keeps its exact strings."""
    la, _, ia = a.rpartition(":")
    lb, _, ib = b.rpartition(":")
    if la and lb and ia.isdigit() and ib.isdigit():
        ida = _LABEL_IDS.setdefault(la, len(_LABEL_IDS))
        idb = _LABEL_IDS.setdefault(lb, len(_LABEL_IDS))
        if ida < 256 and idb < 256 and extra < 256:
            return (((extra << 8 | ida) << 8 | idb) << 128) | (int(ia) << 64) | int(ib)
    return (a, b, extra)


class EdgeFilter:
    """What the engine's edge insert does to the stream: a self-loop is skipped, a repeated triple is one edge, and an
    endpoint that has no node row is registered as a bare node. Repeats are looked for within one group (one source file):
    two files never produce the same triple, so the seen-set is cleared between them."""

    def __init__(self, known_nodes):
        self.known = known_nodes
        self.self_loops = 0
        self.duplicates = 0
        self.extra_nodes = []
        self._seen = set()
        self._pids = {}

    def next_group(self):
        self._seen = set()

    def filter(self, edges):
        for s, p, o in edges:
            if s == o:
                self.self_loops += 1
                continue
            key = pair_key(s, o, self._pids.setdefault(p, len(self._pids)))
            if key in self._seen:
                self.duplicates += 1
                continue
            self._seen.add(key)
            for n in (s, o):
                if n not in self.known:
                    self.known.add(n)
                    self.extra_nodes.append(n)
            yield s, p, o


def _edge_groups(root):
    """(type, directory) groups of the model's edge files, each with its own edges, in the model's order."""
    for etype, directory, slabel, scol, tlabel, tcol in model.EDGE_FILES:
        rows = model._rows(root, directory)

        def gen(rows=rows, slabel=slabel, scol=scol, tlabel=tlabel, tcol=tcol, etype=etype):
            for row in rows:
                s, t = row.get(scol), row.get(tcol)
                if s and t:
                    yield model.node_key(slabel, s), etype, model.node_key(tlabel, t)

        yield gen()


def write_tables(root, outdir, progress=None):
    """Write nodes/labels/props/edges.tsv for ``root`` into ``outdir`` and return (and save) the counts."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    counts = {"nodes": 0, "labels": 0, "props": 0, "edges": 0}
    known = set()
    with open(out / "nodes.tsv", "w", encoding="utf-8", newline="") as fn, open(out / "labels.tsv", "w", encoding="utf-8", newline="") as fl, open(
        out / "props.tsv", "w", encoding="utf-8", newline=""
    ) as fp:
        for n in model.iter_nodes(root):
            key = model.node_key(n["label"], n["id"])
            if key in known:
                raise ValueError(f"node {key} appears twice in the data; the loader does not merge repeated nodes")
            known.add(key)
            fn.write(tsv_line(key))
            fl.write(tsv_line(key, n["label"]))
            counts["labels"] += 1
            # bulk_create_nodes (SQL path, the one the engine-loaded namespaces took) adds id = the node key to the properties.
            for k, v in {**n["props"], "id": key}.items():
                if v is not None and v != "":
                    fp.write(tsv_line(key, k, v))
                    counts["props"] += 1
            counts["nodes"] += 1
            if progress and counts["nodes"] % 500_000 == 0:
                progress(f"nodes {counts['nodes']:,}")
        flt = EdgeFilter(known)
        with open(out / "edges.tsv", "w", encoding="utf-8", newline="") as fe:
            for group in _edge_groups(root):
                flt.next_group()
                for s, p, o in flt.filter(group):
                    fe.write(tsv_line(s, p, o))
                    counts["edges"] += 1
                if progress:
                    progress(f"edges {counts['edges']:,}")
        for key in flt.extra_nodes:  # endpoints with no row of their own
            fn.write(tsv_line(key))
    counts["nodes"] += len(flt.extra_nodes)
    counts.update(self_loops=flt.self_loops, duplicates=flt.duplicates, extra_nodes=len(flt.extra_nodes))
    (out / "counts.json").write_text(json.dumps(counts, indent=2))
    return counts


def files_ready(outdir):
    out = Path(outdir)
    return all((out / f).is_file() for f in (*FILES, "counts.json"))


# --------------------------------------------------------------------------- summaries


def _flatten(d, prefix=""):
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from _flatten(v, path + ".")
        else:
            yield path, v


def diff_summaries(a, b):
    """Human-readable differences between two summaries (counts, ^KG shape, query signatures); [] when identical."""
    fa, fb = dict(_flatten(a)), dict(_flatten(b))
    out = []
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path, "<missing>") != fb.get(path, "<missing>"):
            out.append(f"{path}: {fa.get(path, '<missing>')} != {fb.get(path, '<missing>')}")
    return out


def parse_kg_summary(text):
    """{name: int} from the `KGS name=value` lines the summary script prints."""
    out = {}
    for ln in text.splitlines():
        if ln.startswith("KGS ") and "=" in ln:
            k, _, v = ln[4:].strip().partition("=")
            out[k] = int(v) if v.lstrip("-").isdigit() else v
    return out


class PhaseLog:
    """Wall time per named phase, in the order they ran."""

    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self._times = {}

    @contextlib.contextmanager
    def phase(self, name):
        t0 = self._clock()
        try:
            yield
        finally:
            self._times[f"{name}_s"] = round(self._clock() - t0, 2)

    def set(self, name, seconds):
        self._times[f"{name}_s"] = round(seconds, 2)

    def as_dict(self):
        return dict(self._times)


# --------------------------------------------------------------------------- the server-side half

LOADER_CLASS = r'''/// Server-side half of scripts/bench/alt/snb_bulk.py: reads the TSV files it wrote and loads them. Benchmark scaffolding.
Class Bench.SnbBulk Extends %RegisteredObject
{

/// The file as a UTF-8 character stream; pSC carries the failure.
ClassMethod Open(pFile As %String, Output pSC As %Status) As %Stream.FileCharacter
{
	Set pSC = $$$OK
	Set tStream = ##class(%Stream.FileCharacter).%New()
	Set tStream.TranslateTable = "UTF8"
	If '##class(%File).Exists(pFile) {
		Set pSC = $$$ERROR($$$GeneralError, "no such file: "_pFile)
		Quit tStream
	}
	Set pSC = tStream.LinkToFile(pFile)
	Quit tStream
}

ClassMethod Fail(pWhat As %String, pRow As %Integer) As %Integer
{
	Write "ERROR ", pWhat, " SQLCODE=", $Get(SQLCODE), " row=", pRow, " ", $Get(%msg), !
	Quit -1
}

/// One node_id per line, default graph. Returns the rows written, -1 on failure.
ClassMethod LoadNodes(pFile As %String) As %Integer
{
	Set tCount = 0, tStart = $ZHorolog
	Set tStream = ..Open(pFile, .tSC)
	If $$$ISERR(tSC) { Write "ERROR ", $System.Status.GetErrorText(tSC), ! Quit -1 }
	While 'tStream.AtEnd {
		Set tId = tStream.ReadLine(100000)
		If tId = "" { Continue }
		&sql(INSERT %NOCHECK %NOINDEX %NOLOCK %NOTRIGGER INTO Graph_KG.nodes (node_id, graph_id) VALUES (:tId, ''))
		If SQLCODE '= 0 { Quit }
		Set tCount = tCount + 1
		If tCount # 500000 = 0 { Write "PROGRESS nodes ", tCount, " ", $ZHorolog - tStart, "s", ! }
	}
	If SQLCODE < 0 { Quit ..Fail("nodes", tCount + 1) }
	Quit tCount
}

/// node_id, label per line.
ClassMethod LoadLabels(pFile As %String) As %Integer
{
	Set tCount = 0, tStart = $ZHorolog
	Set tStream = ..Open(pFile, .tSC)
	If $$$ISERR(tSC) { Write "ERROR ", $System.Status.GetErrorText(tSC), ! Quit -1 }
	While 'tStream.AtEnd {
		Set tLine = tStream.ReadLine(100000)
		If tLine = "" { Continue }
		Set tId = $Piece(tLine, $Char(31), 1), tLabel = $Piece(tLine, $Char(31), 2)
		&sql(INSERT %NOCHECK %NOINDEX %NOLOCK %NOTRIGGER INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES ('', :tId, :tLabel))
		If SQLCODE '= 0 { Quit }
		Set tCount = tCount + 1
		If tCount # 500000 = 0 { Write "PROGRESS labels ", tCount, " ", $ZHorolog - tStart, "s", ! }
	}
	If SQLCODE < 0 { Quit ..Fail("labels", tCount + 1) }
	Quit tCount
}

/// node_id, key, val per line.
ClassMethod LoadProps(pFile As %String) As %Integer
{
	Set tCount = 0, tStart = $ZHorolog
	Set tStream = ..Open(pFile, .tSC)
	If $$$ISERR(tSC) { Write "ERROR ", $System.Status.GetErrorText(tSC), ! Quit -1 }
	While 'tStream.AtEnd {
		Set tLine = tStream.ReadLine(100000)
		If tLine = "" { Continue }
		Set tId = $Piece(tLine, $Char(31), 1), tKey = $Piece(tLine, $Char(31), 2), tVal = $Piece(tLine, $Char(31), 3)
		&sql(INSERT %NOCHECK %NOINDEX %NOLOCK %NOTRIGGER INTO Graph_KG.rdf_props (graph_id, s, "key", val) VALUES ('', :tId, :tKey, :tVal))
		If SQLCODE '= 0 { Quit }
		Set tCount = tCount + 1
		If tCount # 500000 = 0 { Write "PROGRESS props ", tCount, " ", $ZHorolog - tStart, "s", ! }
	}
	If SQLCODE < 0 { Quit ..Fail("props", tCount + 1) }
	Quit tCount
}

/// s, p, o per line; ekey and edge_id take their defaults, as the engine's insert leaves them.
ClassMethod LoadEdges(pFile As %String) As %Integer
{
	Set tCount = 0, tStart = $ZHorolog
	Set tStream = ..Open(pFile, .tSC)
	If $$$ISERR(tSC) { Write "ERROR ", $System.Status.GetErrorText(tSC), ! Quit -1 }
	While 'tStream.AtEnd {
		Set tLine = tStream.ReadLine(100000)
		If tLine = "" { Continue }
		Set tS = $Piece(tLine, $Char(31), 1), tP = $Piece(tLine, $Char(31), 2), tO = $Piece(tLine, $Char(31), 3)
		&sql(INSERT %NOCHECK %NOINDEX %NOLOCK %NOTRIGGER INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (:tS, :tP, :tO, ''))
		If SQLCODE '= 0 { Quit }
		Set tCount = tCount + 1
		If tCount # 500000 = 0 { Write "PROGRESS edges ", tCount, " ", $ZHorolog - tStart, "s", ! }
	}
	If SQLCODE < 0 { Quit ..Fail("edges", tCount + 1) }
	Quit tCount
}

/// ^KG (and ^NKG, through the skeleton) for the edges of the file, by the tree's own WriteAdjacency, then the session's close.
ClassMethod LoadAdjacency(pFile As %String) As %Integer
{
	Set tCount = 0, tStart = $ZHorolog
	Set tStream = ..Open(pFile, .tSC)
	If $$$ISERR(tSC) { Write "ERROR ", $System.Status.GetErrorText(tSC), ! Quit -1 }
	Do ##class(Graph.KG.Traversal).InitNKGSkeleton()
	// With the adjacency functional index (spec 252) the index build above wrote ^KG from the rows; the
	// per-edge pass below would write it again. The baseline side has no such method, hence the dictionary check.
	Set tIndexed = 0
	If ##class(%Dictionary.CompiledMethod).%ExistsId("Graph.KG.Adjacency||Indexed") {
		Set tIndexed = $ClassMethod("Graph.KG.Adjacency", "Indexed")
	}
	If tIndexed { Write "PROGRESS adjacency-from-index", ! }
	While 'tStream.AtEnd {
		Set tLine = tStream.ReadLine(100000)
		If tLine = "" { Continue }
		If 'tIndexed {
			Do ##class(Graph.KG.EdgeScan).WriteAdjacency($Piece(tLine, $Char(31), 1), $Piece(tLine, $Char(31), 2), $Piece(tLine, $Char(31), 3), "1.0", 0)
		}
		Set tCount = tCount + 1
		If tCount # 500000 = 0 { Write "PROGRESS adjacency ", tCount, " ", $ZHorolog - tStart, "s", ! }
	}
	Write "PROGRESS adjacency-written ", tCount, " ", $ZHorolog - tStart, "s", !
	Set tClose = $ZHorolog
	If ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.Adj") {
		Try { Do $ClassMethod("Graph.KG.Adj", "WarmCache") } Catch ex { Write "WARN WarmCache ", ex.DisplayString(), ! }
	}
	Try { Do ##class(Graph.KG.Traversal).Build2HopStats() } Catch ex { Write "WARN Build2HopStats ", ex.DisplayString(), ! }
	Write "PROGRESS adjacency-close ", $ZHorolog - tClose, "s", !
	Quit tCount
}

/// Build the indexes of one table class; pIgnore / pOnly are index names (comma separated), "" for all / none.
ClassMethod BuildIdx(pClass As %String, pOnly As %String = "", pIgnore As %String = "", pJobs As %Integer = 1) As %Status
{
	Set tOnly = "", tIgnore = ""
	If pOnly '= "" { For i = 1:1:$Length(pOnly, ",") { Set tOnly = tOnly _ $ListBuild($Piece(pOnly, ",", i)) } }
	If pIgnore '= "" { For i = 1:1:$Length(pIgnore, ",") { Set tIgnore = tIgnore _ $ListBuild($Piece(pIgnore, ",", i)) } }
	Quit $ClassMethod(pClass, "%BuildIndices", tOnly, 1, 0, "", "", "", tIgnore, 0, pJobs)
}

}
'''

# One statement per line: the summary is read-only and runs in namespaces that do not hold the loader class (the engine-loaded ones).
KG_SUMMARY_SCRIPT = r'''Set n=0,e=0,s=""
For { Set s=$Order(^KG("out",0,s)) Quit:s=""  Set n=n+1,p="" For { Set p=$Order(^KG("out",0,s,p)) Quit:p=""  Set o="" For { Set o=$Order(^KG("out",0,s,p,o)) Quit:o=""  Set e=e+1 } } }
Write "KGS out_sources=",n,!,"KGS out_edges=",e,!
Set n=0,e=0,s=""
For { Set s=$Order(^KG("in",0,s)) Quit:s=""  Set n=n+1,p="" For { Set p=$Order(^KG("in",0,s,p)) Quit:p=""  Set o="" For { Set o=$Order(^KG("in",0,s,p,o)) Quit:o=""  Set e=e+1 } } }
Write "KGS in_sources=",n,!,"KGS in_edges=",e,!
Set t=""
For { Set t=$Order(^KG(t)) Quit:t=""  If (t="out")||(t="in")||(t="__version") { Continue }  Set n=0,x="" If $Data(^KG(t,0))>1 { For { Set x=$Order(^KG(t,0,x)) Quit:x=""  Set n=n+1 } }  Write "KGS kg_",t,"=",n,!  }
Write "KGS kg_version=",$Get(^KG("__version")),!
Set t=""
For { Set t=$Order(^NKG(t)) Quit:t=""  If t="$meta" { Set k="" For { Set k=$Order(^NKG("$meta",k)) Quit:k=""  Write "KGS nkg_meta_",k,"=",$Get(^NKG("$meta",k)),! } Continue }  Set n=0,x="" For { Set x=$Order(^NKG(t,x)) Quit:x=""  Set n=n+1 }  Write "KGS nkg_",t,"=",n,!  }
Halt
'''

# --------------------------------------------------------------------------- orchestration

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
VENV_PYTHON = os.environ.get("IVG_BENCH_PYTHON", str(REPO / ".venv" / "bin" / "python"))
TABLES = (("nodes", "Graph.KG.nodes"), ("labels", "Graph.KG.rdflabels"), ("props", "Graph.KG.rdfprops"), ("edges", "Graph.KG.rdfedges"))


def _sides():
    from scripts.bench.alt import kgbench

    return kgbench.prefixed_sides("SNB1")


def _check_ns(ns):
    from scripts.bench.alt import sides as sd

    return sd.check_namespace(ns)


def _say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def run_session(ns, script, label):
    """Run ObjectScript in ``ns`` through `iris session`, echoing progress lines; returns all output lines."""
    _check_ns(ns)
    p = subprocess.Popen(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", ns],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    p.stdin.write(script)
    p.stdin.close()
    lines = []
    for ln in p.stdout:
        lines.append(ln.rstrip("\n"))
        if ln.startswith(("PROGRESS", "RESULT", "ERROR", "WARN")):
            _say(f"[{label}] {ln.rstrip()}")
    p.wait()
    return lines


def result_of(lines, tag):
    """The integer after `RESULT <tag> ` (the last such line), or None."""
    hits = [ln for ln in lines if ln.startswith(f"RESULT {tag} ")]
    return hits[-1].split(" ", 2)[2].strip() if hits else None


def deploy_loader(ns):
    _check_ns(ns)
    local = Path("/tmp") / "Bench.SnbBulk.cls"
    local.write_text(LOADER_CLASS)
    remote = "/tmp/Bench.SnbBulk.cls"
    subprocess.run(["docker", "cp", str(local), f"{CONTAINER}:{remote}"], check=True)
    script = f'Set sc=$System.OBJ.Load("{remote}","ck")\nWrite "RESULT compile ",$Select($System.Status.IsOK(sc):"OK",1:$System.Status.GetErrorText(sc)),!\nHalt\n'
    lines = run_session(ns, script, "deploy")
    if result_of(lines, "compile") != "OK":
        raise RuntimeError("loader class did not compile: " + "\n".join(lines[-15:]))


def copy_files(outdir, ns):
    remote = f"/tmp/snbbulk-{ns}"
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "rm", "-rf", remote], check=True)
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "mkdir", "-p", remote], check=True)
    subprocess.run(["docker", "cp", f"{outdir}/.", f"{CONTAINER}:{remote}/"], check=True)
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "chmod", "-R", "a+rX", remote], check=True)
    return remote


def sql_scalar(ns, sql):
    import iris

    conn = iris.connect("localhost", 31972, ns, "_SYSTEM", "SYS")
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def table_counts(ns):
    return {t: int(sql_scalar(ns, f"SELECT COUNT(*) FROM Graph_KG.{t}")) for t in ("nodes", "rdf_labels", "rdf_props", "rdf_edges")}


def tune(side, ns):
    """GraphSchema.tune_tables(cursor) under the side's own tree, one table at a time so each one's time is on record."""
    code = (
        "import iris, time\nfrom iris_vector_graph.schema import GraphSchema\n"
        f"c=iris.connect('localhost',31972,'{ns}','_SYSTEM','SYS')\nr={{}}\n"
        "for t in GraphSchema.TUNABLE_TABLES:\n"
        "    t0=time.perf_counter(); r.update(GraphSchema.tune_tables(c.cursor(),[t])); print('TUNE',t,round(time.perf_counter()-t0,1),flush=True)\n"
        "c.commit()\nprint('TUNED',r)\n"
    )
    env = {**os.environ, "PYTHONPATH": side.tree, "PYTHONDONTWRITEBYTECODE": "1", "IVG_IGNORE_NAMESPACE_CHECK": "1"}
    p = subprocess.run([VENV_PYTHON, "-c", code], cwd=side.tree, env=env, capture_output=True, text=True)
    if p.returncode != 0 or "TUNED" not in p.stdout:
        raise RuntimeError("tune_tables failed: " + p.stderr[-600:])
    return p.stdout.strip()


def reset_script():
    """ObjectScript that empties the four tables, ^KG and ^NKG of a namespace and restarts the edge_id counter."""
    return r'''For c="Graph.KG.nodes","Graph.KG.rdflabels","Graph.KG.rdfprops","Graph.KG.rdfedges" { Set sc=$ClassMethod(c,"%KillExtent") Write "RESULT kill ",c," ",$Select($System.Status.IsOK(sc):"OK",1:$System.Status.GetErrorText(sc)),! }
Kill ^KG,^NKG
Set rs=##class(%SQL.Statement).%ExecDirect(,"SELECT COLUMN_DEFAULT FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='Graph_KG' AND TABLE_NAME='rdf_edges' AND COLUMN_NAME='edge_id'")
If rs.%Next() { Set d=rs.%GetData(1)  If d["$i(" { Set g=$Piece($Piece(d,"$i(",2),")",1)  Kill @g  Write "RESULT counter ",g," reset",! } }
Halt
'''


def reset(args):
    ns = args.ns
    _check_ns(ns)
    for ln in run_session(ns, reset_script(), "reset"):
        pass
    _say(f"{ns}: {table_counts(ns)}")


def load(args):
    side = _sides()[args.side]
    ns = args.ns or side.namespace
    _check_ns(ns)
    from scripts.bench.alt import sides as sd

    outdir = Path(args.files or sd.BENCH_HOME / f"snb_bulk_files_{Path(args.root).resolve().parent.name}_{Path(args.root).resolve().name}")
    log = PhaseLog()
    t_start = time.perf_counter()
    if sql_scalar(ns, "SELECT COUNT(*) FROM Graph_KG.nodes") not in (0, None):
        raise RuntimeError(f"{ns} already holds nodes; the bulk loader loads an empty namespace only")
    with log.phase("extract"):
        if files_ready(outdir) and not args.force_extract:
            _say(f"reusing {outdir}")
        else:
            write_tables(args.root, outdir, progress=_say)
    counts = json.loads((outdir / "counts.json").read_text())
    _say(f"files: {counts}")
    with log.phase("stage"):
        deploy_loader(ns)
        remote = copy_files(outdir, ns)
    jobs = {
        "nodes": f'Set tN=##class(Bench.SnbBulk).LoadNodes("{remote}/nodes.tsv")\nWrite "RESULT nodes ",tN,!\nHalt\n',
        "labels": f'Set tN=##class(Bench.SnbBulk).LoadLabels("{remote}/labels.tsv")\nWrite "RESULT labels ",tN,!\nHalt\n',
        "props": f'Set tN=##class(Bench.SnbBulk).LoadProps("{remote}/props.tsv")\nWrite "RESULT props ",tN,!\nHalt\n',
        "edges": f'Set tN=##class(Bench.SnbBulk).LoadEdges("{remote}/edges.tsv")\nWrite "RESULT edges ",tN,!\nHalt\n',
        "adjacency": f'Set tN=##class(Bench.SnbBulk).LoadAdjacency("{remote}/edges.tsv")\nWrite "RESULT adjacency ",tN,!\nHalt\n',
    }
    expect = {"nodes": counts["nodes"], "labels": counts["labels"], "props": counts["props"], "edges": counts["edges"], "adjacency": counts["edges"]}
    per_job = {}

    def run_job(name):
        t0 = time.perf_counter()
        lines = run_session(ns, jobs[name], name)
        per_job[name] = round(time.perf_counter() - t0, 2)
        got = result_of(lines, name)
        if got is None or int(got) != expect[name]:
            raise RuntimeError(f"{name}: loaded {got}, expected {expect[name]}: " + "\n".join(lines[-8:]))

    with log.phase("load_parallel"):
        # Five independent jobs: the four tables (unchecked, unindexed) and the adjacency (reads only the edge file).
        with ThreadPoolExecutor(max_workers=5) as ex:
            for f in [ex.submit(run_job, n) for n in jobs]:
                f.result()
    for name, secs in per_job.items():
        log.set(f"job_{name}", secs)
    idx_jobs = [
        ("nodes", "Graph.KG.nodes", "", ""),
        ("labels", "Graph.KG.rdflabels", "", ""),
        ("edges", "Graph.KG.rdfedges", "", ""),
        ("props", "Graph.KG.rdfprops", "", IFIND_CLASS_INDEX),
        ("props_ifind", "Graph.KG.rdfprops", IFIND_CLASS_INDEX, ""),
    ]
    if getattr(args, "skip_ifind", False):
        # The full-text index on property values is not used by any SNB query and costs hours to build over 25M rows (SF1); both
        # sides skip it alike. It stays defined and unbuilt.
        idx_jobs = [j for j in idx_jobs if j[0] != "props_ifind"]
    per_idx = {}

    def run_idx(item):
        name, cls, only, ignore = item
        t0 = time.perf_counter()
        script = (
            f'Set sc=##class(Bench.SnbBulk).BuildIdx("{cls}","{only}","{ignore}",{args.index_jobs})\n'
            f'Write "RESULT idx_{name} ",$Select($System.Status.IsOK(sc):"OK",1:$System.Status.GetErrorText(sc)),!\nHalt\n'
        )
        lines = run_session(ns, script, f"idx-{name}")
        per_idx[name] = round(time.perf_counter() - t0, 2)
        if result_of(lines, f"idx_{name}") != "OK":
            raise RuntimeError(f"index build {name}: " + "\n".join(lines[-8:]))

    with log.phase("indexes"):
        with ThreadPoolExecutor(max_workers=len(idx_jobs)) as ex:
            for f in [ex.submit(run_idx, i) for i in idx_jobs]:
                f.result()
    for name, secs in per_idx.items():
        log.set(f"index_{name}", secs)
    with log.phase("tune"):
        _say(tune(side, ns))
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "rm", "-rf", remote], check=True)
    log.set("total", time.perf_counter() - t_start)
    summary = summarize(side, ns, args.root, with_queries=False)
    result = {"side": args.side, "ns": ns, "root": args.root, "files": counts, "phases": log.as_dict(), "summary": summary}
    Path(args.out).write_text(json.dumps(result, indent=2, default=str))
    _say(json.dumps(result, default=str))


def summarize(side, ns, root, with_queries=True, n_params=3, cap_s=90):
    """Row counts, `^KG`/`^NKG` shape and (optionally) the signatures of the SNB queries' first parameter sets."""
    out = {"counts": table_counts(ns)}
    out["groups"] = {
        "edges_by_p": {str(p): int(c) for p, c in _rows(ns, "SELECT p, COUNT(*) FROM Graph_KG.rdf_edges GROUP BY p")},
        "labels": {str(p): int(c) for p, c in _rows(ns, "SELECT label, COUNT(*) FROM Graph_KG.rdf_labels GROUP BY label")},
        "props_by_key": {str(p): int(c) for p, c in _rows(ns, 'SELECT "key", COUNT(*) FROM Graph_KG.rdf_props GROUP BY "key"')},
        "prop_chars": int(sql_scalar(ns, "SELECT SUM(LENGTH(val)) FROM Graph_KG.rdf_props")),
        "null_graph_ids": int(sql_scalar(ns, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id IS NULL")),
        "ifind_india": _ifind_count(ns),
    }
    out["kg"] = parse_kg_summary("\n".join(run_session(ns, KG_SUMMARY_SCRIPT, "kg-summary")))
    if with_queries:
        out["queries"] = query_signatures(side, ns, root, n_params, cap_s)
    return out


def _rows(ns, sql):
    import iris

    conn = iris.connect("localhost", 31972, ns, "_SYSTEM", "SYS")
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    return rows


def _ifind_count(ns):
    try:
        return int(sql_scalar(ns, f"SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE %ID %FIND search_index({IFIND_INDEX}, 'India')"))
    except Exception as ex:  # a missing or unbuilt full-text index is a finding, not a crash
        return f"ERR {str(ex)[:80]}"


def query_signatures(side, ns, root, n_params, cap_s):
    env = {**os.environ, "PYTHONPATH": side.tree, "PYTHONDONTWRITEBYTECODE": "1", "IVG_IGNORE_NAMESPACE_CHECK": "1"}
    cmd = [VENV_PYTHON, str(HERE), "worker-queries", "--ns", ns, "--root", root, "--n", str(n_params), "--cap-s", str(cap_s)]
    p = subprocess.run(cmd, cwd=side.tree, env=env, capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("SIGS ")]
    if p.returncode != 0 or not lines:
        raise RuntimeError("query worker failed: " + (p.stderr or p.stdout)[-600:])
    return json.loads(lines[-1][5:])


def worker_queries(args):
    """Under a side's tree: the signature of each SNB query for the first ``n`` parameter sets (errors are recorded, not raised)."""
    try:
        from scripts.bench.alt import snb_queries as queries
        from scripts.bench.alt import snb_worker as w
    except ImportError:
        import snb_queries as queries
        import snb_worker as w
    conn, engine = w._engine(args.ns)
    plist = queries.sample_params(args.root, n=args.n, seed=42)
    out = {}
    for name, spec in queries.QUERIES.items():
        cypher = queries.ivg_cypher(spec)
        keep = spec.get("ivg_client_limit")
        sets = [{k: p[k] for k in spec["params"]} for p in plist] if spec["params"] else [{}]
        sigs = []
        for ps in sets[: args.n]:
            def one(ps=ps):
                rows = w.rows_or_raise(engine.execute_cypher(cypher, ps))
                return rows[:keep] if keep else rows

            finished, val = w.call_with_cap(one, args.cap_s)
            if not finished:
                sigs.append("timeout")
                break
            sigs.append(f"ERR {type(val).__name__}: {str(val)[:80]}" if isinstance(val, Exception) else f"{len(val)}:{w.signature(val)}")
        out[name] = sigs
    print("SIGS " + json.dumps(out), flush=True)
    sys.stdout.flush()
    os._exit(0)  # a call abandoned by the cap may still run on the server; exiting drops the connection


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("load")
    pl.add_argument("--side", choices=["A", "B"], required=True)
    pl.add_argument("--root", required=True)
    pl.add_argument("--ns")
    pl.add_argument("--files", help="directory for the TSV files (default: <IVG_BENCH_HOME>/snb_bulk_files_<root>)")
    pl.add_argument("--force-extract", action="store_true")
    pl.add_argument("--index-jobs", type=int, default=1)
    pl.add_argument("--skip-ifind", action="store_true", help="do not build the full-text index on property values (SF1: hours, unused)")
    pl.add_argument("--out", default="snb_bulk_load.json")
    ps = sub.add_parser("summary")
    ps.add_argument("--side", choices=["A", "B"], required=True)
    ps.add_argument("--root", required=True)
    ps.add_argument("--ns", required=True)
    ps.add_argument("--no-queries", action="store_true")
    ps.add_argument("--out", required=True)
    pc = sub.add_parser("compare")
    pc.add_argument("--side", choices=["A", "B"], required=True)
    pc.add_argument("--root", required=True)
    pc.add_argument("--ns", required=True)
    pc.add_argument("--against", required=True, help="a namespace loaded by the engine path (read only)")
    pc.add_argument("--params", type=int, default=3)
    pc.add_argument("--cap-s", type=float, default=90.0)
    pr = sub.add_parser("reset", help="empty the four tables, ^KG and ^NKG of one of the harness's own namespaces")
    pr.add_argument("--ns", required=True)
    pw = sub.add_parser("worker-queries")
    pw.add_argument("--ns", required=True)
    pw.add_argument("--root", required=True)
    pw.add_argument("--n", type=int, default=3)
    pw.add_argument("--cap-s", type=float, default=90.0)
    args = ap.parse_args(argv)
    if args.cmd == "load":
        load(args)
    elif args.cmd == "summary":
        side = _sides()[args.side]
        Path(args.out).write_text(json.dumps(summarize(side, args.ns, args.root, with_queries=not args.no_queries), indent=2, default=str))
    elif args.cmd == "compare":
        side = _sides()[args.side]
        a = summarize(side, args.against, args.root, n_params=args.params, cap_s=args.cap_s)
        b = summarize(side, args.ns, args.root, n_params=args.params, cap_s=args.cap_s)
        diffs = diff_summaries(a, b)
        print(json.dumps({"engine_loaded": a, "bulk_loaded": b}, indent=2, default=str))
        print("IDENTICAL" if not diffs else "DIFFERENCES:\n  " + "\n  ".join(diffs))
        return 0 if not diffs else 1
    elif args.cmd == "reset":
        reset(args)
    elif args.cmd == "worker-queries":
        worker_queries(args)
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.append(str(REPO))  # last: a side tree on PYTHONPATH must win for iris_vector_graph
    raise SystemExit(main())
