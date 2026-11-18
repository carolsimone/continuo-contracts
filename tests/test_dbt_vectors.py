"""The `dbt_contracts.json` vector: each case's node generated, stored and hashed."""

import hashlib
import importlib.util
import json

from importlib import resources
from pathlib import Path

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt
from continuo_contracts.contract import contract_hash, normalise_dbt
from continuo_contracts.validate import validate_dbt_contract
from continuo_contracts.vocabulary import continuo_values

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "write_dbt_goldens.py"
spec = importlib.util.spec_from_file_location("write_dbt_goldens", SCRIPT)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)

VECTORS = resources.files("continuo_contracts") / "vectors"


def _vectors(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


CASES = _vectors("dbt_contracts.json")
INPUT_FIELDS = {"name", "node", "tests", "manifest", "reads"}
CONTRACT_FIELDS = INPUT_FIELDS | {"generated", "flags", "stored", "contract_hash"}
ISSUES_FIELDS = INPUT_FIELDS | {"issues"}
# The case of each rule of the generator, with the manifest key of its node.
RULES = {
    "model_with_tests": "model.cases.generic_tests",
    "seed_with_column_types": "seed.cases.typed_seed",
    "dormant_model": "model.cases.dormant",
    "versioned_model": "model.cases.customer.v2",
    "ephemeral_model": "model.cases.eph",
    "snapshot": "snapshot.cases.orders_snapshot",
    "enforced_contract": "model.cases.enforced",
    "governed": "model.cases.governed",
    "sql_reads": "model.cases.governed",
}
# The case of each node whose generated contract is not valid, with the manifest key of its node.
ISSUES = {"bad_criticality": "model.cases.bad_criticality"}

# The SHA-256 of the file as it was written with its nine contract cases and no other: the text
# of those cases, a list that ends after the ninth, which no later case may change.
NINE_CASES = "fd2b2da40f72cccfe70a5701fdf5885160c12a2369d25de31b41203e411cae58"

# The SHA-256 of each vector file of 0.2.0, which no later change may move.
OLDER_VECTORS = {
    "canonical_json.json": "5a597cd1c06dc5fe7b666c79015433b2311fa3e896dc14d1ee6300def2a50c6d",
    "contracts.json": "069f87e5c4e7ed7110a41b02b1cd9b23c56a6b2a0e6cf2cb845f162fdac445c2",
    "hashing.json": "2f2270fd329f2380c4e8623225014222599b331766b3827d252fe1b2670cf329",
    "projection.json": "4cc7f7907b5c93e4830bdf5c5225cf2da2f3d8b4fca1b115ab24148eec2d1ea6",
    "sql_types.json": "7584965983a61ebc0ea5aa8efdf14ae12f7d76062891f727c7188f055577ed46",
    "strict_yaml.json": "0039839e3541a4c368cca601a9d4063ef2866490e57ca2404507aacf305c380b",
}


def _case(name):
    return next(case for case in CASES if case["name"] == name)


def _issues(document):
    """The `{path, reason}` of each issue that `validate_dbt_contract` finds in `document`."""
    return [
        {"path": issue.path, "reason": issue.reason}
        for issue in validate_dbt_contract(document)
    ]


def _root(doc):
    return continuo_values(doc["customProperties"])


CONTRACT_CASES = [case for case in CASES if "issues" not in case]
ISSUES_CASES = [case for case in CASES if "issues" in case]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_dbt_contract_vector(case):
    generated = dbt.generate(
        case["node"], case["tests"], manifest=case["manifest"]
    )
    # A contract case is valid, and an issues case has the issues that it lists.
    assert _issues(generated.document) == case.get("issues", [])
    if "issues" in case:
        return
    assert generated.document == case["generated"]
    assert list(generated.flags) == case["flags"]
    stored = normalise_dbt(generated.document, reads=case["reads"])
    assert stored == case["stored"]
    assert contract_hash(stored) == case["contract_hash"]


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=lambda c: c["name"])
def test_the_pinned_hash_is_the_hash_of_the_pinned_stored_form(case):
    assert contract_hash(case["stored"]) == case["contract_hash"]
    assert normalise_dbt(case["generated"], reads=case["reads"]) == case["stored"]
    assert _issues(case["stored"]) == []


