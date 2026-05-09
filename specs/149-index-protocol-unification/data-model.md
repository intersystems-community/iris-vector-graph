# Data Model: Index Protocol Unification

## IVGIndex Protocol

```python
@runtime_checkable
class IVGIndex(Protocol):
    def search(self, query: Any, k: int = 10, **kwargs) -> list: ...
    def insert(self, id: str, vector: Any) -> None: ...
    def drop(self) -> None: ...
    def info(self) -> dict: ...
```

## IndexHandle

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Index name (key in registry globals) |
| `type` | `str` | One of: `"ivf"`, `"bm25"`, `"vec"`, `"plaid"` |
| `_engine` | `IRISGraphEngine` | Engine reference for dispatch |

## IndexRegistry (in IRISGraphEngine)

| Field | Type | Description |
|---|---|---|
| `_index_registry` | `Dict[str, str]` | `{name: type_str}`, populated on `__init__` |

Auto-populated by probing:
- `$Order(^IVF(""))` → type `"ivf"`
- `$Order(^VecIdx(""))` → type `"vec"`
- `$Order(^BM25Idx(""))` → type `"bm25"`
- `$Order(^PLAID(""))` → type `"plaid"`

## PLAIDSearch.cls Method Visibility

| Method | Before | After |
|---|---|---|
| `Build` | (does not exist) | Public |
| `Search` | Public | Public (unchanged) |
| `Insert` | Public | Public (unchanged) |
| `Drop` | Public | Public (unchanged) |
| `Info` | Public | Public (unchanged) |
| `StoreCentroids` | Public | **Private** |
| `StoreDocTokens` | Public | **Private** |
| `StoreDocTokensBatch` | Public | **Private** |
| `BuildInvertedIndex` | Public | **Private** |
| `JsonToVector` | (utility) | **Private** |

## info() dict contract

All `*_info` methods return a dict including `"type"`:

| Index | Keys |
|---|---|
| IVF | `type="ivf"`, `nlist`, `dim`, `metric`, `indexed` |
| BM25 | `type="bm25"`, `indexed`, `k1`, `b` |
| Vec | `type="vec"`, `name`, `dim`, `metric`, `indexed` |
| PLAID | `type="plaid"`, `indexed`, `dim`, `nlist` |
