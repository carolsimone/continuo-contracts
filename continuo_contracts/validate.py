"""Check the ODCS contract of a Python node and list every problem it has.

A Python node is declared by one ODCS contract file. Checking it takes two stages. The first
stage, `schema_issues`, checks the document against the ODCS v3.2.0 JSON schema, after a scan
that refuses a column that nests, on which the JSON schema check would take exponential time.
The second stage, `rule_issues`, checks continuo's own rules on a document that the first stage
accepted: the envelope, the identity of the table, the file name, the team, the fields a Python
contract may not carry, the custom properties of the `continuo` vendor, the rules of each kind
of node, the columns and the key of an upsert node. `validate_python_contract` runs the first
stage and, when it finds nothing, the second.

Every problem is an `Issue` with the path of the place it is found at, one of the closed reason
codes of `REASONS` and a sentence for a person. The issues come in path order, so the same
document always gives the same list.
"""

import ast
import functools
import re

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Final, cast

from jsonschema import Draft201909Validator
from jsonschema.exceptions import ValidationError

from continuo_contracts import schema
from continuo_contracts.canonical import (
    _ascii_lower,
    _has_control,
    _has_whitespace,
)
from continuo_contracts.contract import (
    _MAX_LENGTH,
    _SHAPES,
    _STRING_TYPES,
    _length,
)
from continuo_contracts.errors import ProjectionError
from continuo_contracts.python import (
    EXTRA_COLUMNS_POLICIES,
    KIND_ALIASES,
    MODE_TYPES,
    PYTHON_KINDS,
    _id_schema,
    _key_position,
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

# The level of the `customProperties` list of an object, by the ODCS type of the object that
# `contract` walks: the contract, its schema object, a quality rule, or any other object.
_LEVELS: Final = {"root": "root", "schema_object": "schema", "quality": "quality"}

# The fields a column may not carry: the column of a Python node has a name, a type and a
# nullability, and nothing nested.
_NESTED_COLUMN_FIELDS: Final = ("items", "map", "properties")

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
        [
            "filePath",
            "materialization",
            "contractEnforced",
            "access",
            "group",
            "deprecationDate",
            "freshnessBuildAfter",
            "snapshot",
            "quoteColumns",
        ],
        "it describes how dbt builds or governs a node, and nothing reads it on a Python node",
    ),
}

_CRITICALITIES: Final = ("REGULATORY", "CORE", "SECONDARY")
# The longest schedule name: the width of the schedule name of a run in continuo's run store.
_SCHEDULE_MAX: Final = 50
# A Windows drive letter at the start of a path, as in `C:/x` or `c:x`.
_DRIVE: Final = re.compile(r"[A-Za-z]:")
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
    """What a kind of node needs and refuses.

    `required` lists the properties it must write and `refused` maps each property
    it may not write to the reason.
    """

    required: tuple[str, ...]
    refused: Mapping[str, str]


_CSV_ONLY: Final = "only a python-csv node loads a CSV file"
_READS_ONLY: Final = (
    "it reads no table, only a python-node reads the tables its queries name"
)
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
    r"Unevaluated properties are not allowed \((.*) (?:was|were) unexpected\)\Z",
    re.DOTALL,
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
        """Return the path and the message of the issue."""
        return f"{self.path}: {self.message}"


def _issue(path: str, reason: str, message: str) -> Issue:
    """Build an issue whose message is cut when it is long.

    One huge value of a contract cannot make a huge message.
    """
    if len(message) > _MAX_MESSAGE:
        message = message[: _MAX_MESSAGE - 3] + "..."
    return Issue(path, reason, message)


