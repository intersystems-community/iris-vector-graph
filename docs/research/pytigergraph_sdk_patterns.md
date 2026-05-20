# pyTigerGraph SDK Research Report
**Date**: May 19, 2026  
**Repository**: [tigergraph/pyTigerGraph](https://github.com/tigergraph/pyTigerGraph)  
**Commit**: e7a7e7ebd49baae265a33b9c6e76a76eef5815ec

---

## 1. CONNECTION & INITIALIZATION PATTERN

### Synchronous Connection
```python
from pyTigerGraph import TigerGraphConnection

conn = TigerGraphConnection(
    host="http://localhost",
    graphname="my_graph",
    username="tigergraph",
    password="tigergraph"
)
```

**Pattern**: Thread-local session per connection object.
- Each thread gets its own dedicated `requests.Session` with a single TCP connection pool
- Lazy-initialized on first use via `_session` property
- Isolated via `threading.local()` — no cross-thread pollution

**Async Connection**
```python
from pyTigerGraph import AsyncTigerGraphConnection
import asyncio

async def main():
    async with AsyncTigerGraphConnection(
        host="http://localhost",
        graphname="my_graph",
        username="tigergraph",
        password="tigergraph"
    ) as conn:
        result = await conn.runInstalledQuery("my_query", {"param": "value"})
```

**Pattern**: Single unbounded `aiohttp.ClientSession` shared across all concurrent coroutines.
- Lazy-initialized inside async context to avoid "session created outside event loop" warnings
- Unbounded connection pool (`limit=0`) — grows with demand
- Uses `asyncio.Lock()` for port failover + token refresh synchronization

---

## 2. REST API vs QUERY PROTOCOL ABSTRACTION

### Multi-API Surface
pyTigerGraph wraps **three distinct protocols**:

| Protocol | Purpose | Abstraction | Example |
|----------|---------|-------------|---------|
| **REST++** | Graph queries, CRUD | `_req()` generic method | `runInstalledQuery()` |
| **GSQL Server** | Query management, schema, auth | GSQL subprocess via `pyTigerDriver` | `getSchema()`, `createQuery()` |
| **GraphStudio** | UI endpoints (secondary) | Embedded in REST++ calls | Schema ops |

### Request Layer Architecture
- **_req()** (base): Generic HTTP dispatcher, handles auth + retry logic
- **_get() / _post() / _put() / _delete()**: Thin wrappers over _req()
- **_prep_req()**: Header + auth encoding (Bearer token or Basic auth)
- **_do_request()** (sync): Calls `requests.Session.request()` directly
- **_do_request()** (async): Calls `aiohttp.ClientSession.request()` with timeout wrappe

**Evidence**: [pyTigerGraphBase._req() and session management](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pyTigerGraphBase.py#L155-L240)

---

## 3. RESULT TYPES FROM QUERIES

### runInstalledQuery() Return
Returns **`List[Dict]`** — always a list, even for scalar results:

```python
result = conn.runInstalledQuery("query1")
# Result format:
# [
#   {"ret": 15}    # Scalar wrapped in dict inside list
# ]

# Multi-row result:
result = conn.runInstalledQuery("query_all_param_types", params)
# [
#   {"column1": value1, "column2": value2},
#   {"column1": value1, "column2": value2},
#   ...
# ]
```

### Schema Queries (getSchema, getVertexCount, etc.)
Returns **`dict`** or **`int`** depending on the query:

```python
schema = conn.getSchema()  # dict with full schema
count = conn.getVertexCount()  # int
```

### Error Response Format
Returns error in **JSON body** (not HTTP exception):
```python
# TigerGraph returns HTTP 200 with:
{
  "error": true,
  "message": "Specific error message",
  "code": "REST-10016"  # optional error code
}
```

---

## 4. AUTHENTICATION & TOKEN MANAGEMENT

### Three Auth Modes

#### A. Username/Password (GSQL auth)
```python
conn = TigerGraphConnection(
    host="...", graphname="...",
    username="tigergraph",
    password="tigergraph"
)
```
- Used for **GSQL operations** (schema, query creation)
- Encoded as `Basic {base64(username:password)}`
- **Always required** if GSQL endpoints are used

#### B. API Token (REST++ auth)
```python
# Option 1: Pre-obtained token
conn = TigerGraphConnection(
    host="...", graphname="...",
    apiToken="pre_obtained_token_value"
)

# Option 2: Generate from secret
conn = TigerGraphConnection(
    host="...", graphname="...",
    gsqlSecret="my_secret",  # automatically generates token on init
    username="tigergraph",
    password="tigergraph"
)
token, expiration_ms, formatted_expiry = conn.getToken("my_secret", setToken=True)
```
- Used for **REST++ operations** (queries, graph ops)
- Encoded as `Bearer {token}`
- Token auto-minted + cached, auto-refreshed on 401

#### C. JWT Token (customer-managed auth)
```python
conn = TigerGraphConnection(
    host="...", graphname="...",
    jwtToken="eyJhbGciOiJIUzI1NiIs..."
)
```
- Passed as `Bearer {jwtToken}`
- Token refresh happens if server returns 401
- Verified on init via `_verify_jwt_token_support()`

### Token Refresh Logic
**Evidence**: [pyTigerGraphBase token retry + getToken()](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pyTigerGraphBase.py#L220-L260)

```python
# Automatic token refresh on 401:
# 1. Detect 401 OR auth-failure JSON response
# 2. Call self.getToken() inside _token_refresh_lock
# 3. Reconstruct headers with new token
# 4. Retry request once

# Only auto-refresh if token source is internal (gsqlSecret or apiToken),
# NOT if user supplied apiToken or jwtToken (treated as deliberate)
```

---

## 5. CONNECTION POOLING STRATEGY

### Synchronous (requests)
```python
_adapter = HTTPAdapter(
    pool_connections=1,   # one pool per host (we talk to one TG server)
    pool_maxsize=1,       # each thread is sequential; only 1 socket needed
    max_retries=0,
)
s.mount("http://", _adapter)
s.mount("https://", _adapter)
```
- **One session per thread** via `threading.local()`
- Thread-local prevents concurrent modification of shared state
- Pool size 1 because each thread is sequential
- Concurrent threads never block each other (separate sessions)

### Asynchronous (aiohttp)
```python
connector = aiohttp.TCPConnector(
    limit=0,                            # unbounded — grows with demand
    ssl=None if self.verify else False, # SSL context or disabled
)
return aiohttp.ClientSession(connector=connector)
```
- **Single session** shared across all concurrent coroutines
- Unbounded connection pool — scales automatically
- No GIL serialization — tasks parse concurrently
- Optional: Install `pyTigerGraph[fast]` for orjson (2-10× faster JSON parsing, releases GIL)

**Evidence**: [pyTigerGraphBase._session property (sync)](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pyTigerGraphBase.py#L291-L310)  
[AsyncPyTigerGraphBase._make_async_client() (async)](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pytgasync/pyTigerGraphBase.py#L520-L533)

---

## 6. ASYNC SUPPORT

### Full Dual API
- `TigerGraphConnection` — synchronous (requests-based)
- `AsyncTigerGraphConnection` — asynchronous (aiohttp-based)
- **100% parallel class hierarchy** under `pytgasync/`
  - `AsyncPyTigerGraphBase`, `AsyncPyTigerGraphQuery`, `AsyncPyTigerGraphVertex`, etc.

### Async Context Manager
```python
# Preferred pattern: automatic cleanup
async with AsyncTigerGraphConnection(...) as conn:
    result = await conn.runInstalledQuery("q1", {"p": "v"})
    # Socket pool auto-closed on exit

# Manual cleanup
conn = AsyncTigerGraphConnection(...)
result = await conn.runInstalledQuery("q1", {"p": "v"})
await conn.aclose()  # close socket pool
```

### High-Concurrency Pattern
```python
async def main():
    async with AsyncTigerGraphConnection(...) as conn:
        # 1000s of queries in flight simultaneously
        tasks = [
            conn.runInstalledQuery("q", {"p": v})
            for v in range(10000)
        ]
        results = await asyncio.gather(*tasks)
```

**Evidence**: [AsyncTigerGraphConnection class](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pytgasync/pyTigerGraph.py)  
[Async request pipeline with lazy session init](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pytgasync/pyTigerGraphBase.py#L114-L191)

---

## 7. ERROR HANDLING

### Exception Hierarchy
```python
class TigerGraphException(Exception):
    def __init__(self, message, code=None):
        self.message = message  # from server response
        self.code = code        # optional error code (e.g., "REST-10016")
```

### Error Detection Logic
**Sync**: [pyTigerGraphBase._error_check()](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pyTigerGraphBase.py#L145-L150)

```python
def _error_check(self, res):
    """Check if response indicates error"""
    if "error" in res and res["error"] and res["error"] != "false":
        # Note: server sometimes returns string "false" instead of bool False
        raise TigerGraphException(
            res["message"],
            res["code"] if "code" in res else None
        )
```

### Automatic Auth Error Recovery
```python
# Triggers on EITHER:
# 1. HTTP 401 status
# 2. JSON body with auth-failure shape: {"error": true, "message": "Authentication failed."}

# Auto-recovery only if token managed by SDK (not user-supplied):
if (not getattr(self, "_refreshing_token", False)
    and getattr(self, "_token_source", None) != "user"):
    # Refresh token and retry once
```

### Port Failover Logic (TG 3.x → 4.x)
```python
# TigerGraph 4.x moved REST++ from port 9000 to 14240
# On first connection error to :9000:
# 1. Acquire failover lock (prevents thundering herd in async)
# 2. Construct new URL: host:14240/restpp
# 3. Retry request
# 4. Update self.restppUrl, self.restppPort permanently
```

---

## 8. QUERY PARAMETER BINDING

### Parameter Encoding
```python
# Sync version uses urllib.parse + URL encoding
def _prep_params(self, params: dict) -> str:
    """Encode dict params to query string"""
    ret = ""
    for k, v in params.items():
        if isinstance(v, tuple):
            # (vertex_id, vertex_type) tuples get special encoding
            ret += k + "=" + str(v[0]) + "&" + k + ".type=" + self._safeChar(v[1])
        elif isinstance(v, list):
            # Lists become multiple key=value&key=value&...
            for item in v:
                ret += k + "=" + self._safeChar(item) + "&"
        else:
            # Scalars: URL-encode
            ret += k + "=" + self._safeChar(v) + "&"
    return ret[:-1]  # trim trailing &
```

### POST vs GET Auto-Detection
```python
# usePost=None (default):
#   - Dict params → POST (with JSON body)
#   - String params → GET (in URL query string)
# usePost=True:
#   - Force POST body (faster for lists, avoids 8KB URL limit)
# usePost=False:
#   - Force GET query string

# Reason: Empty sets in DB 3.8+ MUST use POST
# Large vectors MUST use POST (URL limit)
```

### Type Coercion
```python
params = {
    "p01_int": 1,
    "p02_float": 3.14,
    "p03_string": "hello",
    "p04_bool": True,
    "p05_list": [1, 2, 3],
    "p06_tuple": (vertex_id, "vertex_type"),
    "p07_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}
# All passed through _safeChar() for URL encoding
```

**Evidence**: [pyTigerGraphQuery parameter encoding](https://github.com/tigergraph/pyTigerGraph/blob/e7a7e7ebd49baae265a33b9c6e76a76eef5815ec/pyTigerGraph/pyTigerGraphQuery.py#L50-L90)

---

## SUMMARY TABLE

| Aspect | Sync (requests) | Async (aiohttp) |
|--------|-----------------|-----------------|
| **Connection** | `requests.Session` (thread-local) | `aiohttp.ClientSession` (single, shared) |
| **Pool Size** | 1 socket/thread (sequential) | Unbounded (grows with demand) |
| **Concurrency** | ThreadPoolExecutor + threads | `asyncio.gather()` + tasks |
| **Token Refresh** | `threading.Lock()` | `asyncio.Lock()` |
| **Port Failover** | `threading.Lock()` | `asyncio.Lock()` |
| **Error Recovery** | Automatic on 401 + retry | Automatic on 401 + retry |
| **Context Manager** | `with conn:` | `async with conn:` |
| **Cleanup** | `.close()` | `await .aclose()` |
| **Max QPS** | ~100-200 (GIL contention) | ~1000+ (no GIL lock) |

---

## RECOMMENDATIONS FOR IVG INTEGRATION

1. **Mirror pyTigerGraph's dual API** if planning high-concurrency support
2. **Use threading.local() for sync** session management (prevents cross-thread pollution)
3. **Use unbounded aiohttp pool** for async (automatic scaling is key for perf)
4. **Implement automatic token refresh** on 401, WITH user-supplied token bypass
5. **Parameter binding**: POST body for lists/sets, GET query string for scalars (unless usePost specified)
6. **Lock strategy**: `threading.Lock()` for sync, `asyncio.Lock()` for async (prevents thundering herd on failover)

