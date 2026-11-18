"""The check of a dbt contract: the ODCS schema first, then continuo's rules for a dbt node."""

import copy
import hashlib
import json

from importlib import resources
from pathlib import Path

import pytest

from continuo_contracts import dbt, yaml_strict
from continuo_contracts.validate import (
    REASONS,
    dbt_rule_issues,
    schema_issues,
    validate_dbt_contract,
    validate_python_contract,
)
from continuo_contracts.vocabulary import PROPERTIES

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "dbt" / "golden"
VECTORS = resources.files("continuo_contracts") / "vectors"
BAD = {"analytics.bad_criticality", "analytics.long_tag"}


def _golden(project, name):
    return json.loads((GOLDEN / project / f"{name}.json").read_text("ascii"))


def _doc(name="analytics.generic_tests", *, form="generated"):
    """A copy of the generated (or stored) contract of a golden node of the `cases` project."""
    return copy.deepcopy(_golden("cases", name)[form])


def _found(issues):
    return [(issue.path, issue.reason) for issue in issues]


def _only(issues):
    """The one issue of a check, which must have exactly one."""
    assert len(issues) == 1, [str(issue) for issue in issues]
    return issues[0]


def _continuo(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def _index(doc, name):
    """The `customProperties` index of a continuo property of the contract."""
    for index, item in enumerate(doc["customProperties"]):
        if item["vendor"] == "continuo" and item["property"] == name:
            return index
    raise KeyError(name)


def _set(doc, name, value):
    doc["customProperties"][_index(doc, name)]["value"] = value


def _drop(doc, name):
    del doc["customProperties"][_index(doc, name)]


# The goldens.


def test_every_golden_generated_contract_is_valid_except_the_two_bad_cases():
    paths = sorted(GOLDEN.rglob("*.json"))
    assert {path.stem for path in paths} >= BAD
    for path in paths:
        issues = validate_dbt_contract(json.loads(path.read_text())["generated"])
        assert bool(issues) == (path.stem in BAD), path


def test_every_golden_stored_contract_is_valid_except_the_two_bad_cases():
    for path in sorted(GOLDEN.rglob("*.json")):
        issues = validate_dbt_contract(json.loads(path.read_text())["stored"])
        assert bool(issues) == (path.stem in BAD), path


def test_a_golden_that_validates_has_no_issue_from_either_stage():
    doc = _doc("analytics.governed")
    assert schema_issues(doc) == []
    assert dbt_rule_issues(doc) == []
    assert validate_dbt_contract(doc) == []


# The value rules.


def test_a_criticality_outside_the_three_is_a_value_issue():
    doc = _golden("cases", "analytics.bad_criticality")["generated"]
    index = next(
        i
        for i, p in enumerate(doc["customProperties"])
        if p["property"] == "criticality"
    )
    assert [(i.reason, i.path) for i in validate_dbt_contract(doc)] == [
        ("value", f"customProperties[{index}].value")
    ]


def test_a_criticality_message_names_the_value_and_the_three():
    issue = _only(
        validate_dbt_contract(
            _golden("cases", "analytics.bad_criticality")["generated"]
        )
    )
    assert "`criticality` is 'high'" in issue.message
    assert "REGULATORY, CORE, SECONDARY" in issue.message


@pytest.mark.parametrize("value", ["REGULATORY", "CORE", "SECONDARY"])
def test_each_of_the_three_criticalities_is_accepted(value):
    doc = _doc()
    _set(doc, "criticality", value)
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize("value", ["core", "", None, 3, ["CORE"]])
def test_any_other_criticality_is_a_value_issue(value):
    doc = _doc()
    _set(doc, "criticality", value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'criticality')}].value",
        "value",
    )


def test_a_schedule_over_fifty_characters_is_a_value_issue():
    doc = _golden("cases", "analytics.long_tag")["generated"]
    index = _index(doc, "schedule")
    assert len(doc["customProperties"][index]["value"]) == 51
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{index}].value",
        "value",
    )
    assert "at most 50 characters" in issue.message


