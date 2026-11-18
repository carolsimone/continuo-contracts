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
the identity, the owner, the tags, the roles, continuo's root properties and the schema object.

A field of a node in a shape these functions cannot read raises `DbtNodeError`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from continuo_contracts.canonical import canonical_json, normalise_text
from continuo_contracts.errors import DbtNodeError
from continuo_contracts.hashing import sha256_hex
from continuo_contracts.vocabulary import VENDOR

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

# The top-level keys of a manifest node that `generate` reads. `source_sha256` covers their values
# and nothing else of the node, so `created_at` and the compiled SQL never move it.
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
    "config",
)

# The top-level keys of a test node that `generate` reads.
_TEST_FIELDS: Final[tuple[str, ...]] = ()

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


def _refusal(
    node: Mapping[str, Any], field: str, must_be: str, value: object
) -> DbtNodeError:
    shown = "an empty string" if value == "" else type(value).__name__
    return DbtNodeError(f"{_label(node)}: {field} must be {must_be}, got {shown}")


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
    value = node.get(field)
    if not isinstance(value, str) or not value:
        raise _refusal(node, field, "a non-empty string", value)
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
    declared either, and then `skip_reason` leaves no node out as `installed_package`.
    """
    metadata = manifest.get("metadata") or {}
    name = metadata.get("project_name")
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
    `test` is joined, and the tests of one node come in the order of the manifest's keys.
    """
    joined: dict[str, list[Mapping[str, Any]]] = {}
    for node in (manifest.get("nodes") or {}).values():
        if node.get("resource_type") != "test":
            continue
        attached = node.get("attached_node")
        depends_on = node.get("depends_on") or {}
        targets = [attached] if attached else depends_on.get("nodes") or []
        for target in dict.fromkeys(targets):
            joined.setdefault(target, []).append(node)
    return joined


@dataclass(frozen=True)
class Generated:
    """The contract `generate` writes for a dbt node.

    `document` is the ODCS contract. `flags` names the marks of `FLAGS` that the contract carries.
    `source_sha256` is the lowercase hex SHA-256 of the canonical JSON of the manifest fields that
    `generate` read, so it moves only when one of them does.
    """

    document: dict[str, Any]
    flags: tuple[str, ...]
    source_sha256: str


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _mapping(
    node: Mapping[str, Any], field: str, value: object
) -> Mapping[str, Any]:
    """Return `value`, the field `field` of `node`, as a mapping; null is an empty one."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _refusal(node, field, "a mapping", value)
    return value


def _strings(node: Mapping[str, Any], field: str, value: object) -> list[str]:
    """Return `value`, the field `field` of `node`, as a list of strings; null is none."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _refusal(node, field, "a list of strings", value)
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _refusal(node, f"{field}[{index}]", "a string", item)
    return value


def _optional_text(node: Mapping[str, Any], field: str) -> str:
    """Return the field `field` of `node` as a string; a missing or null one is `""`."""
    value = node.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _refusal(node, field, "a string", value)
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
    """Return the date of the node's `deprecation_date`, in the offset the value carries.

    dbt writes the date a team declares as midnight in the zone of the machine that parsed the
    project, with that zone's offset, so the date part is the declared date in every zone.
    """
    value = node.get("deprecation_date")
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except TypeError, ValueError:
        raise _refusal(
            node, "deprecation_date", "an ISO 8601 date and time", value
        ) from None


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
    return [
        {"vendor": VENDOR, "property": name, "value": value}
        for name, value in values.items()
        if not _is_empty(value)
    ]


def _roles(
    node: Mapping[str, Any],
    config: Mapping[str, Any],
    continuo: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the readers, then the grantees of each privilege of `config.grants` in name order."""
    readers = _strings(
        node, "config.meta.continuo.readers", continuo.get("readers")
    )
    roles = [{"role": reader, "access": "read"} for reader in readers]
    grants = _mapping(node, "config.grants", config.get("grants"))
    for privilege in sorted(grants):
        field = f"config.grants.{privilege}"
        grantees = grants[privilege]
        if isinstance(grantees, str):
            grantees = [grantees]
        elif not isinstance(grantees, list):
            raise _refusal(node, field, "a string or a list of strings", grantees)
        roles += [
            {"role": grantee, "access": privilege}
            for grantee in _strings(node, field, grantees)
        ]
    return roles


def _source_sha256(
    node: Mapping[str, Any], tests: Sequence[Mapping[str, Any]]
) -> str:
    read_tests = [
        {key: test[key] for key in _TEST_FIELDS if key in test} for test in tests
    ]
    inputs = {
        "node": {key: node[key] for key in _NODE_FIELDS if key in node},
        "tests": sorted(read_tests, key=canonical_json),
    }
    return sha256_hex(canonical_json(inputs))


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
    one schema object whose `physicalName` is the node's alias. A field with no value is left
    out, and descriptions are trimmed. The result does not depend on the order of a mapping's
    keys or on the machine's time zone.

    A field of `node` in a shape the generator cannot read raises `DbtNodeError`, whose message
    starts with the node's id and names the field.
    """
    config = _mapping(node, "config", node.get("config"))
    meta = _mapping(node, "config.meta", config.get("meta"))
    continuo = _mapping(node, "config.meta.continuo", meta.get("continuo"))
    owner = _owner(node, meta)
    materialized = _materialized(node, config)
    table = table_name(node)
    alias = _optional_text(node, "alias")
    description = normalise_text(_optional_text(node, "description"))
    reads = _strings(node, "config.meta.continuo.reads", continuo.get("reads"))
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
        "tags": _tags(node),
        "roles": _roles(node, config, continuo),
        "customProperties": _root_properties(node, config, meta, materialized),
        "schema": [{k: v for k, v in schema_object.items() if not _is_empty(v)}],
    }
    return Generated(
        document={k: v for k, v in document.items() if not _is_empty(v)},
        flags=(),
        source_sha256=_source_sha256(node, tests),
    )
