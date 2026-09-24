"""Spec 231 FR-013, FR-014, US3: concept resolution and the concept -> PPR pipeline.

Resolution and expansion run in Graph.KG.FHIRGraph; Python validates, calls, and
wires the three steps together. The live behaviour and latency (SC-005) are in
tests/e2e/test_231_fhir_concept_pipeline_e2e.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"
CG = "umls"


@pytest.fixture
def eng():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    native.classMethodValue.return_value = json.dumps({"status": "ok", "keys": [], "ids": []})
    with patch.object(engine, "_iris_obj", return_value=native):
        yield engine, native


def _args(native):
    return native.classMethodValue.call_args.args


class TestResolve:
    def test_defaults(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps(
            {"status": "ok", "keys": ["Condition/c1", "Observation/o1"]}
        )
        out = engine.fhir_resolve_concepts(G, CG, ["C0011849"])
        assert out == ["Condition/c1", "Observation/o1"]
        assert _args(native) == (
            "Graph.KG.FHIRGraph",
            "ResolveConcepts",
            G,
            CG,
            '["C0011849"]',
            "",
            "",
        )

    def test_params_and_relations_are_passed(self, eng):
        engine, native = eng
        engine.fhir_resolve_concepts(
            G, CG, ["C1"], params=["code", "category"], relations=["exact", "broader"]
        )
        args = _args(native)
        assert json.loads(args[5]) == ["code", "category"]
        assert json.loads(args[6]) == ["exact", "broader"]

    def test_default_concept_graph_is_the_default_graph(self, eng):
        engine, native = eng
        engine.fhir_resolve_concepts(G, None, ["C1"])
        assert _args(native)[3] == ""

    def test_no_ids_is_no_round_trip(self, eng):
        engine, native = eng
        assert engine.fhir_resolve_concepts(G, CG, []) == []
        native.classMethodValue.assert_not_called()

    @pytest.mark.parametrize("bad", [["sibling"], ["EXACT"], [3]])
    def test_relation_is_checked(self, eng, bad):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_resolve_concepts(G, CG, ["C1"], relations=bad)
        native.classMethodValue.assert_not_called()

    @pytest.mark.parametrize("bad", [["co de"], ["code;"], [""], [None]])
    def test_param_is_a_search_param_name(self, eng, bad):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_resolve_concepts(G, CG, ["C1"], params=bad)
        native.classMethodValue.assert_not_called()

    @pytest.mark.parametrize("bad", [None, "", "0"])
    def test_fhir_graph_is_required(self, eng, bad):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_resolve_concepts(bad, CG, ["C1"])

    def test_ids_are_strings(self, eng):
        engine, _ = eng
        with pytest.raises(ValueError):
            engine.fhir_resolve_concepts(G, CG, ["C1", 2])


class TestExpand:
    def test_call(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps({"status": "ok", "ids": ["C1", "C2"]})
        assert engine.fhir_expand_concepts(CG, ["C1"], predicates=["broader"], hops=2) == ["C1", "C2"]
        assert _args(native) == (
            "Graph.KG.FHIRGraph",
            "ExpandConcepts",
            CG,
            '["C1"]',
            '["broader"]',
            2,
        )

    def test_all_predicates_by_default(self, eng):
        engine, native = eng
        engine.fhir_expand_concepts(CG, ["C1"])
        assert _args(native)[4] == ""
        assert _args(native)[5] == 1

    @pytest.mark.parametrize("bad", [-1, 11, 1.5, "1"])
    def test_hops_bounded(self, eng, bad):
        engine, _ = eng
        with pytest.raises(ValueError):
            engine.fhir_expand_concepts(CG, ["C1"], hops=bad)


class TestPipeline:
    def test_expand_then_resolve_then_graph_scoped_bidirectional_ppr(self, eng):
        engine, _ = eng
        calls = []
        with (
            patch.object(
                engine,
                "fhir_expand_concepts",
                side_effect=lambda *a, **k: calls.append(("expand", a, k)) or ["C1", "C2"],
            ),
            patch.object(
                engine,
                "fhir_resolve_concepts",
                side_effect=lambda *a, **k: calls.append(("resolve", a, k)) or ["Condition/c1"],
            ),
            patch.object(
                engine,
                "kg_PERSONALIZED_PAGERANK",
                side_effect=lambda *a, **k: calls.append(("ppr", a, k)) or {"Patient/p1": 0.4},
            ),
        ):
            out = engine.fhir_concept_ppr(G, CG, ["C1"], hops=1, top_k=10)
        assert out == {"Patient/p1": 0.4}
        assert [c[0] for c in calls] == ["expand", "resolve", "ppr"]
        resolve = calls[1]
        assert resolve[1][:3] == (G, CG, ["C1", "C2"])
        ppr = calls[2]
        assert ppr[1][0] == ["Condition/c1"]
        assert ppr[2]["graph"] == G
        assert ppr[2]["bidirectional"] is True
        assert ppr[2]["return_top_k"] == 10
        assert ppr[2]["max_iterations"] == 20

    def test_zero_hops_skips_expansion(self, eng):
        engine, _ = eng
        with (
            patch.object(engine, "fhir_expand_concepts") as expand,
            patch.object(engine, "fhir_resolve_concepts", return_value=["Condition/c1"]),
            patch.object(engine, "kg_PERSONALIZED_PAGERANK", return_value={}),
        ):
            engine.fhir_concept_ppr(G, CG, ["C1"], hops=0)
        expand.assert_not_called()

    def test_no_seeds_is_empty_without_ppr(self, eng):
        engine, _ = eng
        with (
            patch.object(engine, "fhir_expand_concepts", return_value=["C1"]),
            patch.object(engine, "fhir_resolve_concepts", return_value=[]),
            patch.object(engine, "kg_PERSONALIZED_PAGERANK") as ppr,
        ):
            assert engine.fhir_concept_ppr(G, CG, ["C1"]) == {}
        ppr.assert_not_called()


class TestCrosswalkAdd:
    def test_upserts_one_row(self, eng):
        engine, _ = eng
        cur = engine.conn.cursor.return_value
        engine.code_crosswalk_add(
            "http://hl7.org/fhir/sid/icd-10-cm", "E11.9", "MESH:D003924", target_graph="mesh",
            relation="broader", source="umls", source_version="2026AA", confidence=0.9,
        )
        sql, params = cur.execute.call_args.args
        assert sql.startswith("INSERT OR UPDATE INTO Graph_KG.code_crosswalk")
        assert params == [
            "http://hl7.org/fhir/sid/icd-10-cm", "E11.9", "mesh", "MESH:D003924",
            "broader", "umls", "2026AA", 0.9,
        ]

    def test_default_graph_and_relation(self, eng):
        engine, _ = eng
        cur = engine.conn.cursor.return_value
        engine.code_crosswalk_add("http://loinc.org", "4548-4", "HbA1c")
        params = cur.execute.call_args.args[1]
        assert params[2] == "" and params[4] == "exact"

    @pytest.mark.parametrize("field", ["code_system_uri", "code", "target_node_id"])
    def test_required_fields(self, eng, field):
        engine, _ = eng
        kw = {"code_system_uri": "http://loinc.org", "code": "1", "target_node_id": "n"}
        kw[field] = ""
        with pytest.raises(ValueError):
            engine.code_crosswalk_add(**kw)

    def test_relation_checked(self, eng):
        engine, _ = eng
        with pytest.raises(ValueError):
            engine.code_crosswalk_add("http://loinc.org", "1", "n", relation="same")
