"""A stale `^NKG` index must not make the lazy-resolution suite assert on absent data.

`tests/e2e/test_lazy_node_resolution.py` is written against one specific load: a
graph whose node ids are `node_<n>`, ingested with `BulkIngestEdges` and indexed
with `BuildNKG`. Its `m_seed` fixture asked `^NKG` for a seed and skipped only when
the answer was empty or `"0"`.

`^NKG` outlives the rows it indexes. On the enterprise container the namespace held
`Graph_KG.rdf_edges` = 0 rows and no `node_%` node at all, while
`GetFirstNKGNode` still answered `'SGS_eefd73_A'` — a leftover from some other
suite's load. So the fixture produced a seed, every test ran, and six of them
failed on data that was never there:

    SC-003: result o='SGS_eefd73_B' must be a node name string, not integer index
    assert len(r) > 0

None of those failures says anything about lazy node resolution, which is what the
file measures. A seed that indexes nothing is a missing dataset, and the honest
answer is a skip that names what is missing.
"""

from tests.e2e.test_lazy_node_resolution import why_the_benchmark_dataset_is_missing


def test_the_loaded_dataset_is_not_missing():
    assert why_the_benchmark_dataset_is_missing("node_1", edge_count=4096) is None


def test_no_seed_at_all_is_missing():
    reason = why_the_benchmark_dataset_is_missing("", edge_count=0)
    assert reason and "BuildNKG" in reason


def test_the_zero_seed_is_missing():
    # `GetFirstNKGNode` answers "0" when ^NKG has no first node.
    assert why_the_benchmark_dataset_is_missing("0", edge_count=4096) is not None


def test_a_seed_with_no_edges_behind_it_is_a_stale_index():
    # The exact state the gate ran in: ^NKG answers, the rows are gone.
    reason = why_the_benchmark_dataset_is_missing("SGS_eefd73_A", edge_count=0)
    assert reason and "stale" in reason.lower()
    assert "SGS_eefd73_A" in reason, "the skip has to name the seed it found"


def test_another_suites_dataset_is_not_this_suites_dataset():
    # 4096 edges of *something else*: the assertions here read `node_` ids, so a
    # differently-named load cannot satisfy them however healthy it is.
    reason = why_the_benchmark_dataset_is_missing("SGS_eefd73_A", edge_count=4096)
    assert reason and "node_" in reason
