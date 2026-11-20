"""The ontology file, and the ontology part of a stored contract.

An ontology file is one YAML document in two parts. `principals` names who acts on data: its
domains, services, teams, roles and people. `entities` names what the data is about: each thing
has an owner, a key, properties and access rules. `load` reads the file with the strict YAML
reader and checks its shape against the keys below, and an `Ontology` answers which services it
declares and which domain each belongs to.

`description`, a string, is allowed on every mapping that has a fixed set of keys. A mapping
whose keys are names (the domains, the services, a thing's properties) has no `description` key
of its own, because it would be a name.

====================  ==================================================================
mapping               keys
====================  ==================================================================
root                  `principals`, `entities`
`principals`          `domains`, `services`, `teams`, `roles`, `people`
a domain              `owner_team`
a service             `domain`, `kind`
a team or a role      `idp_group`
`people`              `source`
a thing               `owner`, `born_in`, `key`, `properties`, `access`
`key`                 each key part, with its type as a string
a property            `type`, `ref`, `personal`, `currency`, `derived_from`, `nullable`
an access rule        `properties`, `readers`, `purpose`
====================  ==================================================================

A stored contract carries the ontology part in two kinds of place: seven binding properties,
which a team writes (`entity`, `grain`, `fixed` and `filter` on the schema object, `property`,
`local` and `aggregate` on a column), and the links that point at the ontology, which are
`authoritativeDefinitions` entries whose `url` starts with `LINK_PREFIX`. `has_bindings` says
whether a stored contract writes any binding, and `strip` returns the stored contract without
the whole ontology part. `resolve` goes the other way: it fills a stored contract with what the
ontology knows.
"""

import re

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, cast

from continuo_contracts import yaml_strict
from continuo_contracts.contract import _drop_single_key_position, canonical_form
from continuo_contracts.errors import OntologyError, StrictYamlError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.vocabulary import VENDOR, continuo_values

# The `url` of an `authoritativeDefinitions` entry that links a contract to the ontology.
LINK_PREFIX: Final = "continuo-ontology:"

_SCHEMA_BINDINGS: Final = ("entity", "grain", "fixed", "filter")
_COLUMN_BINDINGS: Final = ("property", "local", "aggregate")

# The `continuo` properties that bind a table or a column to the ontology.
BINDING_PROPERTIES: Final = (*_SCHEMA_BINDINGS, *_COLUMN_BINDINGS)


@dataclass(frozen=True)
class Ontology:
    """An ontology file that `load` accepted.

    `document` is the parsed file, plain `dict`, `list`, `str` and `bool` values, and `sha256` is
    the hex SHA-256 of the file's text encoded as UTF-8.
    """

    document: Mapping[str, Any]
    sha256: str

    def declares(self, service: str) -> bool:
        """Say whether the ontology names `service` under `principals.services`."""
        return service in self._services()

    def domain_of(self, service: str) -> str | None:
        """Return the `domain` of `service`, or `None` when it has none or is not declared."""
        domain: str | None = self._services().get(service, {}).get("domain")
        return domain

    def services(self) -> tuple[str, ...]:
        """Return the names under `principals.services`, sorted by code point."""
        return tuple(sorted(self._services()))

    def _services(self) -> Mapping[str, Mapping[str, Any]]:
        principals: Mapping[str, Any] = self.document.get("principals", {})
        services: Mapping[str, Mapping[str, Any]] = principals.get("services", {})
        return services


# What the shape check knows about each kind of value. A `_Leaf` is a scalar of one Python type
# and `what` says it in the message; a `_List` holds items of one spec; a `_Record` is a mapping
# with a fixed set of keys; a `_Named` is a mapping of names to values of one spec.


@dataclass(frozen=True)
class _Leaf:
    kind: type
    what: str


@dataclass(frozen=True)
class _List:
    what: str
    item: _Spec


@dataclass(frozen=True)
class _Record:
    what: str
    keys: Mapping[str, _Spec]


@dataclass(frozen=True)
class _Named:
    names: str
    value: _Spec
    values: str


type _Spec = _Leaf | _List | _Record | _Named

