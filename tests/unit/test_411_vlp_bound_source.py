"""An ID-bound var-length path starts from the bound node and returns what RETURN asks.

Found by running `docs/setup/QUICKSTART.md` against a stock container (2026-09-30):

    MATCH (g {node_id:$id})-[*1..2]->(target) RETURN DISTINCT target.name LIMIT 10

returned `[]`. The translated statement binds the property key before the id
(`LEFT JOIN rdf_props p2 ON ... p2."key" = ?  WHERE n0.node_id = ?`, parameters
`['name', 'gene:BRCA1']`), and both source resolvers took the *first* string
parameter as the source, so BFS started from a node called "name". With the source
right, the ID-bound route still answered its own `(id, hops, pred)` rows whatever the
RETURN said, so `target.name` could not have come back anyway.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph._engine.query import extract_vlp_source_ids
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult

_NAMES = "MATCH (g {node_id:$id})-[*1..2]->(t) RETURN DISTINCT t.name LIMIT 10"
_IDS = "MATCH (g {node_id:$id})-[*1..2]->(t) RETURN DISTINCT t.node_id"


def _translate(cypher):
    return translate_to_sql(parse_query(cypher), {"id": "gene:BRCA1"})


class _Store:
    _schema_prefix = "Graph_KG"

    def __init__(self):
        self.conn = MagicMock()
        self.sources: list = []
        self.names = {"drug:Olaparib": "Olaparib", "disease:BRCA": "Breast cancer"}

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results, *, graph=None):
        self.sources.append(source_id)
        rows = [["drug:Olaparib", 1, "TARGETS"], ["disease:BRCA", 2, "TREATS"]]
        return IVGResult(
            columns=["id", "hops", "pred"], rows=rows if source_id == "gene:BRCA1" else []
        )

    def get_nodes(self, node_ids, prop_keys=None):
        rows = [[n, "[]"] + [self.names.get(n) for _ in (prop_keys or [])] for n in node_ids]
        return IVGResult(columns=["node_id", "labels"] + list(prop_keys or []), rows=rows)


def _engine(store):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store
    engine._nkg_dirty = False
    return engine


class TestTheSourceIsTheBoundParameter:
    def test_the_property_key_bound_first_is_not_the_source(self):
        sql_query = _translate(_NAMES)
        params = sql_query.parameters[0]
        assert params.index("gene:BRCA1") > 0, params  # the shape that broke it
        vl = sql_query.var_length_paths[0]
        ids = extract_vlp_source_ids(
            sql_query=sql_query,
            source_labels=[],
            source_alias=vl["source_alias"],
            target_alias=vl["target_alias"],
            store=MagicMock(),
        )
        assert ids == ["gene:BRCA1"], f"{ids}\n{sql_query.sql}"

    def test_a_question_mark_inside_a_literal_is_not_a_placeholder(self):
        from iris_vector_graph._engine.query import bound_node_id_param

        sql = "SELECT 'why?' AS q, x.val FROM t x WHERE x.k = ? AND n0.node_id = ?"
        assert bound_node_id_param(sql, ["k", "src"], "n0") == "src"

    def test_the_route_walks_from_the_bound_node(self):
        store = _Store()
        _engine(store)._route_var_length(_translate(_NAMES), {"id": "gene:BRCA1"})
        assert store.sources and set(store.sources) == {"gene:BRCA1"}, store.sources


class TestTheRouteAnswersTheReturn:
    def test_a_target_property_comes_back_as_that_property(self):
        store = _Store()
        result = _engine(store)._route_var_length(_translate(_NAMES), {"id": "gene:BRCA1"})
        assert result.rows == [["Olaparib"], ["Breast cancer"]], (result.columns, result.rows)
        assert result.columns == ["t.name"], result.columns

    def test_an_id_only_return_keeps_its_rows(self):
        store = _Store()
        result = _engine(store)._route_var_length(_translate(_IDS), {"id": "gene:BRCA1"})
        assert [r[0] for r in result.rows] == ["drug:Olaparib", "disease:BRCA"], result.rows

    def test_distinct_applies_to_the_projected_values(self):
        # Two nodes can share a name: BFS reports each node once, so DISTINCT has to
        # run over what RETURN projects, not over node ids (live: 'Breast cancer' x2).
        store = _Store()
        store.names["disease:BRCA2"] = "Breast cancer"
        rows = [["drug:Olaparib", 1, "T"], ["disease:BRCA", 2, "T"], ["disease:BRCA2", 2, "T"]]
        store.execute_bfs = lambda *a, **k: IVGResult(columns=["id", "hops", "pred"], rows=rows)
        result = _engine(store)._route_var_length(_translate(_NAMES), {"id": "gene:BRCA1"})
        assert result.rows == [["Olaparib"], ["Breast cancer"]], result.rows

    def test_without_distinct_every_node_keeps_its_row(self):
        store = _Store()
        store.names["disease:BRCA2"] = "Breast cancer"
        rows = [["drug:Olaparib", 1, "T"], ["disease:BRCA", 2, "T"], ["disease:BRCA2", 2, "T"]]
        store.execute_bfs = lambda *a, **k: IVGResult(columns=["id", "hops", "pred"], rows=rows)
        cypher = "MATCH (g {node_id:$id})-[*1..2]->(t) RETURN t.name"
        result = _engine(store)._route_var_length(_translate(cypher), {"id": "gene:BRCA1"})
        assert result.rows == [["Olaparib"], ["Breast cancer"], ["Breast cancer"]], result.rows
