# IRIS Graph Python SDK

## Overview

The **IRIS Graph Python SDK** provides direct, high-performance access to biomedical knowledge graphs stored in InterSystems IRIS. This is the **primary interface** for data scientists and researchers working with large-scale biomedical data.

## Why Python SDK?

### **Performance Advantages**
- **Direct IRIS connection** - No HTTP overhead
- **Native SQL execution** - Full IRIS query power
- **Batch processing** - Handle millions of entities efficiently
- **Concurrent operations** - Multi-threaded data processing

### **Research-Friendly**
- **NetworkX integration** - Standard graph tools
- **Pandas compatibility** - DataFrame workflows
- **Jupyter notebooks** - Interactive analysis
- **NumPy vectors** - Efficient embedding operations

## Installation

### Prerequisites
```bash
# Install IRIS Python driver
pip install intersystems_irispython

# Install scientific computing stack
pip install numpy pandas networkx python-dotenv

# Optional: Visualization and ML
pip install matplotlib seaborn scikit-learn
```

### IRIS Database Setup
```bash
# Start IRIS with ACORN-1 (recommended)
docker-compose -f docker-compose.acorn.yml up -d

# Or start Community Edition
docker-compose up -d
```

## Quick Start

### 1. Basic Connection
```python
import iris
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Connect to IRIS
conn = iris.connect(
    hostname='localhost',
    port=1973,
    namespace='USER',
    username='_SYSTEM',
    password='SYS'
)

print("✓ Connected to IRIS Graph-AI")
```

### 2. Simple Graph Query
```python
# Find protein interactions
cursor = conn.cursor()
cursor.execute("""
    SELECT s, p, o_id
    FROM rdf_edges
    WHERE s = ? AND p = 'interacts_with'
    LIMIT 10
""", ['PROTEIN:BRCA1'])

interactions = cursor.fetchall()
for subject, predicate, object_id in interactions:
    print(f"{subject} → {predicate} → {object_id}")
```

### 3. Entity Discovery by Type
```python
# Find entities by label
cursor.execute("""
    SELECT s, label
    FROM rdf_labels
    WHERE label = ?
    LIMIT 10
""", ['protein'])

entities = cursor.fetchall()
for entity_id, label in entities:
    print(f"{entity_id} ({label})")
```

## Advanced Usage

### 1. Bulk Data Loading
```python
def bulk_insert_proteins(conn, protein_data):
    """
    Efficiently insert thousands of proteins with properties
    """
    cursor = conn.cursor()

    # Prepare batch insert
    insert_sql = """
        INSERT INTO rdf_props (s, key, val)
        VALUES (?, ?, ?)
    """

    # Process in batches for memory efficiency
    batch_size = 5000
    for i in range(0, len(protein_data), batch_size):
        batch = protein_data[i:i + batch_size]

        # Prepare batch data
        batch_data = []
        for protein in batch:
            batch_data.extend([
                (protein['id'], 'name', protein['name']),
                (protein['id'], 'organism', protein['organism']),
                (protein['id'], 'function', protein.get('function', ''))
            ])

        # Execute batch
        cursor.executemany(insert_sql, batch_data)
        print(f"Inserted batch {i//batch_size + 1}, proteins {i+1}-{min(i+batch_size, len(protein_data))}")

    cursor.close()
    print(f"✓ Loaded {len(protein_data)} proteins")

# Example usage
proteins = [
    {'id': 'PROTEIN:P53', 'name': 'TP53', 'organism': 'Homo sapiens', 'function': 'tumor suppressor'},
    {'id': 'PROTEIN:BRCA1', 'name': 'BRCA1', 'organism': 'Homo sapiens', 'function': 'DNA repair'},
    # ... thousands more
]

bulk_insert_proteins(conn, proteins)
```

