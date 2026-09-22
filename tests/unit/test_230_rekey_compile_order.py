"""Spec 230 — the `^KG` rebuild cannot be ordered out of a class that did not compile.

Measured against a real 3.2.0 install upgraded by the working tree: `initialize_schema`
deploys the ObjectScript layer (step 6) *before* anything gives `rdf_labels` and
`rdf_props` their `graph_id` column — that happens in the 227 re-key, which runs later,
inside `upgrade_to_4_0_0`. So `Graph.KG.TraversalBuild` compiles against a table its
embedded SQL does not match:

    ERROR: Graph.KG.TraversalBuild.cls — Field 'GRAPH_ID' not found in the applicable
    tables^ DECLARE c1 CURSOR FOR SELECT s , label , graph_id FROM

and the rebuild step then fails with `ERROR #5123: Unable to find entry point for
method 'BuildKG' in routine 'Graph.KG.TraversalBuild.1'` — *after* the kill, which
would leave the install with neither the flat layout nor the scoped one.

Two things follow, and both are tested here: the classes are recompiled once the
columns exist, and the entry points are verified before a single `Kill`.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.migrations.kg_node_stores import REBUILD_PACKAGE, rekey_kg_node_stores
from tests.unit.migration_fakes_230 import kg_stores_conn

FLAT_RESIDUE = {
    ("prop", "t:n1", "name"): "Alice",
    ("label", "Person", "t:n1"): "",
    ("deg2p", "t:n1", "knows"): 2,
}


def _graph_rows():
    return {
        "props": [{"s": "t:n1", "key": "name", "val": "Alice", "graph_id": ""}],
        "labels": [{"s": "t:n1", "label": "Person", "graph_id": ""}],
        "edges": [{"s": "t:n1", "p": "knows", "o_id": "t:n2", "graph_id": ""}],
    }


class TestTheClassesAreRecompiledFirst:
    def test_the_package_is_recompiled_before_the_kill(self):
        conn = kg_stores_conn(**_graph_rows(), flat_entries=dict(FLAT_RESIDUE))
        rekey_kg_node_stores(conn)

        kinds = [event[0] for event in conn.registry.order]
        assert "compile" in kinds, conn.registry.order
        assert kinds.index("compile") < kinds.index("kill"), conn.registry.order
        assert conn.registry.compiles[0][0] == REBUILD_PACKAGE, conn.registry.compiles

    def test_a_recompile_that_restores_the_entry_point_lets_the_rekey_run(self):
        """The 3.2.0 case exactly: the method is missing until the columns exist."""
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            compiled_methods=(),
            compile_repairs=True,
        )
        report = rekey_kg_node_stores(conn)
        assert report.entries_dropped == len(FLAT_RESIDUE)
        assert report.entries_rebuilt["label"] == {"": 1}
        assert "BuildKG" in conn.registry.rebuilds

    def test_a_dry_run_recompiles_too(self):
        """Otherwise a pre-flight reports a failure the real run does not have.

        Compiling already-loaded source writes no rows and kills no entry, so it is
        not the kind of write `dry_run` promises to withhold.
        """
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            compiled_methods=(),
            compile_repairs=True,
        )
        report = rekey_kg_node_stores(conn, dry_run=True)
        assert report.entries_dropped == len(FLAT_RESIDUE)
        assert conn.registry.killed == []
        assert conn.registry.compiles, "a dry run must not report a gap a recompile closes"


class TestAMissingEntryPointRefusesBeforeTheKill:
    def test_the_refusal_names_the_class_and_the_method(self):
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            compiled_methods=(),
            compile_repairs=False,
        )
        before = conn.registry.snapshot()
        with pytest.raises(RuntimeError) as excinfo:
            rekey_kg_node_stores(conn)
        message = str(excinfo.value)
        assert "Graph.KG.TraversalBuild" in message
        assert "BuildKG" in message
        # The operator has to know the tree is still whole.
        assert "nothing has been dropped" in message.lower()
        assert conn.registry.killed == []
        assert conn.registry.snapshot() == before

    def test_a_dry_run_refuses_the_same_way(self):
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            compiled_methods=(),
            compile_repairs=False,
        )
        with pytest.raises(RuntimeError, match="BuildKG"):
            rekey_kg_node_stores(conn, dry_run=True)
        assert conn.registry.killed == []

    def test_one_missing_method_out_of_three_is_still_a_refusal(self):
        """`Build2HopExactStats` alone missing leaves `deg2p_exact` killed and empty."""
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            compiled_methods=(
                ("Graph.KG.TraversalBuild", "BuildKG"),
                ("Graph.KG.TraversalBuild", "Build2HopStats"),
            ),
            compile_repairs=False,
        )
        with pytest.raises(RuntimeError, match="Build2HopExactStats"):
            rekey_kg_node_stores(conn)
        assert conn.registry.killed == []


class TestARefusalNamesWhatIsActuallyWrong:
    """A table that is gone is not a table with a missing column.

    Both answer 0 rows from `INFORMATION_SCHEMA.COLUMNS`, and the measured 3.2.0
    upgrade hit the first case while being told the second: `Graph.KG.Edge`'s deletion
    had taken `Graph_KG.rdf_edges` with it, and the refusal read "has no graph_id
    column. Run initialize_schema()" — which the operator had just run. The cause and
    the fix are different, so the message has to be.
    """

    def test_an_absent_source_is_reported_as_absent(self):
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            absent_tables=("rdf_edges",),
        )
        with pytest.raises(RuntimeError) as excinfo:
            rekey_kg_node_stores(conn)
        message = str(excinfo.value)
        assert "rdf_edges" in message
        assert "does not exist" in message
        assert "no graph_id column" not in message
        assert conn.registry.killed == []

    def test_an_absent_source_still_says_nothing_was_dropped(self):
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            absent_tables=("rdf_edges",),
        )
        with pytest.raises(RuntimeError, match="nothing has been dropped"):
            rekey_kg_node_stores(conn)

    def test_a_present_but_unscoped_source_still_names_the_column(self):
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            scoped_columns=("rdf_props", "rdf_edges"),
        )
        with pytest.raises(RuntimeError, match="no graph_id column"):
            rekey_kg_node_stores(conn)


class TestTheColumnCheckStillComesFirst:
    def test_an_unscoped_source_refuses_before_any_compile(self):
        """No point recompiling a class against rows that are still pre-214."""
        conn = kg_stores_conn(
            **_graph_rows(),
            flat_entries=dict(FLAT_RESIDUE),
            scoped_columns=("rdf_props", "rdf_edges"),
        )
        with pytest.raises(RuntimeError, match="graph_id"):
            rekey_kg_node_stores(conn)
        assert conn.registry.compiles == []
        assert conn.registry.killed == []
