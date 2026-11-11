"""4.6.0 R312, live: setup on a namespace whose IVG Schema is IVGALT tunes IVGALT's tables, and reports them.

Run against `IVG_NAMESPACE` (an IVG460* scratch namespace recording IVGALT, e.g. IVG460FXGALT). On the release head the
status's `tuned` named `Graph_KG.*`, tables that namespace does not have.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


def test_setup_tunes_the_namespaces_own_schema():
    import iris

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    conn = alt.connect()
    try:
        schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema")
        if not schema or schema.upper() == "GRAPH_KG":
            pytest.skip(f"{alt.NS} records {schema!r}; this case needs a non-default IVG Schema")
        engine = IRISGraphEngine(conn, schema_prefix=schema, namespace=alt.NS, embedding_dimension=alt.DIM)
        tuned = engine.initialize_schema()["tuned"]
        assert tuned, tuned
        assert all(name.split(".")[0] == schema for name in tuned), tuned
        core = {f"{schema}.{t}" for t in ("nodes", "rdf_edges", "rdf_labels", "rdf_props")}
        assert core <= set(tuned) and all(tuned[name] for name in core), tuned
        assert set(tuned) == {f"{schema}.{t}" for t in GraphSchema.TUNABLE_TABLES}
    finally:
        conn.close()