### 2. NetworkX Integration
```python
import networkx as nx

def load_networkx_graph(conn, entity_type='protein'):
    """
    Load IRIS graph data into NetworkX for analysis
    """
    cursor = conn.cursor()

    # Get edges
    cursor.execute("""
        SELECT DISTINCT s, o_id, p
        FROM rdf_edges e
        JOIN rdf_labels l1 ON e.s = l1.s
        JOIN rdf_labels l2 ON e.o_id = l2.s
        WHERE l1.label = ? AND l2.label = ?
    """, [entity_type, entity_type])

    # Build NetworkX graph
    G = nx.DiGraph()
    for source, target, relation in cursor.fetchall():
        G.add_edge(source, target, relation=relation)

    cursor.close()
    return G

# Load protein interaction network
protein_graph = load_networkx_graph(conn, 'protein')
print(f"Loaded graph: {protein_graph.number_of_nodes()} nodes, {protein_graph.number_of_edges()} edges")

# NetworkX analysis
centrality = nx.degree_centrality(protein_graph)
top_proteins = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:10]

for protein, score in top_proteins:
    print(f"{protein}: {score:.3f}")
```

### 3. Multi-hop Graph Analysis
```python
def find_pathway_proteins(conn, pathway_id, max_hops=2):
    """
    Find proteins connected to a pathway through multiple relationship types
    """
    cursor = conn.cursor()

    # Multi-hop graph traversal
    cursor.execute("""
        SELECT DISTINCT
            e1.s as protein_id,
            e1.p as direct_relation,
            e2.p as indirect_relation,
            e2.o_id as connected_entity
        FROM rdf_edges e1
        JOIN rdf_edges e2 ON e1.o_id = e2.s
        WHERE e1.o_id = ?
          AND e1.s LIKE 'PROTEIN:%'
        ORDER BY e1.s
        LIMIT 100
    """, [pathway_id])

    results = cursor.fetchall()
    cursor.close()

    return [(protein_id, direct_rel, indirect_rel, entity)
            for protein_id, direct_rel, indirect_rel, entity in results]

# Example usage
pathway_proteins = find_pathway_proteins(conn, 'PATHWAY:apoptosis')

for protein, direct, indirect, entity in pathway_proteins[:10]:
    print(f"{protein} → {direct} → {indirect} → {entity}")
```

## Data Format Support

### 1. TSV/CSV Loading
```python
import pandas as pd

def load_tsv_interactions(conn, file_path):
    """
    Load protein interactions from TSV file
    """
    # Read with pandas
    df = pd.read_csv(file_path, sep='\t')

    # Validate required columns
    required_cols = ['source', 'target', 'confidence']
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f"TSV must contain columns: {required_cols}")

    # Prepare data for IRIS
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO rdf_edges (s, p, o_id, qualifiers)
        VALUES (?, 'interacts_with', ?, ?)
    """

    # Convert to IRIS format
    interactions = []
    for _, row in df.iterrows():
        qualifiers = json.dumps({
            'confidence': float(row['confidence']),
            'source': row.get('source_db', 'unknown'),
            'evidence': row.get('evidence_type', 'experimental')
        })
        interactions.append((row['source'], row['target'], qualifiers))

    # Batch insert
    cursor.executemany(insert_sql, interactions)
    cursor.close()

    print(f"✓ Loaded {len(interactions)} interactions from {file_path}")

# Example usage
load_tsv_interactions(conn, 'data/string_interactions.tsv')
```

### 2. NetworkX Graph Import
```python
def import_networkx_graph(conn, G, node_type='protein'):
    """
    Import NetworkX graph into IRIS
    """
    cursor = conn.cursor()

    # Insert nodes
    node_sql = "INSERT INTO rdf_labels (s, label) VALUES (?, ?)"
    nodes = [(node, node_type) for node in G.nodes()]
    cursor.executemany(node_sql, nodes)

    # Insert edges
    edge_sql = "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)"
    edges = []
    for source, target, data in G.edges(data=True):
        relation = data.get('relation', 'interacts_with')
        qualifiers = json.dumps({k: v for k, v in data.items() if k != 'relation'})
        edges.append((source, relation, target, qualifiers))

    cursor.executemany(edge_sql, edges)
    cursor.close()

    print(f"✓ Imported NetworkX graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# Example: Load GraphML file via NetworkX
G = nx.read_graphml('data/protein_network.graphml')
import_networkx_graph(conn, G)
```

