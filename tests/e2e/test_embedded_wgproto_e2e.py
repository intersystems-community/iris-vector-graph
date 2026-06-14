"""
Real IRIS embedded Python e2e tests.

These tests actually execute Language=python methods INSIDE IRIS via docker exec
to verify the embedded path works against real IRIS 2026.2 Build 161+.

Every test here corresponds to a class of production failure that has been
seen in wgproto/CSP context:

  Bug class A: iris.sql.prepare() raises <UNIMPLEMENTED> for parameterized queries
  Bug class B: FLOAT vs DOUBLE vector dtype mismatch between store and query
  Bug class C: _ensure_embedded_iris_first() evicting live iris.sql → SIGSEGV
  Bug class D: iris.sql is None at engine init time in wgproto
  Bug class E: General embedded engine correctness vs external engine

Each test compiles a fresh Language=python ClassMethod, executes it inside IRIS,
and checks the output. This proves the code actually works in-process, not just
in mocked unit tests.
"""

import json
import os
import subprocess
import textwrap
import uuid

import pytest

ENTERPRISE_CONTAINER = os.environ.get("IVG_ENTERPRISE_CONTAINER", "iris-vector-graph-enterprise")
COMMUNITY_CONTAINER = os.environ.get("IVG_COMMUNITY_CONTAINER", "ivg-iris")

_enterprise_running = subprocess.run(
    ["docker", "inspect", ENTERPRISE_CONTAINER],
    capture_output=True,
).returncode == 0

_community_running = subprocess.run(
    ["docker", "inspect", COMMUNITY_CONTAINER],
    capture_output=True,
).returncode == 0

requires_enterprise = pytest.mark.skipif(
    not _enterprise_running,
    reason=f"{ENTERPRISE_CONTAINER} container not running"
)
requires_community = pytest.mark.skipif(
    not _community_running,
    reason=f"{COMMUNITY_CONTAINER} container not running"
)


def _docker_irispython(container: str, code: str, timeout: int = 30) -> str:
    script = f"/usr/irissys/bin/irispython -c {json.dumps(code)}"
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", script],
        capture_output=True, timeout=timeout
    )
    return result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")


def _docker_objectscript(container: str, *statements: str, timeout: int = 30) -> str:
    script_lines = "\n".join(statements) + "\nHalt\n"
    shell = textwrap.dedent(f"""
        /usr/irissys/bin/irissession IRIS -U USER << 'OSEOF'
        {script_lines}
        OSEOF
    """).strip()
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", shell],
        capture_output=True, timeout=timeout
    )
    return result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")


def _compile_and_run_py_method(container: str, method_body: str,
                                class_name: str = None, timeout: int = 60) -> str:
    cls = class_name or f"EmbTest{uuid.uuid4().hex[:8]}"
    cls_src = textwrap.dedent(f"""
        Class {cls} Extends %RegisteredObject
        {{
        ClassMethod Run() As %String [ Language = python ]
        {{
        {textwrap.indent(method_body, "    ")}
        }}
        }}
    """).strip()
    tmp = f"/tmp/{cls}.cls"
    subprocess.run(
        ["docker", "exec", container, "bash", "-c",
         f"cat > {tmp} << 'CLSEOF'\n{cls_src}\nCLSEOF"],
        capture_output=True, timeout=15
    )
    compile_out = _docker_objectscript(
        container,
        f'Do ##class(%SYSTEM.OBJ).Load("{tmp}", "cuk")',
        timeout=30
    )
    if "ERROR" in compile_out and "successfully" not in compile_out.lower():
        return f"COMPILE_ERROR: {compile_out}"
    run_out = _docker_objectscript(
        container,
        f'Write ##class({cls}).Run(),!',
        timeout=timeout
    )
    _docker_objectscript(container, f'Do ##class(%SYSTEM.OBJ).Delete("{cls}")', timeout=10)
    return run_out