def test_the_file_holds_one_case_for_each_rule_with_the_documented_fields():
    assert [case["name"] for case in CONTRACT_CASES] == list(RULES)
    for case in CONTRACT_CASES:
        assert set(case) == CONTRACT_FIELDS, case["name"]


def test_the_file_ends_with_the_cases_of_the_nodes_that_have_issues():
    assert [case["name"] for case in CASES] == [*RULES, *ISSUES]
    assert [case["name"] for case in ISSUES_CASES] == list(ISSUES)
    for case in ISSUES_CASES:
        assert set(case) == ISSUES_FIELDS, case["name"]
        assert case["reads"] == []
        for issue in case["issues"]:
            assert set(issue) == {"path", "reason"}


def test_the_file_is_the_writers_output():
    assert (VECTORS / "dbt_contracts.json").read_bytes() == (
        writer.vector_text().encode("ascii")
    )
    assert [(name, key) for name, _, key, _ in writer.CONTRACT_CASES] == list(
        RULES.items()
    )
    assert [(name, key) for name, _, key, _ in writer.ISSUES_CASES] == list(
        ISSUES.items()
    )


def test_the_nine_contract_cases_are_the_ones_of_the_first_version_of_the_file():
    first = CASES[: len(RULES)]
    assert [case["name"] for case in first] == list(RULES)
    text = json.dumps(first, indent=1, ensure_ascii=True) + "\n"
    assert hashlib.sha256(text.encode("ascii")).hexdigest() == NINE_CASES


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_the_pruned_inputs_generate_what_the_whole_manifest_generates(case):
    manifest = load_manifest("cases")
    key = {**RULES, **ISSUES}[case["name"]]
    tests = dbt.tests_by_node(manifest).get(key, [])
    whole = dbt.generate(manifest["nodes"][key], tests, manifest=manifest)
    pruned = dbt.generate(case["node"], case["tests"], manifest=case["manifest"])
    assert pruned == whole
    assert case["node"]["unique_id"] == key
    assert [test["unique_id"] for test in case["tests"]] == [
        test["unique_id"] for test in tests
    ]


def test_a_pruned_node_keeps_only_the_fields_generate_reads():
    node = _case("model_with_tests")["node"]
    assert "raw_code" not in node and "compiled_code" not in node
    read = {name.split(".")[0] for name in dbt._CONFIG_FIELDS}
    assert set(node["config"]) <= read | {"meta"}
    assert {"docs", "enabled", "lookback", "on_configuration_change"}.isdisjoint(
        node["config"]
    )
    for column in node["columns"].values():
        assert set(column) <= {"name", "description", "data_type", "tags", "meta"}
    for test in _case("model_with_tests")["tests"]:
        kwargs = test.get("test_metadata", {}).get("kwargs", {})
        assert "model" not in kwargs and "column_name" not in kwargs


def test_a_pruned_node_keeps_the_declared_column_order():
    node = _case("seed_with_column_types")["node"]
    assert list(node["columns"]) == ["id", "amount", "label"]
    properties = _case("seed_with_column_types")["stored"]["schema"][0][
        "properties"
    ]
    assert [column["name"] for column in properties] == ["id", "amount", "label"]


def test_the_pruned_manifest_holds_only_the_relationship_targets():
    assert _case("model_with_tests")["manifest"] == {
        "nodes": {
            "model.cases.customer.v2": {
                "name": "customer",
                "schema": "analytics",
                "version": 2,
            }
        },
        "sources": {
            "source.cases.raw.events": {
                "identifier": "events",
                "name": "events",
                "schema": "raw",
            }
        },
    }
    for case in CASES:
        if case["name"] != "model_with_tests":
            assert case["manifest"] == {"nodes": {}, "sources": {}}, case["name"]


def test_the_model_with_tests_flags_its_jinja_and_has_relationships():
    case = _case("model_with_tests")
    assert case["flags"] == ["jinja_in_test_arguments"]
    relationships = [
        rel["to"]
        for column in case["stored"]["schema"][0]["properties"]
        for rel in column.get("relationships", [])
    ]
    assert sorted(relationships) == ["analytics.customer.v2.id", "raw.events.id"]


