"""Tests for TypeAdapter precompile cache functionality."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest

from pydantic import (
    BaseModel,
    CacheStats,
    ConfigDict,
    TypeAdapter,
    TypeAdapterCache,
    get_global_cache,
    make_cache_key,
)
from pydantic.type_adapter_cache import CacheDisabled, PrecompileError, PrecompileFailure


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

        result_cache = TypeAdapter.precompile(
            [(list[int], None), (dict[str, int], None)],
            cache=cache,
        )

        assert result_cache is cache

        stats = cache.get_stats()
        assert stats.size == 2

    def test_precompile_with_global_cache(self) -> None:
        """Test precompile with global cache."""
        TypeAdapter.clear_cache(_use_global_cache=True)

        result_cache = TypeAdapter.precompile(
            [(list[int], None), (dict[str, int], None)],
            _use_global_cache=True,
        )

        global_cache = get_global_cache()
        assert result_cache is global_cache

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

    def test_cache_precompile_returns_failures(self) -> None:
        """Test that cache.precompile returns failures instead of swallowing exceptions."""
        invalid_type = 'not a valid type'

        cache = TypeAdapterCache()
        success, failures = cache.precompile([
            (list[int], None),
            (invalid_type, None),
        ])

        assert success == 1
        assert len(failures) == 1
        assert failures[0].type_ == invalid_type
        assert isinstance(failures[0].exception, Exception)

    def test_cache_precompile_raises_with_raise_errors(self) -> None:
        """Test that cache.precompile raises PrecompileError when raise_errors=True."""
        invalid_type = 'not a valid type'

        cache = TypeAdapterCache()
        with pytest.raises(PrecompileError) as exc_info:
            cache.precompile(
                [(list[int], None), (invalid_type, None)],
                raise_errors=True,
            )

        assert 'Precompile failed' in str(exc_info.value)
        assert len(exc_info.value.failures) == 1
        assert exc_info.value.failures[0].type_ == invalid_type


class TestTypeAdapterCacheManagement:
    """Tests for cache management static methods."""

    def test_clear_cache(self) -> None:
        """Test clearing cache via static method."""
        cache = TypeAdapterCache()
        cache.precompile([(list[int], None)])

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
        cache.precompile(
            [(list[int], None), (dict[str, int], None)],
        )

        assert cache.get_stats().size == 2

        result = TypeAdapter.invalidate_type(list[int], cache=cache)
        assert result is True

        assert cache.get_stats().size == 1


class TestThreadSafety:
    """Tests for thread safety of the cache."""

    def test_concurrent_reads(self) -> None:
        """Test that concurrent reads are thread-safe."""
        cache = TypeAdapterCache()
        cache.precompile([(list[int], None)])

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
        key1 = make_cache_key(list[int], None)
        key2 = make_cache_key(list[int], None)

        assert key1 == key2
        assert hash(key1) == hash(key2)

    def test_cache_key_different_types(self) -> None:
        """Test that different types produce different keys."""
        key1 = make_cache_key(list[int], None)
        key2 = make_cache_key(list[str], None)

        assert key1 != key2

    def test_cache_key_different_configs(self) -> None:
        """Test that different configs produce different keys."""
        key1 = make_cache_key(list[int], None)
        key2 = make_cache_key(list[int], {'strict': True})

        assert key1 != key2


class TestConservativeCaching:
    """Tests for conservative caching strategy.

    These tests verify that:
    1. Types with forward references are NOT cached
    2. Configs with unhashable values are NOT cached
    3. CacheDisabled exception is raised internally
    """

    def test_forward_ref_type_not_cached(self) -> None:
        """Test that types with forward references are not cached."""
        from typing import ForwardRef

        cache = TypeAdapterCache()

        UserRef = ForwardRef('list[int]')
        TypeAdapter(UserRef, cache=cache)

        stats = cache.get_stats()
        assert stats.size == 0
        assert stats.disabled_count == 1

    def test_string_forward_ref_not_cached(self) -> None:
        """Test that string forward references are not cached."""
        cache = TypeAdapterCache()

        TypeAdapter('list[int]', cache=cache)

        stats = cache.get_stats()
        assert stats.size == 0
        assert stats.disabled_count == 1

    def test_unhashable_config_not_cached(self) -> None:
        """Test that configs with unhashable values are not cached.

        Important: The new behavior is that unhashable configs simply
        disable caching (all misses, no hits), rather than using id()
        which could cause unpredictable behavior.

        This test directly tests the cache methods (get/set) rather than
        going through TypeAdapter, because TypeAdapter requires config
        values to be valid for their config keys.
        """

        class UnhashableClass:
            __hash__ = None

        unhashable_obj = UnhashableClass()
        config = {'strict': unhashable_obj}

        cache = TypeAdapterCache()

        with pytest.raises(CacheDisabled):
            make_cache_key(list[int], config)

        result = cache.get(list[int], config)
        assert result is None

        stats = cache.get_stats()
        assert stats.misses == 1
        assert stats.disabled_count == 1
        assert stats.size == 0

        set_result = cache.set(
            list[int],
            config,
            core_schema={'type': 'list'},
            validator=None,
            serializer=None,
        )
        assert set_result is False

        stats = cache.get_stats()
        assert stats.size == 0
        assert stats.disabled_count == 1

        result2 = cache.get(list[int], config)
        assert result2 is None

        stats = cache.get_stats()
        assert stats.misses == 2
        assert stats.disabled_count == 2
        assert stats.hits == 0

    def test_nested_unhashable_config_not_cached(self) -> None:
        """Test that configs with nested unhashable values are not cached."""
        cache = TypeAdapterCache()

        config_with_dict = {'extra': {'key': 'value'}}
        TypeAdapter(list[int], config=config_with_dict, cache=cache)

        stats = cache.get_stats()
        assert stats.size == 1
        assert stats.disabled_count == 0

    def test_cache_disabled_reason_available(self) -> None:
        """Test that CacheDisabled exception contains a reason."""

        class UnhashableClass:
            __hash__ = None

        unhashable_obj = UnhashableClass()
        config = {'strict': unhashable_obj}

        with pytest.raises(CacheDisabled) as exc_info:
            make_cache_key(list[int], config)

        assert 'unhashable' in exc_info.value.reason.lower()


class TestPublicAPIs:
    """Tests for public API exports."""

    def test_cache_stats_dataclass(self) -> None:
        """Test that CacheStats is a proper dataclass."""
        stats = CacheStats(
            hits=1,
            misses=2,
            size=3,
            precompiled_count=4,
            disabled_count=5,
        )
        assert stats.hits == 1
        assert stats.misses == 2
        assert stats.size == 3
        assert stats.precompiled_count == 4
        assert stats.disabled_count == 5

    def test_precompile_failure_dataclass(self) -> None:
        """Test that PrecompileFailure is a proper dataclass."""
        exc = ValueError('test error')
        failure = PrecompileFailure(type_=list[int], config={'strict': True}, exception=exc)

        assert failure.type_ == list[int]
        assert failure.config == {'strict': True}
        assert failure.exception is exc

    def test_precompile_error_exception(self) -> None:
        """Test that PrecompileError is a proper exception."""
        failures = [
            PrecompileFailure(type_=list[int], config=None, exception=ValueError('test')),
        ]
        error = PrecompileError('Test error', failures)

        assert str(error) == 'Test error'
        assert error.failures == failures

    def test_get_global_cache_singleton(self) -> None:
        """Test that get_global_cache returns a singleton."""
        cache1 = get_global_cache()
        cache2 = get_global_cache()

        assert cache1 is cache2
        assert isinstance(cache1, TypeAdapterCache)
