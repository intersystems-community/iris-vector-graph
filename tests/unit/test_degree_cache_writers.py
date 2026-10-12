"""Source scans for the generation-validated degree cache (spec 248).

The cache is sound only if every code path that changes an `out` entry bumps the
generation under the shared lock (or kills the cache), and only the cache's own code
writes the cache. This runs without a container; the behaviour is locked in
tests/e2e/test_degree_cache_live.py and the protocol itself in
tests/unit/test_degree_cache_protocol.py.

The rules, from docs/research/alt-impl/BRIEF-PARITY.md:

  1. Nothing in iris_src writes ^KG("deg") or ^KG("degp"): a stored counter cannot be
     kept exact inside a transaction.
  2. ^KG("gen") is set or incremented only in Graph.KG.Adjacency (and killed only there).
  3. ^KG("degc") is set only in Graph.KG.Adj (the reader's store and the warm fill) and
     killed only in Graph.KG.Adj and Graph.KG.Adjacency.
  4. Every class that sets or kills ^KG("out") references ^KG("gen") or ^KG("degc").
     The allow-list for out/in writers is empty: Adjacency is the only one.
  5. Inside Adjacency, every method that sets or kills out takes the shared lock and
     bumps or kills gen, except the listed ones, each with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_adjacency_single_writer import (
    ADJACENCY,
    IRIS_SRC,
    _strip_comments,
    _writes_in,
)

KG_DIR = IRIS_SRC / "Graph" / "KG"
ADJ = KG_DIR / "Adj.cls"

#: Classes that write out/in and are not Graph.KG.Adjacency: none. A new entry needs a
#: reason that says why it may bypass the lock and the generation.
OUT_WRITER_EXCEPTIONS: dict[str, str] = {}

#: Adjacency methods that set or kill `out` without a lock-and-bump of their own.
LOCK_EXEMPT_METHODS = {
    "Reweight": "weight refresh only: the set of out entries does not change, so degree does not",
    "SetLeafFromLowest": "private; repoints a leaf at a surviving ekey's weight inside PutKeyed/RemoveKeyed's lock window",
    "FillEdge": "bulk rebuild: the caller holds the shared lock on the parent name from BeginRebuild to EndRebuild",
    "FillTriple": "bulk rebuild, as FillEdge",
}


def _stores(*names: str) -> str:
    return r'\^KG\(\s*"(?:' + "|".join(names) + r')"'


SETTERS = r"(?i:set|s|merge|m)"
KILLERS = r"(?i:kill|k)"


def _hits(path: Path, store: str, verbs: str, include_increment: bool = True) -> list[tuple[int, str]]:
    """Lines of one file that apply one of `verbs` (or $Increment) to `store`."""
    patterns = [
        re.compile(r"(?<![\w$.])" + verbs + r"\s+" + store),
        # a later target in a Set/Kill list
        re.compile(r"(?<![\w$.])" + verbs + r"\s+(?:[^,()]|\([^()]*\))*,\s*" + store),
    ]
    if include_increment:
        patterns.append(re.compile(r"\$(?i:increment|i)\(\s*" + store))
    found = []
    for number, line in enumerate(_strip_comments(path.read_text(encoding="utf-8", errors="replace")), start=1):
        if any(p.search(line) for p in patterns):
            found.append((number, line.strip()))
    return found


def _files_with(store: str, verbs: str, include_increment: bool = True) -> dict[str, list]:
    result = {}
    for path in sorted(IRIS_SRC.rglob("*.cls")):
        hits = _hits(path, store, verbs, include_increment)
        if hits:
            result[path.name] = hits
    return result


# --- scanner self-test ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line, store, verbs, inc, expect",
    [
        ('Set tmp = $Increment(^KG("gen", g, s))', _stores("gen"), SETTERS, True, True),
        ('Set ^KG("degc", g, s, k) = $ListBuild(a, b)', _stores("degc"), SETTERS, False, True),
        ('Kill ^KG("degc", g)', _stores("degc"), KILLERS, False, True),
        ('Kill ^KG("gen"), ^KG("degc")', _stores("degc"), KILLERS, False, True),
        ('Set x = +$Get(^KG("gen", g, s))', _stores("gen"), SETTERS, True, False),
        ('Set tC = $Get(^KG("degc", g, s, k))', _stores("degc"), SETTERS, False, False),
        ('Set ^KG("deg2p", g, s, p) = 1', _stores("deg", "degp"), SETTERS, True, False),
        ('Set tmp = $Increment(^KG("deg", g, s))', _stores("deg", "degp"), SETTERS, True, True),
        ('Set ^KG("degp", g, s, p) = n', _stores("deg", "degp"), SETTERS, True, True),
        ('Kill ^KG("deg")', _stores("deg", "degp"), KILLERS, False, True),
        ('// Kill ^KG("deg") is prose', _stores("deg", "degp"), KILLERS, False, False),
    ],
)
def test_scanner(tmp_path, line, store, verbs, inc, expect):
    f = tmp_path / "X.cls"
    f.write_text(line + "\n")
    assert bool(_hits(f, store, verbs, inc)) is expect


# --- rule 1: no stored counters --------------------------------------------------------


#: Adjacency methods that Kill the stored counters: residue from before spec 248 that nothing writes
#: any more but a database upgraded in place may still hold. An erase must not leave it behind
#: (the Eraser used to kill it), and a rebuild (RemoveAll) clears it for the readers that listed it.
LEGACY_COUNTER_KILLS = ("RemoveSource", "RemoveGraph", "RemoveAll")


def test_nothing_writes_the_stored_degree_counters():
    stores = _stores("deg", "degp")
    setters = _files_with(stores, SETTERS)
    assert not setters, (
        'These classes still write ^KG("deg") or ^KG("degp"); the degree is counted and cached, not stored:\n'
        + "\n".join(f"  {name}:{n}: {line}" for name, hits in sorted(setters.items()) for n, line in hits)
    )


def test_the_stored_counters_are_killed_only_by_the_adjacency_erase_methods():
    killers = _files_with(stores_re := _stores("deg", "degp"), KILLERS, include_increment=False)
    assert set(killers) <= {"Adjacency.cls"}, f"stored counters killed by {sorted(killers)}"
    methods = _methods(ADJACENCY)
    for name, body in methods.items():
        has_kill = any(re.search(r"(?i:kill|k)\s+" + stores_re, line) for line in body.splitlines())
        assert has_kill == (name in LEGACY_COUNTER_KILLS), (
            f"Adjacency.{name}: kills of the legacy counters are allowed only in {LEGACY_COUNTER_KILLS}"
        )


# --- rules 2 and 3: who may touch gen and degc --------------------------------------------


def test_only_adjacency_sets_or_increments_gen():
    writers = _files_with(_stores("gen"), SETTERS)
    assert set(writers) == {"Adjacency.cls"}, f"gen written by {sorted(writers)}"


def test_only_adjacency_kills_gen():
    writers = _files_with(_stores("gen"), KILLERS, include_increment=False)
    assert set(writers) == {"Adjacency.cls"}, f"gen killed by {sorted(writers)}"


def test_only_adj_sets_degc():
    writers = _files_with(_stores("degc"), SETTERS, include_increment=False)
    assert set(writers) == {"Adj.cls"}, f"degc set by {sorted(writers)}"


def test_only_adj_and_adjacency_kill_degc():
    writers = _files_with(_stores("degc"), KILLERS, include_increment=False)
    assert set(writers) == {"Adj.cls", "Adjacency.cls"}, f"degc killed by {sorted(writers)}"


# --- rule 4: out/in writers reference the cache ------------------------------------------


def test_every_out_writer_other_than_adjacency_is_listed_and_references_the_cache():
    out_writers = {n for n in _files_with(_stores("out"), SETTERS) } | set(
        _files_with(_stores("out"), KILLERS, include_increment=False)
    )
    others = out_writers - {"Adjacency.cls"}
    assert others == set(OUT_WRITER_EXCEPTIONS), (
        f"out is written by {sorted(others)}; the only allowed exceptions are {sorted(OUT_WRITER_EXCEPTIONS)}"
    )
    for name in others:
        text = "\n".join(_strip_comments((KG_DIR / name).read_text(encoding="utf-8")))
        assert '^KG("gen"' in text or '^KG("degc"' in text, f"{name} writes out without touching gen or degc"


# --- rule 5: inside Adjacency ------------------------------------------------------------


def _methods(path: Path) -> dict[str, str]:
    """name -> comment-stripped body, for every ClassMethod in a class file."""
    text = "\n".join(_strip_comments(path.read_text(encoding="utf-8")))
    parts = re.split(r"(?m)^ClassMethod\s+", text)[1:]
    out = {}
    for part in parts:
        name = re.match(r"(\w+)\(", part)
        assert name, part[:80]
        out[name.group(1)] = part
    return out


ADJACENCY_METHODS = _methods(ADJACENCY)
S_LOCK = re.compile(r'Lock\s+\+\^IVG\.AdjW(?:\([^)]*\))?#"S"')
S_UNLOCK = re.compile(r'Lock\s+-\^IVG\.AdjW(?:\([^)]*\))?#"S"')
GEN_BUMP = re.compile(r'\$(?i:increment|i)\(\s*\^KG\(\s*"gen"')
GEN_KILL = re.compile(r'(?i:kill|k)\s+(?:[^,()\n]|\([^()]*\))*\^KG\(\s*"gen"')
OUT_TOUCH = [
    re.compile(r"(?<![\w$.])(?i:set|s|kill|k|merge|m)\s+(?:[^,()\n]|\([^()]*\))*\^KG\(\s*\"out\""),
]


def _touches_out(body: str) -> bool:
    return any(p.search(line) for line in body.splitlines() for p in OUT_TOUCH)


def test_adjacency_has_the_methods_the_lead_asked_for():
    for name in (
        "Put", "PutShadow", "PutKeyed", "Remove", "RemoveKeyed", "Reweight",
        "RemoveSource", "RemoveNode", "RemoveGraph", "RemoveAll",
        "BeginRebuild", "FillEdge", "FillTriple", "EndRebuild",
    ):
        assert name in ADJACENCY_METHODS, f"Adjacency has no ClassMethod {name}"


@pytest.mark.parametrize("name", sorted(ADJACENCY_METHODS))
def test_a_method_that_changes_out_takes_the_lock_and_bumps_gen(name):
    body = ADJACENCY_METHODS[name]
    if not _touches_out(body):
        pytest.skip(f"{name} does not touch out")
    if name in LOCK_EXEMPT_METHODS:
        assert LOCK_EXEMPT_METHODS[name].strip()
        return
    assert S_LOCK.search(body), f"Adjacency.{name} changes out without Lock +^IVG.AdjW(g)#\"S\""
    assert GEN_BUMP.search(body) or GEN_KILL.search(body), (
        f"Adjacency.{name} changes out without bumping or killing ^KG(\"gen\")"
    )


@pytest.mark.parametrize("name", sorted(ADJACENCY_METHODS))
def test_the_shared_lock_is_released_in_the_method_that_takes_it(name):
    body = ADJACENCY_METHODS[name]
    taken = len(S_LOCK.findall(body))
    released = len(S_UNLOCK.findall(body))
    if name in ("BeginRebuild",):
        assert taken == 1 and released == 0, "BeginRebuild takes the lock and EndRebuild releases it"
        return
    if name in ("EndRebuild",):
        assert taken == 0 and released >= 1
        return
    # A method with several exits releases on each; it never releases less than it takes
    # on any one path, which a textual count can only bound from below.
    assert released >= (1 if taken else 0), f"Adjacency.{name} takes the shared lock and never releases it"


def test_the_lock_is_always_taken_with_a_timeout_and_checked():
    for name, body in ADJACENCY_METHODS.items():
        for line in body.splitlines():
            if S_LOCK.search(line):
                assert re.search(r'#"S":\s*\d+', line), f"Adjacency.{name}: lock without a timeout: {line.strip()}"
        if S_LOCK.search(body):
            assert "$Test" in body, f"Adjacency.{name} never checks whether the lock was granted"


def test_gen_is_bumped_after_the_data_and_before_the_unlock_in_put_and_remove():
    for name in ("Put", "Remove"):
        body = ADJACENCY_METHODS[name]
        lock, unlock = S_LOCK.search(body), S_UNLOCK.search(body)
        bump = GEN_BUMP.search(body)
        data = _touches_out_pos(body)
        assert lock and unlock and bump and data is not None, name
        assert lock.start() < data < bump.start() < unlock.start(), (
            f"Adjacency.{name}: order must be lock, change out, bump gen, unlock"
        )


def _touches_out_pos(body: str):
    for p in OUT_TOUCH:
        m = p.search(body)
        if m:
            return m.start()
    return None


#: One `__version` bump per write, exactly as before the cache (BRIEF-PARITY, item 2).
VERSION_BUMPS = {"Put": 1, "PutShadow": 1, "Remove": 1, "PutKeyed": 1, "RemoveKeyed": 2, "Reweight": 1}


@pytest.mark.parametrize("name", sorted(VERSION_BUMPS))
def test_version_bumps_are_unchanged(name):
    body = ADJACENCY_METHODS[name]
    bumps = len(re.findall(r'\$(?i:increment|i)\(\s*\^KG\(\s*"__version"\s*\)', body))
    assert bumps == VERSION_BUMPS[name], f"Adjacency.{name} bumps ^KG(\"__version\") {bumps} times"


def test_reweight_does_not_bump_gen():
    assert not GEN_BUMP.search(ADJACENCY_METHODS["Reweight"]), "a weight change does not change degree"


def test_put_shadow_bumps_gen_only_for_a_new_edge():
    body = ADJACENCY_METHODS["PutShadow"]
    assert "tExists" in body
    new_branch = body[body.index("If 'tExists"):]
    assert GEN_BUMP.search(new_branch), "PutShadow bumps gen inside the new-edge branch"
    assert not GEN_BUMP.search(body[: body.index("If 'tExists")])


# --- Remove keeps the merged ^NKG entry another graph holds ---------------------------------


def test_remove_guards_the_nkg_delete_against_another_graph_holding_the_triple():
    body = ADJACENCY_METHODS["Remove"]
    delete = body.index("DeleteIndex")
    before = body[:delete]
    assert re.search(r'\$Order\(\^KG\(\s*"out"\s*,\s*\w+\s*\)\)', before) or "HeldElsewhere" in before, (
        "Remove calls GraphIndex.DeleteIndex without checking whether another graph holds the triple"
    )


# --- Adj.Degree: the four reader steps, in order ---------------------------------------------

DEGREE = _methods(ADJ)["Degree"]


def _pos(pattern: str, text: str = DEGREE) -> int:
    m = re.search(pattern, text)
    assert m, f"Adj.Degree has no `{pattern}`"
    return m.start()


def test_degree_follows_the_four_steps_in_order():
    read_gen = _pos(r'\+\$Get\(\^KG\("gen"')
    hit = _pos(r'\$ListGet?\(|\$List\(')
    probe = _pos(r'Lock\s+\+\^IVG\.AdjW\(\w+\)#"E":0')
    got = _pos(r"\$Test")
    count = _pos(r"\.\.Count\(")
    store = _pos(r'Set\s+\^KG\("degc"')
    assert read_gen < hit < probe < got < count < store, "reader steps out of order"


def test_degree_stores_only_when_probe_ok_and_gen_unchanged():
    store_at = _pos(r'Set\s+\^KG\("degc"')
    guard = DEGREE[:store_at].rsplit("If", 1)[-1]
    assert "tOk" in guard or "tGot" in guard, "store is not conditional on the probe"
    assert re.search(r'\^KG\("gen"', guard), "store is not conditional on the generation being unchanged"
    assert "$TLevel" not in guard, "no $TLevel guard: the store belongs to the reader's own transaction and rolls back with it"


def test_degree_releases_the_probe_lock_at_once():
    assert re.search(r'Lock\s+-\^IVG\.AdjW\(\w+\)#"E"', DEGREE)


def test_degree_rechecks_gen_after_the_store_and_kills_a_stale_entry():
    store_at = _pos(r'Set\s+\^KG\("degc"')
    tail = DEGREE[store_at:]
    assert re.search(r'\^KG\("gen"', tail) and re.search(r'(?i:kill|k)\s+\^KG\("degc"', tail)


def test_the_count_is_the_out_entries_with_multiplicity():
    count = _methods(ADJ)["Count"]
    assert '$Order(^KG("out"' in count and '"#"' in count


def test_degree_has_no_reference_to_the_stored_counters():
    assert not re.search(r'\^KG\("degp?"', DEGREE)


# --- the readers ------------------------------------------------------------------------------


def test_khopcount_goes_through_degree_and_does_not_walk():
    body = _methods(KG_DIR / "TraversalKHop.cls")["KHopCount"]
    assert re.search(r"##class\(Graph\.KG\.Adj\)\.Degree\(", body)
    assert "$Order" not in body, "KHopCount must not walk the edges itself"


def test_buildnkg_takes_the_merged_degree_from_the_cache():
    body = _methods(KG_DIR / "TraversalBuild.cls")["BuildNKG"]
    assert "##class(Graph.KG.Adj).Degree(" in body
    assert '"#"' not in body, "BuildNKG no longer counts multiplicity inside the edge walk"


def test_build2hopstats_reads_the_cache_not_a_per_node_array():
    body = _methods(KG_DIR / "TraversalBuild.cls")["Build2HopStats"]
    assert '^KG("degc"' in body and '^KG("gen"' in body, "Build2HopStats reads the warm cache inline"
    assert "Adj).Degree(" in body, "and falls back to Adj.Degree on a miss"
    assert not re.search(r'(?i:set|kill)\s+\^KG\("degc"', body), "a reader never writes the cache"


def test_buildkg_uses_the_rebuild_entry_points_and_leaves_the_cache_warm():
    body = _methods(KG_DIR / "TraversalBuild.cls")["BuildKG"]
    for needle in ("Adjacency).BeginRebuild(", "Adjacency).RemoveAll(", "Adjacency).EndRebuild("):
        assert needle in body, f"BuildKG does not call {needle}"
    assert not _writes_in(body), "BuildKG still sets or kills out/in itself"
    assert "FillEdge(" in body or "BuildKGKeyed" in body


def test_end_rebuild_warms_the_cache():
    body = ADJACENCY_METHODS["EndRebuild"]
    assert "Adj).WarmCache(" in body


def test_warm_all_and_warm_cache_share_one_fill():
    adj = _methods(ADJ)
    assert "WarmFill(" in adj["WarmAll"] and "WarmAll(" in adj["WarmCache"]
    assert "WarmAll(" in adj["WarmCache"] and "Count(" in adj["WarmFill"]
    assert "$Test" in adj["WarmFill"]


def test_backfilldegp_warms_the_cache_instead_of_writing_counters():
    body = _methods(KG_DIR / "TraversalBuild.cls")["BackfillDegp"]
    assert "Adj).WarmCache(" in body


def test_eraser_and_edgescan_do_not_set_or_kill_out_or_in():
    for name in ("Eraser.cls", "EdgeScan.cls", "TemporalIndex.cls", "FHIRGraph.cls", "TraversalBuild.cls"):
        text = "\n".join(_strip_comments((KG_DIR / name).read_text(encoding="utf-8")))
        assert not _writes_in(text), f"{name} writes out/in itself"


def test_eraser_erases_adjacency_through_adjacency():
    text = (KG_DIR / "Eraser.cls").read_text(encoding="utf-8")
    for needle in ("Adjacency).RemoveGraph(", "Adjacency).RemoveAll(", "Adjacency).RemoveNode("):
        assert needle in text, f"Eraser does not call {needle}"


def test_write_adjacency_shadow_wrapper_is_gone():
    text = (KG_DIR / "EdgeScan.cls").read_text(encoding="utf-8")
    assert "WriteAdjacencyShadow" not in text
    assert "WriteAdjacencyShadow" not in (KG_DIR / "TemporalIndex.cls").read_text(encoding="utf-8")


def test_temporal_index_calls_adjacency_directly():
    text = "\n".join(_strip_comments((KG_DIR / "TemporalIndex.cls").read_text(encoding="utf-8")))
    assert "Adjacency).PutShadow(" in text and "Adjacency).Remove(" in text
