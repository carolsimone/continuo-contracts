"""The dbt nodes that get a contract, how a node is identified, and the tests of a node.

continuo reads a dbt `manifest.json` and keeps the models, seeds and snapshots in it
(`RESOURCE_TYPES`), leaving out the ones that no gate lets through. This module holds those
gates and the names a kept node gets, so a producer's CI and continuo's parser give the same
answer for the same node.

`skip_reason` runs the gates in a fixed order and returns the reason of the first one a node
meets, or `None` for a node that is kept. The gates are, in order:

- `no_owner`: the node has no `config.meta.owner`, or an empty one;
- `local_stub`: the node has the tag `local_stub`;
- `installed_package`: the node has no tag and its service is not the project's. dbt applies a
  project's top-level `+meta` to the nodes of its installed packages too, so such a node can
  carry the project's owner and still not be the project's to schedule.

A node's service is the first entry of its `fqn` with `_` written `-`, and the project is the
manifest's `metadata.project_name` written the same way. With no project, no node is left out as
`installed_package`.

A node's table is its `name`, or `<name>.v<version>` for a versioned model, and its id is
`<schema>.<table>` in lower case. A node a gate keeps runs on the schedule of its first tag, and
an untagged seed runs on the `seed` schedule. An untagged model or snapshot has the empty
schedule: it is a dormant node that runs only by hand.

`tests_by_node` joins the tests of a manifest to the nodes they test, the way dbt counts them.

`generate` writes the ODCS contract of a kept node from the node and its tests: the envelope,
the identity, the owner, the tags, the roles, continuo's root properties and the schema object,
with the declared columns, their types, and the quality rules and relationships the tests give.
It also gives the SHA-256 of the fields of the node, of its tests and of the nodes its
relationships point to that it reads, and of nothing else of them; `read_tree` names those
fields. `logical_type` gives the ODCS logical type of a declared column type. `run_facts` reads back from a contract what continuo
runs the node with.

`seed_header_issues` compares the columns a seed declares with the header of its CSV file.

A field of a node in a shape these functions cannot read raises `DbtNodeError`.
"""

import re

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts.canonical import canonical_json, normalise_text
from continuo_contracts.errors import CanonicalJsonError, DbtNodeError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.vocabulary import VENDOR, continuo_values

RESOURCE_TYPES: Final = ("model", "seed", "snapshot")

# The reasons `skip_reason` gives, in the order of the gates.
SKIP_REASONS: Final = ("no_owner", "local_stub", "installed_package")

DEFAULT_CRITICALITY: Final = "SECONDARY"

# The schedule of a seed that carries no tag.
SEED_SCHEDULE: Final = "seed"

_NO_OWNER, _LOCAL_STUB, _INSTALLED_PACKAGE = SKIP_REASONS

# The tag that marks a node as a stand-in for a table that another service owns.
_LOCAL_STUB_TAG: Final = "local_stub"

# The marks a generated contract may carry, for a fact that the generator keeps as written but
# that the team may want to look at.
FLAGS: Final = (
    "jinja_in_test_arguments",
    "test_column_not_declared",
    "primary_key_column_not_declared",
)

# The fields of a manifest node that `generate` reads, by where they sit: the node's own keys
# (`config` and `columns` apart), the keys of its `config`, of `config.meta`, of
# `config.meta.continuo` and of each of its columns. A name with a dot reaches further down, as
# `contract.enforced` is `config.contract.enforced`, and a `*` stands for every key of a mapping
# or every item of a list, in order. `source_sha256` covers the values of these fields and nothing
# else of the node, so `created_at`, the compiled SQL and a `meta` key that continuo does not read
# never move it. The node's `unique_id` is read too, but only to name the node in an error.
_NODE_FIELDS: Final = (
    "resource_type",
    "name",
    "version",
    "schema",
    "alias",
    "fqn",
    "original_file_path",
    "description",
    "tags",
    "deprecation_date",
    "primary_key",
)
_COLUMN_FIELDS: Final = (
    "name",
    "description",
    "data_type",
    "tags",
    "meta.continuo.classification",
)
_TABLE_TEST_FIELDS: Final = ("name", "enabled", "granularity", "params")
_CONFIG_FIELDS: Final = (
    "materialized",
    "unique_key",
    "incremental_strategy",
    "on_schema_change",
    "contract.enforced",
    "access",
    "group",
    "freshness.build_after.count",
    "freshness.build_after.period",
    "grants",
    "strategy",
    "updated_at",
    "check_cols",
    "hard_deletes",
    "snapshot_meta_column_names",
    "dbt_valid_to_current",
    "quote_columns",
    "column_types",
)
_META_FIELDS: Final = ("owner", "criticality", "timeout_minutes")
_CONTINUO_META_FIELDS: Final = (
    "readers",
    "reads",
    *(f"tests.*.{name}" for name in _TABLE_TEST_FIELDS),
)

# Every field above as a dotted path from the node.
_READ_PATHS: Final = (
    *_NODE_FIELDS,
    *(f"config.{name}" for name in _CONFIG_FIELDS),
    *(f"config.meta.{name}" for name in _META_FIELDS),
    *(f"config.meta.continuo.{name}" for name in _CONTINUO_META_FIELDS),
    *(f"columns.*.{name}" for name in _COLUMN_FIELDS),
)

# The fields of a test node that `generate` reads, as dotted paths from the test node. Of the
# test's kwargs it reads every one but `model`, which dbt fills with the Jinja that selects the
# tested node, and but a `column_name` that repeats the test's own `column_name`. A `column_name`
# kwarg that differs, as dbt writes for a test declared on the model (whose own `column_name` is
# null) or one whose arguments name another column, is the column or expression the test checks.
# The test's `unique_id` is read only to name the test in an error.
_KWARGS_FIELD: Final = "test_metadata.kwargs.*"
_TEST_FIELDS: Final = (
    "name",
    "column_name",
    "attached_node",
    "depends_on.nodes",
    "test_metadata.name",
    "test_metadata.namespace",
    _KWARGS_FIELD,
    "description",
    "tags",
    "config.severity",
    "config.where",
    "config.warn_if",
    "config.error_if",
    "config.fail_calc",
)
_MODEL_KWARG, _COLUMN_KWARG = "model", "column_name"
# Two more fields of a test that `generate` reads only for some tests. Of a test whose generic
# test has the name of one of dbt's own and no namespace, it reads the first macro dbt resolved
# for it, and of one that may be dbt's `relationships`, the refs dbt recorded for it. The step
# `0` reads the first item of a list alone, so a helper macro that a dbt upgrade adds after it
# moves nothing.
_FIRST: Final = "0"
_RESOLVED_MACRO_FIELD: Final = f"depends_on.macros.{_FIRST}"
_REF_FIELDS: Final = ("refs.*.name", "refs.*.package", "refs.*.version")
# The namespace of dbt's own tests, and the macro dbt resolves its test `<name>` to.
_DBT_NAMESPACE: Final = "dbt"
_DBT_MACRO: Final = "macro.dbt.test_{}"

# The fields of the manifest node or source that a `relationships` test points to, which give
# its id, and the same as dotted paths from the manifest.
_TARGET_FIELDS: Final = (
    ("nodes", ("schema", "name", "version")),
    ("sources", ("schema", "identifier", "name")),
)
_MANIFEST_PATHS: Final = tuple(
    f"{kind}.*.{name}" for kind, names in _TARGET_FIELDS for name in names
)

