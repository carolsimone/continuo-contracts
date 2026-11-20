"""The stored form of a node's ODCS contract and its `contract_hash`.

continuo stores a contract in one canonical shape, its stored form, so two files that mean the
same thing store the same document. `normalise_python` builds it from a Python contract that
`validate.validate_python_contract` accepted: it fills what continuo knows about the node (the
stored labels, the table's names, the kind, the service, the tables the node reads and a
`logicalType` per column) and then writes the result in the canonical form. `normalise_dbt`
builds it from a contract that `dbt.generate` wrote: it joins the tables the node's compiled SQL
names with the ones the node declares and then writes the result in the canonical form. Both also
drop the `primaryKeyPosition` of a column when it is the only column with `primaryKey: true`,
because a position orders the columns of a key and a key of one column has no order, before the
walk. `canonical_form` runs the walk alone on any ODCS document, for a caller that changes a
stored form: it fills nothing and drops no `primaryKeyPosition`.

The canonical form walks the ODCS structure: the contract, its schema object, the columns, the
quality rules, the relationships, the custom properties and every other ODCS object. On each
object it

- strips the description text: a `description` string, and each string of the contract's
  `description` object, has CRLF and CR turned into LF and its outer ASCII whitespace removed;
- lower-cases the ASCII letters of the `severity` of a quality rule;
- drops a value that is null, an empty string, an empty list or an empty object, and a value
  that equals the ODCS default of its object type: `deprecated: false` on a schema object or a
  column, `logicalType: object` (the only logical type ODCS allows there) on a schema object,
  `required: false`, `unique: false`, `primaryKey: false`, `primaryKeyPosition: -1`,
  `partitioned: false`, `partitionKeyPosition: -1`, `criticalDataElement: false` and
  `semanticType: column` on a column, `type: library` on a quality rule and `type: foreignKey`
  on a relationship;
- drops the `physicalName` of a column when it equals the column's `name`;
- drops a custom property whose value is null or empty, and a `continuo` property that says
  continuo's default: `modeType: replace` or `extraColumns: raise`;
- sorts the set-like lists `customProperties`, `tags`, `roles`, `quality`, `relationships` and
  `authoritativeDefinitions` by the canonical JSON of each item and keeps an item that equals an
  earlier one once, and keeps every other list, such as the columns in `properties`, in file
  order.

A free-form value keeps its content as written: the `value` of a custom property, the
`examples` of a column, the `implementation`, `arguments`, `mustBe` and `mustNotBe` of a quality
rule, the `value` and `valueExt` of a service level and the `value` of an enum entry. A free-form
value that is null or empty as a whole is dropped, except the `mustBe` and `mustNotBe` of a
quality rule and the `value` of a service level or an enum entry, where the null or the empty
value is what the author compares with. `validate` reads the same object types and free-form
values, so both treat the same keys as data. Everywhere, free-form values included, a float that
is a whole number within -(2**53 - 1) to 2**53 - 1 becomes an integer.

`contract_hash` is `"sha256:"` and the lowercase hex SHA-256 of the RFC 8785 canonical JSON of
the stored form without the root keys that `fingerprints.yaml` lists as
`contract_hash_exclusions`: `apiVersion`, `kind`, `status` and `version`, which every stored form
holds with the same values.
"""

import re

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts import fingerprints
from continuo_contracts.canonical import (
    _ascii_lower,
    _has_control,
    _has_whitespace,
    canonical_json,
    normalise_text,
)
from continuo_contracts.errors import HashInputError, ProjectionError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.python import KIND_ALIASES, canonical_sql_type
from continuo_contracts.vocabulary import VENDOR, continuo_values

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
_STRING_TYPE: Final = re.compile(r"(?:VARCHAR|CHAR)\((\d+)\)", re.ASCII)
# The longest length of a `VARCHAR(n)` or `CHAR(n)`: canonical JSON writes no larger integer.
_MAX_LENGTH: Final = 2**53 - 1

# The lists whose order says nothing: they are sorted by the canonical JSON of each item.
_SET_LIKE: Final = frozenset(
    {
        "customProperties",
        "tags",
        "roles",
        "quality",
        "relationships",
        "authoritativeDefinitions",
    }
)

# The `continuo` properties whose value is continuo's default, which the stored form leaves out.
_CONTINUO_DEFAULTS: Final = MappingProxyType(
    {"modeType": "replace", "extraColumns": "raise"}
)

