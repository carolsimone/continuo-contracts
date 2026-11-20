"""Write the golden dbt contracts and the dbt contract vector.

Run it with `uv run python scripts/write_dbt_goldens.py` after a change to the generator or to
the fixture manifests, and read the difference before committing it.

For every model, seed and snapshot of every fixture manifest under `tests/fixtures/dbt/manifests`
that `dbt.skip_reason` keeps, except the manifests of `UNGOLDEN` (the bound projects, which the
ontology tests read and whose contracts they pin), it writes
`tests/fixtures/dbt/golden/<project>/<id>.json`: the document `dbt.generate` writes (`generated`),
its `flags`, its `source_sha256`, its stored form `contract.normalise_dbt(generated, reads=[])`
(`stored`) and the `contract_hash` of that form. It removes a golden file that belongs to no kept
node.

It also writes `continuo_contracts/vectors/dbt_contracts.json`, one case for each rule in
`CONTRACT_CASES`, then one for each node in `ISSUES_CASES`, then one for each case of
`EDITED_CASES`, whose node and tests are fixture nodes changed by the edit the case names. A case
holds the node, its tests and the part of its manifest that `generate` reads, each pruned to the
fields `generate` reads (`pruned_node`, `pruned_test`, `pruned_manifest`, which read them from
`dbt.read_tree`), the `reads` passed to `normalise_dbt`, and what the library gives for them: every
case holds `source_sha256`, a contract case `generated`, `flags`, `stored` and `contract_hash`, and
an issues case, a node whose generated contract is not valid, `issues`, the `{path, reason}` of
each issue that `validate.validate_dbt_contract` finds in it, in order.

Each file is JSON with one space of indent and ASCII only, and ends with a newline. The keys of
every object are sorted, except a node's `columns` in the vector, which keep the order in which
the node declares its columns, because the contract's columns follow it. Two runs write the
same bytes.
"""

from __future__ import annotations

import copy
import gzip
import json
import logging
import sys

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from continuo_contracts import dbt
from continuo_contracts.contract import contract_hash, normalise_dbt
from continuo_contracts.validate import validate_dbt_contract

logger = logging.getLogger(__name__)

ROOT: Final = Path(__file__).resolve().parents[1]
MANIFESTS: Final = ROOT / "tests" / "fixtures" / "dbt" / "manifests"
GOLDEN: Final = ROOT / "tests" / "fixtures" / "dbt" / "golden"
VECTOR: Final = ROOT / "continuo_contracts" / "vectors" / "dbt_contracts.json"

# The fixture projects whose manifests the ontology tests read: their contracts have no golden
# file here.
UNGOLDEN: Final = ("core_bound", "finance_bound")

# The mapping under this key of a node lists its columns in the order the node declares them.
DECLARED_ORDER_KEY: Final = "columns"

# The parts of a manifest that hold the targets of a `relationships` test, as `dbt.read_tree`
# names them.
TARGET_KINDS: Final = ("nodes", "sources")

# The cases of the vector whose contract the library writes: the name of the case, the fixture
# project, the manifest key of the node and the tables its compiled SQL names.
CONTRACT_CASES: Final[tuple[tuple[str, str, str, tuple[str, ...]], ...]] = (
    ("model_with_tests", "cases", "model.cases.generic_tests", ()),
    ("seed_with_column_types", "cases", "seed.cases.typed_seed", ()),
    ("dormant_model", "cases", "model.cases.dormant", ()),
    ("versioned_model", "cases", "model.cases.customer.v2", ()),
    ("ephemeral_model", "cases", "model.cases.eph", ()),
    ("snapshot", "cases", "snapshot.cases.orders_snapshot", ()),
    ("enforced_contract", "cases", "model.cases.enforced", ()),
    ("governed", "cases", "model.cases.governed", ()),
    (
        "sql_reads",
        "cases",
        "model.cases.governed",
        ("Analytics.Orders", "analytics.orders", "raw.events"),
    ),
)

# The cases of the vector whose generated contract is not valid, in the same form: the name of the
# case, the fixture project, the manifest key of the node and the tables its compiled SQL names.
ISSUES_CASES: Final[tuple[tuple[str, str, str, tuple[str, ...]], ...]] = (
    ("bad_criticality", "cases", "model.cases.bad_criticality", ()),
)

