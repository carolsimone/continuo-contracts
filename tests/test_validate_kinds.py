"""The rules of `validate` that depend on the kind of the node: its properties, its columns and
its upsert key."""

import json
from pathlib import Path

import pytest

from continuo_contracts import yaml_strict
from continuo_contracts.python import PYTHON_KINDS
from continuo_contracts.validate import REASONS, rule_issues, validate_python_contract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = FIXTURES / "demo" / "service-py" / "contracts" / "py_daily_kpis.odcs.yaml"
KPIS_PATH = "contracts/py_daily_kpis.odcs.yaml"
SERVICE = "service-py"
FULL = ["python_node", "python_csv", "python_api"]
# continuo's own list of the secret names a python-api node may name, copied byte for byte from
# pkg/domain/model/testdata/secret_ref_cases.json in the continuo repository, where the Go rule
# and topology-controller's rule are pinned to it too.
SECRET_REF_CASES = json.loads((FIXTURES / "secret_ref_cases.json").read_text(encoding="utf-8"))


def _load(path):
    return yaml_strict.load(path.read_text(encoding="utf-8"))


def _kpis():
    """The demo contract, a python-node that replaces its table and has four custom properties,
    in the order schedule, criticality, script, readQueries."""
    return _load(DEMO)


def _full(name):
    return _load(FIXTURES / "full" / f"{name}.odcs.yaml")


def _check(doc, *, path=KPIS_PATH, service=SERVICE):
    return validate_python_contract(doc, path=path, service=service)


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


def _at(doc, name):
    """The issue path of a continuo property of the contract."""
    return f"customProperties[{_index(doc, name)}]"


def _set(doc, name, value):
    """Set a continuo property of the contract, adding it when the contract has none."""
    try:
        doc["customProperties"][_index(doc, name)]["value"] = value
    except KeyError:
        doc["customProperties"].append(_continuo(name, value))


def _drop(doc, name):
    del doc["customProperties"][_index(doc, name)]


def _path_of(name):
    return f"contracts/{name}.odcs.yaml"


def _columns(doc):
    return doc["schema"][0]["properties"]


# the full fixtures


@pytest.mark.parametrize("fixture", FULL)
def test_the_full_fixtures_are_valid(fixture):
    assert _check(_full(fixture), path=_path_of(fixture)) == []


@pytest.mark.parametrize("fixture", FULL)
def test_the_full_fixtures_are_valid_when_the_service_is_not_known(fixture):
    assert validate_python_contract(_full(fixture), path=_path_of(fixture), service=None) == []


def test_the_full_fixtures_are_one_of_each_kind():
    kinds = {_full(name)["customProperties"][1]["value"] for name in FULL}
    assert kinds == set(PYTHON_KINDS)


# the missing fields


@pytest.mark.parametrize("name", ["schedule", "criticality"])
def test_a_contract_without_a_schedule_or_a_criticality_is_refused(name):
    doc = _kpis()
    _drop(doc, name)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("customProperties", "missing_field")
    assert f"`{name}`" in issue.message


def test_a_contract_with_no_custom_properties_misses_each_required_one():
    doc = _kpis()
    del doc["customProperties"]
    assert sorted(_found(_check(doc))) == [
        ("customProperties", "kind_rule"),
        ("customProperties", "kind_rule"),
        ("customProperties", "missing_field"),
        ("customProperties", "missing_field"),
    ]


def test_a_schedule_of_another_vendor_is_not_the_schedule():
    doc = _kpis()
    doc["customProperties"][_index(doc, "schedule")]["vendor"] = "lotse"
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("customProperties", "missing_field")
    assert "`schedule`" in issue.message


@pytest.mark.parametrize(
    "change", [lambda d: d["schema"][0].pop("properties"), lambda d: _columns(d).clear()]
)
def test_a_contract_with_no_columns_is_refused(change):
    doc = _kpis()
    change(doc)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties", "missing_field")
    assert "properties" in issue.message


