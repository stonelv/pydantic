"""TypeAdapter precompile cache for library authors.

This module provides a thread-safe caching mechanism for TypeAdapter's
core schema, validator, and serializer. This allows library authors to
precompile commonly used types during process startup and reuse them
across multiple TypeAdapter instantiations.

Example:
    >>> from pydantic import TypeAdapter, TypeAdapterCache, get_global_cache
    >>>
    >>> # Create a cache instance
    >>> cache = TypeAdapterCache()
    >>>
    >>> # Precompile types during process startup
    >>> success, failures = cache.precompile([
    ...     (list[int], None),
    ...     (dict[str, int], {'strict': True}),
    ... ])
    >>>
    >>> # Use cache when creating TypeAdapters (this will hit the cache)
    >>> ta = TypeAdapter(list[int], cache=cache)
    >>> ta.validate_python([1, 2, 3])
    [1, 2, 3]
"""

from __future__ import annotations as _annotations

import threading
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast, final

from pydantic_core import CoreSchema, SchemaSerializer, SchemaValidator

from .config import ConfigDict

T = TypeVar('T')


@dataclass
class CacheStats:
    """Statistics for the TypeAdapter cache.

    Attributes:
        hits: Number of cache hits.
        misses: Number of cache misses.
        size: Current number of entries in the cache.
        precompiled_count: Number of entries added via precompile.
    """

    hits: int = 0
    misses: int = 0
    size: int = 0
    precompiled_count: int = 0


@dataclass
class CachedTypeAdapterData:
    """Cached data for a TypeAdapter.

    This stores the core schema, validator, and serializer that are
    expensive to compute. These can be reused across TypeAdapter instances.

    Attributes:
        core_schema: The core schema for the type.
        validator: The schema validator.
        serializer: The schema serializer.
        version: Version identifier for cache invalidation.
    """

    core_schema: CoreSchema
    validator: SchemaValidator | Any
    serializer: SchemaSerializer
    version: int = field(default=1)


@dataclass
class PrecompileFailure:
    """Information about a failed precompile attempt.

    Attributes:
        type_: The type that failed to precompile.
        config: The config used for precompilation.
        exception: The exception that was raised.
    """

    type_: Any
    config: ConfigDict | None
    exception: Exception

    def __str__(self) -> str:
        return f'PrecompileFailure(type={self.type_!r}, config={self.config!r}, error={self.exception!r})'


def _make_hashable(value: Any) -> Hashable:
    """Convert a value to a hashable form for use in cache keys.

    This is a conservative implementation - if a value cannot be reliably
    made hashable, it returns a sentinel value that will cause cache
    misses (intentional, to avoid incorrect cache hits).

    Args:
        value: The value to convert.

    Returns:
        A hashable representation of the value, or a unique sentinel
        if the value cannot be reliably hashed.
    """
    if isinstance(value, Hashable) and not isinstance(value, (list, dict, set)):
        try:
            hash(value)
            return value
        except Exception:
            return id(value)
    elif isinstance(value, dict):
        try:
            return frozenset((k, _make_hashable(v)) for k, v in sorted(value.items()))
        except Exception:
            return id(value)
    elif isinstance(value, (list, tuple)):
        try:
            return tuple(_make_hashable(v) for v in value)
        except Exception:
            return id(value)
    elif isinstance(value, set):
        try:
            return frozenset(_make_hashable(v) for v in value)
        except Exception:
            return id(value)
    else:
        return id(value)


_RELEVANT_CONFIG_KEYS: frozenset[str] = frozenset({
    'strict',
    'extra',
    'from_attributes',
    'str_strip_whitespace',
    'str_to_lower',
    'str_to_upper',
    'arbitrary_types_allowed',
    'use_enum_values',
    'validate_default',
    'loc_by_alias',
    'revalidate_instances',
    'ser_json_timedelta',
    'ser_json_temporal',
    'val_temporal_unit',
    'ser_json_bytes',
    'val_json_bytes',
    'ser_json_inf_nan',
    'coerce_numbers_to_str',
    'regex_engine',
    'validation_error_cause',
    'cache_strings',
    'validate_by_alias',
    'validate_by_name',
    'serialize_by_alias',
    'url_preserve_empty_path',
    'polymorphic_serialization',
})


def _config_to_cache_key(config: ConfigDict | None) -> frozenset[tuple[str, Hashable]]:
    """Convert a ConfigDict to a hashable form for cache key.

    Only includes config values that actually affect schema generation
    and validation behavior. If any value cannot be reliably hashed,
    returns a unique sentinel that will cause cache misses (safer than
    incorrect cache hits).

    Args:
        config: The ConfigDict to convert.

    Returns:
        A frozenset of key-value pairs that can be used in a cache key.
    """
    if config is None:
        return frozenset()

    items: list[tuple[str, Hashable]] = []
    for key in _RELEVANT_CONFIG_KEYS:
        if key in config:
            value = config[key]
            try:
                hashable_value = _make_hashable(value)
                items.append((key, hashable_value))
            except Exception:
                return frozenset({('_cannot_hash', id(value))})

    return frozenset(sorted(items))


