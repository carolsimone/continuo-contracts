import threading
from collections import OrderedDict
from datetime import date
from types import MappingProxyType

import pytest

from continuo_contracts.errors import ProjectionError
from continuo_contracts.fingerprints import projection_keys
from continuo_contracts.python import (
    PythonColumn,
    PythonNode,
    canonical_sql_type,
    code_projection,
    projection_hash,
)

DEMO = {"schema": "analytics", "table": "py_daily_kpis", "script": "scripts/py_daily_kpis.py",
        "reads": {"daily": "select created_at, amount_eur from analytics.daily_transactions"},
        "output_columns": [{"name": "day", "type": "DATE", "nullable": False},
                           {"name": "tx_count", "type": "INTEGER", "nullable": False},
                           {"name": "total_amount", "type": "NUMERIC(12,2)", "nullable": False}]}
DEMO_HASH = "a01d7d6065f055bb2d87959aaf178d08f5a3f96c86912099b9876239d84f6efe"
CSV = {"kind": "python-csv", "script": "", "reads": {"csv": "s3://b/k.csv"}}


def node(**changes):
    base = dict(schema="analytics", table="t", script="scripts/t.py", reads={"r": "select 1 as x"},
                output_columns=(PythonColumn("x", "INTEGER"),))
    return PythonNode(**{**base, **changes})


def test_the_projection_has_exactly_the_declared_keys_in_order():
    assert list(code_projection(node())) == list(projection_keys())


def test_the_stored_kind_alias_hashes_as_python_node():
    assert projection_hash(PythonNode.from_mapping({**DEMO, "kind": "python-model"})) == DEMO_HASH


@pytest.mark.parametrize("mode", ["replace", "append"])
def test_a_key_on_a_replace_or_append_node_stays_out_of_the_projection(mode):
    with_key = PythonNode.from_mapping({**DEMO, "mode_type": mode, "primary_key": ["day"]})
    without = PythonNode.from_mapping({**DEMO, "mode_type": mode})
    assert code_projection(with_key)["primary_key"] == []
    assert projection_hash(with_key) == projection_hash(without)


@pytest.mark.parametrize(("changes", "needle"), [
    (dict(kind="python-sql"), "kind"),
    (dict(CSV, script="scripts/t.py"), "script"),
    (dict(CSV, reads={"r": "select 1"}), "reads"),
    (dict(kind="python-api", reads={"r": "select 1"}), "reads"),
    (dict(secret_ref="continuo-api-x"), "secret_ref"),
    (dict(CSV, mode_type="upsert"), "mode_type"),
    (dict(CSV, assets=("lib/a.csv",)), "assets"),
    (dict(assets=("lib/a.csv", "lib/a.csv")), "assets"),
    (dict(mode_type="merge"), "mode_type"),
    (dict(extra_columns="ignore"), "extra_columns"),
    (dict(output_columns=(PythonColumn("x", "INTEGER", nullable="false"),)), "nullable"),
    (dict(output_columns=(PythonColumn("x", "FLOAT"),)), "type"),
])
def test_the_projection_refuses_what_it_cannot_hash_the_same_way_everywhere(changes, needle):
    with pytest.raises(ProjectionError, match=needle) as err:
        code_projection(node(**changes))
    assert str(err.value).startswith("analytics.t: ")


def test_projection_hash_names_the_node_and_the_path_of_a_bad_value():
    with pytest.raises(ProjectionError) as err:
        projection_hash(node(config={"start": date(2024, 1, 1)}))
    assert str(err.value).startswith("analytics.t: $.config.start: ")


def test_from_mapping_ignores_other_keys_and_fills_defaults():
    built = PythonNode.from_mapping({"schema": "analytics", "table": "t", "owner": "team-a",
                                     "output_columns": [{"name": "x", "type": "INT"}]})
    assert built == PythonNode(schema="analytics", table="t", output_columns=(PythonColumn("x", "INT", True),))


def test_from_mapping_refuses_a_missing_table():
    with pytest.raises(ProjectionError, match="table"):
        PythonNode.from_mapping({"schema": "analytics"})


@pytest.mark.parametrize(("entry", "needle"), [
    ({"reads": ["select 1"]}, "reads must be a mapping"),
    ({"config": "{}"}, "config must be a mapping"),
    ({"assets": "lib/a.csv"}, "assets must be a list"),
    ({"primary_key": "id"}, "primary_key must be a list"),
    ({"output_columns": "x INTEGER"}, "output_columns must be a list"),
    ({"output_columns": ["x INTEGER"]}, r"output_columns\[0\] must be a mapping"),
    ({"output_columns": [{"name": "x"}]}, r"output_columns\[0\] has no 'type' field"),
])
def test_from_mapping_refuses_a_field_of_the_wrong_container_type(entry, needle):
    with pytest.raises(ProjectionError, match=needle) as err:
        PythonNode.from_mapping({"schema": "analytics", "table": "t", **entry})
    assert str(err.value).startswith("analytics.t: ")


@pytest.mark.parametrize("changes", [
    dict(mode_type="upsert", primary_key="id"),
    dict(assets="lib/a.csv"),
])
def test_a_bare_string_is_not_read_as_a_list_of_characters(changes):
    with pytest.raises(ProjectionError, match="must be a list"):
        code_projection(node(**changes))


