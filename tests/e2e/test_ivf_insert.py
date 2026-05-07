import random
import json
import pytest
from iris_vector_graph.engine import IRISGraphEngine


DIM = 16
NLIST = 4


@pytest.fixture(scope="module")
def engine(iris_connection):
    return IRISGraphEngine(iris_connection, embedding_dimension=768)


@pytest.fixture(scope="module")
def ivf_index(engine):
    rng = random.Random(99)
    iris_obj = engine._iris_obj()

    centroids = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(NLIST)]
    iris_obj.classMethodValue(
        "Graph.KG.IVFIndex", "Build",
        "ivftest_idx",
        json.dumps(NLIST),
        json.dumps("cosine"),
        json.dumps(centroids),
        "[]",
    )

    for i in range(40):
        nid = f"ivftest_{i}"
        vec = [rng.gauss(0, 1) for _ in range(DIM)]
        iris_obj.classMethodValue(
            "Graph.KG.IVFIndex", "Insert", "ivftest_idx", nid, json.dumps(vec)
        )
    iris_obj.classMethodValue("Graph.KG.IVFIndex", "FinalizeIndex", "ivftest_idx")

    info = engine.ivf_info("ivftest_idx")
    yield info

    engine.ivf_drop("ivftest_idx")


@pytest.mark.requires_database
@pytest.mark.e2e
class TestIVFInsert:
    def test_insert_appears_in_search(self, engine, ivf_index):
        baseline = ivf_index["indexed"]
        rng = random.Random(7)
        new_id = "ivftest_NEW"
        new_vec = [rng.gauss(0, 1) for _ in range(DIM)]

        cell = engine.ivf_insert("ivftest_idx", new_id, new_vec)
        assert 0 <= cell < NLIST

        assert engine.ivf_info("ivftest_idx")["indexed"] == baseline + 1
        hits = engine.ivf_search("ivftest_idx", new_vec, k=5, nprobe=NLIST)
        assert new_id in [h[0] for h in hits]

        engine.ivf_delete("ivftest_idx", new_id)

    def test_delete_removes_from_search(self, engine, ivf_index):
        baseline = ivf_index["indexed"]
        rng = random.Random(13)
        new_id = "ivftest_DEL"
        new_vec = [rng.gauss(0, 1) for _ in range(DIM)]

        engine.ivf_insert("ivftest_idx", new_id, new_vec)
        assert engine.ivf_delete("ivftest_idx", new_id)
        assert engine.ivf_info("ivftest_idx")["indexed"] == baseline
        hits = engine.ivf_search("ivftest_idx", new_vec, k=5, nprobe=NLIST)
        assert new_id not in [h[0] for h in hits]

    def test_insert_nonexistent_index_raises(self, engine, ivf_index):
        with pytest.raises(ValueError, match="not found"):
            engine.ivf_insert("no_such_index", "x", [0.0] * DIM)

    def test_delete_nonexistent_node_returns_false(self, engine, ivf_index):
        assert not engine.ivf_delete("ivftest_idx", "definitely_not_there")

    def test_multiple_inserts_accumulate(self, engine, ivf_index):
        baseline = ivf_index["indexed"]
        rng = random.Random(21)

        ids = [f"ivftest_MULTI_{i}" for i in range(5)]
        for nid in ids:
            engine.ivf_insert("ivftest_idx", nid, [rng.gauss(0, 1) for _ in range(DIM)])

        assert engine.ivf_info("ivftest_idx")["indexed"] == baseline + 5

        for nid in ids:
            engine.ivf_delete("ivftest_idx", nid)

        assert engine.ivf_info("ivftest_idx")["indexed"] == baseline
