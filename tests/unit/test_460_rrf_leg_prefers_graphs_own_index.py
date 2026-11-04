"""4.6.0 R119 (second part): `kg_RRF_FUSE`'s vector leg prefers the graph's own IVF index over the built-in `hnsw`.

`_fusion_leg_index` returned the first eligible entry, and `hnsw` returned at once wherever it stood in the registry,
so with the server-side route probed (`hnsw` registered when the engine starts) an IVF index built for the graph
afterwards was never read. Preference now: an index recorded for the graph; else `hnsw`; else an index whose graph
is unknown (with a warning).
"""

from iris_vector_graph._engine.vector import VectorMixin


class _Engine:
    def __init__(self, registry, scopes):
        self._index_registry = registry
        self._scopes = scopes

    def _index_graph(self, name, index_type):
        return self._scopes.get(name)


def _pick(registry, scopes, graph="G"):
    return VectorMixin._fusion_leg_index(_Engine(registry, scopes), ("ivf", "hnsw"), graph)


def test_the_graphs_own_ivf_wins_over_hnsw_registered_first():
    assert _pick({"hnsw": "hnsw", "g_ivf": "ivf"}, {"g_ivf": "G"}) == ("g_ivf", "ivf")


def test_the_graphs_own_ivf_wins_over_hnsw_registered_after():
    assert _pick({"g_ivf": "ivf", "hnsw": "hnsw"}, {"g_ivf": "G"}) == ("g_ivf", "ivf")


def test_hnsw_wins_over_another_graphs_ivf_and_an_unknown_one():
    assert _pick({"other": "ivf", "unknown": "ivf", "hnsw": "hnsw"}, {"other": "H"}) == ("hnsw", "hnsw")


def test_an_unknown_scope_ivf_is_the_last_resort():
    assert _pick({"other": "ivf", "unknown": "ivf"}, {"other": "H"}) == ("unknown", "ivf")
