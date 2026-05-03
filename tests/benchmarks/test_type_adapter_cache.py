"""Benchmark tests for TypeAdapter cache functionality.

This module provides benchmarks to demonstrate the performance benefits
of using TypeAdapterCache and precompile functionality.

To run these benchmarks:
    pytest tests/benchmarks/test_type_adapter_cache.py --benchmark-autosave
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from pydantic import BaseModel, ConfigDict, TypeAdapter, TypeAdapterCache, get_global_cache
from pydantic.type_adapter_cache import PrecompileError


class BenchmarkModel(BaseModel):
    x: int
    y: str
    z: dict[str, list[int]]
    optional_field: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


@dataclass
class BenchmarkDataclass:
    x: int
    y: str
    z: dict[str, list[int]]


BenchmarkType = dict[str, list[tuple[int, str]]]


def get_types_to_test() -> list[tuple[Any, ConfigDict | None]]:
    return [
        (list[int], None),
        (dict[str, int], None),
        (BenchmarkModel, None),
        (BenchmarkDataclass, None),
        (BenchmarkType, None),
        (list[int], {'strict': True}),
        (dict[str, int], {'extra': 'forbid'}),
    ]


pytestmark = pytest.mark.benchmark(group='type_adapter_cache')


class TestTypeAdapterCreation:
    """Benchmarks for TypeAdapter creation performance."""

    def test_without_cache(self, benchmark) -> None:
        """Benchmark creating TypeAdapter instances without caching.

        This simulates the scenario where TypeAdapters are created repeatedly
        without any caching mechanism.
        """

        def create_type_adapters() -> None:
            TypeAdapter(list[int])
            TypeAdapter(dict[str, int])
            TypeAdapter(BenchmarkModel)
            TypeAdapter(BenchmarkDataclass)
            TypeAdapter(BenchmarkType)

        benchmark(create_type_adapters)

    def test_with_cache(self, benchmark) -> None:
        """Benchmark creating TypeAdapter instances with caching.

        This simulates the scenario where TypeAdapters are created repeatedly
        with a shared cache, allowing for reuse of previously built validators.
        """
        cache = TypeAdapterCache()
        TypeAdapter(list[int], cache=cache)
        TypeAdapter(dict[str, int], cache=cache)
        TypeAdapter(BenchmarkModel, cache=cache)
        TypeAdapter(BenchmarkDataclass, cache=cache)
        TypeAdapter(BenchmarkType, cache=cache)

        def create_type_adapters() -> None:
            TypeAdapter(list[int], cache=cache)
            TypeAdapter(dict[str, int], cache=cache)
            TypeAdapter(BenchmarkModel, cache=cache)
            TypeAdapter(BenchmarkDataclass, cache=cache)
            TypeAdapter(BenchmarkType, cache=cache)

        benchmark(create_type_adapters)

    def test_with_precompile(self, benchmark) -> None:
        """Benchmark creating TypeAdapter instances with precompiled cache.

        This simulates the optimal scenario where all commonly used types are
        precompiled during process startup, and subsequent TypeAdapter creations
        are all cache hits.
        """
        cache = TypeAdapterCache()
        cache.precompile(get_types_to_test())

        def create_type_adapters() -> None:
            TypeAdapter(list[int], cache=cache)
            TypeAdapter(dict[str, int], cache=cache)
            TypeAdapter(BenchmarkModel, cache=cache)
            TypeAdapter(BenchmarkDataclass, cache=cache)
            TypeAdapter(BenchmarkType, cache=cache)

        benchmark(create_type_adapters)


class TestPrecompilePerformance:
    """Benchmarks for the precompile operation itself."""

    def test_precompile_basic_types(self, benchmark) -> None:
        """Benchmark precompiling basic types."""
        types_to_precompile = [
            (list[int], None),
            (dict[str, int], None),
            (list[str], None),
            (dict[int, str], None),
        ]

        def run_precompile() -> tuple[int, list]:
            cache = TypeAdapterCache()
            return cache.precompile(types_to_precompile)

        benchmark(run_precompile)

    def test_precompile_with_complex_types(self, benchmark) -> None:
        """Benchmark precompiling complex types (models, dataclasses)."""

        def run_precompile() -> tuple[int, list]:
            cache = TypeAdapterCache()
            return cache.precompile(get_types_to_test())

        benchmark(run_precompile)


class TestValidationPerformance:
    """Benchmarks to verify that caching does not affect validation performance.

    These benchmarks ensure that using a cached TypeAdapter performs identically
    to a non-cached one for validation operations.
    """

    def test_validation_without_cache(self, benchmark) -> None:
        """Benchmark validation performance without cache."""
        ta = TypeAdapter(BenchmarkModel)
        data = {'x': 42, 'y': 'hello', 'z': {'key': [1, 2, 3]}}

        benchmark(ta.validate_python, data)

    def test_validation_with_cache(self, benchmark) -> None:
        """Benchmark validation performance with cache."""
        cache = TypeAdapterCache()
        ta = TypeAdapter(BenchmarkModel, cache=cache)
        data = {'x': 42, 'y': 'hello', 'z': {'key': [1, 2, 3]}}

        benchmark(ta.validate_python, data)


class TestSerializationPerformance:
    """Benchmarks to verify that caching does not affect serialization performance.

    These benchmarks ensure that using a cached TypeAdapter performs identically
    to a non-cached one for serialization operations.
    """

    def test_serialization_without_cache(self, benchmark) -> None:
        """Benchmark serialization performance without cache."""
        ta = TypeAdapter(BenchmarkModel)
        instance = BenchmarkModel(x=42, y='hello', z={'key': [1, 2, 3]})

        benchmark(ta.dump_json, instance)

    def test_serialization_with_cache(self, benchmark) -> None:
        """Benchmark serialization performance with cache."""
        cache = TypeAdapterCache()
        ta = TypeAdapter(BenchmarkModel, cache=cache)
        instance = BenchmarkModel(x=42, y='hello', z={'key': [1, 2, 3]})

        benchmark(ta.dump_json, instance)


class TestCacheManagement:
    """Benchmarks for cache management operations."""

    def test_get_stats(self, benchmark) -> None:
        """Benchmark getting cache statistics."""
        cache = TypeAdapterCache()
        cache.precompile(get_types_to_test())

        benchmark(cache.get_stats)

    def test_info(self, benchmark) -> None:
        """Benchmark getting detailed cache info."""
        cache = TypeAdapterCache()
        cache.precompile(get_types_to_test())

        benchmark(cache.info)

    def test_clear(self, benchmark) -> None:
        """Benchmark clearing the cache.

        Note: This creates and populates a new cache for each iteration.
        """

        def clear_cache() -> None:
            cache = TypeAdapterCache()
            cache.precompile(get_types_to_test())
            cache.clear()

        benchmark(clear_cache)

    def test_invalidate_type(self, benchmark) -> None:
        """Benchmark invalidating a type from cache."""
        cache = TypeAdapterCache()
        cache.precompile(get_types_to_test())

        benchmark(cache.invalidate_type, list[int])


class TestGlobalCache:
    """Benchmarks for global cache usage."""

    def setup_method(self) -> None:
        """Clear global cache before each test."""
        get_global_cache().clear()

    def test_global_cache_precompile(self, benchmark) -> None:
        """Benchmark using global cache with precompile."""

        def run_with_global_cache() -> None:
            cache = get_global_cache()
            cache.precompile(get_types_to_test())
            TypeAdapter(list[int], cache=cache)
            TypeAdapter(dict[str, int], cache=cache)

        benchmark(run_with_global_cache)


class TestPrecompileErrorHandling:
    """Tests (not benchmarks) for precompile error handling.

    These are functional tests rather than benchmarks, but they're included
    here to ensure the error handling works correctly.
    """

    def test_precompile_returns_failures(self) -> None:
        """Test that precompile returns failures instead of swallowing exceptions."""
        invalid_type = 'not a valid type'

        cache = TypeAdapterCache()
        success, failures = cache.precompile([
            (list[int], None),
            (invalid_type, None),
        ])

        assert success == 1
        assert len(failures) == 1
        assert failures[0].type_ == invalid_type

    def test_precompile_raises_with_raise_errors(self) -> None:
        """Test that precompile raises PrecompileError when raise_errors=True."""
        invalid_type = 'not a valid type'

        cache = TypeAdapterCache()
        with pytest.raises(PrecompileError) as exc_info:
            cache.precompile(
                [(list[int], None), (invalid_type, None)],
                raise_errors=True,
            )

        assert 'Precompile failed' in str(exc_info.value)
        assert len(exc_info.value.failures) == 1
