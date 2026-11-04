"""4.6.0 R195: the `^NKG` centrality writers emit JSON numbers.

ObjectScript writes a number below 1 without its leading zero (`.5773502691896258`), which no JSON parser reads.
`EigenvectorGlobal` wrote its scores so, `json.loads` refused the answer, and `eigenvector_centrality` fell back to
its Python walk on every call; the parallel betweenness writers wrote a sampled score the same way. Every score a
writer in `NKGAccelCentrality.cls` puts in its JSON goes through `TraversalBFS.jsonNum`, or is normalised to a
leading zero on the lines before it (the closeness writers). The live answer is in
tests/e2e/test_460_r195_eigenvector_json_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "NKGAccelCentrality.cls"


def test_every_score_writer_emits_a_json_number():
    lines = SRC.read_text().splitlines()
    writers = [i for i, line in enumerate(lines) if '""score"":"_' in line]
    assert len(writers) >= 5, writers
    raw = []
    for i in writers:
        value = re.search(r'""score"":"_(.*?)_"\}"', lines[i]).group(1)
        normalised = any(f'If $Extract({value})="." Set {value} = "0"_{value}' in lines[j] for j in range(i - 3, i))
        assigned = any(re.search(rf"Set {re.escape(value)} = .*jsonNum\(", lines[j]) for j in range(i - 3, i))
        if not ("jsonNum(" in value or normalised or assigned):
            raw.append((i + 1, lines[i].strip()))
    assert raw == [], raw


def test_eigenvector_global_escapes_the_node_id():
    body = SRC.read_text().split("ClassMethod EigenvectorGlobal", 1)[1].split("ClassMethod ", 1)[0]
    assert "jsonEsc(nid2)" in body
