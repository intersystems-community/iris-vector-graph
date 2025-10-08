# Data Model: Interactive IRIS Demo Web Interface

**Feature**: 005-interactive-demo-web
**Date**: 2025-01-06

## Entity Definitions

### 1. DemoSession

**Description**: Represents a user's interaction with the demo system, tracking selected mode and query history.

**Fields**:
- `session_id`: str (UUID) - Unique session identifier
- `mode`: Enum['fraud', 'biomedical'] - Currently selected demo mode
- `query_history`: List[QueryHistoryEntry] - Ordered list of queries performed in session
- `visualization_state`: Dict - Current state of visualizations (zoom level, selected nodes, etc.)
- `created_at`: datetime - Session creation timestamp
- `last_activity`: datetime - Last user interaction timestamp

**Validation Rules**:
- FR-021: Mode switch must preserve query_history context
- Session timeout: 30 minutes of inactivity

**State Transitions**:
```
fraud <-> biomedical  # Bidirectional mode switching (FR-021)
```

---

### 2. QueryHistoryEntry

**Description**: Single query record within a demo session.

**Fields**:
- `query_id`: str (UUID) - Unique query identifier
- `query_type`: Enum['fraud_score', 'bitemporal', 'audit_trail', 'protein_search', 'pathway', 'hybrid_search'] - Type of query
- `query_params`: Dict - Query parameters (depends on query_type)
- `result_summary`: Dict - High-level result metrics (count, execution_time)
- `timestamp`: datetime - When query was executed

**Validation Rules**:
- FR-003: query_history must be preserved during session
- Maximum 100 queries per session (prevent memory bloat)

---

### 3. FraudTransactionQuery

**Description**: User-submitted transaction details for fraud scoring.

**Fields**:
- `payer`: str - Payer account identifier (format: "acct:{identifier}")
- `payee`: str (optional) - Payee account identifier
- `amount`: Decimal - Transaction amount (positive, max 2 decimal places)
- `device`: str - Device identifier (format: "dev:{identifier}")
- `merchant`: str (optional) - Merchant identifier (format: "merch:{identifier}")
- `ip_address`: str (optional) - IP address (IPv4 or IPv6)
- `timestamp`: datetime - Transaction timestamp (defaults to current time)

**Validation Rules**:
- FR-006: All required fields (payer, amount, device) must be provided
- amount: > 0, max 1,000,000.00
- payer/device: Non-empty strings, max 100 chars
- ip_address: Valid IPv4/IPv6 format if provided

**Example**:
```json
{
  "payer": "acct:user_12345",
  "amount": 1500.00,
  "device": "dev:laptop_001",
  "merchant": "merch:store_789",
  "ip_address": "192.168.1.100",
  "timestamp": "2025-01-06T14:30:00Z"
}
```

---

### 4. FraudScoringResult

**Description**: System-generated fraud probability and risk assessment.

**Fields**:
- `fraud_probability`: float - Probability score (0.0-1.0)
- `risk_classification`: Enum['low', 'medium', 'high', 'critical'] - Human-readable risk level
- `contributing_factors`: List[str] - Factors that influenced the score
- `scoring_timestamp`: datetime - When fraud score was calculated
- `scoring_model`: str - Model version used (e.g., "MLP", "graph_centrality")
- `confidence`: float (optional) - Model confidence (0.0-1.0)

**Validation Rules**:
- FR-007: risk_classification must be in {low, medium, high, critical}
- fraud_probability: 0.0 <= value <= 1.0
- Risk classification mapping:
  - low: probability < 0.30
  - medium: 0.30 <= probability < 0.60
  - high: 0.60 <= probability < 0.85
  - critical: probability >= 0.85

**Example**:
```json
{
  "fraud_probability": 0.92,
  "risk_classification": "critical",
  "contributing_factors": [
    "High transaction amount",
    "New device fingerprint",
    "Late-night transaction pattern"
  ],
  "scoring_timestamp": "2025-01-06T14:30:05Z",
  "scoring_model": "MLP",
  "confidence": 0.87
}
```

---

### 5. BitemporalQuery

**Description**: User-specified parameters for temporal time-travel queries.

**Fields**:
- `event_id`: str - Transaction/event identifier to query
- `system_time`: datetime - "When did we know?" timestamp (system time)
- `valid_time`: datetime (optional) - "When did it happen?" timestamp (valid time)
- `query_mode`: Enum['as_of', 'diff', 'audit_trail'] - Type of temporal query

