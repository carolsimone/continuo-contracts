import dataclasses
import re

import pytest

from continuo_contracts.vocabulary import (
    PROPERTIES,
    VENDOR,
    ContinuoProperty,
    continuo_values,
)

ROOT_AUTHORED = [
    "service",
    "kind",
    "schedule",
    "criticality",
    "timeoutMinutes",
    "script",
    "readQueries",
    "csvSource",
    "extraColumns",
    "config",
    "secretRef",
    "modeType",
    "assets",
]
ROOT_REFUSED = [
    "filePath",
    "materialization",
    "contractEnforced",
    "access",
    "group",
    "deprecationDate",
    "freshnessBuildAfter",
    "snapshot",
    "quoteColumns",
    "provenance",
]
QUALITY_AUTHORED = ["where", "warnIf", "errorIf", "failCalc"]
DBT_ROOT_WRITTEN = [
    "service",
    "kind",
    "schedule",
    "criticality",
    "timeoutMinutes",
    "filePath",
    "materialization",
    "contractEnforced",
    "access",
    "group",
    "deprecationDate",
    "freshnessBuildAfter",
    "snapshot",
    "quoteColumns",
]
NOT_USED_BY_DBT = [
    "script",
    "readQueries",
    "csvSource",
    "extraColumns",
    "config",
    "secretRef",
    "modeType",
    "assets",
    "provenance",
]
SCHEMA_BINDINGS = ["entity", "grain", "fixed", "filter"]
COLUMN_BINDINGS = ["property", "local", "aggregate"]
# The keys of an aggregate binding are one `aggregate` value, never properties of their own.
AGGREGATE_KEYS = ["from", "agg", "period"]


def _named(sits_on, python):
    return {
        name
        for name, prop in PROPERTIES.items()
        if prop.sits_on == sits_on and prop.python == python
    }


def _named_dbt(sits_on, dbt):
    return {
        name
        for name, prop in PROPERTIES.items()
        if prop.sits_on == sits_on and prop.dbt == dbt
    }


def test_the_vendor_is_continuo():
    assert VENDOR == "continuo"


def test_every_property_name_is_camel_case():
    for name in PROPERTIES:
        assert re.fullmatch(r"[a-z][A-Za-z0-9]*", name), name


def test_each_row_is_filed_under_its_own_name():
    for name, prop in PROPERTIES.items():
        assert prop.name == name


def test_sits_on_and_python_use_the_closed_words():
    for prop in PROPERTIES.values():
        assert prop.sits_on in {"root", "schema", "quality", "column"}, prop
        assert prop.python in {"authored", "filled", "refused"}, prop


def test_the_properties_are_exactly_the_rows_of_this_release():
    assert set(PROPERTIES) == {
        *ROOT_AUTHORED,
        *ROOT_REFUSED,
        "reads",
        *QUALITY_AUTHORED,
        *SCHEMA_BINDINGS,
        *COLUMN_BINDINGS,
    }
    assert len(PROPERTIES) == (
        len(ROOT_AUTHORED)
        + len(ROOT_REFUSED)
        + 1
        + len(QUALITY_AUTHORED)
        + len(SCHEMA_BINDINGS)
        + len(COLUMN_BINDINGS)
    )


def test_the_root_properties_an_author_may_write():
    assert _named("root", "authored") == set(ROOT_AUTHORED)


def test_the_python_refused_set():
    assert len(ROOT_REFUSED) == 10
    refused = {
        name for name, prop in PROPERTIES.items() if prop.python == "refused"
    }
    assert refused == set(ROOT_REFUSED)
    assert refused == _named("root", "refused")


def test_reads_is_filled_by_continuo():
    assert PROPERTIES["reads"] == ContinuoProperty(
        "reads", "schema", "filled", "filled"
    )
    assert _named("schema", "filled") == {"reads"}


def test_the_quality_properties_an_author_may_write():
    assert _named("quality", "authored") == set(QUALITY_AUTHORED)


def test_the_bindings_an_author_may_write_sit_on_the_schema_object_and_on_a_column():
    assert _named("schema", "authored") == set(SCHEMA_BINDINGS)
    assert _named("column", "authored") == set(COLUMN_BINDINGS)
    assert _named_dbt("schema", "written") == set(SCHEMA_BINDINGS)
    assert _named_dbt("column", "written") == set(COLUMN_BINDINGS)


def test_the_dbt_root_properties():
    assert len(DBT_ROOT_WRITTEN) == 14
    assert _named_dbt("root", "written") == set(DBT_ROOT_WRITTEN)


def test_the_dbt_quality_properties():
    assert _named_dbt("quality", "written") == set(QUALITY_AUTHORED)


def test_reads_is_filled_for_both_families():
    assert PROPERTIES["reads"].python == "filled"
    assert PROPERTIES["reads"].dbt == "filled"
    assert {
        name for name, prop in PROPERTIES.items() if prop.dbt == "filled"
    } == {"reads"}
    assert _named_dbt("schema", "filled") == {"reads"}


