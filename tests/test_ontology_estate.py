"""The bound demo estate: core and finance from two bound dbt manifests, checked as one.

Each project is generated from its fixture manifest, stored with the tables its SQL reads (written
by hand in `ontology_fixtures.READS`) and resolved against `tests/fixtures/ontology/demo.yaml`,
which declares both projects. The two are checked together with every release rule. The list of
findings the estate gives is written down by hand below, a line for each with its reason; a
finding that is not in it is a defect to look into, not a line to add.
"""

import copy
import json
import re

from pathlib import Path

import pytest

from dbt_fixtures import load_manifest
from ontology_fixtures import (
    DEMO,
    PROJECTS,
    READS,
    resolved,
    stored,
    stored_from,
    stored_without_bindings,
    without_filled,
)

from continuo_contracts import dbt, ontology, yaml_strict
from continuo_contracts.contract import contract_hash, normalise_python
from continuo_contracts.ontology_check import (
    RELEASE_RULES,
    check,
    ontology_findings,
)
from continuo_contracts.validate import validate_python_contract

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "tests" / "fixtures" / "dbt" / "projects"
GOLDEN = (
    ROOT
    / "tests"
    / "fixtures"
    / "ontology"
    / "golden"
    / "analytics.fx_transactions_eur.json"
)
PY_DEMO = (
    ROOT
    / "tests"
    / "fixtures"
    / "demo"
    / "service-py"
    / "contracts"
    / "py_daily_kpis.odcs.yaml"
)


def estate(*projects):
    """Return the resolved contracts of `projects`, or of both, in the order of their ids."""
    contracts = {}
    for project in projects or PROJECTS:
        contracts.update(resolved(project))
    return [contracts[contract_id] for contract_id in sorted(contracts)]


def estate_findings(*projects):
    return check(DEMO, estate(*projects), rules=RELEASE_RULES)


# The findings other than information that core and finance give together, sorted. Each line is
# one finding and says why the estate has it.
EXPECTED = sorted(
    [
        # finance's operational_cost_per_user reads core's seed_users, and core grants it to no one:
        # the one error of the estate.
        ("error", "access", "analytics.operational_cost_per_user"),
        # core's dbt_daily_kpis reads analytics.py_daily_kpis, a table of service-py, which is not
        # in the estate.
        ("warning", "access", "analytics.dbt_daily_kpis"),
        # core's read_order reads analytics.demo_orders_csv, a table of service-py.
        ("warning", "access", "analytics.read_order"),
        # finance's ltv_per_user reads analytics.marketing_cost_per_user, a table of marketing,
        # which is not in the estate.
        ("warning", "access", "analytics.ltv_per_user"),
        # fx_transactions_eur holds FxRate.rate_to_eur, which Transaction does not reach through
        # a reference: the join on currency and date is the model's own.
        ("warning", "contract", "analytics.fx_transactions_eur"),
        # Transaction is born in kafka.card_events, which no contract of the estate holds: a
        # warning, since the order of its copies against a birth copy cannot be checked.
        ("warning", "order", "Transaction.born_in"),
    ]
)


def test_the_bound_demo_estate_reports_exactly_the_known_findings():
    got = sorted(
        (f.severity, f.rule, f.subject)
        for f in estate_findings()
        if f.severity != "info"
    )

    assert got == EXPECTED


def test_every_error_of_the_estate_names_its_contract():
    for finding in estate_findings():
        if finding.severity == "error":
            assert finding.contract_id == finding.subject


def test_the_access_error_names_the_table_that_has_no_grant():
    [error] = [f for f in estate_findings() if f.severity == "error"]

    assert error.contract_id == "analytics.operational_cost_per_user"
    assert "analytics.seed_users" in error.message
    assert "grant" in error.message


def test_the_demo_ontology_itself_has_no_finding():
    assert ontology_findings(DEMO) == []


def test_the_estate_holds_every_model_and_seed_of_both_projects():
    ids = {c["id"] for c in estate()}

    assert ids == {
        contract_id for reads in READS.values() for contract_id in reads
    }
    assert len(ids) == 15


def _reads_of(contract):
    """Return the tables the stored `contract` says its SQL reads."""
    for item in contract["schema"][0].get("customProperties", []):
        if item["property"] == "reads":
            return item["value"]
    return []


@pytest.mark.parametrize("project", PROJECTS)
def test_the_reads_of_a_model_are_tables_its_sql_names(project):
    # The reads are written by hand from the SQL. Each table of a model has its name in the
    # model's file, and every model reads something.
    for node in load_manifest(project)["nodes"].values():
        if node["resource_type"] != "model":
            continue
        path = PROJECTS_DIR / project / node["original_file_path"]
        sql = path.read_text(encoding="utf-8")
        reads = READS[project][dbt.unique_id(node)]
        assert reads, node["unique_id"]
        for table in reads:
            name = table.split(".", 1)[1]
            assert re.search(rf"\b{name}\b", sql), (node["unique_id"], table)


