"""Spec 233 US2: the ontology helpers in tests/e2e/genomics_fixture.py. No IRIS."""

from __future__ import annotations

import os

import pytest

from tests.e2e import genomics_fixture as gf

rdflib = pytest.importorskip("rdflib")

HGNC = "http://www.genenames.org"
HGVS = "http://varnomen.hgvs.org"
CLINVAR = "http://www.ncbi.nlm.nih.gov/clinvar"
SO = "http://www.sequenceontology.org"
MONDO = "http://purl.obolibrary.org/obo/mondo.owl"
REFSEQ = "http://www.ncbi.nlm.nih.gov/refseq"

OBO = "http://purl.obolibrary.org/obo/"
GENE = "http://identifiers.org/hgnc/"
SUB = str(rdflib.RDFS.subClassOf)
LABEL = str(rdflib.RDFS.label)
GAWC = "https://w3id.org/biolink/vocab/gene_associated_with_condition"
ISVO = "https://w3id.org/biolink/vocab/is_sequence_variant_of"


def _cc(*codings):
    return {"coding": [dict(zip(("system", "code", "display"), c)) for c in codings]}


def _obs(oid, *components, value=None, subject="Patient/p"):
    r = {
        "resourceType": "Observation",
        "id": oid,
        "subject": {"reference": subject},
        "component": [{"valueCodeableConcept": _cc(*c)} for c in components],
    }
    if value:
        r["valueCodeableConcept"] = _cc(*value)
    return r


class TestCodeIri:
    @pytest.mark.parametrize(
        "system, code, iri",
        [
            (HGNC, "HGNC:2621", GENE + "2621"),
            (SO, "SO:0001583", OBO + "SO_0001583"),
            (SO, "SO_0002054", OBO + "SO_0002054"),
            (MONDO, "MONDO:0012624", OBO + "MONDO_0012624"),
            (CLINVAR, "13961", "http://identifiers.org/clinvar:13961"),
            (CLINVAR, "RCV000664188.1", "http://identifiers.org/clinvar:RCV000664188.1"),
            (HGVS, "NM_000059.3:c.8167G>C", "urn:ivg233:hgvs:NM_000059.3%3Ac.8167G%3EC"),
            (REFSEQ, "NM_001184.4", None),
            (HGNC, None, None),
            (HGNC, "", None),
        ],
    )
    def test_code_iri(self, system, code, iri):
        assert gf.code_iri(system, code) == iri

    def test_clinvar_is_not_the_naive_biolink_expansion(self):
        assert gf.code_iri(CLINVAR, "13961") != "http://identifiers.org/clinvar13961"


class TestCrosswalkRows:
    def test_code_wins_over_display_and_codeless_unmapped(self):
        res = [
            _obs("a", [(HGNC, "HGNC:262", "CYP2C19")], [(HGNC, None, "JAK2")]),
            _obs("b", [(SO, "SO_0002054", "loss_of_function_variant")], [(REFSEQ, "NM_1.1", None)]),
            _obs("c", [(HGNC, "HGNC:262", "CYP2C19")], value=[(CLINVAR, "13961", None)]),
        ]
        rows, counts = gf.crosswalk_rows(res)
        assert rows == [
            (HGNC, "HGNC:262", GENE + "262"),
            (CLINVAR, "13961", "http://identifiers.org/clinvar:13961"),
            (SO, "SO_0002054", OBO + "SO_0002054"),
        ]
        assert counts == {"normalized": 1, "clean": 2, "unmapped": 1}

    def test_rows_sorted_and_distinct(self):
        res = [_obs("a", [(HGNC, "HGNC:2", None)], [(HGNC, "HGNC:1", None)]), _obs("b", [(HGNC, "HGNC:1", None)])]
        rows, _ = gf.crosswalk_rows(res)
        assert rows == [(HGNC, "HGNC:1", GENE + "1"), (HGNC, "HGNC:2", GENE + "2")]


_SO_TTL = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix obo: <http://purl.obolibrary.org/obo/> .
obo:SO_0001589 rdfs:subClassOf obo:SO_0001818 ; rdfs:label "frameshift_variant" .
obo:SO_0001818 rdfs:subClassOf obo:SO_0001060 ; rdfs:label "protein_altering_variant" .
obo:SO_0001060 rdfs:subClassOf obo:SO_0000110 ; rdfs:label "sequence_variant" .
obo:SO_0009999 rdfs:subClassOf obo:SO_0001818 ; rdfs:label "unused" .
"""

_MONDO_TTL = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix obo: <http://purl.obolibrary.org/obo/> .
@prefix hgnc: <http://identifiers.org/hgnc/> .
obo:MONDO_0012624 rdfs:subClassOf obo:MONDO_0017713 , obo:MONDO_0000001 ,
    [ a owl:Restriction ; owl:onProperty obo:RO_0004003 ; owl:someValuesFrom hgnc:21497 ] ;
    rdfs:label "acyl-CoA dehydrogenase 9 deficiency" .
obo:MONDO_0017713 rdfs:subClassOf obo:MONDO_0019052 ; rdfs:label "fatty acid oxidation disorder" .
obo:MONDO_0019052 rdfs:subClassOf obo:MONDO_0000001 ; rdfs:label "inborn errors of metabolism" .
obo:MONDO_0000001 rdfs:label "disease" .
obo:MONDO_0005555 rdfs:subClassOf obo:MONDO_0000001 ,
    [ a owl:Restriction ; owl:onProperty obo:RO_0004003 ; owl:someValuesFrom hgnc:1100 ] .
obo:MONDO_0006666 rdfs:subClassOf obo:MONDO_0019052 ,
    [ a owl:Restriction ; owl:onProperty obo:RO_0004003 ; owl:someValuesFrom hgnc:21497 ] ;
    owl:deprecated true .
hgnc:21497 rdfs:label "ACAD9" .
"""


