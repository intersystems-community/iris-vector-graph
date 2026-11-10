"""The column widths a batch writer checks before it sends anything (4.7.0 row 14).

IRIS refuses a value wider than its column (SQLCODE -104, `Field validation failed in INSERT`). The driver's
`executemany` raises that refusal when the statement has one row, and skips the refused row without raising when it
has more, so a batch writer answered success without the row: `bulk_create_nodes` returned every id with a label
missing, `create_node` returned True without its long label. The writers check these widths first and raise
`ValueError` naming the row, with nothing written. Widths are characters, as `VARCHAR(n)` counts them.
"""

from __future__ import annotations

from typing import Any

#: rdf_labels.label, rdf_props.key, rdf_edges.p (iris_vector_graph/schema.py)
LABEL_WIDTH = 128
KEY_WIDTH = 128
PREDICATE_WIDTH = 128
#: nodes.node_id, and rdf_edges.s / o_id
NODE_ID_WIDTH = 256
#: rdf_props.val (4.7.0 row 3d: BulkLoader cut every value to 60000 characters without a word)
VALUE_WIDTH = 64000


def check_width(writer: str, what: str, value: Any, width: int, where: str = "") -> None:
    """ValueError when ``value``'s text is wider than ``width``: ``<writer>: <where><what> '<start>…' is N characters;
    the column holds <width>. Nothing was written``."""
    text = str(value)
    if len(text) > width:
        raise ValueError(
            f"{writer}: {where}{what} {text[:32]!r}... is {len(text)} characters; the column holds {width}. "
            "Nothing was written"
        )