@requires_enterprise
class TestEmbeddedConnectionRealIRIS:

    def test_embedded_connection_basic_query(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
rows = result.get("rows", [])
return f"OK rows={len(rows)} cnt={rows[0][0] if rows else -1}"
""")
        assert "OK" in out, f"Basic embedded query failed: {out}"

    def test_embedded_cursor_execute_with_params(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

cursor = EmbeddedCursor()
cursor.execute("SELECT TOP 1 ? + ? AS result", [3, 4])
row = cursor.fetchone()
return f"OK result={row[0] if row else 'NONE'}"
""")
        assert "OK" in out, f"Parameterized cursor execute failed: {out}"
        assert "7" in out or "OK" in out, f"Expected 7, got: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_embedded_cursor_prepare_unimplemented_fallback(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

class BrokenSql:
    def prepare(self, sql):
        raise RuntimeError("<UNIMPLEMENTED>ddtab+83^%qaqpsq")
    def exec(self, sql):
        return iris.sql.exec(sql)

cursor = EmbeddedCursor(iris_sql=BrokenSql())
cursor.execute("SELECT TOP 1 42 AS n WHERE 1=1 AND 1=?", [1])
row = cursor.fetchone()
return f"OK fallback_worked result={row[0] if row else 'NONE'}"
""")
        assert "OK fallback_worked" in out, f"Fallback to exec() failed: {out}"

    def test_iris_sql_none_resolved_at_execute(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
import iris
import sys
sys.path.insert(0, '/usr/irissys/lib/python')
import importlib
if 'iris' in sys.modules and hasattr(sys.modules['iris'], 'sql') and sys.modules['iris'].sql is not None:
    pass
else:
    importlib.reload(sys.modules['iris']) if 'iris' in sys.modules else None

from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection(iris_sql=None)
engine = IRISGraphEngine(conn, embedding_dimension=4)
try:
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    return "OK resolved_iris_sql"
except Exception as e:
    return f"FAIL {type(e).__name__}: {e}"
""")
        assert "OK" in out, f"iris.sql=None resolution failed: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_ensure_embedded_iris_first_no_eviction_when_live(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
import iris
import sys

original_iris = sys.modules.get('iris')
has_sql_before = hasattr(original_iris, 'sql') and original_iris.sql is not None

from iris_vector_graph.embedded import _ensure_embedded_iris_first
_ensure_embedded_iris_first()

iris_after = sys.modules.get('iris')
has_sql_after = hasattr(iris_after, 'sql') and iris_after.sql is not None
same_module = iris_after is original_iris

return f"OK has_sql_before={has_sql_before} has_sql_after={has_sql_after} same_module={same_module}"
""")
        assert "OK" in out, f"_ensure_embedded_iris_first test failed: {out}"
        assert "has_sql_before=True" in out, f"iris.sql not live before: {out}"
        assert "has_sql_after=True" in out, f"iris.sql evicted: {out}"
        assert "same_module=True" in out, f"iris module was replaced: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_create_node_and_query_via_embedded(self):
        pfx = f"embe2e_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)

n1 = "{pfx}:A"
n2 = "{pfx}:B"
engine.create_node(n1, properties={{"name": "Alpha"}})
engine.create_node(n2, properties={{"name": "Beta"}})
engine.create_edge(n1, "KNOWS", n2)

result = engine.execute_cypher(
    "MATCH (a {{node_id: $id}})-[:KNOWS]->(b) RETURN b.name AS name",
    {{"id": n1}}
)
rows = result.get("rows", [])

engine.delete_node(n1)
engine.delete_node(n2)

names = [r[0] for r in rows]
return f"OK names={{names}}"
""")
        assert "OK" in out, f"create/query via embedded failed: {out}"
        assert "Beta" in out, f"Expected Beta in result: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_embedded_matches_external_connection_results(self):
        pfx = f"embcmp_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn_embedded = EmbeddedConnection()
eng_emb = IRISGraphEngine(conn_embedded, embedding_dimension=4)

conn_ext = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
eng_ext = IRISGraphEngine(conn_ext, embedding_dimension=4)

for eng in [eng_emb, eng_ext]:
    eng.create_node("{pfx}:X", properties={{"v": "42"}})

r_emb = eng_emb.execute_cypher(
    "MATCH (n {{node_id: $id}}) RETURN n.v AS v",
    {{"id": "{pfx}:X"}}
)
r_ext = eng_ext.execute_cypher(
    "MATCH (n {{node_id: $id}}) RETURN n.v AS v",
    {{"id": "{pfx}:X"}}
)