@pytest.mark.parametrize("project", PROJECTS)
def test_a_stored_contract_keeps_the_reads_it_was_given(project):
    for contract_id, contract in stored(project).items():
        assert _reads_of(contract) == sorted(READS[project][contract_id])


# What `strip` gives back: the stored form of a release that runs no ontology.


@pytest.mark.parametrize("project", PROJECTS)
def test_strip_of_a_bound_stored_form_is_the_stored_form_with_no_binding_key(
    project,
):
    bound = stored(project)
    unbound = stored_without_bindings(project)

    assert sorted(bound) == sorted(unbound)
    for contract_id, contract in bound.items():
        assert ontology.has_bindings(contract), contract_id
        assert not ontology.has_bindings(unbound[contract_id]), contract_id
        stripped = ontology.strip(contract)
        assert stripped == unbound[contract_id], contract_id
        assert contract_hash(stripped) == contract_hash(unbound[contract_id])
        # Hashed as written, without `strip`, the bound form hashes differently.
        assert contract_hash(contract) != contract_hash(unbound[contract_id])


# Values that the canonical form drops, in contracts that validate accepts.


def _emptied_dbt(project):
    """The resolved contracts of `project`, generated from a manifest full of empty values.

    Each node and column has an empty description and each node an empty list of tags. A node
    that grants nothing and names no reader has an empty mapping of grants and an empty list of
    readers. The stored form leaves each of them out.
    """
    manifest = copy.deepcopy(load_manifest(project))
    for node in manifest["nodes"].values():
        node["description"] = ""
        node["tags"] = []
        node["config"].setdefault("grants", {})
        node["config"].setdefault("meta", {}).setdefault(
            "continuo", {}
        ).setdefault("readers", [])
        for column in node.get("columns", {}).values():
            column["description"] = ""
    return {
        contract_id: ontology.resolve(contract, DEMO)
        for contract_id, contract in stored_from(project, manifest).items()
    }


def _emptied_python():
    """A bound Python contract of `service-py` that writes values the stored form leaves out.

    It has an empty reader role, which is dropped and leaves `{access: read}`, an empty
    description, empty tags and quality, empty `fixed` and `filter` mappings, and columns with an
    empty description and classification. It is valid, and its one finding is the reader that has
    no name.
    """
    doc = yaml_strict.load(PY_DEMO.read_text(encoding="utf-8"))
    doc.update(id="analytics.py_empties", name="py_empties")
    doc["roles"] = [{"role": "", "access": "read"}]
    doc["tags"] = []
    doc["description"]["usage"] = ""
    table = doc["schema"][0]
    table.update(name="py_empties", quality=[])
    table["customProperties"] = [
        {"vendor": "continuo", "property": "grain", "value": ["day"]},
        {"vendor": "continuo", "property": "fixed", "value": {}},
        {"vendor": "continuo", "property": "filter", "value": {}},
    ]
    for column in table["properties"]:
        column.update(description="", classification="", tags=[])
        column["customProperties"] = [
            {"vendor": "continuo", "property": "local", "value": True}
        ]
    assert (
        validate_python_contract(
            doc, path="contracts/py_empties.odcs.yaml", project="service-py"
        )
        == []
    )
    stored_form = normalise_python(doc, project="service-py", reads=[])
    assert stored_form["roles"] == [{"access": "read"}]
    return ontology.resolve(stored_form, DEMO)


def test_empty_values_the_stored_form_drops_abort_no_rule():
    contracts = [
        *_emptied_dbt("core_bound").values(),
        *_emptied_dbt("finance_bound").values(),
        _emptied_python(),
    ]

    findings = check(DEMO, contracts, rules=RELEASE_RULES)

    got = sorted(
        (f.severity, f.rule, f.subject) for f in findings if f.severity != "info"
    )
    # The dbt nodes give the findings they give with their values written; the Python contract
    # adds the reader with no name, the one finding about it.
    assert got == sorted(
        [*EXPECTED, ("error", "contract", "analytics.py_empties")]
    )
    [reader] = [
        f
        for f in findings
        if f.subject == "analytics.py_empties" and f.severity != "info"
    ]
    assert reader.message == "reader '' is not a declared domain"


@pytest.mark.parametrize("rule", RELEASE_RULES)
def test_each_rule_runs_over_contracts_with_empty_values(rule):
    contracts = [
        *_emptied_dbt("core_bound").values(),
        _emptied_python(),
    ]

    check(DEMO, contracts, rules=(rule,))


# Where the contracts come after the models they read.


