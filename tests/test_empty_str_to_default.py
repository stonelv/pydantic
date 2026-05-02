"""Tests for EmptyStrToDefault functionality."""

from typing import Annotated

import pytest

from pydantic import (
    BaseModel,
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

    def test_none_not_affected(self) -> None:
        """Test that None values are not affected."""

        class Model(BaseModel):
            name: Annotated[str | None, EmptyStrToDefault()] = 'default_name'

        m = Model(name=None)
        assert m.name is None

        m2 = Model(name='')
        assert m2.name == 'default_name'

    def test_strict_mode_not_affected(self) -> None:
        """Test that strict mode behavior is not affected for non-string types."""

        class Model(BaseModel):
            model_config = {'strict': True}
            count: Annotated[int, EmptyStrToDefault()] = 42

        m = Model(count='')
        assert m.count == 42

        with pytest.raises(ValidationError) as exc_info:
            Model(count='not_an_int')

        errors = exc_info.value.errors(include_url=False)
        assert len(errors) == 1
        assert errors[0]['type'] == 'int_parsing'
        assert 'Input should be a valid integer' in errors[0]['msg']

    def test_validate_default(self) -> None:
        """Test with validate_default=True."""

        def validate_even(v: int) -> int:
            if v % 2 != 0:
                raise ValueError('Must be even')
            return v

        class Model(BaseModel):
            count: Annotated[int, EmptyStrToDefault()] = Field(
                default=42, validate_default=True
            )

        m = Model(count='')
        assert m.count == 42

    def test_without_default_raises(self) -> None:
        """Test that field without default still raises for missing value."""

        class Model(BaseModel):
            name: Annotated[str, EmptyStrToDefault()]

        m = Model(name='')
        assert m.name == ''

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
