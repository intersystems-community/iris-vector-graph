"""4.6.0 R314 (part B): one registry says what every CALL procedure yields, and it lists exactly the procedures IVG
answers.

The procedures live in three places: the translator's `_IVG_PROCEDURES` (each a CTE), the engine's
`_SYSTEM_PROCEDURES` (each a Python handler) and the gds.* shims (`GDS_SHIM_MAP`). Only db.* and dbms.* had a
signature (`dbms.procedures`); the value tests (test_460_r314_call_yield_values.py) are generated from this registry,
so a procedure missing from it has no value test. These tests fail when a registry gains a procedure this one lacks, or
this one names a procedure nothing answers; and when the registry's columns disagree with what the handlers answer,
what `dbms.procedures` says, or what the docs' tables list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph._engine.query import GDS_SHIM_MAP, GDS_YIELDS
from iris_vector_graph.cypher.procedure_signatures import PATH_KEYS, PROCEDURE_SIGNATURES, TYPES, signature
from iris_vector_graph.cypher.translator import _IVG_PROCEDURES
from iris_vector_graph.engine import IRISGraphEngine

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _answered() -> set:
    return {n.lower() for n in _IVG_PROCEDURES} | set(IRISGraphEngine._SYSTEM_PROCEDURES) | set(GDS_SHIM_MAP)


def test_every_answered_procedure_has_a_signature():
    missing = _answered() - {n.lower() for n in PROCEDURE_SIGNATURES}
    assert not missing, f"procedures with no signature in procedure_signatures.py: {sorted(missing)}"


def test_every_signature_names_an_answered_procedure():
    extra = {n.lower() for n in PROCEDURE_SIGNATURES} - _answered()
    assert not extra, f"signatures of procedures nothing answers: {sorted(extra)}"


def test_names_are_unique_whatever_the_case():
    assert len({n.lower() for n in PROCEDURE_SIGNATURES}) == len(PROCEDURE_SIGNATURES)


@pytest.mark.parametrize("name", sorted(PROCEDURE_SIGNATURES))
def test_each_column_has_a_known_type_and_a_unique_name(name):
    sig = PROCEDURE_SIGNATURES[name]
    assert sig, name
    assert {t for _, t in sig} <= TYPES, sig
    cols = [c for c, _ in sig]
    assert len(set(cols)) == len(cols), cols


def test_every_path_column_names_its_keys():
    paths = {(p, c) for p, sig in PROCEDURE_SIGNATURES.items() for c, t in sig if t == "PATH"}
    assert paths == set(PATH_KEYS)
    for keys in PATH_KEYS.values():
        assert set(keys.values()) <= TYPES


def test_weighted_path_keys_are_dijkstra_json_s():
    """By hand from Graph.KG.TraversalPaths: DijkstraJson's own answer (a node to itself) and DijkstraBacktrack's (every
    other path) are each PathJson's, which writes nodes, rels, costs, length and totalCost (4.6.3: as text, so a cost
    is a JSON float; tests/e2e/test_463_weighted_path_costs_e2e.py)."""
    src = (Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/TraversalPaths.cls").read_text()

    def body(method):
        return src.split(f"ClassMethod {method}(", 1)[1].split("\nClassMethod ", 1)[0]

    for method in ("DijkstraJson", "DijkstraBacktrack"):
        assert "..PathJson(" in body(method), method
    keys = re.findall(r'""(\w+)"":', body("PathJson"))
    assert keys == list(PATH_KEYS[("ivg.shortestPath.weighted", "path")]), keys


@pytest.mark.parametrize("name", sorted(GDS_YIELDS))
def test_the_gds_columns_are_the_ones_its_errors_name(name):
    assert [c for c, _ in signature(name)] == [c.strip() for c in GDS_YIELDS[name].split(",")]


@pytest.mark.parametrize("gds, ivg", sorted(GDS_SHIM_MAP.items()))
def test_a_shim_yields_its_ivg_procedure_s_columns(gds, ivg):
    assert signature(gds) == signature(ivg)


_SIG_TYPE = re.compile(r"(\w+) :: (\w+)")


def _dbms_procedures():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    return {row[0]: row[1] for row in IRISGraphEngine._proc_dbms_procedures(eng, None).rows}


@pytest.mark.parametrize("name", sorted(_dbms_procedures()))
def test_dbms_procedures_agrees_with_the_registry(name):
    """The output columns `dbms.procedures` reports (`name :: TYPE`, after the `::` that ends the arguments) are the
    registry's, every one, in its order (it listed 3 of dbms.procedures' 10 and showCurrentUser without `flags`)."""
    sig = _dbms_procedures()[name]
    outputs = sig.split(") :: (", 1)[1]
    assert _SIG_TYPE.findall(outputs) == list(signature(name)), (name, sig)


def _doc_yields(path: Path, heading: str) -> dict:
    text = path.read_text(encoding="utf-8").split(f"\n{heading}\n", 1)[1]
    rows = {}
    for line in text.splitlines():
        if not line.startswith("| `"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[cells[0].strip("`")] = re.findall(r"`([^`]+)`", cells[-1])
    return rows


@pytest.mark.parametrize("doc, heading", [("USER_GUIDE.md", "### Cypher procedures"),
                                          ("COMMUNITIES.md", "## Cypher procedures")])
def test_the_docs_tables_list_each_procedure_s_columns(doc, heading):
    table = _doc_yields(DOCS / doc, heading)
    assert table
    for proc, yields in table.items():
        assert yields == [c for c, _ in signature(proc)], (proc, yields)
