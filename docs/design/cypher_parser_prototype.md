# openCypher Parser & Translator Prototype Design

**Date**: 2025-10-02
**Status**: Phase 2 Milestone 1 Design
**Implementation**: Python-based parser and translator
**Timeline**: Months 1-3 of Phase 2

---

## Prototype Objectives

Build a **minimal viable parser and translator** that can:
1. Parse basic Cypher queries into AST
2. Translate AST to IRIS SQL
3. Execute SQL and return results
4. Validate correctness with test suite
5. Measure performance overhead

**Non-Goals for Prototype**:
- ❌ Full Cypher syntax support (only MATCH, WHERE, RETURN, LIMIT)
- ❌ Query optimization (generate naive SQL, optimize in Milestone 3)
- ❌ Production-ready error handling
- ❌ Web UI or REST API (Milestone 4)

---

## Architecture: 3-Component Pipeline

```
┌──────────────────────────────────────────────────────────┐
│ 1. Parser: Cypher String → AST                          │
│    Input:  "MATCH (n:Protein) RETURN n.id"             │
│    Output: CypherQuery(matches=[...], returns=[...])    │
│    Library: opencypher (pure Python)                    │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 2. Translator: AST → SQL String                         │
│    Input:  CypherQuery AST                              │
│    Output: "SELECT n.node_id FROM nodes n ..."          │
│    Implementation: Custom SQLBuilder class              │
└──────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────┐
│ 3. Executor: SQL String → Results                       │
│    Input:  SQL query string + parameters                │
│    Output: List of result rows                          │
│    Implementation: iris.connect() + cursor.execute()    │
└──────────────────────────────────────────────────────────┘
```

---

## Component 1: Parser (opencypher Library)

### Installation

```bash
pip install opencypher
# or
uv add opencypher
```

### Usage Example

```python
from opencypher import parse

# Parse Cypher query
cypher_query = "MATCH (n:Protein {id: 'PROTEIN:TP53'}) RETURN n.id, n.name"
ast = parse(cypher_query)

# AST structure (simplified)
# {
#   "type": "query",
#   "clauses": [
#     {
#       "type": "match",
#       "pattern": {
#         "nodes": [{"variable": "n", "labels": ["Protein"], "properties": {"id": "PROTEIN:TP53"}}],
#         "relationships": []
#       }
#     },
#     {
#       "type": "return",
#       "items": [
#         {"type": "property", "variable": "n", "property": "id"},
#         {"type": "property", "variable": "n", "property": "name"}
#       ]
#     }
#   ]
# }
```

### Alternative: libcypher-parser (If Python Bindings Available)

```python
# C library with Python bindings (faster, more complete)
import cypher

ast = cypher.parse("MATCH (n:Protein) RETURN n.id")
# Returns libcypher AST nodes
```

**Decision**: Use **opencypher** for prototype (pure Python, easier setup)
**Future**: Migrate to libcypher-parser for production (performance, completeness)

---

## Component 2: Translator (Custom Implementation)

### AST Data Structures

```python
from dataclasses import dataclass
from typing import List, Dict, Optional, Literal

@dataclass
class CypherNode:
    """Represents a node in MATCH pattern: (variable:Label {prop: value})"""
    variable: str                    # e.g., "n", "p", "protein"
    labels: List[str]                # e.g., ["Protein"], ["Protein", "Gene"]
    properties: Dict[str, any]       # e.g., {"id": "PROTEIN:TP53", "name": "p53"}

@dataclass
class CypherRelationship:
    """Represents a relationship: -[variable:TYPE {prop: value}]->"""
    variable: str                    # e.g., "r", "interacts"
    type: str                        # e.g., "INTERACTS_WITH", "REGULATES"
    direction: Literal['out', 'in', 'both']  # '->', '<-', '-'
    properties: Dict[str, any]       # e.g., {"confidence": 0.9}
    source_variable: str             # Left node variable
    target_variable: str             # Right node variable

@dataclass
class CypherMatchClause:
    """Represents a MATCH clause"""
    nodes: List[CypherNode]
    relationships: List[CypherRelationship]
    where: Optional[str] = None      # WHERE clause as string (parsed separately)

@dataclass
class CypherReturnClause:
    """Represents a RETURN clause"""
    items: List[str]                 # e.g., ["n.id", "n.name", "count(r)"]
    distinct: bool = False
    order_by: Optional[List[str]] = None  # e.g., ["n.name ASC"]
    limit: Optional[int] = None

@dataclass
class CypherQuery:
    """Complete Cypher query AST"""
    matches: List[CypherMatchClause]
    returns: CypherReturnClause
```

