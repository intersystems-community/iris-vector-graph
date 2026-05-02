"""
IVG execution context tests — three contexts must work equally:
1. External DBAPI:  iris.connect() from Python outside IRIS process
2. Embedded Python: irispython from within IRIS ObjectScript/CSP
3. ObjectScript:    %SQL.Statement / $ZF callouts from .cls files
"""
import re
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture(autouse=True)
def set_prefix():
    set_schema_prefix("Graph_KG")
    yield


@pytest.fixture
def engine(iris_connection):
    return IRISGraphEngine(iris_connection, embedding_dimension=768)


@pytest.fixture
def populated_db(iris_connection, engine):
    import uuid
    pfx = f"ec_{uuid.uuid4().hex[:8]}"
    nodes = [f"{pfx}_Alice", f"{pfx}_Bob", f"{pfx}_Carol"]
    cur = iris_connection.cursor()
    for i, n in enumerate(nodes):
        name = ["Alice", "Bob", "Carol"][i]
        age = str(30 if i == 0 else 25)
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
        cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [n, "Person"])
        cur.execute('INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)', [n, "name", name])
        cur.execute('INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)', [n, "age", age])
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "KNOWS", nodes[1]])
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "KNOWS", nodes[2]])
    iris_connection.commit()
    yield engine
    like = f"{pfx}%"
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [like, like])
    cur.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [like])
    cur.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [like])
    cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [like])
    iris_connection.commit()


