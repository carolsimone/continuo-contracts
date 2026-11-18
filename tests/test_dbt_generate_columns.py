"""The columns, column types, quality rules and flags of a generated dbt contract."""

import copy

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt
from continuo_contracts.canonical import canonical_json
from continuo_contracts.errors import DbtNodeError
from continuo_contracts.validate import schema_issues
from continuo_contracts.vocabulary import PROPERTIES, VENDOR, continuo_values

GENERIC = "model.cases.generic_tests"


def _manifest(project):
    """Return a copy of the fixture manifest `project` that a test may change."""
    return copy.deepcopy(load_manifest(project))


def _run(manifest, node_id):
    node = manifest["nodes"][node_id]
    tests = dbt.tests_by_node(manifest).get(node_id, [])
    return dbt.generate(node, tests, manifest=manifest)


def _generated(project, node_id):
    return _run(load_manifest(project), node_id)


def _doc(project, node_id):
    return _generated(project, node_id).document


def _props(document):
    """Return the properties of the schema object by name."""
    return {
        column["name"]: column
        for column in document["schema"][0].get("properties", [])
    }


def _all_quality(document):
    """Return every quality rule of the contract: the schema object's, then each column's."""
    table = document["schema"][0]
    rules = list(table.get("quality", []))
    for column in table.get("properties", []):
        rules += column.get("quality", [])
    return rules


def _test(manifest, name):
    """Return the test node called `name` of `manifest`."""
    (test,) = (
        node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test" and node["name"] == name
    )
    return test


def _edited(project, name, change):
    """Return a copy of the manifest `project` whose test `name` went through `change`."""
    manifest = _manifest(project)
    change(_test(manifest, name))
    return manifest


def _cp(name, value):
    return {"vendor": VENDOR, "property": name, "value": value}


def test_fx_transactions_eur_columns_are_the_designs():
    props = _doc("finance", "model.finance.fx_transactions_eur")["schema"][0][
        "properties"
    ]
    tid = props[0]
    assert (
        tid["name"] == "transaction_id"
        and tid["required"] is True
        and tid["unique"] is True
    )
    assert tid["primaryKey"] is True
    assert {q["metric"] for q in tid["quality"]} == {
        "duplicateValues",
        "nullValues",
    }


def _not_null(table, column):
    return {
        "id": f"not_null_{table}_{column}",
        "metric": "nullValues",
        "mustBe": 0,
    }


def test_fx_transactions_eur_reads_as_the_designs_contract():
    # The design's example, except that a single-column key has no `primaryKeyPosition` and an
    # error test no `severity`. Quality rules come in the order of their canonical JSON.
    table = "fx_transactions_eur"
    schema = _doc("finance", f"model.finance.{table}")["schema"][0]
    assert schema["quality"] == [
        {
            "id": "assert_fx_transactions_eur_amount",
            "name": "assert_fx_transactions_eur_amount",
            "type": "custom",
            "engine": "dbt",
            "implementation": "assert_fx_transactions_eur_amount",
        }
    ]
    assert schema["properties"] == [
        {
            "name": "transaction_id",
            "primaryKey": True,
            "required": True,
            "unique": True,
            "quality": [
                _not_null(table, "transaction_id"),
                {
                    "id": "unique_fx_transactions_eur_transaction_id",
                    "metric": "duplicateValues",
                    "mustBe": 0,
                },
            ],
        },
        {
            "name": "rate_to_eur",
            "description": "Multiplier applied to amount; NULL means an uncovered "
            "(currency, date) pair.",
            "required": True,
            "quality": [_not_null(table, "rate_to_eur")],
        },
        {
            "name": "amount_eur",
            "required": True,
            "quality": [_not_null(table, "amount_eur")],
        },
        {
            "name": "fee_amount",
            "description": "Fee the company earned, denominated in currency_from.",
            "required": True,
            "quality": [_not_null(table, "fee_amount")],
        },
        {
            "name": "fee_amount_eur",
            "description": "fee_amount converted to EUR on the same rate_to_eur join as "
            "amount_eur.",
            "required": True,
            "quality": [_not_null(table, "fee_amount_eur")],
        },
    ]


def test_seed_users_has_no_columns_and_the_singular_test_it_feeds():
    # The core fixture declares no column of `seed_users`. The singular test that reads it and
    # `daily_transactions` counts for both, as `tests_by_node` joins it.
    schema = _doc("core", "seed.core.seed_users")["schema"][0]
    assert "properties" not in schema
    assert schema["quality"] == [
        {
            "id": "assert_transactions_after_acquisition",
            "name": "assert_transactions_after_acquisition",
            "type": "custom",
            "engine": "dbt",
            "implementation": "assert_transactions_after_acquisition",
        }
    ]


def _declared_column(name, **fields):
    """Return a column as dbt writes one into a manifest."""
    column = {
        "config": {"meta": {}, "tags": []},
        "constraints": [],
        "data_type": None,
        "description": "",
        "meta": {},
        "name": name,
        "quote": None,
        "tags": [],
    }
    column.update(fields)
    return column


def test_seed_users_with_its_declarations_reads_as_the_designs_contract():
    manifest = _manifest("core")
    seed = manifest["nodes"]["seed.core.seed_users"]
    personal = {"continuo": {"classification": "personal"}}
    seed["columns"] = {
        "user_id": _declared_column("user_id"),
        "name": _declared_column("name", meta=personal),
        "email": _declared_column("email", meta=personal),
        "birth_year": _declared_column("birth_year", meta=personal),
        "created_at": _declared_column("created_at"),
    }
    for kind in ("unique", "not_null"):
        test = copy.deepcopy(_test(manifest, f"{kind}_revenue_per_user_user_id"))
        test.update(
            name=f"{kind}_seed_users_user_id",
            unique_id=f"test.core.{kind}_seed_users_user_id.0",
            attached_node="seed.core.seed_users",
            depends_on={"macros": [], "nodes": ["seed.core.seed_users"]},
        )
        manifest["nodes"][test["unique_id"]] = test
    assert _run(manifest, "seed.core.seed_users").document["schema"][0][
        "properties"
    ] == [
        {
            "name": "user_id",
            "required": True,
            "unique": True,
            "quality": [
                {
                    "id": "not_null_seed_users_user_id",
                    "metric": "nullValues",
                    "mustBe": 0,
                },
                {
                    "id": "unique_seed_users_user_id",
                    "metric": "duplicateValues",
                    "mustBe": 0,
                },
            ],
        },
        {"name": "name", "classification": "personal"},
        {"name": "email", "classification": "personal"},
        {"name": "birth_year", "classification": "personal"},
        {"name": "created_at"},
    ]


