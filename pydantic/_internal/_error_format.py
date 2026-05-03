"""Error location formatting with optional path compression.

This module provides utilities for formatting validation error locations
with optional path compression for deeply nested structures.

Note: This module does NOT modify the behavior of `str(ValidationError)`.
Use the provided formatting functions explicitly when you need compressed output.
"""

from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, NamedTuple, Union, cast

from typing_extensions import TypeAlias

if TYPE_CHECKING:
    from pydantic_core import ErrorDetails, ValidationError

LocItem: TypeAlias = Union[int, str]
Loc: TypeAlias = tuple[LocItem, ...]


class LocCompressionStrategy(str, Enum):
    """Strategy for compressing long error locations.

    Attributes:
        NONE: No compression, show full path (default).
        THRESHOLD: Compress paths longer than threshold by keeping start/end items.
        COLLAPSE_INDICES: Collapse consecutive integer indices.
        COLLAPSE_PATTERNS: Collapse repeating key-index patterns.
    """

    NONE = 'none'
    THRESHOLD = 'threshold'
    COLLAPSE_INDICES = 'collapse_indices'
    COLLAPSE_PATTERNS = 'collapse_patterns'


@dataclass
class LocCompressionConfig:
    """Configuration for error location compression.

    By default, no compression is applied (`strategy=NONE`).

    Attributes:
        strategy: The compression strategy to use.
        threshold: Minimum length of location before compression is applied.
        keep_start: Number of items to keep at the start when compressing.
        keep_end: Number of items to keep at the end when compressing.
        collapse_placeholder: Placeholder string for collapsed items.

    Example:
        ```python
        from pydantic.errors import LocCompressionConfig, LocCompressionStrategy

        # Default: no compression
        config = LocCompressionConfig()

        # Enable threshold-based compression
        config = LocCompressionConfig(
            strategy=LocCompressionStrategy.THRESHOLD,
            threshold=6,
            keep_start=3,
            keep_end=2,
        )

        # Or use the convenience classmethod
        config = LocCompressionConfig.compressed(
            strategy=LocCompressionStrategy.COLLAPSE_PATTERNS,
            threshold=4,
        )
        ```
    """

    strategy: LocCompressionStrategy = LocCompressionStrategy.NONE
    threshold: int = 6
    keep_start: int = 3
    keep_end: int = 2
    collapse_placeholder: str = '[...]'

    @classmethod
    def compressed(
        cls,
        strategy: LocCompressionStrategy = LocCompressionStrategy.THRESHOLD,
        threshold: int = 6,
        keep_start: int = 3,
        keep_end: int = 2,
    ) -> 'LocCompressionConfig':
        """Create a compression configuration with compression enabled.

        This is a convenience method for creating a config that enables
        location path compression.

        Args:
            strategy: The compression strategy to use.
            threshold: Minimum length before compression is applied.
            keep_start: Number of items to keep at the start (for THRESHOLD).
            keep_end: Number of items to keep at the end (for THRESHOLD).

        Returns:
            A configured LocCompressionConfig instance with compression enabled.

        Example:
            ```python
            config = LocCompressionConfig.compressed(
                strategy=LocCompressionStrategy.COLLAPSE_PATTERNS,
                threshold=4,
            )
            ```
        """
        return cls(
            strategy=strategy,
            threshold=threshold,
            keep_start=keep_start,
            keep_end=keep_end,
        )


def format_loc_item(item: LocItem) -> str:
    """Format a single location item as a string.

    This matches the behavior of pydantic-core's LocItem Display implementation:
    - Strings containing '.' are wrapped in backticks
    - Other strings are displayed directly
    - Integers are displayed directly

    Args:
        item: A location item (int or str).

    Returns:
        The formatted string representation.

    Example:
        ```python
        format_loc_item('field')  # 'field'
        format_loc_item('field.name')  # '`field.name`'
        format_loc_item(0)  # '0'
        ```
    """
    if isinstance(item, str):
        if '.' in item:
            return f'`{item}`'
        return item
    else:
        return str(item)


