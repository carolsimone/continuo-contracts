"""Check the ODCS contract of a Python node and list every problem it has.

A Python node is declared by one ODCS contract file. Checking it takes two stages. The first
stage, `schema_issues`, checks the document against the ODCS v3.2.0 JSON schema. The second
stage, `rule_issues`, checks continuo's own rules on a document that the first stage accepted:
the envelope, the identity of the table, the file name, the team, the fields a Python contract
may not carry, the custom properties of the `continuo` vendor, the rules of each kind of node,
the columns and the key of an upsert node. `validate_python_contract` runs the first stage and,
when it finds nothing, the second.

Every problem is an `Issue` with the path of the place it is found at, one of the closed reason
codes of `REASONS` and a sentence for a person. The issues come in path order, so the same
document always gives the same list.
"""

import ast
import functools
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Final, cast

from jsonschema import Draft201909Validator
from jsonschema.exceptions import ValidationError

from continuo_contracts import schema
from continuo_contracts.errors import ProjectionError
from continuo_contracts.python import (
    EXTRA_COLUMNS_POLICIES,
    KIND_ALIASES,
    MODE_TYPES,
    PYTHON_KINDS,
    canonical_sql_type,
)
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

# Why continuo fills a property or a Python contract does not carry it, for the message that
# refuses the property.
_WHY_FILLED: Final = {
    "reads": "continuo fills `reads` from the tables the read queries name, an author may not "
    "write it",
}
_NOT_READ_BY_PYTHON: Final = "nothing reads it on a Python node"
_WHY_REFUSED: Final = {
    "provenance": "continuo adds `provenance` to the published file and never stores it in a "
    "contract",
    **dict.fromkeys(
        "filePath materialization contractEnforced access group deprecationDate "
        "freshnessBuildAfter snapshot quoteColumns".split(),
        "it describes how dbt builds or governs a node, and nothing reads it on a Python node",
    ),
}

_CRITICALITIES: Final = ("REGULATORY", "CORE", "SECONDARY")
_TIMEOUT_MAX_MINUTES: Final = 10080
_SECRET_REF_MAX: Final = 253
_SECRET_REF_PREFIX: Final = "continuo-api-"
_SECRET_REF: Final = re.compile(r"continuo-api-[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")
_S3_OBJECT: Final = re.compile(r"s3://[^/\s]+/.+")
_HTTPS_ADDRESS: Final = re.compile(r"https://[^/?#\s]+(?:/\S*)?")
_SHOWN: Final = 60

_COLUMN_TYPES: Final = (
    "BIGINT, INTEGER (or INT), DOUBLE PRECISION, TEXT, TIMESTAMP, DATE, BOOLEAN, NUMERIC(p,s) "
    "(or DECIMAL), VARCHAR(n) and CHAR(n)"
)

# The properties that every Python contract writes, with what each says.
_ALWAYS_NEEDED: Final = {
    "schedule": "the schedule that runs the node",
    "criticality": "how critical the node is: REGULATORY, CORE or SECONDARY",
}

# What a property that a kind needs says, for the message of the missing one.
_NEEDS: Final = {
    "script": "the path of the script that fills the table",
    "readQueries": "the queries that read the tables the script uses, by name",
    "csvSource": "the s3:// or https:// address of the CSV file",
}


@dataclass(frozen=True)
class _KindRule:
    """What a kind of node needs and refuses. `required` lists the properties it must write and
    `refused` maps each property it may not write to the reason."""

    required: tuple[str, ...]
    refused: Mapping[str, str]


_CSV_ONLY: Final = "only a python-csv node loads a CSV file"
_READS_ONLY: Final = "it reads no table, only a python-node reads the tables its queries name"
_API_ONLY: Final = "only a python-api node reads a Secret"

_KIND_RULES: Final = {
    "python-node": _KindRule(
        required=("script", "readQueries"),
        refused={"csvSource": _CSV_ONLY, "secretRef": _API_ONLY},
    ),
    "python-csv": _KindRule(
        required=("csvSource",),
        refused={
            "script": "it loads its CSV file and runs no script",
            "readQueries": _READS_ONLY,
            "secretRef": _API_ONLY,
            "assets": "it runs no script that could read an asset",
        },
    ),
    "python-api": _KindRule(
        required=("script",),
        refused={
            "readQueries": _READS_ONLY,
            "csvSource": _CSV_ONLY,
        },
    ),
}

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
class _Prop:
    """A `continuo` property of the contract: the path of its item and its value."""

    path: str
    value: Any