def test_a_column_without_a_physical_type_is_refused():
    doc = _kpis()
    del _columns(doc)[1]["physicalType"]
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[1].physicalType", "missing_field")
    assert "tx_count" in issue.message
    assert "INTEGER" in issue.message


# the values


@pytest.mark.parametrize("value", ["HIGH", "core", "", 1, None, ["CORE"]])
def test_a_criticality_outside_the_three_is_refused(value):
    doc = _kpis()
    _set(doc, "criticality", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "criticality"), "value")
    assert "REGULATORY, CORE, SECONDARY" in issue.message


@pytest.mark.parametrize("value", ["REGULATORY", "CORE", "SECONDARY"])
def test_each_criticality_is_accepted(value):
    doc = _kpis()
    _set(doc, "criticality", value)
    assert _check(doc) == []


@pytest.mark.parametrize("value", [0, 10081, -5, True, False, 1.5, 30.0, "30", None, [30]])
def test_a_timeout_that_is_not_a_whole_number_of_minutes_up_to_a_week_is_refused(value):
    doc = _kpis()
    _set(doc, "timeoutMinutes", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "timeoutMinutes"), "value")
    assert "10080" in issue.message


@pytest.mark.parametrize("value", [1, 60, 10080])
def test_a_timeout_between_one_minute_and_a_week_is_accepted(value):
    doc = _kpis()
    _set(doc, "timeoutMinutes", value)
    assert _check(doc) == []


@pytest.mark.parametrize("value", ["ignore", "Raise", "", 1, True])
def test_an_extra_columns_policy_outside_raise_and_warn_is_refused(value):
    doc = _kpis()
    _set(doc, "extraColumns", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "extraColumns"), "value")
    assert "raise or warn" in issue.message


@pytest.mark.parametrize("value", ["raise", "warn"])
def test_both_extra_columns_policies_are_accepted(value):
    doc = _kpis()
    _set(doc, "extraColumns", value)
    assert _check(doc) == []


@pytest.mark.parametrize("value", ["merge", "REPLACE", "", 1, ["append"]])
def test_a_mode_type_outside_the_three_is_refused(value):
    doc = _kpis()
    _set(doc, "modeType", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "modeType"), "value")
    assert "replace, append or upsert" in issue.message


def test_a_mode_type_that_is_refused_for_its_value_does_not_also_start_the_key_rules():
    doc = _full("python_node")
    _set(doc, "modeType", "merge")
    for column in _columns(doc):
        column.pop("primaryKey", None)
    assert _found(_check(doc, path=_path_of("python_node"))) == [(_at(doc, "modeType"), "value")]


@pytest.mark.parametrize("value", ["", "  ", 5, None, ["a.py"], {"a": "b"}])
def test_a_script_that_is_not_a_string_with_text_is_refused(value):
    doc = _kpis()
    _set(doc, "script", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "script"), "value")
    assert "`script`" in issue.message


@pytest.mark.parametrize("value", ["", "  ", 5, None, ["daily"]])
def test_a_schedule_that_is_not_a_string_with_text_is_refused(value):
    doc = _kpis()
    _set(doc, "schedule", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "schedule"), "value")
    assert "`schedule`" in issue.message


@pytest.mark.parametrize("value", [[], "a=b", 5, None])
def test_a_config_that_is_not_a_mapping_is_refused(value):
    doc = _kpis()
    _set(doc, "config", value)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "config"), "value")
    assert "`config`" in issue.message


def test_a_config_mapping_is_accepted_whatever_it_holds():
    doc = _kpis()
    _set(doc, "config", {"batch_size": 10, "tags": ["a", {"b": None}], "ratio": 0.5})
    assert _check(doc) == []
    _set(doc, "config", {})
    assert _check(doc) == []


# the secret reference


def _api(secret_ref):
    doc = _full("python_api")
    _set(doc, "secretRef", secret_ref)
    return doc


