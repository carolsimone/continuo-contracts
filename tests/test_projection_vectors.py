import json
from importlib import resources

import pytest

from continuo_contracts.errors import ProjectionError
from continuo_contracts.python import (
    PythonNode,
    canonical_sql_type,
    code_projection,
    projection_hash,
)

VECTORS = resources.files("continuo_contracts") / "vectors"
PROJECTIONS = json.loads((VECTORS / "projection.json").read_text(encoding="utf-8"))
SQL_TYPES = json.loads((VECTORS / "sql_types.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SQL_TYPES, ids=lambda c: repr(c["raw"]))
def test_sql_type_vector(case):
    if case["canonical"] is None:
        with pytest.raises(ProjectionError):
            canonical_sql_type(case["raw"])
    else:
        assert canonical_sql_type(case["raw"]) == case["canonical"]


@pytest.mark.parametrize("case", PROJECTIONS, ids=lambda c: c["name"])
def test_projection_vector(case):
    node = PythonNode.from_mapping(case["node"])
    assert code_projection(node) == case["projection"]
    assert projection_hash(node) == case["projection_hash"]


def test_every_kind_has_a_vector():
    assert {c["node"]["kind"] for c in PROJECTIONS} == {"python-node", "python-csv", "python-api"}


def test_spelling_only_edits_hash_like_the_demo_node():
    by_name = {c["name"]: c["projection_hash"] for c in PROJECTIONS}
    assert by_name["spellings_canonicalised"] == by_name["demo_py_daily_kpis"] == (
        "a01d7d6065f055bb2d87959aaf178d08f5a3f96c86912099b9876239d84f6efe")


def _utf16_order(texts):
    return sorted(texts, key=lambda text: text.encode("utf-16-be"))


def test_the_assets_case_sorts_by_code_point_and_not_by_utf16_code_unit():
    case = next(c for c in PROJECTIONS if c["name"] == "assets_code_point_order")
    given, sorted_assets = case["node"]["assets"], case["projection"]["assets"]
    assert given == _utf16_order(given)
    assert sorted_assets == sorted(given)
    assert sorted_assets != _utf16_order(given)


def test_the_config_case_has_keys_that_utf16_order_and_code_point_order_sort_differently():
    case = next(c for c in PROJECTIONS if c["name"] == "config_utf16_key_order")
    config = case["projection"]["config"]
    assert config == case["node"]["config"]
    bmp, astral = chr(0xFF5E), chr(0x1F600)
    for level in (config, config[astral]):
        assert {bmp, astral} <= set(level)
        assert _utf16_order(list(level)) != sorted(level)


def test_every_vector_file_is_ascii():
    files = [entry for entry in VECTORS.iterdir() if entry.name.endswith(".json")]
    assert len(files) >= 4
    for entry in files:
        assert all(b == 10 or 32 <= b < 127 for b in entry.read_bytes()), entry.name
