"""The ontology binding properties: where they sit, and the shape of each value.

A binding says which thing of an ontology a table or a column stands for. The seven properties
are checked for their shape only, in a Python contract and in a dbt contract alike. Whether a
binding names a real thing is for the ontology check.
"""

import copy
import json

from pathlib import Path

import pytest

from continuo_contracts import validate, yaml_strict
from continuo_contracts.validate import (
    dbt_rule_issues,
    rule_issues,
    validate_dbt_contract,
    validate_python_contract,
)
from continuo_contracts.vocabulary import PROPERTIES, is_name

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = FIXTURES / "demo" / "service-py" / "contracts" / "py_daily_kpis.odcs.yaml"
GOLDEN = FIXTURES / "dbt" / "golden" / "cases" / "analytics.generic_tests.json"
PATH = "contracts/py_daily_kpis.odcs.yaml"
PROJECT = "service-py"

# Each binding property, a value that is well shaped, and where it sits.
SCHEMA_BINDINGS = [
    ("entity", "Transaction"),
    ("grain", ["day", "currency"]),
    ("fixed", {"currency": "EUR", "scale": 2, "live": True}),
    ("filter", {"source": "card", "year": 2026, "refunded": False}),
]
COLUMN_BINDINGS = [
    ("property", "Transaction.amount"),
    ("local", True),
    ("aggregate", {"from": "Transaction.amount", "agg": "sum"}),
]
BINDINGS = SCHEMA_BINDINGS + COLUMN_BINDINGS

MALFORMED = [
    ("entity", 42),
    ("entity", ""),
    ("entity", "Two Words"),
    ("entity", "Thing.field"),
    ("grain", []),
    ("grain", [""]),
    ("grain", ["day", 3]),
    ("grain", "day"),
    ("fixed", ["card"]),
    ("fixed", {"currency": None}),
    ("fixed", {"currency": ["EUR"]}),
    ("fixed", {"": "EUR"}),
    ("fixed", {"rate": float("nan")}),
    ("filter", {"source": {"a": 1}}),
    ("filter", "source"),
    ("filter", {"rate": float("inf")}),
    ("property", "User"),
    ("property", "User.email.x"),
    ("property", ".email"),
    ("property", "User."),
    ("property", "User. email"),
    ("property", 7),
    ("local", False),
    ("local", "true"),
    ("local", 1),
    ("aggregate", {"agg": "sum"}),
    ("aggregate", {}),
    ("aggregate", []),
    ("aggregate", {"from": "T.f", "unknown": 1}),
    ("aggregate", {"from": "T.f", "where": "x"}),
    ("aggregate", {"from": "T.f", "agg": 3}),
    ("aggregate", {"from": "T.f", "agg": ""}),
    ("aggregate", {"period": 3}),
    ("aggregate", {"from": "T.f.g"}),
    ("aggregate", {"from": 5}),
    ("aggregate", {"from": ""}),
]


