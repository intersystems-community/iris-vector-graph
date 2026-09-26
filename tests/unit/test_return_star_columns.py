"""RETURN * projects every variable in scope, in alphabetical column order (Unwind1 [13]).

Scalars used to be dropped when a node was also in scope, and an all-scalar scope
fell back to SELECT *, whose column order is the stage's, not the variables'."""
import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _final_select(q):
    s = translate_to_sql(parse_query(q), {}).sql
    s = s if isinstance(s, str) else "\n".join(s)
    return s[s.rindex("\nSELECT ") if "\nSELECT " in s else 0 :]


def _aliases(q):
    head = _final_select(q).split("\nFROM", 1)[0]
    return re.findall(r" AS (\w+)(?=,|$)", head)


def test_all_scalar_scope_is_alphabetical():
    q = (
        "WITH [1, 2] AS xs, [3, 4] AS ys, [5, 6] AS zs "
        "UNWIND xs AS x UNWIND ys AS y UNWIND zs AS z RETURN *"
    )
    assert _aliases(q) == ["x", "xs", "y", "ys", "z", "zs"]


def test_scalar_kept_beside_a_node():
    assert _aliases("MATCH (n) UNWIND [1] AS x RETURN *") == ["n_id", "n_labels", "n_props", "x"]


def test_scalar_sorts_before_a_node():
    assert _aliases("MATCH (n) UNWIND [1] AS a RETURN *") == ["a", "n_id", "n_labels", "n_props"]


def test_stage_scalar_beside_stage_node():
    assert _aliases("MATCH (n) WITH n, 1 AS b RETURN *") == ["b", "n_id", "n_labels", "n_props"]


def test_named_path_sorts_with_the_rest():
    cols = _aliases("MATCH p = (a)-[r:T]->(b) RETURN *")
    assert cols == ["a_id", "a_labels", "a_props", "b_id", "b_labels", "b_props", "p", "r_s", "r_p", "r_o_id"]