# The tested node of the edited cases, and its tests that the edits change.
EDITED_NODE: Final = "model.cases.generic_tests"
_TEST_KEYS: Final = {
    "amount": "test.cases.is_positive_generic_tests_amount__0.b4bf29aa76",
    "warned": "test.cases.not_null_generic_tests_warned.0c31e50172",
    "kind": "test.cases.accepted_values_generic_tests_kind__a__b.56ac4e8409",
    "customer": (
        "test.cases.relationships_generic_tests_customer_id__id__ref_customer_."
        "04ebaabb0a"
    ),
    "lenient": "test.cases.unique_generic_tests_lenient.408a2824ab",
    "id": "test.cases.not_null_generic_tests_id.3e9e0d509b",
}


def edit_tests(manifest: dict[str, Any]) -> None:
    """Change four tests of the node of `EDITED_NODE` in `manifest`.

    The test of `amount` becomes the generic test `accepted_range` of the package `dbt_utils`,
    the warned `not_null` test gets the `warn_if` `> 5` and the `fail_calc` `sum(n)`, the
    `accepted_values` test of `kind` gets the extra kwarg `quote: false`, and the `relationships`
    test of `customer_id` gets the severity `warn`, so it is not strict.
    """
    nodes = manifest["nodes"]
    amount = nodes[_TEST_KEYS["amount"]]["test_metadata"]
    amount.update(name="accepted_range", namespace="dbt_utils")
    nodes[_TEST_KEYS["warned"]]["config"].update(
        warn_if="> 5", fail_calc="sum(n)"
    )
    nodes[_TEST_KEYS["kind"]]["test_metadata"]["kwargs"]["quote"] = False
    nodes[_TEST_KEYS["customer"]]["config"]["severity"] = "warn"


def edit_columns(manifest: dict[str, Any]) -> None:
    """Change the key, two column types and a test of the node of `EDITED_NODE` in `manifest`.

    The node's `primary_key` becomes `id`, `status` and `ghost`, a column it does not declare;
    `id` gets the type `varchar(36)` and `status` the type `character varying(00020)`; and the
    lenient `unique` test moves to the column `ghost`.
    """
    node = manifest["nodes"][EDITED_NODE]
    node["primary_key"] = ["id", "status", "ghost"]
    node["columns"]["id"]["data_type"] = "varchar(36)"
    node["columns"]["status"]["data_type"] = "character varying(00020)"
    lenient = manifest["nodes"][_TEST_KEYS["lenient"]]
    lenient["column_name"] = "ghost"
    lenient["test_metadata"]["kwargs"]["column_name"] = "ghost"


def edit_column_names(manifest: dict[str, Any]) -> None:
    """Name two columns of the node of `EDITED_NODE` in `manifest` as dbt does in two cases.

    The lenient `unique` test becomes a test declared on the model, which checks the expression
    `(id || '-' || status)`: its own `column_name` is null and its `column_name` kwarg is the
    expression. The `not_null` test of `id` and the node's `primary_key` name the column quoted,
    `"id"`, as dbt writes a column with `quote: true`.
    """
    nodes = manifest["nodes"]
    lenient = nodes[_TEST_KEYS["lenient"]]
    lenient["column_name"] = None
    lenient["test_metadata"]["kwargs"]["column_name"] = "(id || '-' || status)"
    quoted = nodes[_TEST_KEYS["id"]]
    quoted["column_name"] = '"id"'
    quoted["test_metadata"]["kwargs"]["column_name"] = '"id"'
    nodes[EDITED_NODE]["primary_key"] = ['"id"']


def edit_resolution(manifest: dict[str, Any]) -> None:
    """Change three tests of the node of `EDITED_NODE` in `manifest` that dbt resolved otherwise.

    The `not_null` test of `id` is written `dbt.not_null`, and the project's own `test_not_null`
    macro is the one dbt resolved first for it; the warned `not_null` test is strict and that
    macro overrides it; and the `relationships` test of `customer_id` points to the literal
    relation `analytics.some_table`, so the tested node is its only dependency and its one ref.
    """
    nodes = manifest["nodes"]
    project_macro = "macro.cases.test_not_null"
    qualified = nodes[_TEST_KEYS["id"]]
    qualified["test_metadata"]["namespace"] = "dbt"
    qualified["depends_on"]["macros"] = [project_macro]
    overridden = nodes[_TEST_KEYS["warned"]]
    overridden["config"]["severity"] = "error"
    overridden["depends_on"]["macros"] = [project_macro]
    literal = nodes[_TEST_KEYS["customer"]]
    literal["test_metadata"]["kwargs"]["to"] = "analytics.some_table"
    literal["depends_on"]["nodes"] = [EDITED_NODE]
    literal["refs"] = [
        {"name": "generic_tests", "package": None, "version": None}
    ]


