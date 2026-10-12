"""Reference model of the generation-validated degree cache (spec 248).

The protocol, as `Graph.KG.Adj.Degree` and `Graph.KG.Adjacency` implement it
(docs/research/alt-impl/BRIEF-PARITY.md):

  writer   take the shared lock, change `out`, bump `gen` with $Increment, release
           (inside a transaction the release waits for commit or rollback)
  reader   1. read `gen`; if the cached entry carries that generation, return its count
           2. probe the lock exclusively without waiting; note whether it succeeded
           3. count the `out` entries
           4. store (gen, count) only when the probe succeeded AND `gen` is unchanged

This file is a dict-backed model with a simulated "writer in flight" lock state and a
rollback that restores Set and Kill but NOT $Increment, which is what IRIS does. It
proves the four reader steps sound over every interleaving the tests drive, and that
dropping the probe, the unchanged-generation check, or the gen-before-probe order each
produces a wrong answer after a rollback. No container: the live behaviour is locked in
tests/e2e/test_degree_cache_live.py.

One addition to the contract is modelled because erase needs it:

  * `post_check`: after the store the reader re-reads `gen` and kills the entry if it
    moved. Erase kills `gen`, so a counter can restart at a value an old entry carries.

And one property of IRIS the live checks showed, modelled as `tx`: a lock never conflicts
with its own process, so a reader inside the writer's own open transaction gets its probe,
counts its own dirty edge and stores it. The store is a Set inside that transaction, so
the writer's rollback undoes it too (the `tx` argument records the undo).
"""

from __future__ import annotations

import random

import pytest


class Store:
    """The three globals and the lock state of one graph, for one source node each."""

    def __init__(self) -> None:
        self.out: dict[str, int] = {}  # source -> live out-edge count (all predicates)
        self.gen: dict[str, int] = {}
        self.cache: dict[str, tuple[int, int]] = {}
        self.shared_holders = 0  # writers whose lock is still held
        self.walks = 0  # how many times a reader counted the out entries

    def truth(self, src: str) -> int:
        return self.out.get(src, 0)


class Writer:
    """One writer transaction. `gen` bumps survive a rollback, Set and Kill do not."""

    def __init__(self, store: Store, autocommit: bool = False) -> None:
        self.s = store
        self.undo: list = []
        self.open = True
        self.s.shared_holders += 1  # Lock +^IVG.AdjW(g)#"S"
        self.autocommit = autocommit

    def _set_out(self, src: str, value: int) -> None:
        self.undo.append(("out", src, self.s.out.get(src)))
        if value <= 0:
            self.s.out.pop(src, None)
        else:
            self.s.out[src] = value

    def put(self, src: str) -> None:
        self._set_out(src, self.s.out.get(src, 0) + 1)
        self.s.gen[src] = self.s.gen.get(src, 0) + 1  # $Increment: never undone

    def remove(self, src: str) -> None:
        if self.s.out.get(src, 0) > 0:
            self._set_out(src, self.s.out[src] - 1)
            self.s.gen[src] = self.s.gen.get(src, 0) + 1

    def kill_source(self, src: str) -> None:
        """RemoveSource: kills the out entries, `gen` and `degc` of the source."""
        self._set_out(src, 0)
        self.undo.append(("gen", src, self.s.gen.get(src)))
        self.s.gen.pop(src, None)
        # The Kill of `degc` is not undone on purpose: a rollback would put back the
        # before-image over whatever a concurrent reader stored meanwhile, and the model
        # must not lean on that.
        self.s.cache.pop(src, None)

    def commit(self) -> None:
        assert self.open
        self.open = False
        self.s.shared_holders -= 1

    def rollback(self) -> None:
        assert self.open
        for kind, src, old in reversed(self.undo):
            table = {"out": self.s.out, "gen": self.s.gen, "cache": self.s.cache}[kind]
            if old is None:
                table.pop(src, None)
            else:
                table[src] = old
        self.open = False
        self.s.shared_holders -= 1


