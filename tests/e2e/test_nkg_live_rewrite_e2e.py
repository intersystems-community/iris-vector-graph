"""D-NKG-LIVE-READER fixed by D-NKG-SWAP: ^NKG is rewritten in place, never emptied.

`Graph.KG.TraversalBuild:BuildNKG` used to `DropNKG` and rebuild, so a reader during the
rebuild saw an incomplete ^NKG (lab, 1.9M edges: 92 % of reads of a late-interned node
wrong for 155 s of a 167 s build). The rewrite keeps every row until the walk has written
its replacement and sweeps only what the walk did not write, with node and label indices
stable.

What these tests hold against the server (spec 260 D-NKG-SWAP's five oracle items):

(a) no empty read: a reader job reading sampled nodes (the first and the last interned,
    the drifted ones) during the rebuild sees zero "no node" and zero "no row" reads, and
    every row it reads belongs to the graph before or after the rebuild;
(b) convergence: the rewritten ^NKG equals a from-scratch build by the D-NKG-EQUIV oracle
    (by names), and the version strictly rises on every rebuild. Sketch VALUES are compared
    only where both sides interned identically ("strict": a fresh build against the legacy
    builder, a rewrite over an unchanged graph), because a sketch hashes the target's index
    and stable indices differ from a fresh build's scan order once the graph has changed.
    Elsewhere the sketch KEYS are compared by names and each side's sketch must equal the
    sketch recomputed from that side's own -1 rows (so no removed edge's contribution was
    merged in). nodeCount/labelCount are interning high-water marks, checked by rule;
(c) with a writer adding and removing edges during the rebuild, one more rebuild after
    quiescing equals a from-scratch build;
(d) a builder killed mid-walk or mid-sweep leaves an ^NKG that still passes (a), and a
    rerun converges;
(e) every name present before and after keeps its index.

The oracle and the jobs are the test-only class IVGTest.NkgEquiv
(tests/e2e/fixtures/nkg/IVGTest/NkgEquiv.cls), loaded into the test namespace here. It is
ObjectScript rather than Python over the Native API because the reader must make many
thousands of reads inside a build that lasts about a second, the builder must be a job
that can be killed, and the sketch check must use the server's own SHA-1.

The reader is put in the window by size, not by a hook: the fixture is ~40,000 edges,
whose rewrite takes long enough for hundreds of reader passes (asserted).

`SKIP_IRIS_TESTS=true` fails rather than skips.
"""

from __future__ import annotations

import glob
import json
import os
import time

import pytest

pytestmark = [pytest.mark.e2e]

CLS = "IVGTest.NkgEquiv"
PREFIX = "nkgsw_"
GAP = "nkggap_"
NODES = 4000
DEG = 8
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "nkg")
LONG = PREFIX + "L" * (256 - len(PREFIX))
EXTRA = ",".join(["42", "007", "3.5", LONG, PREFIX + "1", PREFIX + str(NODES), PREFIX + "noweight"])


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("the ^NKG rewrite tests need a live IRIS; SKIP_IRIS_TESTS=true is not a pass.")
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


