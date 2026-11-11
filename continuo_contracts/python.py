"""The Python node, its code projection and the SQL type canonicaliser.

A Python node is a table that a script, a CSV file or an API call fills. Its code projection is
the part of its declaration that decides what the node computes, written in one fixed shape: the
twelve keys of `fingerprints.projection_keys()`, in that order. `projection_hash` is the lowercase
hex SHA-256 of the RFC 8785 canonical JSON of the projection. It is the config part of the
node's `content_hash`, and the projection itself is what continuo's code bundle stores as the
node's `raw_code`.

The projection removes every spelling that does not change what the node computes, so two
producers that declare the same node write the same bytes:

- a stored `python-model` kind is the `python-node` kind;
- an SQL type is written the way `canonical_sql_type` writes it (`int` is `INTEGER`);
- the SQL of a read has LF line endings and no ASCII whitespace around it;
- the assets are sorted;
- the primary key is kept only on an `upsert` node.

A node that the projection cannot write the same way everywhere raises `ProjectionError`.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Final, Self

from continuo_contracts.canonical import MAX_DEPTH, canonical_json, normalise_text
from continuo_contracts.errors import CanonicalJsonError, ProjectionError
from continuo_contracts.fingerprints import projection_keys
from continuo_contracts.hashing import sha256_hex

PYTHON_KINDS: Final = ("python-node", "python-csv", "python-api")

# Spellings of a kind that a stored artifact carries, each with the kind it stands for.
KIND_ALIASES: Final = {"python-model": "python-node"}

MODE_TYPES: Final = ("replace", "append", "upsert")
EXTRA_COLUMNS_POLICIES: Final = ("raise", "warn")

_NUMERIC_MAX_PRECISION: Final = 38

# The types a Python node can declare. Matching is ASCII only, so that a letter such as the
# dotless i, which upper-cases to `I`, is refused instead of read as `INT`. `\Z` and not `$`
# anchors the end: `$` would also match before a trailing line feed.
_SQL_TYPE = re.compile(
    r"^(BIGINT|INT|INTEGER|DOUBLE PRECISION|TEXT|TIMESTAMP|DATE|BOOLEAN"
    r"|(NUMERIC|DECIMAL)\(\d+,\s*\d+\)|(VARCHAR|CHAR)\(\d+\))\Z",
    re.IGNORECASE | re.ASCII,
)
_PARAMETERISED_SQL_TYPE = re.compile(
    r"(NUMERIC|DECIMAL|VARCHAR|CHAR)\((\d+)(?:,\s*(\d+))?\)", re.ASCII
)


@dataclass(frozen=True)
class PythonColumn:
    """An output column of a Python node: its name, its SQL type as declared and whether it
    accepts NULL."""

    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True)
class PythonNode:
    """The declaration of a Python node, as the fields of one entry of a node's wire form.

    The fields are not checked when the node is built: `code_projection` refuses a node that it
    cannot project.
    """

    schema: str
    table: str
    kind: str = "python-node"
    script: str = ""
    reads: Mapping[str, str] = field(default_factory=dict)
    output_columns: Sequence[PythonColumn] = ()
    extra_columns: str = "raise"
    config: Mapping[str, Any] = field(default_factory=dict)
    secret_ref: str = ""
    mode_type: str = "replace"
    primary_key: Sequence[str] = ()
    assets: Sequence[str] = ()

    @property
    def relation(self) -> str:
        """The node's table as `<schema>.<table>`."""
        return f"{self.schema}.{self.table}"

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> Self:
        """Build a node from a wire entry.

        The entry may carry any other key, which is ignored, and may leave a field out, which
        takes its default. `output_columns` holds mappings of `name`, `type` and, optionally,
        `nullable`. `schema` and `table` are required. A missing required field, and a field
        whose value is not the kind of container the field holds, raise `ProjectionError`.
        """
        if not isinstance(entry, Mapping):
            raise ProjectionError(
                f"a python node entry must be a mapping, not {type(entry).__name__}"
            )
        for required in ("schema", "table"):
            if required not in entry:
                raise ProjectionError(f"a python node entry has no {required!r} field")
        values = {item.name: entry[item.name] for item in fields(cls) if item.name in entry}
        try:
            _convert_containers(values)
        except ProjectionError as error:
            raise ProjectionError(f"{entry['schema']}.{entry['table']}: {error}") from None
        return cls(**values)