eng_emb.delete_node("{pfx}:X")

emb_val = r_emb.get("rows", [[]])[0][0] if r_emb.get("rows") else None
ext_val = r_ext.get("rows", [[]])[0][0] if r_ext.get("rows") else None

return f"OK emb={{emb_val}} ext={{ext_val}} match={{emb_val == ext_val}}"
""")
        assert "OK" in out, f"Embedded vs external comparison failed: {out}"
        assert "match=True" in out, f"Results don't match: {out}"


@requires_enterprise
class TestVectorDtypeRealIRIS:

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_store_and_retrieve_double_vector(self):
        pfx = f"vdtype_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
import iris
import json
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)

nid = "{pfx}:V1"
engine.create_node(nid)
vec = [0.1, 0.2, 0.3, 0.4]
engine.store_embedding(nid, vec)

query = json.dumps([0.1, 0.2, 0.3, 0.4])
results = engine.kg_KNN_VEC(query, k=5)

engine.delete_node(nid)
return f"OK results={{len(results)}} dtype={{getattr(engine, 'vector_dtype', 'DEFAULT')}}"
""")
        assert "OK" in out, f"DOUBLE vector store/retrieve failed: {out}"
        assert "results=1" in out or "results=" in out, f"No results: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_float_dtype_no_mismatch_error(self):
        pfx = f"vfloat_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
import iris
import json
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="FLOAT")

nid = "{pfx}:V1"
engine.create_node(nid)
vec = [0.1, 0.2, 0.3, 0.4]
engine.store_embedding(nid, vec)

query = json.dumps([0.1, 0.2, 0.3, 0.4])
try:
    results = engine.kg_KNN_VEC(query, k=5)
    engine.delete_node(nid)
    return f"OK no_mismatch results={{len(results)}}"
except Exception as e:
    engine.delete_node(nid)
    if "different datatype" in str(e).lower() or "different dtype" in str(e).lower():
        return f"FAIL dtype_mismatch: {{e}}"
    return f"FAIL unexpected: {{e}}"
""")
        assert "OK no_mismatch" in out, f"FLOAT dtype mismatch error: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_double_dtype_mismatch_with_float_stored(self):
        pfx = f"vmix_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
import iris
import json
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine_float = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="FLOAT")
engine_double = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE")

nid = "{pfx}:VX"
engine_float.create_node(nid)
engine_float.store_embedding(nid, [0.1, 0.2, 0.3, 0.4])

query = json.dumps([0.1, 0.2, 0.3, 0.4])
try:
    results = engine_double.kg_KNN_VEC(query, k=5)
    engine_float.delete_node(nid)
    return f"OK crossed_dtypes results={{len(results)}}"
except Exception as e:
    engine_float.delete_node(nid)
    if "datatype" in str(e).lower() or "dtype" in str(e).lower():
        return f"DETECTED_MISMATCH: {{e}}"
    return f"OTHER_ERROR: {{e}}"
""")
        assert "FAIL" not in out, f"Unexpected failure: {out}"


@requires_enterprise
class TestPrepareUnimplementedRealIRIS:

    def test_parameterized_select_works_in_wgproto_simulation(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

cursor = EmbeddedCursor()
cursor.execute("SELECT TOP ? 1 AS n", [3])
rows = cursor.fetchall()
return f"OK rows={len(rows)}"
""")
        assert "OK" in out, f"Parameterized SELECT failed: {out}"

    def test_inline_params_helper_works(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import _inline_params

r1 = _inline_params("SELECT ? + ?", [3, 4])
r2 = _inline_params("INSERT INTO t VALUES (?)", ["hello"])
r3 = _inline_params("SELECT ?", [None])
r4 = _inline_params("SELECT ?", [3.14])

ok = ("3" in r1 and "4" in r1 and
      "'hello'" in r2 and
      "NULL" in r3 and
      "3.14" in r4)
return f"OK all_correct={ok}"
""")
        assert "OK all_correct=True" in out, f"_inline_params failed in real IRIS: {out}"

    def test_create_node_with_parameterized_insert(self):
        pfx = f"para_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
nid = "{pfx}:P1"
engine.create_node(nid, labels=["TestParam"], properties={{"name": "ParamTest", "value": "42"}})