def test_the_seed_writes_its_column_types():
    properties = _case("seed_with_column_types")["stored"]["schema"][0][
        "properties"
    ]
    amount = next(column for column in properties if column["name"] == "amount")
    assert amount["physicalType"] == "numeric(12,2)"
    assert amount["logicalType"] == "number"


def test_the_dormant_model_has_no_schedule():
    assert "schedule" not in _root(_case("dormant_model")["stored"])


def test_the_versioned_model_is_its_own_contract():
    stored = _case("versioned_model")["stored"]
    assert (stored["id"], stored["name"]) == (
        "analytics.customer.v2",
        "customer.v2",
    )
    assert stored["schema"][0]["physicalName"] == "customer_v2"


def test_the_ephemeral_model_is_an_ephemeral_schema_object():
    assert (
        _case("ephemeral_model")["stored"]["schema"][0]["physicalType"]
        == "ephemeral"
    )


def test_the_snapshot_writes_its_unique_key_in_both_properties():
    root = _root(_case("snapshot")["stored"])
    assert root["materialization"]["uniqueKey"] == "id"
    assert root["snapshot"]["uniqueKey"] == "id"
    names = [
        c["name"] for c in _case("snapshot")["stored"]["schema"][0]["properties"]
    ]
    assert names == [
        "dbt_scd_id",
        "dbt_updated_at",
        "valid_from",
        "dbt_valid_to",
        "dbt_is_deleted",
    ]


def test_the_enforced_contract_says_so():
    assert _root(_case("enforced_contract")["stored"])["contractEnforced"] is True


def test_the_governed_model_carries_its_governance():
    stored = _case("governed")["stored"]
    root = _root(stored)
    assert root["access"] == "private"
    assert root["group"] == "finance_team"
    assert root["freshnessBuildAfter"] == {"count": 4, "period": "hour"}
    assert root["timeoutMinutes"] == 90
    assert {role["role"] for role in stored["roles"]} == {
        "core",
        "reporter",
        "analyst",
    }
    assert continuo_values(stored["schema"][0]["customProperties"]) == {
        "reads": ["analytics.raw_events"]
    }


def test_the_sql_reads_join_the_declared_reads_once_in_lower_case():
    case, governed = _case("sql_reads"), _case("governed")
    assert case["reads"] == ["Analytics.Orders", "analytics.orders", "raw.events"]
    assert case["generated"] == governed["generated"]
    assert continuo_values(case["stored"]["schema"][0]["customProperties"]) == {
        "reads": ["analytics.orders", "analytics.raw_events", "raw.events"]
    }
    assert case["contract_hash"] != governed["contract_hash"]


def test_the_hashes_of_the_cases_differ():
    hashes = [case["contract_hash"] for case in CONTRACT_CASES]
    assert len(set(hashes)) == len(hashes)


def test_the_bad_criticality_case_has_the_one_issue_of_its_criticality():
    case = _case("bad_criticality")
    assert case["node"]["config"]["meta"]["criticality"] == "high"
    generated = dbt.generate(
        case["node"], case["tests"], manifest=case["manifest"]
    )
    properties = generated.document["customProperties"]
    index = next(
        i
        for i, item in enumerate(properties)
        if item["property"] == "criticality"
    )
    assert properties[index]["value"] == "high"
    assert case["issues"] == [
        {"path": f"customProperties[{index}].value", "reason": "value"}
    ]
    assert case["issues"] == [
        {"path": "customProperties[3].value", "reason": "value"}
    ]


def test_the_generated_contract_of_every_contract_case_is_valid():
    for case in CONTRACT_CASES:
        assert _issues(case["generated"]) == [], case["name"]


def test_no_older_vector_moved():
    for name, digest in OLDER_VECTORS.items():
        assert (
            hashlib.sha256((VECTORS / name).read_bytes()).hexdigest() == digest
        ), name
    names = {
        entry.name for entry in VECTORS.iterdir() if entry.name.endswith(".json")
    }
    assert names == {*OLDER_VECTORS, "dbt_contracts.json"}