def canonical_sql_type(raw: str) -> str:
    """Return the canonical spelling of the SQL type `raw`.

    Outer whitespace is stripped and case is ignored. The whitespace that is stripped is exactly
    these 29 code points and no other character: U+0009 to U+000D, U+001C to U+001F, U+0020,
    U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F and U+3000. They
    are the code points that `str.strip()` removes, the same on Python 3.12, 3.13 and 3.14, so
    a zero width space (U+200B) around a type is refused. `normalise_text`, by contrast, removes
    only the six ASCII whitespace characters. After the comma of `NUMERIC(p, s)` any run of ASCII
    whitespace is accepted.

    The supported types are `BIGINT`, `INT`, `INTEGER`, `DOUBLE PRECISION` (with one space),
    `TEXT`, `TIMESTAMP`, `DATE`, `BOOLEAN`, `NUMERIC(p,s)`, `DECIMAL(p,s)`, `VARCHAR(n)` and
    `CHAR(n)`. `INT` becomes `INTEGER` and `DECIMAL` becomes `NUMERIC`. Precision, scale and
    length are written as plain integers, with no leading zeros and no space. `NUMERIC` needs a
    precision of 1 to 38 and a scale of 0 up to the precision.

    Anything else raises `ProjectionError`.
    """
    if not isinstance(raw, str):
        raise ProjectionError(f"unsupported or invalid SQL type: {raw!r}")
    text = raw.strip()
    if not _SQL_TYPE.match(text):
        raise ProjectionError(f"unsupported or invalid SQL type: {raw!r}")
    spelled = text.upper()
    if spelled == "INT":
        return "INTEGER"
    parameters = _PARAMETERISED_SQL_TYPE.fullmatch(spelled)
    if parameters is None:
        return spelled
    name, first, second = parameters.groups()
    if name in ("VARCHAR", "CHAR"):
        return f"{name}({_plain_integer(first)})"
    precision, scale = _plain_integer(first), _plain_integer(second)
    # A number of more than two digits is above 38, and checking the length first keeps a very
    # long digit string from reaching `int`, which refuses it.
    if len(precision) > 2 or len(scale) > 2 or not _numeric_in_range(int(precision), int(scale)):
        raise ProjectionError(
            f"NUMERIC({precision},{scale}) is out of range: the precision is 1 to "
            f"{_NUMERIC_MAX_PRECISION} and the scale is 0 up to the precision"
        )
    return f"NUMERIC({precision},{scale})"


def code_projection(node: PythonNode) -> dict[str, Any]:
    """Return the code projection of `node`: one entry for each key of
    `fingerprints.projection_keys()`, in that order.

    `config` is copied with every mapping as a `dict` and every list or tuple as a `list`, down to
    level `canonical.MAX_DEPTH` (the config itself is level 1), so changing the result does not
    change `node` down to that level. A value that is none of these is left in place, and so is a
    container on a deeper level, which `projection_hash` refuses. Assets are sorted by Unicode
    code point, which is the order of their UTF-8 bytes (not UTF-16 order).

    Raises `ProjectionError`, whose message starts with `<schema>.<table>: `, for a node it cannot
    project: a field that is not of its type or that holds a value outside its set, a field that
    its kind does not allow, an SQL type that is not supported, or a projection key that has no
    rule.
    """
    try:
        return _project(node)
    except ProjectionError as error:
        raise ProjectionError(f"{node.relation}: {error}") from None


def projection_hash(node: PythonNode) -> str:
    """Return the lowercase hex SHA-256 of the canonical JSON of `code_projection(node)`.

    This is the Python config part of the node's `content_hash`. Raises `ProjectionError`, whose
    message starts with `<schema>.<table>: `, for a node that `code_projection` refuses and for a
    value of its `config` that canonical JSON cannot write, in which case the reason starts with
    the JSON path of that value. The projection is a mapping on level 1 of the canonical JSON and
    `config` is its value on level 2, so a `config` is refused when it nests containers deeper
    than `canonical.MAX_DEPTH - 1` levels, counting the config itself as level 1.
    """
    projection = code_projection(node)
    try:
        return sha256_hex(canonical_json(projection))
    except CanonicalJsonError as error:
        raise ProjectionError(f"{node.relation}: {error}") from error