# The cases of the vector whose node and tests are fixture nodes changed by an edit, which the name
# of the case states: the name, the fixture project, the manifest key of the node, the tables its
# compiled SQL names and the edit.
EDITED_CASES: Final[
    tuple[
        tuple[str, str, str, tuple[str, ...], Callable[[dict[str, Any]], None]],
        ...,
    ]
] = (
    (
        "namespaced_test_thresholds_extra_kwarg_and_loose_relationship",
        "cases",
        EDITED_NODE,
        (),
        edit_tests,
    ),
    (
        "composite_key_lengths_and_undeclared_columns",
        "cases",
        EDITED_NODE,
        (),
        edit_columns,
    ),
    (
        "model_level_expression_and_quoted_columns",
        "cases",
        EDITED_NODE,
        (),
        edit_column_names,
    ),
    (
        "dbt_tests_by_resolved_macro_and_unresolved_target",
        "cases",
        EDITED_NODE,
        (),
        edit_resolution,
    ),
)


def projects() -> list[str]:
    """Return the names of the fixture projects that have a manifest and golden files, in name order.

    The projects of `UNGOLDEN` have a manifest and no golden file.
    """
    return sorted(
        name
        for path in MANIFESTS.glob("*.json.gz")
        if (name := path.name.removesuffix(".json.gz")) not in UNGOLDEN
    )


def load_manifest(project: str) -> dict[str, Any]:
    """Return the manifest of the fixture project `project`."""
    with gzip.open(
        MANIFESTS / f"{project}.json.gz", "rt", encoding="utf-8"
    ) as stream:
        manifest: dict[str, Any] = json.load(stream)
    return manifest


def kept(manifest: Mapping[str, Any]) -> list[str]:
    """Return the manifest keys of the nodes of `manifest` that get a contract, in key order."""
    project = dbt.project_service(manifest, "")
    return [
        key
        for key, node in sorted(manifest["nodes"].items())
        if node["resource_type"] in dbt.RESOURCE_TYPES
        and dbt.skip_reason(node, project=project) is None
    ]


