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
the whole ontology part.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from continuo_contracts import yaml_strict
from continuo_contracts.contract import canonical_form
from continuo_contracts.errors import OntologyError, StrictYamlError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.vocabulary import VENDOR

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