## Performance Optimization

### 1. Connection Pooling
```python
from contextlib import contextmanager
import threading

class IRISConnectionPool:
    def __init__(self, max_connections=10, **conn_params):
        self.max_connections = max_connections
        self.conn_params = conn_params
        self.pool = []
        self.lock = threading.Lock()

    @contextmanager
    def get_connection(self):
        with self.lock:
            if self.pool:
                conn = self.pool.pop()
            else:
                conn = iris.connect(**self.conn_params)

        try:
            yield conn
        finally:
            with self.lock:
                if len(self.pool) < self.max_connections:
                    self.pool.append(conn)
                else:
                    conn.close()

# Usage
pool = IRISConnectionPool(
    max_connections=5,
    hostname='localhost',
    port=1973,
    namespace='USER',
    username='_SYSTEM',
    password='SYS'
)

with pool.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM rdf_edges")
    count = cursor.fetchone()[0]
    print(f"Total edges: {count}")
```

### 2. Parallel Processing
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

def process_protein_batch(batch_data, thread_id):
    """
    Process a batch of proteins in separate thread
    """
    local_conn = iris.connect(
        hostname='localhost',
        port=1973,
        namespace='USER',
        username='_SYSTEM',
        password='SYS'
    )

    try:
        cursor = local_conn.cursor()

        # Process batch
        for protein in batch_data:
            # Insert protein properties
            cursor.execute("""
                INSERT INTO rdf_props (s, key, val)
                VALUES (?, 'name', ?)
            """, [protein['id'], protein['name']])

        print(f"Thread {thread_id}: Processed {len(batch_data)} proteins")
        return len(batch_data)

    finally:
        local_conn.close()

