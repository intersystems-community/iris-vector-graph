"""4.7.0 row 12, live: a cosine VecIndex's ``score`` is the cosine.

``Graph.KG.VecIndex`` answered score = -distance = cos - 1 for a cosine index, so identical vectors scored 0. Now score
= cos on every search entry (brute force, the trees, several queries, a seeded search): identical 1.0, orthogonal 0.0,
opposite -1.0. ``distance`` stays 1 - cos: 0, 1 and 2.

    IVG_NAMESPACE=IVG470ALG IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_vecindex_cosine_e2e.py
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import conn  # noqa: F401 - a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

#: cos of the query [1, 0] with each stored vector, and the distance 1 - cos
COSINE = {"same": (1.0, 0.0), "orth": (0.0, 1.0), "opp": (-1.0, 2.0)}
VECTORS = {"same": [1.0, 0.0], "orth": [0.0, 1.0], "opp": [-1.0, 0.0]}


@pytest.fixture(scope="module")
def vec(conn):  # noqa: F811
    eng = ns463.engines(conn)["default"]
    name = f"alg470_{uuid.uuid4().hex[:6]}"
    eng.vec_create_index(name, dim=2, metric="cosine")
    for doc, v in VECTORS.items():
        eng.vec_insert(name, doc, v)
    try:
        yield eng, name
    finally:
        eng.vec_drop(name)


def _check_hits(hits):
    got = {h["id"]: (float(h["score"]), float(h["distance"])) for h in hits}
    assert set(got) == set(COSINE), hits
    for doc, (score, distance) in COSINE.items():
        assert got[doc][0] == pytest.approx(score, abs=1e-9), (doc, got)
        assert got[doc][1] == pytest.approx(distance, abs=1e-9), (doc, got)
    assert [h["id"] for h in hits] == ["same", "orth", "opp"]


def test_brute_force_cosine_score_is_the_cosine(vec):
    eng, name = vec
    _check_hits(eng.vec_search(name, [1.0, 0.0], k=3))


def test_tree_search_cosine_score_is_the_cosine(vec):
    eng, name = vec
    eng.vec_build(name)
    _check_hits(eng.vec_search(name, [1.0, 0.0], k=3, nprobe=64))
    [multi] = eng.vec_search_multi(name, [[1.0, 0.0]], k=3, nprobe=64)
    _check_hits(multi)
    _check_hits(eng.vec_expand(name, "same", k=3))
