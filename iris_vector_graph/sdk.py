from __future__ import annotations

import time
from typing import Any, Optional

try:
    import httpx as _httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

from iris_vector_graph.result import IVGResult


class IVGError(Exception):
    pass


class IVGClientError(IVGError):
    def __init__(self, message: str, http_code: int | None = None):
        super().__init__(message)
        self.http_code = http_code


class IVGServerError(IVGError):
    def __init__(self, message: str, http_code: int = 500, query: str | None = None):
        super().__init__(message)
        self.http_code = http_code
        self.query = query

    def is_retryable(self) -> bool:
        return self.http_code >= 500


class IVGRecord:
    __slots__ = ("_keys", "_values")

    def __init__(self, keys: list[str], values: list[Any]):
        self._keys = keys
        self._values = list(values)

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._keys.index(key)]
        except ValueError:
            return None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (IndexError, TypeError):
            return default

    def data(self) -> dict[str, Any]:
        return dict(zip(self._keys, self._values))

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<IVGRecord {self.data()!r}>"


def _wrap_result(raw: dict) -> IVGResult:
    return IVGResult(
        columns=raw.get("columns", []),
        rows=raw.get("rows", []),
        error=raw.get("error"),
    )


class IVGClient:
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        if not _HAS_HTTPX:
            raise ImportError("httpx is required for IVGClient: pip install httpx")
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: _httpx.Client | None = None

    def _get_client(self) -> _httpx.Client:
        if self._client is None:
            self._client = _httpx.Client(
                base_url=self._url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    def execute_cypher(
        self,
        query: str,
        parameters: dict | None = None,
    ) -> IVGResult:
        payload = {"query": query, "parameters": parameters or {}}
        raw = self._post("/api/cypher", payload)
        result = _wrap_result(raw)
        if result.error:
            raise IVGServerError(result.error, http_code=200, query=query)
        return result

    def execute_aql(
        self,
        query: str,
        bind_vars: dict | None = None,
    ) -> IVGResult:
        from iris_vector_graph.cypher.aql import translate_aql
        cypher, params = translate_aql(query, bind_vars or {})
        return self.execute_cypher(cypher, parameters=params)

    def ping(self) -> dict:
        try:
            resp = self._get_client().get("/health", timeout=5.0)
            resp.raise_for_status()
            return resp.json()
        except _httpx.ConnectError as e:
            raise IVGClientError(f"Cannot reach {self._url}: {e}")
        except _httpx.HTTPStatusError as e:
            raise IVGClientError(f"Health check failed: {e}", http_code=e.response.status_code)

    def schema(self) -> dict:
        return self._get("/schema")

    def server_info(self) -> dict:
        return self._get("/server")

    def stats(self) -> dict:
        return self._get("/stats")

    def node_count(self) -> int:
        result = self.execute_cypher("MATCH (n) RETURN count(n) AS c")
        return result.rows[0][0] if result.rows else 0

    def get_labels(self) -> list[str]:
        result = self.execute_cypher("CALL db.labels() YIELD label RETURN label")
        return [r[0] for r in result.rows]

    def load_ndjson(self, path: str) -> dict:
        with open(path, "rb") as f:
            resp = self._get_client().post(
                "/admin/load",
                content=f.read(),
                headers={"Content-Type": "application/x-ndjson"},
            )
        resp.raise_for_status()
        return resp.json()

    def explain(self, query: str, parameters: dict | None = None) -> dict:
        resp = self._get_client().post(
            "/admin/explain",
            json={"query": query, "parameters": parameters or {}},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get(self, path: str) -> dict:
        resp = self._get_client().get(path)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._get_client().post(path, json=payload)
                if resp.status_code == 401:
                    raise IVGClientError("Authentication failed — check api_key", http_code=401)
                if resp.status_code == 403:
                    raise IVGClientError("Forbidden", http_code=403)
                if resp.status_code >= 500:
                    if attempt < self._max_retries - 1:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise IVGServerError(resp.text[:200], http_code=resp.status_code)
                resp.raise_for_status()
                return resp.json()
            except (IVGClientError, IVGServerError):
                raise
            except (_httpx.ConnectError, _httpx.TimeoutException) as e:
                last_exc = e
                if attempt < self._max_retries - 1:
                    time.sleep(0.5 * (2 ** attempt))
        raise IVGClientError(f"Failed after {self._max_retries} attempts: {last_exc}")


class AsyncIVGClient:
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        if not _HAS_HTTPX:
            raise ImportError("httpx is required for AsyncIVGClient: pip install httpx")
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._client: _httpx.AsyncClient | None = None

    def _get_client(self) -> _httpx.AsyncClient:
        if self._client is None:
            self._client = _httpx.AsyncClient(
                base_url=self._url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    async def execute_cypher(
        self,
        query: str,
        parameters: dict | None = None,
    ) -> IVGResult:
        resp = await self._get_client().post(
            "/api/cypher",
            json={"query": query, "parameters": parameters or {}},
        )
        resp.raise_for_status()
        result = _wrap_result(resp.json())
        if result.error:
            raise IVGServerError(result.error, http_code=200, query=query)
        return result

    async def execute_aql(
        self,
        query: str,
        bind_vars: dict | None = None,
    ) -> IVGResult:
        from iris_vector_graph.cypher.aql import translate_aql
        cypher, params = translate_aql(query, bind_vars or {})
        return await self.execute_cypher(cypher, parameters=params)

    async def ping(self) -> dict:
        resp = await self._get_client().get("/health")
        resp.raise_for_status()
        return resp.json()

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