@pytest.mark.parametrize("secret_ref", SECRET_REF_CASES["valid"])
def test_a_secret_name_that_continuo_accepts_is_accepted(secret_ref):
    assert _check(_api(secret_ref), path=_path_of("python_api")) == []


@pytest.mark.parametrize("secret_ref", SECRET_REF_CASES["invalid"])
def test_a_secret_name_that_continuo_refuses_is_refused(secret_ref):
    doc = _api(secret_ref)
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "secretRef"), "value")
    assert "continuo-api-" in issue.message


def test_the_secret_cases_of_continuo_hold_the_edges_this_rule_needs():
    assert len(SECRET_REF_CASES["valid"]) >= 5
    assert any(len(case) == 253 for case in SECRET_REF_CASES["valid"])
    assert any(len(case) == 254 for case in SECRET_REF_CASES["invalid"])


@pytest.mark.parametrize(
    "secret_ref",
    [
        "my-secret",
        "continuo-api-" + "a" * 241,
        "continuo-api-" + "a" * 300,
        "continuo-api-ünï",
        "continuo-api-fx\n",
        "continuo-api-fx\r\n",
        "Continuo-api-fx",
        5,
        None,
        ["continuo-api-fx"],
    ],
)
def test_a_secret_name_with_the_wrong_prefix_length_or_type_is_refused(secret_ref):
    doc = _api(secret_ref)
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "secretRef"), "value")


def test_a_secret_name_of_253_characters_is_the_longest_accepted():
    assert len("continuo-api-" + "a" * 240) == 253
    assert _check(_api("continuo-api-" + "a" * 240), path=_path_of("python_api")) == []


def test_a_long_secret_name_does_not_make_a_long_message():
    issue = _only(_check(_api("continuo-api-" + "a" * 5000), path=_path_of("python_api")))
    assert len(issue.message) < 300
    assert "253" in issue.message


# the CSV source


def _csv(source):
    doc = _full("python_csv")
    _set(doc, "csvSource", source)
    return doc


@pytest.mark.parametrize(
    "source",
    [
        "s3://bucket/key.csv",
        "s3://bucket/a/b/c.csv",
        "s3://continuo-dev/static-files/full/orders.csv",
        "https://example.com",
        "https://example.com/",
        "https://example.com/orders.csv",
        "https://example.com:8443/orders.csv?x=1#top",
        "https://localhost/o.csv",
    ],
)
def test_a_csv_source_that_is_an_s3_object_or_an_https_address_is_accepted(source):
    assert _check(_csv(source), path=_path_of("python_csv")) == []


@pytest.mark.parametrize(
    "source",
    [
        "s3://bucket",
        "s3://bucket/",
        "s3:///key",
        "s3://",
        "https://",
        "https:///x",
        "http://h/x",
        "ftp://h/x",
        "file:///etc/passwd",
        "orders.csv",
        "/data/orders.csv",
        "",
        " s3://bucket/key",
        "s3://bucket/key ",
        "s3://bu cket/key",
        "https://exa mple.com/x",
        "https://example.com?x=1",
        "S3://bucket/key",
        "s3://bucket/key\n",
        5,
        None,
        ["s3://bucket/key"],
    ],
)
def test_a_csv_source_that_is_not_an_s3_object_or_an_https_address_is_refused(source):
    doc = _csv(source)
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "csvSource"), "value")
    assert "s3://<bucket>/<key>" in issue.message
    assert "https://<host>" in issue.message


# the kind


@pytest.mark.parametrize(
    "kind", ["python-model2", "Python-Node", "", "dbt-model", 5, None, ["python-node"]]
)
def test_a_kind_that_is_not_a_python_kind_is_refused_once(kind):
    doc = _kpis()
    doc["customProperties"].append(_continuo("kind", kind))
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "kind"), "value")
    assert "python-node, python-csv or python-api" in issue.message