def test_a_schedule_of_exactly_fifty_characters_is_accepted():
    doc = _golden("cases", "analytics.long_tag")["generated"]
    _set(doc, "schedule", "s" * 50)
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize(
    "value", ["", "   ", "two words", " pad", "x\ty", 5, None]
)
def test_a_schedule_that_is_not_a_name_is_a_value_issue(value):
    doc = _doc()
    _set(doc, "schedule", value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'schedule')}].value",
        "value",
    )


def test_a_dormant_node_writes_no_schedule_and_is_valid():
    doc = _doc("analytics.dormant")
    assert "schedule" not in {p["property"] for p in doc["customProperties"]}
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize("value", [1, 90, 10080])
def test_a_timeout_from_one_to_10080_whole_minutes_is_accepted(value):
    doc = _doc("analytics.governed")
    _set(doc, "timeoutMinutes", value)
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize("value", [0, -5, 10081, 1.5, True, "90", None, [1]])
def test_any_other_timeout_is_a_value_issue(value):
    doc = _doc("analytics.governed")
    _set(doc, "timeoutMinutes", value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'timeoutMinutes')}].value",
        "value",
    )
    assert "from 1 to 10080" in issue.message


@pytest.mark.parametrize("name", ["service", "filePath"])
@pytest.mark.parametrize("value", ["", "   ", 7, None, ["a"], {"a": 1}])
def test_a_service_or_a_file_path_that_is_not_text_is_a_value_issue(name, value):
    doc = _doc()
    _set(doc, name, value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, name)}].value",
        "value",
    )
    assert f"`{name}` is {value!r}" in issue.message


def test_a_service_and_a_file_path_with_text_are_accepted_whatever_they_say():
    doc = _doc()
    _set(doc, "service", "some-service")
    _set(doc, "filePath", "models\\sub dir\\a.sql")
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize(
    "value", [7, "table", [], None, [{"materialized": "table"}]]
)
def test_a_materialization_that_is_not_a_mapping_is_a_value_issue(value):
    doc = _doc()
    _set(doc, "materialization", value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'materialization')}].value",
        "value",
    )
    assert "`materialization` is" in issue.message


@pytest.mark.parametrize(
    "value", [{}, {"materialized": ""}, {"materialized": "  "}]
)
@pytest.mark.parametrize("extra", [{}, {"uniqueKey": "id"}])
def test_a_materialization_with_no_materialized_text_is_a_value_issue_inside(
    value, extra
):
    doc = _doc()
    _set(doc, "materialization", {**value, **extra})
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'materialization')}].value.materialized",
        "value",
    )
    assert "`materialized` is" in issue.message


@pytest.mark.parametrize("value", [7, None, ["table"], {"a": 1}])
def test_a_materialized_that_is_not_text_is_a_value_issue_inside(value):
    doc = _doc()
    _set(doc, "materialization", {"materialized": value})
    issue = _only(validate_dbt_contract(doc))
    assert issue.path.endswith(".value.materialized")
    assert issue.reason == "value"


@pytest.mark.parametrize(
    "materialized", ["table", "view", "incremental", "ephemeral", "other"]
)
def test_any_materialized_with_text_is_accepted(materialized):
    doc = _doc()
    _set(
        doc, "materialization", {"materialized": materialized, "uniqueKey": "id"}
    )
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize(
    ("name", "form"),
    [
        ("analytics.generic_tests", "dbt-model"),
        ("analytics.typed_seed", "dbt-seed"),
        ("analytics.orders_snapshot", "dbt-snapshot"),
    ],
)
def test_the_three_dbt_kinds_are_accepted(name, form):
    doc = _doc(name)
    assert doc["customProperties"][_index(doc, "kind")]["value"] == form
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize(
    "value", ["python-node", "dbt-view", "DBT-MODEL", "", None, 3, ["dbt-model"]]
)
def test_a_kind_that_is_not_a_dbt_kind_is_a_value_issue(value):
    doc = _doc()
    _set(doc, "kind", value)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{_index(doc, 'kind')}].value",
        "value",
    )
    assert "dbt-model, dbt-seed or dbt-snapshot" in issue.message


