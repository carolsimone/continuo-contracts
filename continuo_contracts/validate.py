"""Check the ODCS contract of a Python node and list every problem it has.

A Python node is declared by one ODCS contract file. Checking it takes two stages. The first
stage, `schema_issues`, checks the document against the ODCS v3.2.0 JSON schema. The second
stage, `rule_issues`, checks continuo's own rules on a document that the first stage accepted:
the envelope, the identity of the table, the file name, the team, the fields a Python contract
may not carry and the custom properties of the `continuo` vendor. `validate_python_contract`
runs the first stage and, when it finds nothing, the second.

Every problem is an `Issue` with the path of the place it is found at, one of the closed reason
codes of `REASONS` and a sentence for a person. The issues come in path order, so the same
document always gives the same list.
"""

import ast
import functools
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from jsonschema import Draft201909Validator
from jsonschema.exceptions import ValidationError

from continuo_contracts import schema
from continuo_contracts.vocabulary import PROPERTIES, VENDOR

REASONS: Final = (
    "strict_yaml",
    "odcs_schema",
    "api_version",
    "identity",
    "file_name",
    "service",
    "missing_field",
    "unknown_property",
    "refused_property",
    "refused_field",
    "kind_rule",
    "column_type",
    "primary_key",
    "value",
)

_API_VERSION: Final = "v3.2.0"
_FILE_SUFFIX: Final = ".odcs.yaml"
_MAX_MESSAGE: Final = 300

# The root fields a Python contract may not carry, each with its reason.
_REFUSED_FIELDS: Final = {
    "servers": "a Python contract names no server, the install decides where the table lives",
    "tenant": "a Python contract names no tenant, the install decides it",
    "contractCreatedTs": "a Python contract carries no creation time, a clock is not part of it",
}

# The keys whose value the JSON schema leaves free-form: a value there is data of the author, so
# a `customProperties` key inside it is not a list of custom properties.
_FREE_FORM: Final = frozenset({"value", "examples", "arguments", "implementation"})

# Where a `customProperties` list sits, as the words of a message.
_PLACES: Final = {
    "root": "the contract",
    "schema": "the schema object",
    "quality": "a quality rule",
    "other": "this object",
}

_PATH_TOKEN: Final = re.compile(r"\[(\d+)\]|([^.\[\]]+)")
_UNEVALUATED: Final = re.compile(
    r"Unevaluated properties are not allowed \((.*) (?:was|were) unexpected\)\Z", re.DOTALL
)


@dataclass(frozen=True)
class Issue:
    """One problem of a contract.

    `path` says where it is, in the form `schema[0].properties[2].physicalType`, with `$` for
    the contract as a whole. `reason` is one of `REASONS`. `message` is a sentence that says what
    is wrong.
    """

    path: str
    reason: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _issue(path: str, reason: str, message: str) -> Issue:
    """Build an issue whose message is cut when it is long, so one huge value of a contract
    cannot make a huge message."""
    if len(message) > _MAX_MESSAGE:
        message = message[: _MAX_MESSAGE - 3] + "..."
    return Issue(path, reason, message)


def _path_key(path: str) -> list[tuple[int, int, str]]:
    """Return the sort key of a path: the root first, a key by its text and an array index by its
    number, so `a[4]` comes before `a[10]`."""
    if path == "$":
        return []
    return [
        (0, int(index), "") if index else (1, 0, name) for index, name in _PATH_TOKEN.findall(path)
    ]


def _ordered(issues: Iterable[Issue]) -> list[Issue]:
    """Return the issues in path order, then in message order."""
    return sorted(issues, key=lambda i: (_path_key(i.path), i.message, i.reason))


def _json_path(parts: Iterable[str | int]) -> str:
    """Write the parts of a position in a document as an issue path."""
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path or "$"


@functools.cache
def _validator() -> Draft201909Validator:
    """Return the validator of the ODCS schema, built on the first call."""
    return Draft201909Validator(schema.load())


def _is_knock_on(error: ValidationError, failing: set[tuple[str | int, ...]]) -> bool:
    """Say whether `error` only repeats failures below it.

    A property whose value fails its schema also counts as not evaluated, so the schema of an
    object that allows no other property reports "Unevaluated properties are not allowed" for it
    next to the error of the value itself. The error is a knock-on when it names only properties
    that have an error of their own, which are the paths in `failing`. A message of another form
    is never a knock-on.
    """
    match = _UNEVALUATED.match(error.message)
    if error.validator != "unevaluatedProperties" or match is None:
        return False
    try:
        keys = ast.literal_eval(f"[{match.group(1)}]")
    except (ValueError, SyntaxError):
        return False
    here = tuple(error.absolute_path)
    return bool(keys) and all(
        any(path[: len(here) + 1] == (*here, key) for path in failing) for key in keys
    )


