import copy
import time
from pathlib import Path

import pytest

from continuo_contracts import yaml_strict
from continuo_contracts.validate import (
    REASONS,
    Issue,
    rule_issues,
    schema_issues,
    validate_python_contract,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = FIXTURES / "demo" / "service-py" / "contracts" / "py_daily_kpis.odcs.yaml"
FULL = sorted((FIXTURES / "full").glob("*.odcs.yaml"))
KPIS_PATH = "contracts/py_daily_kpis.odcs.yaml"
SERVICE = "service-py"


def _load(path):
    return yaml_strict.load(path.read_text(encoding="utf-8"))


def _kpis():
    """The demo contract, a fresh copy each time. It has four custom properties, so a property
    that a test appends is `customProperties[4]`."""
    return _load(DEMO)


def _check(doc, *, path=KPIS_PATH, service=SERVICE):
    return validate_python_contract(doc, path=path, service=service)


def _found(issues):
    return [(issue.path, issue.reason) for issue in issues]


def _continuo(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def test_the_demo_contract_is_valid():
    assert _check(_kpis()) == []


@pytest.mark.parametrize("fixture", FULL, ids=lambda path: path.name)
def test_a_full_contract_is_valid(fixture):
    assert _check(_load(fixture), path=f"contracts/{fixture.name}") == []


def test_the_full_contracts_name_their_table_after_their_file():
    assert [path.name for path in FULL] == [
        "python_api.odcs.yaml",
        "python_csv.odcs.yaml",
        "python_node.odcs.yaml",
    ]
    for path in FULL:
        assert _load(path)["schema"][0]["name"] == path.name.removesuffix(".odcs.yaml")


@pytest.mark.parametrize(
    "change, path, reason",
    [
        (lambda d: d.update(apiVersion="v3.1.0"), "apiVersion", "api_version"),
        (lambda d: d.update(id="Analytics.py_daily_kpis"), "id", "identity"),
        (lambda d: d.update(id="analytics.other"), "id", "identity"),
        (lambda d: d.update(name="kpis"), "name", "identity"),
        (
            lambda d: d["schema"][0].update(physicalName="kpis"),
            "schema[0].physicalName",
            "identity",
        ),
        (
            lambda d: d["schema"][0].update(physicalType="view"),
            "schema[0].physicalType",
            "identity",
        ),
        (lambda d: d["schema"].append(dict(d["schema"][0])), "schema", "identity"),
        (lambda d: d.pop("team"), "team.name", "missing_field"),
        (
            lambda d: d.update(
                servers=[
                    {
                        "server": "w",
                        "type": "postgres",
                        "host": "h",
                        "port": 5432,
                        "database": "d",
                        "schema": "analytics",
                    }
                ]
            ),
            "servers",
            "refused_field",
        ),
        (lambda d: d.update(tenant="t"), "tenant", "refused_field"),
        (
            lambda d: d.update(contractCreatedTs="2026-11-14T00:00:00Z"),
            "contractCreatedTs",
            "refused_field",
        ),
        (
            lambda d: d["customProperties"].append(_continuo("provenance", {})),
            "customProperties[4]",
            "refused_property",
        ),
        (
            lambda d: d["customProperties"].append(_continuo("filePath", "x")),
            "customProperties[4]",
            "refused_property",
        ),
        (
            lambda d: (
                d["schema"][0]
                .setdefault("customProperties", [])
                .append(_continuo("reads", ["a.b"]))
            ),
            "schema[0].customProperties[0]",
            "refused_property",
        ),
        (
            lambda d: d["customProperties"].append(_continuo("entity", "Transaction")),
            "customProperties[4]",
            "unknown_property",
        ),
        (
            lambda d: d["customProperties"].append(_continuo("service", "other")),
            "customProperties[4]",
            "service",
        ),
    ],
)
def test_one_change_gives_one_issue(change, path, reason):
    doc = _kpis()
    change(doc)
    assert _found(_check(doc)) == [(path, reason)]


def test_the_issue_prints_its_path_and_its_message():
    assert str(Issue("schema[0].name", "identity", "is wrong")) == "schema[0].name: is wrong"


def test_an_issue_is_frozen():
    issue = Issue("$", "identity", "m")
    with pytest.raises(AttributeError):
        issue.path = "x"


def test_the_reasons_are_the_closed_set():
    assert REASONS == (
        "strict_yaml",
        "odcs_schema",
        "api_version",
        "identity",
        "file_name",
        "service",
        "missing_field",
        "unknown_property",
        "refused_property",
        "refused_field",
        "kind_rule",
        "column_type",
        "primary_key",
        "value",
    )


def test_every_issue_has_one_of_the_reasons():
    bad_rules = _kpis()
    bad_rules.update(apiVersion="v3.1.0", tenant="t", id="Analytics.other")
    bad_rules["customProperties"] += [_continuo("entity", 1), _continuo("provenance", 1)]
    bad_schema = _kpis()
    bad_schema["schema"][0]["properties"][0]["name"] = 3
    issues = [
        *schema_issues(bad_schema),
        *rule_issues(bad_rules, path="contracts/other.odcs.yaml", service=SERVICE),
    ]
    assert len(issues) > 5
    assert all(issue.reason in REASONS for issue in issues)


# the file name


def test_the_file_name_must_match_the_table():
    assert [issue.reason for issue in _check(_kpis(), path="contracts/kpis.odcs.yaml")] == [
        "file_name"
    ]
    assert _check(_kpis(), path="contracts/analytics.py_daily_kpis.odcs.yaml") == []


def test_the_file_name_issue_is_for_the_whole_file_and_names_both_spellings():
    (issue,) = _check(_kpis(), path="contracts/kpis.odcs.yaml")
    assert issue.path == "$"
    assert "'kpis.odcs.yaml'" in issue.message
    assert "'py_daily_kpis.odcs.yaml'" in issue.message
    assert "'analytics.py_daily_kpis.odcs.yaml'" in issue.message


@pytest.mark.parametrize(
    "path",
    [
        "py_daily_kpis.odcs.yaml",
        "contracts/py_daily_kpis.odcs.yaml",
        "services/service-py/contracts/py_daily_kpis.odcs.yaml",
        "contracts\\py_daily_kpis.odcs.yaml",
        "C:\\repo\\contracts\\analytics.py_daily_kpis.odcs.yaml",
    ],
)
def test_only_the_base_name_of_the_path_counts(path):
    assert _check(_kpis(), path=path) == []


@pytest.mark.parametrize(
    "path",
    [
        "py_daily_kpis.odcs.yml",
        "py_daily_kpis.yaml",
        "py_daily_kpis.odcs.yaml.bak",
        "PY_DAILY_KPIS.odcs.yaml",
        "other.py_daily_kpis.odcs.yaml",
        "analytics.py_daily_kpis.yaml",
        "contracts/py_daily_kpis.odcs.yaml/",
        "",
    ],
)
def test_another_base_name_is_refused(path):
    assert _found(_check(_kpis(), path=path)) == [("$", "file_name")]


def test_a_table_with_capitals_names_its_file_as_written():
    doc = _kpis()
    doc["name"] = "Py_Daily_KPIs"
    doc["schema"][0]["name"] = "Py_Daily_KPIs"
    assert _check(doc, path="Py_Daily_KPIs.odcs.yaml") == []
    assert _check(doc, path="analytics.Py_Daily_KPIs.odcs.yaml") == []
    assert _found(_check(doc, path="py_daily_kpis.odcs.yaml")) == [("$", "file_name")]
    assert _found(_check(doc, path="analytics.py_daily_kpis.odcs.yaml")) == [("$", "file_name")]


def test_a_file_name_with_the_schema_of_an_id_that_names_another_table_is_refused():
    doc = _kpis()
    doc["id"] = "analytics.other"
    assert _found(_check(doc, path="analytics.other.odcs.yaml")) == [
        ("$", "file_name"),
        ("id", "identity"),
    ]


# service


def test_service_none_skips_the_service_rule():
    doc = _kpis()
    doc["customProperties"].append(_continuo("service", "other"))
    assert _check(doc, service=None) == []
    assert _found(_check(doc, service=SERVICE)) == [("customProperties[4]", "service")]


def test_the_service_of_the_release_is_accepted():
    doc = _kpis()
    doc["customProperties"].append(_continuo("service", SERVICE))
    assert _check(doc) == []


def test_a_contract_without_a_service_property_is_accepted():
    assert _check(_kpis()) == []
    assert _check(_kpis(), service=None) == []


def test_the_service_issue_names_both_services():
    doc = _kpis()
    doc["customProperties"].append(_continuo("service", "other"))
    (issue,) = _check(doc)
    assert "other" in issue.message
    assert SERVICE in issue.message


@pytest.mark.parametrize("value", [5, None, "", " ", "\t", ["service-py"], {"a": "b"}])
@pytest.mark.parametrize("service", [SERVICE, None])
def test_a_service_value_that_is_not_text_is_refused_whether_or_not_the_service_is_known(
    value, service
):
    doc = _kpis()
    doc["customProperties"].append(_continuo("service", value))
    (issue,) = _check(doc, service=service)
    assert (issue.path, issue.reason) == ("customProperties[4]", "value")
    assert "`service`" in issue.message


def test_a_service_string_other_than_the_release_service_is_a_service_issue():
    doc = _kpis()
    doc["customProperties"].append(_continuo("service", "other"))
    assert _found(_check(doc)) == [("customProperties[4]", "service")]
    assert _check(doc, service=None) == []


# the envelope


def test_the_api_version_must_be_the_one_the_schema_is():
    doc = _kpis()
    doc["apiVersion"] = "v3.0.2"
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("apiVersion", "api_version")
    assert "v3.2.0" in issue.message


def test_an_api_version_the_schema_does_not_know_fails_the_schema_check():
    doc = _kpis()
    doc["apiVersion"] = "v4"
    assert _found(_check(doc)) == [("apiVersion", "odcs_schema")]


# identity


def test_the_root_name_may_be_left_out():
    doc = _kpis()
    del doc["name"]
    assert _check(doc) == []


def test_a_physical_name_equal_to_the_table_is_accepted():
    doc = _kpis()
    doc["schema"][0]["physicalName"] = "py_daily_kpis"
    assert _check(doc) == []


def test_a_contract_without_a_schema_object_is_refused():
    for change in (lambda d: d.pop("schema"), lambda d: d.update(schema=[])):
        doc = _kpis()
        change(doc)
        assert _found(_check(doc)) == [("schema", "identity")]


def test_a_schema_object_without_a_physical_type_is_refused():
    doc = _kpis()
    del doc["schema"][0]["physicalType"]
    assert _found(_check(doc)) == [("schema[0].physicalType", "identity")]


@pytest.mark.parametrize("bad_id", [".py_daily_kpis", "py_daily_kpis", "analytics.py_daily", ""])
def test_the_id_is_a_schema_a_dot_and_the_table(bad_id):
    doc = _kpis()
    doc["id"] = bad_id
    assert _found(_check(doc)) == [("id", "identity")]


def test_a_table_with_no_name_is_refused():
    doc = _kpis()
    doc["schema"][0]["name"] = ""
    doc["name"] = ""
    doc["id"] = "analytics."
    assert _found(_check(doc, path="analytics..odcs.yaml")) == [("schema[0].name", "identity")]
    assert _found(_check(doc, path="contracts/x.odcs.yaml")) == [("schema[0].name", "identity")]


def _rename_table(doc, name):
    """Give the table, the root name and the id the same new name."""
    doc["name"] = name
    doc["schema"][0]["name"] = name
    doc["id"] = f"analytics.{name}"


@pytest.mark.parametrize(
    "name",
    [
        " ",
        "\t",
        " \n ",
        " kpis",
        "kpis ",
        "\tkpis",
        "kpis\n",
        "a.b",
        "kpis.",
        ".kpis",
        "a..b",
        "kpis kpis",
        "kpis\tkpis",
        "kpis\u00a0kpis",
        "a/b",
        "/kpis",
        "k\x00pis",
        "k\x1fpis",
        "k\x7fpis",
        "k\x9fpis",
    ],
)
def test_a_table_name_that_is_blank_has_whitespace_a_dot_a_slash_or_a_control_character_is_refused(
    name,
):
    doc = _kpis()
    _rename_table(doc, name)
    assert _found(_check(doc, path=f"contracts/{name}.odcs.yaml")) == [
        ("schema[0].name", "identity")
    ]


@pytest.mark.parametrize("name", [" ", " kpis", "kpis ", "a.b"])
def test_a_table_name_that_is_refused_is_compared_with_nothing(name):
    doc = _kpis()
    doc["schema"][0]["name"] = name
    assert _found(_check(doc)) == [("schema[0].name", "identity")]


def test_each_table_name_fault_says_what_is_wrong():
    messages = {}
    for name in ("", " ", " kpis", "a.b"):
        doc = _kpis()
        doc["schema"][0]["name"] = name
        (issue,) = _check(doc)
        messages[name] = issue.message
    assert "blank" in messages[""]
    assert messages[""] == messages[" "]
    assert "whitespace" in messages[" kpis"]
    assert "dot" in messages["a.b"]


@pytest.mark.parametrize(
    "name, word",
    [("kpis kpis", "whitespace"), ("a/b", "slash"), ("k\x00pis", "control character")],
)
def test_a_table_name_with_inner_whitespace_a_slash_or_a_control_character_says_so(name, word):
    doc = _kpis()
    doc["schema"][0]["name"] = name
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("schema[0].name", "identity")
    assert word in issue.message


@pytest.mark.parametrize("name", ["kpis_2", "Kpis", "k-pis", "k\u00e4se", "k\u00c4se"])
def test_a_table_name_with_inner_characters_is_not_refused_by_the_name_rule(name):
    doc = _kpis()
    _rename_table(doc, name)
    doc["id"] = f"analytics.{name.replace('K', 'k')}"
    assert _check(doc, path=f"contracts/{name}.odcs.yaml") == []


def test_the_id_lower_cases_only_the_ascii_letters_of_the_table():
    doc = _kpis()
    _rename_table(doc, "K\u00c4SE")
    doc["id"] = "analytics.k\u00c4se"
    assert _check(doc, path="contracts/K\u00c4SE.odcs.yaml") == []
    doc["id"] = "analytics.k\u00e4se"
    assert _found(_check(doc, path="contracts/K\u00c4SE.odcs.yaml")) == [("id", "identity")]


@pytest.mark.parametrize("letter", ["\u00c4", "\u0130", "\ua7cb", "\u1e9e"])
def test_a_capital_letter_outside_ascii_is_kept_in_the_id_on_every_python(letter):
    # U+A7CB has a lower case only from Unicode 16, which Python 3.14 has and 3.12 has not, so a
    # rule that lower-cases beyond ASCII would accept this id on one Python and refuse it on the
    # other.
    doc = _kpis()
    doc["id"] = f"analytics{letter}.py_daily_kpis"
    assert _check(doc) == []


def test_the_lower_case_id_message_lower_cases_only_ascii_letters():
    doc = _kpis()
    doc["id"] = "Analytics\u00c4.py_daily_kpis"
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("id", "identity")
    assert "'analytics\u00c4.py_daily_kpis'" in issue.message


@pytest.mark.parametrize(
    "bad_id",
    [
        "a.b.py_daily_kpis",
        "analytics..py_daily_kpis",
        ".analytics.py_daily_kpis",
        "a.b.c.py_daily_kpis",
    ],
)
def test_the_schema_part_of_the_id_has_no_dot(bad_id):
    doc = _kpis()
    doc["id"] = bad_id
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("id", "identity")
    assert "must not contain a dot" in issue.message


@pytest.mark.parametrize(
    "bad_id",
    [
        " .py_daily_kpis",
        "\t.py_daily_kpis",
        " analytics.py_daily_kpis",
        "analytics .py_daily_kpis",
        "my schema.py_daily_kpis",
        "my\u00a0schema.py_daily_kpis",
        "a/b.py_daily_kpis",
        "/analytics.py_daily_kpis",
        "ana\x00lytics.py_daily_kpis",
        "ana\x85lytics.py_daily_kpis",
    ],
)
def test_the_schema_part_of_the_id_has_no_whitespace_slash_or_control_character(bad_id):
    doc = _kpis()
    doc["id"] = bad_id
    assert _found(_check(doc)) == [("id", "identity")]


def test_a_file_named_after_an_id_whose_schema_has_a_dot_is_refused_too():
    doc = _kpis()
    doc["id"] = "a.b.py_daily_kpis"
    assert _found(_check(doc, path="a.b.py_daily_kpis.odcs.yaml")) == [
        ("$", "file_name"),
        ("id", "identity"),
    ]


def test_an_id_in_capitals_and_the_wrong_table_gives_two_issues():
    doc = _kpis()
    doc["id"] = "Analytics.other"
    assert _found(_check(doc)) == [("id", "identity"), ("id", "identity")]


def test_a_second_schema_object_does_not_hide_a_lowercase_id_fault():
    doc = _kpis()
    doc["id"] = "Analytics.py_daily_kpis"
    doc["schema"].append(dict(doc["schema"][0]))
    assert _found(_check(doc)) == [("id", "identity"), ("schema", "identity")]


def test_a_team_that_is_a_list_has_no_name():
    doc = _kpis()
    doc["team"] = [{"username": "ada"}]
    assert _found(_check(doc)) == [("team.name", "missing_field")]


@pytest.mark.parametrize("name", ["", "   "])
def test_a_team_name_with_no_text_is_missing(name):
    doc = _kpis()
    doc["team"]["name"] = name
    assert _found(_check(doc)) == [("team.name", "missing_field")]


def test_a_team_with_a_name_and_other_fields_is_accepted():
    doc = _kpis()
    doc["team"]["description"] = "The people of service-py"
    assert _check(doc) == []


@pytest.mark.parametrize(
    "field, value", [("servers", []), ("tenant", ""), ("contractCreatedTs", "")]
)
def test_a_refused_field_is_refused_even_when_it_is_empty(field, value):
    doc = _kpis()
    doc[field] = value
    assert _found(_check(doc)) == [(field, "refused_field")]


def test_each_refused_field_gets_its_own_issue():
    doc = _kpis()
    doc.update(servers=[], tenant="t", contractCreatedTs="2026-11-14T00:00:00Z")
    assert _found(_check(doc)) == [
        ("contractCreatedTs", "refused_field"),
        ("servers", "refused_field"),
        ("tenant", "refused_field"),
    ]


# continuo properties


def test_a_filled_property_names_who_fills_it():
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [_continuo("reads", ["a.b"])]
    (issue,) = _check(doc)
    assert "reads" in issue.message
    assert "fills" in issue.message


def test_a_filled_property_says_what_it_is_filled_from():
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [_continuo("reads", ["a.b"])]
    (issue,) = _check(doc)
    assert issue.message == (
        "continuo fills `reads` from the tables the read queries name, an author may not write it"
    )


@pytest.mark.parametrize(
    "name",
    "filePath materialization contractEnforced access group deprecationDate freshnessBuildAfter "
    "snapshot quoteColumns".split(),
)
def test_a_dbt_property_is_refused_because_it_says_nothing_about_a_python_node(name):
    doc = _kpis()
    doc["customProperties"].append(_continuo(name, "x"))
    (issue,) = _check(doc)
    assert issue.message == (
        f"a Python contract does not carry `{name}`: it describes how dbt builds or governs a "
        "node, and nothing reads it on a Python node"
    )


def test_provenance_is_refused_because_continuo_adds_it_to_the_published_file():
    doc = _kpis()
    doc["customProperties"].append(_continuo("provenance", {}))
    (issue,) = _check(doc)
    assert "published file" in issue.message
    assert "`provenance`" in issue.message


def test_an_unknown_property_says_to_check_the_spelling():
    doc = _kpis()
    doc["customProperties"].append(_continuo("entity", "Transaction"))
    (issue,) = _check(doc)
    assert "`entity`" in issue.message
    assert "spelling" in issue.message


def test_a_repeated_property_says_why_it_is_refused():
    doc = _kpis()
    doc["customProperties"].append(_continuo("schedule", "hourly"))
    (issue,) = _check(doc)
    assert "which one counts" in issue.message


def test_a_refused_property_is_refused_on_any_level():
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [_continuo("materialization", "view")]
    assert _found(_check(doc)) == [("schema[0].customProperties[0]", "refused_property")]


@pytest.mark.parametrize(
    "name",
    "filePath materialization contractEnforced access group deprecationDate freshnessBuildAfter "
    "snapshot quoteColumns provenance".split(),
)
def test_each_property_a_python_contract_does_not_carry_is_refused(name):
    doc = _kpis()
    doc["customProperties"].append(_continuo(name, "x"))
    assert _found(_check(doc)) == [("customProperties[4]", "refused_property")]


@pytest.mark.parametrize(
    "name",
    "service kind schedule criticality timeoutMinutes script readQueries csvSource extraColumns "
    "config secretRef modeType assets".split(),
)
def test_an_authored_property_on_the_schema_object_is_on_the_wrong_level(name):
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [_continuo(name, "x")]
    assert _found(_check(doc)) == [("schema[0].customProperties[0]", "refused_property")]


def test_a_continuo_property_on_the_wrong_level_is_refused():
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [_continuo("schedule", "daily")]
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("schema[0].customProperties[0]", "refused_property")
    assert "schedule" in issue.message
    assert "contract" in issue.message


def test_a_quality_property_on_the_contract_is_on_the_wrong_level():
    doc = _kpis()
    doc["customProperties"].append(_continuo("where", "amount > 0"))
    assert _found(_check(doc)) == [("customProperties[4]", "refused_property")]


def test_a_continuo_property_on_a_column_is_on_the_wrong_level_or_unknown():
    doc = _kpis()
    column = doc["schema"][0]["properties"][0]
    column["customProperties"] = [_continuo("schedule", "daily"), _continuo("entity", "Day")]
    assert _found(_check(doc)) == [
        ("schema[0].properties[0].customProperties[0]", "refused_property"),
        ("schema[0].properties[0].customProperties[1]", "unknown_property"),
    ]


@pytest.mark.parametrize(
    "where",
    [
        lambda doc: doc["schema"][0],
        lambda doc: doc["schema"][0]["properties"][1],
    ],
)
def test_the_quality_properties_are_accepted_on_a_quality_rule(where):
    doc = _kpis()
    where(doc)["quality"] = [
        {
            "metric": "nullValues",
            "mustBe": 0,
            "customProperties": [
                _continuo("where", "day > date '2020-01-01'"),
                _continuo("warnIf", ">= 1"),
                _continuo("errorIf", "> 10"),
                _continuo("failCalc", "count(*)"),
            ],
        }
    ]
    assert _check(doc) == []


def test_an_authored_contract_property_on_a_quality_rule_is_on_the_wrong_level():
    doc = _kpis()
    doc["schema"][0]["quality"] = [
        {"metric": "rowCount", "mustBeGreaterThan": 0, "customProperties": [_continuo("kind", "x")]}
    ]
    assert _found(_check(doc)) == [("schema[0].quality[0].customProperties[0]", "refused_property")]


def test_a_continuo_property_on_the_description_is_on_the_wrong_level():
    doc = _kpis()
    doc["description"]["customProperties"] = [_continuo("schedule", "daily")]
    assert _found(_check(doc)) == [("description.customProperties[0]", "refused_property")]


def test_a_continuo_property_in_a_list_deep_in_the_contract_is_found():
    doc = _kpis()
    doc["team"]["customProperties"] = [_continuo("entity", "x")]
    doc["roles"] = [{"role": "reader", "customProperties": [_continuo("entity", "x")]}]
    assert _found(_check(doc)) == [
        ("roles[0].customProperties[0]", "unknown_property"),
        ("team.customProperties[0]", "unknown_property"),
    ]


def test_a_value_that_looks_like_a_custom_property_list_is_not_walked():
    doc = _kpis()
    nested = {"customProperties": [_continuo("entity", "x")]}
    doc["customProperties"].append(_continuo("config", nested))
    column = doc["schema"][0]["properties"][0]
    column["examples"] = [nested]
    column["quality"] = [
        {"metric": "rowCount", "mustBeGreaterThan": 0, "arguments": nested},
        {"type": "custom", "engine": "soda", "implementation": nested},
    ]
    assert _check(doc) == []


def test_another_vendors_property_with_a_continuo_name_is_kept_and_ignored():
    doc = _kpis()
    doc["customProperties"] += [
        {"vendor": "lotse", "property": "secretRef", "value": "not-a-continuo-name"},
        {"vendor": "lotse", "property": "schedule", "value": "weekly"},
        {"property": "provenance", "value": {}},
        {"vendor": "Continuo", "property": "entity", "value": 1},
    ]
    doc["schema"][0]["customProperties"] = [{"vendor": "lotse", "property": "reads", "value": 1}]
    assert _check(doc) == []


def test_a_continuo_property_written_twice_is_refused():
    doc = _kpis()
    doc["customProperties"].append(_continuo("schedule", "hourly"))
    (issue,) = _check(doc)
    assert (issue.path, issue.reason) == ("customProperties[4]", "value")
    assert "schedule" in issue.message
    assert "customProperties[0]" in issue.message


def test_a_continuo_property_written_three_times_is_refused_at_each_repeat():
    doc = _kpis()
    doc["customProperties"] += [_continuo("schedule", "hourly"), _continuo("schedule", "daily")]
    assert _found(_check(doc)) == [
        ("customProperties[4]", "value"),
        ("customProperties[5]", "value"),
    ]


def test_a_continuo_property_written_twice_on_the_schema_object_is_refused_there():
    doc = _kpis()
    doc["schema"][0]["customProperties"] = [
        _continuo("where", "a"),
        _continuo("where", "b"),
    ]
    assert _found(_check(doc)) == [
        ("schema[0].customProperties[0]", "refused_property"),
        ("schema[0].customProperties[1]", "refused_property"),
    ]


def test_a_quality_property_written_twice_on_a_rule_is_refused():
    doc = _kpis()
    doc["schema"][0]["quality"] = [
        {
            "metric": "rowCount",
            "mustBeGreaterThan": 0,
            "customProperties": [_continuo("where", "a"), _continuo("where", "b")],
        }
    ]
    assert _found(_check(doc)) == [("schema[0].quality[0].customProperties[1]", "value")]


def test_the_same_property_on_two_lists_is_not_a_repeat():
    doc = _kpis()
    doc["schema"][0]["quality"] = [
        {
            "metric": "rowCount",
            "mustBeGreaterThan": 0,
            "customProperties": [_continuo("where", "a")],
        },
        {
            "metric": "rowCount",
            "mustBeGreaterThan": 1,
            "customProperties": [_continuo("where", "b")],
        },
    ]
    assert _check(doc) == []


def test_an_unknown_property_written_twice_is_unknown_each_time():
    doc = _kpis()
    doc["customProperties"] += [_continuo("entity", 1), _continuo("entity", 2)]
    assert _found(_check(doc)) == [
        ("customProperties[4]", "unknown_property"),
        ("customProperties[5]", "unknown_property"),
    ]


def test_a_repeated_service_that_is_also_wrong_gets_both_issues_at_the_repeat():
    doc = _kpis()
    doc["customProperties"] += [_continuo("service", SERVICE), _continuo("service", "other")]
    assert sorted(_found(_check(doc))) == [
        ("customProperties[5]", "service"),
        ("customProperties[5]", "value"),
    ]


# the schema check


def test_a_schema_error_names_its_path():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["name"] = 3
    issues = schema_issues(doc)
    assert [(issue.path, issue.reason) for issue in issues] == [
        ("schema[0].properties[0].name", "odcs_schema")
    ]
    assert "3" in issues[0].message


def test_a_valid_document_has_no_schema_issue():
    assert schema_issues(_kpis()) == []
    for path in FULL:
        assert schema_issues(_load(path)) == []


def test_two_quality_operators_in_one_rule_fail_the_schema():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["quality"] = [
        {"metric": "nullValues", "mustBe": 0, "mustBeLessThan": 1}
    ]
    issues = _check(doc)
    assert issues
    assert {issue.reason for issue in issues} == {"odcs_schema"}
    assert all(issue.path.startswith("schema[0].properties[0].quality[0]") for issue in issues)


@pytest.mark.parametrize("doc", [[1, 2], None, "text", 5, True])
def test_a_document_that_is_not_a_mapping_fails_the_schema(doc):
    issues = _check(doc)
    assert [(issue.path, issue.reason) for issue in issues] == [("$", "odcs_schema")]
    assert schema_issues(doc) == issues


def test_a_missing_required_field_fails_the_schema_at_the_root():
    doc = _kpis()
    del doc["id"]
    issues = _check(doc)
    assert [(issue.path, issue.reason) for issue in issues] == [("$", "odcs_schema")]
    assert "id" in issues[0].message


def test_an_unexpected_field_fails_the_schema_at_its_object():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["nonsense"] = 1
    assert _found(_check(doc)) == [("schema[0].properties[0]", "odcs_schema")]


def test_an_unknown_field_next_to_a_wrong_value_is_still_reported():
    doc = _kpis()
    column = doc["schema"][0]["properties"][0]
    column["name"] = 3
    column["nonsense"] = 1
    issues = schema_issues(doc)
    assert [issue.path for issue in issues] == [
        "schema[0].properties[0]",
        "schema[0].properties[0].name",
    ]
    assert "nonsense" in issues[0].message


def test_a_schema_failure_hides_the_rules():
    doc = _kpis()
    doc["tenant"] = "t"
    doc["schema"][0]["properties"][0]["name"] = 3
    assert _found(_check(doc)) == [("schema[0].properties[0].name", "odcs_schema")]
    assert _found(rule_issues(doc, path=KPIS_PATH, service=SERVICE)) == [
        ("tenant", "refused_field")
    ]


def test_a_schema_message_is_cut_when_it_is_long():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["name"] = ["x" * 5000]
    (issue,) = schema_issues(doc)
    assert len(issue.message) <= 300
    assert issue.message.endswith("...")


def test_a_short_schema_message_is_whole():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["name"] = 3
    (issue,) = schema_issues(doc)
    assert issue.message == "3 is not of type 'string'"


def test_the_schema_issues_do_not_change_the_document():
    doc = _kpis()
    doc["schema"][0]["properties"][0]["name"] = 3
    before = copy.deepcopy(doc)
    schema_issues(doc)
    rule_issues(doc, path=KPIS_PATH, service=SERVICE)
    assert doc == before


# a column that nests, which the schema check refuses before it runs the JSON schema

# The time a check of a deep nesting may take. The check takes milliseconds, and the JSON schema
# alone takes seconds at eight levels and doubles or triples with each level after.
QUICK = 0.5


def _nesting(shape, levels):
    """Return a column that nests `levels` levels deep, each level through `properties`, `items`
    or a `map` value as `shape` says, or through the three in turn when `shape` is `mix`."""
    column = {"name": "payload", "physicalType": "TEXT"}
    current = column
    for level in range(levels):
        field = shape if shape != "mix" else ("properties", "items", "map")[level % 3]
        inner = {"name": f"n{level + 1}"}
        if field == "properties":
            current.update(logicalType="object", properties=[inner])
        elif field == "items":
            current.update(logicalType="array", items=inner)
        else:
            current.update(
                logicalType="map", map={"key": {"logicalType": "string"}, "value": inner}
            )
        current = inner
    return column


def _with_nesting(shape, levels=30):
    doc = _kpis()
    doc["schema"][0]["properties"].append(_nesting(shape, levels))
    return doc


def _timed(check, doc):
    start = time.perf_counter()
    issues = check(doc)
    return issues, time.perf_counter() - start


# Where the issue of each shape is: the field the column itself carries.
FIRST_FIELD = {"properties": "properties", "items": "items", "map": "map", "mix": "properties"}


@pytest.mark.parametrize("shape", sorted(FIRST_FIELD))
@pytest.mark.parametrize("check", [schema_issues, _check], ids=["schema_issues", "validate"])
def test_a_column_nested_thirty_levels_deep_is_refused_at_once(check, shape):
    issues, seconds = _timed(check, _with_nesting(shape))
    assert seconds < QUICK
    assert _found(issues) == [(f"schema[0].properties[3].{FIRST_FIELD[shape]}", "refused_field")]


@pytest.mark.parametrize("shape", sorted(FIRST_FIELD))
def test_the_schema_check_refuses_a_nesting_as_the_rules_do(shape):
    doc = _with_nesting(shape, levels=2)
    assert schema_issues(doc) == rule_issues(doc, path=KPIS_PATH, service=SERVICE)


def test_a_column_that_carries_items_and_a_map_gives_an_issue_for_each():
    doc = _kpis()
    doc["schema"][0]["properties"][1].update(
        items={"logicalType": "string"}, map={"key": {}, "value": {}}
    )
    assert _found(schema_issues(doc)) == [
        ("schema[0].properties[1].items", "refused_field"),
        ("schema[0].properties[1].map", "refused_field"),
    ]


def test_a_nesting_in_any_schema_object_is_refused_before_the_json_schema():
    doc = _kpis()
    second = copy.deepcopy(doc["schema"][0])
    second["name"] = "other"
    second["properties"] = [_nesting("map", 30)]
    doc["schema"].append(second)
    for check in (schema_issues, _check):
        issues, seconds = _timed(check, doc)
        assert seconds < QUICK
        assert _found(issues) == [("schema[1].properties[0].map", "refused_field")]


def test_a_nesting_hides_the_other_schema_errors():
    doc = _with_nesting("properties")
    doc["schema"][0]["properties"][0]["name"] = 3
    assert _found(schema_issues(doc)) == [("schema[0].properties[3].properties", "refused_field")]
    del doc["schema"][0]["properties"][3]
    assert _found(schema_issues(doc)) == [("schema[0].properties[0].name", "odcs_schema")]


@pytest.mark.parametrize(
    "schema",
    ["text", [1, "column"], [{"name": "t", "properties": "columns"}], [{"properties": [1, None]}]],
)
def test_a_schema_too_malformed_to_scan_is_left_to_the_json_schema(schema):
    doc = _kpis()
    doc["schema"] = schema
    issues = schema_issues(doc)
    assert issues
    assert {issue.reason for issue in issues} == {"odcs_schema"}


# the order


def _spoil(doc, index):
    doc["customProperties"][index]["property"] = index


def test_schema_issues_put_array_indexes_in_numeric_order():
    doc = _kpis()
    doc["customProperties"] += [_continuo(f"p{n}", n) for n in range(10)]
    for index in (10, 4, 2, 13):
        _spoil(doc, index)
    assert [issue.path for issue in schema_issues(doc)] == [
        "customProperties[2].property",
        "customProperties[4].property",
        "customProperties[10].property",
        "customProperties[13].property",
    ]


def test_rule_issues_put_array_indexes_in_numeric_order():
    doc = _kpis()
    doc["customProperties"] += [_continuo("entity", n) for n in range(10)]
    assert [issue.path for issue in rule_issues(doc, path=KPIS_PATH, service=SERVICE)] == [
        f"customProperties[{n}]" for n in range(4, 14)
    ]


def test_issues_are_in_path_order_and_then_message_order():
    doc = _kpis()
    doc.update(tenant="t", apiVersion="v3.1.0", id="Analytics.py_daily_kpis")
    doc["customProperties"].append(_continuo("entity", 1))
    issues = rule_issues(doc, path="contracts/other.odcs.yaml", service=SERVICE)
    assert [issue.path for issue in issues] == [
        "$",
        "apiVersion",
        "customProperties[4]",
        "id",
        "tenant",
    ]
    assert issues == sorted(
        issues, key=lambda issue: (issue.path != "$", issue.path, issue.message)
    )


def test_the_same_document_gives_the_same_issues_every_time():
    doc = _kpis()
    doc.update(tenant="t", servers=[])
    doc["customProperties"] += [_continuo("entity", 1), _continuo("schedule", "x")]
    first = _check(doc)
    assert first == _check(copy.deepcopy(doc))
    assert first == _check(doc)


# the two entry points


def test_rule_issues_runs_the_rules_without_the_schema_check():
    assert rule_issues(_kpis(), path=KPIS_PATH, service=SERVICE) == []
    doc = _kpis()
    doc["tenant"] = "t"
    assert _found(rule_issues(doc, path=KPIS_PATH, service=SERVICE)) == [
        ("tenant", "refused_field")
    ]


def test_rule_issues_takes_the_service_and_the_path_by_keyword():
    with pytest.raises(TypeError):
        rule_issues(_kpis(), KPIS_PATH, SERVICE)


def test_validate_python_contract_takes_the_service_and_the_path_by_keyword():
    with pytest.raises(TypeError):
        validate_python_contract(_kpis(), KPIS_PATH, SERVICE)


def test_validate_python_contract_gives_the_schema_issues_when_there_are_any():
    doc = _kpis()
    doc["tenant"] = "t"
    doc["schema"][0]["properties"][0]["name"] = 3
    assert validate_python_contract(doc, path=KPIS_PATH, service=SERVICE) == schema_issues(doc)


def test_validate_python_contract_gives_the_rule_issues_otherwise():
    doc = _kpis()
    doc["tenant"] = "t"
    assert validate_python_contract(doc, path=KPIS_PATH, service=SERVICE) == rule_issues(
        doc, path=KPIS_PATH, service=SERVICE
    )


def test_a_comparison_value_of_a_quality_rule_is_data_and_not_walked():
    doc = _kpis()
    nested = {"customProperties": [_continuo("entity", "x")]}
    doc["schema"][0]["quality"] = [
        {"metric": "rowCount", "mustBe": nested},
        {"metric": "rowCount", "mustNotBe": [nested]},
    ]
    assert _check(doc) == []


def test_the_value_of_a_map_is_a_property_and_is_walked():
    doc = _kpis()
    doc["schema"][0]["properties"][0].update(
        logicalType="map",
        map={
            "key": {"logicalType": "string"},
            "value": {"logicalType": "string", "customProperties": [_continuo("entity", "x")]},
        },
    )
    assert _found(rule_issues(doc, path=KPIS_PATH, service=SERVICE)) == [
        ("schema[0].properties[0].map", "refused_field"),
        ("schema[0].properties[0].map.value.customProperties[0]", "unknown_property"),
    ]
    # The schema check refuses the map before anything walks it.
    assert _found(_check(doc)) == [("schema[0].properties[0].map", "refused_field")]