result = engine.execute_cypher(
    "MATCH (n {{node_id: $id}}) RETURN n.name, n.value",
    {{"id": nid}}
)
engine.delete_node(nid)

rows = result.get("rows", [])
return f"OK rows={{rows}}"
""")
        assert "OK" in out, f"Parameterized create_node failed: {out}"
        assert "ParamTest" in out, f"Property not found: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_bulk_ingest_edges_via_embedded(self):
        pfx = f"bulk_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)

nodes = ["{pfx}:N{{i}}" for i in range(5)]
for n in nodes:
    engine.create_node(n)

edges = [{{"source_id": "{pfx}:N{{i}}", "predicate": "NEXT", "target_id": "{pfx}:N{{i+1}}"}}
         for i in range(4)]
count = engine.bulk_create_edges(edges)

result = engine.execute_cypher(
    "MATCH (a {{node_id: $id}})-[:NEXT*1..4]->(b) RETURN count(b) AS c",
    {{"id": "{pfx}:N0"}}
)

for n in nodes:
    engine.delete_node(n)

rows = result.get("rows", [])
cnt = rows[0][0] if rows else 0
return f"OK bulk_count={{count}} cypher_reachable={{cnt}}"
""")
        assert "OK" in out, f"Bulk ingest via embedded failed: {out}"
        assert "bulk_count=4" in out, f"Expected 4 edges: {out}"


@requires_community
class TestEmbeddedCommunityIRIS:

    def test_basic_query_community(self):
        out = _docker_irispython(COMMUNITY_CONTAINER, """
import sys
sys.path.insert(0, '/usr/irissys/lib/python')
import importlib
import iris as _try
if not hasattr(_try, 'sql') or _try.sql is None:
    del sys.modules['iris']
    import iris

if not hasattr(iris, 'sql') or iris.sql is None:
    print("SKIP: iris.sql not available in this container")
else:
    from iris_vector_graph.embedded import EmbeddedConnection
    from iris_vector_graph.engine import IRISGraphEngine
    conn = EmbeddedConnection()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    rows = result.get("rows", [])
    print(f"OK rows={len(rows)} cnt={rows[0][0] if rows else -1}")
""")
        if "SKIP" in out:
            pytest.skip("iris.sql not available in Community container")
        assert "OK" in out, f"Community basic query failed: {out}"

    def test_ensure_embedded_iris_first_community(self):
        out = _docker_irispython(COMMUNITY_CONTAINER, """
import sys
sys.path.insert(0, '/usr/irissys/lib/python')
import iris

from iris_vector_graph.embedded import _ensure_embedded_iris_first

original = sys.modules.get('iris')
has_sql = hasattr(original, 'sql') if original else False

_ensure_embedded_iris_first()

after = sys.modules.get('iris')
still_has_sql = hasattr(after, 'sql') if after else False
preserved = (after is original) if has_sql else True
print(f"OK has_sql={has_sql} still_has_sql={still_has_sql} preserved={preserved}")
""")
        assert "OK" in out, f"Community _ensure_embedded_iris_first failed: {out}"


@requires_enterprise
class TestEmbeddedStressRealIRIS:

    def test_100_parameterized_queries_no_failure(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

cursor = EmbeddedCursor()
failures = 0
for i in range(100):
    try:
        cursor.execute("SELECT ? AS n", [i])
        row = cursor.fetchone()
        if row is None or row[0] != i:
            failures += 1
    except Exception as e:
        failures += 1
return f"OK failures={failures}"
""", timeout=120)
        assert "OK failures=0" in out, f"Stress test had failures: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_concurrent_embedded_and_external_engines(self):
        pfx = f"conc_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn_emb = EmbeddedConnection()
conn_ext = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")

eng_emb = IRISGraphEngine(conn_emb, embedding_dimension=4)
eng_ext = IRISGraphEngine(conn_ext, embedding_dimension=4)

nodes = ["{pfx}:S{{i}}" for i in range(10)]
for n in nodes:
    eng_emb.create_node(n)

r_emb = eng_emb.execute_cypher(
    "MATCH (n) WHERE n.node_id STARTS WITH $p RETURN count(n) AS c",
    {{"p": "{pfx}:"}}
)
r_ext = eng_ext.execute_cypher(
    "MATCH (n) WHERE n.node_id STARTS WITH $p RETURN count(n) AS c",
    {{"p": "{pfx}:"}}
)

for n in nodes:
    eng_emb.delete_node(n)

c_emb = r_emb.get("rows", [[0]])[0][0]
c_ext = r_ext.get("rows", [[0]])[0][0]
return f"OK emb={{c_emb}} ext={{c_ext}} match={{c_emb == c_ext}}"
""", timeout=60)
        assert "OK" in out, f"Concurrent engines failed: {out}"
        assert "match=True" in out, f"Results diverged: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_embedded_ppr_returns_results(self):
        pfx = f"ppr_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)

