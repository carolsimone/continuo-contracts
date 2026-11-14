import dataclasses
import re

import pytest

from continuo_contracts.vocabulary import PROPERTIES, VENDOR, ContinuoProperty, continuo_values

ROOT_AUTHORED = (
    "service kind schedule criticality timeoutMinutes script readQueries csvSource extraColumns "
    "config secretRef modeType assets"
).split()
ROOT_REFUSED = (
    "filePath materialization contractEnforced access group deprecationDate freshnessBuildAfter "
    "snapshot quoteColumns provenance"
).split()
QUALITY_AUTHORED = "where warnIf errorIf failCalc".split()
ONTOLOGY = "entity grain fixed filter property local from agg period".split()


def _named(sits_on, python):
    return {
        name
        for name, prop in PROPERTIES.items()
        if prop.sits_on == sits_on and prop.python == python
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
        assert prop.sits_on in {"root", "schema", "quality"}, prop
        assert prop.python in {"authored", "filled", "refused"}, prop


def test_the_properties_are_exactly_the_rows_of_this_release():
    assert set(PROPERTIES) == {*ROOT_AUTHORED, *ROOT_REFUSED, "reads", *QUALITY_AUTHORED}
    assert len(PROPERTIES) == len(ROOT_AUTHORED) + len(ROOT_REFUSED) + 1 + len(QUALITY_AUTHORED)


def test_the_root_properties_an_author_may_write():
    assert _named("root", "authored") == set(ROOT_AUTHORED)


def test_the_python_refused_set():
    assert len(ROOT_REFUSED) == 10
    refused = {name for name, prop in PROPERTIES.items() if prop.python == "refused"}
    assert refused == set(ROOT_REFUSED)
    assert refused == _named("root", "refused")


def test_reads_is_filled_by_continuo():
    assert PROPERTIES["reads"] == ContinuoProperty("reads", "schema", "filled")
    assert _named("schema", "filled") == {"reads"}


def test_the_quality_properties_an_author_may_write():
    assert _named("quality", "authored") == set(QUALITY_AUTHORED)


def test_no_ontology_property_is_in_this_release():
    assert not set(ONTOLOGY) & set(PROPERTIES)


def test_a_row_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        PROPERTIES["service"].python = "refused"


def test_the_table_cannot_be_changed():
    with pytest.raises(TypeError):
        PROPERTIES["extra"] = ContinuoProperty("extra", "root", "authored")


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
    assert values == {"config": config, "timeoutMinutes": 30, "assets": [], "extraColumns": None}


def test_continuo_values_reads_an_item_with_other_keys():
    item = {"vendor": "continuo", "property": "kind", "value": "python-csv", "description": "d"}
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
