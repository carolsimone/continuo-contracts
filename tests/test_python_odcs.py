"""`node_from_odcs`: the `PythonNode` that a Python node's ODCS contract stands for, and the
golden test that the demo nodes' projections are the same whether the node is written as an
old-style entry or as an ODCS contract."""

import copy
import json
from importlib import resources
from pathlib import Path

import pytest
import yaml

from continuo_contracts import yaml_strict
from continuo_contracts.errors import ProjectionError
from continuo_contracts.python import (
    PythonColumn,
    PythonNode,
    code_projection,
    node_from_odcs,
    projection_hash,
)
from continuo_contracts.validate import validate_python_contract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OLD = FIXTURES / "demo" / "service-py" / "old"
NEW = FIXTURES / "demo" / "service-py" / "contracts"
DEMO_TABLES = ["py_daily_kpis", "demo_orders_csv", "fx_eur_usd_rates"]
VECTORS = json.loads(
    (
        resources.files("continuo_contracts") / "vectors" / "projection.json"
    ).read_text("utf-8")
)


def _load(path):
    return yaml_strict.load(path.read_text(encoding="utf-8"))


def _demo(table):
    return _load(NEW / f"{table}.odcs.yaml")


def _full(name):
    return _load(FIXTURES / "full" / f"{name}.odcs.yaml")


def _contract(*, id="analytics.t", table="t", columns=(), custom=()):
    """A bare contract: only what `node_from_odcs` reads."""
    return {
        "id": id,
        "customProperties": [
            {"vendor": "continuo", "property": name, "value": value}
            for name, value in custom
        ],
        "schema": [
            {"name": table, "physicalType": "table", "properties": list(columns)}
        ],
    }


def _column(name, **fields):
    return {"name": name, "physicalType": "INTEGER", **fields}


def _issues(doc):
    """The issues of the demo contract `doc`, with the service left unknown."""
    issues = validate_python_contract(
        doc, path="contracts/py_daily_kpis.odcs.yaml", service=None
    )
    return [str(issue) for issue in issues]


def _upsert_kpis(*keys):
    """The demo `py_daily_kpis` contract as an upsert node whose key is `keys`, each a column
    name with the position it is written with (`None` writes no position)."""
    doc = _demo("py_daily_kpis")
    doc["customProperties"].append(
        {"vendor": "continuo", "property": "modeType", "value": "upsert"}
    )
    columns = {
        column["name"]: column for column in doc["schema"][0]["properties"]
    }
    for name, position in keys:
        columns[name]["primaryKey"] = True
        if position is not None:
            columns[name]["primaryKeyPosition"] = position
    return doc


# the demo nodes: the projection does not move


@pytest.mark.parametrize("table", DEMO_TABLES)
def test_the_demo_projection_does_not_move(table):
    old = yaml.safe_load((OLD / f"{table}.yml").read_text(encoding="utf-8"))[
        "nodes"
    ][0]
    new = node_from_odcs(_demo(table))
    assert code_projection(new) == code_projection(PythonNode.from_mapping(old))
    assert projection_hash(new) == projection_hash(PythonNode.from_mapping(old))


@pytest.mark.parametrize("table", DEMO_TABLES)
def test_the_demo_contracts_are_valid(table):
    issues = validate_python_contract(
        _demo(table), path=f"contracts/{table}.odcs.yaml", service="service-py"
    )
    assert [str(issue) for issue in issues] == []


def test_the_demo_contracts_project_to_the_pinned_demo_vectors():
    pinned = {case["name"]: case for case in VECTORS}
    for table, name in [
        ("py_daily_kpis", "demo_py_daily_kpis"),
        ("demo_orders_csv", "demo_orders_csv"),
        ("fx_eur_usd_rates", "demo_fx_eur_usd_rates"),
    ]:
        node = node_from_odcs(_demo(table))
        assert code_projection(node) == pinned[name]["projection"], table
        assert projection_hash(node) == pinned[name]["projection_hash"], table


def test_the_csv_node_reads_its_source():
    node = node_from_odcs(_demo("demo_orders_csv"))
    assert node.reads == {"csv": "s3://continuo-dev/static-files/demo/orders.csv"}
    assert node.script == ""


def test_the_api_node_keeps_its_secret_and_reads_nothing():
    node = node_from_odcs(_demo("fx_eur_usd_rates"))
    assert node.secret_ref == "continuo-api-demo-fx"
    assert dict(node.reads) == {}


# every vector, written as a contract and read back


