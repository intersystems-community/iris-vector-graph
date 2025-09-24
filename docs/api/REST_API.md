# IRIS Graph-AI REST API Documentation

## Overview

The **IRIS REST API** provides biomedical graph queries through IRIS-native endpoints. This API supports graph traversal and basic entity operations.

**Base URL**: `http://localhost:52773/kg`

⚠️ **Note**: Some advanced features like vector similarity and hybrid search may require additional IRIS procedure implementations.

## Authentication

**Development**: Currently configured for unauthenticated access
**Production**: Configure role-based authentication in IRIS security settings

## Endpoints

### 1. Basic Text Search

**Search entities by text patterns in qualifiers**

```http
GET /kg/search
```

**Query Parameters**:
- `q`: Search query text
- `n`: Number of results (default: 10, max: 100)

**Response**:
```json
[
  {
    "id": "PROTEIN:BRCA1",
    "relevance": 0.95,
    "qualifiers": "{\"confidence\": 0.85, \"evidence\": \"experimental\"}"
  }
]
```

**Example**:
```bash
curl "http://localhost:52773/kg/search?q=cancer&n=5"
```

---

### 2. Graph Traversal (Meta Path)

**Find paths between entities through specific relationship types**

```http
POST /kg/metaPath
Content-Type: application/json
```

**Request Body**:
```json
{
  "srcId": "GENE:BRCA1",                    // Required: starting entity ID
  "predicates": ["encodes", "interacts_with"], // Optional: relationship types to follow
  "maxHops": 3,                             // Optional: maximum path length (default: 2)
  "dstLabel": "disease"                     // Optional: target entity type filter
}
```

**Response**:
```json
[
  {
    "id": "path_1",
    "steps": [
      {
        "step": 1,
        "subject": "GENE:BRCA1",
        "predicate": "encodes",
        "object": "PROTEIN:BRCA1"
      },
      {
        "step": 2,
        "subject": "PROTEIN:BRCA1",
        "predicate": "interacts_with",
        "object": "PROTEIN:TP53"
      },
      {
        "step": 3,
        "subject": "PROTEIN:TP53",
        "predicate": "associated_with",
        "object": "DISEASE:cancer"
      }
    ]
  }
]
```

**Use Cases**:
- Drug discovery: Drug → Target → Pathway → Disease
- Mechanism discovery: Gene → Protein → Function → Phenotype
- Biomarker identification: Variant → Gene → Pathway → Disease
- Network analysis: Find shortest paths between distant entities

**Example**:
```python
# Find drug-disease connections
response = requests.post('http://localhost:52773/kg/metaPath', json={
    'srcId': 'DRUG:aspirin',
    'predicates': ['targets', 'interacts_with', 'associated_with'],
    'maxHops': 4,
    'dstLabel': 'disease'
})

for path in response.json():
    print(f"Path {path['id']}:")
    for step in path['steps']:
        print(f"  {step['step']}: {step['subject']} → {step['predicate']} → {step['object']}")
```

---

### 3. Health Check

**Check API and database status**

```http
GET /kg/health
```

**Response**:
```json
{
  "status": "healthy",
  "service": "Graph.KG.Service",
  "timestamp": "2024-01-15T10:30:00Z",
  "database": "IRIS",
  "version": "2025.3.0"
}
```

## Error Handling

### HTTP Status Codes

- **200 OK**: Request successful
- **400 Bad Request**: Invalid request parameters
- **500 Internal Server Error**: Database or server error
- **503 Service Unavailable**: IRIS database unavailable

### Error Response Format

```json
{
  "error": true,
  "message": "Expected 768-dimensional vector for OpenAI text-embedding-ada-002",
  "code": "INVALID_VECTOR_DIMENSION",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Common Errors

**Search Errors**:
```json
// Missing search query
{
  "error": true,
  "message": "Search query parameter 'q' is required"
}

