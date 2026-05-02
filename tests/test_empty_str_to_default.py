"""Tests for EmptyStrToDefault functionality."""

from typing import Annotated

import pytest

from pydantic import (
    BaseModel,
    ConfigDict,
    EmptyStrToDefault,
    Field,
    ValidationError,
)


class TestEmptyStrToDefault:
    """Tests for EmptyStrToDefault functionality."""

    def test_basic_usage_with_annotated(self) -> None:
        """Test basic usage with Annotated."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()] = 'default_name'
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(name='', count='')
        assert m.name == 'default_name'
        assert m.count == 42

    def test_basic_usage_with_field(self) -> None:
        """Test basic usage with Field parameter."""

        class Model(BaseModel):
            name: str = Field(default='default_name', empty_str_to_default=True)
            value: int = Field(default=100, empty_str_to_default=True)

        m = Model(name='', value='')
        assert m.name == 'default_name'
        assert m.value == 100

    def test_normal_input_unchanged(self) -> None:
        """Test that non-empty string inputs are not affected."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()] = 'default_name'
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(name='hello', count='100')
        assert m.name == 'hello'
        assert m.count == 100

    def test_default_factory(self) -> None:
        """Test with default_factory."""

        class Model(BaseModel):
            items: Annotated[list[str], EmptyStrToDefault()] = Field(default_factory=list)

        m = Model(items='')
        assert m.items == []

        m2 = Model(items=['a', 'b'])
        assert m2.items == ['a', 'b']

    def test_default_factory_creates_new_instance(self) -> None:
        """Test that default_factory creates a new instance each time."""

        class Model(BaseModel):
            items: Annotated[list[str], EmptyStrToDefault()] = Field(default_factory=list)

        m1 = Model(items='')
        m1.items.append('a')
        assert m1.items == ['a']

        m2 = Model(items='')
        assert m2.items == []
        assert m1.items is not m2.items

    def test_none_not_affected(self) -> None:
        """Test that None values are not affected."""

        class Model(BaseModel):
            name: Annotated[str | None, EmptyStrToDefault()] = 'default_name'

        m = Model(name=None)
        assert m.name is None

        m2 = Model(name='')
        assert m2.name == 'default_name'

    def test_strict_mode_int_field(self) -> None:
        """Test that strict mode behavior works correctly for int field."""

        class Model(BaseModel):
            model_config = ConfigDict(strict=True)
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(count='')
        assert m.count == 42

        with pytest.raises(ValidationError) as exc_info:
            Model(count='not_an_int')

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'int_parsing'
        assert 'Input should be a valid integer' in errors[0]['msg']

    def test_strict_mode_str_field(self) -> None:
        """Test that strict mode behavior works correctly for str field."""

        class Model(BaseModel):
            model_config = ConfigDict(strict=True)
            name: Annotated[str, EmptyStrToDefault()] = 'default_name'

        m = Model(name='')
        assert m.name == 'default_name'

        m2 = Model(name='hello')
        assert m2.name == 'hello'

    def test_validate_default_with_valid_default(self) -> None:
        """Test with validate_default=True when default is valid."""

        class Model(BaseModel):
            count: Annotated[int, EmptyStrToDefault()] = Field(
                default=42, validate_default=True
            )

        m = Model(count='')
        assert m.count == 42

    def test_validate_default_with_invalid_default(self) -> None:
        """Test with validate_default=True when default is invalid."""

        class Model(BaseModel):
            count: Annotated[int, EmptyStrToDefault()] = Field(
                default='not_an_int', validate_default=True
            )

        with pytest.raises(ValidationError) as exc_info:
            Model()

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'int_parsing'

    def test_empty_str_to_default_false(self) -> None:
        """Test that EmptyStrToDefault(False) does not convert empty string."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault(False)] = 'default_name'

        m = Model(name='')
        assert m.name == ''

    def test_field_empty_str_to_default_false(self) -> None:
        """Test that Field(empty_str_to_default=False) does not convert empty string."""

        class Model(BaseModel):
            name: str = Field(default='default_name', empty_str_to_default=False)

        m = Model(name='')
        assert m.name == ''

    def test_error_format(self) -> None:
        """Test that error messages maintain existing format."""

        class Model(BaseModel):
            count: Annotated[int, EmptyStrToDefault()] = 42

        with pytest.raises(ValidationError) as exc_info:
            Model(count='invalid')

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert 'input' in errors[0]
        assert 'loc' in errors[0]
        assert 'msg' in errors[0]
        assert 'type' in errors[0]

    def test_multiple_fields(self) -> None:
        """Test with multiple fields using EmptyStrToDefault."""

        class Model(BaseModel):
            a: Annotated[str, EmptyStrToDefault()] = 'default_a'
            b: Annotated[int, EmptyStrToDefault()] = 1
            c: Annotated[float, EmptyStrToDefault()] = 2.5
            d: str = 'unchanged'

        m = Model(a='', b='', c='', d='hello')
        assert m.a == 'default_a'
        assert m.b == 1
        assert m.c == 2.5
        assert m.d == 'hello'

    def test_mixed_annotations(self) -> None:
        """Test EmptyStrToDefault with other annotations."""
        from pydantic import BeforeValidator

        def uppercase(v: str) -> str:
            return v.upper()

        class Model(BaseModel):
            name: Annotated[str, BeforeValidator(uppercase), EmptyStrToDefault()] = 'DEFAULT'

        m = Model(name='')
        assert m.name == 'DEFAULT'

        m2 = Model(name='hello')
        assert m2.name == 'HELLO'

    def test_default_factory_with_data(self) -> None:
        """Test default_factory that takes validated data."""

        def create_list(data: dict) -> list:
            return [data.get('name', 'default')]

        class Model(BaseModel):
            name: str = 'test'
            items: Annotated[list, EmptyStrToDefault()] = Field(
                default_factory=create_list,
                default_factory_takes_data=True,
            )

        m = Model(items='')
        assert m.items == ['test']

        m2 = Model(items=['a', 'b'])
        assert m2.items == ['a', 'b']

    def test_default_factory_with_data_field_order(self) -> None:
        """Test default_factory with data respects field ordering."""

        def create_greeting(data: dict) -> str:
            return f"Hello, {data.get('name', 'stranger')}!"

        class Model(BaseModel):
            name: str = 'World'
            greeting: Annotated[str, EmptyStrToDefault()] = Field(
                default_factory=create_greeting,
                default_factory_takes_data=True,
            )

        m = Model(greeting='')
        assert m.greeting == 'Hello, World!'

        m2 = Model(name='Alice', greeting='')
        assert m2.greeting == 'Hello, Alice!'

    def test_float_field(self) -> None:
        """Test EmptyStrToDefault with float field."""

        class Model(BaseModel):
            price: Annotated[float, EmptyStrToDefault()] = 99.99

        m = Model(price='')
        assert m.price == 99.99

        m2 = Model(price='19.99')
        assert m2.price == 19.99

    def test_bool_field(self) -> None:
        """Test EmptyStrToDefault with bool field."""

        class Model(BaseModel):
            active: Annotated[bool, EmptyStrToDefault()] = True

        m = Model(active='')
        assert m.active is True

    def test_optional_int_with_default(self) -> None:
        """Test EmptyStrToDefault with Optional[int] that has a default."""

        class Model(BaseModel):
            count: Annotated[int | None, EmptyStrToDefault()] = 0

        m = Model(count='')
        assert m.count == 0

        m2 = Model(count=None)
        assert m2.count is None

        m3 = Model(count='42')
        assert m3.count == 42

    def test_partial_empty_strings(self) -> None:
        """Test that only empty strings are converted, not all strings."""

        class Model(BaseModel):
            a: Annotated[str, EmptyStrToDefault()] = 'default_a'
            b: Annotated[str, EmptyStrToDefault()] = 'default_b'

        m = Model(a='', b='hello')
        assert m.a == 'default_a'
        assert m.b == 'hello'

    def test_lax_mode_int_field(self) -> None:
        """Test EmptyStrToDefault with int field in lax mode."""

        class Model(BaseModel):
            model_config = ConfigDict(strict=False)
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(count='')
        assert m.count == 42

        m2 = Model(count='100')
        assert m2.count == 100

    def test_validate_default_error_on_empty_string(self) -> None:
        """Test that validate_default error is raised when empty string triggers invalid default."""

        class Model(BaseModel):
            count: Annotated[int, EmptyStrToDefault()] = Field(
                default='not_an_int', validate_default=True
            )

        with pytest.raises(ValidationError) as exc_info:
            Model(count='')

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'int_parsing'


class TestEmptyStrToDefaultWithoutDefault:
    """Tests for EmptyStrToDefault when field has no default value."""

    def test_str_field_no_default_empty_string_kept(self) -> None:
        """Test that str field without default keeps '' as-is (empty_str_to_default only works with default)."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()]

        m = Model(name='')
        assert m.name == ''

    def test_str_field_no_default_normal_value(self) -> None:
        """Test that str field without default works normally."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()]

        m = Model(name='hello')
        assert m.name == 'hello'

    def test_str_field_no_default_missing_raises(self) -> None:
        """Test that missing value still raises required error."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()]

        with pytest.raises(ValidationError) as exc_info:
            Model()

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'missing'

    def test_int_field_no_default_strict_mode_empty_string(self) -> None:
        """Test that int field without default in strict mode raises type error for ''."""

        class Model(BaseModel):
            model_config = ConfigDict(strict=True)
            count: Annotated[int, EmptyStrToDefault()]

        with pytest.raises(ValidationError) as exc_info:
            Model(count='')

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'int_type'

    def test_int_field_with_default_strict_mode_empty_string(self) -> None:
        """Test that int field WITH default in strict mode uses default for ''."""

        class Model(BaseModel):
            model_config = ConfigDict(strict=True)
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(count='')
        assert m.count == 42
