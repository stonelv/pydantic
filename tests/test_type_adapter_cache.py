"""Tests for TypeAdapter precompile cache functionality."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest

from pydantic import BaseModel, ConfigDict, TypeAdapter, TypeAdapterCache, get_global_cache
from pydantic._internal._type_adapter_cache import CacheStats, _make_cache_key


class TestTypeAdapterCache:
    """Tests for TypeAdapterCache class."""

    def test_cache_basic_operations(self) -> None:
        """Test basic cache get/set operations."""
        cache = TypeAdapterCache()

        ta1 = TypeAdapter(list[int], cache=cache)
        result1 = ta1.validate_python([1, '2', '3'])
        assert result1 == [1, 2, 3]

        stats = cache.get_stats()
        assert stats.misses == 1
        assert stats.hits == 0

        ta2 = TypeAdapter(list[int], cache=cache)
        result2 = ta2.validate_python([1, '2', '3'])
        assert result2 == [1, 2, 3]

        stats = cache.get_stats()
        assert stats.misses == 1
        assert stats.hits == 1

    def test_cache_with_different_configs(self) -> None:
        """Test that different configs create different cache entries."""
        cache = TypeAdapterCache()

        ta1 = TypeAdapter(list[int], cache=cache)
        ta1.validate_python([1])

        ta2 = TypeAdapter(list[int], config={'strict': True}, cache=cache)
        with pytest.raises(Exception):
            ta2.validate_python(['1'])

        stats = cache.get_stats()
        assert stats.size == 2
        assert stats.misses == 2
        assert stats.hits == 0

    def test_cache_clear(self) -> None:
        """Test cache clearing functionality."""
        cache = TypeAdapterCache()

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(dict[str, int], cache=cache)

        stats = cache.get_stats()
        assert stats.size == 2

        cache.clear()

        stats = cache.get_stats()
        assert stats.size == 0
        assert stats.hits == 0
        assert stats.misses == 0

    def test_cache_invalidate_type(self) -> None:
        """Test invalidating specific types from cache."""
        cache = TypeAdapterCache()

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(dict[str, int], cache=cache)
        TypeAdapter(list[int], config={'strict': True}, cache=cache)

        stats = cache.get_stats()
        assert stats.size == 3

        result = cache.invalidate_type(list[int])
        assert result is True

        stats = cache.get_stats()
        assert stats.size == 1

        result = cache.invalidate_type(list[int])
        assert result is False

    def test_cache_invalidate_type_with_config(self) -> None:
        """Test invalidating specific type with specific config."""
        cache = TypeAdapterCache()

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(list[int], config={'strict': True}, cache=cache)

        stats = cache.get_stats()
        assert stats.size == 2

        result = cache.invalidate_type(list[int], config={'strict': True})
        assert result is True

        stats = cache.get_stats()
        assert stats.size == 1

    def test_cache_max_size(self) -> None:
        """Test cache with max_size limit."""
        cache = TypeAdapterCache(max_size=2)

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(dict[str, int], cache=cache)

        stats = cache.get_stats()
        assert stats.size == 2

        TypeAdapter(list[str], cache=cache)

        stats = cache.get_stats()
        assert stats.size == 2

    def test_cache_info(self) -> None:
        """Test cache info method."""
        cache = TypeAdapterCache()

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(list[int], cache=cache)

        info = cache.info()
        assert info['current_size'] == 1
        assert info['stats']['hits'] == 1
        assert info['stats']['misses'] == 1
        assert 'list[int]' in str(info['cached_types'])


class TestTypeAdapterPrecompile:
    """Tests for TypeAdapter.precompile static method."""

    def test_precompile_creates_cache(self) -> None:
        """Test that precompile creates and returns a cache."""
        cache = TypeAdapter.precompile([
            (list[int], None),
            (dict[str, int], None),
        ])

        assert isinstance(cache, TypeAdapterCache)

        stats = cache.get_stats()
        assert stats.size == 2
        assert stats.precompiled_count == 2

    def test_precompile_with_existing_cache(self) -> None:
        """Test precompile with an existing cache."""
        cache = TypeAdapterCache()

        TypeAdapter.precompile(
            [(list[int], None), (dict[str, int], None)],
            cache=cache,
        )

        stats = cache.get_stats()
        assert stats.size == 2

    def test_precompile_with_global_cache(self) -> None:
        """Test precompile with global cache."""
        TypeAdapter.clear_cache(_use_global_cache=True)

        TypeAdapter.precompile(
            [(list[int], None), (dict[str, int], None)],
            _use_global_cache=True,
        )

        global_cache = get_global_cache()
        stats = global_cache.get_stats()
        assert stats.size == 2

    def test_precompile_reuses_cache(self) -> None:
        """Test that TypeAdapter uses precompiled cache entries."""
        cache = TypeAdapter.precompile([
            (list[int], None),
        ])

        stats_before = cache.get_stats()
        assert stats_before.hits == 0

        ta = TypeAdapter(list[int], cache=cache)
        result = ta.validate_python([1, '2', '3'])
        assert result == [1, 2, 3]

        stats_after = cache.get_stats()
        assert stats_after.hits == 1


class TestTypeAdapterCacheManagement:
    """Tests for cache management static methods."""

    def test_clear_cache(self) -> None:
        """Test clearing cache via static method."""
        cache = TypeAdapterCache()
        TypeAdapter.precompile([(list[int], None)], cache=cache)

        assert cache.get_stats().size == 1

        TypeAdapter.clear_cache(cache=cache)

        assert cache.get_stats().size == 0

    def test_get_cache_stats(self) -> None:
        """Test getting cache stats via static method."""
        cache = TypeAdapterCache()

        TypeAdapter(list[int], cache=cache)
        TypeAdapter(list[int], cache=cache)

        stats = TypeAdapter.get_cache_stats(cache=cache)
        assert stats['hits'] == 1
        assert stats['misses'] == 1
        assert stats['size'] == 1

    def test_invalidate_type_static(self) -> None:
        """Test invalidating type via static method."""
        cache = TypeAdapterCache()
        TypeAdapter.precompile([(list[int], None), (dict[str, int], None)], cache=cache)

        assert cache.get_stats().size == 2

        result = TypeAdapter.invalidate_type(list[int], cache=cache)
        assert result is True

        assert cache.get_stats().size == 1


class TestThreadSafety:
    """Tests for thread safety of the cache."""

    def test_concurrent_reads(self) -> None:
        """Test that concurrent reads are thread-safe."""
        cache = TypeAdapterCache()
        TypeAdapter.precompile([(list[int], None)], cache=cache)

        results: list[bool] = []
        errors: list[Exception] = []

        def read_cache() -> None:
            try:
                for _ in range(100):
                    ta = TypeAdapter(list[int], cache=cache)
                    result = ta.validate_python([1, '2'])
                    results.append(result == [1, 2])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_cache) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(results)

    def test_concurrent_writes(self) -> None:
        """Test that concurrent writes are thread-safe."""
        cache = TypeAdapterCache()
        errors: list[Exception] = []

        types_to_write = [
            list[int],
            list[str],
            dict[str, int],
            dict[int, str],
            tuple[int, str],
        ]

        def write_cache(t: Any) -> None:
            try:
                for _ in range(10):
                    TypeAdapter(t, cache=cache)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_cache, args=(t,)) for t in types_to_write]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        stats = cache.get_stats()
        assert stats.size == 5


class TestIntegrationWithModels:
    """Tests for cache integration with Pydantic models."""

    def test_cache_with_basemodel(self) -> None:
        """Test that cache works with BaseModel types."""

        class MyModel(BaseModel):
            x: int
            y: str

        cache = TypeAdapterCache()

        ta1 = TypeAdapter(MyModel, cache=cache)
        result1 = ta1.validate_python({'x': '1', 'y': 'hello'})
        assert result1.x == 1
        assert result1.y == 'hello'

        stats = cache.get_stats()
        assert stats.misses == 1

        ta2 = TypeAdapter(MyModel, cache=cache)
        result2 = ta2.validate_python({'x': '2', 'y': 'world'})
        assert result2.x == 2

        stats = cache.get_stats()
        assert stats.hits == 1

    def test_cache_with_dataclass(self) -> None:
        """Test that cache works with dataclasses."""

        @dataclass
        class MyDataclass:
            x: int
            y: str

        cache = TypeAdapterCache()

        ta1 = TypeAdapter(MyDataclass, cache=cache)
        result1 = ta1.validate_python({'x': '1', 'y': 'hello'})
        assert result1.x == 1
        assert result1.y == 'hello'

        stats = cache.get_stats()
        assert stats.misses == 1

        ta2 = TypeAdapter(MyDataclass, cache=cache)
        stats = cache.get_stats()
        assert stats.hits == 1


class TestTemporaryCache:
    """Tests for temporary cache context manager."""

    def test_temporary_cache_context(self) -> None:
        """Test that temporary cache context works correctly."""
        parent_cache = TypeAdapterCache()
        TypeAdapter(list[int], cache=parent_cache)

        assert parent_cache.get_stats().size == 1

        with parent_cache as temp_cache:
            TypeAdapter(dict[str, int], cache=temp_cache)

            assert temp_cache.get_stats().size == 2

            TypeAdapter(list[int], cache=temp_cache)
            assert temp_cache.get_stats().hits == 1

        assert parent_cache.get_stats().size == 1


class TestCacheKeyGeneration:
    """Tests for cache key generation."""

    def test_cache_key_equality(self) -> None:
        """Test that same types with same config produce same keys."""
        key1 = _make_cache_key(list[int], None)
        key2 = _make_cache_key(list[int], None)

        assert key1 == key2
        assert hash(key1) == hash(key2)

    def test_cache_key_different_types(self) -> None:
        """Test that different types produce different keys."""
        key1 = _make_cache_key(list[int], None)
        key2 = _make_cache_key(list[str], None)

        assert key1 != key2

    def test_cache_key_different_configs(self) -> None:
        """Test that different configs produce different keys."""
        key1 = _make_cache_key(list[int], None)
        key2 = _make_cache_key(list[int], {'strict': True})

        assert key1 != key2
