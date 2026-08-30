"""Lazy loading patterns — on-demand resource initialization.

Resources are only initialized when first needed, not at application
startup. This keeps cold-start times low and avoids paying for
capabilities the caller never exercises.

Key patterns
~~~~~~~~~~~~
- **LazyPromise**: A promise that only executes on first await.
- **LazyValue**: A value that is computed on first access and cached.
- **LazyFactory**: A factory that defers creation until first use.
- **LazyPool**: A pool of resources initialized on demand.

Usage
~~~~~
    # LazyPromise — defer async work
    lazy = LazyPromise(expensive_init)
    # ... later, when actually needed
    result = await lazy.get()

    # LazyValue — defer computation
    lazy_val = LazyValue(lambda: compute_heavy_thing())
    print(lazy_val.value)  # Computes on first access, caches after

    # LazyPool — on-demand resource pool
    pool = LazyPool(max_size=4)
    arm = await pool.acquire()  # Creates arm if pool is empty
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

_logger = logging.getLogger(__name__)

T = TypeVar("T")


class LazyPromise(Generic[T]):
    """A promise that defers execution until first access.

    Usage:
        async def create_resource() -> str:
            await asyncio.sleep(1)  # Expensive init
            return "ready"

        lazy = LazyPromise(create_resource)
        # ... later
        result = await lazy.get()  # Only now does init happen
    """

    def __init__(
        self,
        factory: Callable[[], Awaitable[T]],
        name: str = "lazy",
    ) -> None:
        self._factory = factory
        self._name = name
        self._promise: asyncio.Future[T] | None = None
        self._value: T | None = None
        self._error: Exception | None = None
        self._initialized = False

    async def get(self) -> T:
        """Get the value, initializing if necessary.

        Returns:
            The initialized value.

        Raises:
            The exception that occurred during initialization.
        """
        if self._initialized:
            if self._error:
                raise self._error
            return self._value  # type: ignore[return-value]

        if self._promise is not None:
            # Initialization in progress — wait for it
            try:
                return await self._promise
            except Exception as e:
                self._error = e
                raise

        # Start initialization
        loop = asyncio.get_running_loop()
        self._promise = loop.create_future()

        try:
            start = time.perf_counter()
            self._value = await self._factory()
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._initialized = True
            self._promise.set_result(self._value)

            _logger.info("lazy %s initialized in %.1fms", self._name, elapsed_ms)
            return self._value

        except Exception as e:
            self._error = e
            self._promise.set_exception(e)
            _logger.exception("lazy %s initialization failed", self._name)
            raise

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_initializing(self) -> bool:
        return self._promise is not None and not self._initialized

    async def reset(self) -> None:
        """Reset to uninitialized state.

        The next call to get() will re-initialize.
        """
        self._promise = None
        self._value = None
        self._error = None
        self._initialized = False


class LazyValue(Generic[T]):
    """A value that is computed on first access and cached.

    Usage:
        def compute_config() -> dict:
            # Expensive config loading
            return load_config_from_disk()

        config = LazyValue(compute_config)
        print(config.value)  # Computes on first access
        print(config.value)  # Returns cached value
    """

    def __init__(
        self,
        factory: Callable[[], T],
        name: str = "lazy_value",
    ) -> None:
        self._factory = factory
        self._name = name
        self._value: T | None = None
        self._initialized = False

    @property
    def value(self) -> T:
        if not self._initialized:
            start = time.perf_counter()
            self._value = self._factory()
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._initialized = True
            _logger.info("lazy value %s computed in %.1fms", self._name, elapsed_ms)
        return self._value  # type: ignore[return-value]

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def reset(self) -> None:
        """Reset to uncomputed state."""
        self._value = None
        self._initialized = False


class LazyPool(Generic[T]):
    """A pool of lazily-initialized resources.

    Resources are created on-demand up to max_size. When the pool
    is exhausted, callers wait until a resource is released.

    Usage:
        async def create_arm() -> Arm:
            return Arm()

        pool = LazyPool(create_arm, max_size=4)
        arm = await pool.acquire()
        try:
            await arm.do_work()
        finally:
            pool.release(arm)
    """

    def __init__(
        self,
        factory: Callable[[], Awaitable[T]],
        max_size: int = 4,
        name: str = "lazy_pool",
    ) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")

        self._factory = factory
        self._max_size = max_size
        self._name = name
        self._available: asyncio.Queue[T] = asyncio.Queue()
        self._current_size = 0
        self._lock = asyncio.Lock()
        self._total_acquired = 0
        self._total_created = 0

    async def acquire(self) -> T:
        """Acquire a resource from the pool.

        If the pool has available resources, one is returned immediately.
        If the pool is not at max capacity, a new resource is created.
        Otherwise, the caller waits until a resource is released.
        """
        # Try to get from available resources
        if not self._available.empty():
            resource = self._available.get_nowait()
            self._total_acquired += 1
            return resource

        # Try to create a new resource
        async with self._lock:
            if self._current_size < self._max_size:
                self._current_size += 1
                self._total_created += 1
                resource = await self._factory()
                self._total_acquired += 1
                return resource

        # Pool is full — wait for a resource to be released
        resource = await self._available.get()
        self._total_acquired += 1
        return resource

    def release(self, resource: T) -> None:
        """Release a resource back to the pool."""
        self._available.put_nowait(resource)

    @property
    def available_count(self) -> int:
        """Number of resources currently available."""
        return self._available.qsize()

    @property
    def current_size(self) -> int:
        """Total number of resources created."""
        return self._current_size

    @property
    def stats(self) -> dict[str, int]:
        """Pool usage statistics."""
        return {
            "max_size": self._max_size,
            "current_size": self._current_size,
            "available_count": self.available_count,
            "total_acquired": self._total_acquired,
            "total_created": self._total_created,
        }

    async def close(self) -> None:
        """Close the pool and clean up resources."""
        while not self._available.empty():
            resource = self._available.get_nowait()
            if hasattr(resource, "close"):
                if asyncio.iscoroutinefunction(resource.close):
                    await resource.close()
                else:
                    resource.close()
        self._current_size = 0


class LazyArmPool:
    """Specialized lazy pool for Arm resources.

    Integrates with echo-agent's Arm architecture to provide
    on-demand arm initialization.
    """

    def __init__(
        self,
        arm_factory: Callable[[], Awaitable[Any]],
        max_arms: int = 4,
    ) -> None:
        self._pool = LazyPool(arm_factory, max_size=max_arms, name="arm_pool")
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> bool:
        """Ensure at least one arm is ready for use.

        Returns:
            True if an arm is ready, False otherwise.
        """
        if self._pool.available_count > 0:
            return True

        async with self._lock:
            if self._pool.available_count > 0:
                return True
            try:
                arm = await self._pool.acquire()
                self._pool.release(arm)
                return True
            except (TypeError, ValueError, RuntimeError):
                return False

    async def get_arm(self) -> Any:
        """Get an arm, initializing if necessary."""
        return await self._pool.acquire()

    def release_arm(self, arm: Any) -> None:
        """Release an arm back to the pool."""
        self._pool.release(arm)

    @property
    def stats(self) -> dict[str, Any]:
        return self._pool.stats