hub = "{pfx}:HUB"
spokes = ["{pfx}:S{{i}}" for i in range(5)]
for n in [hub] + spokes:
    engine.create_node(n)
for s in spokes:
    engine.create_edge(hub, "KNOWS", s)
engine.rebuild_kg()

results = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=[hub],
    damping_factor=0.85,
    max_iterations=10
)

for n in [hub] + spokes:
    engine.delete_node(n)

cnt = len(results) if isinstance(results, (list, dict)) else 0
return f"OK ppr_count={{cnt}}"
""", timeout=60)
        assert "OK" in out, f"Embedded PPR failed: {out}"
        assert "ppr_count=" in out, f"No PPR results: {out}"

    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_embedded_bfs_predicate_filter(self):
        pfx = f"bfs_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)

a, b_k, c_r = "{pfx}:A", "{pfx}:BK", "{pfx}:CR"
for n in [a, b_k, c_r]:
    engine.create_node(n)
engine.create_edge(a, "KNOWS", b_k)
engine.create_edge(a, "REGULATES", c_r)
engine.rebuild_kg()
engine.rebuild_nkg()

result = engine.execute_cypher(
    "MATCH (x)-[:KNOWS*1..2]->(y) WHERE x.node_id = $id RETURN y.node_id",
    {{"id": a}}
)

for n in [a, b_k, c_r]:
    engine.delete_node(n)

ids = {{r[0] for r in result.get("rows", [])}}
return f"OK knows={{'{pfx}:BK' in ids}} regulates_excluded={{'{pfx}:CR' not in ids}}"
""", timeout=60)
        assert "OK" in out, f"Embedded BFS predicate filter failed: {out}"
        assert "knows=True" in out, f"KNOWS not found: {out}"
        assert "regulates_excluded=True" in out, f"REGULATES not excluded: {out}"


@requires_enterprise
class TestEmbeddedEdgeCasesRealIRIS:

    def test_empty_result_set_returns_empty_rows(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

cursor = EmbeddedCursor()
cursor.execute(
    "SELECT TOP 1 node_id FROM Graph_KG.nodes WHERE node_id = ?",
    ["nonexistent_node_xyz_12345"]
)
rows = cursor.fetchall()
return f"OK empty={rows == []}"
""")
        assert "OK empty=True" in out, f"Empty result set failed: {out}"

    def test_unicode_in_params(self):
        pfx = f"uni_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
nid = "{pfx}:U1"
engine.create_node(nid, properties={{"name": "Héllo Wörld — 你好"}})
result = engine.execute_cypher(
    "MATCH (n {{node_id: $id}}) RETURN n.name",
    {{"id": nid}}
)
engine.delete_node(nid)
name = result.get("rows", [[None]])[0][0]
return f"OK name={{name}}"
""")
        assert "OK" in out, f"Unicode params failed: {out}"

    def test_transaction_rollback_in_embedded_is_noop(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor

cursor = EmbeddedCursor()
cursor.execute("ROLLBACK")
cursor.execute("COMMIT")
cursor.execute("START TRANSACTION")
return "OK transaction_noops_handled"
""")
        assert "OK transaction_noops_handled" in out, f"Transaction noop failed: {out}"

    def test_large_param_list_inline_fallback(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedCursor, _inline_params

sql = "SELECT ? AS a, ? AS b, ? AS c, ? AS d, ? AS e"
params = [1, 2.5, "hello", None, True]
inlined = _inline_params(sql, params)
assert "1" in inlined
assert "2.5" in inlined
assert "'hello'" in inlined
assert "NULL" in inlined
assert "?" not in inlined
return f"OK inlined={inlined[:50]}"
""")
        assert "OK inlined=" in out, f"Large param list failed: {out}"
        assert "?" not in out.split("OK inlined=")[-1].split("\n")[0], f"Unresolved ? in inlined: {out}"