def test_accepted_values_in_both_syntaxes():
    for project, node_id, table in (
        (
            "finance",
            "seed.finance.seed_operational_costs",
            "seed_operational_costs",
        ),
        (
            "marketing",
            "seed.marketing.seed_marketing_spend",
            "seed_marketing_spend",
        ),
    ):
        currency = _props(_doc(project, node_id))["currency"]
        assert {
            "id": f"accepted_values_{table}_currency__EUR",
            "metric": "invalidValues",
            "mustBe": 0,
            "arguments": {"validValues": ["EUR"]},
        } in currency["quality"]
    props = _props(_doc("cases", GENERIC))
    # `status` puts the values beside the test, `kind` nests them under `arguments`.
    assert props["status"]["quality"] == [
        {
            "id": "accepted_values_generic_tests_status__active__closed",
            "metric": "invalidValues",
            "mustBe": 0,
            "arguments": {"validValues": ["active", "closed"]},
        }
    ]
    assert props["kind"]["quality"] == [
        {
            "id": "accepted_values_generic_tests_kind__a__b",
            "metric": "invalidValues",
            "mustBe": 0,
            "arguments": {"validValues": ["a", "b"]},
        }
    ]
    assert props["status"]["description"] == (
        "accepted_values in the syntax that puts the values beside the test."
    )


@pytest.mark.parametrize(
    ("name", "values"),
    [
        (
            "accepted_values_generic_tests_status__active__closed",
            ["active", "closed"],
        ),
        ("accepted_values_generic_tests_kind__a__b", ["a", "b"]),
    ],
)
def test_accepted_values_keeps_its_other_arguments(name, values):
    # `status` puts the values beside the test and `kind` nests them under `arguments`; a copy of
    # each gets `quote: false`, which changes what the test compares.
    manifest = _manifest("cases")
    unquoted = copy.deepcopy(_test(manifest, name))
    unquoted["test_metadata"]["kwargs"]["quote"] = False
    unquoted.update(
        name=f"{name}_unquoted", unique_id=f"test.cases.{name}_unquoted.0"
    )
    manifest["nodes"][unquoted["unique_id"]] = unquoted
    column = _props(_run(manifest, GENERIC).document)[unquoted["column_name"]]
    rules = {rule["id"]: rule for rule in column["quality"]}
    assert rules[name]["arguments"] == {"validValues": values}
    assert rules[f"{name}_unquoted"]["arguments"] == {
        "validValues": values,
        "quote": False,
    }


def test_relationships_to_a_model_and_to_a_source():
    props = _props(_doc("cases", GENERIC))
    assert props["customer_id"] == {
        "name": "customer_id",
        "relationships": [{"to": "analytics.customer.v2.id"}],
    }
    assert props["event_id"] == {
        "name": "event_id",
        "relationships": [{"to": "raw.events.id"}],
    }


def test_a_relationship_to_its_own_node_names_the_node():
    def to_itself(test):
        test["depends_on"]["nodes"] = [GENERIC]

    manifest = _edited(
        "cases",
        "relationships_generic_tests_customer_id__id__ref_customer_",
        to_itself,
    )
    assert _props(_run(manifest, GENERIC).document)["customer_id"][
        "relationships"
    ] == [{"to": "analytics.generic_tests.id"}]


def test_a_source_with_no_identifier_is_named_by_its_name():
    manifest = _manifest("cases")
    manifest["sources"]["source.cases.raw.events"]["identifier"] = None
    manifest["sources"]["source.cases.raw.events"]["name"] = "Events"
    assert _props(_run(manifest, GENERIC).document)["event_id"][
        "relationships"
    ] == [{"to": "raw.events.id"}]


_TO_CUSTOMER = "relationships_generic_tests_customer_id__id__ref_customer_"


def test_a_strict_relationships_test_keeps_only_the_relationship():
    def described(test):
        test["description"] = "Customers exist."
        test["tags"] = ["fk"]

    manifest = _edited("cases", _TO_CUSTOMER, described)
    assert _props(_run(manifest, GENERIC).document)["customer_id"] == {
        "name": "customer_id",
        "relationships": [{"to": "analytics.customer.v2.id"}],
    }


@pytest.mark.parametrize(
    ("config", "written"),
    [
        ({"severity": "warn"}, {"severity": "warning"}),
        ({"where": "id > 0"}, {"customProperties": [_cp("where", "id > 0")]}),
        ({"error_if": "> 1"}, {"customProperties": [_cp("errorIf", "> 1")]}),
        ({"warn_if": "> 1"}, {"customProperties": [_cp("warnIf", "> 1")]}),
        (
            {"fail_calc": "sum(n)"},
            {"customProperties": [_cp("failCalc", "sum(n)")]},
        ),
    ],
    ids=["warn", "where", "error_if", "warn_if", "fail_calc"],
)
def test_a_relationships_test_that_is_not_strict_is_a_custom_rule(
    config, written
):
    def loosened(test):
        test["config"].update(config)
        test["description"] = "Customers exist."
        test["tags"] = ["fk"]

    manifest = _edited("cases", _TO_CUSTOMER, loosened)
    generated = _run(manifest, GENERIC)
    assert _props(generated.document)["customer_id"] == {
        "name": "customer_id",
        "quality": [
            {
                "id": _TO_CUSTOMER,
                "name": _TO_CUSTOMER,
                "type": "custom",
                "engine": "dbt",
                "implementation": {
                    "test": "relationships",
                    "arguments": {"field": "id", "to": "ref('customer')"},
                },
                **written,
                "description": "Customers exist.",
                "tags": ["fk"],
            }
        ],
    }
    assert schema_issues(generated.document) == []
    # The relationship's target is not read, so it is not in `source_sha256`.
    target = manifest["nodes"]["model.cases.customer.v2"]
    before = generated.source_sha256
    target["schema"] = "elsewhere"
    assert _run(manifest, GENERIC).source_sha256 == before


def test_a_relationships_test_that_is_not_strict_keeps_its_jinja_flag():
    def loosened(test):
        test["config"]["severity"] = "warn"
        test["test_metadata"]["kwargs"]["to"] = "{{ ref('customer') }}"

    manifest = _edited("cases", _TO_CUSTOMER, loosened)
    generated = _run(manifest, GENERIC)
    assert "jinja_in_test_arguments" in generated.flags
    (rule,) = _props(generated.document)["customer_id"]["quality"]
    assert rule["implementation"]["arguments"]["to"] == "{{ ref('customer') }}"


def test_a_warn_test_is_severity_warning_and_sets_no_required():
    warned = _props(_doc("cases", GENERIC))["warned"]
    assert "required" not in warned
    assert warned["quality"] == [
        {
            "id": "not_null_generic_tests_warned",
            "metric": "nullValues",
            "mustBe": 0,
            "severity": "warning",
        }
    ]
    # An error test writes no severity, in whatever case dbt has it.
    assert "severity" not in _props(_doc("cases", GENERIC))["id"]["quality"][0]


