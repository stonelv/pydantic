use std::str::from_utf8;
use std::sync::Arc;

use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyType};
use uuid::Uuid;
use uuid::Variant;

use crate::build_tools::is_strict;
use crate::errors::{ErrorType, ErrorTypeDefaults, ValError, ValResult};
use crate::input::Input;
use crate::input::InputType;
use crate::input::ValidationMatch;
use crate::input::input_as_python_instance;
use crate::serializers::BytesMode;
use crate::tools::SchemaDict;

use super::config::ValBytesMode;
use super::model::create_class;
use super::model::force_setattr;
use super::{BuildValidator, CombinedValidator, DefinitionsBuilder, Exactness, ValidationState, Validator};

const UUID_INT: &str = "int";
const UUID_IS_SAFE: &str = "is_safe";

static UUID_TYPE: PyOnceLock<Py<PyType>> = PyOnceLock::new();

fn get_uuid_type(py: Python<'_>) -> PyResult<&Bound<'_, PyType>> {
    UUID_TYPE.import(py, "uuid", "UUID")
}

#[derive(Debug, Clone, Copy)]
enum Version {
    UUIDv1 = 1,
    UUIDv3 = 3,
    UUIDv4 = 4,
    UUIDv5 = 5,
    UUIDv6 = 6,
    UUIDv7 = 7,
    UUIDv8 = 8,
}

impl From<Version> for usize {
    fn from(v: Version) -> Self {
        v as usize
    }
}

impl From<u8> for Version {
    fn from(u: u8) -> Self {
        match u {
            1 => Version::UUIDv1,
            3 => Version::UUIDv3,
            4 => Version::UUIDv4,
            5 => Version::UUIDv5,
            6 => Version::UUIDv6,
            7 => Version::UUIDv7,
            8 => Version::UUIDv8,
            _ => unreachable!(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct UuidValidator {
    strict: bool,
    version: Option<usize>,
}

impl BuildValidator for UuidValidator {
    const EXPECTED_TYPE: &'static str = "uuid";

    fn build(
        schema: &Bound<'_, PyDict>,
        config: Option<&Bound<'_, PyDict>>,
        _definitions: &mut DefinitionsBuilder<Arc<CombinedValidator>>,
    ) -> PyResult<Arc<CombinedValidator>> {
        let py = schema.py();
        // Note(lig): let's keep this conversion through the Version enum just for the sake of validation
        let version = schema.get_as::<u8>(intern!(py, "version"))?.map(Version::from);
        Ok(CombinedValidator::Uuid(Self {
            strict: is_strict(schema, config)?,
            version: version.map(usize::from),
        })
        .into())
    }
}

impl_py_gc_traverse!(UuidValidator {});

impl Validator for UuidValidator {
    fn validate<'py>(
        &self,
        py: Python<'py>,
        input: &(impl Input<'py> + ?Sized),
        state: &mut ValidationState<'_, 'py>,
    ) -> ValResult<Py<PyAny>> {
        let class = get_uuid_type(py)?;
        if let Some(py_input) = input_as_python_instance(input, class) {
            if let Some(expected_version) = self.version {
                let py_input_version: Option<usize> = py_input.getattr(intern!(py, "version"))?.extract()?;
                if !match py_input_version {
                    Some(py_input_version) => py_input_version == expected_version,
                    None => false,
                } {
                    return Err(ValError::new(
                        ErrorType::UuidVersion {
                            expected_version,
                            context: None,
                        },
                        input,
                    ));
                }
            }
            Ok(py_input.clone().unbind())
        } else if state.strict_or(self.strict) && state.extra().input_type == InputType::Python {
            Err(ValError::new(
                ErrorType::IsInstanceOf {
                    class: class
                        .qualname()
                        .and_then(|name| name.extract())
                        .unwrap_or_else(|_| "UUID".to_owned()),
                    context: None,
                },
                input,
            ))
        } else {
            // In python mode this is a coercion, in JSON mode we treat a UUID string as an
            // exact match.
            // TODO V3: we might want to remove the JSON special case
            if state.extra().input_type == InputType::Python {
                state.floor_exactness(Exactness::Lax);
            }
            let uuid = self.get_uuid(input)?;
            // This block checks if the UUID version matches the expected version and
            // if the UUID variant conforms to RFC 9562 (superseding RFC 4122).
            // When dealing with Python inputs, UUIDs must adhere to RFC 9562 standards.
            if let Some(expected_version) = self.version
                && (uuid.get_version_num() != expected_version || uuid.get_variant() != Variant::RFC4122)
            {
                return Err(ValError::new(
                    ErrorType::UuidVersion {
                        expected_version,
                        context: None,
                    },
                    input,
                ));
            }
            self.create_py_uuid(class, &uuid)
        }
    }

    fn get_name(&self) -> &str {
        Self::EXPECTED_TYPE
    }
}

const UUID_HYPHENATED_LEN: usize = 36;
const UUID_SIMPLE_LEN: usize = 32;
const UUID_BYTES_LEN: usize = 16;

const HYPHEN_POSITIONS: [usize; 4] = [8, 13, 18, 23];

fn is_hex_char(c: u8) -> bool {
    matches!(c, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F')
}

fn fast_check_uuid_str<'py>(
    uuid_str: &str,
    input: &(impl Input<'py> + ?Sized),
) -> ValResult<()> {
    let len = uuid_str.len();

    if len != UUID_HYPHENATED_LEN && len != UUID_SIMPLE_LEN {
        return Err(ValError::new(
            ErrorType::UuidInvalidLength {
                expected: if len > UUID_HYPHENATED_LEN {
                    UUID_HYPHENATED_LEN
                } else {
                    UUID_SIMPLE_LEN
                },
                actual: len,
                context: None,
            },
            input,
        ));
    }

    let bytes = uuid_str.as_bytes();

    if len == UUID_HYPHENATED_LEN {
        for &pos in &HYPHEN_POSITIONS {
            if bytes[pos] != b'-' {
                return Err(ValError::new(
                    ErrorType::UuidInvalidHyphenPosition {
                        index: pos + 1,
                        context: None,
                    },
                    input,
                ));
            }
        }

        for (i, &byte) in bytes.iter().enumerate() {
            if HYPHEN_POSITIONS.contains(&i) {
                continue;
            }
            if !is_hex_char(byte) {
                return Err(ValError::new(
                    ErrorType::UuidInvalidCharacter {
                        character: (byte as char).to_string(),
                        index: i + 1,
                        context: None,
                    },
                    input,
                ));
            }
        }
    } else {
        for (i, &byte) in bytes.iter().enumerate() {
            if !is_hex_char(byte) {
                return Err(ValError::new(
                    ErrorType::UuidInvalidCharacter {
                        character: (byte as char).to_string(),
                        index: i + 1,
                        context: None,
                    },
                    input,
                ));
            }
        }
    }

    Ok(())
}

fn parse_uuid_bytes<'py>(
    bytes: &[u8],
    input: &(impl Input<'py> + ?Sized),
) -> ValResult<Uuid> {
    let len = bytes.len();
    if len != UUID_BYTES_LEN {
        return Err(ValError::new(
            ErrorType::UuidInvalidByteLength {
                expected: UUID_BYTES_LEN,
                actual: len,
                context: None,
            },
            input,
        ));
    }

    Uuid::from_slice(bytes).map_err(|e| {
        ValError::new(
            ErrorType::UuidParsing {
                error: e.to_string(),
                context: None,
            },
            input,
        )
    })
}

impl UuidValidator {
    fn get_uuid<'py>(&self, input: &(impl Input<'py> + ?Sized)) -> ValResult<Uuid> {
        let uuid = match input.validate_str(true, false).ok().map(ValidationMatch::into_inner) {
            Some(either_string) => {
                let cow = either_string.as_cow()?;
                let uuid_str = cow.as_ref();

                fast_check_uuid_str(uuid_str, input)?;

                Uuid::parse_str(uuid_str).map_err(|e| {
                    ValError::new(
                        ErrorType::UuidParsing {
                            error: e.to_string(),
                            context: None,
                        },
                        input,
                    )
                })?
            }
            None => {
                let either_bytes = input
                    .validate_bytes(true, ValBytesMode { ser: BytesMode::Utf8 })
                    .map_err(|_| ValError::new(ErrorTypeDefaults::UuidType, input))?
                    .into_inner();
                let bytes_slice = either_bytes.as_slice();

                if let Ok(utf8_str) = from_utf8(bytes_slice) {
                    if fast_check_uuid_str(utf8_str, input).is_ok() {
                        if let Ok(uuid) = Uuid::parse_str(utf8_str) {
                            uuid
                        } else {
                            parse_uuid_bytes(bytes_slice, input)?
                        }
                    } else {
                        parse_uuid_bytes(bytes_slice, input)?
                    }
                } else {
                    parse_uuid_bytes(bytes_slice, input)?
                }
            }
        };

        if let Some(expected_version) = self.version
            && uuid.get_version_num() != expected_version
        {
            return Err(ValError::new(
                ErrorType::UuidVersion {
                    expected_version,
                    context: None,
                },
                input,
            ));
        }
        Ok(uuid)
    }

    /// Sets the attributes in a Python type object (`py_type`) to represent a UUID class.
    /// The function creates the python class and converts the UUID to a u128 integer and
    /// sets the corresponding attributes in the dictionary object to the converted value
    /// and a 'safe' flag.
    ///
    /// This implementation does not use the Python `__init__` function to speed up the process,
    /// as the `__init__` function in the Python `uuid` module performs extensive checks.
    fn create_py_uuid(&self, py_type: &Bound<'_, PyType>, uuid: &Uuid) -> ValResult<Py<PyAny>> {
        static UUID_SAFE_UNKNOWN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
        let py = py_type.py();
        let safe_unknown = UUID_SAFE_UNKNOWN.get_or_try_init(py, || {
            py.import("uuid")?
                .getattr("SafeUUID")?
                .get_item("unknown")
                .map(Bound::unbind)
        })?;

        let uuid_instance = create_class(py_type)?;
        force_setattr(py, &uuid_instance, intern!(py, UUID_INT), uuid.as_u128())?;
        force_setattr(py, &uuid_instance, intern!(py, UUID_IS_SAFE), safe_unknown)?;
        Ok(uuid_instance.into())
    }
}
