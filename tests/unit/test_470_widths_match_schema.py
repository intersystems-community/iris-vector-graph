"""4.7.0 row 14: the widths the batch writers check (iris_vector_graph/_widths.py) are the ones setup's DDL declares.

Read from the CREATE TABLE text `GraphSchema.get_base_schema_sql()` sends, so a column widened there and not in
_widths (or the other way) fails here. tests/e2e/test_470_batch_widths_e2e.py compares them with a namespace's
INFORMATION_SCHEMA.COLUMNS.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph import _widths
from iris_vector_graph.schema import GraphSchema


def _declared(table: str, column: str) -> int:
    sql = GraphSchema.get_base_schema_sql()
    m = re.search(rf"CREATE TABLE(?: IF NOT EXISTS)? Graph_KG\.{table}\s*\((.*?)\n\)", sql, re.S)
    assert m, f"no CREATE TABLE Graph_KG.{table} in the base schema"
    col = re.search(rf"^\s*\"?{column}\"?\s+VARCHAR\((\d+)\)", m.group(1), re.M)
    assert col, f"Graph_KG.{table}.{column} is not a VARCHAR in the base schema"
    return int(col.group(1))


@pytest.mark.parametrize(
    "table, column, width",
    [
        ("rdf_labels", "label", _widths.LABEL_WIDTH),
        ("rdf_props", "key", _widths.KEY_WIDTH),
        ("rdf_edges", "p", _widths.PREDICATE_WIDTH),
        ("nodes", "node_id", _widths.NODE_ID_WIDTH),
        ("rdf_edges", "s", _widths.NODE_ID_WIDTH),
        ("rdf_edges", "o_id", _widths.NODE_ID_WIDTH),
    ],
)
def test_the_checked_width_is_the_declared_one(table, column, width):
    assert _declared(table, column) == width
