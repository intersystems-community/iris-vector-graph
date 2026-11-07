<!-- markdownlint-disable MD013 -->

# Quickstart: checking spec 262 by hand

Container `ivg-iris-enterprise`, port 31972 (`tests/conftest.py`); scratch namespaces only.

## A new namespace has nothing of IVG's in SQLUser

```python
from iris_vector_graph import IRISGraphEngine
engine = IRISGraphEngine(conn, embedding_dimension=4)
print(engine.initialize_schema()["schema"])          # Graph_KG
```

```sql
SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'SQLUser'   -- 0 of IVG's
SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'SQLUser' AND TABLE_TYPE = 'VIEW'  -- 0 of IVG's
```

## A chosen schema

```python
engine = IRISGraphEngine(conn, embedding_dimension=4, schema_prefix="IVGALT")
engine.initialize_schema()
engine.execute_cypher("CREATE (:P {id: 'a'})-[:R]->(:P {id: 'b'})")
engine.execute_cypher("MATCH (a)-[:R]->(b) RETURN b.id").rows                  # [['b']]
IRISGraphEngine(conn, embedding_dimension=4).execute_cypher("RETURN 1")        # IVGSchemaError: IVGALT vs Graph_KG
```

## Upgrading a 4.5.1 namespace

```python
engine.initialize_schema(dry_run=True)                 # what would be recorded and which grants copied; writes nothing
engine.initialize_schema()
engine.remove_legacy_objects()                         # lists the 4.5.x SQLUser/User objects still there
engine.remove_legacy_objects(dry_run=False)            # drops exactly those
```

## Terminal check (an operator's view)

```text
USER> zn "MYNS"
MYNS> w ^IVG.Deploy("schema")
Graph_KG
MYNS> w ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.NKGAccel")
1
```
