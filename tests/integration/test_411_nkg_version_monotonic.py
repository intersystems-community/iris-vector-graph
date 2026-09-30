"""4.1.1 — ^NKG("$meta","version") keeps rising across every rebuild, erase and restore.

`kg_betweenness_global_v` caches its answer in the IRIS process keyed by that version,
and `^ArnoKG("KG","nkg_adj_ver")` compares against it. `BuildNKG` and the Eraser both
`Kill ^NKG` and then count up from nothing, so every fresh ^NKG was version 1 and a
graph loaded after an erase got the erased graph's betweenness back. A restore imports
the archive's ^NKG and with it an older version. See
tests/unit/test_411_nkg_version_monotonic.py.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


def _version(engine):
    return int(engine._iris_obj().get("^NKG", "$meta", "version") or 0)


def _chain(engine, prefix, n=6, graph=None):
    for i in range(n):
        engine.create_node(f"{prefix}{i}", graph=graph)
    for i in range(n - 1):
        engine.create_edge(f"{prefix}{i}", "IVG411_NKGVER", f"{prefix}{i + 1}", graph=graph)
    engine.conn.commit()
    engine.sync()


def test_betweenness_after_an_erase_answers_the_new_graph(engine):
    engine._store._detect_arno()
    for _ in range(3):
        engine.erase_all()
        prefix = f"nv{uuid.uuid4().hex[:6]}_"
        _chain(engine, prefix)
        rows = engine.betweenness_centrality(sample_size=0, top_k=20)
        assert rows and all(r["id"].startswith(prefix) for r in rows), rows[:3]


def test_rebuild_erase_all_and_erase_graph_each_raise_the_version(engine):
    _chain(engine, f"nv{uuid.uuid4().hex[:6]}_")
    seen = [_version(engine)]
    engine._iris_obj().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    seen.append(_version(engine))
    _chain(engine, f"nv{uuid.uuid4().hex[:6]}_", graph="ivg411nkgver")
    engine.erase_graph("ivg411nkgver")
    seen.append(_version(engine))
    engine.erase_all()
    seen.append(_version(engine))
    assert seen == sorted(set(seen)), seen


def test_restore_does_not_bring_back_an_older_version(engine, tmp_path):
    _chain(engine, f"nv{uuid.uuid4().hex[:6]}_")
    path = str(tmp_path / "nkgver.zip")
    engine.save_snapshot(path)
    archived = _version(engine)
    for _ in range(3):
        engine._iris_obj().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    before = _version(engine)
    assert before > archived

    engine.restore_snapshot(path)

    assert _version(engine) > before