_TEXT: Final = _Leaf(str, "a string")
_FLAG: Final = _Leaf(bool, "true or false")
_STRINGS: Final = _List("a list of strings", _TEXT)


def _record(what: str, **keys: _Spec) -> _Record:
    """Return the record `what` with the keys `keys` and a `description`."""
    return _Record(what, {"description": _TEXT, **keys})


def _by_name(what: str, **keys: _Spec) -> _Named:
    """Return a mapping of names to records of the keys `keys`, each record `what`."""
    return _Named(what, _record(f"a {what}", **keys), what)


_ACCESS: Final = _record(
    "an access rule", properties=_STRINGS, readers=_STRINGS, purpose=_TEXT
)
_PROPERTY: Final = _record(
    "a property",
    type=_TEXT,
    ref=_TEXT,
    personal=_FLAG,
    currency=_TEXT,
    derived_from=_STRINGS,
    nullable=_FLAG,
)
_THING: Final = _record(
    "a thing",
    owner=_TEXT,
    born_in=_TEXT,
    key=_Named("part", _TEXT, "type string"),
    properties=_Named("property", _PROPERTY, "property"),
    access=_List("a list of access rules", _ACCESS),
)
_ONTOLOGY: Final = _record(
    "the ontology",
    principals=_record(
        "the principals",
        domains=_by_name("domain", owner_team=_TEXT),
        services=_by_name("service", domain=_TEXT, kind=_TEXT),
        teams=_by_name("team", idp_group=_TEXT),
        roles=_by_name("role", idp_group=_TEXT),
        people=_record("people", source=_TEXT),
    ),
    entities=_Named("thing", _THING, "thing"),
)

_KINDS: Final = {
    type(None): "null",
    bool: "a boolean",
    int: "a number",
    float: "a number",
    str: "a string",
    list: "a list",
    dict: "a mapping",
}


def load(text: str) -> Ontology:
    """Return the `Ontology` that the YAML `text` holds.

    The text is read with `yaml_strict.load` and its shape is checked against the keys the
    module describes: each value has the type of its key, and a key that the module does not
    list is refused. Raises `OntologyError` for any failure, with the message `<path>: <reason>`,
    where the path is dotted, such as `entities.User.personnal`, with `[<index>]` for an item of
    a list, and is `<root>` for the document itself. A text that strict YAML refuses is reported
    at `<root>` with the reader's own message. Only the first failure, in the order of the
    file, is reported.
    """
    try:
        document = yaml_strict.load(text)
    except StrictYamlError as error:
        raise OntologyError(f"<root>: {error}") from error
    _check(document, _ONTOLOGY, "")
    return Ontology(
        document=cast(Mapping[str, Any], document),
        sha256=sha256_hex(text.encode("utf-8")),
    )


def _check(value: object, spec: _Spec, path: str) -> None:
    """Raise `OntologyError` when `value`, found at `path`, does not have the shape `spec`."""
    if isinstance(spec, _Leaf):
        if not isinstance(value, spec.kind):
            raise _refusal(path, f"must be {spec.what}, got {_kind_of(value)}")
    elif isinstance(spec, _List):
        if not isinstance(value, list):
            raise _refusal(path, f"must be {spec.what}, got {_kind_of(value)}")
        for index, entry in enumerate(value):
            _check(entry, spec.item, f"{path}[{index}]")
    elif isinstance(spec, _Record):
        if not isinstance(value, Mapping):
            raise _refusal(path, f"must be a mapping, got {_kind_of(value)}")
        for key, entry in value.items():
            if key not in spec.keys:
                raise _refusal(
                    _join(path, key),
                    f"unknown key, the keys of {spec.what} are "
                    f"{', '.join(sorted(spec.keys))}",
                )
            _check(entry, spec.keys[key], _join(path, key))
    else:
        if not isinstance(value, Mapping):
            raise _refusal(
                path,
                f"must be a mapping of {spec.names} name to {spec.values}, "
                f"got {_kind_of(value)}",
            )
        for key, entry in value.items():
            if not key.strip():
                raise _refusal(
                    path, f"a {spec.names} name is not blank, got {key!r}"
                )
            _check(entry, spec.value, _join(path, key))


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _kind_of(value: object) -> str:
    return _KINDS[type(value)]