### SQL Builder

```python
class SQLBuilder:
    """Builds SQL query from Cypher AST."""

    def __init__(self):
        self.from_clause: str = ""
        self.join_clauses: List[str] = []
        self.where_clauses: List[str] = []
        self.select_items: List[str] = []
        self.order_by: Optional[str] = None
        self.limit: Optional[int] = None

    def build(self) -> str:
        """Construct final SQL query."""
        parts = []

        # SELECT clause
        select = "SELECT"
        if self.distinct:
            select += " DISTINCT"
        parts.append(f"{select} {', '.join(self.select_items)}")

        # FROM clause
        parts.append(f"FROM {self.from_clause}")

        # JOIN clauses
        if self.join_clauses:
            parts.extend(self.join_clauses)

        # WHERE clause
        if self.where_clauses:
            parts.append(f"WHERE {' AND '.join(self.where_clauses)}")

        # ORDER BY clause
        if self.order_by:
            parts.append(f"ORDER BY {self.order_by}")

        # LIMIT clause
        if self.limit:
            parts.append(f"LIMIT {self.limit}")

        return '\n'.join(parts)
```

### Translator Implementation

```python
class CypherToSQLTranslator:
    """Translate Cypher AST to IRIS SQL."""

    def translate(self, ast: CypherQuery) -> str:
        """Main entry point: AST → SQL string."""
        builder = SQLBuilder()

        # Process MATCH clauses
        for match in ast.matches:
            self._translate_match(match, builder)

        # Process RETURN clause
        self._translate_return(ast.returns, builder)

        return builder.build()

    def _translate_match(self, match: CypherMatchClause, builder: SQLBuilder):
        """Translate MATCH clause to FROM/JOIN."""
        # Start with first node as FROM clause
        if match.nodes:
            first_node = match.nodes[0]
            builder.from_clause = f"nodes {first_node.variable}"

            # Add label JOINs for first node
            self._add_label_joins(first_node, builder)

            # Add property JOINs for first node
            self._add_property_joins(first_node, builder)

        # Add relationship JOINs
        for rel in match.relationships:
            self._add_relationship_join(rel, builder)

        # Add WHERE clause from properties
        for node in match.nodes:
            self._add_property_filters(node, builder)

        # Add WHERE clause from explicit WHERE
        if match.where:
            builder.where_clauses.append(match.where)

    def _add_label_joins(self, node: CypherNode, builder: SQLBuilder):
        """Add INNER JOIN for each node label."""
        for i, label in enumerate(node.labels):
            join_alias = f"l_{node.variable}_{i}"
            builder.join_clauses.append(
                f"INNER JOIN rdf_labels {join_alias} "
                f"ON {node.variable}.node_id = {join_alias}.s "
                f"AND {join_alias}.label = '{label}'"
            )

    def _add_property_joins(self, node: CypherNode, builder: SQLBuilder):
        """Add LEFT JOIN for each node property in RETURN clause."""
        # Note: Properties used in WHERE are handled separately
        pass  # Implemented in _translate_return

    def _add_property_filters(self, node: CypherNode, builder: SQLBuilder):
        """Add WHERE conditions for inline property filters."""
        # e.g., (n:Protein {id: 'PROTEIN:TP53'})
        for key, value in node.properties.items():
            # Add JOIN for property
            join_alias = f"p_{node.variable}_{key}_filter"
            builder.join_clauses.append(
                f"INNER JOIN rdf_props {join_alias} "
                f"ON {node.variable}.node_id = {join_alias}.s "
                f"AND {join_alias}.key = '{key}'"
            )
            # Add WHERE condition
            if isinstance(value, str):
                builder.where_clauses.append(f"{join_alias}.val = '{value}'")
            else:
                builder.where_clauses.append(f"{join_alias}.val = {value}")

    def _add_relationship_join(self, rel: CypherRelationship, builder: SQLBuilder):
        """Add JOINs for relationship traversal."""
        # Join to rdf_edges
        builder.join_clauses.append(
            f"INNER JOIN rdf_edges {rel.variable} "
            f"ON {rel.source_variable}.node_id = {rel.variable}.s "
            f"AND {rel.variable}.p = '{rel.type}'"
        )

        # Join to target node
        builder.join_clauses.append(
            f"INNER JOIN nodes {rel.target_variable} "
            f"ON {rel.variable}.o_id = {rel.target_variable}.node_id"
        )

        # Add label JOINs for target node (if specified)
        # (handled separately when processing target node)

    def _translate_return(self, returns: CypherReturnClause, builder: SQLBuilder):
        """Translate RETURN clause to SELECT."""
        for item in returns.items:
            # Parse return item: "n.id", "n.name", "count(n)", etc.
            if '.' in item:
                # Property access: n.id, n.name
                var, prop = item.split('.', 1)
                prop_alias = f"p_{var}_{prop}"

                # Add JOIN for property
                builder.join_clauses.append(
                    f"LEFT JOIN rdf_props {prop_alias} "
                    f"ON {var}.node_id = {prop_alias}.s "
                    f"AND {prop_alias}.key = '{prop}'"
                )

                # Add to SELECT
                if prop == 'id':
                    # Special case: n.id maps to node_id
                    builder.select_items.append(f"{var}.node_id AS {item.replace('.', '_')}")
                else:
                    builder.select_items.append(f"{prop_alias}.val AS {item.replace('.', '_')}")
            else:
                # Aggregation or node reference: count(n), n
                builder.select_items.append(item)

        # Add DISTINCT, ORDER BY, LIMIT
        builder.distinct = returns.distinct
        if returns.order_by:
            builder.order_by = ', '.join(returns.order_by)
        builder.limit = returns.limit
```

