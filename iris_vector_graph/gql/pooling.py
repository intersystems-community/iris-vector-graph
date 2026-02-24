import asyncio
import contextlib
from typing import AsyncGenerator, Optional, Any
from ..engine import IRISGraphEngine

class AsyncConnectionPool:
    """
    Asynchronous connection pool for IRIS native connections.
    Respects Community Edition license limits using a Semaphore.
    """
    def __init__(self, engine: IRISGraphEngine, max_size: int = 5):
        self.engine = engine
        self.max_size = max_size
        self._semaphore = asyncio.Semaphore(max_size)
        self._pool = asyncio.Queue()
        self._created_count = 0
        self._lock = asyncio.Lock()

    async def _create_connection(self):
        """Creates a new physical connection using the existing connection's params if possible."""
        # For MVP, we'll reuse the existing connection if we can't create new ones
        # Real pooling would require connection parameters (host, port, etc.)
        return self.engine.conn

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncGenerator[Any, None]:
        """Acquires a connection from the pool, waiting if necessary."""
        async with self._semaphore:
            conn = None
            async with self._lock:
                if not self._pool.empty():
                    conn = await self._pool.get()
                elif self._created_count < self.max_size:
                    conn = await self._create_connection()
                    self._created_count += 1
            
            if conn is None:
                # This should not happen if semaphore is working correctly and pool is empty
                # but we haven't reached max_size yet.
                conn = await self._pool.get()

            try:
                yield conn
            finally:
                await self._pool.put(conn)

_pool_instance: Optional[AsyncConnectionPool] = None
_pool_lock = asyncio.Lock()

async def get_pool(engine: Optional[IRISGraphEngine] = None) -> AsyncConnectionPool:
    """Singleton getter for the connection pool."""
    global _pool_instance
    if _pool_instance is None:
        async with _pool_lock:
            if _pool_instance is None:
                if engine is None:
                    raise ValueError("Pool not initialized and no engine provided")
                _pool_instance = AsyncConnectionPool(engine)
    return _pool_instance

@contextlib.asynccontextmanager
async def connection_context() -> AsyncGenerator[Any, None]:
    """Helper context manager for acquisition."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