def _refusal(path: str, reason: str) -> OntologyError:
    return OntologyError(f"{path or '<root>'}: {reason}")


# The ontology part of a stored contract.


def has_bindings(stored: Mapping[str, Any]) -> bool:
    """Say whether the stored contract `stored` writes any of the binding properties.

    The properties are `entity`, `grain`, `fixed` and `filter` in the `customProperties` of its
    schema object, and `property`, `local` and `aggregate` in those of one of its columns, each
    an item of the `continuo` vendor. A link to the ontology is not a binding.
    """
    return any(
        _is_binding(item, bindings)
        for obj, bindings in _bound_objects(stored)
        for item in obj.get("customProperties", [])
    )


def strip(stored: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stored contract `stored` without its ontology part, a new dict.

    The ontology part is the binding properties of the schema object and of each column (see
    `has_bindings`) and every `authoritativeDefinitions` entry whose `url` starts with
    `LINK_PREFIX`, on the contract, its schema object or a column. An entry with another
    `url`, such as an author's own link, stays. The result is written in the canonical form, so
    a list that held only ontology items is gone, and a stored contract with no ontology part
    comes back as it is. `domain` and every other value stay: `strip` removes what a team or
    the ontology wrote and fills nothing in. `stored` is not changed.
    """
    stripped = _strip_object(stored, ())
    stripped["schema"] = [
        _strip_table(table) for table in stored.get("schema", [])
    ]
    return canonical_form(stripped)


def _bound_objects(
    stored: Mapping[str, Any],
) -> Iterator[tuple[Mapping[str, Any], tuple[str, ...]]]:
    """Yield each schema object and column of `stored` with the bindings that may sit on it."""
    for table in stored.get("schema", []):
        yield table, _SCHEMA_BINDINGS
        for column in table.get("properties", []):
            yield column, _COLUMN_BINDINGS


def _is_binding(item: object, bindings: tuple[str, ...]) -> bool:
    """Say whether `item` is a `continuo` custom property named in `bindings`."""
    return (
        isinstance(item, Mapping)
        and item.get("vendor") == VENDOR
        and item.get("property") in bindings
    )


def _is_link(item: object) -> bool:
    """Say whether `item` is an `authoritativeDefinitions` entry that links to the ontology."""
    if not isinstance(item, Mapping):
        return False
    url = item.get("url")
    return isinstance(url, str) and url.startswith(LINK_PREFIX)


def _strip_table(table: Mapping[str, Any]) -> dict[str, Any]:
    stripped = _strip_object(table, _SCHEMA_BINDINGS)
    stripped["properties"] = [
        _strip_object(column, _COLUMN_BINDINGS)
        for column in table.get("properties", [])
    ]
    return stripped


def _strip_object(
    obj: Mapping[str, Any], bindings: tuple[str, ...]
) -> dict[str, Any]:
    """Return a copy of `obj` without the `bindings` and the ontology links it lists."""
    return {key: _strip_items(key, value, bindings) for key, value in obj.items()}


def _strip_items(key: str, value: Any, bindings: tuple[str, ...]) -> Any:
    if key == "customProperties":
        return [item for item in value if not _is_binding(item, bindings)]
    if key == "authoritativeDefinitions":
        return [item for item in value if not _is_link(item)]
    return value


# What an ontology fills into a stored contract.

# The ODCS `logicalType` of each ontology type that has one besides `enum[...]` and `ref <Thing>`.
_LOGICAL_TYPES: Final = MappingProxyType(
    {
        "int": "integer",
        "string": "string",
        "timestamp": "timestamp",
        "date": "date",
        "money": "number",
        "decimal": "number",
        "bool": "boolean",
    }
)
_REF: Final = re.compile(r"ref\s+(\S+)")


@dataclass(frozen=True)
class _Field:
    """A key part or a property of a thing.

    `type` is the type text of the field, `ref <Thing>` for a property that names a `ref`.
    `position` is the place of a key part in the thing's key, counted from 1 in file order, and
    `None` for a property.
    """

    type: str | None
    personal: bool
    position: int | None


def resolve(stored: Mapping[str, Any], ontology: Ontology) -> dict[str, Any]:
    """Return the stored contract `stored` filled with what `ontology` knows, a new dict.

    The contract's `service` property (the `continuo` custom property of the contract) must be
    a service that `ontology` declares. For any other contract, one with no `service` included,
    the result is `stored` in the canonical form and nothing more. The result is always in the
    canonical form, so the caller hashes it as it hashes any stored form. `stored` is not
    changed.

    A value fills a gap and never replaces a value the contract holds, so `resolve` run on its
    own result gives the same result. Stored forms are in the canonical form, where a default such
    as `semanticType: column` or `primaryKey: false` is not written, so a gap is a value that
    the form does not hold. What `resolve` fills:

    - the root `domain`: the domain of the service;
    - on a schema object whose `entity` names a thing of the ontology: a link to the thing, an
      `authoritativeDefinitions` entry of `type` `ontology` and the `url` `continuo-ontology:<Thing>`;
    - on each column with no binding (no `property`, `local` or `aggregate`), when the entity
      is a thing that has a key part or a property of the column's name: the binding
      `property: <Thing>.<column>`;
    - on each column whose `property` is `<Thing>.<field>`, where the thing and the field
      exist: a link `continuo-ontology:<Thing>.<field>`; the `logicalType` of the field's type
      (`int` is `integer`, `string` is `string`, `timestamp` is `timestamp`, `date` is `date`,
      `money` and `decimal` are `number`, `bool` is `boolean`, `enum[...]` is `string`, and
      `ref <Thing>` is the type of the key of that thing when its key has one part, so the
      type of a part is read from the key before a property of the same name, and any other
      type gives no `logicalType`); `classification: personal` for a property with
      `personal: true`; and, when `<Thing>` is the entity of the schema object, `<field>` is a
      part of its key and no column of the schema object has `primaryKey: true`,
      `primaryKey: true` with the `primaryKeyPosition` of the part, its place in the thing's
      key counted from 1 in file order (a contract that ends with one key column, whatever
      filled it, holds no position, as every stored form does);
    - on each column with an `aggregate` whose `from` exists (when it has one): `semanticType:
      measure` and the `logicalType` of `from` when it has an `agg` (`integer` for `count`), and
      `semanticType: dimension` and `logicalType: date` when it has a `period` and no `agg`.

    A binding that does not resolve (a thing or a field the ontology does not have, or a value
    of the wrong shape) is left as written and nothing is filled for it, and so is a column that
    holds more than one of `property`, `local` and `aggregate`. Whether a binding resolves is for
    `ontology_check`. Only top-level columns are read.
    """
    resolved = canonical_form(stored)
    service = continuo_values(resolved.get("customProperties")).get("service")
    if not isinstance(service, str) or not ontology.declares(service):
        return resolved
    entities: Mapping[str, Mapping[str, Any]] = ontology.document.get(
        "entities", {}
    )
    domain = ontology.domain_of(service)
    if domain is not None:
        resolved.setdefault("domain", domain)
    for table in resolved.get("schema", []):
        _resolve_table(table, entities)
    return canonical_form(resolved)


def _resolve_table(table: dict[str, Any], entities: Mapping[str, Any]) -> None:
    """Fill the schema object `table`, and its columns, from `entities`."""
    entity = continuo_values(table.get("customProperties")).get("entity")
    thing = entities.get(entity) if isinstance(entity, str) else None
    if thing is not None:
        _add_link(table, f"{LINK_PREFIX}{entity}")
    columns = table.get("properties", [])
    keyed = any(column.get("primaryKey") is True for column in columns)
    for column in columns:
        bound = continuo_values(column.get("customProperties"))
        kinds = [name for name in _COLUMN_BINDINGS if name in bound]
        if len(kinds) > 1:
            continue
        if not kinds and thing is not None:
            name = column.get("name")
            if _field(thing, name) is not None:
                bound = {"property": f"{entity}.{name}"}
                column.setdefault("customProperties", []).append(
                    {
                        "vendor": VENDOR,
                        "property": "property",
                        "value": bound["property"],
                    }
                )
        if "property" in bound:
            _resolve_property(column, bound["property"], entities, entity, keyed)
        elif "aggregate" in bound:
            _resolve_aggregate(column, bound["aggregate"], entities)
    # A position orders the columns of a key, and a key of one column, declared or filled in by
    # the ontology, has none: the form is the one the `normalise_*` functions give.
    _drop_single_key_position(columns)


def _resolve_property(
    column: dict[str, Any],
    value: object,
    entities: Mapping[str, Any],
    entity: object,
    keyed: bool,
) -> None:
    """Fill `column` from the `<Thing>.<field>` that `value` names, when it resolves."""
    if not isinstance(value, str):
        return
    name, _, field_name = value.partition(".")
    found = _field(entities.get(name), field_name)
    if found is None:
        return
    _add_link(column, f"{LINK_PREFIX}{value}")
    logical = _logical_type(entities, found.type, ())
    if logical is not None:
        column.setdefault("logicalType", logical)
    if found.personal:
        column.setdefault("classification", "personal")
    if found.position is not None and name == entity and not keyed:
        column.setdefault("primaryKey", True)
        column.setdefault("primaryKeyPosition", found.position)


def _resolve_aggregate(
    column: dict[str, Any], value: object, entities: Mapping[str, Any]
) -> None:
    """Fill `column` from the `aggregate` mapping `value`, when its `from` resolves."""
    if not isinstance(value, Mapping):
        return
    source = value.get("from")
    logical = None
    if source is not None:
        if not isinstance(source, str):
            return
        name, _, field_name = source.partition(".")
        thing = entities.get(name)
        if thing is None:
            return
        if field_name:
            found = _field(thing, field_name)
            if found is None:
                return
            logical = _logical_type(entities, found.type, ())
    agg = value.get("agg")
    if agg and isinstance(agg, str):
        semantic = "measure"
        if agg == "count":
            logical = "integer"
    elif value.get("period") and isinstance(value["period"], str):
        semantic, logical = "dimension", "date"
    else:
        return
    column.setdefault("semanticType", semantic)
    if logical is not None:
        column.setdefault("logicalType", logical)


def _add_link(obj: dict[str, Any], url: str) -> None:
    """Add the ontology link `url` to the `authoritativeDefinitions` of `obj`, once."""
    links = obj.setdefault("authoritativeDefinitions", [])
    if not any(
        isinstance(link, Mapping) and link.get("url") == url for link in links
    ):
        links.append({"type": "ontology", "url": url})


def _field(thing: Mapping[str, Any] | None, name: object) -> _Field | None:
    """Return the key part or property `name` of `thing`, or `None` when it has none.

    A key part is looked up before a property of the same name.
    """
    if thing is None or not isinstance(name, str):
        return None
    key: Mapping[str, str] = thing.get("key", {})
    if name in key:
        return _Field(key[name], False, list(key).index(name) + 1)
    properties: Mapping[str, Mapping[str, Any]] = thing.get("properties", {})
    declared = properties.get(name)
    if declared is None:
        return None
    ref = declared.get("ref")
    return _Field(
        f"ref {ref}" if ref is not None else declared.get("type"),
        declared.get("personal") is True,
        None,
    )


def _logical_type(
    entities: Mapping[str, Any], type_text: str | None, seen: tuple[str, ...]
) -> str | None:
    """Return the ODCS `logicalType` of the ontology type `type_text`, or `None` when it has none.

    `seen` lists the things whose key the walk has read, so a `ref` cycle ends with `None`.
    """
    if type_text is None:
        return None
    text = type_text.strip()
    if text in _LOGICAL_TYPES:
        return _LOGICAL_TYPES[text]
    if text.startswith("enum[") and text.endswith("]"):
        return "string"
    ref = _REF.fullmatch(text)
    if ref is None or (target := ref.group(1)) in seen:
        return None
    key: Mapping[str, str] = entities.get(target, {}).get("key", {})
    if len(key) != 1:
        return None
    return _logical_type(entities, next(iter(key.values())), (*seen, target))
