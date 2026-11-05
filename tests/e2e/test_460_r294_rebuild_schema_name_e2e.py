"""4.6.0 R294, live: `rebuild_edge_indices(pSchema)` takes a schema name, not SQL text.

`Graph.KG.Ledger.RebuildEdgeIndices` pasted `pSchema` into `BUILD INDEX FOR TABLE <pSchema>.<table>` for each of
nodes, rdf_labels, rdf_props and rdf_edges. So `'<schema>.nodes --'` ran `BUILD INDEX FOR TABLE <schema>.nodes
--.nodes` four times: the caller's text was SQL. The schema argument is now an SQL identifier: it starts with a letter
and holds only letters, digits and underscores, like an IVG Schema name (`schema_name.validate_schema_name`). Anything
else is refused before any statement runs, with an error that names the argument.

By hand: the IVG Schema's own name rebuilds the four tables and answers 4. `'<schema>.nodes --'`, `''`, and
`'<schema>; x'` are each refused, naming what was given.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); nothing is written but the
indexes rebuilt.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


@pytest.fixture(scope="module")
def call():
    import iris

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"

    def run(arg: str):
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT {schema}.rebuild_edge_indices(?)", [arg])
            return "ok", cur.fetchall()[0][0]
        except Exception as exc:  # the refusal is the answer under test
            return "error", str(exc)
        finally:
            cur.close()

    yield run, schema
    conn.close()


def test_the_schema_name_rebuilds_the_four_tables(call):
    run, schema = call
    assert run(schema) == ("ok", 4)


@pytest.mark.parametrize("tail", [".nodes --", "; x", ".nodes /*"])
def test_sql_text_in_the_schema_argument_is_refused(call, tail):
    run, schema = call
    kind, text = run(schema + tail)
    assert kind == "error", (tail, text)
    assert "not an SQL identifier" in text and (schema + tail) in text, text


def test_an_empty_schema_is_refused(call):
    run, _ = call
    kind, text = run("")
    assert kind == "error" and "not an SQL identifier" in text, text