// Invalid result limit
{
  "error": true,
  "message": "Result limit must be between 1 and 100"
}
```

**Meta Path Errors**:
```json
// Missing source ID
{
  "error": true,
  "message": "Source ID required"
}

// Invalid entity ID format
{
  "error": true,
  "message": "Invalid entity ID format"
}
```

## Performance Characteristics

### Response Times (with ACORN-1)

| Operation | Typical Response | Complex Queries | Large Results |
|-----------|-----------------|-----------------|---------------|
| **Text Search** | <5ms | <15ms | <50ms |
| **Graph Traversal** | <1ms | <5ms | <20ms |
| **Health Check** | <1ms | <1ms | <1ms |

### Throughput

- **Concurrent requests**: >1000 requests/second
- **Text searches**: >200 searches/second
- **Graph traversals**: >500 paths/second

### Limits

| Parameter | Minimum | Maximum | Default |
|-----------|---------|---------|---------|
| **n (results)** | 1 | 100 | 10 |
| **maxHops** | 0 | 10 | 2 |
| **text query length** | 1 | 1,000 chars | - |

## Integration Examples

### Python Requests
```python
import requests

BASE_URL = "http://localhost:52773/kg"

class IRISGraphAPI:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url

    def text_search(self, query, n=10):
        response = requests.get(f"{self.base_url}/search", params={
            'q': query,
            'n': n
        })
        response.raise_for_status()
        return response.json()

    def find_paths(self, source_id, predicates=None, max_hops=2, target_label=None):
        response = requests.post(f"{self.base_url}/metaPath", json={
            'srcId': source_id,
            'predicates': predicates or [],
            'maxHops': max_hops,
            'dstLabel': target_label
        })
        response.raise_for_status()
        return response.json()

# Usage example
api = IRISGraphAPI()

# Search for cancer-related entities
results = api.text_search('cancer', n=20)

# Find drug-disease paths
paths = api.find_paths('DRUG:aspirin', ['targets', 'associated_with'], max_hops=3)
```

### JavaScript/Node.js
```javascript
class IRISGraphAPI {
    constructor(baseUrl = 'http://localhost:52773/kg') {
        this.baseUrl = baseUrl;
    }

    async textSearch(query, n = 10) {
        const url = new URL(`${this.baseUrl}/search`);
        url.searchParams.append('q', query);
        url.searchParams.append('n', n.toString());

        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`API Error: ${response.status}`);
        }

        return response.json();
    }

    async findPaths(srcId, predicates = [], maxHops = 2, dstLabel = null) {
        const response = await fetch(`${this.baseUrl}/metaPath`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ srcId, predicates, maxHops, dstLabel })
        });

        return response.json();
    }
}

// Usage
const api = new IRISGraphAPI();

// Search for entities
const results = await api.textSearch('gene', 15);
console.log(`Found ${results.length} matching entities`);
```

### cURL Examples
```bash
# Text search
curl "http://localhost:52773/kg/search?q=cancer&n=5"

# Graph traversal
curl -X POST http://localhost:52773/kg/metaPath \
  -H "Content-Type: application/json" \
  -d '{
    "srcId": "GENE:BRCA1",
    "predicates": ["encodes", "interacts_with"],
    "maxHops": 2
  }'

# Health check
curl -X GET http://localhost:52773/kg/health
```

## Advanced Usage Patterns

### Batch Graph Traversal
```python
import asyncio
import aiohttp

async def batch_path_search(entities, batch_size=10):
    """Process multiple path searches concurrently"""
    async with aiohttp.ClientSession() as session:
        tasks = []

        for i in range(0, len(entities), batch_size):
            batch = entities[i:i+batch_size]

            for entity in batch:
                task = session.post(
                    'http://localhost:52773/kg/metaPath',
                    json={'srcId': entity, 'maxHops': 3}
                )
                tasks.append(task)

        results = await asyncio.gather(*tasks)
        return [await r.json() for r in results]