def _graph(ttl):
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    return g


_RES = [
    _obs(
        "v1",
        [(HGNC, "HGNC:21497", "ACAD9")],
        [(HGVS, "NM_014049.4:c.1A>G", None)],
        [(SO, "SO:0001589", "frameshift_variant")],
    ),
    _obs("v2", value=[(CLINVAR, "13961", None)], subject="Patient/q"),
    _obs("d", [(MONDO, "MONDO:0012624", None)]),
]


@pytest.fixture(scope="module")
def triples():
    return gf.ontology_triples(_graph(_SO_TTL), _graph(_MONDO_TTL), _RES)


def _str(triples):
    return {(str(s), str(p), str(o)) for s, p, o in triples}


class TestOntologyTriples:
    def test_named_ancestor_closure_minus_stop(self, triples):
        sub = {(s, o) for s, p, o in _str(triples) if p == SUB}
        assert sub == {
            (OBO + "SO_0001589", OBO + "SO_0001818"),
            (OBO + "MONDO_0012624", OBO + "MONDO_0017713"),
            (OBO + "MONDO_0017713", OBO + "MONDO_0019052"),
        }

    def test_no_blank_nodes_or_stop_listed_iris(self, triples):
        nodes = {t for s, _, o in triples for t in (s, o) if not isinstance(t, rdflib.Literal)}
        assert not [n for n in nodes if isinstance(n, rdflib.BNode)]
        assert not {str(n) for n in nodes} & set(gf.STOP)

    def test_gene_association_from_ro_restriction(self, triples):
        gawc = {(s, o) for s, p, o in _str(triples) if p == GAWC}
        assert gawc == {(GENE + "21497", OBO + "MONDO_0012624")}

    def test_variant_of_gene_from_one_observation(self, triples):
        isvo = {(s, o) for s, p, o in _str(triples) if p == ISVO}
        assert isvo == {(gf.code_iri(HGVS, "NM_014049.4:c.1A>G"), GENE + "21497")}

    def test_labels_are_literals(self, triples):
        labels = {(str(s), str(o)) for s, p, o in triples if str(p) == LABEL}
        assert (OBO + "MONDO_0019052", "inborn errors of metabolism") in labels
        assert (GENE + "21497", "ACAD9") in labels
        assert all(isinstance(o, rdflib.Literal) for _, p, o in triples if str(p) == LABEL)

    def test_writer_is_sorted_ntriples(self, triples):
        text = gf.ntriples(triples)
        lines = text.splitlines()
        assert lines == sorted(lines)
        assert len(lines) == len(triples)
        assert all(line.endswith(" .") for line in lines)
        assert _str(rdflib.Graph().parse(data=text, format="nt")) == _str(triples)


_CHAIN = [("c", SUB, "b"), ("b", SUB, "a"), ("g", GAWC, "c"), ("v", ISVO, "g")]


class TestExpand:
    def test_out_and_in(self):
        assert gf.expand(_CHAIN, ["c"], 1) == {"c", "b"}
        assert gf.expand(_CHAIN, ["c"], 2, "out") == {"c", "b", "a"}
        assert gf.expand(_CHAIN, ["a"], 4, "in") == {"a", "b", "c", "g", "v"}
        assert gf.expand(_CHAIN, ["a"], 2, "in") == {"a", "b", "c"}

    def test_both_and_hops_zero(self):
        assert gf.expand(_CHAIN, ["c"], 1, "both") == {"c", "b", "g"}
        assert gf.expand(_CHAIN, ["c"], 0, "in") == {"c"}

    def test_bad_direction(self):
        with pytest.raises(ValueError):
            gf.expand(_CHAIN, ["c"], 1, "up")


class TestExpected:
    def test_component_and_value_paths(self):
        assert gf.expected(_RES, {GENE + "21497"}) == {"Observation/v1"}
        assert gf.expected(_RES, {"http://identifiers.org/clinvar:13961"}) == {"Observation/v2"}
        assert gf.expected(_RES, {OBO + "MONDO_0012624", GENE + "1100"}) == {"Observation/d"}

    def test_only_observations(self):
        report = {"resourceType": "DiagnosticReport", "id": "r", "code": _cc((HGNC, "HGNC:21497", None))}
        assert gf.expected([*_RES, report], {GENE + "21497"}) == {"Observation/v1"}

    def test_patients_of_follows_subject_to_patient(self):
        dev = _obs("x", [(HGNC, "HGNC:21497", None)], subject="Device/d")
        assert gf.patients_of([*_RES, dev], {"Observation/v1", "Observation/v2", "Observation/x"}) == {
            "Patient/p",
            "Patient/q",
        }


