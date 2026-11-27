"""4.7.2: Graph.KG.Adjacency's class comment names every writer that goes on without a refused lock.

4.7.0's comment said a lock not taken in 5 seconds is an error status for every writer but "the row writers", though
`WriterLock` replaced the error in Put, PutShadow, Remove, PutKeyed, RemoveKeyed and RemoveSource as well (LedgerApply,
TemporalIndex and BenchFormat call those). The comment is read by whoever adds a writer, so it lists them.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Adjacency.cls"
CALL = "." + "." + "WriterLock("  # the ObjectScript call, `..WriterLock(`


def _class_comment_and_body():
    text = CLS.read_text()
    head, body = text.split("\nClass Graph.KG.Adjacency", 1)
    return head, body


def _methods_calling_writer_lock():
    _, body = _class_comment_and_body()
    names = set()
    for chunk in re.split(r"\n(?=ClassMethod )", body):
        m = re.match(r"ClassMethod (\w+)\(", chunk)
        if m and CALL in chunk:
            names.add(m.group(1))
    names.discard("WriterLock")
    return names


def test_the_writers_that_go_on_without_the_lock_are_the_ones_the_code_names():
    # the guard against a test that finds nothing: these are the writers 4.7.0 row 17 changed
    assert {"Put", "PutShadow", "Remove", "PutKeyed", "RemoveKeyed", "RemoveSource", "RowUpdate"} <= (
        _methods_calling_writer_lock()
    )


def test_the_class_comment_names_each_of_them():
    head, _ = _class_comment_and_body()
    paragraph = head.split("A lock that cannot be taken in 5 seconds", 1)[1].split("\n///\n", 1)[0]
    flat = re.sub(r"\s*\n///\s*", " ", paragraph)
    for name in sorted(_methods_calling_writer_lock()):
        assert re.search(rf"\b{name}\b", flat), f"the class comment does not name {name}: {flat}"
    assert "row writers" not in flat, "the vague name stays out: the comment lists the writers"