_COLUMN_DEFAULTS: Final = MappingProxyType(
    {
        "deprecated": False,
        "primaryKey": False,
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

    `children` names the object type of the value under a key (of each item, for a list), and a key
    it does not name holds a plain object. `defaults` are the ODCS defaults of the type. `free_form`
    are the keys whose value keeps its content as written, and `kept_empty` are the free-form keys
    that stay when their value is null or empty. `validate` reads `children` and `free_form` too, to
    find the `customProperties` lists of a contract. `text_values` says that every string of the
    object is description text, `lower_case` names the keys whose string is lower-cased, and
    `own_physical_name` says that a `physicalName` equal to the object's `name` says nothing and is
    dropped.
    """

    children: Mapping[str, str] = field(default_factory=dict)
    defaults: Mapping[str, object] = field(default_factory=dict)
    free_form: frozenset[str] = frozenset()
    kept_empty: frozenset[str] = frozenset()
    text_values: bool = False
    lower_case: frozenset[str] = frozenset()
    own_physical_name: bool = False

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
            # `object` is the only logical type ODCS allows on a schema object.
            defaults={"deprecated": False, "logicalType": "object"},
        ),
        # A column, a nested property and the item of an array or a map share one type.
        "property": _Shape(
            children=_PROPERTY_CHILDREN,
            defaults=_COLUMN_DEFAULTS,
            free_form=frozenset({"examples"}),
            own_physical_name=True,
        ),
        "map": _Shape(children={"key": "property", "value": "property"}),
        "quality": _Shape(
            defaults={"type": "library"},
            free_form=frozenset(
                {"implementation", "arguments", "mustBe", "mustNotBe"}
            ),
            kept_empty=frozenset({"mustBe", "mustNotBe"}),
            lower_case=frozenset({"severity"}),
        ),
        "relationship": _Shape(defaults={"type": "foreignKey"}),
        "custom_property": _Shape(
            free_form=frozenset({"value"}), kept_empty=frozenset({"value"})
        ),
        "service_level": _Shape(
            free_form=frozenset({"value", "valueExt"}),
            kept_empty=frozenset({"value"}),
        ),
        "enum_value": _Shape(
            free_form=frozenset({"value"}), kept_empty=frozenset({"value"})
        ),
        "object": _Shape(),
    }
)


def logical_type(physical_type: str) -> dict[str, Any] | None:
    """Return the ODCS logical type of the SQL type `physical_type`, or `None` when there is none.

    The type is read as `python.canonical_sql_type` writes it, so `int` is `INTEGER`. `INTEGER`
    and `BIGINT` are `integer`, `NUMERIC(p,s)` and `DOUBLE PRECISION` are `number`, `TEXT` is
    `string`, `VARCHAR(n)` and `CHAR(n)` are `string` with the `logicalTypeOptions`
    `{"maxLength": n}`, and `DATE`, `TIMESTAMP` and `BOOLEAN` are `date`, `timestamp` and
    `boolean`. A type that `canonical_sql_type` refuses has none, and so has a `VARCHAR(n)` or
    `CHAR(n)` whose length is not from 1 to 2**53 - 1, the lengths the contract check accepts. The
    result is a new dict, such as `{"logicalType": "integer"}`.
    """
    try:
        canonical = canonical_sql_type(physical_type)
    except ProjectionError:
        return None
    name = canonical.partition("(")[0]
    if name in _STRING_TYPES:
        length = _length(canonical)
        if length is None:
            return None
        return {
            "logicalType": "string",
            "logicalTypeOptions": {"maxLength": length},
        }
    logical = _LOGICAL_TYPES.get(name)
    return {"logicalType": logical} if logical is not None else None


def _length(canonical: str) -> int | None:
    """Return the length `n` of the canonical type `VARCHAR(n)` or `CHAR(n)`.

    The result is `n` when it is from 1 to 2**53 - 1, and `None` for a length
    outside that range and for any other type.

    A length of more than 16 digits is above the range, and checking the number of digits first
    keeps a very long digit string from reaching `int`, which refuses it.
    """
    match = _STRING_TYPE.fullmatch(canonical)
    if match is None or len(digits := match.group(1)) > 16:
        return None
    length = int(digits)
    return length if 1 <= length <= _MAX_LENGTH else None


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
    when `doc` writes none) and `service` always written, and on each column the `physicalType`
    as `python.canonical_sql_type` writes it, with the `logicalType` and `logicalTypeOptions`
    that `logical_type` gives for it in place of any written ones (both are removed when it gives
    none). A `physicalType` that `canonical_sql_type` refuses, which an accepted contract never
    holds, stays as written and gets no logical type. When `reads` is not empty, the schema
    object gets the `continuo` property `reads` with the tables' ASCII letters in lower case, sorted
    by code point and listed once. The result is then written in the canonical form that the module
    describes.

    Raises `HashInputError` when `service` is empty or blank, when `reads` is a string or lists a
    table that is not a string of exactly two parts joined by a dot, neither empty, with no
    whitespace and no control character, and `CanonicalJsonError` when an item of a set-like list
    holds a value that canonical JSON cannot write, which a document that `yaml_strict.load` read
    never holds.
    """
    if not service.strip():
        raise HashInputError(
            f"service {service!r}: the service of a release is not empty or blank"
        )
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
    columns = table_object.get("properties", [])
    for column in columns:
        column.pop("logicalType", None)
        column.pop("logicalTypeOptions", None)
        try:
            physical = canonical_sql_type(column.get("physicalType"))
        except ProjectionError:
            continue
        column["physicalType"] = physical
        derived = logical_type(physical)
        if derived is not None:
            column.update(derived)
    _drop_single_key_position(columns)
    if tables:
        table_object.setdefault("customProperties", []).append(
            {"vendor": VENDOR, "property": "reads", "value": tables}
        )
    return canonical_form(stored)


def normalise_dbt(
    doc: Mapping[str, Any], *, reads: Sequence[str]
) -> dict[str, Any]:
    """Return the stored form of the dbt contract `doc`, a new dict.

    `doc` is a contract that `dbt.generate` wrote, or its stored form, and it is not changed.
    `reads` lists the tables that the node's compiled SQL names, each `<schema>.<table>`.

    The stored form is `doc` with the `continuo` property `reads` of the schema object holding the
    tables of `reads` joined with the tables that property lists in `doc`, their ASCII letters in
    lower case, sorted by code point and listed once, and left out when there are none; the
    `primaryKeyPosition` of the only column with `primaryKey: true` is dropped. The result is then
    written in the canonical form that the module describes. Storing a stored form again with no
    `reads` gives it back unchanged.

    Raises `HashInputError` when `reads` is a string, or when `reads` or the schema object's
    `reads` lists a table that is not a string of exactly two parts joined by a dot, neither
    empty, with no whitespace and no control character, and `CanonicalJsonError` when an item of a
    set-like list holds a value that canonical JSON cannot write, such as a `mustBe` of 2**60. A
    contract `dbt.generate` writes never holds one, but a stored contract passed back that
    `validate.validate_dbt_contract` accepts can.
    """
    given = _reads(reads)
    stored: dict[str, Any] = _as_written(doc)
    table_object = stored["schema"][0]
    custom = table_object.get("customProperties", [])
    tables = sorted({*given, *_reads(continuo_values(custom).get("reads", []))})
    table_object["customProperties"] = [
        item for item in custom if not _is_continuo(item, "reads")
    ]
    if tables:
        table_object["customProperties"].append(
            {"vendor": VENDOR, "property": "reads", "value": tables}
        )
    _drop_single_key_position(table_object.get("properties", []))
    return canonical_form(stored)


def canonical_form(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Return `doc` after the walk of the canonical form that the module describes, a new dict.

    `doc` is an ODCS contract and it is not changed. This is the walk that `normalise_python`
    and `normalise_dbt` end with, so a caller that adds to or removes from a stored form calls it
    to get the stored form of the result. The walk gives a stored form back unchanged, and any
    other document its canonical shape: null and empty values and ODCS defaults are dropped, the
    set-like lists are sorted and hold each item once, and description text is trimmed. It fills
    nothing and drops no `primaryKeyPosition`: `normalise_python` and `normalise_dbt` drop the
    position of a lone key column before they call it.

    Raises `CanonicalJsonError` when an item of a set-like list holds a value that canonical
    JSON cannot write, which a document that `yaml_strict.load` read never holds.
    """
    canonical: dict[str, Any] = _clean(doc, "root")
    return canonical


def _drop_single_key_position(columns: list[Any]) -> None:
    """Drop the `primaryKeyPosition` of the only column with `primaryKey: true`."""
    keys = [column for column in columns if column.get("primaryKey") is True]
    if len(keys) == 1:
        # A position orders the columns of a key, so one key column says nothing with it.
        keys[0].pop("primaryKeyPosition", None)


def contract_hash(stored: Mapping[str, Any]) -> str:
    """Return the `contract_hash` of the stored form `stored`, `"sha256:"` and 64 hex digits.

    It is the SHA-256 of the RFC 8785 canonical JSON of `stored` without the root keys that
    `fingerprints.contract_hash_exclusions()` lists, `apiVersion`, `kind`, `status` and `version`.
    `stored` is hashed as it is: it is the result of `normalise_python` or `normalise_dbt`, which
    have already written it in the canonical form. Raises `CanonicalJsonError` for a value that
    canonical JSON cannot write.
    """
    left_out = fingerprints.contract_hash_exclusions()
    hashed = {key: value for key, value in stored.items() if key not in left_out}
    return "sha256:" + sha256_hex(canonical_json(hashed))


def _reads(reads: Sequence[str]) -> list[str]:
    """Return the tables of `reads` with their ASCII letters in lower case.

    The tables are sorted by code point and listed once.
    """
    if isinstance(reads, str):
        raise HashInputError(
            f"reads {reads!r}: reads is a list of tables, not one string"
        )
    tables = set()
    for entry in reads:
        if (
            not isinstance(entry, str)
            or len(parts := entry.split(".")) != 2
            or not all(parts)
            or _has_whitespace(entry)
            or _has_control(entry)
        ):
            raise HashInputError(
                f"reads entry {entry!r}: a table is written <schema>.<table>, exactly two parts "
                "that are not empty, with no whitespace and no control character"
            )
        tables.add(_ascii_lower(entry))
    return sorted(tables)


def _set_property(items: list[Any], name: str, value: str) -> None:
    """Set the value of the `continuo` property `name` of the list `items`.

    The property is added when the list has none.
    """
    for item in items:
        if _is_continuo(item, name):
            item["value"] = value
            return
    items.append({"vendor": VENDOR, "property": name, "value": value})


def _is_continuo(item: object, name: str) -> bool:
    """Say whether `item` is the `continuo` custom property `name`."""
    return (
        isinstance(item, dict)
        and item.get("vendor") == VENDOR
        and item.get("property") == name
    )


def _number(value: float) -> float | int:
    """Return `value` as an integer when it is a whole number in the safe integer range."""
    if value.is_integer() and abs(value) <= _MAX_SAFE_INTEGER:
        return int(value)
    return value


def _as_written(value: Any) -> Any:
    """Copy `value` with mappings as `dict`, lists and tuples as `list`.

    Each float that is a whole number in the safe integer range becomes an
    integer. Nothing else changes.
    """
    if isinstance(value, Mapping):
        return {key: _as_written(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_written(item) for item in value]
    if isinstance(value, float):
        return _number(value)
    return value


def _is_empty(value: object) -> bool:
    return value is None or (
        isinstance(value, str | list | tuple | Mapping) and len(value) == 0
    )


def _is_default(value: object, default: object) -> bool:
    # The type is compared too, so `0` is not the default `False`.
    return type(value) is type(default) and value == default


def _clean(value: Any, shape: str) -> Any:
    """Return `value` in the canonical form.

    `value` is an object or a list of objects of the ODCS type `shape`, or a
    scalar.
    """
    if isinstance(value, Mapping):
        return _clean_object(value, _SHAPES[shape])
    if isinstance(value, list | tuple):
        items = [_clean(item, shape) for item in value]
        return [
            item
            for item in items
            if not _is_empty(item) and not _dropped(item, shape)
        ]
    if isinstance(value, float):
        return _number(value)
    return value


def _dropped(item: object, shape: str) -> bool:
    """Say whether `item` is a custom property that the stored form leaves out.

    It is one whose value is null or empty, or a `continuo` property that says
    continuo's default.
    """
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
                item = _ascii_lower(item)
        if _is_empty(item) or (
            key in shape.defaults and _is_default(item, shape.defaults[key])
        ):
            continue
        if key in _SET_LIKE and isinstance(item, list):
            item = _sorted_once(item)
        cleaned[key] = item
    if (
        shape.own_physical_name
        and "physicalName" in cleaned
        and _is_default(cleaned["physicalName"], cleaned.get("name"))
    ):
        del cleaned["physicalName"]
    return cleaned


def _sorted_once(items: list[Any]) -> list[Any]:
    """Return `items` sorted by the canonical JSON of each.

    An item that equals an earlier one is left out.
    """
    by_bytes = {canonical_json(item): item for item in items}
    return [by_bytes[key] for key in sorted(by_bytes)]
