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

A field of a node in a shape these functions cannot read raises `DbtNodeError`.
"""

from collections.abc import Mapping
from typing import Any, Final

from continuo_contracts.errors import DbtNodeError

RESOURCE_TYPES: Final = ("model", "seed", "snapshot")

# The reasons `skip_reason` gives, in the order of the gates.
SKIP_REASONS: Final = ("no_owner", "local_stub", "installed_package")

DEFAULT_CRITICALITY: Final = "SECONDARY"

# The schedule of a seed that carries no tag.
SEED_SCHEDULE: Final = "seed"

_NO_OWNER, _LOCAL_STUB, _INSTALLED_PACKAGE = SKIP_REASONS

# The tag that marks a node as a stand-in for a table that another service owns.
_LOCAL_STUB_TAG: Final = "local_stub"


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
