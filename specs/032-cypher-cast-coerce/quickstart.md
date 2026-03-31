# Quickstart: CAST Functions

```cypher
-- Integer filtering
MATCH (g:Gene) WHERE toInteger(g.chromosome) IN [1, 7, 17] RETURN g.name

-- Float comparison
MATCH (d:Drug) WHERE toFloat(d.confidence) > 0.9 RETURN d.name, d.confidence

-- String conversion
MATCH (n) RETURN toString(n.count) + ' occurrences' AS label

-- Boolean normalization
MATCH (n:Trial) WHERE toBoolean(n.active) = 1 RETURN n.id

-- Deduplicated count
MATCH (p:Patient)-[:HAS_ICD]->(icd) RETURN icd.code, COUNT(DISTINCT p) AS patients
```
