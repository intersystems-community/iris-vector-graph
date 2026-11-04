"""4.6.0 R211: a RETURN after a write that removes the key its MATCH filters on answers the matched node (TCK Set5 [4]).

`MATCH (n:X {name: 'A'}) SET n += {name: null} RETURN n` removed `name` and answered no row: the RETURN reads the
MATCH again, and its join on the `name` row, which is gone, dropped the node. `REMOVE n.name` the same. The writes
already read the matched ids once (R186's capture); now the RETURN does too: it keeps the node through the capture
(`n0.node_id IN (__IDSALL_<key>__)`, every id inlined, the RETURN is one statement) and drops the MATCH's join and
tests on the removed key.

These pin the statements; tests/e2e/test_460_r211_set_null_in_map_e2e.py checks the stored graph and the answers.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")


def _stmts(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


@pytest.mark.parametrize(
    "write",
    ["SET n += {name: null}", "REMOVE n.name", "SET n += {name: null, name2: 'Z'}", "SET n.k = 1 REMOVE n.name"],
)
def test_the_return_reads_the_captured_node_not_the_removed_key(write):
    stmts = _stmts(f"MATCH (n:X {{name: 'A'}}) {write} RETURN n")
    caps = [s.split("\n", 1)[0].split()[1] for s, _ in stmts if s.startswith("__capture_ids__ ")]
    assert len(caps) == 1, stmts
    final, params = stmts[-1]
    assert final.startswith("SELECT "), final
    assert f"n0.node_id IN (__IDSALL_{caps[0]}__)" in final, final
    # no join or test on `name` is left
    assert "'name'" not in final and "name" not in [p for p in params], (final, params)
    assert final.count("?") == len(params), (final, params)
    assert params == ["X"], params  # the label join only


@pytest.mark.parametrize(
    "q",
    [
        # the removed key is not one the MATCH filters on
        "MATCH (n:X {name: 'A'}) SET n += {name2: null} RETURN n",
        # a key changed, not removed: the RETURN is as before
        "MATCH (n:X {name: 'A'}) SET n.name = 'B' RETURN n",
        "MATCH (n:X) REMOVE n.name RETURN n",
    ],
)
def test_the_return_is_unchanged_when_no_removed_key_is_filtered(q):
    assert "__IDSALL_" not in _stmts(q)[-1][0]


def test_every_captured_id_is_inlined_in_one_statement():
    from iris_vector_graph.stores.iris_sql_store import _CAPTURED_ID_CHUNK, _expand_all_ids

    ids = [f"id{i}" for i in range(_CAPTURED_ID_CHUNK + 3)]
    out = _expand_all_ids("SELECT 1 FROM nodes n0 WHERE n0.node_id IN (__IDSALL_w0__)", {"w0": ids})
    assert out.count("'id") == len(ids) and "__IDSALL_" not in out, out[:200]
    assert _expand_all_ids("SELECT n0.node_id IN (__IDSALL_w0__)", {"w0": []}).endswith("IN (NULL)")
    with pytest.raises(RuntimeError, match="w9"):
        _expand_all_ids("x IN (__IDSALL_w9__)", {})