def test_the_per_kind_rules_do_not_run_for_a_kind_that_is_refused():
    doc = _kpis()
    doc["customProperties"].append(_continuo("kind", "python-model2"))
    _drop(doc, "script")
    _set(doc, "csvSource", "s3://bucket/key.csv")
    _set(doc, "secretRef", "continuo-api-fx")
    assert _found(_check(doc)) == [(_at(doc, "kind"), "value")]


def test_the_value_rules_still_run_for_a_kind_that_is_refused():
    doc = _kpis()
    doc["customProperties"].append(_continuo("kind", "python-model2"))
    _set(doc, "criticality", "HIGH")
    assert _found(_check(doc)) == [
        (_at(doc, "criticality"), "value"),
        (_at(doc, "kind"), "value"),
    ]


def test_the_old_name_of_python_node_is_accepted():
    doc = _kpis()
    doc["customProperties"].append(_continuo("kind", "python-model"))
    assert _check(doc) == []


def test_python_node_is_the_kind_of_a_contract_that_names_none():
    doc = _kpis()
    assert "kind" not in {item["property"] for item in doc["customProperties"]}
    _drop(doc, "script")
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("customProperties", "kind_rule")
    assert "python-node" in issue.message


@pytest.mark.parametrize("kind", PYTHON_KINDS)
def test_every_python_kind_has_its_rules(kind):
    doc = _kpis()
    doc["customProperties"].append(_continuo("kind", kind))
    assert {issue.reason for issue in _check(doc)} <= {"kind_rule"}


def test_a_python_node_without_a_script_is_refused():
    doc = _kpis()
    _drop(doc, "script")
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("customProperties", "kind_rule")
    assert "`script`" in issue.message


def test_a_python_node_without_read_queries_is_refused():
    doc = _kpis()
    _drop(doc, "readQueries")
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("customProperties", "kind_rule")
    assert "`readQueries`" in issue.message


def test_a_python_node_with_a_csv_source_is_refused():
    doc = _kpis()
    _set(doc, "csvSource", "s3://bucket/key.csv")
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "csvSource"), "kind_rule")
    assert "python-csv" in issue.message


def test_a_python_node_with_a_secret_ref_is_refused():
    doc = _kpis()
    _set(doc, "secretRef", "continuo-api-fx")
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "secretRef"), "kind_rule")
    assert "python-api" in issue.message


def test_a_python_node_may_carry_assets_and_any_mode_type():
    doc = _kpis()
    _set(doc, "assets", ["assets/a.csv"])
    _set(doc, "modeType", "append")
    assert _check(doc) == []


def test_a_python_csv_with_a_script_is_refused():
    doc = _full("python_csv")
    _set(doc, "script", "scripts/python_csv.py")
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "script"), "kind_rule")
    assert "python-csv" in issue.message


def test_a_python_csv_with_read_queries_is_refused():
    doc = _full("python_csv")
    _set(doc, "readQueries", {"orders": "select 1"})
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "readQueries"), "kind_rule")


def test_a_python_csv_with_assets_is_refused():
    doc = _full("python_csv")
    _set(doc, "assets", ["assets/a.csv"])
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "assets"), "kind_rule")


def test_a_python_csv_that_appends_is_refused():
    doc = _full("python_csv")
    _set(doc, "modeType", "append")
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "modeType"), "kind_rule")
    assert "replace" in issue.message


def test_a_python_csv_that_upserts_is_refused():
    doc = _full("python_csv")
    _set(doc, "modeType", "upsert")
    assert _found(_check(doc, path=_path_of("python_csv"))) == [(_at(doc, "modeType"), "kind_rule")]


def test_a_python_csv_without_a_mode_type_replaces():
    doc = _full("python_csv")
    _drop(doc, "modeType")
    assert _check(doc, path=_path_of("python_csv")) == []


def test_a_python_csv_without_a_csv_source_is_refused():
    doc = _full("python_csv")
    _drop(doc, "csvSource")
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == ("customProperties", "kind_rule")
    assert "`csvSource`" in issue.message