def test_the_projection_shares_nothing_with_the_node():
    source = node(config={"layout": {"a": [1, 2]}}, reads={"r": "select 1"})
    projection = code_projection(source)
    projection["config"]["layout"]["a"].append(3)
    projection["reads"]["r"] = "changed"
    assert source.config == {"layout": {"a": [1, 2]}}
    assert source.reads == {"r": "select 1"}


def test_a_projection_key_without_a_rule_is_refused(monkeypatch):
    monkeypatch.setattr("continuo_contracts.python.projection_keys",
                        lambda: (*projection_keys(), "extra"))
    with pytest.raises(ProjectionError, match="projection key extra has no rule") as err:
        code_projection(node())
    assert str(err.value).startswith("analytics.t: ")


def test_a_type_that_is_not_text_is_refused_as_a_type():
    with pytest.raises(ProjectionError, match="unsupported or invalid SQL type: 5"):
        canonical_sql_type(5)


def test_a_very_long_number_in_a_type_does_not_reach_int():
    assert canonical_sql_type("VARCHAR(" + "0" * 5000 + "7)") == "VARCHAR(7)"
    with pytest.raises(ProjectionError, match="out of range"):
        canonical_sql_type("NUMERIC(" + "1" * 5000 + ",0)")


def test_a_nested_mapping_proxy_hashes_like_the_same_data_in_plain_containers():
    plain = node(config={"layout": {"a": 1}, "steps": [1, {"b": 2}]})
    proxied = node(config={"layout": MappingProxyType({"a": 1}),
                           "steps": (1, MappingProxyType({"b": 2}))})
    assert projection_hash(proxied) == projection_hash(plain)
    assert projection_hash(node(config=MappingProxyType({"a": 1}))) == projection_hash(
        node(config={"a": 1}))


def test_a_value_json_cannot_hold_is_refused_with_its_node_and_its_path():
    with pytest.raises(ProjectionError) as err:
        projection_hash(node(config={"lock": threading.Lock()}))
    assert str(err.value).startswith("analytics.t: $.config.lock: ")


def test_config_comes_out_as_plain_dicts_and_lists_at_every_depth():
    config = {"o": OrderedDict(a=OrderedDict(b=(1, MappingProxyType({"c": [2]}))))}
    copied = code_projection(node(config=config))["config"]
    assert type(copied) is dict
    assert type(copied["o"]) is dict
    assert type(copied["o"]["a"]) is dict
    assert type(copied["o"]["a"]["b"]) is list
    assert type(copied["o"]["a"]["b"][1]) is dict
    assert type(copied["o"]["a"]["b"][1]["c"]) is list
    assert copied == {"o": {"a": {"b": [1, {"c": [2]}]}}}


def test_a_container_that_appears_twice_without_a_cycle_is_copied_each_time():
    shared = {"k": [1]}
    copied = code_projection(node(config={"a": shared, "b": shared}))["config"]
    assert copied == {"a": {"k": [1]}, "b": {"k": [1]}}
    assert copied["a"] is not copied["b"]
    assert projection_hash(node(config={"a": shared, "b": shared})) == projection_hash(
        node(config={"a": {"k": [1]}, "b": {"k": [1]}}))


def _mapping_in_a_mapping():
    loop = {}
    loop["self"] = loop
    return {"c": loop}


def _list_in_a_list():
    loop = []
    loop.append(loop)
    return {"c": loop}


def _mapping_in_a_list_in_a_mapping():
    inner = {}
    outer = [inner]
    inner["back"] = outer
    return {"c": outer}


@pytest.mark.parametrize(("build", "path"), [
    (_mapping_in_a_mapping, "$.config.c.self"),
    (_list_in_a_list, "$.config.c[0]"),
    (_mapping_in_a_list_in_a_mapping, "$.config.c[0].back"),
])
def test_a_config_that_contains_itself_is_refused_with_its_node_and_its_path(build, path):
    with pytest.raises(ProjectionError) as err:
        projection_hash(node(config=build()))
    assert str(err.value) == f"analytics.t: {path}: circular reference"


def test_the_whole_config_that_contains_itself_is_refused_with_its_path():
    config = {}
    config["self"] = config
    with pytest.raises(ProjectionError) as err:
        projection_hash(node(config=config))
    assert str(err.value) == "analytics.t: $.config.self: circular reference"


def test_a_key_that_is_not_a_string_is_refused_at_the_top_and_with_its_path_below():
    with pytest.raises(ProjectionError, match="key of config must be a string") as err:
        code_projection(node(config={1: "x"}))
    assert str(err.value).startswith("analytics.t: ")
    with pytest.raises(ProjectionError) as err:
        projection_hash(node(config={"a": {1: "x"}}))
    assert str(err.value).startswith("analytics.t: $.config.a: ")


def test_assets_are_sorted_by_code_point_not_by_utf_16_unit():
    # U+FF5E is in the BMP and U+1F600 is above it, which UTF-16 writes as a surrogate pair that
    # starts with 0xD83D. By code point (and by UTF-8 bytes) the tilde comes first, by UTF-16
    # unit the emoji does.
    tilde, emoji = chr(0xFF5E) + ".csv", chr(0x1F600) + ".csv"
    assert code_projection(node(assets=(emoji, tilde)))["assets"] == [tilde, emoji]
