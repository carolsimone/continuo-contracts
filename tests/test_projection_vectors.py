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
