"""The ontology bindings a dbt team writes in `meta.continuo`, as `dbt.generate` writes them."""

import copy
import json

from importlib import resources

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt
from continuo_contracts.contract import contract_hash, normalise_dbt
from continuo_contracts.validate import schema_issues, validate_dbt_contract
from continuo_contracts.vocabulary import VENDOR, continuo_values

GOVERNED = "model.cases.governed"
VECTOR = resources.files("continuo_contracts") / "vectors" / "dbt_contracts.json"


def _cases():
    return json.loads(VECTOR.read_text(encoding="utf-8"))


def _node(node_id=GOVERNED):
    return copy.deepcopy(load_manifest("cases")["nodes"][node_id])


def _generate(node, project="cases"):
    manifest = load_manifest(project)
    tests = dbt.tests_by_node(manifest).get(node["unique_id"], [])
    return dbt.generate(node, tests, manifest=manifest)


def _with_node_bindings(**bindings):
    node = _node()
    node["config"]["meta"]["continuo"].update(bindings)
    return node


def _with_column_bindings(column="email", **bindings):
    node = _node()
    node["columns"][column]["meta"].setdefault("continuo", {}).update(bindings)
    return node


def _schema_custom(document):
    """The `continuo` properties of the schema object, by name."""
    return continuo_values(document["schema"][0]["customProperties"])


def _column(document, name):
    return next(
        column
        for column in document["schema"][0]["properties"]
        if column["name"] == name
    )


def _column_custom(document, name):
    """The `continuo` properties of the column `name`, by name."""
    return continuo_values(_column(document, name).get("customProperties", []))


NODE_BINDINGS = {
    "entity": "Transaction",
    "grain": ["transaction_id"],
    "fixed": {"currency": "EUR", "version": 2, "live": True},
    "filter": {"status": "booked", "amount": 0, "voided": False},
}


def test_node_bindings_reach_the_schema_object():
    document = _generate(_with_node_bindings(**NODE_BINDINGS)).document
    assert _schema_custom(document) == {
        "reads": ["analytics.raw_events"],
        **NODE_BINDINGS,
    }
    for item in document["schema"][0]["customProperties"]:
        assert item["vendor"] == VENDOR


def test_the_bindings_of_a_node_are_written_on_the_schema_object_alone():
    document = _generate(_with_node_bindings(**NODE_BINDINGS)).document
    assert "entity" not in continuo_values(document["customProperties"])
    for name in ("id", "email"):
        assert _column_custom(document, name) == {}


def test_a_binding_written_on_the_schema_object_comes_from_the_node_not_a_column():
    node = _with_column_bindings(entity="Customer", grain=["id"])
    document = _generate(node).document
    assert _schema_custom(document) == {"reads": ["analytics.raw_events"]}
    assert _column_custom(document, "email") == {}


def test_column_bindings_reach_the_column():
    node = _with_column_bindings(property="Customer.email", local=True)
    document = _generate(node).document
    assert _column_custom(document, "email") == {
        "property": "Customer.email",
        "local": True,
    }
    assert _column(document, "email")["classification"] == "personal"
    assert _column(document, "email")["customProperties"][0]["vendor"] == VENDOR
    assert _column_custom(document, "id") == {}
    assert "customProperties" not in _column(document, "id")
    assert _schema_custom(document) == {"reads": ["analytics.raw_events"]}


def test_the_four_aggregate_keys_become_one_aggregate_property():
    node = _with_column_bindings(
        **{
            "from": "MarketingSpend.amount",
            "agg": "sum",
            "where": {"channel": "email"},
            "period": "month",
        }
    )
    document = _generate(node).document
    assert _column_custom(document, "email") == {
        "aggregate": {
            "agg": "sum",
            "from": "MarketingSpend.amount",
            "period": "month",
            "where": {"channel": "email"},
        }
    }
    (item,) = _column(document, "email")["customProperties"]
    assert list(item["value"]) == ["agg", "from", "period", "where"]
    assert item["property"] == "aggregate"


@pytest.mark.parametrize(
    "keys",
    [
        {"from": "Order"},
        {"from": "Order.amount", "agg": "sum"},
        {"period": "day"},
        {"from": "Order.amount", "period": "week"},
        {"agg": "count"},
        {"where": {"status": "paid"}},
    ],
)
def test_the_aggregate_holds_only_the_keys_present(keys):
    document = _generate(_with_column_bindings(**keys)).document
    assert _column_custom(document, "email") == {"aggregate": keys}