def _contract_of(entry):
    """The contract of a vector's wire entry, with the properties a Python node writes."""
    kind = entry.get("kind", "python-node")
    custom = [("kind", kind)]
    if "script" in entry:
        custom.append(("script", entry["script"]))
    if kind == "python-csv":
        custom.append(("csvSource", entry["reads"]["csv"]))
    elif entry.get("reads"):
        custom.append(("readQueries", entry["reads"]))
    for field, name in [
        ("secret_ref", "secretRef"),
        ("mode_type", "modeType"),
        ("extra_columns", "extraColumns"),
        ("config", "config"),
        ("assets", "assets"),
    ]:
        if field in entry:
            custom.append((name, entry[field]))
    key = entry.get("primary_key", [])
    columns = []
    for column in entry["output_columns"]:
        written = {
            "name": column["name"],
            "physicalType": column["type"],
            "required": not column.get("nullable", True),
        }
        if column["name"] in key:
            written["primaryKey"] = True
            written["primaryKeyPosition"] = key.index(column["name"]) + 1
        columns.append(written)
    schema, table = entry["schema"], entry["table"]
    return _contract(
        id=f"{schema}.{table}".lower(),
        table=table,
        columns=columns,
        custom=custom,
    )


@pytest.mark.parametrize("case", VECTORS, ids=lambda case: case["name"])
def test_the_projection_vectors_still_pass(case):
    node = node_from_odcs(_contract_of(case["node"]))
    assert code_projection(node) == case["projection"]
    assert projection_hash(node) == case["projection_hash"]


# the mapping


def test_a_contract_with_nothing_but_a_table_takes_the_defaults():
    node = node_from_odcs(_contract(columns=[_column("x")]))
    assert node == PythonNode(
        schema="analytics",
        table="t",
        kind="python-node",
        script="",
        reads={},
        output_columns=(PythonColumn("x", "INTEGER", nullable=True),),
        extra_columns="raise",
        config={},
        secret_ref="",
        mode_type="replace",
        primary_key=(),
        assets=(),
    )


def test_every_property_of_a_full_python_node_is_read():
    node = node_from_odcs(_full("python_node"))
    assert (node.schema, node.table, node.kind) == (
        "analytics",
        "python_node",
        "python-node",
    )
    assert node.script == "scripts/python_node.py"
    assert set(node.reads) == {"orders", "rates"}
    assert (
        node.reads["rates"]
        == "select currency, rate_to_eur from analytics.fx_rates"
    )
    assert node.extra_columns == "warn"
    assert node.config == {
        "batch_size": 5000,
        "compression": "zstd",
        "tags": ["a", "b"],
    }
    assert node.secret_ref == ""
    assert node.mode_type == "upsert"
    assert node.primary_key == ("order_id", "line_no")
    assert list(node.assets) == ["assets/currencies.csv", "assets/rounding.json"]
    assert [column.name for column in node.output_columns] == [
        "order_id",
        "line_no",
        "customer_email",
        "amount",
        "weight",
        "seen_at",
    ]


def test_the_columns_keep_their_physical_type_as_written_and_are_nullable_unless_required():
    node = node_from_odcs(
        _contract(
            columns=[
                {"name": "a", "physicalType": "int", "required": True},
                {"name": "b", "physicalType": "VARCHAR(64)"},
                {"name": "c", "physicalType": "DATE", "required": False},
            ]
        )
    )
    assert node.output_columns == (
        PythonColumn("a", "int", nullable=False),
        PythonColumn("b", "VARCHAR(64)", nullable=True),
        PythonColumn("c", "DATE", nullable=True),
    )
    assert [column["type"] for column in code_projection(node)["output_columns"]][
        0
    ] == "INTEGER"


def test_the_columns_come_in_file_order():
    node = node_from_odcs(
        _contract(columns=[_column(name) for name in ["z", "a", "m"]])
    )
    assert [column.name for column in node.output_columns] == ["z", "a", "m"]


def test_python_model_is_read_as_python_node():
    node = node_from_odcs(
        _contract(columns=[_column("x")], custom=[("kind", "python-model")])
    )
    assert node.kind == "python-node"


def test_another_vendors_property_is_not_read():
    doc = _contract(columns=[_column("x")], custom=[("script", "scripts/t.py")])
    doc["customProperties"].append(
        {"vendor": "lotse", "property": "script", "value": "other.py"}
    )
    doc["customProperties"].insert(
        0, {"vendor": "lotse", "property": "modeType", "value": "append"}
    )
    node = node_from_odcs(doc)
    assert (node.script, node.mode_type) == ("scripts/t.py", "replace")


def test_the_schema_is_the_id_without_the_table():
    node = node_from_odcs(
        _contract(id="raw_data.orders", table="Orders", columns=[_column("x")])
    )
    assert (node.schema, node.table) == ("raw_data", "Orders")


def test_the_id_lower_cases_only_the_ascii_letters_of_the_table():
    node = node_from_odcs(
        _contract(id="raw.k\u00c4se", table="K\u00c4SE", columns=[_column("x")])
    )
    assert (node.schema, node.table) == ("raw", "K\u00c4SE")
    with pytest.raises(ProjectionError):
        node_from_odcs(
            _contract(
                id="raw.k\u00e4se", table="K\u00c4SE", columns=[_column("x")]
            )
        )


def test_node_from_odcs_and_the_validator_read_the_same_schema_from_the_id():
    doc = _demo("py_daily_kpis")
    doc["id"] = "analytics\ua7cb.py_daily_kpis"
    assert _issues(doc) == []
    assert node_from_odcs(doc).schema == "analytics\ua7cb"