def make_cache_key(type_: Any, config: ConfigDict | None) -> tuple[Any, frozenset[tuple[str, Hashable]]]:
    """Create a cache key from a type and config.

    This is a public function that can be used to inspect or debug
    cache key generation.

    Args:
        type_: The type to adapt.
        config: The configuration for the TypeAdapter.

    Returns:
        A tuple that can be used as a dictionary key.
    """
    config_key = _config_to_cache_key(config)
    return (type_, config_key)


@final
class TypeAdapterCache:
    """A thread-safe cache for TypeAdapter core schemas, validators, and serializers.

    This cache is designed for library authors who want to precompile
    commonly used types during process startup and avoid the overhead
    of repeated schema generation and validator/serializer construction.

    Example:
        >>> from pydantic import TypeAdapter, TypeAdapterCache
        >>>
        >>> cache = TypeAdapterCache()
        >>>
        >>> # Precompile types during startup
        >>> success, failures = cache.precompile([
        ...     (list[int], None),
        ...     (dict[str, int], None),
        ... ])
        >>>
        >>> # Use cache when creating TypeAdapters
        >>> ta = TypeAdapter(list[int], cache=cache)
        >>> ta.validate_python([1, 2, '3'])
        [1, 2, 3]

    Thread Safety:
        This class is fully thread-safe. All operations that modify or
        access the cache are protected by a reentrant lock.

    Cache Invalidation:
        - Manual clearing via `clear()`
        - Type-level invalidation via `invalidate_type()`
        - Version-based invalidation via `invalidate_version()`
    """

    def __init__(self, max_size: int | None = None) -> None:
        """Initialize the TypeAdapter cache.

        Args:
            max_size: Maximum number of entries to keep in the cache.
                If None, the cache can grow without bound. When the
                limit is reached, the oldest entries are evicted (FIFO).
        """
        self._cache: dict[Any, CachedTypeAdapterData] = {}
        self._lock = threading.RLock()
        self._max_size = max_size
        self._stats = CacheStats()
        self._version = 1

    def get(
        self, type_: Any, config: ConfigDict | None
    ) -> CachedTypeAdapterData | None:
        """Retrieve cached data for a type and config combination.

        Args:
            type_: The type to look up.
            config: The configuration for the TypeAdapter.

        Returns:
            The cached data if found, None otherwise.
        """
        key = make_cache_key(type_, config)

        with self._lock:
            if key in self._cache:
                self._stats.hits += 1
                return self._cache[key]
            self._stats.misses += 1
            return None

    def set(
        self,
        type_: Any,
        config: ConfigDict | None,
        core_schema: CoreSchema,
        validator: SchemaValidator | Any,
        serializer: SchemaSerializer,
    ) -> None:
        """Store data in the cache.

        Args:
            type_: The type being cached.
            config: The configuration for the TypeAdapter.
            core_schema: The core schema for the type.
            validator: The schema validator.
            serializer: The schema serializer.
        """
        key = make_cache_key(type_, config)

        with self._lock:
            if self._max_size is not None and len(self._cache) >= self._max_size:
                self._evict_one()

            self._cache[key] = CachedTypeAdapterData(
                core_schema=core_schema,
                validator=validator,
                serializer=serializer,
                version=self._version,
            )
            self._stats.size = len(self._cache)

    def _evict_one(self) -> None:
        """Evict one entry from the cache when max_size is reached.

        Uses FIFO strategy - evicts the first entry.
        """
        if self._cache:
            first_key = next(iter(self._cache.keys()))
            del self._cache[first_key]

    def precompile(
        self,
        types: Iterable[tuple[Any, ConfigDict | None]],
        *,
        _parent_depth: int = 2,
        raise_errors: bool = False,
    ) -> tuple[int, list[PrecompileFailure]]:
        """Precompile multiple types and store them in the cache.

        This is designed to be called during process startup to pre-warm
        the cache with commonly used types.

        Args:
            types: An iterable of (type, config) tuples to precompile.
            _parent_depth: Depth at which to search for the parent frame
                for resolving forward references. This is passed to TypeAdapter
                to correctly resolve namespaces.
            raise_errors: If True, raises a PrecompileError if any type
                fails to precompile. If False (default), collects failures
                in the returned list.

        Returns:
            A tuple of (success_count, failure_list).
            - success_count: Number of types successfully precompiled.
            - failure_list: List of PrecompileFailure objects for failed types.

        Raises:
            PrecompileError: If raise_errors=True and any type fails to precompile.

        Example:
            >>> cache = TypeAdapterCache()
            >>> success, failures = cache.precompile([
            ...     (list[int], None),
            ...     (dict[str, int], {'strict': True}),
            ... ])
            >>> print(f'Success: {success}, Failures: {len(failures)}')
            Success: 2, Failures: 0
        """
        from .type_adapter import TypeAdapter

        count = 0
        failures: list[PrecompileFailure] = []

        for type_, config in types:
            if self.get(type_, config) is None:
                try:
                    TypeAdapter(
                        type_,
                        config=config,
                        cache=self,
                        _parent_depth=_parent_depth + 1,
                    )
                    count += 1
                except Exception as e:
                    failures.append(PrecompileFailure(
                        type_=type_,
                        config=config,
                        exception=e,
                    ))

        with self._lock:
            self._stats.precompiled_count += count

        if raise_errors and failures:
            error_msg = f'Precompile failed for {len(failures)} type(s):'
            for f in failures:
                error_msg += f'\n  - {f}'
            raise PrecompileError(error_msg, failures)

        return count, failures

    def clear(self) -> None:
        """Clear all entries from the cache.

        This resets the cache to its initial empty state and increments
        the version number to invalidate any references to old cached data.
        """
        with self._lock:
            self._cache.clear()
            self._version += 1
            self._stats = CacheStats()

    def invalidate_type(self, type_: Any, config: ConfigDict | None = None) -> bool:
        """Invalidate a specific type from the cache.

        Args:
            type_: The type to invalidate.
            config: Optional config. If None, all config variants
                of this type will be invalidated.

        Returns:
            True if any entries were invalidated, False otherwise.
        """
        with self._lock:
            if config is not None:
                key = make_cache_key(type_, config)
                if key in self._cache:
                    del self._cache[key]
                    self._stats.size = len(self._cache)
                    return True
                return False
            else:
                keys_to_remove = [k for k in self._cache if k[0] == type_]
                for key in keys_to_remove:
                    del self._cache[key]
                self._stats.size = len(self._cache)
                return len(keys_to_remove) > 0

    def invalidate_version(self, version: int) -> int:
        """Invalidate all cache entries with a specific version.

        This can be used to invalidate entries that were created before
        a certain point in time.

        Args:
            version: The version to invalidate. All entries with this
                version will be removed.

        Returns:
            The number of entries invalidated.
        """
        with self._lock:
            keys_to_remove = [
                k for k, v in self._cache.items()
                if v.version == version
            ]
            for key in keys_to_remove:
                del self._cache[key]
            self._stats.size = len(self._cache)
            return len(keys_to_remove)

    def get_stats(self) -> CacheStats:
        """Get current cache statistics.

        Returns:
            A copy of the current CacheStats.
        """
        with self._lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                size=len(self._cache),
                precompiled_count=self._stats.precompiled_count,
            )

    def info(self) -> dict[str, Any]:
        """Get detailed information about the cache.

        Returns:
            A dictionary containing cache metadata and statistics.
        """
        with self._lock:
            return {
                'version': self._version,
                'max_size': self._max_size,
                'current_size': len(self._cache),
                'stats': {
                    'hits': self._stats.hits,
                    'misses': self._stats.misses,
                    'size': len(self._cache),
                    'precompiled_count': self._stats.precompiled_count,
                    'hit_rate': (
                        self._stats.hits / (self._stats.hits + self._stats.misses)
                        if (self._stats.hits + self._stats.misses) > 0
                        else 0.0
                    ),
                },
                'cached_types': [str(k[0]) for k in self._cache.keys()],
            }

    def __enter__(self) -> TypeAdapterCache:
        """Enter a temporary caching context.

        This creates a child cache that inherits entries from the parent
        but whose modifications do not affect the parent.

        Note: Due to Python's context manager semantics, `__exit__` is
        called on the parent cache, not the temporary one. The temporary
        cache will be garbage collected after exiting the context.

        Returns:
            A temporary TypeAdapterCache instance.

        Example:
            >>> parent_cache = TypeAdapterCache()
            >>> TypeAdapter(list[int], cache=parent_cache)
            >>>
            >>> with parent_cache as temp_cache:
            ...     # temp_cache inherits from parent_cache
            ...     TypeAdapter(dict[str, int], cache=temp_cache)
            ...     # Modifications to temp_cache don't affect parent_cache
            >>>
            >>> # parent_cache is unchanged
        """
        with self._lock:
            temp_cache = TypeAdapterCache(max_size=self._max_size)
            temp_cache._cache = self._cache.copy()
            temp_cache._stats = CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                size=len(self._cache),
                precompiled_count=self._stats.precompiled_count,
            )
            temp_cache._version = self._version
            return temp_cache

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the temporary caching context.

        Note: This is called on the parent cache, not the temporary one.
        The parent cache remains unchanged. The temporary cache will be
        garbage collected after exiting the context.
        """
        pass


class PrecompileError(Exception):
    """Exception raised when precompilation fails and raise_errors=True.

    Attributes:
        failures: List of PrecompileFailure objects containing details
            about each failed precompile attempt.
    """

    def __init__(self, message: str, failures: list[PrecompileFailure]) -> None:
        super().__init__(message)
        self.failures = failures


_global_cache: TypeAdapterCache | None = None
_global_cache_lock = threading.Lock()


def get_global_cache() -> TypeAdapterCache:
    """Get the global TypeAdapter cache instance.

    This is a singleton cache that can be used across an application.

    Returns:
        The global TypeAdapterCache instance.

    Example:
        >>> from pydantic import get_global_cache
        >>> cache = get_global_cache()
        >>> cache.precompile([(list[int], None)])
        (1, [])
    """
    global _global_cache
    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = TypeAdapterCache()
    return _global_cache