class Reader:
    """`Adj.Degree` as four steps, so a test can put a writer between any two of them."""

    def __init__(
        self,
        store: Store,
        src: str,
        *,
        use_probe: bool = True,
        check_gen: bool = True,
        post_check: bool = True,
        gen_before_probe: bool = True,
        tx: "Writer | None" = None,
    ) -> None:
        self.s, self.src = store, src
        self.use_probe, self.check_gen, self.post_check = use_probe, check_gen, post_check
        self.gen_before_probe, self.tx = gen_before_probe, tx
        self.gen = 0
        self.ok = True
        self.count = 0
        self.answer: int | None = None
        self.step = 0

    def _read_gen(self) -> bool:
        """Step 1. True when the cached entry already carries the generation (a hit)."""
        self.gen = self.s.gen.get(self.src, 0)
        hit = self.s.cache.get(self.src)
        if hit is not None and hit[0] == self.gen:
            self.answer = hit[1]
            return True
        return False

    def _probe(self) -> None:
        """Step 2. Lock +^IVG.AdjW(g)#"E":0 succeeds only when no shared holder remains."""
        self.ok = (not self.use_probe) or self.s.shared_holders == 0

    def _count(self) -> None:
        """Step 3."""
        self.s.walks += 1
        self.count = self.s.truth(self.src)

    def _store(self) -> None:
        """Step 4."""
        unchanged = (not self.check_gen) or self.s.gen.get(self.src, 0) == self.gen
        if self.ok and unchanged:
            if self.tx is not None:  # the store is a Set inside the process's own transaction
                self.tx.undo.append(("cache", self.src, self.s.cache.get(self.src)))
            self.s.cache[self.src] = (self.gen, self.count)
            if self.post_check and self.s.gen.get(self.src, 0) != self.gen:
                self.s.cache.pop(self.src, None)
        self.answer = self.count

    def steps(self):
        """The order under test: gen then probe (the contract), or probe then gen (unsound)."""
        if self.gen_before_probe:
            return (self._read_gen, self._probe, self._count, self._store)
        return (self._probe, self._read_gen, self._count, self._store)

    def advance(self) -> bool:
        """Run the next step; True when the read has finished."""
        step = self.steps()[self.step]
        self.step += 1
        result = step()
        if step == self._read_gen and result:
            return True  # served from the cache
        return self.step == 4

    def run(self) -> int:
        while not self.advance():
            pass
        assert self.answer is not None
        return self.answer


def degree(store: Store, src: str, **flags) -> int:
    return Reader(store, src, **flags).run()


# --- the soundness cases ------------------------------------------------------------


def warm(store: Store, src: str = "a", edges: int = 2) -> None:
    for _ in range(edges):
        w = Writer(store)
        w.put(src)
        w.commit()
    assert degree(store, src) == edges


def test_warm_entry_is_returned_without_walking():
    s = Store()
    warm(s)
    walks = s.walks
    assert degree(s, "a") == 2
    assert s.walks == walks, "a current entry must not walk the out entries"


def test_a_committed_write_makes_the_entry_stale_and_it_is_recounted():
    s = Store()
    warm(s)
    w = Writer(s)
    w.put("a")
    w.commit()
    assert degree(s, "a") == 3


def test_dirty_read_during_an_uncommitted_write_is_not_stored_and_rollback_is_clean():
    s = Store()
    warm(s)
    w = Writer(s)
    w.put("a")
    assert degree(s, "a") == 3, "another process sees the uncommitted edge"
    assert "a" in s.cache and s.cache["a"][1] == 2, "the dirty count must not replace the entry"
    w.rollback()
    assert degree(s, "a") == s.truth("a") == 2


def test_without_the_probe_a_rollback_leaves_a_wrong_valid_entry():
    """The spike's failure: reader caches during the uncommitted write at the bumped generation."""
    s = Store()
    warm(s)
    w = Writer(s)
    w.put("a")
    assert degree(s, "a", use_probe=False) == 3
    w.rollback()
    assert s.truth("a") == 2
    assert degree(s, "a", use_probe=False) == 3, "the stale entry carries the current generation"


