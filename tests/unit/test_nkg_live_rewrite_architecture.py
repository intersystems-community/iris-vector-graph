"""D-NKG-SWAP, pinned against the source (no container). The behaviour is proven against
the server in tests/e2e/test_nkg_live_rewrite_e2e.py.

* BuildNKG never empties ^NKG: it does not call DropNKG and kills no whole subtree of it.
* One build at a time, under its own lock ^IVG.NKGBuild, never under the writers' lock
  ^IVG.AdjW (writers keep writing during a rewrite).
* The version is raised before the walk and again after the sweep.
* Sketches are recomputed and Set, never merged into the old value.
* The rewrite swallows no error: no Catch in the rewrite methods, and BuildNKG's own Catch
  hands the status on.
* The readers take no lock and gain none: the classes that reference ^NKG are the same
  eighteen, and only TraversalBuild knows the build lock. Three readers that walked the index
  range as if it were dense were fixed for gaps (option D, Tom 2026-10-31): ClosenessGlobalMSBFS,
  EigenvectorGlobal, ExportAdjacency's node map.
* Dictionary removal (gaps) only without an Arno library, or with one whose capabilities report
  declares "nkg_dict_gaps"; recorded in $meta("dictSwept"), with liveNodes / liveLabels.
* `engine.sync()` has no Rust branch: nothing in the package names BuildNKGRust, and
  _sync_nkg swallows nothing inside its documented True/False result.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "iris_src" / "src" / "Graph" / "KG"
BUILD = KG / "TraversalBuild.cls"
SCHEMA = REPO / "iris_vector_graph" / "_engine" / "schema.py"

#: The classes that reference ^NKG (D-NKG-SWAP "Facts": the same 18 in 4.4.x and at the lab base).
NKG_CLASSES = {
    "Adjacency.cls", "ArnoAccel.cls", "Communities.cls", "EdgeScan.cls", "Eraser.cls",
    "GraphIndex.cls", "GraphKey.cls", "GraphStores.cls", "GraphVerify.cls", "Ledger.cls",
    "Meta.cls", "NKGAccel.cls", "NKGAccelAdjacency.cls", "NKGAccelCentrality.cls",
    "NKGAccelLoader.cls", "NKGAccelTraversal.cls", "TraversalBuild.cls", "TraversalKHop.cls",
}

REWRITE_METHODS = ("RewriteNKG", "RewritePass", "WalkSource", "SweepEdges", "SweepMirrors",
                   "SweepDegrees", "SweepSketches", "SweepNodes", "SweepLabels", "SweepEdge")


def _code(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("///"):
            continue
        out.append(re.sub(r"(?<![:\"])//.*$", "", line))
    return "\n".join(out)


def _method(text: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"no ClassMethod {name}"
    return m.group(0)


def _build() -> str:
    return BUILD.read_text()


def test_build_nkg_never_drops_or_kills_nkg_whole():
    src = _code(_build())
    body = _method(src, "BuildNKG")
    assert "DropNKG" not in body
    for name in ("BuildNKG",) + REWRITE_METHODS:
        b = _method(src, name)
        assert not re.search(r"\bKill\s+\^NKG\s*(,|$)", b, re.M), name
        assert not re.search(r'\bKill\s+\^NKG\(\s*(-1|-2|-3|"\$agg"|"\$ND"|"\$NI"|"\$LS"|"\$LI"|"\$meta")\s*\)', b), name


def test_one_build_at_a_time_under_its_own_lock_not_the_writers():
    body = _code(_method(_build(), "BuildNKG"))
    assert re.search(r"Lock \+\^IVG\.NKGBuild:", body), "BuildNKG takes no build lock with a timeout"
    assert "Lock -^IVG.NKGBuild" in body
    assert "IVG.AdjW" not in "\n".join(_method(_code(_build()), m) for m in ("BuildNKG",) + REWRITE_METHODS)


def test_the_version_rises_before_the_walk_and_after_the_sweep():
    body = _code(_method(_build(), "RewritePass"))
    bumps = [m.start() for m in re.finditer(r'\$Increment\(\^NKG\("\$meta", "version"\)\)', body)]
    assert len(bumps) == 2, bumps
    walk = body.index("WalkSource(")
    sweep = body.index("SweepNodes(")
    assert bumps[0] < walk < sweep < bumps[1]
    assert body.index('Set ^NKG("$meta", "builtFrom")') < bumps[1]


def test_sketches_are_recomputed_and_set_never_merged():
    src = _code(_build())
    for name in ("BuildNKG",) + REWRITE_METHODS:
        b = _method(src, name)
        assert "UpdateStructuralHLL" not in b, f"{name} merges into the old sketch"
        assert "MergeHLL" not in b, name
    sweep = _method(src, "SweepEdges")
    assert "EmptyHLL()" in sweep and 'Set ^NKG("$agg", tS, tPIdx, "hll") = tH' in sweep


def test_the_rewrite_swallows_no_error():
    src = _code(_build())
    for name in REWRITE_METHODS:
        assert "Catch" not in _method(src, name), f"{name} catches"
    body = _method(src, "BuildNKG")
    # the cache invalidations log (they change no data); the rewrite's status is thrown on
    assert "$$$ThrowOnError(tSC)" in body
    for catch in re.finditer(r"Catch (\w+) \{(.*?)\n\t\}", body, re.S):
        if "InvalidateAdjCache" not in body[max(0, catch.start() - 200):catch.start()]:
            assert "AsStatus" in catch.group(2) or "Throw" in catch.group(2), catch.group(0)


def test_the_readers_are_the_same_eighteen_and_only_the_builder_knows_the_lock():
    referencing = {p.name for p in KG.glob("*.cls") if "^NKG" in p.read_text()}
    assert referencing == NKG_CLASSES, referencing ^ NKG_CLASSES
    for path in (REPO / "iris_src" / "src").rglob("*.cls"):
        if path.name != "TraversalBuild.cls":
            assert "IVG.NKGBuild" not in path.read_text(), path.name


def test_the_package_never_names_the_rust_builder():
    for path in (REPO / "iris_vector_graph").rglob("*.py"):
        assert "BuildNKGRust" not in path.read_text(), path


def test_sync_nkg_has_no_inner_handler_that_swallows():
    src = SCHEMA.read_text()
    body = src[src.index("    def _sync_nkg(") :]
    body = body[: body.index("\n    def ", 10)]
    # 4.6.0 R135: no handler at all; a failed rebuild raises out of sync()
    assert body.count("except") == 0
    assert "pass" not in re.sub(r"#.*", "", body)


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0,)
    conn.cursor.return_value.fetchall.return_value = []
    return IRISGraphEngine(conn, embedding_dimension=4)


def test_sync_nkg_builds_in_objectscript_even_when_arno_is_detected():
    eng = _engine()
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = "1"  # BuildNKG's $$$OK (R135 reads it); Build2HopStats' count
    eng._arno_capabilities = {"rust_callout": True}
    with patch.object(eng, "_iris_obj", return_value=iris_obj), patch.object(eng, "_detect_arno", return_value=True):
        assert eng._sync_nkg() is True
    names = [c.args[1] for c in iris_obj.classMethodValue.call_args_list + iris_obj.classMethodVoid.call_args_list]
    assert "BuildNKGRust" not in names
    iris_obj.classMethodValue.assert_any_call("Graph.KG.Traversal", "BuildNKG")


# --- index gaps: option D (Tom, 2026-10-31) -----------------------------------------------


def test_dictionary_removal_is_conditional_and_recorded():
    src = _code(_build())
    rp = _method(src, "RewritePass")
    assert "DictSweepAllowed()" in rp
    assert 'Set ^NKG("$meta", "dictSwept") = ' in rp
    assert 'Set ^NKG("$meta", "liveNodes") = ' in rp and 'Set ^NKG("$meta", "liveLabels") = ' in rp
    for name in ("SweepNodes", "SweepLabels"):
        body = _method(src, name)
        assert re.search(rf"ClassMethod {name}\(pRemove", body), f"{name} takes no removal switch"
        assert "If pRemove" in body or "If 'pRemove" in body, name


def test_the_gap_signal_is_a_capability_key_not_a_version_number():
    src = _code(_build())
    allowed = _method(src, "DictSweepAllowed")
    assert '^ArnoAccel("lib_path")' in allowed
    caps = _method(src, "ArnoCapabilities")
    assert '"capabilities"' in caps
    declared = _method(src, "GapsDeclared")
    assert '"nkg_dict_gaps"' in declared
    assert "LibraryVersion" not in allowed + caps + declared and "version" not in declared


def test_index_range_readers_do_not_assume_dense_indices():
    cen = (KG / "NKGAccelCentrality.cls").read_text()
    ms = _method(_code(cen), "ClosenessGlobalMSBFS")
    assert not re.search(r'\^NKG\("\$meta", *"nodeCount"\)', ms), "classical closeness must count live nodes, not read the high-water mark"
    eig = _method(_code(cen), "EigenvectorGlobal")
    assert not re.search(r"For i = 0:1:n-1", eig), "EigenvectorGlobal walks 0..n-1 over sparse indices"
    exp = _method(_code((KG / "NKGAccelAdjacency.cls").read_text()), "ExportAdjacencyNKG")
    assert re.search(r'Continue:\'\$Data\(\^NKG\("\$ND", i\)\)', exp), "the NODEMAP must skip an undefined $ND(i)"


def test_node_count_is_documented_as_a_high_water_mark_and_verify_sync_reads_live_nodes():
    assert "high-water" in _method(_build(), "NKGNodeCount").lower() or "high-water" in _build().split("ClassMethod NKGNodeCount")[0][-600:].lower()
    admin = (REPO / "iris_vector_graph" / "_engine" / "admin.py").read_text()
    body = admin[admin.index("    def verify_sync(") :]
    body = body[: body.index("\n    def ", 10)]
    assert '"NKGLiveNodeCount"' in body and '"NKGNodeCount"' not in body