def test_a_python_csv_with_a_secret_ref_is_refused():
    doc = _full("python_csv")
    _set(doc, "secretRef", "continuo-api-fx")
    issue = _only(_check(doc, path=_path_of("python_csv")))
    assert (issue.path, issue.reason) == (_at(doc, "secretRef"), "kind_rule")


def test_a_python_api_with_read_queries_is_refused():
    doc = _full("python_api")
    _set(doc, "readQueries", {"rates": "select 1"})
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "readQueries"), "kind_rule")
    assert "python-api" in issue.message


def test_a_python_api_with_a_csv_source_is_refused():
    doc = _full("python_api")
    _set(doc, "csvSource", "s3://bucket/key.csv")
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "csvSource"), "kind_rule")


def test_a_python_api_without_a_script_is_refused():
    doc = _full("python_api")
    _drop(doc, "script")
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == ("customProperties", "kind_rule")
    assert "`script`" in issue.message


def test_a_python_api_needs_no_secret_ref_and_may_replace():
    doc = _full("python_api")
    _drop(doc, "secretRef")
    _set(doc, "modeType", "replace")
    assert _check(doc, path=_path_of("python_api")) == []


def test_a_kind_rule_message_says_why():
    doc = _full("python_csv")
    _set(doc, "script", "scripts/python_csv.py")
    (issue,) = _check(doc, path=_path_of("python_csv"))
    assert issue.message == (
        "a python-csv node does not take `script`: it loads its CSV file and runs no script"
    )


# the read queries


@pytest.mark.parametrize(
    "queries",
    [
        {},
        [],
        "select 1",
        5,
        None,
        {"orders": ""},
        {"orders": "   "},
        {"orders": "\n\t"},
        {"orders": 5},
        {"orders": None},
        {"orders": ["select 1"]},
        {"": "select 1"},
        {"  ": "select 1"},
        {"orders": "select 1", "rates": ""},
    ],
)
def test_read_queries_that_are_not_a_mapping_of_names_to_queries_are_refused(queries):
    doc = _kpis()
    _set(doc, "readQueries", queries)
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (_at(doc, "readQueries"), "value")
    assert "`readQueries`" in issue.message


def test_each_empty_query_gets_its_own_issue_and_names_the_query():
    doc = _kpis()
    _set(doc, "readQueries", {"orders": "", "rates": "select 1", "days": " "})
    issues = _check(doc)
    assert _found(issues) == [(_at(doc, "readQueries"), "value")] * 2
    assert sorted("'days'" in issue.message for issue in issues) == [False, True]
    assert sorted("'orders'" in issue.message for issue in issues) == [False, True]


def test_read_queries_with_text_are_accepted():
    doc = _kpis()
    _set(doc, "readQueries", {"a": "select 1", "b": "select\n  2 from t"})
    assert _check(doc) == []


def test_read_queries_on_a_kind_that_refuses_them_give_one_issue_whatever_they_hold():
    doc = _full("python_api")
    _set(doc, "readQueries", {})
    assert _found(_check(doc, path=_path_of("python_api"))) == [
        (_at(doc, "readQueries"), "kind_rule")
    ]


# the assets


@pytest.mark.parametrize(
    "assets",
    [
        ["../x"],
        ["/abs"],
        ["a/../b"],
        ["a/.."],
        [".."],
        ["assets/ok.csv", "../x"],
        [""],
        ["  "],
        [5],
        [None],
        "assets/a.csv",
        {"a": "b"},
        5,
    ],
)
def test_an_asset_that_leaves_the_project_or_is_not_a_path_is_refused(assets):
    doc = _full("python_api")
    _set(doc, "assets", assets)
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "assets"), "value")
    assert "`assets`" in issue.message


