"""Freshness: is a Rebuilt store still current, answered from the Graph-Version Token and per-store stamps.

ADR-0008 and CONTEXT.md "Rebuilt store": a derived store records the ``^KG("__version")`` it was built from and is
current while that equals the live token. These tests pin that rule against an in-memory host, with no IRIS.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.freshness import DEG2P, NKG, Freshness


class FakeHost:
    """The five global operations of ``iris_vector_graph.host.Host``, over a dict keyed by (name, subscripts)."""

    def __init__(self):
        self.data = {}

    @staticmethod
    def _key(name, subs):
        return (name, tuple(subs))

    def get(self, name, *subs):
        return self.data.get(self._key(name, subs))

    def set(self, value, name, *subs):
        self.data[self._key(name, subs)] = value

    def kill(self, name, *subs):
        for key in [k for k in self.data if k[0] == name and k[1][: len(subs)] == tuple(subs)]:
            del self.data[key]

    def is_defined(self, name, *subs):
        subs = tuple(subs)
        has_value = self._key(name, subs) in self.data
        has_child = any(k[0] == name and len(k[1]) > len(subs) and k[1][: len(subs)] == subs for k in self.data)
        return (10 if has_child else 0) + (1 if has_value else 0)

    def next_subscript(self, name, *subs, reverse=False):
        subs = tuple(subs)
        parent, after = subs[:-1], subs[-1] if subs else ""
        sibs = sorted(
            {k[1][len(parent)] for k in self.data if k[0] == name and k[1][: len(parent)] == parent and len(k[1]) > len(parent)}
        )
        if reverse:
            sibs.reverse()
        if after == "":
            return sibs[0] if sibs else None
        later = [s for s in sibs if (s < after if reverse else s > after)]
        return later[0] if later else None


def bump(host, times=1):
    """What a write path does: ``Set tmp = $Increment(^KG("__version"))``."""
    for _ in range(times):
        host.set(int(host.get("^KG", "__version") or 0) + 1, "^KG", "__version")


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def fresh(host):
    return Freshness(host)


def test_store_names_are_the_constants_the_rest_of_ivg_uses():
    assert NKG == "NKG"
    assert DEG2P == "deg2p"


def test_token_is_zero_when_the_version_global_is_unset(fresh):
    assert fresh.token() == 0


def test_token_reads_the_version_global(host, fresh):
    host.set(7, "^KG", "__version")

    assert fresh.token() == 7


def test_a_store_that_was_never_stamped_is_not_current(fresh):
    assert fresh.built_from(NKG) is None
    assert fresh.is_current(NKG) is False


def test_a_store_that_was_never_stamped_is_not_current_even_at_token_zero(fresh):
    # Token 0 is what an unset version reads as; an unstamped store must not match it by accident.
    assert fresh.token() == 0
    assert fresh.is_current(NKG) is False


def test_stamp_then_current(fresh, host):
    bump(host, 3)

    fresh.stamp(NKG)

    assert fresh.built_from(NKG) == 3
    assert fresh.is_current(NKG) is True


def test_stamp_writes_the_token_under_the_built_from_global(host, fresh):
    bump(host, 2)

    fresh.stamp(DEG2P)

    assert host.get("^IVG.BuiltFrom", DEG2P) == 2


def test_token_moving_makes_a_stamped_store_not_current(host, fresh):
    bump(host, 3)
    fresh.stamp(NKG)

    bump(host)

    assert fresh.built_from(NKG) == 3
    assert fresh.token() == 4
    assert fresh.is_current(NKG) is False


def test_stamping_again_at_the_new_token_makes_it_current(host, fresh):
    bump(host, 3)
    fresh.stamp(NKG)
    bump(host)
    assert fresh.is_current(NKG) is False

    fresh.stamp(NKG)

    assert fresh.built_from(NKG) == 4
    assert fresh.is_current(NKG) is True


def test_a_rolled_back_bump_leaves_the_store_not_current_and_never_wrongly_current(host, fresh):
    # $Increment is not undone by a rollback (ADR-0010), so the token can move with no content change behind it.
    # The stamp then never matches again until a rebuild stamps: stale, never wrongly current.
    bump(host, 5)
    fresh.stamp(NKG)
    bump(host)  # a write that was later rolled back
    assert fresh.is_current(NKG) is False

    bump(host, 10)  # more bumps still never land on the old stamp
    assert fresh.is_current(NKG) is False


def test_stamps_are_per_store(host, fresh):
    bump(host, 2)
    fresh.stamp(NKG)

    assert fresh.is_current(NKG) is True
    assert fresh.is_current(DEG2P) is False

    bump(host)
    fresh.stamp(DEG2P)

    assert fresh.is_current(DEG2P) is True
    assert fresh.is_current(NKG) is False


def test_stale_stores_keeps_the_order_it_was_given(host, fresh):
    bump(host, 1)
    fresh.stamp(NKG)
    fresh.stamp(DEG2P)
    bump(host)
    fresh.stamp(DEG2P)

    assert fresh.stale_stores([DEG2P, NKG, "arno"]) == [NKG, "arno"]
    assert fresh.stale_stores([NKG, DEG2P]) == [NKG]
    assert fresh.stale_stores([DEG2P, NKG]) == [NKG]


def test_stale_stores_of_nothing_is_empty(fresh):
    assert fresh.stale_stores([]) == []


def test_stale_stores_counts_a_never_stamped_store_as_stale(host, fresh):
    bump(host)

    assert fresh.stale_stores(["never-built"]) == ["never-built"]


def test_stale_stores_accepts_any_iterable(host, fresh):
    bump(host)
    fresh.stamp(NKG)

    assert fresh.stale_stores(iter([NKG, DEG2P])) == [DEG2P]


def test_is_current_reads_only_through_the_host_interface(host):
    # The module must work with nothing but the five host operations: no raw native handle is ever asked for.
    class StrictHost(FakeHost):
        def __getattr__(self, name):  # pragma: no cover - failing here is the point
            raise AssertionError(f"Freshness reached past the host interface: {name}")

    strict = StrictHost()
    f = Freshness(strict)
    strict.set(1, "^KG", "__version")
    f.stamp(NKG)

    assert f.is_current(NKG) is True