def test_a_column_with_no_aggregate_key_has_no_aggregate():
    document = _generate(
        _with_column_bindings(property="Customer.email")
    ).document
    assert "aggregate" not in _column_custom(document, "email")


def test_conflicting_bindings_are_all_written_for_the_checker():
    node = _with_column_bindings(
        property="Customer.email",
        local=True,
        **{"from": "Customer.email", "agg": "max"},
    )
    document = _generate(node).document
    assert _column_custom(document, "email") == {
        "property": "Customer.email",
        "local": True,
        "aggregate": {"agg": "max", "from": "Customer.email"},
    }


@pytest.mark.parametrize("local", [False, None])
def test_a_column_that_is_not_local_writes_nothing(local):
    node = _with_column_bindings(property="Customer.email", local=local)
    document = _generate(node).document
    assert _column_custom(document, "email") == {"property": "Customer.email"}
    only_local = _generate(_with_column_bindings(local=local)).document
    assert "customProperties" not in _column(only_local, "email")


def test_a_key_that_is_null_is_a_key_that_is_absent():
    nulls = {
        "entity": None,
        "grain": None,
        "fixed": None,
        "filter": None,
    }
    document = _generate(_with_node_bindings(**nulls)).document
    assert _schema_custom(document) == {"reads": ["analytics.raw_events"]}
    column = {
        "property": None,
        "local": None,
        "from": None,
        "agg": None,
        "where": None,
        "period": None,
    }
    document = _generate(_with_column_bindings(**column)).document
    assert "customProperties" not in _column(document, "email")


def test_a_node_that_declares_only_bindings_gets_its_schema_properties():
    node = _node("model.cases.incr")
    node["config"]["meta"]["continuo"] = {"entity": "Event"}
    document = _generate(node).document
    assert _schema_custom(document) == {"entity": "Event"}


@pytest.mark.parametrize(
    ("node_bindings", "column_bindings"),
    [
        ({"entity": ""}, {}),
        ({"entity": 7}, {}),
        ({"grain": []}, {}),
        ({"grain": "transaction_id"}, {}),
        ({"grain": [1, ""]}, {}),
        ({"fixed": ["currency"]}, {}),
        ({"fixed": {"currency": ["EUR"]}}, {}),
        ({"filter": "booked"}, {}),
        ({"filter": {"status": None}}, {}),
        ({}, {"property": "Customer"}),
        ({}, {"property": 3}),
        ({}, {"local": "yes"}),
        ({}, {"local": 0}),
        ({}, {"from": "Order.amount.x"}),
        ({}, {"agg": ""}),
        ({}, {"where": ["a"]}),
        ({}, {"agg": "sum"}),
    ],
)
def test_the_generator_writes_a_wrong_shape_and_validate_refuses_it(
    node_bindings, column_bindings
):
    node = _node()
    node["config"]["meta"]["continuo"].update(node_bindings)
    node["columns"]["email"]["meta"].setdefault("continuo", {}).update(
        column_bindings
    )
    document = _generate(node).document
    written = {**_schema_custom(document), **_column_custom(document, "email")}
    for name, value in node_bindings.items():
        assert written[name] == value
    reasons = {issue.reason for issue in validate_dbt_contract(document)}
    assert reasons == {"value"}


def test_a_well_shaped_declaration_is_a_valid_contract():
    node = _with_node_bindings(**NODE_BINDINGS)
    node["columns"]["email"]["meta"]["continuo"].update(
        property="Customer.email", local=True
    )
    node["columns"]["id"]["meta"]["continuo"] = {
        "from": "Order",
        "agg": "count",
        "where": {"status": "paid"},
        "period": "day",
    }
    document = _generate(node).document
    assert validate_dbt_contract(document) == []
    assert schema_issues(document) == []