def test_the_copies_are_ordered_after_the_copies_they_read():
    [layered] = [
        f
        for f in estate_findings()
        if f.rule == "order" and f.subject == "copy graph"
    ]
    position = {
        contract_id: index
        for index, layer in enumerate(layered.message.split(" | "))
        for contract_id in layer.split(": ", 1)[1].split(", ")
    }

    # A chain through both projects: finance's transactions, core's union of them and its
    # revenue, and finance's unit economics.
    chain = [
        "analytics.seed_fx_transactions",
        "analytics.fx_transactions_eur",
        "analytics.daily_transactions",
        "analytics.revenue_per_user",
        "analytics.ltv_per_user",
    ]
    assert [position[c] for c in chain] == sorted(position[c] for c in chain)
    assert len({position[c] for c in chain}) == len(chain)
    assert set(position) == {c["id"] for c in estate()}
    for contract in estate():
        for read in _reads_of(contract):
            if read in position:
                assert position[read] < position[contract["id"]], (
                    contract["id"],
                    read,
                )


# The findings do not depend on the order of the contracts.


@pytest.mark.parametrize(
    "projects",
    [("core_bound",), ("finance_bound",), PROJECTS],
    ids=["core", "finance", "core and finance"],
)
def test_findings_do_not_depend_on_contract_order(projects):
    contracts = estate(*projects)

    forward = check(DEMO, contracts, rules=RELEASE_RULES)
    reverse = check(DEMO, contracts[::-1], rules=RELEASE_RULES)

    assert forward
    assert reverse == forward


# The golden: fx_transactions_eur with the ontology switch on.


def _golden_text(document):
    return (
        json.dumps(document, sort_keys=True, indent=1, ensure_ascii=True) + "\n"
    )


def _fx_transactions_eur():
    stored_form = stored("finance_bound")["analytics.fx_transactions_eur"]
    resolved_form = ontology.resolve(stored_form, DEMO)
    return {
        "stored": stored_form,
        "resolved": resolved_form,
        "contract_hash": contract_hash(resolved_form),
    }


def test_fx_transactions_eur_with_the_switch_on_matches_the_golden():
    assert json.loads(GOLDEN.read_text("ascii")) == _fx_transactions_eur()


def test_the_golden_file_is_ascii_sorted_json():
    assert GOLDEN.read_bytes() == _golden_text(_fx_transactions_eur()).encode(
        "ascii"
    )


def _column(resolved_form, name):
    return next(
        column
        for column in resolved_form["schema"][0]["properties"]
        if column["name"] == name
    )


def _custom(element):
    return {
        item["property"]: item["value"]
        for item in element.get("customProperties", [])
    }


def _urls(element):
    return [item["url"] for item in element.get("authoritativeDefinitions", [])]


def test_the_golden_holds_what_the_ontology_fills_in():
    golden = json.loads(GOLDEN.read_text("ascii"))
    form, stored_form = golden["resolved"], golden["stored"]
    table = form["schema"][0]

    # The domain of the project, and the thing the table is a copy of, with the part of its key
    # that the table holds as a constant.
    assert "domain" not in stored_form
    assert form["domain"] == "finance"
    assert _urls(table) == ["continuo-ontology:Transaction"]
    assert _custom(table)["entity"] == "Transaction"
    assert _custom(table)["filter"] == {"source": "fx"}

    # A column with no binding holds the property that has its name, with its link and its type.
    for name, logical in (
        ("user_id", "integer"),
        ("created_at", "timestamp"),
        ("amount_eur", "number"),
        ("fee_amount_eur", "number"),
    ):
        column = _column(form, name)
        assert _custom(column)["property"] == f"Transaction.{name}"
        assert _urls(column) == [f"continuo-ontology:Transaction.{name}"]
        assert column["logicalType"] == logical
    transaction_id = _column(form, "transaction_id")
    assert _custom(transaction_id)["property"] == "Transaction.transaction_id"
    assert transaction_id["logicalType"] == "integer"
    # The key dbt infers from the tests is the only one, so it holds no position.
    assert transaction_id["primaryKey"] is True
    assert "primaryKeyPosition" not in transaction_id

    # rate_to_eur is a property of FxRate, bound by the team.
    rate = _column(form, "rate_to_eur")
    assert _custom(rate)["property"] == "FxRate.rate_to_eur"
    assert _urls(rate) == ["continuo-ontology:FxRate.rate_to_eur"]
    assert rate["logicalType"] == "number"

    # A local column is only this table's: the ontology says nothing of it.
    for name in ("amount", "fee_amount", "currency_from", "currency_to", "rate"):
        column = _column(form, name)
        assert _custom(column) == {"local": True}
        assert "authoritativeDefinitions" not in column


def test_the_golden_differs_from_the_stored_form_only_by_what_the_ontology_fills_in():
    golden = json.loads(GOLDEN.read_text("ascii"))

    assert not ontology.has_bindings(ontology.strip(golden["resolved"]))
    assert without_filled(golden["resolved"]) == without_filled(golden["stored"])
    assert golden["contract_hash"] == contract_hash(golden["resolved"])
    assert golden["contract_hash"] != contract_hash(golden["stored"])