@pytest.fixture(scope="module")
def vendored():
    if not os.path.exists(gf.ONTOLOGY):
        pytest.fail(f"{gf.ONTOLOGY} missing: run `python -m tests.e2e.genomics_fixture vendor-ontology`")
    return gf.load_ontology()


@pytest.fixture(scope="module")
def resources():
    return gf.load_fixture()


class TestVendoredOntology:
    def test_no_stop_listed_iri(self, vendored):
        nodes = {t for s, _, o in vendored for t in (s, o)}
        assert not nodes & set(gf.STOP)

    def test_every_fixture_so_and_mondo_code_is_a_node(self, vendored, resources):
        nodes = {s for s, _, _ in vendored} | {o for _, _, o in vendored}
        rows, _ = gf.crosswalk_rows(resources)
        codes = {iri for system, _, iri in rows if system in (SO, MONDO)}
        assert codes and codes <= nodes

    def test_seeds_are_nodes_except_the_absent_gene(self, vendored):
        nodes = {s for s, _, _ in vendored} | {o for _, _, o in vendored}
        present = {iri for iri, _, _ in gf.SEEDS} - {GENE + "1100"}
        assert present <= nodes
        assert GENE + "1100" not in nodes

    def test_seed_oracles(self, vendored, resources):
        edges = [t for t in vendored if t[1] != LABEL]
        got = {iri: gf.expected(resources, gf.expand(edges, [iri], hops, d)) for iri, hops, d in gf.SEEDS}
        assert got[OBO + "MONDO_0019052"], "the disease seed reaches no Observation"
        assert got[OBO + "SO_0001818"], "the SO seed reaches no Observation"
        assert got[GENE + "1100"] == set()
        cyp = got[GENE + "2621"]
        assert cyp
        assert not cyp & gf.expected(resources, {GENE + "262"})


_OWL_XML = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
    <owl:ObjectProperty rdf:about="http://purl.obolibrary.org/obo/RO_0004003">
        <rdfs:label>has material basis in germline mutation in</rdfs:label>
    </owl:ObjectProperty>
    <owl:Class rdf:about="http://purl.obolibrary.org/obo/MONDO_0012624">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/MONDO_0017713"/>
        <rdfs:subClassOf rdf:nodeID="genid1"/>
        <rdfs:subClassOf>
            <owl:Restriction>
                <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0004003"/>
                <owl:someValuesFrom rdf:resource="http://identifiers.org/hgnc/1"/>
            </owl:Restriction>
        </rdfs:subClassOf>
        <rdfs:subClassOf rdf:nodeID="genid2"/>
        <rdfs:label>acyl-CoA dehydrogenase 9 deficiency</rdfs:label>
    </owl:Class>
    <owl:Restriction rdf:nodeID="genid1">
        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0004003"/>
        <owl:someValuesFrom rdf:resource="http://identifiers.org/hgnc/21497"/>
    </owl:Restriction>
    <owl:Restriction rdf:nodeID="genid2">
        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0002200"/>
        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/HP_1"/>
    </owl:Restriction>
    <owl:Class rdf:about="http://purl.obolibrary.org/obo/MONDO_0006666">
        <owl:deprecated rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">true</owl:deprecated>
    </owl:Class>
</rdf:RDF>
"""


class TestLoadOwl:
    @pytest.fixture
    def g(self, tmp_path):
        path = tmp_path / "x.owl"
        path.write_text(_OWL_XML)
        return gf.load_owl(str(path))

    def test_named_parent_label_and_deprecated(self, g):
        c = rdflib.URIRef(OBO + "MONDO_0012624")
        assert rdflib.URIRef(OBO + "MONDO_0017713") in set(g.objects(c, rdflib.RDFS.subClassOf))
        assert str(g.value(c, rdflib.RDFS.label)) == "acyl-CoA dehydrogenase 9 deficiency"
        assert (rdflib.URIRef(OBO + "MONDO_0006666"), rdflib.OWL.deprecated, rdflib.Literal(True)) in g

    def test_gene_restrictions_inline_and_by_node_id(self, g):
        genes = set()
        for r in g.subjects(rdflib.OWL.onProperty, rdflib.URIRef(gf.RO_GENE)):
            assert (rdflib.URIRef(OBO + "MONDO_0012624"), rdflib.RDFS.subClassOf, r) in g
            genes |= {str(o) for o in g.objects(r, rdflib.OWL.someValuesFrom)}
        assert genes == {GENE + "1", GENE + "21497"}

    def test_other_restrictions_dropped(self, g):
        assert not list(g.subjects(rdflib.OWL.onProperty, rdflib.URIRef(OBO + "RO_0002200")))
