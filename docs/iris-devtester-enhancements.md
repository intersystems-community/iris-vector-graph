# IRIS-DevTester Enhancement Ideas

Pain points encountered during PPR Functional Index development that could be addressed by `iris-devtester`:

## 1. ObjectScript Class Deployment
**Problem**: No easy way to compile ObjectScript classes from external Python
- File path issues: IRIS container can't access host filesystem
- Manual docker cp + irissession required
- Syntax issues with ObjectScript terminal (dollar signs, etc.)

**Proposed Enhancement**:
```python
from iris_devtester import IRISClassLoader

loader = IRISClassLoader(connection)
loader.compile_class('src/iris/Graph/KG/PPRFunctionalIndex.cls')
# Handles: copy to container, compile, verify
```

## 2. Functional Index Creation
**Problem**: Creating Functional Indexes requires complex SQL syntax and debugging
- Error messages are cryptic (`ERROR #5123: Unable to find entry point for method 'zPPRAdjDeleteIndex'`)
- IRIS generates index-specific method names (e.g., `zPPRAdjDeleteIndex` for index named `PPR_Adj`)
- No way to validate if Functional Index is actually working
- Unclear whether error is from:
  - Class not compiled
  - Class compiled but not found
  - Method naming mismatch
  - Index already exists
- Manual steps required: compile class → create index → purge → rebuild
- No programmatic way to check if index exists or is working

**Proposed Enhancement**:
```python
from iris_devtester import FunctionalIndexHelper

helper = FunctionalIndexHelper(connection)

# Create Functional Index with automatic validation
result = helper.create_functional_index(
    table='rdf_edges',
    columns=['s', 'o_id'],
    index_class='Graph.KG.PPRFunctionalIndex',
    name='PPR_Adj',
    auto_purge=True,  # Automatically purge before rebuild
    auto_rebuild=True  # Automatically rebuild after creation
)

# Returns detailed status:
# {
#   'status': 'created' | 'already_exists' | 'error',
#   'class_compiled': True,
#   'index_created': True,
#   'global_populated': True,
#   'validation': {
#       'expected_methods': ['InsertIndex', 'DeleteIndex', 'UpdateIndex', 'PurgeIndex'],
#       'found_methods': [...],
#       'test_insert_worked': True,
#       'global_structure': {...}
#   },
#   'errors': []
# }

# Check if Functional Index exists and is working
if helper.validate_functional_index('PPR_Adj', table='rdf_edges'):
    print("Index is active and working")
```

## 3. Global Verification
**Problem**: No easy way to verify Globals are populated correctly
- Manual traversal with nextSubscript() is tedious
- No built-in diff/comparison tools
- Hard to validate Functional Index callbacks fired

**Proposed Enhancement**:
```python
from iris_devtester import GlobalInspector

inspector = GlobalInspector(connection)
stats = inspector.inspect_global('^PPR')
# Returns: {
#   'subscript_count': {'deg': 100, 'out': 500, 'in': 500},
#   'sample_data': [...],
#   'integrity_checks': {...}
# }

# Validate Functional Index worked
inspector.verify_functional_index_sync(
    table='rdf_edges',
    global_name='^PPR',
    expected_structure={'deg': 'COUNT(*)', 'out': 'EDGES', 'in': 'EDGES'}
)
```

## 4. Integration Test Fixtures
**Problem**: Setting up test data for database-dependent tests is manual
- Need to create nodes, edges, clean up after
- Schema assumptions (node_id vs id) cause failures
- No standardized test data fixtures

**Proposed Enhancement**:
```python
import pytest
from iris_devtester.fixtures import iris_test_data

@pytest.fixture
def sample_graph(iris_connection):
    with iris_test_data(iris_connection) as data:
        data.create_nodes(['TEST_A', 'TEST_B', 'TEST_C'])
        data.create_edges([
            ('TEST_A', 'interacts_with', 'TEST_B'),
            ('TEST_B', 'regulates', 'TEST_C')
        ])
        yield data
    # Auto-cleanup
```

## 5. IRIS Terminal Scripting
**Problem**: Running ObjectScript commands from Python is error-prone
- Heredoc syntax issues
- No output parsing
- Hard to detect errors

**Proposed Enhancement**:
```python
from iris_devtester import IRISTerminal

terminal = IRISTerminal(container='iris-pgwire-db', namespace='USER')
result = terminal.execute('''
    Do $SYSTEM.OBJ.Load("/tmp/MyClass.cls", "ck")
''')

assert result.success
print(result.output)
print(result.errors)
```

## 6. Index Rebuild Automation
**Problem**: After creating Functional Index, need to rebuild for existing data
- Manual SQL commands required
- No progress indication
- Hard to verify completion

**Proposed Enhancement**:
```python
from iris_devtester import IndexManager

mgr = IndexManager(connection)
mgr.rebuild_index(
    table='rdf_edges',
    index_name='PPR_Adj',
    progress_callback=lambda pct: print(f'{pct}% complete')
)
```

## 7. Schema Introspection
**Problem**: Column name mismatches (id vs node_id) cause test failures
- No easy way to get accurate schema from Python
- INFORMATION_SCHEMA queries verbose

**Proposed Enhancement**:
```python
from iris_devtester import SchemaInspector

schema = SchemaInspector(connection)
columns = schema.get_columns('nodes')
# Returns: [
#   {'name': 'node_id', 'type': 'varchar', 'nullable': False, 'pk': True},
#   {'name': 'created_at', 'type': 'timestamp', 'nullable': True}
# ]

# Auto-fix test queries
query = schema.adapt_query(
    "INSERT INTO nodes (id) VALUES (?)",  # Wrong column name
    table='nodes'
)
# Returns: "INSERT INTO nodes (node_id) VALUES (?)"
```

---

**Priority Ordering**:
1. **P0**: ObjectScript Class Deployment (#1) - Most painful
2. **P0**: Integration Test Fixtures (#4) - High ROI
3. **P1**: Global Verification (#3) - Debugging essential
4. **P1**: Schema Introspection (#7) - Prevents common errors
5. **P2**: Functional Index Creation (#2) - Specialized use case
6. **P2**: IRIS Terminal Scripting (#5) - Quality of life
7. **P3**: Index Rebuild Automation (#6) - Nice to have