def parallel_protein_loading(protein_list, max_workers=4):
    """
    Load proteins using multiple threads
    """
    batch_size = len(protein_list) // max_workers
    batches = [protein_list[i:i + batch_size] for i in range(0, len(protein_list), batch_size)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all batches
        futures = [
            executor.submit(process_protein_batch, batch, i)
            for i, batch in enumerate(batches)
        ]

        # Collect results
        total_processed = 0
        for future in as_completed(futures):
            total_processed += future.result()

    print(f"✓ Parallel loading complete: {total_processed} proteins")

# Example usage
large_protein_list = [{'id': f'PROTEIN:{i}', 'name': f'Protein_{i}'} for i in range(10000)]
parallel_protein_loading(large_protein_list, max_workers=8)
```

## Error Handling & Best Practices

### 1. Robust Connection Management
```python
import time
import logging

def robust_iris_connection(max_retries=3, retry_delay=5, **conn_params):
    """
    Create IRIS connection with retry logic
    """
    for attempt in range(max_retries):
        try:
            conn = iris.connect(**conn_params)

            # Test connection
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            logging.info("✓ IRIS connection established")
            return conn

        except Exception as e:
            logging.warning(f"Connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise Exception(f"Failed to connect after {max_retries} attempts")

# Usage with automatic retry
conn = robust_iris_connection(
    max_retries=3,
    hostname='localhost',
    port=1973,
    namespace='USER',
    username='_SYSTEM',
    password='SYS'
)
```

### 2. Data Validation
```python
import re

def validate_entity_id(entity_id):
    """
    Validate entity ID format for biomedical data
    """
    # Common biomedical ID patterns
    patterns = [
        r'^GENE:[A-Z0-9_]+$',      # Gene IDs
        r'^PROTEIN:[A-Z0-9_]+$',   # Protein IDs
        r'^DRUG:[A-Z0-9_-]+$',     # Drug IDs
        r'^DISEASE:[A-Z0-9_]+$',   # Disease IDs
        r'^PATHWAY:[A-Z0-9_]+$',   # Pathway IDs
    ]

    return any(re.match(pattern, entity_id) for pattern in patterns)

def safe_insert_edge(conn, source, predicate, target, qualifiers=None):
    """
    Safely insert edge with validation
    """
    # Validate IDs
    if not validate_entity_id(source):
        raise ValueError(f"Invalid source ID: {source}")
    if not validate_entity_id(target):
        raise ValueError(f"Invalid target ID: {target}")

    # Validate qualifiers JSON
    if qualifiers:
        try:
            json.loads(qualifiers)
        except json.JSONDecodeError:
            raise ValueError("Qualifiers must be valid JSON")

    # Insert with error handling
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO rdf_edges (s, p, o_id, qualifiers)
            VALUES (?, ?, ?, ?)
        """, [source, predicate, target, qualifiers or '{}'])
        cursor.close()
        return True

    except Exception as e:
        logging.error(f"Failed to insert edge {source} → {target}: {e}")
        return False

# Example usage
success = safe_insert_edge(
    conn,
    'PROTEIN:BRCA1',
    'interacts_with',
    'PROTEIN:TP53',
    json.dumps({'confidence': 0.95, 'source': 'experimental'})
)
```

## Integration Examples

### 1. Jupyter Notebook Workflow
```python
# Cell 1: Setup
import iris
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np

conn = iris.connect(hostname='localhost', port=1973, namespace='USER', username='_SYSTEM', password='SYS')

# Cell 2: Data Exploration
cursor = conn.cursor()
cursor.execute("SELECT COUNT(DISTINCT s) as proteins FROM rdf_labels WHERE label = 'protein'")
protein_count = cursor.fetchone()[0]
print(f"Total proteins in database: {protein_count}")

# Cell 3: Network Analysis
G = load_networkx_graph(conn)
plt.figure(figsize=(12, 8))
pos = nx.spring_layout(G, k=1, iterations=50)
nx.draw(G, pos, node_size=50, alpha=0.6)
plt.title("Protein Interaction Network")
plt.show()

# Cell 4: Vector Analysis
# ... continue with embeddings, clustering, etc.
```

### 2. Pandas Integration
```python
def query_to_dataframe(conn, query, params=None):
    """
    Execute IRIS query and return pandas DataFrame
    """
    cursor = conn.cursor()
    cursor.execute(query, params or [])

    # Get column names
    columns = [desc[0] for desc in cursor.description]

    # Fetch data
    data = cursor.fetchall()
    cursor.close()

    return pd.DataFrame(data, columns=columns)

# Example: Protein interaction analysis
interactions_df = query_to_dataframe(conn, """
    SELECT
        e.s as source_protein,
        e.o_id as target_protein,
        e.qualifiers as qualifiers,
        e.p as relationship_type
    FROM rdf_edges e
    JOIN rdf_labels l1 ON e.s = l1.s
    JOIN rdf_labels l2 ON e.o_id = l2.s
    WHERE l1.label = 'protein'
      AND l2.label = 'protein'
      AND e.p = 'interacts_with'
    LIMIT 10000
""")

# Pandas analysis
print("Protein interactions by relationship type:")
print(interactions_df['relationship_type'].value_counts())

print("\nQualifier data samples:")
print(interactions_df['qualifiers'].head())
```

## Conclusion

The **IRIS Graph-AI Python SDK** provides the most powerful and flexible interface for biomedical research workflows. Key advantages:

- ✅ **Direct IRIS Performance** - No REST API overhead
- ✅ **NetworkX Compatibility** - Standard graph analysis tools
- ✅ **Pandas Integration** - Familiar data science workflows
- ✅ **SQL Query Power** - Full IRIS query capabilities
- ✅ **Scalable Processing** - Handle millions of entities
- ✅ **Research-Optimized** - Built for biomedical use cases

For web applications and simple queries, use the REST API. For serious data processing and analysis, use the Python SDK.