# The properties that a dbt contract writes.


@pytest.mark.parametrize(
    "name", ["service", "kind", "filePath", "materialization"]
)
def test_a_missing_needed_property_is_a_missing_field_at_the_custom_properties(
    name,
):
    doc = _doc()
    _drop(doc, name)
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("customProperties", "missing_field")
    assert f"`{name}`" in issue.message


def test_a_contract_with_no_custom_properties_misses_the_four_it_needs():
    doc = _doc()
    del doc["customProperties"]
    issues = validate_dbt_contract(doc)
    assert _found(issues) == [("customProperties", "missing_field")] * 4
    for name in ("service", "kind", "filePath", "materialization"):
        assert sum(f"`{name}`" in i.message for i in issues) == 1, name


def test_a_service_of_another_vendor_is_not_the_continuo_service():
    doc = _doc()
    doc["customProperties"][_index(doc, "service")]["vendor"] = "other"
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("customProperties", "missing_field")


def test_a_contract_does_not_need_a_schedule_a_criticality_or_a_timeout():
    doc = _doc("analytics.dormant")
    _drop(doc, "criticality")
    names = {p["property"] for p in doc["customProperties"]}
    assert not names & {"schedule", "criticality", "timeoutMinutes"}
    assert validate_dbt_contract(doc) == []


@pytest.mark.parametrize(
    "name", sorted(n for n, p in PROPERTIES.items() if p.dbt == "not_used")
)
def test_a_python_only_property_is_refused_on_a_dbt_contract(name):
    doc = _doc()
    doc["customProperties"].append(_continuo(name, "x"))
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{len(doc['customProperties']) - 1}]",
        "refused_property",
    )
    assert issue.message == f"{name} is not a dbt contract property"


def test_the_schema_object_refuses_a_python_only_property_too():
    doc = _doc()
    doc["schema"][0].setdefault("customProperties", []).append(
        _continuo("provenance", {})
    )
    issue = _only(validate_dbt_contract(doc))
    where = len(doc["schema"][0]["customProperties"]) - 1
    assert (issue.path, issue.reason) == (
        f"schema[0].customProperties[{where}]",
        "refused_property",
    )
    assert issue.message == "provenance is not a dbt contract property"


def test_a_property_the_vocabulary_does_not_know_is_an_unknown_property():
    doc = _doc()
    doc["customProperties"].append(_continuo("nonsense", 1))
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{len(doc['customProperties']) - 1}]",
        "unknown_property",
    )
    assert "`nonsense`" in issue.message


def test_a_property_of_another_vendor_is_never_looked_at():
    doc = _doc()
    doc["customProperties"].append(
        {"vendor": "other", "property": "script", "value": "x"}
    )
    doc["customProperties"].append({"property": "nonsense", "value": 1})
    assert validate_dbt_contract(doc) == []


def test_a_property_that_sits_on_another_object_is_refused():
    doc = _doc()
    doc["customProperties"].append(_continuo("reads", ["a.b"]))
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == (
        f"customProperties[{len(doc['customProperties']) - 1}]",
        "refused_property",
    )
    assert (
        "`reads` sits on the schema object, not on the contract" in issue.message
    )


def test_a_property_on_a_column_is_on_the_wrong_object():
    doc = _doc()
    column = doc["schema"][0]["properties"][0]
    column.setdefault("customProperties", []).append(_continuo("schedule", "x"))
    issue = _only(validate_dbt_contract(doc))
    assert issue.reason == "refused_property"
    assert issue.path.endswith(".customProperties[0]")
    assert "not on this object" in issue.message


