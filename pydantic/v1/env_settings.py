"""Settings management with environment variable support.

This module provides `BaseSettings` class for reading settings from
environment variables, .env files, secrets files, and explicit arguments.

## Source Tracking Feature (Optional)

Enable with `_track_sources=True` to debug where each configuration value comes from:
- `get_field_source(field_name)`: Get source info for a specific field
- `get_all_sources()`: Get source info for all fields
- `model_dump_with_sources()`: Export values with source information

Source types: `init`, `env_var`, `dotenv`, `secrets`, `default`
"""

import os
import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
    Union,
)

from pydantic.v1.config import BaseConfig, Extra
from pydantic.v1.fields import ModelField
from pydantic.v1.main import BaseModel
from pydantic.v1.types import JsonWrapper
from pydantic.v1.typing import StrPath, display_as_type, get_origin, is_union
from pydantic.v1.utils import deep_update, lenient_issubclass, path_type, sequence_like

if TYPE_CHECKING:
    from typing_extensions import Literal

env_file_sentinel = str(object())

SettingsSourceCallable = Callable[['BaseSettings'], Dict[str, Any]]
DotenvType = Union[StrPath, List[StrPath], Tuple[StrPath, ...]]


class SettingsSourceType(Enum):
    """Configuration source type enumeration.

    Attributes:
        INIT: Explicit keyword arguments passed to the constructor
        ENV_VAR: System environment variables
        DOTENV: Values from .env files
        SECRETS: Values from secrets files (e.g., Docker secrets)
        DEFAULT: Field default values
    """

    INIT = 'init'
    ENV_VAR = 'env_var'
    DOTENV = 'dotenv'
    SECRETS = 'secrets'
    DEFAULT = 'default'


@dataclass
class FieldSourceInfo:
    """Field source information.

    Attributes:
        source_type: Configuration source type
        raw_value: Raw value before validation and type conversion
        source_name: Source name (e.g., environment variable name, file path)
        source_details: Additional source details (optional)
    """

    source_type: SettingsSourceType
    raw_value: Any
    source_name: Optional[str] = None
    source_details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'source_type': self.source_type.value,
            'raw_value': self.raw_value,
            'source_name': self.source_name,
            'source_details': self.source_details,
        }


@dataclass
class SourceResult:
    """Source result containing values and source information."""

    values: Dict[str, Any]
    sources: Dict[str, FieldSourceInfo]


class SettingsError(ValueError):
    pass