def test_where_and_thresholds_are_custom_properties_and_set_no_required():
    props = _props(_doc("cases", GENERIC))
    assert "required" not in props["filtered"]
    assert props["filtered"]["quality"] == [
        {
            "id": "not_null_generic_tests_filtered",
            "metric": "nullValues",
            "mustBe": 0,
            "customProperties": [_cp("where", "id > 0")],
        }
    ]
    assert "unique" not in props["lenient"]
    assert props["lenient"]["quality"][0]["customProperties"] == [
        _cp("errorIf", ">10")
    ]

    def thresholds(test):
        test["config"].update(warn_if="> 5", fail_calc="sum(n_rows)")

    manifest = _edited("cases", "not_null_generic_tests_id", thresholds)
    column = _props(_run(manifest, GENERIC).document)["id"]
    assert "required" not in column
    assert column["unique"] is True
    (rule,) = [q for q in column["quality"] if q["metric"] == "nullValues"]
    assert rule["customProperties"] == [
        _cp("warnIf", "> 5"),
        _cp("failCalc", "sum(n_rows)"),
    ]
    for item in (
        rule["customProperties"]
        + props["filtered"]["quality"][0]["customProperties"]
    ):
        assert PROPERTIES[item["property"]].sits_on == "quality"


@pytest.mark.parametrize(
    ("key", "value"),
    [("fail_calc", "sum(n_rows)"), ("warn_if", "> 5"), ("where", "id > 0")],
)
def test_each_non_default_setting_alone_blocks_unique(key, value):
    def change(test):
        test["config"][key] = value

    manifest = _edited("cases", "unique_generic_tests_id", change)
    assert "unique" not in _props(_run(manifest, GENERIC).document)["id"]


def test_settings_at_dbt_defaults_are_left_out():
    def defaults(test):
        test["config"].update(
            severity=None, where="", warn_if=None, error_if="!= 0", fail_calc=None
        )

    manifest = _edited("cases", "not_null_generic_tests_id", defaults)
    column = _props(_run(manifest, GENERIC).document)["id"]
    assert column["required"] is True
    assert {
        "id": "not_null_generic_tests_id",
        "metric": "nullValues",
        "mustBe": 0,
    } in (column["quality"])


def test_a_custom_generic_test_is_type_custom_engine_dbt_with_its_arguments():
    amount = _props(_doc("cases", GENERIC))["amount"]
    assert amount["quality"] == [
        {
            "id": "is_positive_generic_tests_amount__0",
            "name": "is_positive_generic_tests_amount__0",
            "type": "custom",
            "engine": "dbt",
            "implementation": {
                "test": "is_positive",
                "arguments": {"min_value": 0},
            },
        }
    ]


def test_a_namespaced_test_names_its_package_and_is_never_a_built_in():
    def in_package(test):
        test["test_metadata"]["namespace"] = "dbt_utils"

    manifest = _edited("cases", "is_positive_generic_tests_amount__0", in_package)
    (rule,) = _props(_run(manifest, GENERIC).document)["amount"]["quality"]
    assert rule["implementation"]["test"] == "dbt_utils.is_positive"

    def a_package_not_null(test):
        test["test_metadata"]["namespace"] = "my_package"

    manifest = _edited("cases", "not_null_generic_tests_id", a_package_not_null)
    column = _props(_run(manifest, GENERIC).document)["id"]
    assert "required" not in column
    assert {
        "id": "not_null_generic_tests_id",
        "name": "not_null_generic_tests_id",
        "type": "custom",
        "engine": "dbt",
        "implementation": {"test": "my_package.not_null"},
    } in column["quality"]


def test_a_test_description_and_tags_become_the_rules():
    def described(test):
        test["description"] = "  Every row has an id.\n"
        test["tags"] = ["nightly", "core"]

    manifest = _edited("cases", "not_null_generic_tests_id", described)
    column = _props(_run(manifest, GENERIC).document)["id"]
    assert {
        "id": "not_null_generic_tests_id",
        "metric": "nullValues",
        "mustBe": 0,
        "description": "Every row has an id.",
        "tags": ["nightly", "core"],
    } in column["quality"]


def test_a_singular_test_sits_on_the_schema_object():
    schema = _doc("cases", "model.cases.singular_target")["schema"][0]
    assert schema["quality"] == [
        {
            "id": "assert_singular_target",
            "name": "assert_singular_target",
            "type": "custom",
            "engine": "dbt",
            "implementation": "assert_singular_target",
        }
    ]
    assert "properties" not in schema

    def warned(test):
        test["config"]["severity"] = "Warn"

    manifest = _edited("cases", "assert_singular_target", warned)
    (rule,) = _run(manifest, "model.cases.singular_target").document["schema"][0][
        "quality"
    ]
    assert rule["severity"] == "warning"


def test_jinja_in_test_arguments_is_kept_and_flagged():
    g = _generated("cases", GENERIC)
    assert "jinja_in_test_arguments" in g.flags
    assert any(
        q.get("arguments", {}).get("validValues") == "{{ var('allowed') }}"
        for q in _all_quality(g.document)
    )


@pytest.mark.parametrize(
    ("name", "change"),
    [
        (
            "assert_singular_target",
            lambda test: test["config"].update(
                where="{% if true %}id > 0{% endif %}"
            ),
        ),
        (
            "is_positive_generic_tests_amount__0",
            lambda test: test["test_metadata"]["kwargs"].update(
                bounds={"low": ["{{ var('low') }}"]}
            ),
        ),
    ],
)
def test_jinja_in_a_where_or_a_nested_argument_is_flagged(name, change):
    # The `allowed_status` test is the fixture's own Jinja: it gets plain values first.
    manifest = _edited(
        "cases",
        "accepted_values_generic_tests_allowed_status___var_allowed_",
        lambda test: test["test_metadata"]["kwargs"].update(values=["open"]),
    )
    target = (
        "model.cases.singular_target" if name.startswith("assert") else GENERIC
    )
    assert "jinja_in_test_arguments" not in _run(manifest, target).flags
    change(_test(manifest, name))
    generated = _run(manifest, target)
    assert "jinja_in_test_arguments" in generated.flags
    assert generated.flags == tuple(
        flag for flag in dbt.FLAGS if flag in generated.flags
    )


def test_a_node_with_plain_tests_has_no_flags():
    assert _generated("finance", "model.finance.fx_transactions_eur").flags == ()
    assert _generated("cases", "model.cases.singular_target").flags == ()


def test_column_types_win_over_data_type_on_a_seed():
    assert _doc("cases", "seed.cases.typed_seed")["schema"][0]["properties"] == [
        {"name": "id", "physicalType": "integer", "logicalType": "integer"},
        {
            "name": "amount",
            "physicalType": "numeric(12,2)",
            "logicalType": "number",
        },
        {"name": "label", "physicalType": "text", "logicalType": "string"},
    ]