def climb(s: Store, src: str, n: int) -> None:
    """Bump gen n times (n even) with no net change to the edge count: puts, then removes."""
    for op in ("put",) * (n // 2) + ("remove",) * (n // 2):
        w = Writer(s)
        getattr(w, op)(src)
        w.commit()


def _stale_entry_store() -> Store:
    """Source `a` with gen 3 and a stale entry (2, 2): a committed write after the warm-up."""
    s = Store()
    warm(s)
    w = Writer(s)
    w.put("a")
    w.commit()
    return s


def test_without_the_unchanged_generation_check_a_rolled_back_erase_leaves_a_wrong_valid_entry():
    """Reader probes clean, then an erase (Kill out, Kill gen) starts and the reader counts 0."""
    s = _stale_entry_store()
    r = Reader(s, "a", check_gen=False, post_check=False)
    r.advance()  # gen 3 read
    r.advance()  # probe: clean
    erase = Writer(s)
    erase.kill_source("a")  # uncommitted: out[a] and gen[a] gone
    r.advance()  # counts 0
    r.advance()  # stores (3, 0): the unchanged check is off
    assert r.answer == 0
    erase.rollback()  # out[a] = 3 and gen[a] = 3 come back
    assert s.truth("a") == 3
    assert degree(s, "a", check_gen=False, post_check=False) == 0, "a wrong entry carries the live generation"


def test_with_the_unchanged_generation_check_the_same_erase_rollback_is_clean():
    s = _stale_entry_store()
    r = Reader(s, "a")
    r.advance()
    r.advance()
    erase = Writer(s)
    erase.kill_source("a")
    r.advance()
    r.advance()
    assert r.answer == 0, "the reader did see the uncommitted erase"
    erase.rollback()
    assert degree(s, "a") == s.truth("a") == 3


def test_reading_the_generation_after_the_probe_is_unsound():
    """Probe clean, a writer starts and bumps, the reader then reads the bumped generation."""
    s = _stale_entry_store()
    r = Reader(s, "a", gen_before_probe=False)
    r.advance()  # probe first: clean
    writer = Writer(s)
    writer.put("a")  # gen 4, uncommitted, lock held
    r.advance()  # reads gen 4 AFTER the writer bumped
    r.advance()  # counts 4 (dirty)
    r.advance()  # probe result is from before the writer: stores (4, 4)
    writer.rollback()
    assert s.truth("a") == 3
    assert degree(s, "a", gen_before_probe=False) == 4, "the dirty count sits at the live generation"


def test_the_contract_order_survives_the_same_window():
    s = _stale_entry_store()
    r = Reader(s, "a")
    r.advance()  # gen 3
    r.advance()  # probe clean
    writer = Writer(s)
    writer.put("a")  # gen 4, uncommitted
    r.advance()  # counts 4 (dirty)
    r.advance()  # gen is 4, not 3: nothing stored
    writer.rollback()
    assert degree(s, "a") == s.truth("a") == 3


def test_a_reader_inside_the_writers_own_transaction_stores_and_the_rollback_takes_it_back():
    """The lock does not conflict with its own process, so the probe succeeds and the reader
    stores its own dirty count; that store is inside the transaction and is rolled back with it."""
    s = Store()
    warm(s)
    w = Writer(s)
    w.put("a")
    held, s.shared_holders = s.shared_holders, 0  # same process: its own S lock does not block its probe
    assert degree(s, "a", tx=w) == 3
    assert s.cache["a"] == (s.gen["a"], 3), "stored inside the transaction"
    s.shared_holders = held
    w.rollback()
    assert s.cache["a"] == (2, 2), "the rollback restored the entry"
    assert degree(s, "a") == s.truth("a") == 2


def test_post_store_check_removes_an_entry_stored_after_a_generation_reset():
    """Erase killed gen; an entry stored with the old generation after the kill must not stay valid.

    The window: the reader's unchanged check passes, the erase commits (Kill gen, Kill
    degc), and only then does the reader store. gen restarts at 0 and climbs back to the
    value the entry carries, with different content.
    """
    s = Store()
    warm(s, edges=3)
    w = Writer(s)
    w.put("a")
    w.commit()  # gen 4, entry (3, 3) stale
    r = Reader(s, "a")
    r.advance()
    r.advance()
    r.advance()  # counts 4; the unchanged check (next step) has not run yet
    gen_seen, count_seen = r.gen, r.count
    erase = Writer(s)
    erase.kill_source("a")
    erase.commit()
    # The store lands after the kill. The post-store check is what must remove it:
    s.cache["a"] = (gen_seen, count_seen)
    if s.gen.get("a", 0) != gen_seen:  # the reader's post-store re-read
        s.cache.pop("a", None)
    assert "a" not in s.cache
    climb(s, "a", gen_seen)  # gen climbs back to the old value, with different content
    assert s.gen["a"] == gen_seen and s.truth("a") == 0
    assert degree(s, "a") == 0


def test_without_the_post_store_check_the_reset_alias_is_wrong():
    s = Store()
    warm(s, edges=3)
    w = Writer(s)
    w.put("a")
    w.commit()
    r = Reader(s, "a", post_check=False)
    for _ in range(3):
        r.advance()
    gen_seen, count_seen = r.gen, r.count
    erase = Writer(s)
    erase.kill_source("a")
    erase.commit()
    s.cache["a"] = (gen_seen, count_seen)  # stored after the kill, nothing removes it
    climb(s, "a", gen_seen)
    assert s.gen["a"] == gen_seen and s.truth("a") == 0
    assert degree(s, "a") == count_seen == 4 != s.truth("a")


# --- every interleaving the generator can produce -----------------------------------

SRCS = ("a", "b")


def _run(seed: int, steps: int, **flags) -> int:
    """Random writers (commit or roll back, some erasing a source) and readers advancing a
    step at a time. Returns the number of wrong answers seen once every writer has ended."""
    rng = random.Random(seed)
    s = Store()
    writers: list[Writer] = []
    readers: list[Reader] = []
    wrong = 0
    for _ in range(steps):
        roll = rng.random()
        if roll < 0.18 and len(writers) < 2:
            writers.append(Writer(s))
        elif roll < 0.45 and writers:
            w = rng.choice(writers)
            src = rng.choice(SRCS)
            op = rng.random()
            if op < 0.55:
                w.put(src)
            elif op < 0.85:
                w.remove(src)
            else:
                w.kill_source(src)
        elif roll < 0.58 and writers:
            w = writers.pop(rng.randrange(len(writers)))
            (w.commit if rng.random() < 0.5 else w.rollback)()
        elif roll < 0.72 and len(readers) < 3:
            readers.append(Reader(s, rng.choice(SRCS), **flags))
        elif readers:
            r = rng.choice(readers)
            if r.advance():
                readers.remove(r)
    for w in writers:
        (w.commit if rng.random() < 0.5 else w.rollback)()
    for r in readers:
        r.run()
    # Quiescent: every cache entry that looks current must be right, and a read must agree.
    for src in SRCS:
        entry = s.cache.get(src)
        if entry is not None and entry[0] == s.gen.get(src, 0) and entry[1] != s.truth(src):
            wrong += 1
        if degree(s, src, **flags) != s.truth(src):
            wrong += 1
    return wrong


@pytest.mark.parametrize("seed", range(60))
def test_the_protocol_is_sound_over_random_interleavings(seed):
    assert _run(seed, steps=120) == 0


def _any_failure(**flags) -> int:
    return sum(_run(seed, steps=120, **flags) for seed in range(400))


@pytest.mark.parametrize(
    "ablation",
    [
        {"use_probe": False},
        {"check_gen": False, "post_check": False},
        {"gen_before_probe": False},
        {"use_probe": False, "check_gen": False},
    ],
    ids=["no-probe", "no-unchanged-check", "probe-before-gen", "neither"],
)
def test_each_ablation_is_caught_by_the_random_interleavings(ablation):
    """The generator is strong enough to find the bug when a step of the protocol is dropped."""
    assert _any_failure(**ablation) > 0, f"the model did not notice dropping {ablation}"