class BaseSettings(BaseModel):
    """
    Base class for settings, allowing values to be overridden by environment variables.

    This is useful in production for secrets you do not wish to save in code, it plays nicely with docker(-compose),
    Heroku and any 12 factor app design.
    """

    __slots__ = ('__sources__',)

    def __init__(
        __pydantic_self__,
        _env_file: Optional[DotenvType] = env_file_sentinel,
        _env_file_encoding: Optional[str] = None,
        _env_nested_delimiter: Optional[str] = None,
        _secrets_dir: Optional[StrPath] = None,
        _track_sources: bool = False,
        **values: Any,
    ) -> None:
        __pydantic_self__.__sources__: Optional[Dict[str, FieldSourceInfo]] = None

        if _track_sources:
            values_dict, sources = __pydantic_self__._build_values_with_sources(
                values,
                _env_file=_env_file,
                _env_file_encoding=_env_file_encoding,
                _env_nested_delimiter=_env_nested_delimiter,
                _secrets_dir=_secrets_dir,
            )
            __pydantic_self__.__sources__ = sources
            super().__init__(**values_dict)
        else:
            super().__init__(
                **__pydantic_self__._build_values(
                    values,
                    _env_file=_env_file,
                    _env_file_encoding=_env_file_encoding,
                    _env_nested_delimiter=_env_nested_delimiter,
                    _secrets_dir=_secrets_dir,
                )
            )

    def _build_values(
        self,
        init_kwargs: Dict[str, Any],
        _env_file: Optional[DotenvType] = None,
        _env_file_encoding: Optional[str] = None,
        _env_nested_delimiter: Optional[str] = None,
        _secrets_dir: Optional[StrPath] = None,
    ) -> Dict[str, Any]:
        init_settings = InitSettingsSource(init_kwargs=init_kwargs)
        env_settings = EnvSettingsSource(
            env_file=(_env_file if _env_file != env_file_sentinel else self.__config__.env_file),
            env_file_encoding=(
                _env_file_encoding if _env_file_encoding is not None else self.__config__.env_file_encoding
            ),
            env_nested_delimiter=(
                _env_nested_delimiter if _env_nested_delimiter is not None else self.__config__.env_nested_delimiter
            ),
            env_prefix_len=len(self.__config__.env_prefix),
        )
        file_secret_settings = SecretsSettingsSource(secrets_dir=_secrets_dir or self.__config__.secrets_dir)
        sources = self.__config__.customise_sources(
            init_settings=init_settings, env_settings=env_settings, file_secret_settings=file_secret_settings
        )
        if sources:
            return deep_update(*reversed([source(self) for source in sources]))
        else:
            return {}

    def _build_values_with_sources(
        self,
        init_kwargs: Dict[str, Any],
        _env_file: Optional[DotenvType] = None,
        _env_file_encoding: Optional[str] = None,
        _env_nested_delimiter: Optional[str] = None,
        _secrets_dir: Optional[StrPath] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, FieldSourceInfo]]:
        init_settings = InitSettingsSource(init_kwargs=init_kwargs)
        env_settings = EnvSettingsSource(
            env_file=(_env_file if _env_file != env_file_sentinel else self.__config__.env_file),
            env_file_encoding=(
                _env_file_encoding if _env_file_encoding is not None else self.__config__.env_file_encoding
            ),
            env_nested_delimiter=(
                _env_nested_delimiter if _env_nested_delimiter is not None else self.__config__.env_nested_delimiter
            ),
            env_prefix_len=len(self.__config__.env_prefix),
        )
        file_secret_settings = SecretsSettingsSource(secrets_dir=_secrets_dir or self.__config__.secrets_dir)
        sources = self.__config__.customise_sources(
            init_settings=init_settings, env_settings=env_settings, file_secret_settings=file_secret_settings
        )

        if not sources:
            return {}, {}

        all_source_results: List[SourceResult] = []
        for source in sources:
            if hasattr(source, '__call_with_sources__'):
                result = source.__call_with_sources__(self)
                all_source_results.append(result)
            else:
                values = source(self)
                source_type = self._infer_source_type(source)
                sources_dict: Dict[str, FieldSourceInfo] = {}
                for key, value in values.items():
                    sources_dict[key] = FieldSourceInfo(
                        source_type=source_type,
                        raw_value=value,
                        source_name=getattr(source, '__class__', source).__name__,
                    )
                all_source_results.append(SourceResult(values=values, sources=sources_dict))

        final_values: Dict[str, Any] = {}
        final_sources: Dict[str, FieldSourceInfo] = {}

        for result in reversed(all_source_results):
            for key, value in result.values.items():
                if key in final_values:
                    if isinstance(final_values[key], dict) and isinstance(value, dict):
                        final_values[key] = deep_update(final_values[key], value)
                    else:
                        final_values[key] = value
                    final_sources[key] = result.sources[key]
                else:
                    final_values[key] = value
                    final_sources[key] = result.sources[key]

        return final_values, final_sources

    def _infer_source_type(self, source: Any) -> SettingsSourceType:
        source_name = source.__class__.__name__
        if source_name == 'InitSettingsSource':
            return SettingsSourceType.INIT
        elif source_name == 'EnvSettingsSource':
            return SettingsSourceType.ENV_VAR
        elif source_name == 'SecretsSettingsSource':
            return SettingsSourceType.SECRETS
        else:
            return SettingsSourceType.DEFAULT

    def get_field_source(self, field_name: str) -> Optional[FieldSourceInfo]:
        """Get source information for a specific field.

        Args:
            field_name: Name of the field.

        Returns:
            FieldSourceInfo if the field exists and source tracking is enabled; otherwise None.

        Raises:
            ValueError: If source tracking is not enabled.
        """
        if self.__sources__ is None:
            raise ValueError(
                'Source tracking is not enabled. '
                'Create the settings instance with _track_sources=True to enable source tracking.'
            )

        if field_name in self.__sources__:
            return self.__sources__[field_name]

        if field_name in self.__fields__:
            field = self.__fields__[field_name]
            if not field.required:
                default_value = field.get_default()
                return FieldSourceInfo(
                    source_type=SettingsSourceType.DEFAULT,
                    raw_value=default_value,
                    source_name=f'{field_name}.default',
                )

        return None

    def get_all_sources(self) -> Dict[str, FieldSourceInfo]:
        """Get source information for all fields.

        For fields using default values, source info is generated automatically.

        Returns:
            Dictionary with field names as keys and FieldSourceInfo objects as values.

        Raises:
            ValueError: If source tracking is not enabled.
        """
        if self.__sources__ is None:
            raise ValueError(
                'Source tracking is not enabled. '
                'Create the settings instance with _track_sources=True to enable source tracking.'
            )

        result: Dict[str, FieldSourceInfo] = {}

        for field_name in self.__fields__:
            if field_name in self.__sources__:
                result[field_name] = self.__sources__[field_name]
            else:
                field = self.__fields__[field_name]
                if not field.required:
                    default_value = field.get_default()
                    result[field_name] = FieldSourceInfo(
                        source_type=SettingsSourceType.DEFAULT,
                        raw_value=default_value,
                        source_name=f'{field_name}.default',
                    )

        return result

    def model_dump_with_sources(
        self,
        *,
        include: Optional[Union[AbstractSet[str], Mapping[str, Any]]] = None,
        exclude: Optional[Union[AbstractSet[str], Mapping[str, Any]]] = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Export field values with source information.

        Returns a dictionary where each key corresponds to a field, and the value is
        a dictionary containing 'value' (the field value) and 'source' (source information).

        Args:
            include: Fields to include.
            exclude: Fields to exclude.
            by_alias: Whether to use field aliases as keys.
            exclude_unset: Whether to exclude unset fields.
            exclude_defaults: Whether to exclude fields using default values.
            exclude_none: Whether to exclude fields with None values.

        Returns:
            Dictionary in the format:
            {
                'field_name': {
                    'value': <field_value>,
                    'source': {
                        'source_type': 'init' | 'env_var' | 'dotenv' | 'secrets' | 'default',
                        'raw_value': <raw_value_before_validation>,
                        'source_name': <source_name>,
                        'source_details': <additional_details>
                    }
                }
            }

        Raises:
            ValueError: If source tracking is not enabled.
        """
        if self.__sources__ is None:
            raise ValueError(
                'Source tracking is not enabled. '
                'Create the settings instance with _track_sources=True to enable source tracking.'
            )

        base_dict = self.dict(
            include=include,
            exclude=exclude,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
        )

        all_sources = self.get_all_sources()
        result: Dict[str, Dict[str, Any]] = {}

        for field_key, value in base_dict.items():
            if by_alias:
                field_name = self._get_field_name_by_alias(field_key)
            else:
                field_name = field_key

            source_info = all_sources.get(field_name)
            result[field_key] = {
                'value': value,
                'source': source_info.to_dict() if source_info else None,
            }

        return result

    def _get_field_name_by_alias(self, alias: str) -> str:
        for field_name, field in self.__fields__.items():
            if field.alias == alias:
                return field_name
        return alias

    class Config(BaseConfig):
        env_prefix: str = ''
        env_file: Optional[DotenvType] = None
        env_file_encoding: Optional[str] = None
        env_nested_delimiter: Optional[str] = None
        secrets_dir: Optional[StrPath] = None
        validate_all: bool = True
        extra: Extra = Extra.forbid
        arbitrary_types_allowed: bool = True
        case_sensitive: bool = False

        @classmethod
        def prepare_field(cls, field: ModelField) -> None:
            env_names: Union[List[str], AbstractSet[str]]
            field_info_from_config = cls.get_field_info(field.name)

            env = field_info_from_config.get('env') or field.field_info.extra.get('env')
            if env is None:
                if field.has_alias:
                    warnings.warn(
                        'aliases are no longer used by BaseSettings to define which environment variables to read. '
                        'Instead use the "env" field setting. '
                        'See https://pydantic-docs.helpmanual.io/usage/settings/#environment-variable-names',
                        FutureWarning,
                    )
                env_names = {cls.env_prefix + field.name}
            elif isinstance(env, str):
                env_names = {env}
            elif isinstance(env, (set, frozenset)):
                env_names = env
            elif sequence_like(env):
                env_names = list(env)
            else:
                raise TypeError(f'invalid field env: {env!r} ({display_as_type(env)}); should be string, list or set')

            if not cls.case_sensitive:
                env_names = env_names.__class__(n.lower() for n in env_names)
            field.field_info.extra['env_names'] = env_names

        @classmethod
        def customise_sources(
            cls,
            init_settings: SettingsSourceCallable,
            env_settings: SettingsSourceCallable,
            file_secret_settings: SettingsSourceCallable,
        ) -> Tuple[SettingsSourceCallable, ...]:
            return init_settings, env_settings, file_secret_settings

        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str) -> Any:
            return cls.json_loads(raw_val)

    __config__: ClassVar[Type[Config]]


class InitSettingsSource:
    __slots__ = ('init_kwargs',)

    def __init__(self, init_kwargs: Dict[str, Any]):
        self.init_kwargs = init_kwargs

    def __call__(self, settings: BaseSettings) -> Dict[str, Any]:
        return self.init_kwargs

    def __call_with_sources__(self, settings: BaseSettings) -> SourceResult:
        sources: Dict[str, FieldSourceInfo] = {}
        for key, value in self.init_kwargs.items():
            sources[key] = FieldSourceInfo(
                source_type=SettingsSourceType.INIT,
                raw_value=value,
                source_name='init_kwargs',
                source_details={'field': key},
            )
        return SourceResult(values=self.init_kwargs, sources=sources)

    def __repr__(self) -> str:
        return f'InitSettingsSource(init_kwargs={self.init_kwargs!r})'


class EnvSettingsSource:
    __slots__ = ('env_file', 'env_file_encoding', 'env_nested_delimiter', 'env_prefix_len')

    def __init__(
        self,
        env_file: Optional[DotenvType],
        env_file_encoding: Optional[str],
        env_nested_delimiter: Optional[str] = None,
        env_prefix_len: int = 0,
    ):
        self.env_file: Optional[DotenvType] = env_file
        self.env_file_encoding: Optional[str] = env_file_encoding
        self.env_nested_delimiter: Optional[str] = env_nested_delimiter
        self.env_prefix_len: int = env_prefix_len

    def __call__(self, settings: BaseSettings) -> Dict[str, Any]:
        return self._build_values(settings)[0]

    def __call_with_sources__(self, settings: BaseSettings) -> SourceResult:
        values, sources = self._build_values(settings)
        return SourceResult(values=values, sources=sources)

    def _build_values(self, settings: BaseSettings) -> Tuple[Dict[str, Any], Dict[str, FieldSourceInfo]]:
        d: Dict[str, Any] = {}
        sources: Dict[str, FieldSourceInfo] = {}

        if settings.__config__.case_sensitive:
            env_vars_lower: Dict[str, Tuple[str, str]] = {k.lower(): (k, v) for k, v in os.environ.items()}
        else:
            env_vars_lower = {k.lower(): (k, v) for k, v in os.environ.items()}

        dotenv_vars = self._read_env_files(settings.__config__.case_sensitive)

        combined_vars: Dict[str, Tuple[Any, SettingsSourceType, str, Optional[str]]] = {}

        for key_lower, (original_key, value) in dotenv_vars.items():
            combined_vars[key_lower] = (value, SettingsSourceType.DOTENV, str(self.env_file), original_key)

        for key_lower, (original_key, value) in env_vars_lower.items():
            if key_lower not in combined_vars:
                combined_vars[key_lower] = (value, SettingsSourceType.ENV_VAR, original_key, original_key)

        lookup_vars: Dict[str, Any] = {k: v[0] for k, v in combined_vars.items()}

        for field in settings.__fields__.values():
            env_val: Optional[str] = None
            source_type: Optional[SettingsSourceType] = None
            source_name: Optional[str] = None
            matched_env_name: Optional[str] = None
            original_env_name: Optional[str] = None

            for env_name in field.field_info.extra['env_names']:
                if env_name in combined_vars:
                    env_val, source_type, source_name, original_env_name = combined_vars[env_name]
                    matched_env_name = env_name
                    break

            is_complex, allow_parse_failure = self.field_is_complex(field)
            if is_complex:
                if env_val is None:
                    env_val_built = self.explode_env_vars(field, lookup_vars)
                    if env_val_built:
                        d[field.alias] = env_val_built
                        if matched_env_name and source_type:
                            sources[field.alias] = FieldSourceInfo(
                                source_type=source_type,
                                raw_value=env_val_built,
                                source_name=source_name,
                                source_details={
                                    'env_name': original_env_name if original_env_name else matched_env_name,
                                    'env_name_lower': matched_env_name,
                                    'is_complex': True,
                                    'is_exploded': True,
                                },
                            )
                else:
                    raw_env_val = env_val
                    try:
                        env_val = settings.__config__.parse_env_var(field.name, env_val)
                    except ValueError as e:
                        if not allow_parse_failure:
                            raise SettingsError(f'error parsing env var "{original_env_name or matched_env_name}"') from e

                    if isinstance(env_val, dict):
                        exploded = self.explode_env_vars(field, lookup_vars)
                        final_value = deep_update(env_val, exploded)
                        d[field.alias] = final_value
                    else:
                        final_value = env_val
                        d[field.alias] = final_value

                    if source_type:
                        sources[field.alias] = FieldSourceInfo(
                            source_type=source_type,
                            raw_value=raw_env_val,
                            source_name=source_name,
                            source_details={
                                'env_name': original_env_name if original_env_name else matched_env_name,
                                'env_name_lower': matched_env_name,
                                'is_complex': True,
                                'parsed_value': env_val,
                            },
                        )
            elif env_val is not None:
                d[field.alias] = env_val
                if source_type:
                    sources[field.alias] = FieldSourceInfo(
                        source_type=source_type,
                        raw_value=env_val,
                        source_name=source_name,
                        source_details={
                            'env_name': original_env_name if original_env_name else matched_env_name,
                            'env_name_lower': matched_env_name,
                        },
                    )

        return d, sources

    def _read_env_files(self, case_sensitive: bool) -> Dict[str, Tuple[str, Optional[str]]]:
        env_files = self.env_file
        if env_files is None:
            return {}

        if isinstance(env_files, (str, os.PathLike)):
            env_files = [env_files]

        dotenv_vars: Dict[str, Tuple[str, Optional[str]]] = {}
        for env_file in env_files:
            env_path = Path(env_file).expanduser()
            if env_path.is_file():
                raw_vars = read_env_file_raw(env_path, encoding=self.env_file_encoding)
                for original_key, value in raw_vars.items():
                    key_lower = original_key if case_sensitive else original_key.lower()
                    if key_lower not in dotenv_vars:
                        dotenv_vars[key_lower] = (original_key, value)

        return dotenv_vars

    def field_is_complex(self, field: ModelField) -> Tuple[bool, bool]:
        if lenient_issubclass(field.annotation, JsonWrapper):
            return False, False

        if field.is_complex():
            allow_parse_failure = False
        elif is_union(get_origin(field.type_)) and field.sub_fields and any(f.is_complex() for f in field.sub_fields):
            allow_parse_failure = True
        else:
            return False, False

        return True, allow_parse_failure

    def explode_env_vars(self, field: ModelField, env_vars: Mapping[str, Optional[str]]) -> Dict[str, Any]:
        prefixes = [f'{env_name}{self.env_nested_delimiter}' for env_name in field.field_info.extra['env_names']]
        result: Dict[str, Any] = {}
        for env_name, env_val in env_vars.items():
            if not any(env_name.startswith(prefix) for prefix in prefixes):
                continue
            env_name_without_prefix = env_name[self.env_prefix_len :]
            _, *keys, last_key = env_name_without_prefix.split(self.env_nested_delimiter)
            env_var = result
            for key in keys:
                env_var = env_var.setdefault(key, {})
            env_var[last_key] = env_val

        return result

    def __repr__(self) -> str:
        return (
            f'EnvSettingsSource(env_file={self.env_file!r}, env_file_encoding={self.env_file_encoding!r}, '
            f'env_nested_delimiter={self.env_nested_delimiter!r})'
        )


class SecretsSettingsSource:
    __slots__ = ('secrets_dir',)

    def __init__(self, secrets_dir: Optional[StrPath]):
        self.secrets_dir: Optional[StrPath] = secrets_dir

    def __call__(self, settings: BaseSettings) -> Dict[str, Any]:
        return self._build_values(settings)[0]

    def __call_with_sources__(self, settings: BaseSettings) -> SourceResult:
        values, sources = self._build_values(settings)
        return SourceResult(values=values, sources=sources)

    def _build_values(self, settings: BaseSettings) -> Tuple[Dict[str, Any], Dict[str, FieldSourceInfo]]:
        secrets: Dict[str, Any] = {}
        sources: Dict[str, FieldSourceInfo] = {}

        if self.secrets_dir is None:
            return secrets, sources

        secrets_path = Path(self.secrets_dir).expanduser()

        if not secrets_path.exists():
            warnings.warn(f'directory "{secrets_path}" does not exist')
            return secrets, sources

        if not secrets_path.is_dir():
            raise SettingsError(f'secrets_dir must reference a directory, not a {path_type(secrets_path)}')

        for field in settings.__fields__.values():
            for env_name in field.field_info.extra['env_names']:
                path = find_case_path(secrets_path, env_name, settings.__config__.case_sensitive)
                if not path:
                    continue

                if path.is_file():
                    raw_secret = path.read_text().strip()
                    secret_value = raw_secret

                    if field.is_complex():
                        try:
                            secret_value = settings.__config__.parse_env_var(field.name, raw_secret)
                        except ValueError as e:
                            raise SettingsError(f'error parsing env var "{env_name}"') from e

                    secrets[field.alias] = secret_value
                    sources[field.alias] = FieldSourceInfo(
                        source_type=SettingsSourceType.SECRETS,
                        raw_value=raw_secret,
                        source_name=str(path),
                        source_details={
                            'env_name': env_name,
                            'secrets_dir': str(self.secrets_dir),
                            'is_complex': field.is_complex(),
                        },
                    )
                else:
                    warnings.warn(
                        f'attempted to load secret file "{path}" but found a {path_type(path)} instead.',
                        stacklevel=4,
                    )
        return secrets, sources

    def __repr__(self) -> str:
        return f'SecretsSettingsSource(secrets_dir={self.secrets_dir!r})'


def read_env_file_raw(
    file_path: StrPath, *, encoding: str = None
) -> Dict[str, Optional[str]]:
    """Read .env file preserving original key casing."""
    try:
        from dotenv import dotenv_values
    except ImportError as e:
        raise ImportError('python-dotenv is not installed, run `pip install pydantic[dotenv]`') from e

    file_vars: Dict[str, Optional[str]] = dotenv_values(file_path, encoding=encoding or 'utf8')
    return file_vars


def read_env_file(
    file_path: StrPath, *, encoding: str = None, case_sensitive: bool = False
) -> Dict[str, Optional[str]]:
    try:
        from dotenv import dotenv_values
    except ImportError as e:
        raise ImportError('python-dotenv is not installed, run `pip install pydantic[dotenv]`') from e

    file_vars: Dict[str, Optional[str]] = dotenv_values(file_path, encoding=encoding or 'utf8')
    if not case_sensitive:
        return {k.lower(): v for k, v in file_vars.items()}
    else:
        return file_vars


def find_case_path(dir_path: Path, file_name: str, case_sensitive: bool) -> Optional[Path]:
    for f in dir_path.iterdir():
        if f.name == file_name:
            return f
        elif not case_sensitive and f.name.lower() == file_name.lower():
            return f
    return None


__all__ = [
    'BaseSettings',
    'SettingsError',
    'SettingsSourceType',
    'FieldSourceInfo',
    'InitSettingsSource',
    'EnvSettingsSource',
    'SecretsSettingsSource',
    'read_env_file',
    'env_file_sentinel',
    'DotenvType',
    'SettingsSourceCallable',
]