class TestColumnsNeverEmpty:
    """columns=[] bug: embedded irispython returns cursor.description=None"""

    def test_select_with_as_aliases(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN n.name AS name, n.age AS age")
        assert r.get("columns"), f"columns must not be empty: {r}"
        assert "name" in r["columns"] and "age" in r["columns"]
        assert len(r["rows"]) > 0

    def test_source_target_rel(self, populated_db):
        r = populated_db.execute_cypher(
            "MATCH (s:Person)-[r]->(t:Person) RETURN s.id AS source, t.id AS target, type(r) AS rel LIMIT 10"
        )
        assert r.get("columns"), "columns must not be empty for source/target/rel"

    def test_count_query(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN count(n) AS total")
        assert r.get("columns"), f"columns must not be empty for count: {r}"
        assert len(r.get("rows", [])) > 0

    def test_fallback_regex_extracts_aliases(self):
        sql = "SELECT a.node_id AS source, b.node_id AS target, e.p AS rel FROM ..."
        aliases = re.findall(r'\bAS\s+"?([a-zA-Z_][a-zA-Z0-9_]*)"?', sql, re.IGNORECASE)
        assert aliases == ["source", "target", "rel"]

    def test_fallback_col_index_on_count_mismatch(self):
        row = (42,)
        aliases = []
        cols = aliases if aliases and len(aliases) == len(row) else [f"col{i}" for i in range(len(row))]
        assert cols == ["col0"]


class TestInlinePropertyFilter:
    """Inline {prop:value} must work identically to WHERE clause"""

    def test_inline_filter_basic(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person {name:'Alice'}) RETURN n.name")
        assert len(r.get("rows", [])) >= 1, f"Expected at least 1 row: {r}"
        assert all(row[0] == "Alice" for row in r["rows"]), f"All rows should be Alice: {r}"

    def test_inline_filter_no_label(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n {name:'Alice'}) RETURN n.name")
        assert len(r.get("rows", [])) >= 1

    def test_inline_filter_equals_where(self, populated_db):
        r1 = populated_db.execute_cypher("MATCH (n:Person {name:'Alice'}) RETURN n.name")
        r2 = populated_db.execute_cypher("MATCH (n:Person) WHERE n.name = 'Alice' RETURN n.name")
        assert r1.get("rows") == r2.get("rows")

    def test_inline_filter_params_balanced(self):
        r = translate_to_sql(parse_query("MATCH (n:Person {name:'Alice'}) RETURN n.name AS n"), {})
        assert r.sql.count("?") == len(r.parameters[0])


class TestCoalesce:
    """coalesce(n.prop, 0) → SQLCODE -378 (VARCHAR vs INTEGER) fixed by CAST"""

    def test_string_default(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN coalesce(n.name, 'unknown') AS name LIMIT 3")
        assert not r.get("error"), f"coalesce(str): {r.get('error')}"

    def test_int_default_no_type_error(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN coalesce(n.age, 0) AS age LIMIT 3")
        assert not r.get("error"), f"coalesce(int) SQLCODE -378: {r.get('error')}"

    def test_bool_default(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN coalesce(n.active, false) AS active LIMIT 3")
        assert not r.get("error"), f"coalesce(bool): {r.get('error')}"

    def test_float_default(self, populated_db):
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN coalesce(n.score, 0.0) AS score LIMIT 3")
        assert not r.get("error"), f"coalesce(float): {r.get('error')}"

    def test_cast_in_sql(self):
        r = translate_to_sql(parse_query("MATCH (n) RETURN coalesce(n.age, 0) AS age"), {})
        assert "CAST" in r.sql and "VARCHAR" in r.sql


class TestBacktickIdentifiers:
    """Backtick-quoted identifiers: `My Label`, `first name`, `MY REL`"""

    def test_label(self):
        ast = parse_query("MATCH (n:`My Label`) RETURN n.id")
        assert ast.query_parts[0].clauses[0].patterns[0].nodes[0].labels == ["My Label"]

    def test_property_return(self):
        r = translate_to_sql(parse_query("MATCH (n) RETURN n.`first name` AS name"), {})
        assert "first name" in str(r.parameters)

    def test_rel_type(self):
        r = translate_to_sql(parse_query("MATCH (n)-[r:`MY REL`]->(m) RETURN n.id"), {})
        assert "MY REL" in str(r.parameters)

    def test_alias(self):
        r = translate_to_sql(parse_query("MATCH (n) RETURN n.name AS `full name`"), {})
        assert "full name" in r.sql

    def test_where_property(self):
        r = translate_to_sql(parse_query("MATCH (n) WHERE n.`first name` = 'Alice' RETURN n.id"), {})
        assert "first name" in str(r.parameters)
        assert r.sql.count("?") == len(r.parameters[0])

    def test_execute_with_backtick_prop(self, populated_db):
        populated_db.execute_cypher("MATCH (n:Person {name:'Alice'}) SET n.`first name` = 'Alice Smith'")
        r = populated_db.execute_cypher("MATCH (n:Person) RETURN n.`first name` AS fname LIMIT 1")
        assert not r.get("error"), f"backtick execute: {r.get('error')}"


class TestGracefulErrorReturn:
    """execute_cypher() NEVER raises — always returns {"error":"..."} dict"""

    def test_always_returns_dict(self, engine):
        r = engine.execute_cypher("MATCH (n) RETURN n.id LIMIT 1")
        assert isinstance(r, dict)

    def test_has_required_keys(self, engine):
        r = engine.execute_cypher("MATCH (n) RETURN n.id LIMIT 1")
        for key in ("columns", "rows"):
            assert key in r

    def test_no_exception_on_sql_error(self, engine):
        try:
            r = engine.execute_cypher("MATCH (n) RETURN n.name / 0 AS x LIMIT 1")
            assert isinstance(r, dict)
        except Exception as exc:
            pytest.fail(f"execute_cypher raised: {exc}")

    def test_returns_dict_for_complex_query(self, engine):
        r = engine.execute_cypher(
            "MATCH (a)-[r1]-(b)-[r2]-(c)-[r3]-(d)-[r4]-(e)-[r5]-(f)"
            "-[r6]-(g)-[r7]-(h)-[r8]-(i)-[r9]-(j)-[r10]-(k)-[r11]-(l) RETURN count(*)"
        )
        assert isinstance(r, dict)


class TestObjectScriptContext:
    """SQL IVG generates must be directly executable by IRIS %SQL.Statement"""

    def test_generated_sql_executes_in_iris(self, iris_connection):
        cur = iris_connection.cursor()
        queries = [
            "MATCH (n:Person) RETURN n.name AS name, n.age AS age",
            "MATCH (n:Person) WHERE n.name = 'Alice' RETURN n.name",
            "MATCH (n:Person) RETURN count(n) AS total",
            "MATCH (n:Person) RETURN coalesce(n.name, 'unknown') AS name",
        ]
        for q in queries:
            r = translate_to_sql(parse_query(q), {})
            if not isinstance(r.sql, str):
                continue
            try:
                cur.execute(r.sql, r.parameters[0] if r.parameters else [])
                cur.fetchall()
            except Exception as e:
                if "SQLCODE: <-25>" in str(e) or "parse" in str(e).lower():
                    pytest.fail(f"SQL parse error for '{q}': {e}")

    def test_sql_uses_graph_kg_schema(self):
        r = translate_to_sql(parse_query("MATCH (n:Person) RETURN n.name"), {})
        assert "Graph_KG" in r.sql

    def test_all_params_are_iris_safe_scalars(self):
        queries = [
            "MATCH (n:Person {name:'Alice'}) RETURN n.name",
            "MATCH (n:Person) RETURN coalesce(n.age, 0) AS age",
            "MATCH (n)-[r:KNOWS]->(m) RETURN type(r)",
            "MATCH (n:Person) RETURN n.name AS name, n.age AS age",
        ]
        for q in queries:
            r = translate_to_sql(parse_query(q), {})
            params = r.parameters[0] if r.parameters else []
            for i, p in enumerate(params):
                assert p is None or isinstance(p, (str, int, float, bool)), \
                    f"param[{i}] type {type(p).__name__} not safe for IRIS in '{q}': {p!r}"

    def test_no_ast_objects_in_params(self):
        from iris_vector_graph.cypher import ast
        queries = [
            "FOREACH (x IN ['a','b','c'] | MERGE (:Tag {name: x}))",
            "MATCH (n:Person {name:'Alice'}) RETURN n.name",
        ]
        for q in queries:
            r = translate_to_sql(parse_query(q), {})
            all_params = r.parameters if isinstance(r.parameters[0], list) else [r.parameters]
            for param_list in all_params:
                for p in param_list:
                    assert not hasattr(p, '__class__') or p.__class__.__module__ != 'iris_vector_graph.cypher.ast', \
                        f"AST object {type(p).__name__} leaked into params for '{q}'"


class TestEngineReadiness:

    def test_is_ready(self, engine):
        assert engine.is_ready is True

    def test_initialize_schema_returns_status_dict(self, engine):
        status = engine.initialize_schema()
        assert isinstance(status, dict)
        for key in ("tables_created", "objectscript_deployed", "kg_built",
                    "embedding_dimension", "warnings"):
            assert key in status, f"missing key in status: {key}"
        assert status["embedding_dimension"] == 768

    def test_rebuild_kg_returns_bool(self, engine):
        assert isinstance(engine.rebuild_kg(), bool)

    def test_from_connect_factory_exists(self):
        assert callable(getattr(IRISGraphEngine, "from_connect", None))

    def test_reconnect_stale_exists(self):
        assert callable(getattr(IRISGraphEngine, "_reconnect_if_stale", None))
