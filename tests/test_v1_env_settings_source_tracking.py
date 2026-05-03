"""Tests for BaseSettings source tracking feature."""
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from pydantic.v1.env_settings import (
    BaseSettings,
    EnvSettingsSource,
    FieldSourceInfo,
    InitSettingsSource,
    SecretsSettingsSource,
    SettingsSourceType,
)


class TestSettingsSourceType:
    """Tests for SettingsSourceType enum."""

    def test_enum_values(self):
        assert SettingsSourceType.INIT.value == 'init'
        assert SettingsSourceType.ENV_VAR.value == 'env_var'
        assert SettingsSourceType.DOTENV.value == 'dotenv'
        assert SettingsSourceType.SECRETS.value == 'secrets'
        assert SettingsSourceType.DEFAULT.value == 'default'


class TestFieldSourceInfo:
    """Tests for FieldSourceInfo dataclass."""

    def test_to_dict(self):
        info = FieldSourceInfo(
            source_type=SettingsSourceType.INIT,
            raw_value='test_value',
            source_name='init_kwargs',
            source_details={'field': 'test_field'},
        )
        result = info.to_dict()
        assert result == {
            'source_type': 'init',
            'raw_value': 'test_value',
            'source_name': 'init_kwargs',
            'source_details': {'field': 'test_field'},
        }

    def test_default_values(self):
        info = FieldSourceInfo(
            source_type=SettingsSourceType.DEFAULT,
            raw_value='default',
        )
        assert info.source_name is None
        assert info.source_details == {}


class TestSourceTrackingDisabled:
    """Tests to ensure default behavior is preserved when tracking is disabled."""

    def test_without_track_sources_works_normally(self):
        """Test that settings work normally when _track_sources is not set."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.foo == 'default_foo'
            assert settings.bar == 42
            assert settings.dict() == {'foo': 'default_foo', 'bar': 42}

    def test_without_track_sources_cannot_access_sources(self):
        """Test that accessing source methods raises error when tracking is disabled."""

        class Settings(BaseSettings):
            foo: str = 'default'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings()

            with pytest.raises(ValueError, match='Source tracking is not enabled'):
                settings.get_field_source('foo')

            with pytest.raises(ValueError, match='Source tracking is not enabled'):
                settings.get_all_sources()

            with pytest.raises(ValueError, match='Source tracking is not enabled'):
                settings.model_dump_with_sources()


class TestInitSettingsSource:
    """Tests for InitSettingsSource with source tracking."""

    def test_source_tracking_init_kwargs(self):
        """Test that init kwargs are tracked correctly."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True, foo='init_foo', bar=99)

            assert settings.foo == 'init_foo'
            assert settings.bar == 99

            foo_source = settings.get_field_source('foo')
            assert foo_source is not None
            assert foo_source.source_type == SettingsSourceType.INIT
            assert foo_source.raw_value == 'init_foo'
            assert foo_source.source_name == 'init_kwargs'

            bar_source = settings.get_field_source('bar')
            assert bar_source is not None
            assert bar_source.source_type == SettingsSourceType.INIT
            assert bar_source.raw_value == 99

    def test_init_kwargs_take_priority(self):
        """Test that init kwargs take priority over env vars."""

        class Settings(BaseSettings):
            foo: str = 'default'

        with mock.patch.dict(os.environ, {'FOO': 'env_value'}):
            settings = Settings(_track_sources=True, foo='init_value')

            assert settings.foo == 'init_value'
            source = settings.get_field_source('foo')
            assert source.source_type == SettingsSourceType.INIT