def _p(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def _python(*, schema=(), column=()):
    """The demo contract with binding items on its schema object and on its first column."""
    doc = yaml_strict.load(DEMO.read_text(encoding="utf-8"))
    table = doc["schema"][0]
    if schema:
        table["customProperties"] = list(schema)
    if column:
        table["properties"][0]["customProperties"] = list(column)
    return doc


def _dbt(*, schema=(), column=()):
    """A generated dbt contract with binding items on its schema object and on its first column."""
    doc = copy.deepcopy(json.loads(GOLDEN.read_text("ascii"))["generated"])
    table = doc["schema"][0]
    if schema:
        table["customProperties"] = list(schema)
    if column:
        table["properties"][0]["customProperties"] = list(column)
    return doc


def _check_python(doc):
    return validate_python_contract(doc, path=PATH, project=PROJECT)


def _check(family, doc):
    return (
        _check_python(doc) if family == "python" else validate_dbt_contract(doc)
    )


def _at(family, name, value):
    """The contract of `family` with one binding item at the place it belongs."""
    make = _python if family == "python" else _dbt
    item = _p(name, value)
    if PROPERTIES[name].sits_on == "schema":
        return make(schema=[item])
    return make(column=[item])


def _found(issues):
    return [(issue.path, issue.reason) for issue in issues]


def test_the_two_families_start_valid():
    assert _check_python(_python()) == []
    assert validate_dbt_contract(_dbt()) == []


@pytest.mark.parametrize("family", ["python", "dbt"])
@pytest.mark.parametrize(
    "name,value", BINDINGS, ids=[name for name, _ in BINDINGS]
)
def test_a_well_shaped_binding_is_valid(family, name, value):
    assert _check(family, _at(family, name, value)) == []


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_every_binding_at_its_place_is_valid_together(family):
    make = _python if family == "python" else _dbt
    doc = make(
        schema=[_p(name, value) for name, value in SCHEMA_BINDINGS],
        column=[_p("property", "Transaction.amount")],
    )
    assert _check(family, doc) == []


def test_the_seven_properties_are_in_the_vocabulary_with_their_place():
    for name, _ in SCHEMA_BINDINGS:
        row = PROPERTIES[name]
        assert (row.sits_on, row.python, row.dbt) == (
            "schema",
            "authored",
            "written",
        )
    for name, _ in COLUMN_BINDINGS:
        row = PROPERTIES[name]
        assert (row.sits_on, row.python, row.dbt) == (
            "column",
            "authored",
            "written",
        )


def test_a_binding_at_the_wrong_place_is_refused():
    [issue] = _check_python(_python(schema=[_p("property", "User.email")]))
    assert issue.path == "schema[0].customProperties[0]"
    assert issue.reason == "refused_property"
    assert "sits on a column" in issue.message
    assert "not on the schema object" in issue.message


@pytest.mark.parametrize(
    "name,value,place",
    [
        ("entity", "Transaction", "the schema object"),
        ("grain", ["day"], "the schema object"),
        ("fixed", {"a": 1}, "the schema object"),
        ("filter", {"a": 1}, "the schema object"),
        ("property", "T.f", "a column"),
        ("local", True, "a column"),
        ("aggregate", {"period": "day"}, "a column"),
    ],
)
@pytest.mark.parametrize("family", ["python", "dbt"])
def test_every_binding_is_refused_at_the_root_and_at_the_wrong_object(
    family, name, value, place
):
    make = _python if family == "python" else _dbt
    doc = make()
    doc["customProperties"].append(_p(name, value))
    index = len(doc["customProperties"]) - 1
    [issue] = _check(family, doc)
    assert (issue.path, issue.reason) == (
        f"customProperties[{index}]",
        "refused_property",
    )
    assert f"sits on {place}, not on the contract" in issue.message


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_a_column_binding_on_the_schema_object_and_a_schema_binding_on_a_column_are_refused(
    family,
):
    make = _python if family == "python" else _dbt
    doc = make(schema=[_p("local", True)], column=[_p("entity", "Transaction")])
    assert _found(_check(family, doc)) == [
        ("schema[0].customProperties[0]", "refused_property"),
        ("schema[0].properties[0].customProperties[0]", "refused_property"),
    ]


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_a_binding_in_the_list_of_another_object_is_refused(family):
    make = _python if family == "python" else _dbt
    doc = make()
    doc["team"]["customProperties"] = [_p("entity", "Transaction")]
    [issue] = _check(family, doc)
    assert (issue.path, issue.reason) == (
        "team.customProperties[0]",
        "refused_property",
    )
    assert (
        "`entity` sits on the schema object, not on this object" in issue.message
    )


def test_a_quality_property_on_a_column_is_refused_by_the_place_it_sits_on():
    [issue] = _check_python(_python(column=[_p("where", "x = 1")]))
    assert issue.reason == "refused_property"
    assert "sits on a quality rule, not on a column" in issue.message


@pytest.mark.parametrize("family", ["python", "dbt"])
@pytest.mark.parametrize("name,value", MALFORMED)
def test_a_malformed_binding_is_a_value_issue(family, name, value):
    [issue] = _check(family, _at(family, name, value))
    assert issue.reason == "value"
    assert issue.path.endswith(".customProperties[0].value")
    assert f"`{name}`" in issue.message


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_the_value_issue_of_a_schema_binding_is_at_the_schema_object(family):
    [issue] = _check(family, _at(family, "grain", []))
    assert issue.path == "schema[0].customProperties[0].value"


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_the_value_issue_of_a_column_binding_is_at_the_column(family):
    [issue] = _check(family, _at(family, "local", False))
    assert issue.path == "schema[0].properties[0].customProperties[0].value"


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_each_fault_of_one_value_is_its_own_issue(family):
    value = {"from": "T.f.g", "agg": 1, "where": "x", "unknown": 2}
    issues = _check(family, _at(family, "aggregate", value))
    assert {issue.reason for issue in issues} == {"value"}
    assert {issue.path for issue in issues} == {
        "schema[0].properties[0].customProperties[0].value"
    }
    assert len(issues) == 4


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_an_aggregate_with_a_period_and_no_from_is_valid(family):
    value = {"period": "day", "agg": "count", "where": {"status": "ok"}}
    assert _check(family, _at(family, "aggregate", value)) == []


@pytest.mark.parametrize("family", ["python", "dbt"])
@pytest.mark.parametrize("source", ["Transaction", "Transaction.amount"])
def test_the_source_of_an_aggregate_is_a_thing_or_one_of_its_fields(
    family, source
):
    assert _check(family, _at(family, "aggregate", {"from": source})) == []


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_a_binding_written_twice_checks_the_first_copy_and_refuses_the_repeat(
    family,
):
    make = _python if family == "python" else _dbt
    doc = make(column=[_p("local", False), _p("local", True)])
    assert _found(_check(family, doc)) == [
        ("schema[0].properties[0].customProperties[0].value", "value"),
        ("schema[0].properties[0].customProperties[1]", "value"),
    ]


def test_two_binding_kinds_on_one_column_are_not_a_validation_issue():
    column = [_p("property", "Transaction.amount"), _p("local", True)]
    assert _check_python(_python(column=column)) == []
    assert validate_dbt_contract(_dbt(column=column)) == []
    both = [
        _p("property", "Transaction.amount"),
        _p("aggregate", {"period": "day"}),
    ]
    assert _check_python(_python(column=both)) == []
    assert validate_dbt_contract(_dbt(column=both)) == []


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_an_item_that_is_not_a_property_is_not_checked(family):
    make = _python if family == "python" else _dbt
    items = [
        {"vendor": "other", "property": "entity", "value": 42},
        {"vendor": "continuo", "property": "entity"},
        {"vendor": "continuo", "property": ["entity"], "value": 42},
        "entity",
    ]
    doc = make(schema=items)
    # The schema stage refuses a list item that is not an object, so the rules read it alone.
    found = (
        rule_issues(doc, path=PATH, project=PROJECT)
        if family == "python"
        else dbt_rule_issues(doc)
    )
    assert [i for i in found if i.reason == "value"] == []


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_a_binding_value_is_checked_even_when_the_node_has_other_faults(family):
    make = _python if family == "python" else _dbt
    doc = make(schema=[_p("entity", 42)])
    doc["customProperties"].append(_p("nonsense", 1))
    assert {issue.reason for issue in _check(family, doc)} == {
        "unknown_property",
        "value",
    }


def test_the_dbt_rules_check_a_stored_contract_the_same_way():
    stored = copy.deepcopy(json.loads(GOLDEN.read_text("ascii"))["stored"])
    assert dbt_rule_issues(stored) == []
    stored["schema"][0]["customProperties"] = [_p("entity", 42)]
    assert _found(dbt_rule_issues(stored)) == [
        ("schema[0].customProperties[0].value", "value")
    ]


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_the_split_aggregate_keys_are_unknown_properties(family):
    make = _python if family == "python" else _dbt
    for name in ("from", "agg", "period"):
        doc = make(column=[_p(name, "x")])
        assert _found(_check(family, doc)) == [
            ("schema[0].properties[0].customProperties[0]", "unknown_property")
        ]


@pytest.mark.parametrize("family", ["python", "dbt"])
def test_a_property_that_the_vocabulary_does_not_know_stays_unknown(family):
    make = _python if family == "python" else _dbt
    doc = make(schema=[_p("nonsense", 1)])
    assert _found(_check(family, doc)) == [
        ("schema[0].customProperties[0]", "unknown_property")
    ]


def test_the_name_of_a_thing_and_the_name_of_a_table_are_read_by_one_rule():
    # `is_name` is the rule for `<Thing>.<field>`, and `validate` has always held a table and a
    # schema to the same one: no whitespace, control character, dot or slash.
    characters = [chr(code) for code in range(0xA0)] + list(
        "\u00a0\u1680\u2003\u2028\u3000.\\/"
    )
    values = [
        *characters,
        *(f"a{character}b" for character in characters),
        "",
        " ",
        "name",
        "display name",
        "a.b",
        "a/b",
        3,
        None,
        ["a"],
    ]

    for value in values:
        assert is_name(value) is (validate._identifier_fault(value) is None), (
            repr(value)
        )