def test_a_written_value_is_a_copy_of_the_manifest_value():
    node = _with_node_bindings(**NODE_BINDINGS)
    node["columns"]["id"]["meta"]["continuo"] = {
        "from": "Order",
        "where": {"status": "paid"},
    }
    document = _generate(node).document
    document["schema"][0]["customProperties"].clear()
    _column(document, "id")["customProperties"][0]["value"]["where"]["status"] = (
        "x"
    )
    assert node["config"]["meta"]["continuo"]["grain"] == ["transaction_id"]
    _schema_custom(_generate(node).document)["fixed"]["currency"] = "USD"
    assert node["config"]["meta"]["continuo"]["fixed"]["currency"] == "EUR"
    assert node["columns"]["id"]["meta"]["continuo"]["where"] == {
        "status": "paid"
    }


def test_the_bindings_reach_the_stored_form_and_move_the_contract_hash():
    plain = _generate(_node()).document
    bound = _generate(_with_node_bindings(**NODE_BINDINGS)).document
    stored = normalise_dbt(bound, reads=[])
    assert _schema_custom(stored) == {
        "reads": ["analytics.raw_events"],
        **NODE_BINDINGS,
    }
    assert normalise_dbt(stored, reads=[]) == stored
    assert contract_hash(stored) != contract_hash(normalise_dbt(plain, reads=[]))


def test_the_document_does_not_depend_on_the_order_of_the_keys():
    node = _with_node_bindings(**NODE_BINDINGS)
    node["columns"]["email"]["meta"]["continuo"].update(
        {
            "period": "day",
            "from": "Order",
            "agg": "count",
            "property": "Customer.email",
        }
    )
    reordered = _node()
    reordered["config"]["meta"]["continuo"].update(
        reversed(NODE_BINDINGS.items())
    )
    reordered["columns"]["email"]["meta"]["continuo"].update(
        {
            "property": "Customer.email",
            "agg": "count",
            "from": "Order",
            "period": "day",
        }
    )
    first, second = _generate(node), _generate(reordered)
    assert first.document == second.document
    assert first.source_sha256 == second.source_sha256


def test_a_node_with_no_binding_keeps_its_source_sha256():
    cases = _cases()
    assert cases
    for case in cases:
        generated = dbt.generate(
            case["node"], case["tests"], manifest=case["manifest"]
        )
        assert generated.source_sha256 == case["source_sha256"], case["name"]


def test_a_node_with_no_binding_has_no_binding_property():
    for case in _cases():
        if "generated" not in case:
            continue
        document = dbt.generate(
            case["node"], case["tests"], manifest=case["manifest"]
        ).document
        assert document == case["generated"], case["name"]
        for column in document["schema"][0].get("properties", []):
            assert "customProperties" not in column, case["name"]


BINDING_FIELDS = (
    ("config.meta.continuo.entity", "Transaction", _with_node_bindings),
    ("config.meta.continuo.grain", ["transaction_id"], _with_node_bindings),
    ("config.meta.continuo.fixed", {"currency": "EUR"}, _with_node_bindings),
    ("config.meta.continuo.filter", {"status": "booked"}, _with_node_bindings),
    (
        "columns.email.meta.continuo.property",
        "Customer.email",
        _with_column_bindings,
    ),
    ("columns.email.meta.continuo.local", True, _with_column_bindings),
    ("columns.email.meta.continuo.from", "Order.amount", _with_column_bindings),
    ("columns.email.meta.continuo.agg", "sum", _with_column_bindings),
    (
        "columns.email.meta.continuo.where",
        {"status": "paid"},
        _with_column_bindings,
    ),
    ("columns.email.meta.continuo.period", "day", _with_column_bindings),
)


@pytest.mark.parametrize(
    ("path", "value", "build"),
    BINDING_FIELDS,
    ids=[path for path, _, _ in BINDING_FIELDS],
)
def test_a_binding_moves_the_source_sha256(path, value, build):
    before = _generate(_node()).source_sha256
    key = path.rpartition(".")[2]
    assert _generate(build(**{key: value})).source_sha256 != before


def test_the_binding_fields_are_declared_as_read():
    paths = {
        path.replace("columns.email", "columns.*")
        for path, _, _ in BINDING_FIELDS
    }
    assert paths <= set(dbt._READ_PATHS)
    tree = dbt.read_tree("node")
    assert set(tree["config"]["meta"]["continuo"]) >= {
        "entity",
        "grain",
        "fixed",
        "filter",
    }
    assert set(tree["columns"]["*"]["meta"]["continuo"]) == {
        "classification",
        "property",
        "local",
        "from",
        "agg",
        "where",
        "period",
    }
