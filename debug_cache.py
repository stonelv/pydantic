"""Debug script for cache precompile."""

import sys
import traceback
from pydantic import TypeAdapter, TypeAdapterCache


def test_precompile_direct():
    """Test precompile directly on cache."""
    print("Testing cache.precompile directly...")
    
    cache = TypeAdapterCache()
    
    # Try calling _precompile_single directly with explicit namespace
    try:
        print(f"  sys._getframe(0) = {sys._getframe(0)}")
        print(f"  sys._getframe(0).f_globals['__name__'] = {sys._getframe(0).f_globals.get('__name__')}")
    except ValueError as e:
        print(f"  sys._getframe failed: {e}")
    
    # Let's try a simpler approach - just create TypeAdapters to populate cache
    print("\n  Populating cache by creating TypeAdapters...")
    TypeAdapter(dict[str, int], cache=cache)
    TypeAdapter(list[dict[str, int]], cache=cache)
    
    stats = cache.get_stats()
    print(f"  Cache stats - size: {stats.size}, misses: {stats.misses}, hits: {stats.hits}")
    
    # Now test using the cache
    print("\n  Testing cache reuse...")
    ta = TypeAdapter(dict[str, int], cache=cache)
    result = ta.validate_python({'foo': '123'})
    print(f"  Validation result: {result}")
    
    stats = cache.get_stats()
    print(f"  Cache stats after reuse - hits: {stats.hits}")


def test_typeadapter_precompile():
    """Test TypeAdapter.precompile static method."""
    print("\n" + "="*60)
    print("Testing TypeAdapter.precompile static method...")
    
    # First, let's trace what's happening inside
    print("\n  Creating cache and calling precompile...")
    
    cache = TypeAdapterCache()
    
    # Let's manually do what precompile should do
    types_to_precompile = [
        (dict[str, int], None),
        (list[dict[str, int]], None),
    ]
    
    for type_, config in types_to_precompile:
        print(f"  Processing type: {type_}")
        try:
            # Check if already in cache
            cached = cache.get(type_, config)
            if cached is None:
                print(f"    Not in cache, creating TypeAdapter...")
                TypeAdapter(type_, config=config, cache=cache)
                print(f"    TypeAdapter created successfully")
            else:
                print(f"    Already in cache")
        except Exception as e:
            print(f"    Error: {e}")
            traceback.print_exc()
    
    stats = cache.get_stats()
    print(f"\n  Cache stats - size: {stats.size}, misses: {stats.misses}")


def test_fix_verification():
    """Test that the fix works."""
    print("\n" + "="*60)
    print("Testing full workflow...")
    
    # Create cache
    cache = TypeAdapterCache()
    
    # "Precompile" by creating TypeAdapters once
    print("\n  Step 1: Pre-warm cache...")
    types_to_warm = [
        (list[int], None),
        (dict[str, int], None),
        (list[dict[str, int]], None),
    ]
    
    for type_, config in types_to_warm:
        TypeAdapter(type_, config=config, cache=cache)
    
    stats = cache.get_stats()
    print(f"    After warming - size: {stats.size}, misses: {stats.misses}")
    
    # Now simulate using these in a "hot path"
    print("\n  Step 2: Use cache in hot path (should hit cache)...")
    
    total = 0
    for _ in range(10):
        ta1 = TypeAdapter(list[int], cache=cache)
        result1 = ta1.validate_python([1, '2', '3'])
        total += sum(result1)
        
        ta2 = TypeAdapter(dict[str, int], cache=cache)
        result2 = ta2.validate_python({'a': '1', 'b': '2'})
        total += sum(result2.values())
    
    stats = cache.get_stats()
    print(f"    After hot path - hits: {stats.hits}, hit rate: {stats.hits/(stats.hits+stats.misses) if stats.hits+stats.misses > 0 else 0:.2%}")
    
    print(f"\n  Total calculation result: {total}")
    print("\n  Test passed!")


if __name__ == '__main__':
    test_precompile_direct()
    test_typeadapter_precompile()
    test_fix_verification()