def golden(manifest: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return the golden contract of the node `key` of `manifest`."""
    node = manifest["nodes"][key]
    tests = dbt.tests_by_node(manifest).get(key, [])
    generated = dbt.generate(node, tests, manifest=manifest)
    stored = normalise_dbt(generated.document, reads=[])
    return {
        "generated": generated.document,
        "flags": list(generated.flags),
        "source_sha256": generated.source_sha256,
        "stored": stored,
        "contract_hash": contract_hash(stored),
    }


def golden_texts() -> dict[Path, str]:
    """Return the text of every golden file, by its path."""
    texts: dict[Path, str] = {}
    for project in projects():
        manifest = load_manifest(project)
        for key in kept(manifest):
            path = (
                GOLDEN / project / f"{dbt.unique_id(manifest['nodes'][key])}.json"
            )
            texts[path] = _dumps(golden(manifest, key))
    return texts


def _prune(value: Any, tree: Mapping[str, Any]) -> Any:
    """Return the fields of `value` that `tree`, a tree of `dbt.read_tree`, names.

    A `*` step keeps every key of a mapping, in its order, or every item of a list, and a `0`
    step over a list keeps its first item alone. A value that is not a mapping, or a list where
    the tree names other keys, is kept whole, since `generate` reads it whole.
    """
    if not tree:
        return value
    if "*" in tree:
        if isinstance(value, Mapping):
            return {key: _prune(value[key], tree["*"]) for key in value}
        if isinstance(value, list):
            return [_prune(item, tree["*"]) for item in value]
        return value
    if isinstance(value, list) and "0" in tree:
        return [_prune(item, tree["0"]) for item in value[:1]]
    if not isinstance(value, Mapping):
        return value
    return {key: _prune(value[key], tree[key]) for key in tree if key in value}


def pruned_node(node: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields of the manifest node `node` that `generate` reads, and its `unique_id`."""
    pruned: dict[str, Any] = _prune(node, dbt.read_tree("node"))
    return {"unique_id": node["unique_id"], **pruned}


def pruned_test(test: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields of the test node `test` that `generate` reads, and its `unique_id`."""
    pruned: dict[str, Any] = _prune(test, dbt.read_tree("test", test=test))
    return {"unique_id": test["unique_id"], **pruned}


def pruned_manifest(
    manifest: Mapping[str, Any], key: str, tests: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return the part of `manifest` that `generate` reads for the node `key` and its `tests`.

    It holds, under `nodes` and `sources`, each entry of a test's `depends_on.nodes` other than
    the test's `attached_node` and the node itself, with the fields that name it.
    """
    pruned: dict[str, dict[str, Any]] = {kind: {} for kind in TARGET_KINDS}
    for test in tests:
        attached = test.get("attached_node")
        for target in (test.get("depends_on") or {}).get("nodes") or []:
            if target in (attached, key):
                continue
            for kind in TARGET_KINDS:
                entry = (manifest.get(kind) or {}).get(target)
                if entry is not None:
                    pruned[kind][target] = _prune(entry, dbt.read_tree(kind))
    return pruned


def edited_manifest(
    project: str, edit: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    """Return the manifest of `project`, changed by `edit` when there is one."""
    manifest = load_manifest(project)
    if edit is not None:
        manifest = copy.deepcopy(manifest)
        edit(manifest)
    return manifest


def case_inputs(
    name: str,
    project: str,
    key: str,
    reads: Sequence[str],
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Return the inputs of the vector case `name`: the node `key` of `project`, pruned.

    `edit` changes the manifest before the node and its tests are read from it.
    """
    manifest = edited_manifest(project, edit)
    tests = dbt.tests_by_node(manifest).get(key, [])
    return {
        "name": name,
        "node": pruned_node(manifest["nodes"][key]),
        "tests": [pruned_test(test) for test in tests],
        "manifest": pruned_manifest(manifest, key, tests),
        "reads": list(reads),
    }


def contract_case(
    name: str,
    project: str,
    key: str,
    reads: Sequence[str],
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Return the vector case `name`, whose contract the library writes from its inputs."""
    case = case_inputs(name, project, key, reads, edit)
    generated = dbt.generate(
        case["node"], case["tests"], manifest=case["manifest"]
    )
    stored = normalise_dbt(generated.document, reads=case["reads"])
    return {
        **case,
        "generated": generated.document,
        "flags": list(generated.flags),
        "source_sha256": generated.source_sha256,
        "stored": stored,
        "contract_hash": contract_hash(stored),
    }


def issues_case(
    name: str, project: str, key: str, reads: Sequence[str]
) -> dict[str, Any]:
    """Return the vector case `name`, whose generated contract has issues."""
    case = case_inputs(name, project, key, reads)
    generated = dbt.generate(
        case["node"], case["tests"], manifest=case["manifest"]
    )
    return {
        **case,
        "source_sha256": generated.source_sha256,
        "issues": [
            {"path": issue.path, "reason": issue.reason}
            for issue in validate_dbt_contract(generated.document)
        ],
    }


def vector_cases() -> list[dict[str, Any]]:
    """Return the cases of the vector: those of `CONTRACT_CASES`, `ISSUES_CASES`, `EDITED_CASES`."""
    return [
        *(contract_case(*case) for case in CONTRACT_CASES),
        *(issues_case(*case) for case in ISSUES_CASES),
        *(contract_case(*case) for case in EDITED_CASES),
    ]


def vector_text() -> str:
    """Return the text of the vector file."""
    return _dumps(_ordered(vector_cases()), sort_keys=False)


def _ordered(value: Any, declared: bool = False) -> Any:
    """Return `value` with the keys of every mapping sorted, except a node's `columns`.

    `declared` says that `value` is a `columns` mapping, which keeps its key order.
    """
    if isinstance(value, Mapping):
        keys = list(value) if declared else sorted(value)
        return {
            key: _ordered(value[key], not declared and key == DECLARED_ORDER_KEY)
            for key in keys
        }
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


def _dumps(value: Any, *, sort_keys: bool = True) -> str:
    return (
        json.dumps(value, sort_keys=sort_keys, indent=1, ensure_ascii=True) + "\n"
    )


def main() -> int:
    """Write the golden files and the vector, and remove the golden files of no kept node."""
    texts = golden_texts()
    for path in sorted(GOLDEN.rglob("*.json")):
        if path not in texts:
            path.unlink()
            logger.info("removed %s", path.relative_to(GOLDEN))
    for path, text in texts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("ascii"))
    VECTOR.write_bytes(vector_text().encode("ascii"))
    logger.info(
        "wrote %d golden contracts and %d vector cases",
        len(texts),
        len(CONTRACT_CASES) + len(ISSUES_CASES) + len(EDITED_CASES),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    raise SystemExit(main())