# The tests dbt ships, which a contract writes as ODCS rules, and the metric of each library rule.
_NOT_NULL, _UNIQUE, _ACCEPTED_VALUES, _RELATIONSHIPS = (
    "not_null",
    "unique",
    "accepted_values",
    "relationships",
)
_BUILT_INS: Final = frozenset(
    (_NOT_NULL, _UNIQUE, _ACCEPTED_VALUES, _RELATIONSHIPS)
)
_METRICS: Final = MappingProxyType(
    {
        _NOT_NULL: "nullValues",
        _UNIQUE: "duplicateValues",
        _ACCEPTED_VALUES: "invalidValues",
    }
)
# The column field that a strict `not_null` or `unique` test sets.
_COLUMN_FACTS: Final = MappingProxyType(
    {_NOT_NULL: "required", _UNIQUE: "unique"}
)

# A test's `config` keys that become quality properties, under their names there, with dbt's
# default of each, which a contract leaves out.
_TEST_SETTINGS: Final = (
    ("where", "where", ""),
    ("warn_if", "warnIf", "!= 0"),
    ("error_if", "errorIf", "!= 0"),
    ("fail_calc", "failCalc", "count(*)"),
)
_SEVERITIES: Final = MappingProxyType({"error": None, "warn": "warning"})
_JINJA_MARKS: Final = ("{{", "{%")
# The pairs of marks that quote an identifier in SQL, opening and closing: dbt writes a quoted
# column's name in a test and in an inferred `primary_key` inside the marks of its adapter, and
# escapes no mark inside the name.
_QUOTES: Final = (('"', '"'), ("`", "`"), ("[", "]"))

# The columns dbt adds to a snapshot's table, by their default names, and the one it adds when
# `hard_deletes` is `new_record`.
_HISTORY_COLUMNS: Final = (
    "dbt_scd_id",
    "dbt_updated_at",
    "dbt_valid_from",
    "dbt_valid_to",
)
_DELETED_COLUMN: Final = "dbt_is_deleted"
_NEW_RECORD: Final = "new_record"

# The logical type of each type name `logical_type` knows, and the string types that may carry a
# length.
_STRING_TYPES: Final = frozenset(
    ("text", "string", "varchar", "character varying", "char", "character")
)
_LOGICAL_TYPES: Final = MappingProxyType(
    {
        **dict.fromkeys(
            ("int", "integer", "int4", "bigint", "int8", "smallint", "int2"),
            "integer",
        ),
        **dict.fromkeys(
            (
                "numeric",
                "decimal",
                "double precision",
                "float",
                "float4",
                "float8",
                "real",
                "double",
            ),
            "number",
        ),
        **dict.fromkeys(_STRING_TYPES, "string"),
        "date": "date",
        **dict.fromkeys(
            (
                "timestamp",
                "timestamptz",
                "timestamp with time zone",
                "timestamp without time zone",
                "datetime",
            ),
            "timestamp",
        ),
        **dict.fromkeys(("boolean", "bool"), "boolean"),
    }
)
# A declared type: its name, then at most one `(...)` with no parenthesis inside, then only
# whitespace, which is a space or a tab.
_DECLARED_TYPE: Final = re.compile(
    r"([^()]*)(?:\(([^()]*)\)([ \t]+with(?:out)?[ \t]+time[ \t]+zone)?[ \t]*)?",
    re.IGNORECASE,
)
# The one name whose parentheses may be followed by `with time zone` or `without time zone`.
_ZONED: Final = "timestamp"
_WHITESPACE: Final = re.compile(r"[ \t]+")
# The length of a string type, ASCII digits between optional whitespace, and the most digits a
# length from 1 to 2**53 - 1 has once its leading zeros are left out, so `int` never reads a huge
# number.
_LENGTH: Final = re.compile(r"[ \t]*([0-9]+)[ \t]*")
_LENGTH_DIGITS: Final = 16
_MAX_LENGTH: Final = 2**53 - 1

# A model's `deprecation_date`: a date in ASCII digits, alone or before `T` or a space and a time.
_DATE: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ].+)?", re.ASCII | re.DOTALL
)

# dbt's default of `config.on_schema_change` and of a model's `config.access`, which a contract
# leaves out.
_DEFAULT_ON_SCHEMA_CHANGE: Final = "ignore"
_DEFAULT_ACCESS: Final = "protected"

# The materializations whose schema object is not a `table`.
_PHYSICAL_TYPES: Final = {"view": "view", "ephemeral": "ephemeral"}

# A snapshot's `config` keys, under the names its `snapshot` property gives them.
_SNAPSHOT_KEYS: Final = (
    ("strategy", "strategy"),
    ("unique_key", "uniqueKey"),
    ("updated_at", "updatedAt"),
    ("check_cols", "checkCols"),
    ("hard_deletes", "hardDeletes"),
    ("snapshot_meta_column_names", "metaColumnNames"),
    ("dbt_valid_to_current", "validToCurrent"),
)


