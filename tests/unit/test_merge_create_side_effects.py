"""MERGE / CREATE writes only what openCypher says they write (TCK spec 229 side effects).

Each test pins the SQL shape behind one TCK scenario that the strict harness caught
writing rows the query should not have written.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _dml(cypher: str, params=None):
    query = translate_to_sql(parse_query(cypher), params or {})
    statements = query.sql if isinstance(query.sql, list) else [query.sql]
    return [
        (sql, query.parameters[i] if i < len(query.parameters) else [])
        for i, sql in enumerate(statements)
    ]


def _inserts(stmts, table):
    return [(s, p) for s, p in stmts if re.search(rf"INSERT INTO (?:\w+\.)?{table} ", s)]


class TestMergeNodeMatchedWritesNothing:
    """Merge1 [6]: a MERGE that matches must not leave a property row for the
    node it did not create."""

    def test_property_insert_is_conditional_on_the_new_node(self):
        stmts = _dml("MERGE (a:TheLabel {num: 42}) RETURN a.num")
        node_ins = _inserts(stmts, "nodes")
        assert len(node_ins) == 1
        new_id = node_ins[0][1][0]
        props = _inserts(stmts, "rdf_props")
        assert props, "the merged node's property is still written when it is created"
        for sql, params in props:
            assert new_id in params
            # guarded by the node row the MERGE inserted (absent when it matched)
            assert re.search(r"AND EXISTS \(SELECT 1 FROM (?:\w+\.)?nodes WHERE node_id = \?", sql), sql
            assert params[-1] == new_id


class TestMergeOnMatchedPropertyRunsPerRow:
    """Merge1 [11]: `MATCH (person:Person) MERGE (city:City {name: person.bornIn})`.

    The static translation bound the PropertyReference object itself as a SQL
    parameter (the driver raised "Unsupported argument type"). The MATCH now runs
    as a read-only prefix returning `person.bornIn`, and the MERGE runs once per
    row with the value inlined, so rows with the same city merge into one node.
    """

    Q = "MATCH (person:Person) MERGE (city:City {name: person.bornIn})"

    def test_prefix_reads_the_property_and_writes_nothing(self):
        from iris_vector_graph.cypher.merge_rows import plan_row_merge

        plan = plan_row_merge(parse_query(self.Q), {})
        assert plan is not None
        t = translate_to_sql(plan.prefix, {})
        assert not t.is_transactional
        assert len(plan.row_vars) == 1

    def test_each_row_binds_the_property_value(self):
        from iris_vector_graph.cypher.merge_rows import plan_row_merge

        plan = plan_row_merge(parse_query(self.Q), {})
        q = plan.bind({plan.row_vars[0]: "Ohio"})
        assert q is not None
        flat = [str(v) for _s, p in _dml_of(q) for v in p]
        assert "Ohio" in flat
        assert not any("PropertyReference(" in v for v in flat), flat

    @pytest.mark.parametrize(
        "q",
        [
            # the merged pattern needs the matched node itself: cannot be inlined
            "MATCH (a:A) MERGE (a)-[:R]->(b:B {x: a.x})",
            # returns the matched node
            "MATCH (a:A) MERGE (b:B {x: a.x}) RETURN a",
            # no property of the match is read
            "MATCH (a:A) MERGE (b:B {x: 1})",
        ],
    )
    def test_shapes_needing_the_node_keep_the_static_translation(self, q):
        from iris_vector_graph.cypher.merge_rows import plan_row_merge

        assert plan_row_merge(parse_query(q), {}) is None


class TestMergeRelationshipOnCreateOnMatch:
    """Merge6 [1] / Merge7 [2]: `MATCH (a:A), (b:B) MERGE (a)-[r:KNOWS]->(b)` with
    an ON CREATE / ON MATCH action.

    ON CREATE SET b.created bound its markers in the wrong order (the SELECT list's
    two markers came last), so it matched no row and wrote nothing. ON MATCH SET
    r.created ran after the edge insert and so updated the edge just created. Both
    actions now run before the edge insert, gated on whether the edge exists.
    """

    ON_CREATE = "MATCH (a:A), (b:B) MERGE (a)-[:KNOWS]->(b) ON CREATE SET b.created = 1"
    ON_MATCH = "MATCH (a:A), (b:B) MERGE (a)-[r:KNOWS]->(b) ON MATCH SET r.created = 1"

    @staticmethod
    def _edge_insert_index(stmts):
        return next(
            i for i, (s, _p) in enumerate(stmts) if re.search(r"INSERT INTO (?:\w+\.)?rdf_edges ", s)
        )

    def test_on_create_node_property_binds_in_text_order(self):
        stmts = _dml(self.ON_CREATE)
        (sql, params), = _inserts(stmts, "rdf_props")
        assert sql.count("?") == len(params)
        # the SELECT list's key and value markers are the statement's first two
        assert params[:2] == ["created", 1], params

    def test_on_create_runs_before_the_edge_insert_and_only_without_the_edge(self):
        stmts = _dml(self.ON_CREATE)
        (sql, _params), = _inserts(stmts, "rdf_props")
        idx = [s for s, _ in stmts].index(sql)
        assert idx < self._edge_insert_index(stmts)
        assert re.search(r"NOT EXISTS \(SELECT 1 FROM (?:\w+\.)?rdf_edges", sql), sql

    def test_on_match_relationship_update_runs_before_the_edge_insert(self):
        stmts = _dml(self.ON_MATCH)
        upd = [i for i, (s, _p) in enumerate(stmts) if re.match(r"UPDATE (?:\w+\.)?rdf_edges", s)]
        assert upd, stmts
        assert all(i < self._edge_insert_index(stmts) for i in upd)


class TestSetNodeToMapReplacesAllProperties:
    """Set4 [2][3][4]: `MATCH (n:X {name: 'A'}) SET n = {...}`.

    The old-property DELETE selected its nodes through the MATCH's own property
    join (`rdf_props ... "key" = 'name' AND val = 'A'`). IRIS re-evaluates that
    subquery while deleting, so once the `name` row went the node stopped
    matching and `name2` survived. The matched ids are now pinned first as a
    marker row, and every later statement selects through the marker.
    """

    Q = "MATCH (n:X {name: 'A'}) SET n = {name: 'B', baz: 'C'} RETURN n"

    def test_matched_ids_are_pinned_before_anything_is_deleted(self):
        from iris_vector_graph.cypher.translator import _REPLACE_MARKER

        stmts = _dml(self.Q)
        first_delete = next(i for i, (s, _p) in enumerate(stmts) if s.lstrip().startswith("DELETE"))
        pins = [
            i
            for i, (s, p) in enumerate(stmts)
            if "INSERT INTO" in s and "rdf_props" in s and _REPLACE_MARKER in p
        ]
        assert pins and pins[0] < first_delete, stmts

    def test_old_property_delete_does_not_depend_on_the_deleted_rows(self):
        from iris_vector_graph.cypher.translator import _REPLACE_MARKER

        dels = [(s, p) for s, p in _dml(self.Q) if s.lstrip().startswith("DELETE") and "rdf_props" in s]
        assert dels
        for sql, params in dels:
            assert _REPLACE_MARKER in params, (sql, params)
            # no join back to the MATCH's property filter
            assert "'A'" not in sql and "A" not in params, (sql, params)

    def test_new_properties_go_only_to_the_pinned_nodes(self):
        from iris_vector_graph.cypher.translator import _REPLACE_MARKER

        ins = [
            (s, p)
            for s, p in _dml(self.Q)
            if "INSERT INTO" in s and "rdf_props" in s and p and p[0] != _REPLACE_MARKER
        ]
        assert {p[0] for _s, p in ins} == {"name", "baz"}, ins
        assert all(_REPLACE_MARKER in p for _s, p in ins), ins

    def test_marker_rows_are_removed_last(self):
        from iris_vector_graph.cypher.translator import _REPLACE_MARKER

        stmts = _dml("MATCH (n:X {name: 'A'}) SET n = { }")
        dml = [(s, p) for s, p in stmts if not s.lstrip().upper().startswith(("SELECT", "WITH"))]
        last_sql, last_params = dml[-1]
        assert last_sql.lstrip().startswith("DELETE"), dml
        assert last_params == [_REPLACE_MARKER], dml


class TestUnwoundParameterMatchThenMerge:
    """Unwind1 [6]: `UNWIND $events AS event MATCH (y:Year {year: event.year})
    MERGE (e:Event {id: event.id}) MERGE (y)<-[:IN]-(e)`.

    The per-element UNWIND expansion skipped the MATCH, so `y` was unbound and
    the relationship MERGE created a fresh node for it on every element.
    """

    Q = (
        "UNWIND $events AS event MATCH (y:Year {year: event.year}) "
        "MERGE (e:Event {id: event.id}) MERGE (y)<-[:IN]-(e) RETURN e.id AS x"
    )
    P = {"events": [{"year": 2016, "id": 1}, {"year": 2016, "id": 2}]}

    def test_matched_node_is_not_created(self):
        stmts = _dml(self.Q, self.P)
        node_ins = _inserts(stmts, "nodes")
        # one conditional insert per element, for `e` only
        assert len(node_ins) == 2, node_ins
        assert all("Event" in p for _s, p in node_ins), node_ins

    def test_relationship_targets_the_matched_year(self):
        stmts = _dml(self.Q, self.P)
        edges = _inserts(stmts, "rdf_edges")
        assert len(edges) == 2
        for sql, params in edges:
            assert "Year" in params and ("2016" in sql or 2016 in params), (sql, params)


def _dml_of(parsed, params=None):
    query = translate_to_sql(parsed, params or {})
    statements = query.sql if isinstance(query.sql, list) else [query.sql]
    return [
        (sql, query.parameters[i] if i < len(query.parameters) else [])
        for i, sql in enumerate(statements)
    ]