def test_column_types_are_a_seeds_only():
    manifest = _manifest("cases")
    manifest["nodes"]["model.cases.enforced"]["config"]["column_types"] = {
        "id": "bigint"
    }
    assert _props(_run(manifest, "model.cases.enforced").document)["id"][
        "physicalType"
    ] == ("integer")


def test_a_blank_column_type_falls_back_to_data_type_and_both_are_trimmed():
    manifest = _manifest("cases")
    seed = manifest["nodes"]["seed.cases.typed_seed"]
    seed["config"]["column_types"] = {"amount": "  ", "id": None}
    seed["columns"]["amount"]["data_type"] = " Decimal(10, 2) "
    seed["columns"]["label"]["data_type"] = "   "
    props = _props(_run(manifest, "seed.cases.typed_seed").document)
    assert props["amount"]["physicalType"] == "Decimal(10, 2)"
    assert props["amount"]["logicalType"] == "number"
    assert props["id"]["physicalType"] == "integer"
    assert props["label"] == {"name": "label"}


def test_quote_columns_is_written_when_set():
    root = continuo_values(
        _doc("cases", "seed.cases.typed_seed")["customProperties"]
    )
    assert root["quoteColumns"] is True
    manifest = _manifest("cases")
    manifest["nodes"]["seed.cases.typed_seed"]["config"]["quote_columns"] = False
    root = continuo_values(
        _run(manifest, "seed.cases.typed_seed").document["customProperties"]
    )
    assert root["quoteColumns"] is False


def test_snapshot_history_columns_follow_meta_column_names():
    props = _doc("cases", "snapshot.cases.orders_snapshot")["schema"][0][
        "properties"
    ]
    assert props == [
        {"name": "dbt_scd_id"},
        {"name": "dbt_updated_at"},
        {"name": "valid_from"},
        {"name": "dbt_valid_to"},
        {"name": "dbt_is_deleted"},
    ]


def test_history_columns_come_after_the_declared_ones_once_each():
    manifest = _manifest("cases")
    snapshot = manifest["nodes"]["snapshot.cases.orders_snapshot"]
    snapshot["columns"] = {
        "id": _declared_column("id", data_type="integer"),
        "valid_from": _declared_column("valid_from", description="Since."),
    }
    snapshot["config"]["hard_deletes"] = "invalidate"
    snapshot["config"]["snapshot_meta_column_names"] = None
    names = [
        column["name"]
        for column in _run(manifest, "snapshot.cases.orders_snapshot").document[
            "schema"
        ][0]["properties"]
    ]
    assert names == [
        "id",
        "valid_from",
        "dbt_scd_id",
        "dbt_updated_at",
        "dbt_valid_from",
        "dbt_valid_to",
    ]
    snapshot["columns"]["dbt_scd_id"] = _declared_column("dbt_scd_id")
    names = [
        column["name"]
        for column in _run(manifest, "snapshot.cases.orders_snapshot").document[
            "schema"
        ][0]["properties"]
    ]
    assert names.count("dbt_scd_id") == 1
    assert names[:3] == ["id", "valid_from", "dbt_scd_id"]


def test_a_model_or_seed_gets_no_history_columns():
    manifest = _manifest("cases")
    seed = manifest["nodes"]["seed.cases.typed_seed"]
    seed["config"]["hard_deletes"] = "new_record"
    names = [
        column["name"]
        for column in _run(manifest, "seed.cases.typed_seed").document["schema"][
            0
        ]["properties"]
    ]
    assert names == ["id", "amount", "label"]


def test_enforced_contract_and_its_primary_key_constraint():
    doc = _doc("cases", "model.cases.enforced")
    assert continuo_values(doc["customProperties"])["contractEnforced"] is True
    assert doc["schema"][0]["properties"] == [
        {
            "name": "id",
            "physicalType": "integer",
            "logicalType": "integer",
            "primaryKey": True,
        },
        {"name": "name", "physicalType": "text", "logicalType": "string"},
        {
            "name": "created_at",
            "physicalType": "timestamp",
            "logicalType": "timestamp",
        },
    ]


def test_a_key_of_two_columns_has_positions_in_its_order():
    manifest = _manifest("cases")
    manifest["nodes"][GENERIC]["primary_key"] = ["status", "id"]
    props = _props(_run(manifest, GENERIC).document)
    assert (
        props["status"]["primaryKey"],
        props["status"]["primaryKeyPosition"],
    ) == (
        True,
        1,
    )
    assert (props["id"]["primaryKey"], props["id"]["primaryKeyPosition"]) == (
        True,
        2,
    )
    assert "primaryKey" not in props["kind"]


def test_a_primary_key_on_a_seed_or_snapshot_is_not_read():
    manifest = _manifest("cases")
    manifest["nodes"]["seed.cases.typed_seed"]["primary_key"] = ["id"]
    props = _props(_run(manifest, "seed.cases.typed_seed").document)
    assert "primaryKey" not in props["id"]


def test_a_primary_key_column_the_node_does_not_declare_is_left_out_and_flagged():
    manifest = _manifest("cases")
    manifest["nodes"][GENERIC]["primary_key"] = ["id", "tenant_id"]
    generated = _run(manifest, GENERIC)
    props = _props(generated.document)
    assert "tenant_id" not in props
    assert props["id"]["primaryKeyPosition"] == 1
    assert "primary_key_column_not_declared" in generated.flags
    assert (
        "primary_key_column_not_declared"
        not in _generated("cases", GENERIC).flags
    )


def test_table_tests_use_engine_data_quality():
    schema = _doc("cases", "model.cases.governed")["schema"][0]
    assert schema["quality"] == [
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "row_count",
            "implementation": {
                "enabled": True,
                "granularity": "daily",
                "params": {"min": 1},
            },
        }
    ]


def test_table_tests_keep_their_order_after_the_test_rules():
    manifest = _manifest("cases")
    node = manifest["nodes"]["model.cases.singular_target"]
    node["config"]["meta"]["continuo"] = {
        "tests": [
            {"name": "zz_last_written_first", "enabled": False},
            {
                "name": "aa_second",
                "enabled": None,
                "params": None,
                "granularity": "",
            },
        ]
    }
    quality = _run(manifest, "model.cases.singular_target").document["schema"][0][
        "quality"
    ]
    assert quality == [
        {
            "id": "assert_singular_target",
            "name": "assert_singular_target",
            "type": "custom",
            "engine": "dbt",
            "implementation": "assert_singular_target",
        },
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "zz_last_written_first",
            "implementation": {"enabled": False},
        },
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "aa_second",
            "implementation": {"enabled": True},
        },
    ]


def test_classification_and_column_tags():
    assert _doc("cases", "model.cases.governed")["schema"][0]["properties"] == [
        {"name": "id"},
        {"name": "email", "tags": ["pii"], "classification": "personal"},
    ]