def test_an_asset_listed_twice_is_refused_because_the_projection_refuses_it():
    doc = _full("python_api")
    _set(doc, "assets", ["assets/a.csv", "assets/b.csv", "assets/a.csv"])
    issue = _only(_check(doc, path=_path_of("python_api")))
    assert (issue.path, issue.reason) == (_at(doc, "assets"), "value")
    assert "assets/a.csv" in issue.message


@pytest.mark.parametrize(
    "assets",
    [[], ["a.csv"], ["assets/a.csv", "assets/b/c.json"], ["a..b/c"], ["./a.csv"], ["a b.csv"]],
)
def test_assets_inside_the_project_are_accepted(assets):
    doc = _full("python_api")
    _set(doc, "assets", assets)
    assert _check(doc, path=_path_of("python_api")) == []


# the columns


def test_two_columns_with_the_same_name_are_refused_at_the_second():
    doc = _kpis()
    _columns(doc)[1]["name"] = "day"
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[1].name", "value")
    assert "day" in issue.message
    assert "schema[0].properties[0]" in issue.message


def test_three_columns_with_the_same_name_are_refused_at_each_repeat():
    doc = _kpis()
    _columns(doc)[1]["name"] = "day"
    _columns(doc)[2]["name"] = "day"
    assert _found(_check(doc)) == [
        ("schema[0].properties[1].name", "value"),
        ("schema[0].properties[2].name", "value"),
    ]


def test_column_names_that_differ_only_in_case_are_two_columns():
    doc = _kpis()
    _columns(doc)[1]["name"] = "DAY"
    assert _check(doc) == []


@pytest.mark.parametrize("name", ["", " ", "\t"])
def test_a_column_with_a_blank_name_is_refused(name):
    doc = _kpis()
    _columns(doc)[0]["name"] = name
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[0].name", "value")


@pytest.mark.parametrize(
    "raw", ["NUMERIC(12)", "NUMERIC(12, 2, 1)", "VARCHAR", "TEXT[]", "float", "", " "]
)
def test_a_column_type_continuo_does_not_accept_is_refused(raw):
    doc = _kpis()
    _columns(doc)[2]["physicalType"] = raw
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[2].physicalType", "column_type")


def test_the_column_type_message_names_the_type():
    doc = _kpis()
    _columns(doc)[2]["physicalType"] = "NUMERIC(12)"
    (issue,) = _check(doc)
    assert issue.path == "schema[0].properties[2].physicalType"
    assert issue.message == "'NUMERIC(12)' is not a type continuo accepts"


def test_a_numeric_out_of_range_says_the_range():
    doc = _kpis()
    _columns(doc)[2]["physicalType"] = "NUMERIC(39,2)"
    (issue,) = _check(doc)
    assert issue.reason == "column_type"
    assert issue.message.startswith("'NUMERIC(39,2)' is not a type continuo accepts")
    assert "precision is 1 to 38" in issue.message


@pytest.mark.parametrize(
    "raw",
    [
        "int",
        "INT",
        "Integer",
        "bigint",
        "numeric(12, 2)",
        "NUMERIC(12,2)",
        "decimal(5,0)",
        "varchar(10)",
        "Char(3)",
        "double precision",
        "text",
        "timestamp",
        "date",
        "boolean",
        "  INTEGER ",
    ],
)
def test_a_column_type_continuo_accepts_is_accepted_in_any_case(raw):
    doc = _kpis()
    _columns(doc)[1]["physicalType"] = raw
    assert _check(doc) == []


def test_each_wrong_column_type_gets_its_own_issue():
    doc = _kpis()
    _columns(doc)[0]["physicalType"] = "float"
    _columns(doc)[2]["physicalType"] = "NUMERIC(12)"
    assert _found(_check(doc)) == [
        ("schema[0].properties[0].physicalType", "column_type"),
        ("schema[0].properties[2].physicalType", "column_type"),
    ]


def test_a_column_physical_name_other_than_its_name_is_refused():
    doc = _kpis()
    _columns(doc)[0]["physicalName"] = "d"
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[0].physicalName", "refused_field")
    assert "'day'" in issue.message