def test_the_reads_of_the_node_are_a_copy_of_the_document():
    doc = _full("python_node")
    before = copy.deepcopy(doc)
    node = node_from_odcs(doc)
    node.reads["extra"] = "select 1"  # type: ignore[index]
    assert doc == before


# a contract that stands for no node


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.pop("schema"),
        lambda doc: doc.update(schema=[]),
        lambda doc: doc.update(schema="analytics.t"),
        lambda doc: doc.update(schema=["t"]),
        lambda doc: doc["schema"][0].pop("name"),
        lambda doc: doc["schema"][0].update(name=7),
        lambda doc: doc.pop("id"),
        lambda doc: doc.update(id="analytics.other"),
        lambda doc: doc.update(id="t"),
        lambda doc: doc.update(id=".t"),
    ],
    ids=[
        "no schema",
        "empty schema",
        "schema is a string",
        "schema object is a string",
        "no table name",
        "table name is a number",
        "no id",
        "id of another table",
        "id with no schema",
        "id with an empty schema",
    ],
)
def test_a_contract_that_names_no_table_is_refused(change):
    doc = _contract(columns=[_column("x")])
    change(doc)
    with pytest.raises(ProjectionError):
        node_from_odcs(doc)


def test_a_document_that_is_not_a_mapping_is_refused():
    with pytest.raises(ProjectionError, match="mapping"):
        node_from_odcs(["analytics.t"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "custom",
    [
        [("readQueries", ["select 1"])],
        [("config", [1])],
        [("assets", "a.csv")],
    ],
)
def test_a_property_of_the_wrong_shape_is_refused_as_a_projection_error(custom):
    with pytest.raises(ProjectionError):
        node_from_odcs(_contract(columns=[_column("x")], custom=custom))


@pytest.mark.parametrize(
    "column", [["x", "INTEGER"], {"name": "x"}, {"physicalType": "DATE"}]
)
def test_a_column_without_a_name_or_a_type_is_refused(column):
    with pytest.raises(ProjectionError):
        node_from_odcs(_contract(columns=[column]))


# the key


def test_an_upsert_key_follows_its_positions():
    doc = _contract(
        columns=[
            _column("a", primaryKey=True, primaryKeyPosition=2),
            _column("b", primaryKey=True, primaryKeyPosition=1),
            _column("c"),
        ],
        custom=[("modeType", "upsert")],
    )
    node = node_from_odcs(doc)
    assert node.primary_key == ("b", "a")
    assert code_projection(node)["primary_key"] == ["b", "a"]


def test_the_positions_of_a_key_need_not_be_next_to_each_other_or_in_file_order():
    names = ["a", "b", "c", "d"]
    positions = [4, 1, 9, 2]
    doc = _contract(
        columns=[
            _column(name, primaryKey=True, primaryKeyPosition=position)
            for name, position in zip(names, positions, strict=True)
        ],
        custom=[("modeType", "upsert")],
    )
    assert node_from_odcs(doc).primary_key == ("b", "d", "a", "c")


def test_a_position_written_as_a_float_reads_as_its_whole_number():
    doc = _upsert_kpis(("tx_count", 1.0), ("day", 2.0))
    assert _issues(doc) == []
    assert node_from_odcs(doc).primary_key == ("tx_count", "day")


def test_the_validator_and_the_node_order_a_valid_key_the_same_way():
    doc = _upsert_kpis(("total_amount", 3), ("day", 1), ("tx_count", 2))
    assert _issues(doc) == []
    assert node_from_odcs(doc).primary_key == ("day", "tx_count", "total_amount")


def test_a_key_of_one_column_needs_no_position():
    doc = _upsert_kpis(("day", None))
    assert _issues(doc) == []
    node = node_from_odcs(doc)
    assert node.primary_key == ("day",)
    assert code_projection(node)["primary_key"] == ["day"]


def test_a_key_column_that_is_not_a_key_stays_out():
    doc = _contract(
        columns=[_column("a", primaryKey=False), _column("b", primaryKey=True)],
        custom=[("modeType", "upsert")],
    )
    assert node_from_odcs(doc).primary_key == ("b",)


def test_a_descriptive_key_without_positions_keeps_the_file_order():
    doc = _contract(
        columns=[_column("z", primaryKey=True), _column("a", primaryKey=True)]
    )
    assert node_from_odcs(doc).primary_key == ("z", "a")


@pytest.mark.parametrize("mode", ["replace", "append"])
def test_a_descriptive_key_leaves_the_projection_empty(mode):
    custom = [("modeType", mode)]
    keyed = _contract(
        columns=[_column("a", primaryKey=True), _column("b")], custom=custom
    )
    plain = _contract(columns=[_column("a"), _column("b")], custom=custom)
    node = node_from_odcs(keyed)
    assert node.primary_key == ("a",)
    assert code_projection(node)["primary_key"] == []
    assert projection_hash(node) == projection_hash(node_from_odcs(plain))