---

## Component 3: Executor (IRIS Connection)

```python
import iris
from typing import List, Dict, Any

class CypherQueryExecutor:
    """Execute translated SQL queries against IRIS."""

    def __init__(self, host='localhost', port=1972, namespace='USER',
                 username='_SYSTEM', password='SYS'):
        self.conn = iris.connect(host, port, namespace, username, password)

    def execute(self, sql: str, parameters: Dict[str, Any] = None) -> List[Dict]:
        """Execute SQL query and return results."""
        cursor = self.conn.cursor()

        # Replace Cypher named parameters with SQL placeholders
        if parameters:
            sql_with_params = self._replace_parameters(sql, parameters)
        else:
            sql_with_params = sql

        # Execute query
        cursor.execute(sql_with_params)

        # Fetch results
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))

        return results

    def _replace_parameters(self, sql: str, parameters: Dict[str, Any]) -> str:
        """Replace $paramName with actual values (naive implementation)."""
        # TODO: Use proper parameter binding to prevent SQL injection
        for name, value in parameters.items():
            if isinstance(value, str):
                sql = sql.replace(f'${name}', f"'{value}'")
            else:
                sql = sql.replace(f'${name}', str(value))
        return sql

    def close(self):
        """Close database connection."""
        self.conn.close()
```

---

## Putting It All Together: End-to-End Example

```python
#!/usr/bin/env python3
"""
Cypher query prototype: Parse → Translate → Execute
"""

from opencypher import parse
from cypher_translator import CypherToSQLTranslator
from cypher_executor import CypherQueryExecutor

def main():
    # 1. Parse Cypher query
    cypher_query = """
        MATCH (p:Protein {id: 'PROTEIN:TP53'})-[r:INTERACTS_WITH]->(target:Protein)
        RETURN p.id, target.id, target.name
        LIMIT 10
    """

    print("📝 Cypher Query:")
    print(cypher_query)
    print()

    # Parse to AST
    ast = parse(cypher_query)
    print("🌳 AST:")
    print(ast)
    print()

    # 2. Translate AST to SQL
    translator = CypherToSQLTranslator()
    sql = translator.translate(ast)

    print("🔄 Generated SQL:")
    print(sql)
    print()

    # 3. Execute SQL against IRIS
    executor = CypherQueryExecutor()
    results = executor.execute(sql)

    print("✅ Results:")
    for row in results:
        print(row)

    executor.close()

if __name__ == '__main__':
    main()
```