def _path_key(path: str) -> list[tuple[int, int, str]]:
    """Return the sort key of a path.

    The root comes first, a key sorts by its text and an array index by its
    number, so `a[4]` comes before `a[10]`.
    """
    if path == "$":
        return []
    return [
        (0, int(index), "") if index else (1, 0, name)
        for index, name in _PATH_TOKEN.findall(path)
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


def _is_knock_on(
    error: ValidationError, failing: set[tuple[str | int, ...]]
) -> bool:
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
    except ValueError, SyntaxError:
        return False
    here = tuple(error.absolute_path)
    return bool(keys) and all(
        any(path[: len(here) + 1] == (*here, key) for path in failing)
        for key in keys
    )


def _nested_field_issues(
    where: str, column: Mapping[str, Any]
) -> Iterator[Issue]:
    """Yield a `refused_field` issue for each nested field of `column`.

    `column` is at `where`. A nested field is `items`, `map` or `properties`,
    since the column of a Python node nests nothing.
    """
    shown = _shown(column.get("name"))
    for field in _NESTED_COLUMN_FIELDS:
        if field in column:
            yield _issue(
                f"{where}.{field}",
                "refused_field",
                f"column {shown} must not carry `{field}`, the column of a Python node has "
                "a name, type and nullability and nothing nested",
            )


def _nesting_issues(doc: object) -> list[Issue]:
    """Return the `refused_field` issues of the nested columns of `doc`.

    A nested column is a column of a schema object of `doc` that has `items`, a
    `map` or `properties`. The issues come in path order.

    The ODCS schema describes a nested column through itself, and the JSON schema check of such a
    column takes time that grows exponentially with the depth of the nesting. This scan reads
    only the fields of each column, so it takes time in proportion to the number of columns. A
    `doc` that is not a mapping, a `schema` that is not a list, a schema object that is not a
    mapping or has no list of columns, and a column that is not a mapping are not scanned, and the
    JSON schema reports them.
    """
    if not isinstance(doc, Mapping):
        return []
    objects = doc.get("schema")
    if not isinstance(objects, list):
        return []
    issues: list[Issue] = []
    for position, obj in enumerate(objects):
        columns = obj.get("properties") if isinstance(obj, Mapping) else None
        if not isinstance(columns, list):
            continue
        for index, column in enumerate(columns):
            if isinstance(column, Mapping):
                issues.extend(
                    _nested_field_issues(
                        f"schema[{position}].properties[{index}]", column
                    )
                )
    return _ordered(issues)


def schema_issues(doc: object) -> list[Issue]:
    """Return the issues of `doc` against the ODCS v3.2.0 JSON schema.

    The check starts with a scan of the columns of every schema object. When a column carries
    `items`, a `map` or `properties`, the result is a `refused_field` issue for each such field,
    the issue the rules give for it, and the JSON schema is not run: the column of a Python
    contract nests nothing, and the JSON schema check of a nested column takes time that grows
    exponentially with the depth of the nesting. The other errors of such a document show once the
    nesting is gone.

    Otherwise the result is an `odcs_schema` issue for each error of `doc` against the JSON
    schema. `doc` is whatever `yaml_strict.load` returned, so it may be any value: one that is not
    a mapping gives a single issue at `$`. Two errors with the same path and message give one
    issue, and so does an "Unevaluated properties" error that names only properties with an error
    of their own, because the error of the property says what is wrong. The issues are sorted by
    path, with array indexes in numeric order, and then by message. A message that is longer
    than 300 characters is cut and ends in `...`.
    """
    nesting = _nesting_issues(doc)
    if nesting:
        return nesting
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


def _name_fault(
    value: object, *, inner_whitespace: bool = True, separators: bool = True
) -> str | None:
    """Return what is wrong with `value` as a name, or `None` when it is fine.

    The fault is the end of a sentence that starts with what the name is of, such as `must not be
    blank`. Every name is a string that is not blank and has no whitespace at either end and no
    control character (U+0000 to U+001F and U+007F to U+009F). `inner_whitespace=False` also
    refuses whitespace anywhere in it, and `separators=False` refuses a `.` and a `/`, which
    separate the parts of an id or a path. Whitespace is what `str.isspace` and `str.strip` read
    as whitespace, the same 29 code points on every supported Python.
    """
    if not isinstance(value, str):
        return "must be a string"
    if not value.strip():
        return "must not be blank"
    if value != value.strip():
        return "must not start or end with whitespace"
    if not inner_whitespace and _has_whitespace(value):
        return "must not contain whitespace"
    if _has_control(value):
        return "must not contain a control character"
    if not separators and "." in value:
        return "must not contain a dot, which separates the schema from the table in the id"
    if not separators and "/" in value:
        return "must not contain a slash"
    return None


def _identifier_fault(value: object) -> str | None:
    """Return what is wrong with `value` as the name of a table or a schema.

    A name has no whitespace, dot or slash anywhere. The result is `None` when
    `value` is one.
    """
    return _name_fault(value, inner_whitespace=False, separators=False)


def _path_fault(value: object) -> str | None:
    r"""Return what is wrong with `value` as the path of a file inside the project, or `None`.

    The path is a name, POSIX and relative, written in its normalised form: its parts are
    separated by `/` and not `\`, it does not start with `/`, `~` or a drive letter such as
    `C:`, and no part is empty, `.` or `..`. Two paths that name one file are then written alike.
    """
    fault = _name_fault(value)
    if fault is not None or not isinstance(value, str):
        return fault
    if "\\" in value:
        return "must separate its parts with / and not \\"
    if value.startswith("/"):
        return "must be relative, not start with /"
    if value.startswith("~"):
        return "must not start with ~, which names a home folder"
    if _DRIVE.match(value):
        return "must not start with a drive letter"
    if any(part in ("", ".", "..") for part in value.split("/")):
        return "must not have an empty, . or .. part"
    return None


@dataclass(frozen=True)
class _Contract:
    """A document that the JSON schema accepted.

    It holds what the rules read of the document besides its fields.
    """

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
        """The name of the table, or `None` when the contract has none.

        The contract has none when it has no single schema object or that object
        has no name that a table can have, so that nothing is compared with it.
        """
        obj = self.table_object
        name = obj.get("name") if obj is not None else None
        return (
            name
            if isinstance(name, str) and _identifier_fault(name) is None
            else None
        )

    @property
    def written_id_schema(self) -> str | None:
        """The schema part of the id as `python._id_schema` reads it.

        It is whatever that part holds, or `None` when there is no table or the id
        does not end in a dot and the table.
        """
        table = self.table
        return None if table is None else _id_schema(self.doc.get("id"), table)

    @property
    def id_schema(self) -> str | None:
        """The schema part of the id, or `None` when the id has no usable one.

        The id has none when it does not end in a dot and the table, or when its
        schema part is not a name that a schema can have.
        """
        part = self.written_id_schema
        return (
            part if part is not None and _identifier_fault(part) is None else None
        )

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
                if (
                    isinstance(name, str)
                    and "value" in item
                    and name not in found
                ):
                    found[name] = _Prop(
                        f"customProperties[{index}]", item["value"]
                    )
        return found

    @cached_property
    def kind(self) -> str | None:
        """The kind of the node, with an old name read as the kind it stands for.

        It is `None` when the `kind` property is not one of `PYTHON_KINDS`. A
        contract with no `kind` is a python-node.
        """
        prop = self.properties.get("kind")
        declared = prop.value if prop is not None else "python-node"
        if not isinstance(declared, str):
            return None
        kind = KIND_ALIASES.get(declared, declared)
        return kind if kind in PYTHON_KINDS else None

    @cached_property
    def mode_type(self) -> str | None:
        """The `modeType` of the node, `replace` when the contract writes none.

        It is `None` when the written one is not one of `MODE_TYPES`.
        """
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
    if isinstance(identifier, str) and identifier != _ascii_lower(identifier):
        yield _issue(
            "id",
            "identity",
            f"the id must be in lower case, as {_ascii_lower(identifier)!r}",
        )
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
    fault = _identifier_fault(obj.get("name"))
    if fault is not None:
        yield _issue(
            "schema[0].name", "identity", f"the name of the table {fault}"
        )
    table = contract.table
    if table is None:
        return
    if isinstance(identifier, str) and contract.id_schema is None:
        part = contract.written_id_schema
        why = (
            ""
            if part is None
            else f": its schema part {_shown(part)} {_identifier_fault(part)}"
        )
        yield _issue(
            "id",
            "identity",
            f"the id must be a schema name, a dot and the table, in lower case, as in "
            f"'analytics.{_ascii_lower(table)}'{why}",
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
        yield _issue(
            "team.name",
            "missing_field",
            "a Python contract names its team in team.name",
        )


def _refused_fields(contract: _Contract) -> Iterator[Issue]:
    for field, why in _REFUSED_FIELDS.items():
        if field in contract.doc:
            yield _issue(field, "refused_field", why)


def _custom_property_lists(
    node: object, path: str, shape: str
) -> Iterator[tuple[str, str, list[Any]]]:
    """Yield the path, the level and the items of every `customProperties` list.

    The lists are those under `node`, an object or a list of objects of the ODCS
    type `shape`.

    The level says which object the list belongs to: `root` (the contract), `schema` (a schema
    object), `quality` (a quality rule) or `other`. The object types and their free-form values
    are those of the canonical form in `contract`, so both read the same keys as data. The items
    of a `customProperties` list are not walked into, and neither is a free-form value.
    """
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _custom_property_lists(item, f"{path}[{index}]", shape)
    elif isinstance(node, Mapping):
        spec = _SHAPES[shape]
        for key, child in node.items():
            if key in spec.free_form:
                continue
            child_path = f"{path}.{key}" if path else key
            if key == "customProperties":
                if isinstance(child, list):
                    yield child_path, _LEVELS.get(shape, "other"), child
            else:
                yield from _custom_property_lists(
                    child, child_path, spec.child(key)
                )


def _placement_fault(name: str, level: str) -> tuple[str, str] | None:
    """Return why a `continuo` property may not sit on `level`.

    `level` is a level of a Python contract. The result is the reason and the
    message, or `None` when it may sit there.
    """
    prop = PROPERTIES.get(name)
    if prop is None:
        return (
            "unknown_property",
            f"`{name}` is not a continuo property that a Python contract knows, "
            "check the spelling or leave it out",
        )
    if prop.python == "filled":
        why = _WHY_FILLED.get(
            name,
            f"continuo fills `{name}` itself, a Python contract does not write it",
        )
        return "refused_property", why
    if prop.python == "refused":
        why = _WHY_REFUSED.get(name, _NOT_READ_BY_PYTHON)
        return (
            "refused_property",
            f"a Python contract does not carry `{name}`: {why}",
        )
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
            if name == "service":
                yield from _service_issues(
                    item.get("value"), where, contract.service
                )


def _service_issues(
    value: object, where: str, service: str | None
) -> Iterator[Issue]:
    """Yield the issue of the `service` property at `where`.

    Its value is `value`, and `service` is the service of the release, or `None`
    when it is not known. A `value` issue says the value is not a string with
    text, whether or not the service is known, and a `service` issue says it names
    another service than the release's.
    """
    if not _is_text(value):
        yield _issue(
            where,
            "value",
            f"`service` is {_shown(value)}, it must be the name of a service, a string with text",
        )
    elif service is not None and value != service:
        yield _issue(
            where,
            "service",
            f"`service` is {_shown(value)}, the service of the release is {service!r}",
        )


def _shown(value: object) -> str:
    """Write `value` for a message, cut when it is long.

    One huge value of a contract cannot push the rest of the sentence out of the
    message.
    """
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
            f"`timeoutMinutes` is {_shown(value)}, it must be an integer number of minutes from 1 "
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
    fault = _name_fault(value, inner_whitespace=False)
    if fault is None and isinstance(value, str) and len(value) > _SCHEDULE_MAX:
        fault = f"must be at most {_SCHEDULE_MAX} characters long"
    if fault is not None:
        yield f"`schedule` is {_shown(value)}, the name of a schedule {fault}"


def _check_script(value: object) -> Iterator[str]:
    fault = _path_fault(value)
    if fault is not None:
        yield f"`script` is {_shown(value)}, the path of the script inside the project {fault}"


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
        or (
            _S3_OBJECT.fullmatch(value) is None
            and _HTTPS_ADDRESS.fullmatch(value) is None
        )
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
        fault = _name_fault(name)
        if fault is not None:
            yield f"`readQueries` has a query named {_shown(name)}, the name of a query {fault}"
        if not _is_text(query):
            yield (
                f"`readQueries` has the query {_shown(name)} that is {_shown(query)}, a query "
                "must be a string with text"
            )


def _check_assets(value: object) -> Iterator[str]:
    if not isinstance(value, list):
        yield f"`assets` is {_shown(value)}, it must be a list of paths inside the project"
        return
    paths = []
    for asset in value:
        fault = _path_fault(asset)
        if fault is None:
            paths.append(asset)
        else:
            yield f"`assets` lists {_shown(asset)}, the path of a file inside the project {fault}"
    # A path that passed is written in its normalised form, so two that name one file are equal.
    counts = Counter(paths)
    for asset in sorted(asset for asset, count in counts.items() if count > 1):
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
            yield _issue(
                prop.path,
                "kind_rule",
                f"a {kind} node does not take `{name}`: {why}",
            )
    mode = contract.properties.get("modeType")
    if (
        kind == "python-csv"
        and mode is not None
        and contract.mode_type not in (None, "replace")
    ):
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
            canonical = canonical_sql_type(raw)
        except ProjectionError as error:
            # A refusal for a range says the range, and any other refusal is the plain sentence.
            detail = f": {error}" if "out of range" in str(error) else ""
        else:
            if (
                canonical.partition("(")[0] not in _STRING_TYPES
                or _length(canonical) is not None
            ):
                return None
            detail = f": the length is 1 to {_MAX_LENGTH}"
    return _issue(
        path,
        "column_type",
        f"{_shown(raw)} is not a type continuo accepts{detail}",
    )


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
        fault = _name_fault(name) if isinstance(name, str) else None
        if fault is not None:
            yield _issue(
                f"{where}.name", "value", f"the name of a column {fault}"
            )
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
            issue = _column_type_issue(
                column["physicalType"], f"{where}.physicalType"
            )
            if issue is not None:
                yield issue
        yield from _nested_field_issues(where, column)
        if "logicalTypeOptions" in column:
            yield _issue(
                f"{where}.logicalTypeOptions",
                "refused_field",
                f"column {shown} must not carry `logicalTypeOptions`, continuo derives the logical "
                "type and its options from physicalType",
            )
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


def _quality_implementations(contract: _Contract) -> Iterator[Issue]:
    obj = contract.table_object
    if obj is None:
        return
    places = [("schema[0]", obj)] + [
        (f"schema[0].properties[{index}]", column)
        for index, column in enumerate(contract.columns)
        if isinstance(column, Mapping)
    ]
    for where, owner in places:
        rules = owner.get("quality")
        if not isinstance(rules, list):
            continue
        for index, rule in enumerate(rules):
            if not isinstance(rule, Mapping) or "implementation" not in rule:
                continue
            implementation = rule["implementation"]
            if implementation == "" or implementation == {}:
                yield _issue(
                    f"{where}.quality[{index}].implementation",
                    "value",
                    "the implementation of a custom quality rule must not be empty, it is the "
                    "check that the engine runs",
                )


def _upsert_key(contract: _Contract) -> Iterator[Issue]:
    # A python-csv node that upserts already gets the kind's issue, and no second one for its key.
    if (
        contract.mode_type != "upsert"
        or contract.kind == "python-csv"
        or not contract.columns
    ):
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
        if len(keys) == 1:
            continue
        number = _key_position(column.get("primaryKeyPosition", -1))
        if number is None:
            has = (
                f"primaryKeyPosition {_shown(column['primaryKeyPosition'])}"
                if "primaryKeyPosition" in column
                else "no primaryKeyPosition"
            )
            yield _issue(
                f"{where}.primaryKeyPosition",
                "primary_key",
                f"key column {name} has {has}, a key of several columns gives each column a "
                "whole number from 1",
            )
        elif number in taken:
            yield _issue(
                f"{where}.primaryKeyPosition",
                "primary_key",
                f"primaryKeyPosition {number} is already the position of key column "
                f"{taken[number]}, each key column has its own position",
            )
        else:
            taken[number] = name


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
    _quality_implementations,
    _upsert_key,
)


def rule_issues(
    doc: Mapping[str, Any], *, path: str, service: str | None
) -> list[Issue]:
    """Return the issues that continuo's rules find in `doc`, without the JSON schema check.

    `doc` is a document that `schema_issues` accepted, and `path` is the repository path of its
    file, of which only the base name counts. `service` is the service of the release that
    carries the contract, or `None` when it is not known, which skips the check of the `service`
    property. The issues come in path order, then in message order.

    A name, wherever the rules ask for one, is a string that is not blank and has no whitespace at
    either end and no control character. The rules are the envelope (`apiVersion` is `v3.2.0`),
    the identity of the table (one schema object, a table and a schema part of the id that are
    names with no whitespace, dot or slash, with the id in ASCII lower case and the id, the name
    and the physical name agreeing with the table), the file name, `team.name`, the root fields a
    Python contract may not carry, and the custom properties of the `continuo` vendor: each must
    be a property that the vocabulary knows, that a Python contract carries and that sits where
    it is written, and it may be written once in a list. A `service` property is a string with
    text and, when `service` is given, names it.

    The rules of the node come after them. The contract writes `schedule` and `criticality`, and
    each property it writes has a value of its own kind: `schedule` is a name with no whitespace,
    at most 50 characters long, the names of `readQueries` are names, and `script` and each entry
    of `assets` are normalised POSIX paths inside the project. The `kind` (a python-node when it
    is not written, `python-model` read as python-node) decides which of `script`, `readQueries`,
    `csvSource`, `secretRef` and `assets` the contract must write, may write or may not write,
    and a python-csv node replaces its table. A kind that is not a Python kind gives one issue
    and skips these per-kind rules. The table lists at least one column, each with a name that is
    used once and a `physicalType` that `canonical_sql_type` accepts with a length from 1 to
    2**53 - 1, and no column has a `physicalName` other than its name, is partitioned, nests
    `properties`, `items` or a `map`, or writes `logicalTypeOptions`. A custom quality rule has an
    `implementation` that is not empty. The key of an `upsert` node is the
    columns with `primaryKey: true`: there is one, each is `required: true` and not
    `DOUBLE PRECISION`, and each column of a key of several has its own whole-number
    `primaryKeyPosition` from 1. A python-csv node is not checked for its key.
    """
    contract = _Contract(doc=doc, file_name=_base_name(path), service=service)
    return _ordered(issue for rule in _RULES for issue in rule(contract))


def validate_python_contract(
    doc: object, *, path: str, service: str | None
) -> list[Issue]:
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
