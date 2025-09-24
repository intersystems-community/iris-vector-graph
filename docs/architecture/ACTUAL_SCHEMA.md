# IRIS Graph-AI Actual Database Schema

## Overview

This document describes the **actual** database schema and capabilities as discovered through testing, versus what is documented in examples.

## 🔍 Schema Validation Results

### ✅ **Tables That Exist**

| Table | Purpose | Structure | Status |
|-------|---------|-----------|--------|
| **`rdf_edges`** | Graph relationships | `(id, s, p, o_id, qualifiers)` | ✅ **Working** |
| **`rdf_labels`** | Entity type labels | `(s, label)` | ✅ **Working** |
| **`rdf_props`** | Entity properties | `(s, key, val)` | ✅ **Working** |
| **`kg_NodeEmbeddings`** | Vector embeddings | Unknown structure | ✅ **Exists** |

### ❌ **Missing Capabilities**

| Feature | Documentation Claims | Reality | Impact |
|---------|---------------------|---------|--------|
| **Vector Functions** | `VECTOR_COSINE()`, `TO_VECTOR()` | ❌ **Don't exist** | Vector similarity examples are broken |
| **JSON Functions** | `JSON_VALUE()`, `JSON_EXTRACT()` | ❌ **Don't exist** | Qualifier parsing examples are broken |
| **Vector Procedures** | `kg_KNN_VEC()`, `kg_RRF_FUSE()` | ❌ **Don't exist** | Python SDK examples are broken |
| **Custom Procedures** | `FindShortestPath()` | ❌ **Don't exist** | Advanced graph queries are broken |

## 📊 **Actual Table Structures**

### `rdf_edges` Table
```sql
-- Actual structure discovered
CREATE TABLE rdf_edges (
    id INTEGER,           -- Auto-increment ID
    s VARCHAR,           -- Subject (source entity)
    p VARCHAR,           -- Predicate (relationship type)
    o_id VARCHAR,        -- Object (target entity)
    qualifiers VARCHAR   -- JSON-formatted metadata
);
```

**Sample Data:**
```
(1, 'protein:9606.ENSP00000000233', 'interacts_with', 'protein:9606.ENSP00000354878', '{"confidence": 513}')
(2, 'protein:9606.ENSP00000000233', 'interacts_with', 'protein:9606.ENSP00000310226', '{"confidence": 648}')
```

### `rdf_labels` Table
```sql
-- Entity type classifications
CREATE TABLE rdf_labels (
    s VARCHAR,           -- Subject (entity ID)
    label VARCHAR        -- Entity type (protein, drug, disease, etc.)
);
```

### `rdf_props` Table
```sql
-- Entity properties and attributes
CREATE TABLE rdf_props (
    s VARCHAR,           -- Subject (entity ID)
    key VARCHAR,         -- Property name
    val VARCHAR          -- Property value
);
```

### `kg_NodeEmbeddings` Table
```sql
-- Vector embeddings (structure unknown)
-- EXISTS but structure not validated
```

## ✅ **SQL Patterns That Actually Work**

### 1. **Basic Graph Traversal**
```sql
-- Multi-hop joins work correctly
SELECT e1.s as drug, e2.o_id as protein, e3.o_id as disease
FROM rdf_edges e1
JOIN rdf_edges e2 ON e1.o_id = e2.s
JOIN rdf_edges e3 ON e2.o_id = e3.s
WHERE e1.s = 'DRUG:aspirin'
  AND e1.p = 'targets'
  AND e2.p = 'interacts_with'
  AND e3.p = 'associated_with';
```

### 2. **Entity Counting and Aggregation**
```sql
-- Hub protein identification
SELECT s as protein, COUNT(*) as connections
FROM rdf_edges
WHERE p = 'interacts_with'
  AND s LIKE 'protein:%'
GROUP BY s
ORDER BY connections DESC
LIMIT 20;
```

### 3. **Label-based Filtering**
```sql
-- Find entities by type
SELECT l.s, l.label
FROM rdf_labels l
WHERE l.label = 'protein'
LIMIT 100;
```

## ❌ **SQL Patterns That DON'T Work**

### 1. **Vector Similarity (Documentation is Wrong)**
```sql
-- BROKEN: These functions don't exist
SELECT TOP 10 id,
       VECTOR_COSINE(embedding, ?) as similarity_score
FROM kg_NodeEmbeddings
WHERE label = 'protein'
ORDER BY similarity_score DESC;
```

### 2. **JSON Qualifier Extraction (Documentation is Wrong)**
```sql
-- BROKEN: JSON functions don't exist
SELECT s, JSON_VALUE(qualifiers, '$.confidence') as confidence
FROM rdf_edges
WHERE JSON_VALUE(qualifiers, '$.confidence') > 0.7;
```

### 3. **Advanced Graph Functions (Documentation is Wrong)**
```sql
-- BROKEN: Custom procedures don't exist
CALL FindShortestPath('DRUG:aspirin', 'DISEASE:cancer', 'targets|interacts_with');
```

### 4. **Recursive CTEs (May Not Be Supported)**
```sql
-- UNCERTAIN: May not work in IRIS
WITH RECURSIVE pathway(...) AS (
  -- Complex recursive queries
)
SELECT * FROM pathway;
```

## 🔧 **Working Alternatives**

### 1. **Manual JSON Parsing**
Since `JSON_VALUE()` doesn't exist, we need manual parsing:
```sql
-- Alternative: Use IRIS string functions or application-level parsing
SELECT s, qualifiers
FROM rdf_edges
WHERE qualifiers LIKE '%"confidence"%';
```

### 2. **Application-Level Vector Search**
Since vector functions don't exist in SQL:
```python
# Use IRIS Python embedding or external vector libraries
# Process vectors in application layer, not SQL
```

### 3. **Iterative Path Finding**
Since recursive CTEs may not work:
```python
# Implement graph traversal in Python
def find_paths(start, end, max_hops=3):
    # Iterative breadth-first search
    pass
```

## 📋 **Documentation Fix Requirements**

### **Critical Issues to Fix:**

1. **Remove ALL vector similarity SQL examples** until functions are implemented
2. **Remove ALL JSON extraction SQL examples** until functions are implemented
3. **Remove ALL stored procedure calls** until procedures are implemented
4. **Update Python SDK examples** to remove non-existent procedure calls
5. **Replace broken examples** with working alternatives

### **Working Examples to Keep:**

1. ✅ Basic graph joins and traversal
2. ✅ Entity counting and aggregation
3. ✅ Label-based filtering
4. ✅ Property-based searches
5. ✅ Basic IRIS connection patterns

### **Examples That Need Alternative Implementations:**

1. **Vector similarity** → External libraries or IRIS Python embedding
2. **JSON parsing** → Application-level processing
3. **Advanced graph algorithms** → NetworkX integration
4. **Hybrid search** → Combine external tools

## 🎯 **Recommendations**

### **Immediate Actions:**
1. **Remove broken examples** from all documentation
2. **Replace with working patterns** that actually function
3. **Implement missing procedures** if vector/JSON capabilities exist elsewhere
4. **Add schema documentation** showing actual table structures

### **Future Development:**
1. **Implement vector functions** as IRIS stored procedures
2. **Add JSON processing** capabilities to IRIS
3. **Create custom graph algorithms** as procedures
4. **Build hybrid search** combining IRIS + external tools

This analysis reveals significant gaps between documented capabilities and actual implementation, requiring immediate documentation fixes to maintain credibility.