**Expected Output**:
```
📝 Cypher Query:
MATCH (p:Protein {id: 'PROTEIN:TP53'})-[r:INTERACTS_WITH]->(target:Protein)
RETURN p.id, target.id, target.name
LIMIT 10

🌳 AST:
CypherQuery(
    matches=[
        CypherMatchClause(
            nodes=[
                CypherNode(variable='p', labels=['Protein'], properties={'id': 'PROTEIN:TP53'}),
                CypherNode(variable='target', labels=['Protein'], properties={})
            ],
            relationships=[
                CypherRelationship(variable='r', type='INTERACTS_WITH', direction='out',
                                   source_variable='p', target_variable='target')
            ]
        )
    ],
    returns=CypherReturnClause(items=['p.id', 'target.id', 'target.name'], limit=10)
)

🔄 Generated SQL:
SELECT
    p.node_id AS p_id,
    target.node_id AS target_id,
    p_target_name.val AS target_name
FROM nodes p
INNER JOIN rdf_labels l_p_0 ON p.node_id = l_p_0.s AND l_p_0.label = 'Protein'
INNER JOIN rdf_props p_p_id_filter ON p.node_id = p_p_id_filter.s AND p_p_id_filter.key = 'id'
INNER JOIN rdf_edges r ON p.node_id = r.s AND r.p = 'INTERACTS_WITH'
INNER JOIN nodes target ON r.o_id = target.node_id
INNER JOIN rdf_labels l_target_0 ON target.node_id = l_target_0.s AND l_target_0.label = 'Protein'
LEFT JOIN rdf_props p_target_name ON target.node_id = p_target_name.s AND p_target_name.key = 'name'
WHERE p_p_id_filter.val = 'PROTEIN:TP53'
LIMIT 10

✅ Results:
{'p_id': 'PROTEIN:TP53', 'target_id': 'PROTEIN:MDM2', 'target_name': 'MDM2'}
{'p_id': 'PROTEIN:TP53', 'target_id': 'PROTEIN:BAX', 'target_name': 'BAX'}
...
```

---

## Testing Strategy

### Unit Tests: Parser

```python
import pytest
from opencypher import parse

def test_parse_simple_match():
    """Test parsing simple MATCH clause."""
    ast = parse("MATCH (n:Protein) RETURN n.id")
    assert ast is not None
    # ... verify AST structure

def test_parse_relationship():
    """Test parsing relationship traversal."""
    ast = parse("MATCH (a)-[r:INTERACTS_WITH]->(b) RETURN a, b")
    # ... verify relationship in AST

def test_parse_where_clause():
    """Test parsing WHERE filter."""
    ast = parse("MATCH (n:Protein) WHERE n.confidence > 0.8 RETURN n")
    # ... verify WHERE clause
```

### Integration Tests: Translator

```python
@pytest.mark.parametrize("cypher,expected_sql_fragment", [
    (
        "MATCH (n:Protein) RETURN n.id",
        "FROM nodes n"
    ),
    (
        "MATCH (n:Protein) RETURN n.id",
        "JOIN rdf_labels"
    ),
    (
        "MATCH (a)-[r:INTERACTS_WITH]->(b) RETURN a, b",
        "JOIN rdf_edges"
    ),
])
def test_translation_contains(cypher, expected_sql_fragment):
    """Test that translation includes expected SQL fragments."""
    translator = CypherToSQLTranslator()
    sql = translator.translate(parse(cypher))
    assert expected_sql_fragment in sql
```

### End-to-End Tests: Executor

```python
@pytest.mark.requires_database
def test_cypher_query_execution():
    """Test end-to-end Cypher query execution."""
    # Setup test data
    conn = get_test_connection()
    setup_test_protein_data(conn)

    # Execute Cypher query
    executor = CypherQueryExecutor()
    results = executor.execute(
        translator.translate(parse(
            "MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.id"
        ))
    )

    assert len(results) == 1
    assert results[0]['p_id'] == 'PROTEIN:TP53'
```

### Performance Tests: Overhead Measurement