def test_a_column_physical_name_equal_to_its_name_is_accepted():
    doc = _kpis()
    _columns(doc)[0]["physicalName"] = "day"
    assert _check(doc) == []


def test_a_partitioned_column_is_refused():
    doc = _kpis()
    _columns(doc)[0]["partitioned"] = True
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[0].partitioned", "refused_field")
    assert "partition" in issue.message


def test_a_column_that_is_not_partitioned_is_accepted():
    doc = _kpis()
    _columns(doc)[0]["partitioned"] = False
    _columns(doc)[0]["partitionKeyPosition"] = -1
    assert _check(doc) == []


@pytest.mark.parametrize("position", [1, 0, 2, -2])
def test_a_partition_key_position_other_than_minus_one_is_refused(position):
    doc = _kpis()
    _columns(doc)[0]["partitionKeyPosition"] = position
    issue = _only(_check(doc))
    assert (issue.path, issue.reason) == (
        "schema[0].properties[0].partitionKeyPosition",
        "refused_field",
    )


def test_a_unique_column_is_accepted_whatever_the_node():
    for name in FULL:
        doc = _full(name)
        for column in _columns(doc):
            column["unique"] = True
        assert _check(doc, path=_path_of(name)) == []
    doc = _kpis()
    for column in _columns(doc):
        column["unique"] = True
    assert _check(doc) == []


def test_a_column_that_is_not_a_mapping_does_not_stop_the_rules():
    doc = _kpis()
    doc["schema"][0]["properties"].insert(0, "day")
    assert isinstance(rule_issues(doc, path=KPIS_PATH, service=SERVICE), list)


# the upsert key


def _upsert():
    """The upsert fixture: two key columns, `order_id` and `line_no`, in positions 1 and 2."""
    return _full("python_node")


def _upsert_check(doc):
    return _check(doc, path=_path_of("python_node"))


def test_the_upsert_fixture_has_two_key_columns_in_order():
    doc = _upsert()
    assert _check(doc, path=_path_of("python_node")) == []
    keys = [(c["name"], c["primaryKeyPosition"]) for c in _columns(doc) if c.get("primaryKey")]
    assert keys == [("order_id", 1), ("line_no", 2)]