def test_the_reads_of_the_schema_object_are_accepted():
    doc = _doc("analytics.governed")
    reads = doc["schema"][0]["customProperties"]
    assert [p["property"] for p in reads] == ["reads"]
    assert validate_dbt_contract(doc) == []


def test_a_quality_property_sits_on_a_quality_rule_and_not_on_the_contract():
    doc = _doc()
    doc["customProperties"].append(_continuo("where", "x = 1"))
    issue = _only(validate_dbt_contract(doc))
    assert issue.reason == "refused_property"
    assert "`where` sits on a quality rule, not on the contract" in issue.message


def test_a_property_written_twice_in_one_list_is_a_value_issue_at_the_repeat():
    doc = _doc()
    first = _index(doc, "schedule")
    doc["customProperties"].append(_continuo("schedule", "another"))
    issue = _only(validate_dbt_contract(doc))
    last = len(doc["customProperties"]) - 1
    assert (issue.path, issue.reason) == (f"customProperties[{last}]", "value")
    assert f"customProperties[{first}]" in issue.message


def test_a_repeated_property_with_a_bad_value_is_checked_once_at_the_first():
    doc = _doc()
    _set(doc, "criticality", "high")
    doc["customProperties"].append(_continuo("criticality", "CORE"))
    found = _found(validate_dbt_contract(doc))
    assert found == [
        (f"customProperties[{_index(doc, 'criticality')}].value", "value"),
        (f"customProperties[{len(doc['customProperties']) - 1}]", "value"),
    ]


def test_the_service_value_is_not_compared_with_a_release_service():
    doc = _doc()
    _set(doc, "service", "whatever-service")
    assert validate_dbt_contract(doc) == []


# The identity.


def test_an_id_that_does_not_end_with_the_table_is_an_identity_issue():
    doc = _doc()
    doc["id"] = "analytics.other"
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("id", "identity")
    assert "'analytics.generic_tests'" in issue.message


def test_an_id_must_be_in_lower_case():
    doc = _doc()
    doc["id"] = "Analytics.generic_tests"
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("id", "identity")
    assert "'analytics.generic_tests'" in issue.message


def test_an_id_with_an_upper_case_table_and_a_lower_case_name_is_two_issues():
    doc = _doc()
    doc["id"] = "analytics.Generic_Tests"
    assert _found(validate_dbt_contract(doc)) == [
        ("id", "identity"),
        ("id", "identity"),
    ]


def test_an_id_with_no_schema_part_is_an_identity_issue():
    doc = _doc()
    doc["id"] = ".generic_tests"
    assert _found(validate_dbt_contract(doc)) == [("id", "identity")]
    doc["id"] = "generic_tests"
    assert _found(validate_dbt_contract(doc)) == [("id", "identity")]


def test_the_table_is_compared_in_lower_case_with_the_id():
    doc = _doc()
    doc["schema"][0]["name"] = "Generic_Tests"
    assert validate_dbt_contract(doc) == []


def test_the_id_of_a_versioned_model_ends_with_its_versioned_table():
    doc = _doc("analytics.customer.v2")
    assert (doc["id"], doc["schema"][0]["name"]) == (
        "analytics.customer.v2",
        "customer.v2",
    )
    assert validate_dbt_contract(doc) == []
    doc["id"] = "analytics.customer"
    assert _found(validate_dbt_contract(doc)) == [("id", "identity")]


def test_a_contract_with_two_schema_objects_is_an_identity_issue_at_schema():
    doc = _doc()
    doc["schema"].append(copy.deepcopy(doc["schema"][0]))
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("schema", "identity")
    assert "exactly one schema object" in issue.message


def test_a_contract_with_no_schema_object_is_an_identity_issue_at_schema():
    doc = _doc()
    del doc["schema"]
    assert _found(validate_dbt_contract(doc)) == [("schema", "identity")]
    doc["schema"] = []
    assert _found(validate_dbt_contract(doc)) == [("schema", "identity")]