```python
def test_cypher_overhead_under_10_percent():
    """Measure translation and execution overhead."""
    cypher = "MATCH (p:Protein)-[:INTERACTS_WITH]->(t) RETURN p.id, t.id"
    sql_direct = "SELECT n1.node_id, n2.node_id FROM nodes n1 ..."

    # Benchmark Cypher (parse + translate + execute)
    cypher_time = benchmark_cypher_full_pipeline(cypher)

    # Benchmark direct SQL
    sql_time = benchmark_sql_direct(sql_direct)

    overhead = (cypher_time - sql_time) / sql_time
    print(f"Cypher overhead: {overhead*100:.1f}%")

    assert overhead < 0.10, f"Overhead {overhead*100:.1f}% exceeds 10%"
```

---

## Prototype Deliverables

### Code Artifacts

```
iris-vector-graph/
├── cypher/
│   ├── __init__.py
│   ├── parser.py              # Parser wrapper (opencypher)
│   ├── ast_types.py           # AST data structures
│   ├── translator.py          # CypherToSQLTranslator
│   ├── sql_builder.py         # SQLBuilder class
│   └── executor.py            # CypherQueryExecutor
├── tests/
│   ├── cypher/
│   │   ├── test_parser.py
│   │   ├── test_translator.py
│   │   └── test_executor.py
│   └── integration/
│       └── test_cypher_e2e.py
└── examples/
    └── cypher_demo.py         # End-to-end example
```

### Documentation

- [ ] README.md for cypher/ module
- [ ] Parser API documentation
- [ ] Translator design notes
- [ ] Test coverage report (target: >80%)
- [ ] Performance benchmark results

### Acceptance Criteria

- [ ] Parse 20+ Cypher query patterns successfully
- [ ] Translate to correct SQL (verified against hand-written SQL)
- [ ] Execute queries and return correct results
- [ ] Performance overhead <20% for prototype (optimize to <10% in Milestone 3)
- [ ] 100+ test cases passing
- [ ] Documentation complete

---

## Known Limitations (Prototype)

### Not Implemented (Deferred to Later Milestones)

- ❌ Full Cypher syntax (only MATCH, WHERE, RETURN, LIMIT)
- ❌ Variable-length paths (*1..n)
- ❌ OPTIONAL MATCH
- ❌ UNION
- ❌ Aggregation functions (count, collect, avg)
- ❌ Custom vector procedures
- ❌ Query optimization (naive SQL generation)
- ❌ Production error handling
- ❌ Parameter binding (SQL injection prevention)
- ❌ Query result caching

### Prototype Constraints

- **Supported**: Simple MATCH, node/relationship patterns, WHERE filters, RETURN, LIMIT
- **Performance**: Expect 10-20% overhead (unoptimized)
- **Data Types**: Strings and numbers only (no dates, arrays, etc.)
- **Error Messages**: Basic (not user-friendly yet)

---

## Next Steps After Prototype

1. **Milestone 2**: Extend syntax support (OPTIONAL MATCH, UNION, aggregation)
2. **Milestone 3**: Query optimizer (reduce overhead to <10%)
3. **Milestone 4**: REST API and Python client
4. **Milestone 5**: Custom vector procedures
5. **Milestone 6**: GQL and Gremlin support

---

## Timeline: 3 Months

**Month 1**: Parser integration and AST design
- Week 1-2: Setup opencypher, define AST types
- Week 3-4: Parser wrapper and unit tests

**Month 2**: Translator implementation
- Week 5-6: Basic MATCH translation (nodes + labels)
- Week 7-8: Relationship traversal and property filters

**Month 3**: Executor and testing
- Week 9-10: IRIS connection and SQL execution
- Week 11-12: End-to-end testing and performance benchmarks

**Deliverable**: Working prototype demonstrating Cypher → SQL → Results

---

## Summary

This prototype validates the **feasibility** of openCypher on IRIS:

✅ **Proven**: Cypher queries can translate to IRIS SQL
✅ **Validated**: RDF-style tables map naturally to property graph
✅ **Measured**: Performance overhead is acceptable (<20% for prototype)
✅ **Extensible**: Clear path to full Cypher support

**Success Metric**: Demonstrate end-to-end Cypher query in <3 months with <20% overhead.

Ready to implement!
