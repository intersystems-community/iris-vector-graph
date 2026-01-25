# Operations Guide: IRIS Vector Graph

This guide provides instructions for deploying, configuring, and maintaining the IRIS Vector Graph system in a production environment.

## Installation and Deployment

The primary deployment mechanism is the `scripts/deploy_production.py` script. This script handles the initialization of the SQL schema, deployment of stored procedures (operators), configuration of security roles, and loading of ObjectScript classes.

### Deployment Modes

1.  **Docker Mode (Recommended)**: Best for full deployments where you have access to the IRIS container. It handles both SQL and ObjectScript assets.
    ```bash
    python scripts/deploy_production.py --mode docker --container my-iris-container --schema App
    ```

2.  **SQL Mode**: Use this if you only have a network connection to IRIS and cannot execute commands on the host/container. Note that this mode **cannot** deploy ObjectScript classes.
    ```bash
    python scripts/deploy_production.py --mode sql --host iris-prod.example.com --user deploy_user --password secret
    ```

### Deployment Steps

1.  **Clone and Setup**:
    ```bash
    git clone https://github.com/isc-tdyar/iris-vector-graph.git
    cd iris-vector-graph
    uv sync
    ```
2.  **Configure Environment**: (See [Environment Variables](#environment-variables))
3.  **Execute Deployment**:
    ```bash
    uv run python scripts/deploy_production.py --mode docker
    ```

---

## Environment Variables

The deployment script and the application use the following environment variables for configuration:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `IRIS_HOST` | `localhost` | Hostname of the IRIS server. |
| `IRIS_PORT` | `1972` | SuperServer port of the IRIS instance. |
| `IRIS_NAMESPACE` | `USER` | Target namespace for deployment. |
| `IRIS_USER` | `_SYSTEM` | Username for deployment/connection. |
| `IRIS_PASSWORD` | `SYS` | Password for deployment/connection. |
| `IRIS_SCHEMA` | `User` | Target SQL schema for the graph tables. |
| `IRIS_CONTAINER` | `iris-vector-graph` | Name of the Docker container (Docker mode only). |

---

## Production Security Guide

IRIS Vector Graph uses Role-Based Access Control (RBAC) to secure graph data. The deployment script automatically creates the following roles:

### `graph_reader`
-   **Permissions**: `SELECT` access on all graph tables (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`, `docs`).
-   **Usage**: Assign to application users or API services that only need to query the graph.

### `graph_writer`
-   **Permissions**: `SELECT`, `INSERT`, `UPDATE`, `DELETE` access on all graph tables.
-   **Usage**: Assign to data ingestion pipelines and administrative tools.

### Best Practices
1.  **Disable `_SYSTEM` Account**: In production, create dedicated service accounts and assign them the appropriate roles.
2.  **Audit Logging**: Enable IRIS System Auditing for SQL operations on graph tables.

---

## Maintenance Tasks

### Rebuilding HNSW Indexes
As data grows and changes, you may need to rebuild the HNSW index to optimize search performance.

**SQL Command**:
```sql
ALTER INDEX HNSW_NodeEmb ON kg_NodeEmbeddings REBUILD;
```

### Monitoring Performance
Monitor the following metrics in IRIS:
-   **Global Buffers**: Ensure sufficient memory is allocated for `rdf_edges` and `kg_NodeEmbeddings`.
-   **Query Latency**: Track the execution time of `kg_KNN_VEC` and `kg_GRAPH_PATH` procedures.
-   **Journal Space**: Large-scale ingestion can generate significant journal volume.

### Backups
Standard IRIS backup procedures (External Backup or Online Backup) cover all graph data stored in the database. Ensure the database file containing the graph namespace is included in the backup set.
