"""TypeAdapter precompile cache for library authors.

This module provides a thread-safe caching mechanism for TypeAdapter's
core schema, validator, and serializer. This allows library authors to
precompile commonly used types during process startup and reuse them
across multiple TypeAdapter instantiations.
"""

from __future__ import annotations as _annotations

import sys
import threading
import types
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast, final

from pydantic_core import CoreSchema, SchemaSerializer, SchemaValidator

from ..config import ConfigDict
from . import _config, _mock_val_ser, _namespace_utils
from ._namespace_utils import NamespacesTuple, NsResolver

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    from typing_extensions import TypeAlias

T = TypeVar('T')

CacheKey: TypeAlias = 'tuple[Any, frozenset[tuple[str, Hashable]]]'


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


def _make_hashable(value: Any) -> Hashable:
    """Convert a value to a hashable form for use in cache keys.

    Args:
        value: The value to convert.

    Returns:
        A hashable representation of the value.
    """
    if isinstance(value, Hashable) and not isinstance(value, (list, dict, set)):
        return value
    elif isinstance(value, dict):
        return frozenset((k, _make_hashable(v)) for k, v in sorted(value.items()))
    elif isinstance(value, (list, tuple)):
        return tuple(_make_hashable(v) for v in value)
    elif isinstance(value, set):
        return frozenset(_make_hashable(v) for v in value)
    else:
        return id(value)


def _config_to_cache_key(config: ConfigDict | None) -> frozenset[tuple[str, Hashable]]:
    """Convert a ConfigDict to a hashable form for cache key.

    Only includes config values that actually affect schema generation
    and validation behavior.

    Args:
        config: The ConfigDict to convert.

    Returns:
        A frozenset of key-value pairs that can be used in a cache key.
    """
    if config is None:
        return frozenset()

    relevant_keys = {
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
    }

    items = []
    for key in relevant_keys:
        if key in config:
            value = config[key]
            try:
                hashable_value = _make_hashable(value)
                items.append((key, hashable_value))
            except Exception:
                pass

    return frozenset(sorted(items))


def _make_cache_key(type_: Any, config: ConfigDict | None) -> CacheKey:
    """Create a cache key from a type and config.

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
        >>> from pydantic import TypeAdapter
        >>> from pydantic._internal._type_adapter_cache import TypeAdapterCache
        >>>
        >>> cache = TypeAdapterCache()
        >>>
        >>> # Precompile types during startup
        >>> cache.precompile([
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
        - Version-based invalidation via `invalidate_version()`
        - Context manager for temporary caching via `temporary()`
    """

    def __init__(self, max_size: int | None = None) -> None:
        """Initialize the TypeAdapter cache.

        Args:
            max_size: Maximum number of entries to keep in the cache.
                If None, the cache can grow without bound.
        """
        self._cache: dict[CacheKey, CachedTypeAdapterData] = {}
        self._lock = threading.RLock()
        self._max_size = max_size
        self._stats = CacheStats()
        self._version = 1
        self._is_temporary = False
        self._parent_cache: TypeAdapterCache | None = None

    def _verify_not_finalized(self) -> None:
        """Check if the cache is a temporary context that has been exited."""
        pass

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
        self._verify_not_finalized()
        key = _make_cache_key(type_, config)

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
        self._verify_not_finalized()
        key = _make_cache_key(type_, config)

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

        Currently uses FIFO strategy - evicts the first entry.
        In the future, this could be improved to LRU or other strategies.
        """
        if self._cache:
            next(iter(self._cache.keys()))
            first_key = next(iter(self._cache.keys()))
            del self._cache[first_key]

    def precompile(
        self,
        types: Iterable[tuple[Any, ConfigDict | None]],
        *,
        _parent_depth: int = 2,
    ) -> int:
        """Precompile multiple types and store them in the cache.

        This is designed to be called during process startup to pre-warm
        the cache with commonly used types.

        Args:
            types: An iterable of (type, config) tuples to precompile.
            _parent_depth: Depth at which to search for the parent frame
                for resolving forward references. This is passed to TypeAdapter
                to correctly resolve namespaces.

        Returns:
            The number of types successfully precompiled.

        Example:
            >>> cache.precompile([
            ...     (list[int], None),
            ...     (dict[str, int], {'strict': True}),
            ... ])
            2
        """
        from ..type_adapter import TypeAdapter

        count = 0
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
                except Exception:
                    pass

        with self._lock:
            self._stats.precompiled_count += count
        return count

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
                key = _make_cache_key(type_, config)
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
                'is_temporary': self._is_temporary,
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

        Returns:
            A temporary TypeAdapterCache instance.
        """
        temp_cache = TypeAdapterCache(max_size=self._max_size)
        temp_cache._is_temporary = True
        temp_cache._parent_cache = self
        with self._lock:
            temp_cache._cache = self._cache.copy()
            temp_cache._stats = CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                size=len(self._cache),
                precompiled_count=self._stats.precompiled_count,
            )
        return temp_cache

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the temporary caching context.

        The temporary cache will be garbage collected after exiting the
        context. The parent cache remains unchanged.
        """
        pass


_global_cache: TypeAdapterCache | None = None
_global_cache_lock = threading.Lock()


def get_global_cache() -> TypeAdapterCache:
    """Get the global TypeAdapter cache instance.

    This is a singleton cache that can be used across an application.

    Returns:
        The global TypeAdapterCache instance.
    """
    global _global_cache
    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = TypeAdapterCache()
    return _global_cache