def test_a_table_name_that_is_not_a_name_is_an_identity_issue_at_the_name():
    doc = _doc()
    doc["schema"][0]["name"] = " generic_tests"
    assert _found(validate_dbt_contract(doc)) == [("schema[0].name", "identity")]
    doc["schema"][0]["name"] = "   "
    assert _found(validate_dbt_contract(doc)) == [("schema[0].name", "identity")]


def test_the_physical_name_may_differ_from_the_table():
    doc = _doc("analytics.customer.v2")
    assert doc["schema"][0]["physicalName"] == "customer_v2"
    assert validate_dbt_contract(doc) == []


# The envelope and the team.


def test_an_api_version_other_than_v320_is_an_api_version_issue():
    doc = _doc()
    doc["apiVersion"] = "v3.1.0"
    assert schema_issues(doc) == []
    issue = _only(validate_dbt_contract(doc))
    assert (issue.path, issue.reason) == ("apiVersion", "api_version")
    assert "'v3.1.0'" in issue.message


def test_a_team_with_no_name_is_a_missing_field_at_team_name():
    for team in ({}, {"name": ""}, {"name": "  "}, {"description": "x"}, []):
        doc = _doc()
        doc["team"] = team
        issue = _only(validate_dbt_contract(doc))
        assert (issue.path, issue.reason) == ("team.name", "missing_field"), team
        assert "a dbt contract names its team" in issue.message


def test_a_contract_with_no_team_is_a_missing_field_at_team_name():
    doc = _doc()
    del doc["team"]
    assert _found(validate_dbt_contract(doc)) == [("team.name", "missing_field")]


# The two stages.


def test_the_rules_run_only_when_the_schema_accepts_the_document():
    doc = _doc()
    del doc["id"]
    doc["customProperties"] = []
    found = validate_dbt_contract(doc)
    assert found == schema_issues(doc)
    assert {issue.reason for issue in found} == {"odcs_schema"}
    assert dbt_rule_issues(_doc() | {"customProperties": []}) != []


def test_a_document_that_is_not_a_mapping_gives_one_schema_issue():
    for doc in ([], "text", 3, None):
        found = validate_dbt_contract(doc)
        assert _found(found) == [("$", "odcs_schema")], doc


def test_the_result_of_a_valid_document_is_the_rule_issues_of_it():
    doc = _golden("cases", "analytics.bad_criticality")["generated"]
    assert schema_issues(doc) == []
    assert validate_dbt_contract(doc) == dbt_rule_issues(doc)


def test_the_issues_come_in_path_order_with_a_closed_reason():
    doc = _doc()
    del doc["team"]
    doc["id"] = "Analytics.other"
    _set(doc, "criticality", "high")
    doc["customProperties"].append(_continuo("script", "x"))
    issues = validate_dbt_contract(doc)
    assert [issue.path for issue in issues] == [
        "customProperties[3].value",
        f"customProperties[{len(doc['customProperties']) - 1}]",
        "id",
        "id",
        "team.name",
    ]
    assert {issue.reason for issue in issues} <= set(REASONS)


def test_the_rules_do_not_change_the_document():
    doc = _doc("analytics.governed")
    before = copy.deepcopy(doc)
    validate_dbt_contract(doc)
    assert doc == before


# Python validation does not move.


def _python_cases():
    text = (VECTORS / "contracts.json").read_text(encoding="utf-8")
    return json.loads(text)