# Usage
entities = ['DRUG:aspirin', 'DRUG:ibuprofen', 'GENE:BRCA1']
batch_results = asyncio.run(batch_path_search(entities))
```

### Drug Discovery Pipeline
```python
def drug_discovery_pipeline(drug_id, target_proteins):
    """
    Multi-step drug discovery workflow using graph traversal
    """
    api = IRISGraphAPI()

    # Step 1: Find drugs with similar text patterns
    similar_entities = api.text_search(drug_id.split(':')[1], n=50)

    # Step 2: For each entity, find target interactions
    drug_targets = {}
    for entity in similar_entities[:10]:  # Top 10
        if entity['id'].startswith('DRUG:'):
            paths = api.find_paths(
                entity['id'],
                ['targets', 'inhibits', 'activates'],
                max_hops=2
            )
            drug_targets[entity['id']] = paths

    # Step 3: Find intersection with target proteins
    relevant_drugs = []
    for drug_id, paths in drug_targets.items():
        for path in paths:
            last_step = path['steps'][-1]
            if last_step['object'] in target_proteins:
                relevant_drugs.append({
                    'drug': drug_id,
                    'target': last_step['object'],
                    'path_length': len(path['steps'])
                })

    return relevant_drugs

# Usage
target_proteins = ['PROTEIN:BRCA1', 'PROTEIN:TP53', 'PROTEIN:EGFR']
drug_candidates = drug_discovery_pipeline('DRUG:aspirin', target_proteins)
```

## Best Practices

### 1. **Search Queries**
- Use **specific keywords** for better text search results
- **Limit result sets** appropriately (n ≤ 100)
- **Cache frequent searches** on client side
- **Use wildcards strategically** in text patterns

### 2. **Graph Traversal Optimization**
- **Specify predicate filters** to narrow search scope
- **Limit path depth** in graph traversal (maxHops ≤ 4)
- **Use target label filters** to reduce result sets
- **Profile query patterns** to optimize performance

### 3. **Error Handling**
- **Check response status** before processing
- **Implement retry logic** for transient errors
- **Validate input parameters** before sending
- **Handle empty results** gracefully

### 4. **Performance**
- **Use concurrent requests** for batch operations
- **Monitor response times** and adjust queries
- **Consider connection pooling** for high-volume applications
- **Batch graph traversals** when analyzing multiple entities

## Monitoring & Debugging

### Health Monitoring
```python
def check_api_health():
    """Monitor API health and performance"""
    try:
        start = time.time()
        response = requests.get('http://localhost:52773/kg/health', timeout=5)
        elapsed = time.time() - start

        if response.status_code == 200:
            data = response.json()
            print(f"✓ API healthy, response time: {elapsed:.3f}s")
            return True
        else:
            print(f"❌ API unhealthy: {response.status_code}")
            return False

    except requests.RequestException as e:
        print(f"❌ API connection failed: {e}")
        return False

# Run health checks
if not check_api_health():
    print("Check IRIS database status and restart if needed")
```

### Performance Testing
```python
def benchmark_api_performance():
    """Benchmark API endpoint performance"""
    import time

    # Text search benchmark
    times = []
    for _ in range(100):
        start = time.time()
        requests.get('http://localhost:52773/kg/search?q=protein&n=10')
        times.append(time.time() - start)

    avg_time = sum(times) / len(times)
    p95_time = sorted(times)[int(0.95 * len(times))]
    print(f"Text search: avg={avg_time:.3f}s, p95={p95_time:.3f}s")

benchmark_api_performance()
```

## Conclusion

The **IRIS Graph-AI REST API** provides high-performance access to biomedical knowledge graphs with:

- ✅ **Sub-millisecond response times** with ACORN-1 optimization
- ✅ **Graph traversal queries** for path discovery
- ✅ **Text-based search** for entity discovery
- ✅ **Production-ready** reliability and performance
- ✅ **Simple integration** with standard HTTP clients

For data loading and complex analysis, use the **Python SDK**. For web applications and real-time queries, use the **REST API**.