def test_a_test_on_an_undeclared_column_is_flagged():
    manifest = _manifest("cases")
    columns = manifest["nodes"][GENERIC]["columns"]
    for name in ("id", "status", "amount", "customer_id"):
        del columns[name]
    generated = _run(manifest, GENERIC)
    assert "test_column_not_declared" in generated.flags
    # `id` is the node's key too, so that flag is raised as well.
    assert "primary_key_column_not_declared" in generated.flags
    schema = generated.document["schema"][0]
    assert "id" not in _props(generated.document)
    assert not any(column.get("required") for column in schema["properties"])
    quality = {rule["id"]: rule for rule in schema["quality"]}
    assert quality["not_null_generic_tests_id"] == {
        "id": "not_null_generic_tests_id",
        "metric": "nullValues",
        "mustBe": 0,
        "arguments": {"column_name": "id"},
    }
    assert quality["accepted_values_generic_tests_status__active__closed"][
        "arguments"
    ] == {"validValues": ["active", "closed"], "column_name": "status"}
    assert quality["is_positive_generic_tests_amount__0"]["implementation"] == {
        "test": "is_positive",
        "arguments": {"min_value": 0, "column_name": "amount"},
    }
    relationship = quality[
        "relationships_generic_tests_customer_id__id__ref_customer_"
    ]
    assert relationship == {
        "id": "relationships_generic_tests_customer_id__id__ref_customer_",
        "name": "relationships_generic_tests_customer_id__id__ref_customer_",
        "type": "custom",
        "engine": "dbt",
        "implementation": {
            "test": "relationships",
            "arguments": {
                "field": "id",
                "to": "ref('customer')",
                "column_name": "customer_id",
            },
        },
    }
    assert schema_issues(generated.document) == []
    assert "test_column_not_declared" not in _generated("cases", GENERIC).flags


def test_a_generic_test_with_no_column_sits_on_the_schema_object_unflagged():
    def model_level(test):
        test["column_name"] = None
        test["test_metadata"]["kwargs"].pop("column_name")

    manifest = _edited(
        "cases", "is_positive_generic_tests_amount__0", model_level
    )
    generated = _run(manifest, GENERIC)
    assert "test_column_not_declared" not in generated.flags
    rules = [
        rule
        for rule in generated.document["schema"][0]["quality"]
        if rule["id"] == "is_positive_generic_tests_amount__0"
    ]
    assert rules[0]["implementation"] == {
        "test": "is_positive",
        "arguments": {"min_value": 0},
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("integer", {"logicalType": "integer"}),
        ("NUMERIC(12,2)", {"logicalType": "number"}),
        (
            "varchar(64)",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 64}},
        ),
        ("timestamp with time zone", {"logicalType": "timestamp"}),
        ("geometry", None),
        ("", None),
        ("int", {"logicalType": "integer"}),
        ("int2", {"logicalType": "integer"}),
        ("int4", {"logicalType": "integer"}),
        ("int8", {"logicalType": "integer"}),
        ("BIGINT", {"logicalType": "integer"}),
        ("smallint", {"logicalType": "integer"}),
        ("decimal(10, 2)", {"logicalType": "number"}),
        ("double precision", {"logicalType": "number"}),
        ("Double  Precision", {"logicalType": "number"}),
        ("float", {"logicalType": "number"}),
        ("float8", {"logicalType": "number"}),
        ("real", {"logicalType": "number"}),
        ("double", {"logicalType": "number"}),
        ("text", {"logicalType": "string"}),
        ("string", {"logicalType": "string"}),
        ("varchar", {"logicalType": "string"}),
        (
            " Character Varying ( 10 ) ",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 10}},
        ),
        (
            "char(3)",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 3}},
        ),
        ("character", {"logicalType": "string"}),
        ("varchar(0)", {"logicalType": "string"}),
        ("varchar(max)", {"logicalType": "string"}),
        ("varchar(99999999999999999)", {"logicalType": "string"}),
        ("date", {"logicalType": "date"}),
        ("timestamp", {"logicalType": "timestamp"}),
        ("timestamp(6) with time zone", {"logicalType": "timestamp"}),
        ("timestamp(6) without time zone", {"logicalType": "timestamp"}),
        ("TIMESTAMP(3)  WITH  TIME  ZONE", {"logicalType": "timestamp"}),
        ("timestamp(3)\twith\ttime zone ", {"logicalType": "timestamp"}),
        ("varchar(10) with time zone", None),
        ("time(3) with time zone", None),
        ("timestamptz(3) with time zone", None),
        ("timestamp(3) with time zone x", None),
        ("timestamp(3) with zone", None),
        ("timestamp(3)with time zone", None),
        ("timestamp(3) with\ntime zone", None),
        ("timestamptz", {"logicalType": "timestamp"}),
        ("timestamp without time zone", {"logicalType": "timestamp"}),
        ("datetime", {"logicalType": "timestamp"}),
        ("boolean", {"logicalType": "boolean"}),
        ("BOOL", {"logicalType": "boolean"}),
        ("text[]", None),
        ("integer[]", None),
        ("varchar(10)[]", None),
        ("timestamp(3)[]", None),
        ("varchar(\u0663)", None),
        ("time", None),
        ("float4", {"logicalType": "number"}),
        ("FLOAT4", {"logicalType": "number"}),
    ],
)
def test_logical_type(raw, expected):
    assert dbt.logical_type(raw) == expected


_STRING = {"logicalType": "string"}


def _max_length(length):
    return {"logicalType": "string", "logicalTypeOptions": {"maxLength": length}}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A length is read with its leading zeros left out, however many there are.
        ("varchar(00000000000000010)", _max_length(10)),
        ("varchar(0000000000000000000000000001)", _max_length(1)),
        ("varchar(9007199254740991)", _max_length(2**53 - 1)),
        ("varchar(0)", _STRING),
        ("varchar(000)", _STRING),
        ("varchar(9007199254740992)", _STRING),
        ("varchar(99999999999999999)", _STRING),
        # Whitespace is a space or a tab, and a run of them reads as one space.
        ("varchar (10)", _max_length(10)),
        ("varchar\t(\t10\t)\t", _max_length(10)),
        ("character \t varying(10)", _max_length(10)),
        ("varchar\n(10)", None),
        ("var\x1cchar(10)", None),
        ("character\x1fvarying", None),
        ("integer\x0b", None),
        ("double\xa0precision", None),
        # After the closing parenthesis only whitespace may follow.
        ("varchar(10)x", None),
        ("varchar(10) not null", None),
        ("numeric(12,2) ", {"logicalType": "number"}),
        ("varchar(10", None),
        ("varchar((10))", None),
        ("varchar(10))", None),
    ],
    ids=repr,
)
def test_logical_type_reads_a_length_whitespace_and_the_end_by_its_rules(
    raw, expected
):
    assert dbt.logical_type(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "varchar(00000000000000010)",
        "VARCHAR(10)",
        "CHAR(3)",
        "TEXT",
        "varchar(0)",
    ],
)
def test_the_two_readers_agree_on_the_string_types_they_share(raw):
    # The Python reader gives no logical type at all for a length outside 1 to 2**53 - 1, which
    # the Python contract check refuses; a dbt column keeps its type and only loses the length.
    from continuo_contracts import contract

    python = contract.logical_type(raw)
    if python is None:
        assert dbt.logical_type(raw) == _STRING
    else:
        assert dbt.logical_type(raw) == python