def test_the_python_only_properties_are_not_used_by_dbt():
    assert len(NOT_USED_BY_DBT) == 9
    not_used = {
        name for name, prop in PROPERTIES.items() if prop.dbt == "not_used"
    }
    assert not_used == set(NOT_USED_BY_DBT)
    assert not_used == _named_dbt("root", "not_used")


def test_every_property_has_a_dbt_answer():
    for prop in PROPERTIES.values():
        assert prop.dbt in {"written", "filled", "not_used"}, prop
    written = {name for name, prop in PROPERTIES.items() if prop.dbt == "written"}
    assert written == {
        *DBT_ROOT_WRITTEN,
        *QUALITY_AUTHORED,
        *SCHEMA_BINDINGS,
        *COLUMN_BINDINGS,
    }
    filled = {name for name, prop in PROPERTIES.items() if prop.dbt == "filled"}
    not_used = {
        name for name, prop in PROPERTIES.items() if prop.dbt == "not_used"
    }
    assert written | filled | not_used == set(PROPERTIES)
    assert len(written) + len(filled) + len(not_used) == len(PROPERTIES)


def test_the_dbt_answer_does_not_move_a_python_answer():
    for name in DBT_ROOT_WRITTEN:
        expected = "authored" if name in ROOT_AUTHORED else "refused"
        assert PROPERTIES[name].python == expected, name


def test_the_keys_of_an_aggregate_binding_are_not_properties():
    assert not set(AGGREGATE_KEYS) & set(PROPERTIES)


def test_a_row_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PROPERTIES["service"].python = "refused"


def test_the_table_cannot_be_changed():
    with pytest.raises(TypeError):
        PROPERTIES["extra"] = ContinuoProperty(
            "extra", "root", "authored", "written"
        )


def test_continuo_values_skips_other_vendors():
    items = [
        {"vendor": "lotse", "property": "schedule", "value": "x"},
        {"vendor": "continuo", "property": "schedule", "value": "daily"},
    ]
    assert continuo_values(items) == {"schedule": "daily"}


def test_continuo_values_keeps_any_json_value_and_the_order_of_the_file():
    config = {"batch_size": 10, "tags": ["a", None]}
    items = [
        {"vendor": "continuo", "property": "config", "value": config},
        {"vendor": "continuo", "property": "timeoutMinutes", "value": 30},
        {"vendor": "continuo", "property": "assets", "value": []},
        {"vendor": "continuo", "property": "extraColumns", "value": None},
    ]
    values = continuo_values(items)
    assert list(values) == ["config", "timeoutMinutes", "assets", "extraColumns"]
    assert values == {
        "config": config,
        "timeoutMinutes": 30,
        "assets": [],
        "extraColumns": None,
    }


def test_continuo_values_keeps_the_last_value_of_a_repeated_name_in_the_place_of_its_first():
    items = [
        {"vendor": "continuo", "property": "schedule", "value": "daily"},
        {"vendor": "continuo", "property": "criticality", "value": "CORE"},
        {"vendor": "continuo", "property": "schedule", "value": "hourly"},
    ]
    values = continuo_values(items)
    assert values == {"schedule": "hourly", "criticality": "CORE"}
    assert list(values) == ["schedule", "criticality"]


def test_continuo_values_reads_an_item_with_other_keys():
    item = {
        "vendor": "continuo",
        "property": "kind",
        "value": "python-csv",
        "description": "d",
    }
    assert continuo_values([item]) == {"kind": "python-csv"}


def test_continuo_values_does_not_filter_by_the_vocabulary():
    item = {"vendor": "continuo", "property": "somethingNew", "value": 1}
    assert continuo_values([item]) == {"somethingNew": 1}


@pytest.mark.parametrize(
    "unusable",
    [
        None,
        {},
        "schedule",
        5,
        {"vendor": "continuo", "property": "schedule", "value": "daily"},
    ],
)
def test_continuo_values_gives_nothing_for_input_that_is_not_a_list(unusable):
    assert continuo_values(unusable) == {}


def test_continuo_values_gives_nothing_for_an_empty_list():
    assert continuo_values([]) == {}


@pytest.mark.parametrize(
    "item",
    [
        None,
        "continuo",
        7,
        ["continuo", "schedule", "daily"],
        {},
        {"property": "schedule", "value": "daily"},
        {"vendor": "continuo", "value": "daily"},
        {"vendor": "continuo", "property": "schedule"},
        {"vendor": "continuo", "property": 5, "value": "daily"},
        {"vendor": "continuo", "property": ["a"], "value": "daily"},
        {"vendor": 5, "property": "schedule", "value": "daily"},
        {"vendor": "Continuo", "property": "schedule", "value": "daily"},
    ],
)
def test_continuo_values_skips_an_unusable_item_and_keeps_the_rest(item):
    good = {"vendor": "continuo", "property": "criticality", "value": "CORE"}
    assert continuo_values([item, good]) == {"criticality": "CORE"}