def _table_name_fault(name: object) -> str | None:
    """Return what is wrong with `name` as the name of the table, or `None` when it is fine."""
    if not isinstance(name, str) or not name.strip():
        return "the name of the table must not be blank"
    if name != name.strip():
        return "the name of the table must not start or end with whitespace"
    if "." in name:
        return "the name of the table must not contain a dot, the id carries the schema before it"
    return None


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
        that object has no name that a table can have, so that nothing is compared with it."""
        obj = self.table_object
        name = obj.get("name") if obj is not None else None
        return name if isinstance(name, str) and _table_name_fault(name) is None else None

    @property
    def id_schema(self) -> str | None:
        """The schema part of the id, in lower case, or `None` when the id is not a schema name
        without a dot, a dot and the table."""
        identifier = self.doc.get("id")
        table = self.table
        if table is None or not isinstance(identifier, str):
            return None
        lowered = identifier.lower()
        tail = "." + table.lower()
        if lowered.endswith(tail) and len(lowered) > len(tail):
            id_schema = lowered[: -len(tail)]
            return None if "." in id_schema else id_schema
        return None

    @cached_property
    def properties(self) -> Mapping[str, _Prop]:
        """The `continuo` properties of the contract's own `customProperties`, by name.

        A property written twice is the first one, which is the one the repeat refusal names. An
        item with no `value`, or with a `property` that is not a string, is not a property.
        """
        found: dict[str, _Prop] = {}
        items = self.doc.get("customProperties")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, Mapping) or item.get("vendor") != VENDOR:
                    continue
                name = item.get("property")
                if isinstance(name, str) and "value" in item and name not in found:
                    found[name] = _Prop(f"customProperties[{index}]", item["value"])
        return found

    @cached_property
    def kind(self) -> str | None:
        """The kind of the node, with an old name read as the kind it stands for, or `None` when
        the `kind` property is not one of `PYTHON_KINDS`. A contract with no `kind` is a
        python-node."""
        prop = self.properties.get("kind")
        declared = prop.value if prop is not None else "python-node"
        if not isinstance(declared, str):
            return None
        kind = KIND_ALIASES.get(declared, declared)
        return kind if kind in PYTHON_KINDS else None

    @cached_property
    def mode_type(self) -> str | None:
        """The `modeType` of the node, `replace` when the contract writes none, or `None` when
        the written one is not one of `MODE_TYPES`."""
        prop = self.properties.get("modeType")
        if prop is None:
            return "replace"
        value = prop.value
        return value if isinstance(value, str) and value in MODE_TYPES else None

    @property
    def columns(self) -> list[Any]:
        """The columns of the table, or an empty list when it has none."""
        obj = self.table_object
        columns = obj.get("properties") if obj is not None else None
        return columns if isinstance(columns, list) else []


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
    fault = _table_name_fault(obj.get("name"))
    if fault is not None:
        yield _issue("schema[0].name", "identity", fault)
    table = contract.table
    if table is None:
        return
    if isinstance(identifier, str) and contract.id_schema is None:
        yield _issue(
            "id",
            "identity",
            f"the id must be a schema name without a dot, a dot and the table, in lower case, "
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
    object), `quality` (a quality rule) or `other`. The items of a `customProperties` list are
    not walked into, and neither is a free-form value.
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
        return (
            "unknown_property",
            f"`{name}` is not a continuo property that a Python contract knows, "
            "check the spelling or leave it out",
        )
    if prop.python == "filled":
        why = _WHY_FILLED.get(
            name, f"continuo fills `{name}` itself, a Python contract does not write it"
        )
        return "refused_property", why
    if prop.python == "refused":
        why = _WHY_REFUSED.get(name, _NOT_READ_BY_PYTHON)
        return "refused_property", f"a Python contract does not carry `{name}`: {why}"
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
                    f"`{name}` is written twice, the first time at {path}[{first[name]}], "
                    "a repeat leaves it unclear which one counts",
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


def _shown(value: object) -> str:
    """Write `value` for a message, cut when it is long, so one huge value of a contract cannot
    push the rest of the sentence out of the message."""
    text = repr(value)
    return text if len(text) <= _SHOWN else text[: _SHOWN - 3] + "..."


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_criticality(value: object) -> Iterator[str]:
    if value not in _CRITICALITIES:
        yield f"`criticality` is {_shown(value)}, it must be one of {', '.join(_CRITICALITIES)}"


def _check_timeout(value: object) -> Iterator[str]:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= _TIMEOUT_MAX_MINUTES
    ):
        yield (
            f"`timeoutMinutes` is {_shown(value)}, it must be a whole number of minutes from 1 "
            f"to {_TIMEOUT_MAX_MINUTES}"
        )


def _check_extra_columns(value: object) -> Iterator[str]:
    if value not in EXTRA_COLUMNS_POLICIES:
        yield (
            f"`extraColumns` is {_shown(value)}, it must be {' or '.join(EXTRA_COLUMNS_POLICIES)}"
        )


def _check_mode_type(value: object) -> Iterator[str]:
    if value not in MODE_TYPES:
        yield f"`modeType` is {_shown(value)}, it must be {_one_of(MODE_TYPES)}"


def _one_of(words: Sequence[str]) -> str:
    return ", ".join(words[:-1]) + f" or {words[-1]}"


def _check_schedule(value: object) -> Iterator[str]:
    if not _is_text(value):
        yield f"`schedule` is {_shown(value)}, it must be the name of a schedule, a string"


def _check_script(value: object) -> Iterator[str]:
    if not _is_text(value):
        yield f"`script` is {_shown(value)}, it must be the path of the script, a string"


def _check_config(value: object) -> Iterator[str]:
    if not isinstance(value, Mapping):
        yield f"`config` is {_shown(value)}, it must be a mapping of settings"


def _check_secret_ref(value: object) -> Iterator[str]:
    if (
        not isinstance(value, str)
        or len(value) > _SECRET_REF_MAX
        or _SECRET_REF.fullmatch(value) is None
    ):
        yield (
            f"`secretRef` is {_shown(value)}, it must be the name of a Kubernetes Secret that "
            f"starts with `{_SECRET_REF_PREFIX}` and goes on with lowercase letters, digits and "
            f"`-`, at most {_SECRET_REF_MAX} characters"
        )


def _check_csv_source(value: object) -> Iterator[str]:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or (_S3_OBJECT.fullmatch(value) is None and _HTTPS_ADDRESS.fullmatch(value) is None)
    ):
        yield (
            f"`csvSource` is {_shown(value)}, it must be `s3://<bucket>/<key>` or "
            "`https://<host>[/<path>]`"
        )


def _check_read_queries(value: object) -> Iterator[str]:
    if not isinstance(value, Mapping) or not value:
        yield (
            f"`readQueries` is {_shown(value)}, it must be a mapping of query names to the "
            "queries, with at least one"
        )
        return
    for name, query in value.items():
        if not _is_text(name):
            yield f"`readQueries` has a query named {_shown(name)}, a name must have text"
        if not _is_text(query):
            yield (
                f"`readQueries` has the query {_shown(name)} that is {_shown(query)}, a query "
                "must be a string with text"
            )


def _asset_fault(asset: object) -> str | None:
    if not isinstance(asset, str) or not asset.strip():
        return f"{_shown(asset)} is not a path"
    if asset.startswith("/"):
        return f"{_shown(asset)} is an absolute path"
    if ".." in asset.split("/"):
        return f"{_shown(asset)} leaves the project with `..`"
    return None


def _check_assets(value: object) -> Iterator[str]:
    if not isinstance(value, list):
        yield f"`assets` is {_shown(value)}, it must be a list of paths inside the project"
        return
    for asset in value:
        fault = _asset_fault(asset)
        if fault is not None:
            yield f"`assets` lists a path that is not inside the project: {fault}"
    texts = [asset for asset in value if isinstance(asset, str)]
    for asset in sorted({asset for asset in texts if texts.count(asset) > 1}):
        yield f"`assets` lists {_shown(asset)} twice, a path is listed once"


# The properties whose value is checked, each with its check, whatever the kind of the node.
_VALUE_CHECKS: Final[dict[str, Callable[[object], Iterator[str]]]] = {
    "schedule": _check_schedule,
    "criticality": _check_criticality,
    "timeoutMinutes": _check_timeout,
    "script": _check_script,
    "readQueries": _check_read_queries,
    "csvSource": _check_csv_source,
    "extraColumns": _check_extra_columns,
    "config": _check_config,
    "secretRef": _check_secret_ref,
    "modeType": _check_mode_type,
    "assets": _check_assets,
}


def _always_needed(contract: _Contract) -> Iterator[Issue]:
    for name, what in _ALWAYS_NEEDED.items():
        if name not in contract.properties:
            yield _issue(
                "customProperties",
                "missing_field",
                f"a Python contract writes `{name}` in its customProperties: {what}",
            )


def _kind_value(contract: _Contract) -> Iterator[Issue]:
    prop = contract.properties.get("kind")
    if prop is not None and contract.kind is None:
        yield _issue(
            prop.path,
            "value",
            f"`kind` is {_shown(prop.value)}, it must be python-node, python-csv or python-api "
            "(python-model is read as python-node)",
        )


def _property_values(contract: _Contract) -> Iterator[Issue]:
    kind = contract.kind
    refused = _KIND_RULES[kind].refused if kind is not None else {}
    for name, check in _VALUE_CHECKS.items():
        prop = contract.properties.get(name)
        # A property the kind refuses gets the kind's issue and no second one for its value.
        if prop is None or name in refused:
            continue
        for message in check(prop.value):
            yield _issue(prop.path, "value", message)


def _kind_rules(contract: _Contract) -> Iterator[Issue]:
    kind = contract.kind
    if kind is None:
        return
    rule = _KIND_RULES[kind]
    for name in rule.required:
        if name not in contract.properties:
            yield _issue(
                "customProperties",
                "kind_rule",
                f"a {kind} node needs `{name}`: {_NEEDS[name]}",
            )
    for name, why in rule.refused.items():
        prop = contract.properties.get(name)
        if prop is not None:
            yield _issue(prop.path, "kind_rule", f"a {kind} node does not take `{name}`: {why}")
    mode = contract.properties.get("modeType")
    if kind == "python-csv" and mode is not None and contract.mode_type not in (None, "replace"):
        yield _issue(
            mode.path,
            "kind_rule",
            "a python-csv node replaces its table, `modeType` is replace, "
            f"not {_shown(mode.value)}",
        )


def _column_type_issue(raw: object, path: str) -> Issue | None:
    """Return the `column_type` issue of a column type, or `None` when continuo accepts it."""
    detail = ""
    if isinstance(raw, str):
        try:
            canonical_sql_type(raw)
        except ProjectionError as error:
            # A refusal for a range says the range, and any other refusal is the plain sentence.
            detail = f": {error}" if "out of range" in str(error) else ""
        else:
            return None
    return _issue(path, "column_type", f"{_shown(raw)} is not a type continuo accepts{detail}")


def _is_double_precision(raw: object) -> bool:
    """Say whether `raw` is a column type that canonicalises to `DOUBLE PRECISION`."""
    if not isinstance(raw, str):
        return False
    try:
        return canonical_sql_type(raw) == "DOUBLE PRECISION"
    except ProjectionError:
        return False


def _columns(contract: _Contract) -> Iterator[Issue]:
    if contract.table_object is None:
        return
    columns = contract.columns
    if not columns:
        yield _issue(
            "schema[0].properties",
            "missing_field",
            "a Python contract lists the columns of its table in the properties of the schema "
            "object, at least one",
        )
        return
    first: dict[str, int] = {}
    for index, column in enumerate(columns):
        if not isinstance(column, Mapping):
            continue
        where = f"schema[0].properties[{index}]"
        name = column.get("name")
        shown = _shown(name)
        if isinstance(name, str) and not name.strip():
            yield _issue(f"{where}.name", "value", "the name of a column must not be blank")
        elif isinstance(name, str) and name in first:
            yield _issue(
                f"{where}.name",
                "value",
                f"{shown} is also the name of schema[0].properties[{first[name]}], a column name "
                "is used once",
            )
        elif isinstance(name, str):
            first[name] = index
        if "physicalType" not in column:
            yield _issue(
                f"{where}.physicalType",
                "missing_field",
                f"column {shown} declares its type in physicalType: {_COLUMN_TYPES}",
            )
        else:
            issue = _column_type_issue(column["physicalType"], f"{where}.physicalType")
            if issue is not None:
                yield issue
        if "physicalName" in column and column["physicalName"] != name:
            yield _issue(
                f"{where}.physicalName",
                "refused_field",
                f"the physical name of column {shown} must be its name or be left out, the table "
                "has the column that the name gives",
            )
        if column.get("partitioned") is True:
            yield _issue(
                f"{where}.partitioned",
                "refused_field",
                f"column {shown} must not be partitioned, the table of a Python node is not "
                "partitioned",
            )
        position = column.get("partitionKeyPosition", -1)
        if position != -1:
            yield _issue(
                f"{where}.partitionKeyPosition",
                "refused_field",
                f"the partitionKeyPosition of column {shown} must be -1 or left out, the table "
                "of a Python node is not partitioned",
            )


def _upsert_key(contract: _Contract) -> Iterator[Issue]:
    if contract.mode_type != "upsert" or not contract.columns:
        return
    keys = [
        (index, column)
        for index, column in enumerate(contract.columns)
        if isinstance(column, Mapping) and column.get("primaryKey") is True
    ]
    if not keys:
        yield _issue(
            "schema[0].properties",
            "primary_key",
            "an upsert node needs a key: set `primaryKey: true` on the column or columns that "
            "identify a row",
        )
        return
    taken: dict[int, str] = {}
    for index, column in keys:
        where = f"schema[0].properties[{index}]"
        name = _shown(column.get("name"))
        if column.get("required") is not True:
            yield _issue(
                f"{where}.required",
                "primary_key",
                f"key column {name} must be `required: true`, a key never holds NULL",
            )
        if _is_double_precision(column.get("physicalType")):
            yield _issue(
                f"{where}.physicalType",
                "primary_key",
                f"key column {name} must not be DOUBLE PRECISION, a key is compared for equality "
                "and a floating point number is not exact",
            )
        position = column.get("primaryKeyPosition", -1)
        if len(keys) > 1 and isinstance(position, int):
            if position in taken:
                yield _issue(
                    f"{where}.primaryKeyPosition",
                    "primary_key",
                    f"primaryKeyPosition {position} is already the position of key column "
                    f"{taken[position]}, each key column has its own position from 1",
                )
            else:
                taken[position] = name


_RULES: Final[tuple[_Rule, ...]] = (
    _envelope,
    _identity,
    _file_name,
    _team,
    _refused_fields,
    _custom_properties,
    _always_needed,
    _kind_value,
    _property_values,
    _kind_rules,
    _columns,
    _upsert_key,
)


def rule_issues(doc: Mapping[str, Any], *, path: str, service: str | None) -> list[Issue]:
    """Return the issues that continuo's rules find in `doc`, without the JSON schema check.

    `doc` is a document that `schema_issues` accepted, and `path` is the repository path of its
    file, of which only the base name counts. `service` is the service of the release that
    carries the contract, or `None` when it is not known, which skips the check of the `service`
    property. The issues come in path order, then in message order.

    The rules are the envelope (`apiVersion` is `v3.2.0`), the identity of the table (one schema
    object, a table whose name is not blank, has no outer whitespace and no dot, with the id, the
    name and the physical name agreeing with it), the file name, `team.name`, the root fields a
    Python contract may not carry, and the custom properties of the `continuo` vendor: each must
    be a property that the vocabulary knows, that a Python contract carries and that sits where
    it is written, and it may be written once in a list.

    The rules of the node come after them. The contract writes `schedule` and `criticality`, and
    each property it writes has a value of its own kind. The `kind` (a python-node when it is not
    written, `python-model` read as python-node) decides which of `script`, `readQueries`,
    `csvSource`, `secretRef` and `assets` the contract must write, may write or may not write,
    and a python-csv node replaces its table. A kind that is not a Python kind gives one issue
    and skips these per-kind rules. The table lists at least one column, each with a name that is
    used once and a `physicalType` that `canonical_sql_type` accepts, and no column has a
    `physicalName` other than its name or is partitioned. The key of an `upsert` node is the
    columns with `primaryKey: true`: there is one, each is `required: true` and not
    `DOUBLE PRECISION`, and no two have the same `primaryKeyPosition`.
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