def _label(node: Mapping[str, Any]) -> str:
    """Return how an error names `node`: its id, else its name, else a placeholder."""
    for key in ("unique_id", "name"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return "<unknown node>"


# Where a field sits, for an error: a node, whose label `_label` gives, or the label itself, such
# as `<node id>: test <test id>` for a field of a test, or `manifest` for a field of the manifest.
type _Where = Mapping[str, Any] | str

_MANIFEST: Final = "manifest"


def _refusal(
    where: _Where, field: str, must_be: str, value: object
) -> DbtNodeError:
    label = where if isinstance(where, str) else _label(where)
    shown = "an empty string" if value == "" else type(value).__name__
    return DbtNodeError(f"{label}: {field} must be {must_be}, got {shown}")


def _tags(node: Mapping[str, Any]) -> list[str]:
    """Return the tags of `node`, none when it has no `tags` key."""
    tags = node.get("tags", [])
    if not isinstance(tags, list):
        raise _refusal(node, "tags", "a list of strings", tags)
    for index, tag in enumerate(tags):
        if not isinstance(tag, str):
            raise _refusal(node, f"tags[{index}]", "a string", tag)
    return tags


def _text(node: Mapping[str, Any], field: str) -> str:
    """Return the string `field` of `node`, which must not be empty."""
    return _required_string(node, field, node.get(field))


def _required_string(where: _Where, field: str, value: object) -> str:
    """Return `value`, the field `field`, which must be a non-empty string."""
    if not isinstance(value, str) or not value:
        raise _refusal(where, field, "a non-empty string", value)
    return value


def node_service(node: Mapping[str, Any]) -> str:
    """Return the service of `node`: the first entry of its `fqn`, with `_` written `-`.

    A node with no `fqn`, or an empty one, has no service and gives the empty string. An `fqn`
    that is not a list of strings raises `DbtNodeError`.
    """
    fqn = node.get("fqn")
    if fqn is None:
        return ""
    if not isinstance(fqn, list):
        raise _refusal(node, "fqn", "a list", fqn)
    if not fqn:
        return ""
    if not isinstance(fqn[0], str):
        raise _refusal(node, "fqn[0]", "a string", fqn[0])
    return fqn[0].replace("_", "-")


def project_service(manifest: Mapping[str, Any], declared_service: str) -> str:
    """Return the service of the dbt project that `manifest` was compiled from.

    It is the manifest's `metadata.project_name` with `_` written `-`. A manifest with no
    project name gives `declared_service` as it is, which is the empty string when no service is
    declared either, and then `skip_reason` leaves no node out as `installed_package`. A
    `metadata` that is not a mapping, or a `project_name` that is not a string, raises
    `DbtNodeError`, whose message starts with `manifest`.
    """
    metadata = _mapping(_MANIFEST, "metadata", manifest.get("metadata"))
    name = _optional_string(
        _MANIFEST, "metadata.project_name", metadata.get("project_name")
    )
    return name.replace("_", "-") if name else declared_service


def skip_reason(node: Mapping[str, Any], *, project: str) -> str | None:
    """Return why continuo leaves `node` out, or `None` for a node it keeps.

    The reason is one of `SKIP_REASONS`, and the first gate that applies wins. `project` is
    what `project_service` returns. A `config` or a `meta` that is missing or is not a mapping
    gives a node with no owner. `tags` that are not a list of strings, and an `fqn` that is not
    a list of strings, raise `DbtNodeError`, but only for a node that the earlier gates let
    through.
    """
    config = node.get("config")
    meta = config.get("meta") if isinstance(config, Mapping) else None
    owner = meta.get("owner") if isinstance(meta, Mapping) else None
    if not owner:
        return _NO_OWNER
    tags = _tags(node)
    if _LOCAL_STUB_TAG in tags:
        return _LOCAL_STUB
    service = node_service(node)
    if not tags and project and service != project:
        return _INSTALLED_PACKAGE
    return None


def schedule(node: Mapping[str, Any]) -> str:
    """Return the schedule `node` runs on: its first tag, else `seed` for a seed, else `""`.

    The empty schedule belongs to a model or snapshot with no tag, a dormant node. `tags` that
    are not a list of strings raise `DbtNodeError`.
    """
    tags = _tags(node)
    if tags:
        return tags[0]
    return SEED_SCHEDULE if node.get("resource_type") == "seed" else ""


def table_name(node: Mapping[str, Any]) -> str:
    """Return the table of `node`: its `name`, or `<name>.v<version>` for a versioned model.

    The name keeps its case. A `name` that is not a non-empty string, or a `version` that is
    neither a string nor a number, raises `DbtNodeError`; a `version` that is null or empty
    means the node is not versioned.
    """
    name = _text(node, "name")
    version = node.get("version")
    if version is None or version == "":
        return name
    if isinstance(version, bool) or not isinstance(version, str | int | float):
        raise _refusal(node, "version", "a string or a number", version)
    return f"{name}.v{version}"


def unique_id(node: Mapping[str, Any]) -> str:
    """Return the id of `node` in continuo: `<schema>.<table>` in lower case.

    The table is `table_name(node)` and the schema is the node's `schema`, which must be a
    non-empty string. The alias is not part of the id.
    """
    return f"{_text(node, 'schema')}.{table_name(node)}".lower()


def tests_by_node(
    manifest: Mapping[str, Any],
) -> dict[str, list[Mapping[str, Any]]]:
    """Join the tests of `manifest` to the nodes they test.

    The key is a node id. A test counts for its `attached_node` when it has one, and for every
    entry of its `depends_on.nodes` otherwise, once for each node. Only a node of resource type
    `test` is joined, and not one whose `config.enabled` is false: dbt keeps a test whose `ref`
    names a disabled model in `nodes`, disabled, and never runs it. The tests of one node come in
    the order of the manifest's keys.

    A `nodes` that is not a mapping of mappings raises `DbtNodeError`, whose message starts with
    `manifest`, and so does, starting with the test's id, a test whose `attached_node` is not a
    string, whose `depends_on` is not a mapping or whose `depends_on.nodes` is not a list of
    strings.
    """
    joined: dict[str, list[Mapping[str, Any]]] = {}
    nodes = _mapping(_MANIFEST, "nodes", manifest.get("nodes"))
    for key in nodes:
        node = _mapping(_MANIFEST, f"nodes.{key}", nodes[key])
        if node.get("resource_type") != "test":
            continue
        config = node.get("config")
        if isinstance(config, Mapping) and config.get("enabled") is False:
            continue
        attached = _optional_string(
            node, "attached_node", node.get("attached_node")
        )
        depends_on = _mapping(node, "depends_on", node.get("depends_on"))
        nodes_read = _strings(node, "depends_on.nodes", depends_on.get("nodes"))
        targets = [attached] if attached else nodes_read
        for target in dict.fromkeys(targets):
            joined.setdefault(target, []).append(node)
    return joined


@dataclass(frozen=True)
class Generated:
    """The contract `generate` writes for a dbt node.

    `document` is the ODCS contract. `flags` names the marks of `FLAGS` that the contract carries.
    `source_sha256` is the lowercase hex SHA-256 of the canonical JSON of the manifest fields that
    `generate` reads for the node and its tests and of nothing else, so it moves only when one of
    them does.
    """

    document: dict[str, Any]
    flags: tuple[str, ...]
    source_sha256: str


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _without_empty(written: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in written.items() if not _is_empty(value)}


def _mapping(where: _Where, field: str, value: object) -> Mapping[str, Any]:
    """Return `value`, the field `field`, as a mapping; null is an empty one."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _refusal(where, field, "a mapping", value)
    return value


def _strings(where: _Where, field: str, value: object) -> list[str]:
    """Return `value`, the field `field`, as a new list of strings; null is none."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _refusal(where, field, "a list of strings", value)
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _refusal(where, f"{field}[{index}]", "a string", item)
    return list(value)


def _optional_text(node: Mapping[str, Any], field: str) -> str:
    """Return the field `field` of `node` as a string; a missing or null one is `""`."""
    return _optional_string(node, field, node.get(field))


def _optional_string(where: _Where, field: str, value: object) -> str:
    """Return `value`, the field `field`, as a string; null is `""`."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _refusal(where, field, "a string", value)
    return value


def _owner(node: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    owner = meta.get("owner")
    if not isinstance(owner, str) or not owner:
        raise _refusal(node, "config.meta.owner", "a non-empty string", owner)
    return owner


def _kind(node: Mapping[str, Any]) -> str:
    resource_type = node.get("resource_type")
    if resource_type not in RESOURCE_TYPES:
        raise _refusal(
            node,
            "resource_type",
            "one of " + ", ".join(RESOURCE_TYPES),
            resource_type,
        )
    return f"dbt-{resource_type}"


def _materialized(node: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    materialized = config.get("materialized")
    if not isinstance(materialized, str) or not materialized:
        raise _refusal(
            node, "config.materialized", "a non-empty string", materialized
        )
    return materialized


def _materialization(
    materialized: str, config: Mapping[str, Any]
) -> dict[str, Any]:
    on_schema_change = config.get("on_schema_change")
    if on_schema_change == _DEFAULT_ON_SCHEMA_CHANGE:
        on_schema_change = None
    written = {
        "materialized": materialized,
        "uniqueKey": config.get("unique_key"),
        "incrementalStrategy": config.get("incremental_strategy"),
        "onSchemaChange": on_schema_change,
    }
    return {key: value for key, value in written.items() if not _is_empty(value)}


def _deprecation_date(node: Mapping[str, Any]) -> str | None:
    """Return the date part of the node's `deprecation_date`, `YYYY-MM-DD`, as it is written.

    dbt writes the date a team declares as midnight in the zone of the machine that parsed the
    project, with that zone's offset, so the date part is the declared date in every zone. The
    value is a calendar date written `YYYY-MM-DD` in ASCII digits, alone or followed by `T` or a
    space and a time, which is not read, so `2026-11-18T24:00:00` is 2026-11-18.
    """
    value = node.get("deprecation_date")
    if value is None:
        return None
    match = _DATE.fullmatch(value) if isinstance(value, str) else None
    if match is not None:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    raise _refusal(
        node,
        "deprecation_date",
        "a date written YYYY-MM-DD, alone or before a time",
        value,
    )


def _freshness_build_after(
    node: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    freshness = _mapping(node, "config.freshness", config.get("freshness"))
    build_after = _mapping(
        node, "config.freshness.build_after", freshness.get("build_after")
    )
    written = {
        "count": build_after.get("count"),
        "period": build_after.get("period"),
    }
    return {key: value for key, value in written.items() if not _is_empty(value)}


def _snapshot(
    node: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    written: dict[str, Any] = {}
    for key, name in _SNAPSHOT_KEYS:
        value = config.get(key)
        if key == "snapshot_meta_column_names":
            names = _mapping(node, f"config.{key}", value)
            value = {k: v for k, v in names.items() if not _is_empty(v)}
        if not _is_empty(value):
            written[name] = value
    return written


def _root_properties(
    node: Mapping[str, Any],
    config: Mapping[str, Any],
    meta: Mapping[str, Any],
    materialized: str,
) -> list[dict[str, Any]]:
    """Return the continuo items of the contract root, leaving out each one with no value."""
    resource_type = node.get("resource_type")
    is_model = resource_type == "model"
    criticality = meta.get("criticality")
    contract = _mapping(node, "config.contract", config.get("contract"))
    access = config.get("access") if is_model else None
    values: dict[str, Any] = {
        "service": node_service(node),
        "kind": _kind(node),
        "schedule": schedule(node),
        "criticality": DEFAULT_CRITICALITY
        if _is_empty(criticality)
        else criticality,
        "timeoutMinutes": meta.get("timeout_minutes"),
        "filePath": _text(node, "original_file_path"),
        "materialization": _materialization(materialized, config),
        "contractEnforced": True if contract.get("enforced") is True else None,
        "access": None if access == _DEFAULT_ACCESS else access,
        "group": config.get("group"),
        "deprecationDate": _deprecation_date(node) if is_model else None,
        "freshnessBuildAfter": _freshness_build_after(node, config)
        if is_model
        else None,
        "snapshot": _snapshot(node, config)
        if resource_type == "snapshot"
        else None,
        "quoteColumns": config.get("quote_columns")
        if resource_type == "seed"
        else None,
    }
    # A value is copied, so a change to the contract never reaches the node: a `unique_key` or
    # `check_cols` list is the node's own.
    return [
        {"vendor": VENDOR, "property": name, "value": _plain(value)}
        for name, value in values.items()
        if not _is_empty(value)
    ]


def _roles(
    node: Mapping[str, Any],
    config: Mapping[str, Any],
    continuo: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the readers, then the grantees of each privilege of `config.grants` in name order.

    A reader or a grantee that is empty or blank raises `DbtNodeError`: the stored form drops an
    empty role, which ODCS requires.
    """
    field = "config.meta.continuo.readers"
    readers = _strings(node, field, continuo.get("readers"))
    roles = [
        {"role": _role(node, f"{field}[{index}]", reader), "access": "read"}
        for index, reader in enumerate(readers)
    ]
    grants = _mapping(node, "config.grants", config.get("grants"))
    for privilege in sorted(grants):
        field = f"config.grants.{privilege}"
        grantees = grants[privilege]
        if isinstance(grantees, str):
            roles.append(
                {"role": _role(node, field, grantees), "access": privilege}
            )
            continue
        if not isinstance(grantees, list):
            raise _refusal(node, field, "a string or a list of strings", grantees)
        roles += [
            {
                "role": _role(node, f"{field}[{index}]", grantee),
                "access": privilege,
            }
            for index, grantee in enumerate(_strings(node, field, grantees))
        ]
    return roles


def _role(node: Mapping[str, Any], field: str, value: str) -> str:
    """Return `value`, the role at `field`, which must not be empty or blank."""
    if not value.strip():
        raise _refusal(node, field, "a string that is not blank", value)
    return value


def logical_type(data_type: str) -> dict[str, Any] | None:
    """Return the ODCS logical type of the declared column type `data_type`, or `None`.

    The type is a name, then at most one `(...)` with no parenthesis inside it, then nothing but
    whitespace; any other type has none. The one exception is `timestamp`, whose `(...)` may be
    followed by `with time zone` or `without time zone`, in any case and with whitespace between
    the words, as in `timestamp(6) with time zone`, which is `timestamp`. Whitespace is a space or a tab and nothing else, so
    a newline or U+001C is a character of the name. The name is read in lower case, without the
    whitespace at its ends and with each run of whitespace inside it read as one space, so
    `Character  Varying (10)` is `character varying`. `int`, `integer`, `int4`, `bigint`, `int8`,
    `smallint` and `int2` are `integer`; `numeric`, `decimal`, `double precision`, `float`,
    `float4`, `float8`, `real` and `double` are `number`; `text`, `string`, `varchar`,
    `character varying`, `char` and `character` are `string`; `date` is `date`; `timestamp`,
    `timestamptz`, `timestamp with time zone`, `timestamp without time zone` and `datetime` are
    `timestamp`; `boolean` and `bool` are `boolean`. Any other name has none, and so has an array
    type (one with `[`, such as `integer[]`) and a type with a character outside ASCII.

    A string type whose `(...)` holds a length, ASCII digits with optional whitespace around
    them, has the `logicalTypeOptions` `{"maxLength": n}` when the length is from 1 to
    2**53 - 1, read with its leading zeros left out, so `varchar(010)` has 10; another length, or
    other text in the parentheses, keeps the type and gives no `maxLength`. The result is a new
    dict, such as `{"logicalType": "integer"}`.
    """
    if "[" in data_type or not data_type.isascii():
        return None
    parts = _DECLARED_TYPE.fullmatch(data_type)
    if parts is None:
        return None
    name, inside, zone = parts.groups()
    base = _WHITESPACE.sub(" ", name).strip(" ").lower()
    if zone is not None and base != _ZONED:
        return None
    logical = _LOGICAL_TYPES.get(base)
    if logical is None:
        return None
    written: dict[str, Any] = {"logicalType": logical}
    length = _length(inside) if logical == "string" else None
    if length is not None:
        written["logicalTypeOptions"] = {"maxLength": length}
    return written


def _length(inside: str | None) -> int | None:
    """Return the length in `inside`, the text between a string type's parentheses, or `None`.

    It is a length from 1 to 2**53 - 1, read with its leading zeros left out.
    """
    match = _LENGTH.fullmatch(inside) if inside is not None else None
    if match is None:
        return None
    digits = match.group(1).lstrip("0")
    if not digits or len(digits) > _LENGTH_DIGITS:
        return None
    length = int(digits)
    return length if length <= _MAX_LENGTH else None


@dataclass(frozen=True)
class _Run:
    """What one call of `generate` shares while it reads a node's tests.

    `label` names the node in an error. `flags` collects the marks of `FLAGS` the contract
    carries, and `targets` the fields `generate` reads of the manifest node or source each
    relationship points to, by its manifest key.
    """

    node: Mapping[str, Any]
    manifest: Mapping[str, Any]
    label: str
    flags: set[str]
    targets: dict[str, Any]


def _column_types(
    node: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, str]:
    """Return a seed's `config.column_types`, each type as a string; another node has none."""
    if node.get("resource_type") != "seed":
        return {}
    types = _mapping(node, "config.column_types", config.get("column_types"))
    return {
        key: _optional_string(node, f"config.column_types.{key}", types[key])
        for key in types
    }


def _column(
    node: Mapping[str, Any], field: str, value: object, types: Mapping[str, str]
) -> dict[str, Any]:
    """Return the property of the declared column `value`, at `field` of `node`.

    Its `primaryKey`, `required`, `unique`, `relationships` and `quality` start empty, for the
    node's key and tests to fill.
    """
    column = _mapping(node, field, value)
    name = _required_string(node, f"{field}.name", column.get("name"))
    data_type = _optional_string(
        node, f"{field}.data_type", column.get("data_type")
    )
    physical = types.get(name, "").strip() or data_type.strip()
    meta = _mapping(node, f"{field}.meta", column.get("meta"))
    continuo = _mapping(node, f"{field}.meta.continuo", meta.get("continuo"))
    description = _optional_string(
        node, f"{field}.description", column.get("description")
    )
    return {
        "name": name,
        "physicalType": physical,
        **(logical_type(physical) or {}),
        "description": normalise_text(description),
        "tags": _strings(node, f"{field}.tags", column.get("tags")),
        "classification": _optional_string(
            node,
            f"{field}.meta.continuo.classification",
            continuo.get("classification"),
        ),
        "primaryKey": None,
        "primaryKeyPosition": None,
        "required": None,
        "unique": None,
        "relationships": [],
        "quality": [],
    }


def _declared(
    columns: Mapping[str, dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """Return the declared column that `name` names, or `None`.

    `name` names the column of that name. Failing that, a name in one pair of SQL quotes, `"..."`,
    `` `...` `` or `[...]`, names the column whose name is what the outer pair holds, as it is:
    dbt writes the name of a column it quotes inside its adapter's marks and escapes no mark
    inside it, so no doubled mark is read as one. No adapter is read, and a declared name that
    holds quotes is found by the first lookup.
    """
    column = columns.get(name)
    if column is not None or len(name) < 2:
        return column
    for opening, closing in _QUOTES:
        if name.startswith(opening) and name.endswith(closing):
            return columns.get(name[1:-1])
    return None


def _primary_key(run: _Run, columns: Mapping[str, dict[str, Any]]) -> None:
    """Mark the columns of a model's `primary_key`, with their positions in a key of two or more.

    A key column is found as `_declared` finds it. One the node does not declare is left out,
    and the contract is flagged.
    """
    node = run.node
    if node.get("resource_type") != "model":
        return
    key = _strings(node, "primary_key", node.get("primary_key"))
    for position, name in enumerate(key, start=1):
        column = _declared(columns, name)
        if column is None:
            run.flags.add("primary_key_column_not_declared")
            continue
        column["primaryKey"] = True
        if len(key) > 1:
            column["primaryKeyPosition"] = position


def _history_columns(
    node: Mapping[str, Any], config: Mapping[str, Any], declared: Iterable[str]
) -> list[dict[str, Any]]:
    """Return the columns dbt adds to a snapshot's table that the snapshot does not declare.

    Each has the name `config.snapshot_meta_column_names` gives it, else its default name.
    """
    field = "config.snapshot_meta_column_names"
    names = _mapping(node, field, config.get("snapshot_meta_column_names"))
    defaults: tuple[str, ...] = _HISTORY_COLUMNS
    if config.get("hard_deletes") == _NEW_RECORD:
        defaults += (_DELETED_COLUMN,)
    seen = set(declared)
    written = []
    for default in defaults:
        name = (
            _optional_string(node, f"{field}.{default}", names.get(default))
            or default
        )
        if name not in seen:
            seen.add(name)
            written.append({"name": name})
    return written


def _table_tests(
    node: Mapping[str, Any], continuo: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return the table tests of `config.meta.continuo.tests` as quality rules, in their order."""
    field = "config.meta.continuo.tests"
    items = continuo.get("tests")
    if items is None:
        return []
    if not isinstance(items, list):
        raise _refusal(node, field, "a list", items)
    rules = []
    for index, item in enumerate(items):
        where = f"{field}[{index}]"
        if not isinstance(item, Mapping):
            raise _refusal(node, where, "a mapping", item)
        test = item
        enabled = test.get("enabled")
        if enabled is None:
            enabled = True
        if not isinstance(enabled, bool):
            raise _refusal(node, f"{where}.enabled", "a boolean", enabled)
        implementation = {
            "enabled": enabled,
            "granularity": _optional_string(
                node, f"{where}.granularity", test.get("granularity")
            ),
            "params": _writable(
                _mapping(node, f"{where}.params", test.get("params")),
                _label(node),
                f"{where}.params",
            ),
        }
        rules.append(
            {
                "type": "custom",
                "engine": "data-quality",
                "name": _required_string(node, f"{where}.name", test.get("name")),
                "implementation": _without_empty(implementation),
            }
        )
    return rules


def _has_jinja(value: object) -> bool:
    """Say whether a string in `value`, at any depth, holds `{{` or `{%`."""
    if isinstance(value, str):
        return any(mark in value for mark in _JINJA_MARKS)
    if isinstance(value, Mapping):
        return any(_has_jinja(value[key]) for key in value)
    if isinstance(value, list):
        return any(_has_jinja(item) for item in value)
    return False


def _severity(label: str, config: Mapping[str, Any]) -> str:
    """Return a test's severity, `error` or `warn`, read in any case; null is `error`."""
    value = config.get("severity")
    if value is None:
        return "error"
    severity = value.lower() if isinstance(value, str) else ""
    if severity not in _SEVERITIES:
        raise _refusal(label, "config.severity", "warn or error", value)
    return severity


def _source_id(source: Mapping[str, Any]) -> str:
    """Return the id of a manifest source: `<schema>.<identifier or name>` in lower case."""
    schema = _text(source, "schema")
    name = _text(source, "name")
    identifier = _optional_text(source, "identifier")
    return f"{schema}.{identifier or name}".lower()


def _refs_to_itself(run: _Run, test: Mapping[str, Any], label: str) -> int:
    """Return how many of the refs dbt recorded for `test` name the tested node.

    A ref names it when its `name` is the node's `name`, its `package`, when it has one, the
    node's package (the first entry of its `fqn`), and its `version`, when it has one, the node's
    `version`, compared as text. dbt records one ref for each `ref()` call of the test, the one in
    its `model` kwarg among them.
    """
    refs = test.get("refs")
    if refs is None:
        return 0
    if not isinstance(refs, list):
        raise _refusal(label, "refs", "a list", refs)
    node = run.node
    count = 0
    for index, ref in enumerate(refs):
        field = f"refs[{index}]"
        if not isinstance(ref, Mapping):
            raise _refusal(label, field, "a mapping", ref)
        name = _required_string(label, f"{field}.name", ref.get("name"))
        package = _optional_string(label, f"{field}.package", ref.get("package"))
        version = ref.get("version")
        count += (
            name == node.get("name")
            and (not package or package.replace("_", "-") == node_service(node))
            and (_is_empty(version) or str(version) == str(node.get("version")))
        )
    return count


def _relationship_target(
    run: _Run, test: Mapping[str, Any], label: str
) -> str | None:
    """Return the id of the node or source that the `relationships` test `test` points to.

    It is the entry of the test's `depends_on.nodes` other than its `attached_node`. With none,
    the test points to the tested node itself when it refs it a second time, besides its `model`
    kwarg, and otherwise to nothing the manifest holds (a literal relation, a var or a macro, or
    a model that is disabled or absent), which gives `None`. A test with no `attached_node`,
    which dbt 1.12 writes for every generic test on a column, names no tested node, so no entry
    of its `depends_on.nodes` is known to be the target, and it gives `None` whatever they are.
    """
    attached = test.get("attached_node")
    if not attached:
        return None
    depends_on = _mapping(label, "depends_on", test.get("depends_on"))
    nodes = _strings(label, "depends_on.nodes", depends_on.get("nodes"))
    others = list(dict.fromkeys(entry for entry in nodes if entry != attached))
    to_itself = _refs_to_itself(run, test, label) > 1
    if not others:
        return unique_id(run.node) if to_itself else None
    if len(others) > 1:
        raise DbtNodeError(
            f"{label}: depends_on.nodes must name one node besides attached_node, "
            f"got {len(others)}"
        )
    (key,) = others
    for kind, _ in _TARGET_FIELDS:
        target = _mapping(label, kind, run.manifest.get(kind)).get(key)
        if isinstance(target, Mapping):
            run.targets[key] = _read_fields(
                target, read_tree(kind), f"{label}: {key}"
            )
            try:
                return (
                    unique_id(target) if kind == "nodes" else _source_id(target)
                )
            except DbtNodeError as error:
                raise DbtNodeError(f"{label}: {error}") from None
    raise DbtNodeError(
        f"{label}: depends_on.nodes names {key}, which is neither a node nor a source "
        "of the manifest"
    )


def _rule_settings(
    label: str, test: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Return whether the test `test` is strict, and the fields every rule from it carries.

    A strict test has severity `error`, no `where` and dbt's default thresholds, so a `not_null`
    or `unique` one makes its column `required` or `unique`, and a `relationships` one gives a
    relationship. The fields are the `severity`
    (`warning` for `warn`, none for `error`), the `description`, the `tags`, and `where`,
    `warnIf`, `errorIf` and `failCalc` as quality properties when they are not dbt's defaults.
    """
    config = _mapping(label, "config", test.get("config"))
    severity = _severity(label, config)
    settings: dict[str, str] = {}
    for key, prop, default in _TEST_SETTINGS:
        value = _optional_string(label, f"config.{key}", config.get(key))
        if value not in ("", default):
            settings[prop] = value
    description = _optional_string(label, "description", test.get("description"))
    common = {
        "severity": _SEVERITIES[severity],
        "description": normalise_text(description),
        "tags": _strings(label, "tags", test.get("tags")),
        "customProperties": [
            {"vendor": VENDOR, "property": prop, "value": value}
            for prop, value in settings.items()
        ],
    }
    return severity == "error" and not settings, common


def _built_in(
    label: str, test: Mapping[str, Any], generic: str, namespace: str
) -> str:
    """Return the name of the dbt test that `test` runs, or `""` when it runs another.

    It is dbt's `not_null`, `unique`, `accepted_values` or `relationships` when the generic test
    has that name and the namespace `dbt`, or that name, no namespace, and dbt's own macro for it
    (`macro.dbt.test_<name>`) as the first entry of the test's `depends_on.macros`, which is the
    macro dbt resolved the bare name to. A project or a package that writes its own macro of the
    name is not dbt's, and neither is a test with no resolved macro or of another namespace.
    """
    if generic not in _BUILT_INS:
        return ""
    if namespace:
        return generic if namespace == _DBT_NAMESPACE else ""
    depends_on = _mapping(label, "depends_on", test.get("depends_on"))
    macros = depends_on.get("macros")
    if macros is None:
        return ""
    if not isinstance(macros, list):
        raise _refusal(label, "depends_on.macros", "a list", macros)
    if not macros:
        return ""
    first = macros[0]
    if not isinstance(first, str):
        raise _refusal(label, "depends_on.macros[0]", "a string", first)
    return generic if first == _DBT_MACRO.format(generic) else ""


def _add_test(
    run: _Run,
    test: Mapping[str, Any],
    columns: Mapping[str, dict[str, Any]],
    table_rules: list[dict[str, Any]],
) -> None:
    """Write what the dbt test `test` says on the column it tests, else on the schema object.

    The column a test checks is its `column_name` kwarg when it has one that differs from its own
    `column_name`, and its own `column_name` otherwise; a name is found as `_declared` finds it.
    A singular test, and a test on no declared column, gives a rule on the schema object; the
    latter, whose column may be an expression, keeps that column in its `column_name` argument
    and flags the contract. dbt's `not_null`,
    `unique` and `accepted_values`, as `_built_in` tells them, give library rules, the last with
    its `values` as `validValues` beside its other kwargs, a strict `relationships` test on a
    declared column gives a relationship to the target the manifest holds, and any other generic
    test, a `relationships` test that is not strict, has no `attached_node` or whose target the
    manifest does not hold among them, gives a `custom` rule with the generic test and its arguments as its
    `implementation`.
    """
    label = f"{run.label}: test {_label(test)}"
    name = _required_string(label, "name", test.get("name"))
    strict, common = _rule_settings(label, test)
    metadata = _mapping(label, "test_metadata", test.get("test_metadata"))
    generic = _optional_string(label, "test_metadata.name", metadata.get("name"))
    kwargs = _mapping(label, "test_metadata.kwargs", metadata.get("kwargs"))
    arguments = {
        key: _writable(kwargs[key], label, f"test_metadata.kwargs.{key}")
        for key in kwargs
        if not _repeats(test, kwargs, key)
    }
    where = continuo_values(common["customProperties"]).get("where", "")
    if _has_jinja([arguments, where]):
        run.flags.add("jinja_in_test_arguments")
    column_name = _optional_string(label, "column_name", test.get("column_name"))
    if _COLUMN_KWARG in arguments:
        field = f"test_metadata.kwargs.{_COLUMN_KWARG}"
        column_name = (
            _optional_string(label, field, arguments.pop(_COLUMN_KWARG))
            or column_name
        )
    rule: dict[str, Any]
    if not generic:
        rule = {"id": name, "name": name, "type": "custom", "engine": "dbt"}
        table_rules.append(
            _without_empty({**rule, "implementation": name, **common})
        )
        return
    namespace = _optional_string(
        label, "test_metadata.namespace", metadata.get("namespace")
    )
    column = _declared(columns, column_name)
    if column_name and column is None:
        run.flags.add("test_column_not_declared")
        arguments[_COLUMN_KWARG] = column_name
    built_in = _built_in(label, test, generic, namespace)
    if built_in == _RELATIONSHIPS and column is not None and strict:
        field = _required_string(
            label, "test_metadata.kwargs.field", arguments.get("field")
        )
        target = _relationship_target(run, test, label)
        if target is not None:
            column["relationships"].append({"to": f"{target}.{field}"})
            return
        built_in = ""
    if built_in in _METRICS:
        if built_in == _ACCEPTED_VALUES:
            others = {key: arguments[key] for key in arguments if key != "values"}
            written = {
                **_without_empty({"validValues": arguments.get("values")}),
                **others,
            }
        else:
            written = _without_empty(
                {_COLUMN_KWARG: arguments.get(_COLUMN_KWARG)}
            )
        rule = {
            "id": name,
            "metric": _METRICS[built_in],
            "mustBe": 0,
            "arguments": written,
        }
        if strict and column is not None and built_in in _COLUMN_FACTS:
            column[_COLUMN_FACTS[built_in]] = True
    else:
        implementation = {
            "test": f"{namespace}.{generic}" if namespace else generic,
            "arguments": arguments,
        }
        rule = {
            "id": name,
            "name": name,
            "type": "custom",
            "engine": "dbt",
            "implementation": _without_empty(implementation),
        }
    rules = table_rules if column is None else column["quality"]
    rules.append(_without_empty({**rule, **common}))


def _in_order(label: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return `rules` in the order of their canonical JSON."""
    return sorted(rules, key=lambda rule: _canonical(rule, label, "rule"))


def _schema_parts(
    run: _Run,
    tests: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    continuo: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the properties and the quality rules of the node's schema object.

    The properties are the declared columns in the order of `columns`, then a snapshot's history
    columns. The quality rules are the rules of the tests that sit on no column, then the table
    tests in their written order.
    """
    node = run.node
    declared = _mapping(node, "columns", node.get("columns"))
    types = _column_types(node, config)
    properties = [
        _column(node, f"columns.{key}", declared[key], types) for key in declared
    ]
    columns = {column["name"]: column for column in properties}
    _primary_key(run, columns)
    table_rules: list[dict[str, Any]] = []
    for test in tests:
        _add_test(run, test, columns, table_rules)
    written = []
    for column in properties:
        column["relationships"] = _in_order(run.label, column["relationships"])
        column["quality"] = _in_order(run.label, column["quality"])
        written.append(_without_empty(column))
    if node.get("resource_type") == "snapshot":
        written += _history_columns(node, config, columns)
    return written, _in_order(run.label, table_rules) + _table_tests(
        node, continuo
    )


def _tree(paths: Iterable[Sequence[str]]) -> dict[str, Any]:
    """Return the paths, each a sequence of keys, as a tree of nested dicts; a leaf is `{}`."""
    tree: dict[str, Any] = {}
    for keys in paths:
        branch = tree
        for key in keys:
            branch = branch.setdefault(key, {})
    return tree


_NODE_TREE: Final = _tree(path.split(".") for path in _READ_PATHS)
_TARGET_TREES: Final = MappingProxyType(
    {kind: _tree([name] for name in names) for kind, names in _TARGET_FIELDS}
)


def _repeats(
    test: Mapping[str, Any], kwargs: Mapping[str, Any], key: str
) -> bool:
    """Say whether the kwarg `key` of `test` is one `generate` does not read.

    It is `model`, and a `column_name` equal to the test's own `column_name`.
    """
    if key == _MODEL_KWARG:
        return True
    return key == _COLUMN_KWARG and kwargs[key] == test.get(_COLUMN_KWARG)


def _test_tree(test: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Return the tree of the fields of `test` that `generate` reads.

    It is `_TEST_FIELDS`, with the kwargs wildcard written as the test's own kwargs but those
    `_repeats` names; then the first resolved macro of a generic test with the name of one of
    dbt's own and no namespace, and the refs of a `relationships` test with no namespace or the
    namespace `dbt`. A `test_metadata` or a `test_metadata.kwargs` that is not a mapping raises
    `DbtNodeError`, which starts with `label`.
    """
    metadata = _mapping(label, "test_metadata", test.get("test_metadata"))
    kwargs = _mapping(label, "test_metadata.kwargs", metadata.get("kwargs"))
    paths = [field.split(".") for field in _TEST_FIELDS if field != _KWARGS_FIELD]
    paths += [
        ["test_metadata", "kwargs", key]
        for key in kwargs
        if not _repeats(test, kwargs, key)
    ]
    generic, namespace = metadata.get("name"), metadata.get("namespace")
    if isinstance(generic, str) and generic in _BUILT_INS and not namespace:
        paths.append(_RESOLVED_MACRO_FIELD.split("."))
    if generic == _RELATIONSHIPS and namespace in (None, "", _DBT_NAMESPACE):
        paths += [field.split(".") for field in _REF_FIELDS]
    return _tree(paths)


def read_tree(
    part: str, *, test: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return the fields that `generate` reads of one part of its input, as a tree.

    `part` is `node` for the model, seed or snapshot, `test` for the test node `test`, and
    `nodes` or `sources` for the manifest node or source that a `relationships` test points to.
    The tree is a dict whose keys are the field names, each holding the tree of the fields read
    inside it, and `{}` for a field read whole; the key `*` stands for every key of a mapping or
    every item of a list, in order, and the key `0` under a list for its first item alone. The
    tree of a test names each of its own kwargs but `model` and a `column_name` equal to the
    test's own `column_name`; it names the first of its `depends_on.macros` when its generic test
    has the name of one of dbt's own and no namespace, and the `name`, `package` and `version` of
    each of its `refs` when it is a `relationships` test with no namespace or the namespace
    `dbt`. `source_sha256` covers exactly these fields. The result is a new dict.

    Raises `ValueError` for another `part`, for `test` with no test node, and for a test node
    given with another part, and `DbtNodeError`, which starts with the test's id, for a test whose
    `test_metadata` or `test_metadata.kwargs` is not a mapping.
    """
    if (part == "test") != (test is not None):
        raise ValueError(
            "read_tree takes a test node with the part test and with no other part"
        )
    if test is not None:
        return _test_tree(test, _label(test))
    trees = {"node": _NODE_TREE, **_TARGET_TREES}
    if part in trees:
        tree: dict[str, Any] = _plain(trees[part])
        return tree
    raise ValueError(
        f"read_tree reads the part node, test, nodes or sources, not {part!r}"
    )


def _select(source: Any, tree: Mapping[str, Any], label: str, name: str) -> Any:
    """Return the fields of `source` that `tree` names, nested as `source` holds them.

    A `*` step gives a list in the order of `source`: `[key, value]` pairs for a mapping and the
    items of a list, so the order of a node's columns reaches the hash. A `0` step over a list
    gives a list of its first item alone, or an empty one. A field that is missing is
    left out. A value on the way that is not a mapping or a list is kept as it is, since it is
    what the field's reader saw. A value or a mapping key that canonical JSON cannot write raises
    `DbtNodeError`, which starts with `label` and names the field.
    """
    if "*" in tree:
        each = tree["*"]
        if isinstance(source, Mapping):
            return [
                [
                    _key(key, label, name),
                    _field(source[key], each, label, f"{name}.{key}"),
                ]
                for key in source
            ]
        return [
            _field(item, each, label, f"{name}[{index}]")
            for index, item in enumerate(source)
        ]
    if isinstance(source, list):
        return [
            _field(item, tree[_FIRST], label, f"{name}[0]") for item in source[:1]
        ]
    picked = {}
    for key, branch in tree.items():
        if key in source:
            picked[_key(key, label, name)] = _field(
                source[key], branch, label, f"{name}.{key}" if name else key
            )
    return picked


def _field(value: Any, tree: Mapping[str, Any], label: str, name: str) -> Any:
    if isinstance(value, Mapping) and tree:
        return _select(value, tree, label, name)
    if isinstance(value, list) and ("*" in tree or _FIRST in tree):
        return _select(value, tree, label, name)
    return _writable(value, label, name)


def _canonical(value: Any, label: str, name: str) -> bytes:
    """Return the canonical JSON of `value`, the field `name` of what `label` names.

    A value that canonical JSON cannot write, such as a lone surrogate, a number outside its
    range or a nesting deeper than it allows, raises `DbtNodeError`, which starts with `label` and
    names the field and the path in it; with no `name`, the path alone.
    """
    try:
        return canonical_json(value)
    except CanonicalJsonError as error:
        path = f"{name}{str(error).removeprefix('$')}".removeprefix(".")
        raise DbtNodeError(f"{label}: {path}") from None


def _key(key: Any, label: str, name: str) -> Any:
    """Return `key`, a key of the mapping `name`, which canonical JSON must be able to write."""
    _canonical({key: None}, label, name)
    return key


def _writable(value: Any, label: str, name: str) -> Any:
    """Return a copy of `value`, the field `name`, which canonical JSON must be able to write.

    The copy holds plain dicts and lists, so a change to the contract never reaches the manifest.
    """
    _canonical(value, label, name)
    return _plain(value)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(value[key]) for key in value}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _read_fields(source: Any, tree: Mapping[str, Any], label: str) -> Any:
    """Return the fields of `source` that `tree` names, which canonical JSON must be able to write.

    A value that it cannot write, alone or where it sits, raises `DbtNodeError`, which starts with
    `label` and names the field.
    """
    read = _select(source, tree, label, "")
    _canonical(read, label, "")
    return read


def _read(run: _Run, tests: Sequence[Mapping[str, Any]]) -> tuple[Any, list[Any]]:
    """Return the fields `generate` reads of the node and of each of its tests.

    They are read before anything is built from them, so a value canonical JSON cannot write is
    a `DbtNodeError` that names its field, as `<node id>: test <test id>: <field> ...` for a test.
    """
    read_tests = []
    for test in tests:
        label = f"{run.label}: test {_label(test)}"
        read_tests.append(_read_fields(test, _test_tree(test, label), label))
    return _read_fields(run.node, read_tree("node"), run.label), read_tests


def _source_sha256(run: _Run, node: Any, tests: Sequence[Any]) -> str:
    """Return the SHA-256 of the fields `generate` reads.

    They are the fields of the node and of its tests, as `_read` returns them, and of their
    relationship targets.
    """
    inputs = {
        "node": node,
        "tests": sorted(
            tests, key=lambda read: _canonical(read, run.label, "tests")
        ),
        "targets": run.targets,
    }
    return sha256_hex(_canonical(inputs, run.label, "source"))


def generate(
    node: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
) -> Generated:
    """Return the ODCS contract of the dbt node `node`.

    `node` is a model, seed or snapshot of `manifest` that `skip_reason` keeps, and `tests` are
    its tests, as `tests_by_node` joins them. The contract has `id` `unique_id(node)`, `name`
    `table_name(node)`, `version` `"0"`, `status` `active`, the owner as `team.name`, the node's
    tags as dbt lists them, the readers and the grants as `roles`, continuo's root properties and
    one schema object whose `physicalName` is the node's alias.

    The schema object's `properties` are the declared columns in the order of the node's
    `columns`, each with its type (a seed's `config.column_types` wins over `data_type`) and the
    `logicalType` that `logical_type` gives for it, its description, tags and
    `meta.continuo.classification`, the key of a model's `primary_key`, and what the tests on it
    say; a snapshot's history columns follow. Its `quality` holds the rules of the tests that sit
    on no declared column, then the table tests of `config.meta.continuo.tests`. A field with no
    value is left out, and descriptions are trimmed. The result does not depend on the order of
    the tests, of a mapping's keys other than `columns`, or on the machine's time zone.

    `source_sha256` covers only the fields of the node, of its tests and of the nodes and sources
    its `relationships` tests point to that the generator reads, with the order of the columns.
    Another `config` or `meta` key, a test's `model` kwarg, `created_at` and the compiled SQL
    never move it.

    A field in a shape the generator cannot read, or that it reads and canonical JSON cannot write
    (such as an integer outside -(2**53 - 1) to 2**53 - 1, or a lone surrogate in a string or in
    a mapping key), raises `DbtNodeError`, whose message starts with the node's id and names the
    field. A field of a test also names the test, as `<node id>: test <test id>: <field> ...`, and
    a field of a relationship's target also names the target. A value nested so deep that the
    contract, or its rule, is deeper than canonical JSON allows raises `DbtNodeError` too, naming
    its path in the contract, so a contract `generate` returns can always be hashed.
    """
    for index, test in enumerate(tests):
        if not isinstance(test, Mapping):
            raise _refusal(node, f"tests[{index}]", "a mapping", test)
    run = _Run(node, manifest, _label(node), set(), {})
    node_read, tests_read = _read(run, tests)
    config = _mapping(node, "config", node.get("config"))
    meta = _mapping(node, "config.meta", config.get("meta"))
    continuo = _mapping(node, "config.meta.continuo", meta.get("continuo"))
    owner = _owner(node, meta)
    materialized = _materialized(node, config)
    table = table_name(node)
    alias = _optional_text(node, "alias")
    description = normalise_text(_optional_text(node, "description"))
    reads = _strings(node, "config.meta.continuo.reads", continuo.get("reads"))
    properties, quality = _schema_parts(run, tests, config, continuo)
    schema_object: dict[str, Any] = {
        "name": table,
        "physicalName": alias or table,
        "physicalType": _PHYSICAL_TYPES.get(materialized, "table"),
        "description": description,
        "customProperties": [
            {"vendor": VENDOR, "property": "reads", "value": reads}
        ]
        if reads
        else [],
        "properties": properties,
        "quality": quality,
    }
    document: dict[str, Any] = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": unique_id(node),
        "name": table,
        "version": "0",
        "status": "active",
        "description": {"purpose": description} if description else {},
        "team": {"name": owner},
        "tags": list(_tags(node)),
        "roles": _roles(node, config, continuo),
        "customProperties": _root_properties(node, config, meta, materialized),
        "schema": [_without_empty(schema_object)],
    }
    written = _without_empty(document)
    _canonical(written, run.label, "contract")
    return Generated(
        document=written,
        flags=tuple(flag for flag in FLAGS if flag in run.flags),
        source_sha256=_source_sha256(run, node_read, tests_read),
    )


@dataclass(frozen=True)
class RunFacts:
    """What continuo runs a dbt node with, as its contract says it.

    `unique_id` is the contract's `id`, `schema` the part of that id before its first dot (a
    schema name has no dot, so it is the id without `.<table>`, in lower case), `table` the
    schema object's `name` and `physical_name` its `physicalName` (else the table). `service`, `kind`, `schedule`, `criticality`, `timeout_minutes` and `file_path` are
    the root `continuo` properties `service`, `kind`, `schedule` (`""` for a dormant node, which
    has none), `criticality` (`DEFAULT_CRITICALITY` when absent), `timeoutMinutes` (`None` when
    absent) and `filePath`. `owner` is `team.name`, and `materialized` the `materialized` of the
    `materialization` property.
    """

    unique_id: str
    schema: str
    table: str
    physical_name: str
    service: str
    kind: str
    schedule: str
    criticality: str
    timeout_minutes: int | None
    file_path: str
    owner: str
    materialized: str


def run_facts(document: Mapping[str, Any]) -> RunFacts:
    """Return the run facts of the dbt contract `document`.

    `document` is a contract that `validate.validate_dbt_contract` accepted, as `generate` wrote
    it or in its stored form; both give the same facts. Each value is read as the contract holds
    it, and none is checked.
    """
    table_object = document["schema"][0]
    table = table_object["name"]
    values = continuo_values(document["customProperties"])
    contract_id = document["id"]
    return RunFacts(
        unique_id=contract_id,
        schema=contract_id.partition(".")[0],
        table=table,
        physical_name=table_object.get("physicalName") or table,
        service=values["service"],
        kind=values["kind"],
        schedule=values.get("schedule", ""),
        criticality=values.get("criticality", DEFAULT_CRITICALITY),
        timeout_minutes=values.get("timeoutMinutes"),
        file_path=values["filePath"],
        owner=document["team"]["name"],
        materialized=values["materialization"]["materialized"],
    )


def seed_header_issues(
    declared: Sequence[str], header: Sequence[str]
) -> list[str]:
    """Return a message for each column that a seed's CSV header and its contract disagree on.

    `declared` is the names of the columns the seed's contract declares and `header` the names
    in the first row of its CSV file. A column of the header that is not declared gives
    `<column> is in the CSV header but not declared`, in the order of the header, and then a
    declared column that the header lacks gives `<column> is declared but not in the CSV
    header`, in the order of `declared`. Names are compared exactly as written, so `ID` and `id`
    are two columns. A name that a list repeats gets one message. Both lists agreeing, in any
    order, gives an empty list.
    """
    declared_names = set(declared)
    header_names = set(header)
    return [
        f"{column} is in the CSV header but not declared"
        for column in dict.fromkeys(header)
        if column not in declared_names
    ] + [
        f"{column} is declared but not in the CSV header"
        for column in dict.fromkeys(declared)
        if column not in header_names
    ]