def _load_fixture_class(irisobj) -> None:
    ns = str(irisobj.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    for path in sorted(glob.glob(os.path.join(FIXTURES, "**", "*.cls"), recursive=True)):
        rel = os.path.relpath(path, FIXTURES).replace(os.sep, "/")
        dest = f"/tmp/nkgsrc-{ns}/{rel}"
        irisobj.classMethodValue("%File", "CreateDirectoryChain", os.path.dirname(dest))
        stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
        stream.invokeVoid("LinkToFile", dest)
        with open(path) as fh:
            for line in fh.read().split("\n"):
                stream.invokeVoid("WriteLine", line)
        stream.invokeVoid("%Save")
        status = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
        if not irisobj.classMethodValue("%SYSTEM.Status", "IsOK", status):
            pytest.fail(f"{rel}: {irisobj.classMethodValue('%SYSTEM.Status', 'GetOneErrorText', status)}")


@pytest.fixture(scope="module")
def nkg(iris_connection):
    _require_iris(iris_connection)
    import iris

    irisobj = iris.createIRIS(iris_connection)
    _load_fixture_class(irisobj)
    env = _Env(irisobj)
    # The rewrite removes dead dictionary entries only where no Arno library is configured
    # (or one declares it tolerates index gaps). Most tests here want that branch, so the
    # namespace's ^ArnoAccel is set aside for the module and put back at the end.
    irisobj.classMethodValue(CLS, "ArnoHide")
    try:
        env.clean()
        yield env
        env.clean()
        env.call("Clean", GAP)
        env.build()
    finally:
        irisobj.classMethodValue(CLS, "ArnoRestore")
        irisobj.kill("^IVGQ")
        irisobj.kill("^IVGQR")


class _Env:
    def __init__(self, irisobj):
        self.o = irisobj

    # -- calls ---------------------------------------------------------------------
    def call(self, method, *args):
        return self.o.classMethodValue(CLS, method, *args)

    def get(self, *subs):
        return self.o.get("^IVGQ", *subs)

    def build(self):
        self.o.classMethodVoid("Graph.KG.Traversal", "BuildNKG")

    def version(self) -> int:
        return int(self.o.get("^NKG", "$meta", "version") or 0)

    def clean(self):
        # an engine created meanwhile (IRISGraphEngine detects and loads Arno) configures
        # ^ArnoAccel again: set it aside before every scenario (ArnoHide keeps the first copy)
        self.call("ArnoHide")
        self.call("Clean", PREFIX)
        self.call("Clean", GAP)
        for k in ("snap", "rd", "rm", "ra", "rc", "s1", "s2", "mut", "new", "ref", "before", "after", "b0", "b3",
                  "quiesced", "ref_q", "final", "killed", "rerun", "rewritten", "gapped", "dense"):
            self.o.kill("^IVGQ", k)

    def fresh(self, drop: bool = True):
        """The fixture graph alone in ^KG's fixture names, and ^NKG built from it."""
        self.clean()
        if drop:
            self.o.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
        self.call("Gen", PREFIX, NODES, DEG)
        self.build()

    def snap(self, tag, glo="^NKG") -> int:
        return int(self.call("Snap", tag, glo))

    def reference(self, tag="ref"):
        self.call("LegacyBuild", "^IVGQR")
        self.snap(tag, "^IVGQR")

    def diff(self, a, b, strict=False) -> dict:
        return json.loads(str(self.call("Diff", a, b, 1 if strict else 0)))

    def same_index(self, a, b) -> dict:
        return json.loads(str(self.call("SameIndex", a, b)))

    def wait(self, cond, timeout=120.0, step=0.005, what="condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(step)
        pytest.fail(f"timed out waiting for {what}")

    # -- the reader ----------------------------------------------------------------
    def start_reader(self, run, before, after) -> int:
        self.o.kill("^IVGQ", run)
        n = int(self.call("Sample", run, before, 25, EXTRA))
        assert n >= 50, n
        self.call("Expect", run, before, after)
        pid = int(self.call("StartReader", run))
        self.wait(lambda: int(self.get(run, "passes") or 0) >= 3, what="the reader to start")
        return pid

    def stop_reader(self, run) -> dict:
        self.o.set(1, "^IVGQ", run, "stop")
        self.wait(lambda: self.get(run, "r") is not None, what="the reader's counts")
        return json.loads(str(self.get(run, "r")))

    def run_builder(self, run) -> float:
        self.call("StartBuilder", run)
        self.wait(lambda: self.get(run, "built") is not None, timeout=600, step=0.05, what="the builder")
        err = str(self.get(run, "build_error") or "")
        assert err == "", err
        return float(self.get(run, "build_seconds"))


def _assert_converged(d: dict, strict=False):
    for cat in ("nodes", "labels", "e1", "e2", "d", "meta", "agg"):
        c = d[cat]
        assert (c["only_a"], c["only_b"], c["differ"]) == (0, 0, 0), (cat, c)
    assert d["bad_a"]["count"] == 0, d["bad_a"]
    assert d["bad_b"]["count"] == 0, d["bad_b"]


def _assert_clean_reads(r: dict):
    bad = {k: r[k] for k in ("no_node", "no_row", "invalid_row", "missing_row", "bad_degree", "bad_dict", "version_back")}
    assert all(v == 0 for v in bad.values()), (bad, r["example"], r)


# --- the oracle itself ---------------------------------------------------------------


def test_oracle_reports_nothing_for_one_build_and_catches_a_perturbed_one(nkg):
    nkg.fresh()
    nkg.snap("s1")
    nkg.snap("s2")
    _assert_converged(nkg.diff("s1", "s2", strict=True))
    changed = nkg.call("Perturb")
    nkg.snap("mut")
    d = nkg.diff("s1", "mut", strict=True)
    assert d["e1"]["differ"] == 1, (changed, d["e1"])
    assert d["agg"]["only_a"] == 1, (changed, d["agg"])
    assert d["bad_b"]["count"] >= 1, d["bad_b"]  # the -2 mirror no longer matches
    nkg.build()


def test_a_fresh_build_equals_the_legacy_builder_exactly_sketch_values_included_same_index_map(nkg):
    """Same scan order, so the same indices: compared strictly, $agg values included. Also
    D-NKG-EQUIV check 10 (graph coverage) and the fixture's edge cases."""
    nkg.fresh(drop=True)
    nkg.snap("new")
    nkg.reference()
    _assert_converged(nkg.diff("new", "ref", strict=True), strict=True)
    o = nkg.o
    assert int(o.get("^IVGQ", "new", "aggself")) == 0
    # named graphs: label P4 exists only there; the long name and numeric-looking names are interned
    assert o.isDefined("^NKG", "$LI", "P4")
    for name in ("42", "007", "3.5", LONG, PREFIX + "noweight"):
        assert o.isDefined("^NKG", "$NI", name), name
    s = o.get("^NKG", "$NI", PREFIX + "1")
    t = o.get("^NKG", "$NI", PREFIX + "noweight")
    p = o.get("^NKG", "$LI", "P2")
    assert float(o.get("^NKG", -1, s, -(int(p) + 1), t)) == 1.0


def test_a_3000_character_name_cannot_reach_the_adjacency(nkg):
    """D-NKG-EQUIV asked for a 3,000-character node name. IRIS rejects the subscript, so no
    such node can be in ^KG and no rebuild meets one; node ids are VARCHAR(256)."""
    with pytest.raises(Exception, match="SUBSCRIPT"):
        nkg.o.classMethodValue("Graph.KG.Adjacency", "Put", 0, PREFIX + "x" * 3000, "P1", PREFIX + "1", 1)


# --- (a) no empty read ---------------------------------------------------------------


def test_a_no_reader_sees_an_empty_or_invented_nkg_during_the_rebuild(nkg):
    nkg.fresh()
    nkg.call("Drift", PREFIX, NODES, 400, 600)
    nkg.snap("before")
    nkg.reference("after")
    v0 = nkg.version()
    nkg.start_reader("ra", "before", "after")
    secs = nkg.run_builder("ra")
    r = nkg.stop_reader("ra")
    print(f"\n(a) build {secs:.3f}s reader {json.dumps(r)}")
    assert r["in_window"] >= 500, r
    _assert_clean_reads(r)
    assert nkg.version() > v0
    nkg.snap("rewritten")
    _assert_converged(nkg.diff("rewritten", "after"))


# --- (b) convergence and the version rule, (e) index stability -------------------------


def test_b_rewrite_over_an_unchanged_graph_is_exact_sketch_values_included_and_the_version_rises(nkg):
    nkg.fresh()
    nkg.snap("b0")
    versions = [nkg.version()]
    for _ in range(3):
        nkg.build()
        versions.append(nkg.version())
    assert all(b > a for a, b in zip(versions, versions[1:])), versions
    nkg.snap("b3")
    _assert_converged(nkg.diff("b0", "b3", strict=True), strict=True)
    nkg.reference()
    _assert_converged(nkg.diff("b3", "ref", strict=True), strict=True)


def test_b_e_rewrite_after_drift_converges_by_names_sketches_self_consistent_and_keeps_every_index(nkg):
    nkg.fresh()
    # a node removed from every graph, and new nodes, so the dictionary must change both ways
    for g in (0, PREFIX + "g1", PREFIX + "g2"):
        nkg.o.classMethodValue("Graph.KG.Adjacency", "RemoveNode", g, PREFIX + "5")
    nkg.call("Drift", PREFIX, NODES, 400, 600)
    nkg.snap("before")
    v0 = nkg.version()
    nkg.build()
    assert nkg.version() > v0
    nkg.snap("after")
    nkg.reference()
    d = nkg.diff("after", "ref")
    _assert_converged(d)
    assert int(nkg.get("after", "aggself")) == 0
    for gone in (PREFIX + "5", PREFIX + "ghost1", PREFIX + "ghost2"):
        assert not nkg.o.isDefined("^NKG", "$NI", gone), gone
    assert not nkg.o.isDefined("^NKG", "$LI", "PGHOST")
    s = nkg.same_index("before", "after")
    assert s["nodes"]["moved"] == 0 and s["labels"]["moved"] == 0, s
    assert s["nodes"]["both"] > NODES, s
    # no Arno library configured: dead entries removed (gaps allowed), and recorded
    assert int(nkg.o.get("^NKG", "$meta", "dictSwept")) == 1
    assert int(nkg.o.get("^NKG", "$meta", "liveNodes")) == d["nodes"]["same"]
    assert int(nkg.o.get("^NKG", "$meta", "liveLabels")) == d["labels"]["same"] - 3
    gaps = str(nkg.call("Gaps")).split(":")[1]
    assert gaps, "removing a node should have left a gap below nodeCount"


# --- (c) a writer during the rebuild -----------------------------------------------------


def test_c_a_writer_during_the_rebuild_then_one_more_rebuild_converges_by_names(nkg):
    nkg.fresh()
    nkg.call("Drift", PREFIX, NODES, 400, 600)
    nkg.snap("before")
    run = "rc"
    nkg.o.kill("^IVGQ", run)
    nkg.call("StartWriter", run, PREFIX, NODES)
    nkg.wait(lambda: nkg.get(run, "writer") is not None, what="the writer")
    time.sleep(0.2)
    nkg.run_builder(run)
    nkg.o.set(1, "^IVGQ", run, "stop_writer")
    nkg.wait(lambda: nkg.get(run, "w") is not None, what="the writer to stop")
    nkg.snap("quiesced")
    nkg.reference("ref_q")
    pre = nkg.diff("quiesced", "ref_q")
    nkg.build()
    nkg.snap("final")
    nkg.reference()
    print(f"\n(c) writer adds/removes {nkg.get(run, 'w')!r}; before the extra rebuild: "
          + json.dumps({k: v for k, v in pre.items() if k in ('e1', 'e2', 'd', 'meta', 'agg', 'bad_a')}))
    _assert_converged(nkg.diff("final", "ref"))
    # no -1 row the rebuild left was invented: every quiesced row is an edge of ^KG
    assert pre["e1"]["only_a"] == 0, pre["e1"]
    s = nkg.same_index("before", "final")
    assert s["nodes"]["moved"] == 0 and s["labels"]["moved"] == 0, s


# --- (d) the builder killed mid-walk and mid-sweep ---------------------------------------


@pytest.mark.parametrize("phase", ["walk", "sweep"])
def test_d_a_killed_builder_leaves_a_valid_nkg_and_a_rerun_converges(nkg, phase):
    nkg.fresh()
    nkg.call("Drift", PREFIX, NODES, 400, 3000)
    nkg.snap("before")
    nkg.reference("after")
    run = "rd" + phase
    nkg.start_reader(run, "before", "after")
    nkg.call("StartBuilder", run)
    nkg.wait(lambda: nkg.get(run, "builder") is not None, what="the builder")
    pid = int(nkg.get(run, "builder"))

    def in_phase():
        p = str(nkg.call("Phase") or "")
        if phase == "walk":
            return p.startswith("walk:") and int(p.split(":")[1] or 0) >= 1000
        return p.startswith("sweep:")

    nkg.wait(in_phase, timeout=60, step=0.001, what=f"the rewrite to reach its {phase}")
    nkg.call("KillJob", pid)
    nkg.wait(lambda: not int(nkg.call("Alive", pid)), what="the builder to die")
    assert nkg.get(run, "built") is None, "the build finished before the kill"
    assert str(nkg.call("Phase") or "").startswith(phase + ":"), nkg.call("Phase")
    time.sleep(0.3)
    r = nkg.stop_reader(run)
    print(f"\n(d {phase}) reader {json.dumps(r)}")
    _assert_clean_reads(r)
    # the interrupted ^NKG, read at rest, holds only rows of the graph before or after
    nkg.snap("killed")
    for cat in ("e1", "e2", "nodes", "labels"):
        out = str(nkg.call("Outside", "killed", "before", "after", cat))
        assert out.startswith("0 "), (cat, out)
    faults = str(nkg.call("Faults", "killed", 1))
    assert faults.startswith("0 "), faults
    v0 = nkg.version()
    nkg.build()
    assert nkg.version() > v0
    assert not nkg.call("Phase")
    nkg.snap("rerun")
    _assert_converged(nkg.diff("rerun", "after"))
    s = nkg.same_index("before", "rerun")
    assert s["nodes"]["moved"] == 0 and s["labels"]["moved"] == 0, s


# --- sync() and the Rust builder ------------------------------------------------------


def test_sync_nkg_never_calls_the_rust_builder_even_when_arno_is_detected(nkg, iris_connection):
    """IVG is the only ^NKG writer (D-NKG-WRITER, Q2'). The Rust branch of _sync_nkg could
    never succeed (BuildNKGRust reads a handle nothing sets) and, had it run, would have
    built an ^NKG without named graphs or sketches."""
    from unittest.mock import patch

    from iris_vector_graph.engine import IRISGraphEngine

    nkg.fresh()
    eng = IRISGraphEngine(iris_connection)
    calls = []
    real = eng._iris_obj()

    class Spy:
        def __getattr__(self, name):
            attr = getattr(real, name)
            if name.startswith("classMethod"):
                def wrapped(*a, **k):
                    calls.append(a[:2])
                    return attr(*a, **k)
                return wrapped
            return attr

    eng._arno_capabilities = {"rust_callout": True}
    with patch.object(eng, "_detect_arno", return_value=True), patch.object(eng, "_iris_obj", return_value=Spy()):
        assert eng._sync_nkg() is True
    assert not [c for c in calls if "BuildNKGRust" in c], calls
    assert ("Graph.KG.Traversal", "BuildNKG") in calls, calls
    assert nkg.o.isDefined("^NKG", "$LI", "P4")  # a named graph's label: the Rust builder lacked them
    assert nkg.o.isDefined("^NKG", "$agg")


# --- writers behind and in front of the rewrite, with no second rebuild -----------------


@pytest.mark.parametrize("phase", ["walk", "sweep"])
def test_writes_behind_and_in_front_of_the_rewrite_are_right_without_a_second_rebuild(nkg, phase):
    """The lead's hole check: Adjacency.Put and Adjacency.Remove on sources the rewrite has
    already passed and on sources it has not reached, while it runs. The sweep removes a row
    only after re-reading ^KG (a Put writes ^KG before ^NKG), and the walk re-reads ^KG after
    each Set (a Remove kills ^KG before ^NKG), so when the rewrite finishes ^NKG holds exactly
    the edges of the final ^KG, with no further rebuild. Compared by names; sketches checked
    against each side's own rows (index maps differ, so sketch values are not compared)."""
    nkg.fresh()
    nkg.call("Drift", PREFIX, NODES, 400, 3000)
    run = "rm" + phase
    nkg.o.kill("^IVGQ", run)
    nkg.call("StartMutator", run, PREFIX, phase, 1000)
    nkg.wait(lambda: nkg.get(run, "mutator") is not None, what="the mutator")
    nkg.run_builder(run)
    nkg.wait(lambda: nkg.get(run, "mut_end") is not None, what="the mutations")
    at, end = str(nkg.get(run, "mut_at")), str(nkg.get(run, "mut_end"))
    assert at.startswith(phase + ":") and end.startswith(phase + ":"), (at, end)
    nkg.snap("final")
    nkg.reference()
    d = nkg.diff("final", "ref")
    print(f"\n({phase} mutations {nkg.get(run, 'mut')}, phase {at} -> {end}) "
          + json.dumps({k: d[k] for k in ("e1", "e2", "nodes", "labels", "d", "meta", "agg", "bad_a")}))
    for cat in ("e1", "e2"):
        assert (d[cat]["only_a"], d[cat]["only_b"], d[cat]["differ"]) == (0, 0, 0), (cat, d[cat])
    for added in (PREFIX + "mb1" + phase, PREFIX + "mf1" + phase):
        assert nkg.o.isDefined("^NKG", "$NI", added), added
    if phase == "walk":
        _assert_converged(d)
    else:
        # Behind the sweep, the incremental writers' own behaviour remains: Remove never counts
        # edgeCount down nor takes a target out of a sketch (as with no rebuild at all). The rewrite
        # adds no fault of its own, and its edge count never falls below the true one.
        faults = str(nkg.call("Faults", "final", 1))
        assert faults.startswith("0 "), faults
        assert int(nkg.o.get("^NKG", "$meta", "edgeCount")) >= d["e1"]["same"]


# --- index gaps: option D (Tom, 2026-10-31) ---------------------------------------------


#: The library the container mounts (arno-callout 1.2.1 declares "nkg_dict_gaps": 1), and a
#: configured path that loads nothing: a library that cannot be asked keeps the dead entries.
LIB = "/tmp/libarno_callout.so"
DEFAULT_LIB = "/usr/irissys/mgr/libarno_callout.so"
#: a recorded path ArnoAccel.LibPath ignores (outside the manager and binary directories, 4.6.0 R311)
UNTRUSTED_NO_LIB = "/tmp/ivg-no-such-libarno_callout.so"


def _no_lib(nkg):
    """A configured library that does not load: a path in the manager directory, which LibPath takes (R311), with no
    file there. (A missing file under /tmp is not the configured library at all since R311: LibPath ignores it.)"""
    return str(nkg.o.classMethodValue("%SYSTEM.Util", "ManagerDirectory")) + "ivg-no-such-libarno_callout.so"


def _require_library(nkg):
    if not int(nkg.o.classMethodValue("%File", "Exists", LIB)):
        if os.environ.get("IVG_REQUIRE_ARNO") == "1":
            pytest.fail(f"IVG_REQUIRE_ARNO=1 and the container has no {LIB}")
        pytest.skip(f"no Arno library at {LIB}")


@pytest.mark.parametrize(
    "caps,expected",
    [('{"nkg_dict_gaps":1}', 1), ('{"nkg_dict_gaps":2,"readers":{}}', 1), ('{"nkg_dict_gaps":0}', 0),
     ('{"readers":{"bfs":3}}', 0), ("", 0), ("not json", 0), ('{"nkg_dict_gaps":"yes"}', 0)],
)
def test_the_gap_tolerance_signal_is_read_from_the_capabilities_report(nkg, caps, expected):
    """Stub capability reports, for the shapes no real library sends: arno-callout 1.1.0 and
    1.2.0 carry no such key, 1.2.1 carries "nkg_dict_gaps": 1."""
    assert int(nkg.o.classMethodValue("Graph.KG.TraversalBuild", "GapsDeclared", caps)) == expected


def test_dictionary_sweep_is_allowed_without_arno_and_refused_with_a_library_that_cannot_declare(nkg):
    nkg.call("ArnoHide")
    assert int(nkg.o.classMethodValue("Graph.KG.TraversalBuild", "DictSweepAllowed")) == 1
    nkg.call("ArnoShow", _no_lib(nkg))
    try:
        assert int(nkg.o.classMethodValue("Graph.KG.TraversalBuild", "DictSweepAllowed")) == 0
    finally:
        nkg.call("ArnoHide")


def test_a_recorded_library_outside_the_instance_directories_is_ignored_for_the_default(nkg):
    """R311: `^ArnoAccel("lib_path")` naming a missing /tmp file is not taken; the default build is loaded and asked,
    and arno-callout 1.2.1 there declares gap tolerance."""
    if not int(nkg.o.classMethodValue("%File", "Exists", DEFAULT_LIB)):
        if os.environ.get("IVG_REQUIRE_ARNO") == "1":
            pytest.fail(f"IVG_REQUIRE_ARNO=1 and the container has no {DEFAULT_LIB}")
        pytest.skip(f"no Arno library at {DEFAULT_LIB}")
    nkg.call("ArnoShow", UNTRUSTED_NO_LIB)
    try:
        assert int(nkg.o.classMethodValue("Graph.KG.TraversalBuild", "DictSweepAllowed")) == 1
        assert nkg.o.classMethodValue("Graph.KG.ArnoAccel", "LoadedPath") == DEFAULT_LIB
    finally:
        nkg.call("ArnoHide")


def test_dictionary_sweep_is_allowed_with_a_library_that_declares_gap_tolerance(nkg):
    _require_library(nkg)
    nkg.call("ArnoShow", LIB)
    try:
        assert int(nkg.o.classMethodValue("Graph.KG.TraversalBuild", "DictSweepAllowed")) == 1
    finally:
        nkg.call("ArnoHide")


def _drifted_rewrite(nkg, lib_path):
    """Remove one node from every graph, drift the graph, rewrite ^NKG with lib_path configured,
    and return the D-NKG-EQUIV diff of the result against a from-scratch build, which must agree
    on everything but the dictionary."""
    nkg.fresh()
    for g in (0, PREFIX + "g1", PREFIX + "g2"):
        nkg.o.classMethodValue("Graph.KG.Adjacency", "RemoveNode", g, PREFIX + "5")
    nkg.call("Drift", PREFIX, NODES, 400, 600)
    nkg.call("ArnoShow", lib_path)
    try:
        nkg.build()
    finally:
        nkg.call("ArnoHide")
    nkg.snap("final")
    nkg.reference()
    d = nkg.diff("final", "ref")
    for cat in ("e1", "e2", "d", "meta", "agg"):
        assert (d[cat]["only_a"], d[cat]["only_b"], d[cat]["differ"]) == (0, 0, 0), (cat, d[cat])
    assert d["nodes"]["only_b"] == 0 and d["labels"]["only_b"] == 0
    assert int(nkg.o.get("^NKG", "$meta", "liveNodes")) == d["nodes"]["same"]
    return d


def test_with_a_library_that_cannot_declare_the_rewrite_keeps_dead_dictionary_entries(nkg):
    """The 1.1.0 and 1.2.0 readers walk 0..nodeCount-1 over $ND and invent a node for an
    undefined entry, so with a library that does not declare gap tolerance no index is left
    empty: a dead name keeps its $ND/$NI entry (the between-rebuild state) with no row, degree
    or sketch. A configured library that does not load is treated the same way."""
    _drifted_rewrite(nkg, _no_lib(nkg))
    assert int(nkg.o.get("^NKG", "$meta", "dictSwept")) == 0
    assert str(nkg.call("Gaps")).split(":")[1] == "", nkg.call("Gaps")
    dead_nodes = str(nkg.call("Dead", "final", "ref", "nodes")).split(" ")
    dead_labels = str(nkg.call("Dead", "final", "ref", "labels")).split(" ")
    assert int(dead_nodes[0]) == 3 and int(dead_nodes[1]) == 0, dead_nodes  # nkgsw_5, ghost1, ghost2
    assert int(dead_labels[0]) == 1 and int(dead_labels[1]) == 0, dead_labels  # PGHOST


def test_with_a_library_that_declares_gap_tolerance_the_rewrite_removes_dead_entries(nkg):
    """arno-callout 1.2.1 declares "nkg_dict_gaps": 1, so with it configured the rewrite removes
    dead names as it does with no library, and leaves their indices unassigned."""
    _require_library(nkg)
    d = _drifted_rewrite(nkg, LIB)
    assert int(nkg.o.get("^NKG", "$meta", "dictSwept")) == 1
    assert str(nkg.call("Gaps")).split(":")[1] != "", nkg.call("Gaps")
    assert d["nodes"]["only_a"] == 0 and d["labels"]["only_a"] == 0


def _scores(text: str) -> dict:
    import re

    body = text[3:] if text.startswith("OK:") else text
    body = re.sub(r'(":)(-?)\.', r"\g<1>\g<2>0.", body)
    return {r["id"]: float(r["score"]) for r in json.loads(body)}


def test_index_range_readers_answer_the_same_with_gaps_as_on_a_dense_build(nkg):
    """A gap in the middle and one at the end of the index range ($ND undefined below
    nodeCount), against a dense build of the same graph, by name: closeness (MSBFS, classical
    and harmonic), eigenvector, and the NODEMAP the adjacency export hands the callout."""
    nkg.clean()
    nkg.o.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    nkg.call("GapGen", GAP, 40)
    nkg.build()
    nkg.o.classMethodValue("Graph.KG.Adjacency", "Put", 0, GAP + "v1", "P1", GAP + "tail", 1)
    middle = str(nkg.call("MiddleName", GAP))
    for gone in (middle, GAP + "tail"):
        nkg.o.classMethodValue("Graph.KG.Adjacency", "RemoveNode", 0, gone)
    nkg.build()
    assert int(nkg.o.get("^NKG", "$meta", "dictSwept")) == 1
    count, gaps = str(nkg.call("Gaps")).split(":")
    gaps = [int(g) for g in gaps.split(",") if g]
    assert len(gaps) >= 2 and int(count) - 1 in gaps, (count, gaps)
    gapped = json.loads(str(nkg.call("GapReaders")))
    nkg.o.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    nkg.build()
    assert str(nkg.call("Gaps")).split(":")[1] == ""
    dense = json.loads(str(nkg.call("GapReaders")))
    only_fixture = not any(
        not str(n).startswith(GAP) for n in _scores(dense["harmonic"])
    )
    print(f"\n(gaps) middle={middle} gaps={gaps} nodeCount={count} namespace holds only the fixture: {only_fixture} "
          f"nodemap gapped={gapped['nodemap']} dense={dense['nodemap']}")
    for reader in ("classical", "harmonic", "eigenvector"):
        g, d = _scores(gapped[reader]), _scores(dense[reader])
        assert set(g) == set(d), (reader, set(g) ^ set(d))
        bad = {n: (g[n], d[n]) for n in g if abs(g[n] - d[n]) > 1e-6 * max(1.0, abs(d[n]))}
        assert not bad, (reader, list(bad.items())[:5])
    if only_fixture:
        assert any(v > 0 for v in _scores(dense["classical"]).values())
    assert gapped["nodemap"]["empty"] == 0 and gapped["nodemap"]["wrong"] == 0, gapped["nodemap"]
    assert gapped["nodemap"]["entries"] == dense["nodemap"]["entries"], (gapped["nodemap"], dense["nodemap"])


def test_verify_sync_reports_live_nodes_not_the_interning_high_water_mark(nkg, iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    nkg.clean()
    nkg.o.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    nkg.call("GapGen", GAP, 40)
    nkg.build()
    nkg.o.classMethodValue("Graph.KG.Adjacency", "RemoveNode", 0, GAP + "v7")
    nkg.build()
    live = int(nkg.o.get("^NKG", "$meta", "liveNodes"))
    high = int(nkg.o.get("^NKG", "$meta", "nodeCount"))
    assert live < high
    report = IRISGraphEngine(iris_connection).verify_sync()
    assert report.global_nodes == live, (report.global_nodes, live, high)