def test_logical_type_returns_a_new_dict():
    first = dbt.logical_type("varchar(8)")
    first["logicalTypeOptions"]["maxLength"] = 1
    assert dbt.logical_type("varchar(8)") == {
        "logicalType": "string",
        "logicalTypeOptions": {"maxLength": 8},
    }


def test_properties_follow_the_order_of_the_columns_mapping():
    manifest = _manifest("finance")
    node = manifest["nodes"]["model.finance.fx_transactions_eur"]
    before = _run(manifest, node["unique_id"])
    node["columns"] = dict(reversed(list(node["columns"].items())))
    after = _run(manifest, node["unique_id"])
    names = [
        column["name"] for column in after.document["schema"][0]["properties"]
    ]
    assert names == [
        "fee_amount_eur",
        "fee_amount",
        "amount_eur",
        "rate_to_eur",
        "transaction_id",
    ]
    assert after.source_sha256 != before.source_sha256


def test_the_order_of_the_tests_changes_nothing():
    manifest = load_manifest("cases")
    node = manifest["nodes"][GENERIC]
    tests = dbt.tests_by_node(manifest)[GENERIC]
    forward = dbt.generate(node, tests, manifest=manifest)
    backward = dbt.generate(node, list(reversed(tests)), manifest=manifest)
    assert forward == backward
    for column in forward.document["schema"][0]["properties"]:
        quality = column.get("quality", [])
        assert quality == sorted(quality, key=canonical_json)


def test_every_generated_rule_passes_the_odcs_schema_after_each_edit():
    def warn_where(test):
        test["config"].update(severity="warn", where="id > 0", warn_if="> 1")
        test["description"] = "Described."
        test["tags"] = ["t"]

    for name in (
        "not_null_generic_tests_id",
        "is_positive_generic_tests_amount__0",
        "accepted_values_generic_tests_kind__a__b",
        "assert_singular_target",
    ):
        manifest = _edited("cases", name, warn_where)
        for node_id in (GENERIC, "model.cases.singular_target"):
            assert schema_issues(_run(manifest, node_id).document) == []


# Each field of a test node that the generator reads, with another value: a change to any of them
# moves `source_sha256`. The paths are the generator's declared test fields, with the kwargs
# wildcard written for the kwarg the test has.
TEST_READ_FIELDS = (
    ("name", "name", "is_positive_amount"),
    ("column_name", "column_name", "id"),
    ("attached_node", "attached_node", "model.cases.governed"),
    ("depends_on.nodes", "depends_on.nodes", [GENERIC, "model.cases.governed"]),
    ("test_metadata.name", "test_metadata.name", "is_negative"),
    ("test_metadata.namespace", "test_metadata.namespace", "dbt_utils"),
    ("test_metadata.kwargs.*", "test_metadata.kwargs.min_value", 1),
    ("description", "description", "Positive."),
    ("tags", "tags", ["nightly"]),
    ("config.severity", "config.severity", "warn"),
    ("config.where", "config.where", "id > 1"),
    ("config.warn_if", "config.warn_if", "> 5"),
    ("config.error_if", "config.error_if", "> 5"),
    ("config.fail_calc", "config.fail_calc", "sum(n)"),
)


def _set(target, path, value):
    *parents, last = path.split(".")
    for key in parents:
        target = target[key]
    target[last] = value


def test_test_read_fields_are_the_fields_the_generator_declares():
    assert sorted(path for path, _, _ in TEST_READ_FIELDS) == sorted(
        dbt._TEST_FIELDS
    )


@pytest.mark.parametrize(("declared", "path", "value"), TEST_READ_FIELDS)
def test_source_sha256_moves_with_each_test_field_it_reads(declared, path, value):
    # The tests are joined before the change, so a changed `attached_node` still reaches the node.
    manifest = _manifest("cases")
    node = manifest["nodes"][GENERIC]
    tests = dbt.tests_by_node(manifest)[GENERIC]
    before = dbt.generate(node, tests, manifest=manifest).source_sha256
    assert before == _generated("cases", GENERIC).source_sha256
    _set(_test(manifest, "is_positive_generic_tests_amount__0"), path, value)
    assert dbt.generate(node, tests, manifest=manifest).source_sha256 != before


@pytest.mark.parametrize(
    ("kind", "key", "field", "value"),
    [
        ("nodes", "model.cases.customer.v2", "schema", "reporting"),
        ("nodes", "model.cases.customer.v2", "name", "client"),
        ("nodes", "model.cases.customer.v2", "version", 3),
        ("sources", "source.cases.raw.events", "schema", "landing"),
        ("sources", "source.cases.raw.events", "identifier", "event_log"),
        ("sources", "source.cases.raw.events", "name", "Events"),
    ],
)
def test_source_sha256_moves_with_each_field_of_a_relationship_target(
    kind, key, field, value
):
    before = _generated("cases", GENERIC).source_sha256
    manifest = _manifest("cases")
    manifest[kind][key][field] = value
    assert _run(manifest, GENERIC).source_sha256 != before


def test_target_fields_are_the_fields_the_generator_declares():
    assert dict(dbt._TARGET_FIELDS) == {
        "nodes": ("schema", "name", "version"),
        "sources": ("schema", "identifier", "name"),
    }


def test_source_sha256_moves_with_a_column_data_type_and_a_test_argument():
    before = _generated("cases", GENERIC).source_sha256
    manifest = _manifest("cases")
    manifest["nodes"][GENERIC]["columns"]["amount"]["data_type"] = "numeric"
    assert _run(manifest, GENERIC).source_sha256 != before
    manifest = _edited(
        "cases",
        "accepted_values_generic_tests_kind__a__b",
        lambda test: test["test_metadata"]["kwargs"].update(values=["a", "c"]),
    )
    assert _run(manifest, GENERIC).source_sha256 != before