**Validation Rules**:
- FR-008: system_time must be provided for time-travel queries
- system_time must be <= current time (can't query future knowledge)
- valid_time must be <= system_time if provided

**Example**:
```json
{
  "event_id": "txn_2025_001234",
  "system_time": "2024-12-31T23:59:59Z",
  "valid_time": "2024-12-15T10:30:00Z",
  "query_mode": "as_of"
}
```

---

### 6. BitemporalResult

**Description**: Historical version of transaction data from temporal query.

**Fields**:
- `event_id`: str - Transaction identifier
- `version_id`: int - Version number (starts at 1)
- `valid_from`: datetime - When event was valid (actual occurrence time)
- `valid_to`: datetime (nullable) - When event validity ended
- `system_from`: datetime - When this version was recorded in system
- `system_to`: datetime (nullable) - When this version was superseded (NULL = current)
- `fraud_score`: float - Fraud probability at this version
- `fraud_status`: Enum['clean', 'suspicious', 'confirmed_fraud', 'reversed'] - Status at this version
- `changed_by`: str (optional) - Who made the change
- `change_reason`: str (optional) - Why the change was made
- `data_snapshot`: Dict - Full transaction data at this version

**Validation Rules**:
- FR-009: Must provide complete version history showing all changes
- version_id must be sequential (1, 2, 3, ...)
- system_to = NULL indicates current version

**State Transitions** (FR-011: Chargeback workflow):
```
clean -> suspicious -> confirmed_fraud -> reversed
       |                                      ^
       +--------------------------------------+
```

**Example** (Audit Trail):
```json
{
  "versions": [
    {
      "version_id": 1,
      "system_from": "2025-01-15T10:30:00Z",
      "fraud_score": 0.15,
      "fraud_status": "clean",
      "change_reason": "Initial approval"
    },
    {
      "version_id": 2,
      "system_from": "2025-01-15T14:30:00Z",
      "fraud_score": 0.65,
      "fraud_status": "suspicious",
      "changed_by": "fraud_detection_system",
      "change_reason": "Late arrival detected"
    },
    {
      "version_id": 3,
      "system_from": "2025-01-15T15:00:00Z",
      "fraud_score": 0.95,
      "fraud_status": "confirmed_fraud",
      "changed_by": "analyst_john",
      "change_reason": "Manual investigation confirmed fraud"
    },
    {
      "version_id": 4,
      "system_from": "2025-01-15T15:15:00Z",
      "system_to": null,
      "fraud_score": 0.95,
      "fraud_status": "reversed",
      "changed_by": "payments_system",
      "change_reason": "Chargeback processed"
    }
  ]
}
```

---

### 7. LateArrivalTransaction

**Description**: Transaction reported significantly after occurrence (settlement delay).

**Fields**:
- `event_id`: str - Transaction identifier
- `valid_from`: datetime - When transaction actually occurred
- `system_from`: datetime - When system learned about it
- `delay_hours`: float - Hours between occurrence and reporting
- `amount`: Decimal - Transaction amount
- `payer`: str - Payer identifier
- `device`: str - Device identifier
- `suspicion_flags`: List[str] - Why flagged as suspicious

**Validation Rules**:
- FR-010: Late arrival = delay_hours > 24
- delay_hours = (system_from - valid_from) in hours
- Must be ordered by delay_hours (descending) for display

---

### 8. ProteinQuery

**Description**: User-submitted protein search criteria.

**Fields**:
- `protein_identifier`: str (optional) - Protein name or ID (e.g., "TP53", "ENSP00000269305")
- `search_text`: str (optional) - Free-text search query
- `search_mode`: Enum['vector_similarity', 'text_search', 'hybrid'] - Search method
- `top_k`: int - Number of results to return (default: 10, max: 100)
- `min_similarity`: float (optional) - Minimum similarity threshold (0.0-1.0)

**Validation Rules**:
- FR-013: protein_identifier OR search_text must be provided
- FR-017: search_mode='hybrid' combines vector + text search
- top_k: 1 <= value <= 100

**Example**:
```json
{
  "protein_identifier": "TP53",
  "search_mode": "hybrid",
  "top_k": 10,
  "min_similarity": 0.7
}
```

---

### 9. ProteinSearchResult

**Description**: Matching proteins with similarity scores and metadata.

**Fields**:
- `protein_id`: str - Protein identifier
- `protein_name`: str - Human-readable name
- `similarity_score`: float - Similarity to query (0.0-1.0)
- `interaction_count`: int - Number of known interactions
- `metadata`: Dict - Additional protein data (molecular weight, location, function)
- `search_method`: str - How this result was found ("vector", "text", "RRF_fusion")

**Validation Rules**:
- FR-014: similarity_score must be 0.0-1.0, results ranked descending
- Results must be ordered by similarity_score (highest first)

**Example**:
```json
{
  "results": [
    {
      "protein_id": "ENSP00000269305",
      "protein_name": "TP53",
      "similarity_score": 1.0,
      "interaction_count": 487,
      "metadata": {
        "molecular_weight": 53000,
        "cellular_location": "nucleus",
        "function": "tumor suppressor"
      },
      "search_method": "exact_match"
    },
    {
      "protein_id": "ENSP00000398632",
      "protein_name": "MDM2",
      "similarity_score": 0.89,
      "interaction_count": 123,
      "metadata": {...},
      "search_method": "RRF_fusion"
    }
  ]
}
```

---

### 10. PathwayQuery

**Description**: Request for multi-hop protein interaction pathway.

**Fields**:
- `source_protein`: str - Starting protein identifier
- `target_protein`: str - Ending protein identifier
- `max_hops`: int - Maximum path length (default: 5, max: 10)
- `algorithm`: Enum['shortest_path', 'all_paths', 'weighted_path'] - Pathfinding method

**Validation Rules**:
- FR-016: Both source_protein and target_protein required
- max_hops: 1 <= value <= 10 (prevent expensive queries)

---

### 11. InteractionNetwork

**Description**: Graph structure for protein interaction visualization.

**Fields**:
- `nodes`: List[ProteinNode] - Protein nodes in network
- `edges`: List[InteractionEdge] - Interactions between proteins
- `visualization_state`: Dict - D3.js visualization state (zoom, pan, selected nodes)
- `layout_algorithm`: str - Layout used ("force_directed", "hierarchical")

**ProteinNode**:
```python
{
  "id": str,
  "name": str,
  "degree": int,  # Number of connections
  "cluster_id": int (optional),  # For hierarchical clustering
  "x": float (optional),  # Position for resuming layout
  "y": float (optional)
}
```

**InteractionEdge**:
```python
{
  "source": str,  # Node ID
  "target": str,  # Node ID
  "interaction_type": str,  # "binds", "activates", "inhibits", etc.
  "confidence": float  # 0.0-1.0
}
```

**Validation Rules**:
- FR-015: Must support interactive visualization
- FR-018: Support node expansion (load additional neighbors on click)
- Auto-cluster when nodes.length > 500 (research decision)

---

### 12. QueryPerformanceMetrics

**Description**: Execution metrics displayed to user (FR-019).

**Fields**:
- `query_type`: str - Type of query executed
- `execution_time_ms`: int - Total query time in milliseconds
- `backend_used`: str - Which backend serviced the query ("fraud_api", "iris_graph", "cached_demo")
- `result_count`: int - Number of results returned
- `search_methods`: List[str] - Methods used (e.g., ["vector_search", "BM25", "RRF_fusion"])
- `timestamp`: datetime - When query was executed

**Validation Rules**:
- FR-002: execution_time_ms should be < 2000 for typical demos
- FR-019: Must be displayed to user for educational purposes

**Example**:
```json
{
  "query_type": "protein_hybrid_search",
  "execution_time_ms": 127,
  "backend_used": "iris_graph",
  "result_count": 10,
  "search_methods": ["HNSW_vector_search", "BM25_text_search", "RRF_fusion"],
  "timestamp": "2025-01-06T14:35:22Z"
}
```

---

## Entity Relationships

```
DemoSession
├── 1:N QueryHistoryEntry
│   ├── references: FraudTransactionQuery (if fraud query)
│   ├── references: BitemporalQuery (if temporal query)
│   └── references: ProteinQuery (if biomedical query)
└── 1:1 visualization_state (for current network view)

FraudTransactionQuery → FraudScoringResult (1:1)
BitemporalQuery → BitemporalResult (1:N versions)
ProteinQuery → ProteinSearchResult (1:N results)
PathwayQuery → InteractionNetwork (1:1)

All queries → QueryPerformanceMetrics (1:1)
```

---

## Data Storage Strategy

**Session Data**: In-memory (FastHTML signed cookie sessions)
- DemoSession
- QueryHistoryEntry
- visualization_state

**Query Results**: Ephemeral (returned from APIs, not persisted)
- FraudScoringResult
- BitemporalResult
- ProteinSearchResult
- InteractionNetwork
- QueryPerformanceMetrics

**Demo Data** (when DEMO_MODE=true): Pre-generated JSON files in `demo_data/`
- Synthetic fraud transactions
- Synthetic protein networks
- Cached bitemporal audit trails

**No persistent database required** for demo layer - all state is session-based or API-sourced.

---

**Phase 1.1 Complete**: Data model defined. Ready for API contracts.