def _convert_containers(values: dict[str, Any]) -> None:
    for name in ("reads", "config"):
        if name in values:
            values[name] = dict(_mapping(values[name], name))
    for name in ("primary_key", "assets"):
        if name in values:
            values[name] = tuple(_list(values[name], name))
    if "output_columns" in values:
        columns = _list(values["output_columns"], "output_columns")
        values["output_columns"] = tuple(_column(index, item) for index, item in enumerate(columns))


def _column(index: int, item: object) -> PythonColumn:
    where = f"output_columns[{index}]"
    column = _mapping(item, where)
    for required in ("name", "type"):
        if required not in column:
            raise ProjectionError(f"{where} has no {required!r} field")
    return PythonColumn(column["name"], column["type"], column.get("nullable", True))


def _mapping(value: object, what: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise ProjectionError(f"{what} must be a mapping, not {type(value).__name__}")
    return value


def _list(value: object, what: str) -> Sequence[Any]:
    if not isinstance(value, list | tuple):
        raise ProjectionError(f"{what} must be a list, not {type(value).__name__}")
    return value


def _plain_integer(digits: str) -> str:
    return digits.lstrip("0") or "0"


def _numeric_in_range(precision: int, scale: int) -> bool:
    return 1 <= precision <= _NUMERIC_MAX_PRECISION and 0 <= scale <= precision


# A rule computes one key of the projection from the node and its kind, which is already
# resolved and checked.
_Rule = Callable[[PythonNode, str], Any]


def _project(node: PythonNode) -> dict[str, Any]:
    kind = _kind(node)
    projection: dict[str, Any] = {}
    for key in projection_keys():
        rule = _RULES.get(key)
        if rule is None:
            raise ProjectionError(f"projection key {key} has no rule")
        projection[key] = rule(node, kind)
    return projection


def _kind(node: PythonNode) -> str:
    declared = _text(node.kind, "kind")
    kind = KIND_ALIASES.get(declared, declared)
    return _one_of(kind, PYTHON_KINDS, "kind")


def _text(value: object, what: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ProjectionError(f"{what} must be a string, not {type(value).__name__}")
    if not value and not allow_empty:
        raise ProjectionError(f"{what} must not be empty")
    return value


def _one_of(value: object, allowed: Sequence[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ProjectionError(f"{what} {value!r} is not one of {', '.join(allowed)}")
    return value


def _strings(value: object, what: str) -> list[str]:
    items = _list(value, what)
    for index, item in enumerate(items):
        _text(item, f"{what}[{index}]")
    return list(items)


def _schema(node: PythonNode, kind: str) -> str:
    return _text(node.schema, "schema")


def _table(node: PythonNode, kind: str) -> str:
    return _text(node.table, "table")


def _kind_rule(node: PythonNode, kind: str) -> str:
    return kind


def _script(node: PythonNode, kind: str) -> str:
    if kind == "python-csv":
        if node.script != "":
            raise ProjectionError("script must be empty on a python-csv node")
        return ""
    return _text(node.script, "script", allow_empty=True)


def _reads(node: PythonNode, kind: str) -> dict[str, str]:
    reads = _mapping(node.reads, "reads")
    for name, source in reads.items():
        _text(name, "a name in reads", allow_empty=True)
        _text(source, f"reads[{name!r}]", allow_empty=True)
    if kind == "python-node":
        return {name: normalise_text(source) for name, source in reads.items()}
    if kind == "python-csv":
        if set(reads) != {"csv"}:
            raise ProjectionError("reads must hold exactly the key 'csv' on a python-csv node")
        return dict(reads)
    if reads:
        raise ProjectionError("reads must be empty on a python-api node")
    return {}


def _output_columns(node: PythonNode, kind: str) -> list[dict[str, Any]]:
    columns = []
    for index, column in enumerate(_list(node.output_columns, "output_columns")):
        where = f"output_columns[{index}]"
        if not isinstance(column, PythonColumn):
            raise ProjectionError(f"{where} must be a PythonColumn, not {type(column).__name__}")
        name = _text(column.name, f"{where}.name")
        if not isinstance(column.nullable, bool):
            raise ProjectionError(
                f"{where} ({name}): nullable must be a bool, not {type(column.nullable).__name__}"
            )
        try:
            sql_type = canonical_sql_type(column.type)
        except ProjectionError as error:
            raise ProjectionError(f"{where} ({name}): {error}") from None
        columns.append({"name": name, "type": sql_type, "nullable": column.nullable})
    return columns


def _extra_columns(node: PythonNode, kind: str) -> str:
    return _one_of(node.extra_columns, EXTRA_COLUMNS_POLICIES, "extra_columns")


def _config(node: PythonNode, kind: str) -> dict[str, Any]:
    config = _mapping(node.config, "config")
    for key in config:
        _text(key, "a key of config", allow_empty=True)
    return _copy_mapping(config, {}, 1)


def _json_copy(value: object, on_path: dict[int, object], depth: int) -> object:
    """Copy `value`, a container on level `depth` of `config`, by its JSON shape: a mapping
    becomes a `dict`, a list or tuple becomes a `list`, and anything else is returned as it is,
    for `canonical_json` to accept or to refuse with the path of the value.

    `on_path` maps each container that is being copied, from the root down to `value`, to its
    copy. A container that contains itself is copied as a copy that contains itself, so the copy
    ends and `canonical_json` names the path where the cycle closes. A container on a level deeper
    than `MAX_DEPTH` is returned as it is and not copied, so the copy never recurses deeper than
    the limit and `canonical_json` refuses the nesting with the path of the container.
    """
    if not isinstance(value, Mapping | list | tuple):
        return value
    if id(value) in on_path:
        return on_path[id(value)]
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, Mapping):
        return _copy_mapping(value, on_path, depth)
    return _copy_list(value, on_path, depth)


def _copy_mapping(
    source: Mapping[Any, Any], on_path: dict[int, object], depth: int
) -> dict[Any, Any]:
    # A key is kept as it is, so a key that is not a string reaches `canonical_json`.
    copied: dict[Any, Any] = {}
    on_path[id(source)] = copied
    for key, item in source.items():
        copied[key] = _json_copy(item, on_path, depth + 1)
    del on_path[id(source)]
    return copied


def _copy_list(source: Sequence[Any], on_path: dict[int, object], depth: int) -> list[Any]:
    copied: list[Any] = []
    on_path[id(source)] = copied
    for item in source:
        copied.append(_json_copy(item, on_path, depth + 1))
    del on_path[id(source)]
    return copied


def _secret_ref(node: PythonNode, kind: str) -> str:
    if kind == "python-api":
        return _text(node.secret_ref, "secret_ref", allow_empty=True)
    if node.secret_ref != "":
        raise ProjectionError(f"secret_ref must be empty on a {kind} node")
    return ""


def _mode_type(node: PythonNode, kind: str) -> str:
    mode = _one_of(node.mode_type, MODE_TYPES, "mode_type")
    if kind == "python-csv" and mode != "replace":
        raise ProjectionError(f"mode_type must be replace on a python-csv node, not {mode}")
    return mode


def _primary_key(node: PythonNode, kind: str) -> list[str]:
    # On a replace or append node the key only describes the table and stays out.
    if _mode_type(node, kind) != "upsert":
        return []
    return _strings(node.primary_key, "primary_key")


def _assets(node: PythonNode, kind: str) -> list[str]:
    if kind == "python-csv":
        if _list(node.assets, "assets"):
            raise ProjectionError("assets must be empty on a python-csv node")
        return []
    assets = _strings(node.assets, "assets")
    if len(set(assets)) != len(assets):
        raise ProjectionError("assets must not list a path twice")
    # Sorted by Unicode code point, which is the order of the UTF-8 bytes.
    return sorted(assets)


_RULES: Final[dict[str, _Rule]] = {
    "schema": _schema,
    "table": _table,
    "kind": _kind_rule,
    "script": _script,
    "reads": _reads,
    "output_columns": _output_columns,
    "extra_columns": _extra_columns,
    "config": _config,
    "secret_ref": _secret_ref,
    "mode_type": _mode_type,
    "primary_key": _primary_key,
    "assets": _assets,
}