def schema_issues(doc: object) -> list[Issue]:
    """Return an `odcs_schema` issue for each error of `doc` against the ODCS v3.2.0 JSON schema.

    `doc` is whatever `yaml_strict.load` returned, so it may be any value: one that is not a
    mapping gives a single issue at `$`. Two errors with the same path and message give one
    issue, and so does an "Unevaluated properties" error that names only properties with an error
    of their own, because the error of the property says what is wrong. The issues are sorted by
    path, with array indexes in numeric order, and then by message. A message that is longer
    than 300 characters is cut and ends in `...`.
    """
    errors = list(_validator().iter_errors(doc))
    failing = {tuple(error.absolute_path) for error in errors}
    return _ordered(
        {
            _issue(_json_path(error.absolute_path), "odcs_schema", error.message)
            for error in errors
            if not _is_knock_on(error, failing)
        }
    )


@dataclass(frozen=True)
class _Contract:
    """A document that the JSON schema accepted, with what the rules read of it besides its
    fields."""

    doc: Mapping[str, Any]
    file_name: str
    service: str | None

    @property
    def schema_objects(self) -> Sequence[Any]:
        """The items of `schema`, or none when the field is not a list."""
        objects = self.doc.get("schema")
        return objects if isinstance(objects, list) else ()

    @property
    def table_object(self) -> Mapping[str, Any] | None:
        """The one schema object of the contract, or `None` when it has none or more than one."""
        objects = self.schema_objects
        if len(objects) == 1 and isinstance(objects[0], Mapping):
            return cast(Mapping[str, Any], objects[0])
        return None

    @property
    def table(self) -> str | None:
        """The name of the table, or `None` when the contract has no single schema object or
        that object has no name."""
        obj = self.table_object
        name = obj.get("name") if obj is not None else None
        return name if isinstance(name, str) and name else None

    @property
    def id_schema(self) -> str | None:
        """The schema part of the id, in lower case, or `None` when the id is not a schema, a dot
        and the table."""
        identifier = self.doc.get("id")
        table = self.table
        if table is None or not isinstance(identifier, str):
            return None
        lowered = identifier.lower()
        tail = "." + table.lower()
        if lowered.endswith(tail) and len(lowered) > len(tail):
            return lowered[: -len(tail)]
        return None


_Rule = Callable[[_Contract], Iterator[Issue]]


def _base_name(path: str) -> str:
    """Return the last part of a file path, whichever separator it uses."""
    return re.split(r"[\\/]", path)[-1]


def _envelope(contract: _Contract) -> Iterator[Issue]:
    version = contract.doc.get("apiVersion")
    if version != _API_VERSION:
        yield _issue(
            "apiVersion",
            "api_version",
            f"a Python contract is written for {_API_VERSION}, not {version!r}",
        )


def _identity(contract: _Contract) -> Iterator[Issue]:
    doc = contract.doc
    identifier = doc.get("id")
    if isinstance(identifier, str) and identifier != identifier.lower():
        yield _issue("id", "identity", f"the id must be in lower case, as {identifier.lower()!r}")
    obj = contract.table_object
    if obj is None:
        yield _issue(
            "schema",
            "identity",
            f"a Python contract has exactly one schema object, this one has "
            f"{len(contract.schema_objects)}",
        )
        return
    if obj.get("physicalType") != "table":
        found = f", not {obj['physicalType']!r}" if "physicalType" in obj else ""
        yield _issue(
            "schema[0].physicalType",
            "identity",
            f"the physicalType of the schema object of a Python contract must be table{found}",
        )
    if obj.get("name") == "":
        yield _issue("schema[0].name", "identity", "the name of the table must not be empty")
    table = contract.table
    if table is None:
        return
    if isinstance(identifier, str) and contract.id_schema is None:
        yield _issue(
            "id",
            "identity",
            f"the id must be a schema name, a dot and the table in lower case, "
            f"as in 'analytics.{table.lower()}'",
        )
    if "name" in doc and doc["name"] != table:
        yield _issue(
            "name",
            "identity",
            f"the name must be the table, {table!r}, or be left out",
        )
    if "physicalName" in obj and obj["physicalName"] != table:
        yield _issue(
            "schema[0].physicalName",
            "identity",
            f"the physical name must be the table, {table!r}, or be left out",
        )


def _file_name(contract: _Contract) -> Iterator[Issue]:
    table = contract.table
    if table is None:
        return
    allowed = [f"{table}{_FILE_SUFFIX}"]
    id_schema = contract.id_schema
    if id_schema is not None:
        allowed.append(f"{id_schema}.{table}{_FILE_SUFFIX}")
    if contract.file_name not in allowed:
        yield _issue(
            "$",
            "file_name",
            f"the file is named {contract.file_name!r}, it must be "
            + " or ".join(repr(name) for name in allowed),
        )


def _team(contract: _Contract) -> Iterator[Issue]:
    team = contract.doc.get("team")
    name = team.get("name") if isinstance(team, Mapping) else None
    if not isinstance(name, str) or not name.strip():
        yield _issue("team.name", "missing_field", "a Python contract names its team in team.name")