class TestEnvSettingsSource:
    """Tests for EnvSettingsSource with source tracking."""

    def test_source_tracking_env_vars(self):
        """Test that environment variables are tracked correctly."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42

        with mock.patch.dict(os.environ, {'FOO': 'env_foo', 'BAR': '100'}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'env_foo'
            assert settings.bar == 100

            foo_source = settings.get_field_source('foo')
            assert foo_source is not None
            assert foo_source.source_type == SettingsSourceType.ENV_VAR
            assert foo_source.raw_value == 'env_foo'
            assert foo_source.source_details.get('env_name') == 'foo'

            bar_source = settings.get_field_source('bar')
            assert bar_source is not None
            assert bar_source.source_type == SettingsSourceType.ENV_VAR
            assert bar_source.raw_value == '100'

    def test_source_tracking_dotenv(self, tmp_path):
        """Test that .env file values are tracked correctly."""
        env_file = tmp_path / '.env'
        env_file.write_text('FOO=dotenv_value\nBAR=200')

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42

            class Config:
                env_file = str(env_file)

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'dotenv_value'
            assert settings.bar == 200

            foo_source = settings.get_field_source('foo')
            assert foo_source is not None
            assert foo_source.source_type == SettingsSourceType.DOTENV
            assert foo_source.raw_value == 'dotenv_value'
            assert str(env_file) in foo_source.source_name

    def test_env_var_takes_priority_over_dotenv(self, tmp_path):
        """Test that env vars take priority over .env file values."""
        env_file = tmp_path / '.env'
        env_file.write_text('FOO=dotenv_value')

        class Settings(BaseSettings):
            foo: str = 'default_foo'

            class Config:
                env_file = str(env_file)

        with mock.patch.dict(os.environ, {'FOO': 'env_value'}):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'env_value'
            source = settings.get_field_source('foo')
            assert source.source_type == SettingsSourceType.ENV_VAR


class TestDefaultValues:
    """Tests for default value source tracking."""

    def test_default_values_are_tracked(self):
        """Test that fields using default values are tracked correctly."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42
            required_field: str

        with mock.patch.dict(os.environ, {'REQUIRED_FIELD': 'env_value'}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'default_foo'
            assert settings.bar == 42
            assert settings.required_field == 'env_value'

            foo_source = settings.get_field_source('foo')
            assert foo_source is not None
            assert foo_source.source_type == SettingsSourceType.DEFAULT
            assert foo_source.raw_value == 'default_foo'
            assert foo_source.source_name == 'foo.default'

            bar_source = settings.get_field_source('bar')
            assert bar_source is not None
            assert bar_source.source_type == SettingsSourceType.DEFAULT
            assert bar_source.raw_value == 42

            required_source = settings.get_field_source('required_field')
            assert required_source.source_type == SettingsSourceType.ENV_VAR

    def test_get_all_sources_includes_defaults(self):
        """Test that get_all_sources includes fields with default values."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: str = 'default_bar'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)
            all_sources = settings.get_all_sources()

            assert 'foo' in all_sources
            assert 'bar' in all_sources
            assert all_sources['foo'].source_type == SettingsSourceType.DEFAULT
            assert all_sources['bar'].source_type == SettingsSourceType.DEFAULT


class TestModelDumpWithSources:
    """Tests for model_dump_with_sources method."""

    def test_model_dump_with_sources_basic(self):
        """Test basic functionality of model_dump_with_sources."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 42

        with mock.patch.dict(os.environ, {'BAR': '99'}, clear=True):
            settings = Settings(_track_sources=True, foo='init_value')

            result = settings.model_dump_with_sources()

            assert 'foo' in result
            assert 'bar' in result

            assert result['foo']['value'] == 'init_value'
            assert result['foo']['source']['source_type'] == 'init'

            assert result['bar']['value'] == 99
            assert result['bar']['source']['source_type'] == 'env_var'

    def test_model_dump_with_sources_include_exclude(self):
        """Test include/exclude parameters in model_dump_with_sources."""

        class Settings(BaseSettings):
            foo: str = 'foo'
            bar: str = 'bar'
            baz: str = 'baz'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)

            result_include = settings.model_dump_with_sources(include={'foo', 'bar'})
            assert 'foo' in result_include
            assert 'bar' in result_include
            assert 'baz' not in result_include

            result_exclude = settings.model_dump_with_sources(exclude={'foo'})
            assert 'foo' not in result_exclude
            assert 'bar' in result_exclude
            assert 'baz' in result_exclude

    def test_model_dump_with_sources_exclude_defaults(self):
        """Test exclude_defaults parameter in model_dump_with_sources."""

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: str = 'default_bar'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True, foo='custom_value')

            result = settings.model_dump_with_sources(exclude_defaults=True)
            assert 'foo' in result
            assert 'bar' not in result