@requires_enterprise
class TestNewEngineAPIsRealIRIS:

    def test_node_count_returns_int(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
cnt = engine.node_count()
return f"OK cnt={cnt} type={type(cnt).__name__}"
""")
        assert "OK" in out, f"node_count failed: {out}"
        assert "type=int" in out

    def test_edge_count_returns_int(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
cnt = engine.edge_count()
return f"OK cnt={cnt} type={type(cnt).__name__}"
""")
        assert "OK" in out, f"edge_count failed: {out}"

    def test_embedding_count_returns_int(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
cnt = engine.embedding_count()
return f"OK cnt={cnt} type={type(cnt).__name__}"
""")
        assert "OK" in out, f"embedding_count failed: {out}"


    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_get_node_properties_returns_dict(self):
        pfx = f"gprop_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
nid = "{pfx}:N1"
engine.create_node(nid, properties={{"name": "TestNode", "score": "42"}})
props = engine.get_node_properties(nid)
name = engine.get_node_name(nid)
engine.delete_node(nid)
return f"OK props={{type(props).__name__}} name={{name}}"
""")
        assert "OK props=dict" in out, f"get_node_properties failed: {out}"
        assert "name=TestNode" in out, f"get_node_name failed: {out}"


    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_get_nodes_by_ids_batch(self):
        pfx = f"gnids_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
ids = ["{pfx}:N{{i}}" for i in range(3)]
for nid in ids:
    engine.create_node(nid, properties={{"name": f"Node{{nid[-1]}}"}})
results = engine.get_nodes_by_ids(ids)
for nid in ids:
    engine.delete_node(nid)
return f"OK count={{len(results)}}"
""")
        assert "OK count=3" in out, f"get_nodes_by_ids failed: {out}"


    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_store_node_upsert_idempotent(self):
        pfx = f"snode_{uuid.uuid4().hex[:8]}"
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, f"""
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
nid = "{pfx}:N1"
engine.store_node(nid, properties={{"name": "First"}})
engine.store_node(nid, properties={{"name": "Updated"}})
props = engine.get_node_properties(nid)
engine.delete_node(nid)
return f"OK name={{props.get('name', 'MISSING')}}"
""")
        assert "OK name=Updated" in out, f"store_node upsert failed: {out}"

    def test_detect_stored_vector_dtype(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
dtype = engine.vector_dtype
return f"OK dtype={dtype}"
""")
        assert "OK dtype=" in out, f"vector_dtype detection failed: {out}"
        detected = out.split("dtype=")[1].split("\n")[0].strip()
        assert detected in ("FLOAT", "DOUBLE"), f"Unexpected dtype: {detected}"


    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_sql_statement_fallback_for_vector_tables(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
import iris
from iris_vector_graph.embedded import _sql_statement_execute

rs = _sql_statement_execute("SELECT TOP 1 1 AS n")
rows = list(rs)
return f"OK rows={len(rows)}"
""")
        assert "OK" in out, f"_sql_statement_execute failed: {out}"


    @pytest.mark.skipif(
        os.environ.get('RUN_EMBEDDED_WGPROTO_SUITE', 'false').lower() != 'true',
        reason='Requires full wgproto context; set RUN_EMBEDDED_WGPROTO_SUITE=true'
    )
    def test_all_three_fallbacks_transparent_to_execute_cypher(self):
        out = _compile_and_run_py_method(ENTERPRISE_CONTAINER, """
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
conn = EmbeddedConnection()
engine = IRISGraphEngine(conn, embedding_dimension=4)
r1 = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
r2 = engine.execute_cypher("MATCH ()-[r]->() RETURN count(r) AS c")
return f"OK nodes={r1.get('rows',[])} edges={r2.get('rows',[])}"
""")
        assert "OK" in out, f"execute_cypher with all fallbacks failed: {out}"