def _compress_threshold(loc: Loc, config: LocCompressionConfig) -> list[str]:
    """Compress a location using threshold strategy."""
    if len(loc) <= config.threshold:
        return [format_loc_item(item) for item in loc]

    keep_start = min(config.keep_start, len(loc) // 2)
    keep_end = min(config.keep_end, len(loc) - keep_start)

    if keep_start + keep_end >= len(loc):
        return [format_loc_item(item) for item in loc]

    result: list[str] = []
    result.extend(format_loc_item(item) for item in loc[:keep_start])
    result.append(config.collapse_placeholder)
    result.extend(format_loc_item(item) for item in loc[-keep_end:])
    return result


def _compress_indices(loc: Loc, config: LocCompressionConfig) -> list[str]:
    """Compress a location by collapsing consecutive integer indices."""
    if len(loc) <= config.threshold:
        return [format_loc_item(item) for item in loc]

    result: list[str] = []
    i = 0

    while i < len(loc):
        item = loc[i]

        if isinstance(item, int):
            j = i + 1
            while j < len(loc) and isinstance(loc[j], int):
                j += 1

            count = j - i
            if count > 1:
                result.append(f'[×{count}]')
            else:
                result.append(str(item))
            i = j
        else:
            result.append(format_loc_item(item))
            i += 1

    return result


def _compress_patterns(loc: Loc, config: LocCompressionConfig) -> list[str]:
    """Compress a location by identifying repeating patterns."""
    if len(loc) <= config.threshold:
        return [format_loc_item(item) for item in loc]

    result: list[str] = []
    i = 0
    n = len(loc)

    while i < n:
        pattern_found = False

        if i + 1 < n and isinstance(loc[i], str) and isinstance(loc[i + 1], int):
            key = loc[i]
            count = 0
            j = i

            while j + 1 < n and loc[j] == key and isinstance(loc[j + 1], int):
                count += 1
                j += 2

            if count > 1:
                result.append(f'{format_loc_item(key)}[×{count}]')
                i = j
                pattern_found = True
                continue

        if not pattern_found:
            for pattern_len in range(2, min(5, (n - i) // 2) + 1):
                pattern = loc[i : i + pattern_len]
                pattern_formatted = [format_loc_item(item) for item in pattern]
                count = 1
                j = i + pattern_len

                while j + pattern_len <= n:
                    if loc[j : j + pattern_len] == pattern:
                        count += 1
                        j += pattern_len
                    else:
                        break

                if count > 1:
                    if pattern_len == 2 and isinstance(pattern[1], int):
                        result.append(f'{pattern_formatted[0]}[×{count}]')
                    else:
                        result.append(f'({".".join(pattern_formatted)})[×{count}]')
                    i = j
                    pattern_found = True
                    break

        if not pattern_found:
            result.append(format_loc_item(loc[i]))
            i += 1

    return result


def compress_loc(
    loc: Loc,
    config: LocCompressionConfig,
) -> list[str]:
    """Compress a location tuple using the configured strategy.

    Args:
        loc: The location tuple (from ErrorDetails['loc']).
        config: The compression configuration to use.

    Returns:
        A list of formatted strings, potentially including placeholders
        for compressed sections.

    Example:
        ```python
        from pydantic.errors import LocCompressionConfig, LocCompressionStrategy, compress_loc

        loc = ('level0', 'level1', 'level2', 'level3', 'level4', 'level5', 'level6')
        config = LocCompressionConfig.compressed(threshold=4)
        compress_loc(loc, config)
        # ['level0', 'level1', '[...]', 'level5', 'level6']
        ```
    """
    if config.strategy == LocCompressionStrategy.NONE:
        return [format_loc_item(item) for item in loc]
    elif config.strategy == LocCompressionStrategy.THRESHOLD:
        return _compress_threshold(loc, config)
    elif config.strategy == LocCompressionStrategy.COLLAPSE_INDICES:
        return _compress_indices(loc, config)
    elif config.strategy == LocCompressionStrategy.COLLAPSE_PATTERNS:
        return _compress_patterns(loc, config)
    else:
        return [format_loc_item(item) for item in loc]


def format_loc(
    loc: Loc,
    config: LocCompressionConfig | None = None,
    separator: str = '.',
) -> str:
    """Format a location tuple as a human-readable string.

    Args:
        loc: The location tuple (from ErrorDetails['loc']).
        config: Optional compression configuration. If not provided,
                no compression is applied.
        separator: Separator to use between location items.

    Returns:
        A formatted string representation of the location.

    Example:
        ```python
        from pydantic.errors import format_loc

        loc = ('data', 0, 'items', 1, 'value')
        format_loc(loc)  # 'data.0.items.1.value'
        ```

    With compression:
        ```python
        from pydantic.errors import LocCompressionConfig, format_loc

        loc = tuple(f'level{i}' for i in range(10))
        config = LocCompressionConfig.compressed(threshold=6)
        format_loc(loc, config)  # 'level0.level1.level2.[...].level8.level9'
        ```
    """
    if config is None:
        config = LocCompressionConfig()

    compressed = compress_loc(loc, config)
    return separator.join(compressed)


class FormattedErrorDetails(NamedTuple):
    """Formatted error details with compressed location.

    This named tuple contains both the machine-readable original error details
    and a human-readable formatted location string.

    Attributes:
        type: The error type (machine-readable).
        loc: The original location tuple (machine-readable, can be used for error analysis).
        loc_formatted: The formatted location string (human-readable, may be compressed).
        msg: The error message.
        input: The input value (if included).
        ctx: The error context (if included).
        url: The documentation URL (if included).
    """

    type: str
    loc: Loc
    loc_formatted: str
    msg: str
    input: Any
    ctx: dict[str, Any] | None
    url: str | None


def format_error(
    error: ErrorDetails,
    config: LocCompressionConfig | None = None,
) -> FormattedErrorDetails:
    """Format a single error details dictionary.

    Args:
        error: The error details from ValidationError.errors().
        config: Optional compression configuration.

    Returns:
        A FormattedErrorDetails named tuple with both machine-readable
        and human-readable fields.

    Example:
        ```python
        from pydantic import BaseModel, ValidationError
        from pydantic.errors import format_error, LocCompressionConfig

        class Model(BaseModel):
            data: list[list[int]]

        try:
            Model(data=[['not_an_int']])
        except ValidationError as e:
            for err in e.errors():
                formatted = format_error(err)
                print(f'Original loc: {formatted.loc}')  # Machine-readable
                print(f'Formatted: {formatted.loc_formatted}')  # Human-readable
        ```
    """
    if config is None:
        config = LocCompressionConfig()

    loc = cast(Loc, error.get('loc', ()))
    loc_formatted = format_loc(loc, config)

    return FormattedErrorDetails(
        type=error['type'],
        loc=loc,
        loc_formatted=loc_formatted,
        msg=error['msg'],
        input=error.get('input'),
        ctx=error.get('ctx'),
        url=error.get('url'),
    )


def format_validation_error(
    error: ValidationError,
    config: LocCompressionConfig | None = None,
    *,
    include_url: bool = True,
    include_context: bool = True,
    include_input: bool = True,
) -> str:
    """Format a ValidationError with optional location compression.

    This function provides similar output to `str(error)` but with
    optional location path compression for deeply nested structures.

    Note: This function does NOT modify the original ValidationError.
    The `str(error)` behavior remains unchanged.

    Args:
        error: The ValidationError to format.
        config: Optional compression configuration. If not provided,
                no compression is applied (same as `str(error)`).
        include_url: Whether to include documentation URLs.
        include_context: Whether to include error context.
        include_input: Whether to include input values.

    Returns:
        A formatted string representation of the validation error.

    Example:
        ```python
        from pydantic import BaseModel, ValidationError
        from pydantic.errors import format_validation_error, LocCompressionConfig

        class DeepModel(BaseModel):
            data: list[list[list[list[int]]]]

        try:
            DeepModel(data=[[[['not_an_int']]]])
        except ValidationError as e:
            # Default: same as str(e)
            print(str(e))
            print('---')
            # With compression
            config = LocCompressionConfig.compressed(threshold=4)
            print(format_validation_error(e, config))
        ```
    """
    if config is None:
        config = LocCompressionConfig()

    errors = error.errors(
        include_url=include_url,
        include_context=include_context,
        include_input=include_input,
    )

    title = error.title
    count = len(errors)
    plural = '' if count == 1 else 's'

    lines = [f'{count} validation error{plural} for {title}']

    for err in errors:
        formatted = format_error(err, config)

        line_parts = [formatted.loc_formatted]
        detail_parts = [f"type={formatted.type}"]

        if include_input and formatted.input is not None:
            input_str = repr(formatted.input)
            if len(input_str) > 50:
                input_str = input_str[:47] + '...'
            detail_parts.append(f"input_value={input_str}")
            try:
                input_type = type(formatted.input).__name__
                detail_parts.append(f"input_type={input_type}")
            except Exception:
                pass

        line_parts.append(f"  {formatted.msg} [{', '.join(detail_parts)}]")

        if include_url and formatted.url:
            line_parts.append(f"\n    For further information visit {formatted.url}")

        lines.append(''.join(line_parts))

    return '\n'.join(lines)


def format_errors(
    errors: list[ErrorDetails],
    config: LocCompressionConfig | None = None,
) -> list[FormattedErrorDetails]:
    """Format a list of error details.

    Args:
        errors: List of error details from ValidationError.errors().
        config: Optional compression configuration.

    Returns:
        A list of FormattedErrorDetails named tuples.

    Example:
        ```python
        from pydantic import BaseModel, ValidationError
        from pydantic.errors import format_errors, LocCompressionConfig

        try:
            # ... validation that fails
        except ValidationError as e:
            formatted_list = format_errors(e.errors(), LocCompressionConfig.compressed())
            for formatted in formatted_list:
                print(formatted.loc_formatted, formatted.msg)
        ```
    """
    if config is None:
        config = LocCompressionConfig()

    return [format_error(err, config) for err in errors]
