"""The stored form of a Python node's ODCS contract and its `contract_hash`.

continuo stores a Python contract in one canonical shape, its stored form, so two files that
mean the same thing store the same document. `normalise_python` builds it from a contract that
`validate.validate_python_contract` accepted: it fills what continuo knows about the node (the
stored labels, the table's names, the kind, the service, the tables the node reads and a
`logicalType` per column) and then writes the result in the canonical form.

The canonical form walks the ODCS structure: the contract, its schema object, the columns, the
quality rules, the relationships, the custom properties and every other ODCS object. On each
object it

- strips the description text: a `description` string, and each string of the contract's
  `description` object, has CRLF and CR turned into LF and its outer ASCII whitespace removed;
- lower-cases the `severity` of a quality rule;
- drops a value that is null, an empty string, an empty list or an empty object, and a value
  that equals the ODCS default of its object type: `deprecated: false` on a schema object or a
  column, `required: false`, `unique: false`, `primaryKeyPosition: -1`, `partitioned: false`,
  `partitionKeyPosition: -1`, `criticalDataElement: false` and `semanticType: column` on a
  column, `type: library` on a quality rule and `type: foreignKey` on a relationship;
- drops a custom property whose value is null or empty, and a `continuo` property that says
  continuo's default: `modeType: replace` or `extraColumns: raise`;
- sorts the set-like lists `customProperties`, `tags`, `roles`, `quality`, `relationships` and
  `authoritativeDefinitions` by the canonical JSON of each item, and keeps every other list,
  such as the columns in `properties`, in file order.

A free-form value keeps its content as written: the `value` of a custom property, the
`examples` of a column, the `implementation` and `arguments` of a quality rule, the `mustBe` and
`mustNotBe` of a quality rule, the `value` and `valueExt` of a service level and the `value` of
an enum entry. A free-form `examples`, `implementation` or `arguments` that is empty as a whole
is dropped, and the other free-form values are kept even when null or empty, because there the
null or the empty value is what the author compares with. Everywhere, free-form values
included, a float that is a whole number within -(2**53 - 1) to 2**53 - 1 becomes an integer.

`contract_hash` is `"sha256:"` and the lowercase hex SHA-256 of the RFC 8785 canonical JSON of
the stored form without its root keys `apiVersion`, `kind`, `status` and `version`, which every
stored form holds with the same values.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts.canonical import canonical_json, normalise_text
from continuo_contracts.errors import HashInputError, ProjectionError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.python import KIND_ALIASES, canonical_sql_type
from continuo_contracts.vocabulary import VENDOR, continuo_values

# The root keys of the stored form that `contract_hash` leaves out.
_HASH_EXCLUSIONS: Final = frozenset({"apiVersion", "kind", "status", "version"})

_STORED_VERSION: Final = "0"
_STORED_STATUS: Final = "active"
_DEFAULT_KIND: Final = "python-node"

# The integers every JSON implementation reads back exactly.
_MAX_SAFE_INTEGER: Final = 2**53 - 1

# The logical type of each canonical SQL type that takes no length.
_LOGICAL_TYPES: Final = MappingProxyType(
    {
        "INTEGER": "integer",
        "BIGINT": "integer",
        "NUMERIC": "number",
        "DOUBLE PRECISION": "number",
        "TEXT": "string",
        "DATE": "date",
        "TIMESTAMP": "timestamp",
        "BOOLEAN": "boolean",
    }
)
_STRING_TYPES: Final = ("VARCHAR", "CHAR")

# The lists whose order says nothing: they are sorted by the canonical JSON of each item.
_SET_LIKE: Final = frozenset(
    {"customProperties", "tags", "roles", "quality", "relationships", "authoritativeDefinitions"}
)

# The `continuo` properties whose value is continuo's default, which the stored form leaves out.
_CONTINUO_DEFAULTS: Final = MappingProxyType({"modeType": "replace", "extraColumns": "raise"})

_COLUMN_DEFAULTS: Final = MappingProxyType(
    {
        "deprecated": False,
        "primaryKeyPosition": -1,
        "required": False,
        "unique": False,
        "semanticType": "column",
        "partitioned": False,
        "partitionKeyPosition": -1,
        "criticalDataElement": False,
    }
)


@dataclass(frozen=True)
class _Shape:
    """What the canonical form knows about one ODCS object type.

    `children` names the object type of the value under a key (of each item, for a list), and a
    key it does not name holds a plain object. `defaults` are the ODCS defaults of the type.
    `free_form` are the keys whose value keeps its content as written, and `kept_empty` are the
    free-form keys that stay when their value is null or empty. `text_values` says that every
    string of the object is description text, and `lower_case` names the keys whose string is
    lower-cased.
    """

    children: Mapping[str, str] = field(default_factory=dict)
    defaults: Mapping[str, object] = field(default_factory=dict)
    free_form: frozenset[str] = frozenset()
    kept_empty: frozenset[str] = frozenset()
    text_values: bool = False
    lower_case: frozenset[str] = frozenset()

    def child(self, key: str) -> str:
        if key in self.children:
            return self.children[key]
        return "custom_property" if key == "customProperties" else "object"


_PROPERTY_CHILDREN: Final = {
    "properties": "property",
    "items": "property",
    "map": "map",
    "quality": "quality",
    "relationships": "relationship",
    "enum": "enum_value",
}

_SHAPES: Final = MappingProxyType(
    {
        "root": _Shape(
            children={
                "schema": "schema_object",
                "description": "root_description",
                "slaProperties": "service_level",
            }
        ),
        "root_description": _Shape(text_values=True),
        "schema_object": _Shape(
            children={
                "properties": "property",
                "quality": "quality",
                "relationships": "relationship",
            },
            defaults={"deprecated": False},
        ),
        # A column, a nested property and the item of an array or a map share one type.
        "property": _Shape(
            children=_PROPERTY_CHILDREN,
            defaults=_COLUMN_DEFAULTS,
            free_form=frozenset({"examples"}),
        ),
        "map": _Shape(children={"key": "property", "value": "property"}),
        "quality": _Shape(
            defaults={"type": "library"},
            free_form=frozenset({"implementation", "arguments", "mustBe", "mustNotBe"}),
            kept_empty=frozenset({"mustBe", "mustNotBe"}),
            lower_case=frozenset({"severity"}),
        ),
        "relationship": _Shape(defaults={"type": "foreignKey"}),
        "custom_property": _Shape(free_form=frozenset({"value"}), kept_empty=frozenset({"value"})),
        "service_level": _Shape(
            free_form=frozenset({"value", "valueExt"}),
            kept_empty=frozenset({"value", "valueExt"}),
        ),
        "enum_value": _Shape(free_form=frozenset({"value"}), kept_empty=frozenset({"value"})),
        "object": _Shape(),
    }
)


def logical_type(physical_type: str) -> dict[str, Any] | None:
    """Return the ODCS logical type of the SQL type `physical_type`, or `None` when there is none.

    The type is read as `python.canonical_sql_type` writes it, so `int` is `INTEGER`. `INTEGER`
    and `BIGINT` are `integer`, `NUMERIC(p,s)` and `DOUBLE PRECISION` are `number`, `TEXT` is
    `string`, `VARCHAR(n)` and `CHAR(n)` are `string` with the `logicalTypeOptions`
    `{"maxLength": n}`, and `DATE`, `TIMESTAMP` and `BOOLEAN` are `date`, `timestamp` and
    `boolean`. A type that `canonical_sql_type` refuses has none. The result is a new dict, such
    as `{"logicalType": "integer"}`.
    """
    try:
        canonical = canonical_sql_type(physical_type)
    except ProjectionError:
        return None
    name, _, parameters = canonical.partition("(")
    if name in _STRING_TYPES:
        return {"logicalType": "string", "logicalTypeOptions": {"maxLength": int(parameters[:-1])}}
    logical = _LOGICAL_TYPES.get(name)
    return {"logicalType": logical} if logical is not None else None


def normalise_python(
    doc: Mapping[str, Any], *, service: str, reads: Sequence[str]
) -> dict[str, Any]:
    """Return the stored form of the Python contract `doc`, a new dict.

    `doc` is a contract that `validate.validate_python_contract` accepted, and it is not changed.
    `service` is the service of the release that carries it, and `reads` lists the tables that
    the node's read queries name, each `<schema>.<table>`.

    The stored form is `doc` with `version: "0"` and `status: active`, the root `name` and the
    `physicalName` of the schema object set to the table when they are absent, the `continuo`
    properties `kind` (the kind of the node, read through `python.KIND_ALIASES`, `python-node`
    when `doc` writes none) and `service` always written, and on each column the `logicalType`
    and `logicalTypeOptions` that `logical_type` gives for its `physicalType`, in place of any
    written ones (both are removed when it gives none). When `reads` is not empty, the schema
    object gets the `continuo` property `reads` with the tables in lower case, sorted by code
    point and listed once. The result is then written in the canonical form that the module
    describes.

    Raises `HashInputError` when `reads` is a string or lists a table that is not a string with
    a dot, and `CanonicalJsonError` when an item of a set-like list holds a value that canonical
    JSON cannot write, which a document that `yaml_strict.load` read never holds.
    """
    tables = _reads(reads)
    stored: dict[str, Any] = _as_written(doc)
    stored["version"] = _STORED_VERSION
    stored["status"] = _STORED_STATUS
    table_object = stored["schema"][0]
    table = table_object["name"]
    stored.setdefault("name", table)
    table_object.setdefault("physicalName", table)
    custom = stored.setdefault("customProperties", [])
    declared = continuo_values(custom).get("kind", _DEFAULT_KIND)
    _set_property(custom, "kind", KIND_ALIASES.get(declared, declared))
    _set_property(custom, "service", service)
    for column in table_object.get("properties", []):
        column.pop("logicalType", None)
        column.pop("logicalTypeOptions", None)
        derived = logical_type(column.get("physicalType"))
        if derived is not None:
            column.update(derived)
    if tables:
        table_object.setdefault("customProperties", []).append(
            {"vendor": VENDOR, "property": "reads", "value": tables}
        )
    canonical: dict[str, Any] = _clean(stored, "root")
    return canonical


def contract_hash(stored: Mapping[str, Any]) -> str:
    """Return the `contract_hash` of the stored form `stored`, `"sha256:"` and 64 hex digits.

    It is the SHA-256 of the RFC 8785 canonical JSON of `stored` without its root keys
    `apiVersion`, `kind`, `status` and `version`. `stored` is hashed as it is: it is the result
    of `normalise_python`, which has already written it in the canonical form. Raises
    `CanonicalJsonError` for a value that canonical JSON cannot write.
    """
    hashed = {key: value for key, value in stored.items() if key not in _HASH_EXCLUSIONS}
    return "sha256:" + sha256_hex(canonical_json(hashed))


def _reads(reads: Sequence[str]) -> list[str]:
    """Return the tables of `reads` in lower case, sorted by code point and listed once."""
    if isinstance(reads, str):
        raise HashInputError(f"reads {reads!r}: reads is a list of tables, not one string")
    tables = set()
    for entry in reads:
        if not isinstance(entry, str) or "." not in entry:
            raise HashInputError(f"reads entry {entry!r}: a table is written <schema>.<table>")
        tables.add(entry.lower())
    return sorted(tables)


def _set_property(items: list[Any], name: str, value: str) -> None:
    """Set the value of the `continuo` property `name` of the list `items`, adding it when the
    list has none."""
    for item in items:
        if isinstance(item, dict) and item.get("vendor") == VENDOR and item.get("property") == name:
            item["value"] = value
            return
    items.append({"vendor": VENDOR, "property": name, "value": value})


def _number(value: float) -> float | int:
    """Return `value` as an integer when it is a whole number in the safe integer range."""
    if value.is_integer() and abs(value) <= _MAX_SAFE_INTEGER:
        return int(value)
    return value


def _as_written(value: Any) -> Any:
    """Copy `value` with each mapping as a `dict` and each list or tuple as a `list`, and each
    float that is a whole number in the safe integer range as an integer. Nothing else changes."""
    if isinstance(value, Mapping):
        return {key: _as_written(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_written(item) for item in value]
    if isinstance(value, float):
        return _number(value)
    return value


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str | list | tuple | Mapping) and len(value) == 0)


def _is_default(value: object, default: object) -> bool:
    # The type is compared too, so `0` is not the default `False`.
    return type(value) is type(default) and value == default


def _clean(value: Any, shape: str) -> Any:
    """Return `value`, an object or a list of objects of the ODCS type `shape`, or a scalar, in
    the canonical form."""
    if isinstance(value, Mapping):
        return _clean_object(value, _SHAPES[shape])
    if isinstance(value, list | tuple):
        items = [_clean(item, shape) for item in value]
        return [item for item in items if not _is_empty(item) and not _dropped(item, shape)]
    if isinstance(value, float):
        return _number(value)
    return value


def _dropped(item: object, shape: str) -> bool:
    """Say whether `item` is a custom property that the stored form leaves out: one whose value
    is null or empty, or a `continuo` property that says continuo's default."""
    if shape != "custom_property" or not isinstance(item, Mapping):
        return False
    value = item.get("value")
    if _is_empty(value):
        return True
    name = item.get("property")
    return (
        item.get("vendor") == VENDOR
        and isinstance(name, str)
        and name in _CONTINUO_DEFAULTS
        and _is_default(value, _CONTINUO_DEFAULTS[name])
    )


def _clean_object(obj: Mapping[str, Any], shape: _Shape) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in obj.items():
        if key in shape.free_form:
            written = _as_written(value)
            if key in shape.kept_empty or not _is_empty(written):
                cleaned[key] = written
            continue
        item = _clean(value, shape.child(key))
        if isinstance(item, str):
            if key == "description" or shape.text_values:
                item = normalise_text(item)
            if key in shape.lower_case:
                item = item.lower()
        if _is_empty(item) or (key in shape.defaults and _is_default(item, shape.defaults[key])):
            continue
        if key in _SET_LIKE and isinstance(item, list):
            item = sorted(item, key=canonical_json)
        cleaned[key] = item
    return cleaned