def _refused_fields(contract: _Contract) -> Iterator[Issue]:
    for field, why in _REFUSED_FIELDS.items():
        if field in contract.doc:
            yield _issue(field, "refused_field", why)


def _below(level: str, key: str) -> str:
    """Return the level of the objects under `key` of an object on `level`: `schema` objects sit
    under the contract, a quality rule sits under any object, and everything else is `other`."""
    if key == "quality":
        return "quality"
    if key == "schema" and level == "root":
        return "schema"
    return "other"


def _custom_property_lists(
    node: object, path: str, level: str
) -> Iterator[tuple[str, str, list[Any]]]:
    """Yield the path, the level and the items of every `customProperties` list under `node`.

    The level says which object the list belongs to: `root` (the contract), `schema` (a schema
    object), `quality` (a quality rule) or `other`. The items of a list are not walked into, and
    neither is a free-form value.
    """
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _custom_property_lists(item, f"{path}[{index}]", level)
    elif isinstance(node, Mapping):
        for key, child in node.items():
            if key in _FREE_FORM:
                continue
            child_path = f"{path}.{key}" if path else key
            if key == "customProperties":
                if isinstance(child, list):
                    yield child_path, level, child
            else:
                yield from _custom_property_lists(child, child_path, _below(level, key))


def _placement_fault(name: str, level: str) -> tuple[str, str] | None:
    """Return the reason and the message for a `continuo` property that may not sit on `level`
    of a Python contract, or `None` when it may."""
    prop = PROPERTIES.get(name)
    if prop is None:
        return "unknown_property", f"`{name}` is not a continuo property"
    if prop.python == "filled":
        return (
            "refused_property",
            f"continuo fills `{name}` itself, a Python contract does not write it",
        )
    if prop.python == "refused":
        return "refused_property", f"a Python contract does not carry `{name}`"
    if prop.sits_on != level:
        return (
            "refused_property",
            f"`{name}` sits on {_PLACES[prop.sits_on]}, not on {_PLACES[level]}",
        )
    return None


def _custom_properties(contract: _Contract) -> Iterator[Issue]:
    for path, level, items in _custom_property_lists(contract.doc, "", "root"):
        first: dict[str, int] = {}
        for index, item in enumerate(items):
            if not isinstance(item, Mapping) or item.get("vendor") != VENDOR:
                continue
            name = item.get("property")
            if not isinstance(name, str):
                continue
            where = f"{path}[{index}]"
            fault = _placement_fault(name, level)
            if fault is not None:
                yield _issue(where, *fault)
                continue
            if name in first:
                yield _issue(
                    where,
                    "value",
                    f"`{name}` is written twice, the first time at {path}[{first[name]}]",
                )
            else:
                first[name] = index
            if (
                name == "service"
                and contract.service is not None
                and item.get("value") != contract.service
            ):
                yield _issue(
                    where,
                    "service",
                    f"`service` is {item.get('value')!r}, the service of the release is "
                    f"{contract.service!r}",
                )


_RULES: Final[tuple[_Rule, ...]] = (
    _envelope,
    _identity,
    _file_name,
    _team,
    _refused_fields,
    _custom_properties,
)


def rule_issues(doc: Mapping[str, Any], *, path: str, service: str | None) -> list[Issue]:
    """Return the issues that continuo's rules find in `doc`, without the JSON schema check.

    `doc` is a document that `schema_issues` accepted, and `path` is the repository path of its
    file, of which only the base name counts. `service` is the service of the release that
    carries the contract, or `None` when it is not known, which skips the check of the `service`
    property. The issues come in path order, then in message order.

    The rules are the envelope (`apiVersion` is `v3.2.0`), the identity of the table (one schema
    object, a table, with the id, the name and the physical name agreeing with it), the file
    name, `team.name`, the root fields a Python contract may not carry, and the custom properties
    of the `continuo` vendor: each must be a property that the vocabulary knows, that a Python
    contract carries and that sits where it is written, and it may be written once in a list.
    """
    contract = _Contract(doc=doc, file_name=_base_name(path), service=service)
    return _ordered(issue for rule in _RULES for issue in rule(contract))


def validate_python_contract(doc: object, *, path: str, service: str | None) -> list[Issue]:
    """Return every issue of the Python contract `doc`, or an empty list when it is valid.

    The result is `schema_issues(doc)` when that has any issue, and `rule_issues` of the same
    arguments otherwise, because the rules read a document of the shape the schema describes.
    `path` is the repository path of the file, of which only the base name is checked, and
    `service` is the service of the release, or `None` to skip the check of the `service`
    property.
    """
    found = schema_issues(doc)
    if found:
        return found
    # The schema accepts only an object, so `doc` is a mapping here.
    return rule_issues(cast(Mapping[str, Any], doc), path=path, service=service)