def test_python_validation_is_unchanged():
    cases = _python_cases()
    assert cases
    for case in cases:
        try:
            doc = yaml_strict.load(case["text"])
        except yaml_strict.StrictYamlError:
            found = [{"path": "$", "reason": "strict_yaml"}]
        else:
            found = [
                {"path": issue.path, "reason": issue.reason}
                for issue in validate_python_contract(
                    doc, path=case["path"], service=case["service"]
                )
            ]
        assert found == case.get("issues", []), case["name"]
    digest = hashlib.sha256((VECTORS / "contracts.json").read_bytes()).hexdigest()
    assert digest == (
        "069f87e5c4e7ed7110a41b02b1cd9b23c56a6b2a0e6cf2cb845f162fdac445c2"
    )


def test_a_python_contract_is_not_a_dbt_contract_and_the_reverse():
    demo = (
        FIXTURES / "demo" / "service-py" / "contracts" / "py_daily_kpis.odcs.yaml"
    )
    python = yaml_strict.load(demo.read_text(encoding="utf-8"))
    assert (
        validate_python_contract(
            python, path="contracts/py_daily_kpis.odcs.yaml", service="service-py"
        )
        == []
    )
    reasons = {(i.path, i.reason) for i in validate_dbt_contract(python)}
    assert ("customProperties", "missing_field") in reasons
    assert any(reason == "refused_property" for _, reason in reasons)
    dbt_doc = _doc()
    assert validate_python_contract(
        dbt_doc, path="contracts/generic_tests.odcs.yaml", service=None
    )


# What `run_facts` reads is guaranteed by the check.

PROJECTS = sorted(path.name for path in GOLDEN.iterdir())
# A value of the wrong type for a text, a mapping or a list: an int and a list.
WRONG = (7, [])


def _places(doc):
    """The places of the document that `run_facts` may read, each a path of keys and indexes.

    They are the root keys, each item of `customProperties` with its keys, the keys of the
    `materialization` value, `team` and its name, and the schema object with its name and its
    physical name.
    """
    places = [(key,) for key in doc]
    for index, item in enumerate(doc.get("customProperties", [])):
        places.append(("customProperties", index))
        places += [("customProperties", index, key) for key in item]
        if item["property"] == "materialization":
            places += [
                ("customProperties", index, "value", key) for key in item["value"]
            ]
    places += [("team", "name"), ("schema", 0)]
    places += [("schema", 0, key) for key in ("name", "physicalName")]
    return [place for place in places if _exists(doc, place)]


def _exists(doc, place):
    for step in place:
        try:
            doc = doc[step]
        except KeyError, IndexError, TypeError:
            return False
    return True


def _derived(doc):
    """Yield the documents derived from `doc`: one place deleted, or one value of the wrong type."""
    for place in _places(doc):
        deleted = copy.deepcopy(doc)
        parent = deleted
        for step in place[:-1]:
            parent = parent[step]
        del parent[place[-1]]
        yield f"delete {place}", deleted
        for wrong in WRONG:
            replaced = copy.deepcopy(doc)
            parent = replaced
            for step in place[:-1]:
                parent = parent[step]
            parent[place[-1]] = wrong
            yield f"replace {place} with {wrong!r}", replaced


@pytest.mark.parametrize("project", PROJECTS)
def test_a_dbt_contract_that_validates_gives_its_run_facts(project):
    accepted = rejected = 0
    for path in sorted((GOLDEN / project).glob("*.json")):
        if path.stem in BAD:
            continue
        doc = json.loads(path.read_text("ascii"))["generated"]
        assert validate_dbt_contract(doc) == []
        for what, derived in _derived(doc):
            if validate_dbt_contract(derived):
                rejected += 1
                continue
            accepted += 1
            try:
                dbt.run_facts(derived)
            except Exception as error:
                pytest.fail(
                    f"{path.stem}: {what} validates but run_facts raises {error!r}"
                )
    assert accepted and rejected


def test_the_goldens_give_their_run_facts_whole():
    facts = dbt.run_facts(_doc("analytics.governed"))
    assert facts.service == "cases"
    assert facts.kind == "dbt-model"
    assert facts.file_path.endswith("governed.sql")
    assert facts.materialized == "table"
