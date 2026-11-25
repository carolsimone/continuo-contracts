"""The bound demo estate: two bound dbt projects, their contracts stored and then resolved.

`core_bound` and `finance_bound` are the fixture projects `core` and `finance` with `meta.continuo`
bindings. `stored` generates each model and seed of a manifest with `dbt.generate` and stores it
with the tables its SQL reads, and `resolved` fills the stored contracts in against the demo
ontology.
"""

import copy

from pathlib import Path
from typing import Any

from dbt_fixtures import load_manifest

from continuo_contracts import dbt, ontology
from continuo_contracts.contract import normalise_dbt

DEMO_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "ontology" / "demo.yaml"
)
DEMO_TEXT = DEMO_PATH.read_text(encoding="utf-8")
DEMO = ontology.load(DEMO_TEXT)

PROJECTS = ("core_bound", "finance_bound")

# Small ontology files, as text, for the tests of the lite check and of the command. `BROKEN_TEXT`
# has two errors: a project of a domain that is not declared and a thing of an owner that is not
# one. `CORE_ONLY_TEXT` and `FINANCE_ONLY_TEXT` declare one project each and no thing.
BROKEN_TEXT = """\
principals:
  domains:
    core: {owner_team: core-data}
  projects:
    core: {domain: elsewhere}
entities:
  Thing:
    owner: nobody
    key: {thing_id: int}
"""
CORE_ONLY_TEXT = """\
principals:
  domains:
    core: {owner_team: core-data}
  projects:
    core: {domain: core, kind: dbt}
"""
FINANCE_ONLY_TEXT = """\
principals:
  domains:
    finance: {owner_team: finance-data}
  projects:
    finance: {domain: finance, kind: dbt}
"""

# The tables that each model's SQL names, as `<schema>.<table>`, written by hand from the `.sql`
# file of the model: this library parses no SQL, the release lists them. A table reached with
# `ref` is named by its relation, and a seed reads nothing.
READS: dict[str, dict[str, list[str]]] = {
    "core_bound": {
        "analytics.daily_transactions": [
            "analytics.seed_card_transactions",
            "analytics.fx_transactions_eur",
        ],
        "analytics.revenue_per_user": [
            "analytics.seed_users",
            "analytics.daily_transactions",
        ],
        "analytics.read_order": ["analytics.demo_orders_csv"],
        "analytics.read_order_v2": ["analytics.read_order"],
        "analytics.dbt_daily_kpis": ["analytics.py_daily_kpis"],
        "analytics.seed_users": [],
        "analytics.seed_card_transactions": [],
        "analytics.seed_fx_transactions": [],
    },
    "finance_bound": {
        "analytics.fx_transactions_eur": [
            "analytics.seed_fx_transactions",
            "analytics.seed_fx_rates_eur",
        ],
        "analytics.operational_costs_monthly": [
            "analytics.seed_operational_costs"
        ],
        "analytics.operational_cost_per_user": [
            "analytics.seed_users",
            "analytics.operational_costs_monthly",
        ],
        "analytics.operation_cost_per_user_view": [
            "analytics.operational_cost_per_user"
        ],
        "analytics.ltv_per_user": [
            "analytics.revenue_per_user",
            "analytics.marketing_cost_per_user",
            "analytics.operational_cost_per_user",
        ],
        "analytics.seed_fx_rates_eur": [],
        "analytics.seed_operational_costs": [],
    },
}


# The binding keys of `meta.continuo`: on a node, then on a column.
NODE_BINDING_KEYS = ("entity", "grain", "fixed", "filter")
COLUMN_BINDING_KEYS = ("property", "local", "from", "agg", "where", "period")


def _unbound(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `manifest` whose nodes and columns write no binding key."""
    reduced = copy.deepcopy(manifest)
    for node in reduced["nodes"].values():
        continuo = node.get("config", {}).get("meta", {}).get("continuo", {})
        for key in NODE_BINDING_KEYS:
            continuo.pop(key, None)
        for column in node.get("columns", {}).values():
            continuo = column.get("meta", {}).get("continuo", {})
            for key in COLUMN_BINDING_KEYS:
                continuo.pop(key, None)
    return reduced


def stored(project: str) -> dict[str, dict[str, Any]]:
    """Return the stored contract of each kept node of `project`, by contract id.

    Each is what `dbt.generate` writes for the node, stored with the tables of `READS`. A node
    that `READS` does not list raises `KeyError`, so that a new model is read on purpose.
    """
    return _stored(project, load_manifest(project))


def stored_without_bindings(project: str) -> dict[str, dict[str, Any]]:
    """Return what `stored` returns for `project` if no node or column wrote a binding key.

    The nodes and columns are the same: only the ten keys of `meta.continuo` that are bindings
    are taken out of the manifest before the contracts are generated.
    """
    return _stored(project, _unbound(load_manifest(project)))


def stored_from(
    project: str, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return what `stored` returns for `project`, from `manifest` in place of its own."""
    return _stored(project, manifest)


def _stored(project: str, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    named = dbt.manifest_project(manifest, "")
    joined = dbt.tests_by_node(manifest)
    contracts: dict[str, dict[str, Any]] = {}
    for key, node in sorted(manifest["nodes"].items()):
        if (
            node["resource_type"] not in dbt.RESOURCE_TYPES
            or dbt.skip_reason(node, project=named) is not None
        ):
            continue
        document = dbt.generate(
            node, joined.get(key, []), manifest=manifest
        ).document
        contracts[document["id"]] = normalise_dbt(
            document, reads=READS[project][document["id"]]
        )
    return contracts


def resolved(
    project: str, onto: ontology.Ontology = DEMO
) -> dict[str, dict[str, Any]]:
    """Return the stored contracts of `project` resolved against `onto`, by contract id."""
    return {
        contract_id: ontology.resolve(contract, onto)
        for contract_id, contract in stored(project).items()
    }


# What `resolve` fills in besides the links and bindings that `strip` removes: the `domain` of
# the contract and, of a column, its classification, logical type, key and semantic type.
FILLED_COLUMN_FIELDS = (
    "classification",
    "logicalType",
    "primaryKey",
    "primaryKeyPosition",
    "semanticType",
)


def without_filled(contract: dict[str, Any]) -> dict[str, Any]:
    """Return `contract` stripped of its ontology part and of the values `resolve` fills in.

    What is left is what a team wrote and what the ontology never touches, so the stored form
    of a contract and its resolved form are equal once both are reduced to it.
    """
    reduced = ontology.strip(contract)
    reduced.pop("domain", None)
    for table in reduced["schema"]:
        table["properties"] = [
            {k: v for k, v in column.items() if k not in FILLED_COLUMN_FIELDS}
            for column in table.get("properties", [])
        ]
    return reduced