def test_an_upsert_node_with_no_key_is_refused():
    doc = _upsert()
    for column in _columns(doc):
        column.pop("primaryKey", None)
        column.pop("primaryKeyPosition", None)
    issue = _only(_upsert_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties", "primary_key")
    assert "upsert" in issue.message
    assert "primaryKey" in issue.message


def test_a_primary_key_that_is_false_is_no_key():
    doc = _upsert()
    for column in _columns(doc):
        column["primaryKey"] = False
    assert _found(_upsert_check(doc)) == [("schema[0].properties", "primary_key")]


@pytest.mark.parametrize("drop", [True, False])
def test_a_key_column_that_is_not_required_is_refused(drop):
    doc = _upsert()
    if drop:
        del _columns(doc)[0]["required"]
    else:
        _columns(doc)[0]["required"] = False
    issue = _only(_upsert_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[0].required", "primary_key")
    assert "order_id" in issue.message


@pytest.mark.parametrize("raw", ["DOUBLE PRECISION", "double precision", " Double Precision "])
def test_a_double_precision_key_column_is_refused(raw):
    doc = _upsert()
    _columns(doc)[1]["physicalType"] = raw
    issue = _only(_upsert_check(doc))
    assert (issue.path, issue.reason) == ("schema[0].properties[1].physicalType", "primary_key")
    assert "DOUBLE PRECISION" in issue.message


def test_a_key_column_whose_type_is_refused_gets_only_the_type_issue():
    doc = _upsert()
    _columns(doc)[1]["physicalType"] = "float"
    assert _found(_upsert_check(doc)) == [("schema[0].properties[1].physicalType", "column_type")]


def test_two_key_columns_in_the_same_position_are_refused_at_the_second():
    doc = _upsert()
    _columns(doc)[1]["primaryKeyPosition"] = 1
    issue = _only(_upsert_check(doc))
    assert (issue.path, issue.reason) == (
        "schema[0].properties[1].primaryKeyPosition",
        "primary_key",
    )
    assert "order_id" in issue.message


def test_two_key_columns_with_no_position_are_in_the_same_position():
    doc = _upsert()
    for column in _columns(doc)[:2]:
        del column["primaryKeyPosition"]
    issue = _only(_upsert_check(doc))
    assert (issue.path, issue.reason) == (
        "schema[0].properties[1].primaryKeyPosition",
        "primary_key",
    )


def test_one_key_column_needs_no_position():
    doc = _upsert()
    del _columns(doc)[1]["primaryKey"]
    del _columns(doc)[1]["primaryKeyPosition"]
    del _columns(doc)[0]["primaryKeyPosition"]
    assert _upsert_check(doc) == []


def test_key_columns_in_any_order_of_the_file_are_accepted():
    doc = _upsert()
    _columns(doc)[0]["primaryKeyPosition"] = 2
    _columns(doc)[1]["primaryKeyPosition"] = 1
    assert _upsert_check(doc) == []


def test_each_key_fault_gets_its_own_issue():
    doc = _upsert()
    del _columns(doc)[0]["required"]
    _columns(doc)[1]["physicalType"] = "DOUBLE PRECISION"
    _columns(doc)[1]["primaryKeyPosition"] = 1
    assert _found(_upsert_check(doc)) == [
        ("schema[0].properties[0].required", "primary_key"),
        ("schema[0].properties[1].physicalType", "primary_key"),
        ("schema[0].properties[1].primaryKeyPosition", "primary_key"),
    ]


def test_the_key_rules_apply_to_an_upsert_python_api_too():
    doc = _full("python_api")
    _set(doc, "modeType", "upsert")
    assert _check(doc, path=_path_of("python_api")) == []
    del _columns(doc)[0]["required"]
    assert _found(_check(doc, path=_path_of("python_api"))) == [
        ("schema[0].properties[0].required", "primary_key")
    ]


@pytest.mark.parametrize("mode", ["replace", "append"])
def test_a_primary_key_is_descriptive_on_a_replace_or_append_node(mode):
    doc = _upsert()
    _set(doc, "modeType", mode)
    _columns(doc)[0].pop("required")
    _columns(doc)[1]["physicalType"] = "DOUBLE PRECISION"
    _columns(doc)[1]["primaryKeyPosition"] = 1
    assert _upsert_check(doc) == []


def test_a_primary_key_on_a_replace_node_with_no_mode_type_is_accepted():
    doc = _kpis()
    _columns(doc)[0]["primaryKey"] = True
    assert _check(doc) == []
    _columns(doc)[1]["primaryKey"] = True
    assert _check(doc) == []


def test_a_node_with_no_mode_type_needs_no_key():
    doc = _kpis()
    assert not any(column.get("primaryKey") for column in _columns(doc))
    assert _check(doc) == []


def test_an_upsert_node_with_no_columns_misses_the_columns_and_not_the_key():
    doc = _upsert()
    _columns(doc).clear()
    assert _found(_upsert_check(doc)) == [("schema[0].properties", "missing_field")]


def test_every_issue_of_these_rules_has_one_of_the_reasons():
    doc = _upsert()
    _drop(doc, "schedule")
    _set(doc, "criticality", "HIGH")
    _set(doc, "secretRef", "x")
    _columns(doc)[0]["physicalType"] = "NUMERIC(12)"
    _columns(doc)[1]["partitioned"] = True
    _columns(doc)[2]["name"] = "order_id"
    issues = _upsert_check(doc)
    assert len(issues) >= 6
    assert all(issue.reason in REASONS for issue in issues)