class TestSecretsSettingsSource:
    """Tests for SecretsSettingsSource with source tracking."""

    def test_secrets_source_tracking(self, tmp_path):
        """Test that secrets file values are tracked correctly."""
        secrets_dir = tmp_path / 'secrets'
        secrets_dir.mkdir()

        foo_secret = secrets_dir / 'foo'
        foo_secret.write_text('secret_foo')

        bar_secret = secrets_dir / 'bar'
        bar_secret.write_text('42')

        class Settings(BaseSettings):
            foo: str = 'default_foo'
            bar: int = 0

            class Config:
                secrets_dir = str(secrets_dir)

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'secret_foo'
            assert settings.bar == 42

            foo_source = settings.get_field_source('foo')
            assert foo_source is not None
            assert foo_source.source_type == SettingsSourceType.SECRETS
            assert foo_source.raw_value == 'secret_foo'
            assert str(foo_secret) in foo_source.source_name


class TestCustomSources:
    """Tests with custom source priorities."""

    def test_custom_source_priority(self):
        """Test that custom source priorities are respected in tracking."""

        def low_priority_source(settings):
            return {'foo': 'low_priority'}

        def high_priority_source(settings):
            return {'foo': 'high_priority'}

        class Settings(BaseSettings):
            foo: str = 'default'

            class Config:
                @classmethod
                def customise_sources(cls, init_settings, env_settings, file_secret_settings):
                    return (
                        init_settings,
                        high_priority_source,
                        low_priority_source,
                    )

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.foo == 'high_priority'


class TestPerformance:
    """Tests to ensure default performance is not affected."""

    def test_without_track_sources_no_sources_attr(self):
        """Test that __sources__ is None when tracking is disabled."""

        class Settings(BaseSettings):
            foo: str = 'default'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.__sources__ is None

    def test_with_track_sources_has_sources_attr(self):
        """Test that __sources__ is set when tracking is enabled."""

        class Settings(BaseSettings):
            foo: str = 'default'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)
            assert settings.__sources__ is not None
            assert isinstance(settings.__sources__, dict)


class TestEdgeCases:
    """Tests for edge cases."""

    def test_nonexistent_field_returns_none(self):
        """Test that get_field_source returns None for nonexistent fields."""

        class Settings(BaseSettings):
            foo: str = 'default'

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)
            assert settings.get_field_source('nonexistent') is None

    def test_by_alias_in_model_dump(self):
        """Test by_alias parameter in model_dump_with_sources."""

        class Settings(BaseSettings):
            foo: str = 'default'

            class Config:
                fields = {'foo': {'alias': 'FOO_ALIAS'}}

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings(_track_sources=True)

            result_normal = settings.model_dump_with_sources()
            assert 'foo' in result_normal

            result_alias = settings.model_dump_with_sources(by_alias=True)
            assert 'FOO_ALIAS' in result_alias
            assert 'foo' not in result_alias


class TestComplexTypes:
    """Tests for complex types with source tracking."""

    def test_list_from_env_var(self):
        """Test that list types from env vars are tracked correctly."""

        class Settings(BaseSettings):
            my_list: list = ['default']

            class Config:
                @classmethod
                def parse_env_var(cls, field_name, raw_val):
                    if field_name == 'my_list':
                        return raw_val.split(',')
                    return cls.json_loads(raw_val)

        with mock.patch.dict(os.environ, {'MY_LIST': 'a,b,c'}, clear=True):
            settings = Settings(_track_sources=True)

            assert settings.my_list == ['a', 'b', 'c']
            source = settings.get_field_source('my_list')
            assert source.source_type == SettingsSourceType.ENV_VAR
            assert source.raw_value == 'a,b,c'
