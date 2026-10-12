"""Freshness against a live IRIS: the token and the stamps are read and written through ``host_for(conn)``.

Needs the enterprise test container (``iris_connection``). The unit tests in ``tests/unit/test_freshness.py`` pin the
rule against an in-memory host; these check that the same operations work through the real host and the real
``^KG("__version")`` and ``^IVG.BuiltFrom`` globals.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.freshness import DEG2P, NKG, Freshness
from iris_vector_graph.host import host_for

pytestmark = pytest.mark.e2e

_STORES = (NKG, DEG2P)


@pytest.fixture
def live(iris_connection):
    host = host_for(iris_connection)
    fresh = Freshness(host)
    # Start from nothing stamped; leave nothing stamped behind.
    for store in _STORES:
        host.kill("^IVG.BuiltFrom", store)
    yield host, fresh
    for store in _STORES:
        host.kill("^IVG.BuiltFrom", store)


def _bump(host):
    """A write path's ``Set tmp = $Increment(^KG("__version"))``, done through the host's global operations."""
    host.set(int(host.get("^KG", "__version") or 0) + 1, "^KG", "__version")


def test_the_token_matches_the_version_global_the_host_reads(live):
    host, fresh = live

    assert fresh.token() == int(host.get("^KG", "__version") or 0)


def test_a_store_never_stamped_on_the_live_host_is_not_current(live):
    _, fresh = live

    assert fresh.built_from(NKG) is None
    assert fresh.is_current(NKG) is False
    assert set(fresh.stale_stores(_STORES)) == set(_STORES)


def test_stamp_makes_the_store_current_until_the_token_moves(live):
    host, fresh = live

    fresh.stamp(NKG)
    assert fresh.is_current(NKG) is True
    assert fresh.built_from(NKG) == fresh.token()

    _bump(host)

    assert fresh.is_current(NKG) is False
    assert fresh.stale_stores(_STORES) == [NKG, DEG2P]

    fresh.stamp(NKG)
    assert fresh.is_current(NKG) is True


def test_stamps_persist_across_host_objects(live, iris_connection):
    host, fresh = live
    fresh.stamp(DEG2P)

    other = Freshness(host_for(iris_connection))

    assert other.is_current(DEG2P) is True
    assert other.built_from(DEG2P) == fresh.token()
