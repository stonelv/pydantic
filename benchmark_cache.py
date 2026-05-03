"""Benchmark tests for TypeAdapter cache functionality.

This module provides benchmarks to demonstrate the performance benefits
of using TypeAdapterCache and precompile functionality.
"""

import timeit
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic._internal._type_adapter_cache import TypeAdapterCache, get_global_cache


class ComplexModel(BaseModel):
    x: int
    y: str
    z: dict[str, list[int]]
    optional_field: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


@dataclass
class ComplexDataclass:
    x: int
    y: str
    z: dict[str, list[int]]


ComplexType = dict[str, list[tuple[int, str]]]


def benchmark_without_cache() -> float:
    """Benchmark creating TypeAdapter instances without caching.

    This simulates the scenario where TypeAdapters are created repeatedly
    without any caching mechanism.
    """
    setup = '''
from pydantic import TypeAdapter
from __main__ import ComplexModel, ComplexDataclass, ComplexType
'''
    stmt = '''
ta1 = TypeAdapter(list[int])
ta2 = TypeAdapter(dict[str, int])
ta3 = TypeAdapter(ComplexModel)
ta4 = TypeAdapter(ComplexDataclass)
ta5 = TypeAdapter(ComplexType)
'''

    return timeit.timeit(stmt, setup=setup, number=100)


def benchmark_with_cache() -> float:
    """Benchmark creating TypeAdapter instances with caching.

    This simulates the scenario where TypeAdapters are created repeatedly
    with a shared cache, allowing for reuse of previously built validators.
    """
    setup = '''
from pydantic import TypeAdapter
from pydantic._internal._type_adapter_cache import TypeAdapterCache
from __main__ import ComplexModel, ComplexDataclass, ComplexType

cache = TypeAdapterCache()
'''
    stmt = '''
ta1 = TypeAdapter(list[int], cache=cache)
ta2 = TypeAdapter(dict[str, int], cache=cache)
ta3 = TypeAdapter(ComplexModel, cache=cache)
ta4 = TypeAdapter(ComplexDataclass, cache=cache)
ta5 = TypeAdapter(ComplexType, cache=cache)
'''

    return timeit.timeit(stmt, setup=setup, number=100)


def benchmark_with_precompile() -> float:
    """Benchmark creating TypeAdapter instances with precompiled cache.

    This simulates the optimal scenario where all commonly used types are
    precompiled during process startup, and subsequent TypeAdapter creations
    are all cache hits.
    """
    setup = '''
from pydantic import TypeAdapter
from pydantic._internal._type_adapter_cache import TypeAdapterCache
from __main__ import ComplexModel, ComplexDataclass, ComplexType

cache = TypeAdapterCache()
# Precompile all types first
cache.precompile([
    (list[int], None),
    (dict[str, int], None),
    (ComplexModel, None),
    (ComplexDataclass, None),
    (ComplexType, None),
])
'''
    stmt = '''
ta1 = TypeAdapter(list[int], cache=cache)
ta2 = TypeAdapter(dict[str, int], cache=cache)
ta3 = TypeAdapter(ComplexModel, cache=cache)
ta4 = TypeAdapter(ComplexDataclass, cache=cache)
ta5 = TypeAdapter(ComplexType, cache=cache)
'''

    return timeit.timeit(stmt, setup=setup, number=100)


def benchmark_validation_performance() -> tuple[float, float]:
    """Benchmark validation performance with and without cache.

    This demonstrates that caching does not impact validation performance,
    only TypeAdapter creation performance.
    """
    setup = '''
from pydantic import TypeAdapter
from pydantic._internal._type_adapter_cache import TypeAdapterCache
from __main__ import ComplexModel

cache = TypeAdapterCache()
ta_without = TypeAdapter(ComplexModel)
ta_with = TypeAdapter(ComplexModel, cache=cache)
data = {'x': 42, 'y': 'hello', 'z': {'key': [1, 2, 3]}}
'''

    stmt_without = '''
result = ta_without.validate_python(data)
'''
    stmt_with = '''
result = ta_with.validate_python(data)
'''

    time_without = timeit.timeit(stmt_without, setup=setup, number=10000)
    time_with = timeit.timeit(stmt_with, setup=setup, number=10000)

    return time_without, time_with


def benchmark_serialization_performance() -> tuple[float, float]:
    """Benchmark serialization performance with and without cache.

    This demonstrates that caching does not impact serialization performance,
    only TypeAdapter creation performance.
    """
    setup = '''
from pydantic import TypeAdapter
from pydantic._internal._type_adapter_cache import TypeAdapterCache
from __main__ import ComplexModel

cache = TypeAdapterCache()
ta_without = TypeAdapter(ComplexModel)
ta_with = TypeAdapter(ComplexModel, cache=cache)
instance = ComplexModel(x=42, y='hello', z={'key': [1, 2, 3]})
'''

    stmt_without = '''
result = ta_without.dump_json(instance)
'''
    stmt_with = '''
result = ta_with.dump_json(instance)
'''

    time_without = timeit.timeit(stmt_without, setup=setup, number=10000)
    time_with = timeit.timeit(stmt_with, setup=setup, number=10000)

    return time_without, time_with


def run_benchmarks() -> None:
    """Run all benchmarks and print results."""
    print("=" * 80)
    print("TypeAdapter Cache Benchmark Results")
    print("=" * 80)

    print("\n1. TypeAdapter Creation Performance (100 iterations)")
    print("-" * 60)

    time_without = benchmark_without_cache()
    time_with = benchmark_with_cache()
    time_precompiled = benchmark_with_precompile()

    print(f"  Without cache:     {time_without:.4f}s")
    print(f"  With cache:        {time_with:.4f}s")
    print(f"  With precompile:   {time_precompiled:.4f}s")
    print()
    print(f"  Cache speedup:     {time_without / time_with:.2f}x")
    print(f"  Precompile speedup:{time_without / time_precompiled:.2f}x")

    print("\n2. Validation Performance (10,000 iterations)")
    print("-" * 60)
    time_val_without, time_val_with = benchmark_validation_performance()
    print(f"  Without cache:     {time_val_without:.4f}s")
    print(f"  With cache:        {time_val_with:.4f}s")
    print(f"  Ratio:             {time_val_without / time_val_with:.2f}x (should be ~1.0)")

    print("\n3. Serialization Performance (10,000 iterations)")
    print("-" * 60)
    time_ser_without, time_ser_with = benchmark_serialization_performance()
    print(f"  Without cache:     {time_ser_without:.4f}s")
    print(f"  With cache:        {time_ser_with:.4f}s")
    print(f"  Ratio:             {time_ser_without / time_ser_with:.2f}x (should be ~1.0)")

    print("\n" + "=" * 80)
    print("Summary:")
    print("  - TypeAdapter creation is significantly faster with caching")
    print("  - Validation and serialization performance is unaffected")
    print("  - Precompilation provides additional benefits in hot paths")
    print("=" * 80)


if __name__ == '__main__':
    run_benchmarks()