@pytest.mark.parametrize(
    ("place", "path", "value"),
    [
        ("test", "test_metadata.kwargs.model", "{{ ref('elsewhere') }}"),
        ("test", "test_metadata.kwargs.column_name", "other"),
        ("test", "config.limit", 10),
        ("test", "config.store_failures", True),
        ("test", "config.meta", {"ticket": 2**70}),
        ("test", "config.group", "finance_team"),
        ("test", "alias", "another_alias"),
        ("test", "compiled_code", "select 1"),
        ("test", "raw_code", "select 2"),
        ("test", "unique_id", "test.cases.renamed"),
        ("test", "meta", {"note": "x"}),
        ("column", "quote", True),
        ("column", "constraints", [{"type": "not_null"}]),
        ("column", "config", {"meta": {"x": 1}, "tags": ["y"]}),
        ("column", "meta", {"unrelated": 2**70}),
        ("column", "granularity", "day"),
        ("target", "alias", "customer_two"),
        ("target", "description", "Customers."),
        ("source", "loader", "fivetran"),
    ],
)
def test_a_field_the_generator_does_not_read_moves_nothing(place, path, value):
    before = _generated("cases", GENERIC)
    manifest = _manifest("cases")
    target = {
        "test": _test(manifest, "is_positive_generic_tests_amount__0"),
        "column": manifest["nodes"][GENERIC]["columns"]["amount"],
        "target": manifest["nodes"]["model.cases.customer.v2"],
        "source": manifest["sources"]["source.cases.raw.events"],
    }[place]
    _set(target, path, value)
    after = _run(manifest, GENERIC)
    assert after == before


def _test_label(name):
    test = _test(load_manifest("cases"), name)
    return f"{GENERIC}: test {test['unique_id']}"


@pytest.mark.parametrize(
    ("name", "path", "value", "message"),
    [
        (
            "not_null_generic_tests_id",
            "name",
            "",
            "name must be a non-empty string, got an empty string",
        ),
        (
            "not_null_generic_tests_id",
            "config",
            "error",
            "config must be a mapping, got str",
        ),
        (
            "not_null_generic_tests_id",
            "config.severity",
            "fatal",
            "config.severity must be warn or error, got str",
        ),
        (
            "not_null_generic_tests_id",
            "config.severity",
            2,
            "config.severity must be warn or error, got int",
        ),
        (
            "not_null_generic_tests_id",
            "config.where",
            1,
            "config.where must be a string, got int",
        ),
        (
            "not_null_generic_tests_id",
            "config.fail_calc",
            ["count(*)"],
            "config.fail_calc must be a string, got list",
        ),
        (
            "not_null_generic_tests_id",
            "description",
            7,
            "description must be a string, got int",
        ),
        (
            "not_null_generic_tests_id",
            "tags",
            "nightly",
            "tags must be a list of strings, got str",
        ),
        (
            "not_null_generic_tests_id",
            "test_metadata",
            "not_null",
            "test_metadata must be a mapping, got str",
        ),
        (
            "not_null_generic_tests_id",
            "test_metadata.name",
            5,
            "test_metadata.name must be a string, got int",
        ),
        (
            "not_null_generic_tests_id",
            "test_metadata.namespace",
            5,
            "test_metadata.namespace must be a string, got int",
        ),
        (
            "not_null_generic_tests_id",
            "test_metadata.kwargs",
            ["id"],
            "test_metadata.kwargs must be a mapping, got list",
        ),
        (
            "not_null_generic_tests_id",
            "column_name",
            1,
            "column_name must be a string, got int",
        ),
        (
            "relationships_generic_tests_customer_id__id__ref_customer_",
            "test_metadata.kwargs.field",
            "",
            "test_metadata.kwargs.field must be a non-empty string, got an empty string",
        ),
        (
            "relationships_generic_tests_customer_id__id__ref_customer_",
            "depends_on",
            ["model.cases.customer.v2"],
            "depends_on must be a mapping, got list",
        ),
        (
            "relationships_generic_tests_customer_id__id__ref_customer_",
            "depends_on.nodes",
            "model.cases.customer.v2",
            "depends_on.nodes must be a list of strings, got str",
        ),
        (
            "relationships_generic_tests_customer_id__id__ref_customer_",
            "depends_on.nodes",
            ["model.cases.customer.v1", "model.cases.customer.v2", GENERIC],
            "depends_on.nodes must name one node besides attached_node, got 2",
        ),
        (
            "relationships_generic_tests_customer_id__id__ref_customer_",
            "depends_on.nodes",
            ["model.cases.missing", GENERIC],
            "depends_on.nodes names model.cases.missing, which is neither a node nor "
            "a source of the manifest",
        ),
        (
            "is_positive_generic_tests_amount__0",
            "test_metadata.kwargs.min_value",
            2**70,
            "test_metadata.kwargs.min_value: integer is outside -(2**53 - 1) to "
            "2**53 - 1",
        ),
        (
            "is_positive_generic_tests_amount__0",
            "test_metadata.kwargs.min_value",
            [0, float("inf")],
            "test_metadata.kwargs.min_value[1]: NaN and the infinities are not JSON "
            "numbers",
        ),
    ],
)
def test_a_test_field_of_the_wrong_shape_names_the_node_then_the_test(
    name, path, value, message
):
    # The tests are joined before the change, which `tests_by_node` would refuse for some.
    manifest = _manifest("cases")
    tests = dbt.tests_by_node(manifest)[GENERIC]
    _set(_test(manifest, name), path, value)
    with pytest.raises(DbtNodeError) as raised:
        dbt.generate(manifest["nodes"][GENERIC], tests, manifest=manifest)
    assert str(raised.value) == f"{_test_label(name)}: {message}"


def test_a_test_with_no_unique_id_is_named_by_its_name():
    manifest = _edited(
        "cases",
        "not_null_generic_tests_id",
        lambda test: test.update(unique_id=None, description=1),
    )
    with pytest.raises(DbtNodeError) as raised:
        _run(manifest, GENERIC)
    assert str(raised.value) == (
        f"{GENERIC}: test not_null_generic_tests_id: description must be a string, "
        "got int"
    )


@pytest.mark.parametrize(
    ("kind", "key", "field", "value", "message"),
    [
        (
            "nodes",
            "model.cases.customer.v2",
            "schema",
            None,
            "model.cases.customer.v2: schema must be a non-empty string, got NoneType",
        ),
        (
            "nodes",
            "model.cases.customer.v2",
            "version",
            2**70,
            "model.cases.customer.v2: version: integer is outside -(2**53 - 1) to "
            "2**53 - 1",
        ),
        (
            "sources",
            "source.cases.raw.events",
            "schema",
            "",
            "source.cases.raw.events: schema must be a non-empty string, got an empty "
            "string",
        ),
        (
            "sources",
            "source.cases.raw.events",
            "name",
            None,
            "source.cases.raw.events: name must be a non-empty string, got NoneType",
        ),
        (
            "sources",
            "source.cases.raw.events",
            "identifier",
            3,
            "source.cases.raw.events: identifier must be a string, got int",
        ),
    ],
)
def test_a_relationship_target_of_the_wrong_shape_names_the_node_and_the_test(
    kind, key, field, value, message
):
    manifest = _manifest("cases")
    manifest[kind][key][field] = value
    test = (
        "relationships_generic_tests_customer_id__id__ref_customer_"
        if kind == "nodes"
        else "relationships_generic_tests_event_id__id__source_raw_events_"
    )
    with pytest.raises(DbtNodeError) as raised:
        _run(manifest, GENERIC)
    assert str(raised.value) == f"{_test_label(test)}: {message}"


