"""The dbt manifests that `scripts/build_dbt_fixtures.py` builds from the fixture projects."""

import functools
import gzip
import json

from pathlib import Path
from typing import Any

from continuo_contracts import dbt
from continuo_contracts.canonical import canonical_json
from continuo_contracts.hashing import sha256_hex

MANIFESTS = Path(__file__).resolve().parent / "fixtures" / "dbt" / "manifests"


@functools.cache
def load_manifest(name: str) -> dict[str, Any]:
    """Return the manifest of the fixture project `name`.

    The result is cached and shared by every caller, so a test that changes it works on a
    `copy.deepcopy` of it.
    """
    with gzip.open(
        MANIFESTS / f"{name}.json.gz", "rt", encoding="utf-8"
    ) as stream:
        manifest: dict[str, Any] = json.load(stream)
    return manifest


# The `source_sha256` of a node, computed as the README's dbt section describes it, from the
# trees of `dbt.read_tree` and nothing else of the generator.


def _picked(value: Any, tree: dict[str, Any]) -> Any:
    """Return the fields of `value` that `tree` names; `*` gives a list in the order of `value`,
    and `0` over a list a list of its first item alone."""
    if "*" in tree:
        if isinstance(value, dict):
            return [[key, _read(item, tree["*"])] for key, item in value.items()]
        return [_read(item, tree["*"]) for item in value]
    if isinstance(value, list):
        return [_read(item, tree["0"]) for item in value[:1]]
    return {key: _read(value[key], tree[key]) for key in tree if key in value}


def _read(value: Any, tree: dict[str, Any]) -> Any:
    if isinstance(value, dict) and tree:
        return _picked(value, tree)
    if isinstance(value, list) and ("*" in tree or "0" in tree):
        return _picked(value, tree)
    return value


def _is_dbts_relationships(test: dict[str, Any]) -> bool:
    """Say whether `test` runs dbt's `relationships`: by the namespace `dbt`, or by no namespace
    and dbt's own macro as the first that dbt resolved for it."""
    metadata = test.get("test_metadata") or {}
    if metadata.get("name") != "relationships":
        return False
    if metadata.get("namespace"):
        return metadata["namespace"] == "dbt"
    macros = (test.get("depends_on") or {}).get("macros") or []
    return macros[:1] == ["macro.dbt.test_relationships"]


def _is_strict(test: dict[str, Any]) -> bool:
    config = test.get("config") or {}
    return (
        (config.get("severity") or "error").lower() == "error"
        and not config.get("where")
        and (config.get("warn_if") or "!= 0") == "!= 0"
        and (config.get("error_if") or "!= 0") == "!= 0"
        and (config.get("fail_calc") or "count(*)") == "count(*)"
    )


def _targets(
    node: dict[str, Any], tests: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Return the read fields of each target of a strict dbt `relationships` test on a declared
    column, by its manifest key: the entry of the test's `depends_on.nodes` other than its
    `attached_node`, found under `nodes` or else under `sources`."""
    columns = {column["name"] for column in (node.get("columns") or {}).values()}
    targets: dict[str, Any] = {}
    for test in tests:
        if (
            not _is_dbts_relationships(test)
            or test.get("column_name") not in columns
            or not _is_strict(test)
        ):
            continue
        attached = test.get("attached_node")
        for key in test["depends_on"]["nodes"]:
            if key == attached or key in targets:
                continue
            for kind in ("nodes", "sources"):
                entry = (manifest.get(kind) or {}).get(key)
                if entry is not None:
                    targets[key] = _picked(entry, dbt.read_tree(kind))
                    break
    return targets


def source_sha256_of(
    node: dict[str, Any], tests: list[dict[str, Any]], manifest: dict[str, Any]
) -> str:
    """Return the SHA-256 of the canonical JSON of `{node, tests, targets}`."""
    read_tests = [
        _picked(test, dbt.read_tree("test", test=test)) for test in tests
    ]
    document = {
        "node": _picked(node, dbt.read_tree("node")),
        "tests": sorted(read_tests, key=canonical_json),
        "targets": _targets(node, tests, manifest),
    }
    return sha256_hex(canonical_json(document))