def _set_node(path, value):
    """Return a change to a node that sets the key at `path`, a tuple of keys, to `value`."""

    def change(node):
        target = node
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


@pytest.mark.parametrize(
    ("node_id", "change", "message"),
    [
        (
            GENERIC,
            _set_node(("columns",), ["id"]),
            "columns must be a mapping, got list",
        ),
        (
            GENERIC,
            _set_node(("columns", "id"), "id"),
            "columns.id must be a mapping, got str",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "name"), None),
            "columns.id.name must be a non-empty string, got NoneType",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "description"), 1),
            "columns.id.description must be a string, got int",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "data_type"), 1),
            "columns.id.data_type must be a string, got int",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "tags"), "pii"),
            "columns.id.tags must be a list of strings, got str",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "meta"), ["x"]),
            "columns.id.meta must be a mapping, got list",
        ),
        (
            GENERIC,
            _set_node(("columns", "id", "meta"), {"continuo": "personal"}),
            "columns.id.meta.continuo must be a mapping, got str",
        ),
        (
            GENERIC,
            _set_node(
                ("columns", "id", "meta"), {"continuo": {"classification": True}}
            ),
            "columns.id.meta.continuo.classification must be a string, got bool",
        ),
        (
            GENERIC,
            _set_node(("primary_key",), "id"),
            "primary_key must be a list of strings, got str",
        ),
        (
            GENERIC,
            _set_node(("primary_key",), ["id", 2]),
            "primary_key[1] must be a string, got int",
        ),
        (
            "seed.cases.typed_seed",
            _set_node(("config", "column_types"), ["amount"]),
            "config.column_types must be a mapping, got list",
        ),
        (
            "seed.cases.typed_seed",
            _set_node(("config", "column_types", "amount"), 12),
            "config.column_types.amount must be a string, got int",
        ),
        (
            "model.cases.governed",
            _set_node(("config", "meta", "continuo", "tests"), {"name": "rows"}),
            "config.meta.continuo.tests must be a list, got dict",
        ),
        (
            "model.cases.governed",
            _set_node(("config", "meta", "continuo", "tests"), ["rows"]),
            "config.meta.continuo.tests[0] must be a mapping, got str",
        ),
        (
            "model.cases.governed",
            _set_node(("config", "meta", "continuo", "tests"), [{"params": {}}]),
            "config.meta.continuo.tests[0].name must be a non-empty string, got NoneType",
        ),
        (
            "model.cases.governed",
            _set_node(
                ("config", "meta", "continuo", "tests"),
                [{"name": "rows", "enabled": "yes"}],
            ),
            "config.meta.continuo.tests[0].enabled must be a boolean, got str",
        ),
        (
            "model.cases.governed",
            _set_node(
                ("config", "meta", "continuo", "tests"),
                [{"name": "rows", "granularity": 1}],
            ),
            "config.meta.continuo.tests[0].granularity must be a string, got int",
        ),
        (
            "model.cases.governed",
            _set_node(
                ("config", "meta", "continuo", "tests"),
                [{"name": "rows", "params": [1]}],
            ),
            "config.meta.continuo.tests[0].params must be a mapping, got list",
        ),
        (
            "model.cases.governed",
            _set_node(
                ("config", "meta", "continuo", "tests"),
                [{"name": "rows", "params": {"min": 2**70}}],
            ),
            "config.meta.continuo.tests[0].params.min: integer is outside "
            "-(2**53 - 1) to 2**53 - 1",
        ),
        (
            "snapshot.cases.orders_snapshot",
            _set_node(("config", "snapshot_meta_column_names", "dbt_scd_id"), 1),
            "config.snapshot_meta_column_names.dbt_scd_id must be a string, got int",
        ),
    ],
)
def test_a_column_or_key_field_of_the_wrong_shape_raises_dbt_node_error(
    node_id, change, message
):
    manifest = _manifest("cases")
    change(manifest["nodes"][node_id])
    with pytest.raises(DbtNodeError) as raised:
        _run(manifest, node_id)
    assert str(raised.value) == f"{node_id}: {message}"


def _scramble(value):
    """Change every list and dict inside `value`, the deepest first."""
    if isinstance(value, dict):
        for item in value.values():
            _scramble(item)
        value["scrambled"] = True
    elif isinstance(value, list):
        for item in value:
            _scramble(item)
        value.append("scrambled")


def test_the_contract_shares_no_list_or_mapping_with_the_manifest():
    manifest = _manifest("cases")
    snapshot = manifest["nodes"]["snapshot.cases.orders_snapshot"]
    snapshot["config"].update(
        strategy="check", unique_key=["id", "version"], check_cols=["status"]
    )
    manifest["nodes"]["model.cases.incr"]["config"]["unique_key"] = ["id", "day"]
    before = copy.deepcopy(manifest)
    for node_id in (
        GENERIC,
        "model.cases.governed",
        "model.cases.incr",
        "snapshot.cases.orders_snapshot",
    ):
        _scramble(_run(manifest, node_id).document)
    assert manifest == before


def test_a_change_to_the_contracts_tags_leaves_the_node_and_its_schedule():
    manifest = _manifest("cases")
    node = manifest["nodes"]["model.cases.governed"]
    tags = list(node["tags"])
    schedule = dbt.schedule(node)
    document = _run(manifest, "model.cases.governed").document
    document["tags"].insert(0, "another")
    assert node["tags"] == tags
    assert dbt.schedule(node) == schedule


def test_sources_that_are_not_a_mapping_raise_dbt_node_error():
    manifest = _manifest("cases")
    manifest["sources"] = "x"
    test = "relationships_generic_tests_event_id__id__source_raw_events_"
    with pytest.raises(DbtNodeError) as raised:
        _run(manifest, GENERIC)
    assert str(raised.value) == (
        f"{_test_label(test)}: sources must be a mapping, got str"
    )


@pytest.mark.parametrize("item", ["not_null_id", ["x"], None])
def test_a_test_that_is_not_a_mapping_raises_dbt_node_error(item):
    manifest = load_manifest("cases")
    tests = [*dbt.tests_by_node(manifest)[GENERIC], item]
    with pytest.raises(DbtNodeError) as raised:
        dbt.generate(manifest["nodes"][GENERIC], tests, manifest=manifest)
    assert str(raised.value) == (
        f"{GENERIC}: tests[11] must be a mapping, got {type(item).__name__}"
    )
