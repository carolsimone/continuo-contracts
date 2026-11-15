"""`contract`: the stored form of a Python contract and its `contract_hash`."""

import copy
from pathlib import Path

import pytest

from continuo_contracts import fingerprints, yaml_strict
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import (
    contract_hash,
    logical_type,
    normalise_python,
)
from continuo_contracts.errors import CanonicalJsonError, HashInputError
from continuo_contracts.python import node_from_odcs, projection_hash
from continuo_contracts.validate import validate_python_contract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = FIXTURES / "demo" / "service-py" / "contracts"
FULL = FIXTURES / "full"
FULL_NAMES = ["python_node", "python_csv", "python_api"]
LABELS = ("apiVersion", "kind", "status", "version")

PURPOSE = "Daily transaction count and total amount in EUR, rolled up from analytics.daily_transactions."
DAILY = "select created_at, amount_eur from analytics.daily_transactions"

KPIS_STORED = {
    "apiVersion": "v3.2.0",
    "kind": "DataContract",
    "id": "analytics.py_daily_kpis",
    "name": "py_daily_kpis",
    "version": "0",
    "status": "active",
    "description": {"purpose": PURPOSE},
    "team": {"name": "service-py"},
    "customProperties": [
        {"property": "criticality", "value": "SECONDARY", "vendor": "continuo"},
        {"property": "kind", "value": "python-node", "vendor": "continuo"},
        {
            "property": "readQueries",
            "value": {"daily": DAILY},
            "vendor": "continuo",
        },
        {"property": "schedule", "value": "daily", "vendor": "continuo"},
        {
            "property": "script",
            "value": "scripts/py_daily_kpis.py",
            "vendor": "continuo",
        },
        {"property": "service", "value": "service-py", "vendor": "continuo"},
    ],
    "schema": [
        {
            "name": "py_daily_kpis",
            "physicalName": "py_daily_kpis",
            "physicalType": "table",
            "customProperties": [
                {
                    "property": "reads",
                    "value": ["analytics.daily_transactions"],
                    "vendor": "continuo",
                }
            ],
            "properties": [
                {
                    "name": "day",
                    "physicalType": "DATE",
                    "logicalType": "date",
                    "required": True,
                },
                {
                    "name": "tx_count",
                    "physicalType": "INTEGER",
                    "logicalType": "integer",
                    "required": True,
                },
                {
                    "name": "total_amount",
                    "physicalType": "NUMERIC(12,2)",
                    "logicalType": "number",
                    "required": True,
                },
            ],
        }
    ],
}

KPIS_READS = ["analytics.daily_transactions"]


def _load(path):
    return yaml_strict.load(path.read_text(encoding="utf-8"))


def _kpis():
    return _load(DEMO / "py_daily_kpis.odcs.yaml")


def _csv():
    return _load(DEMO / "demo_orders_csv.odcs.yaml")


def _full(name):
    return _load(FULL / f"{name}.odcs.yaml")


def _valid(doc, table):
    """Return `doc` after checking that `validate_python_contract` accepts it, so a test never
    normalises a document that the contract check refuses."""
    path = f"contracts/{table}.odcs.yaml"
    issues = validate_python_contract(doc, path=path, service="service-py")
    assert [str(issue) for issue in issues] == []
    return doc


def _stored(doc, table="py_daily_kpis", reads=()):
    return normalise_python(
        _valid(doc, table), service="service-py", reads=list(reads)
    )


def _full_stored(name):
    return _stored(_full(name), name)


def _custom(name, value, vendor="continuo"):
    return {"vendor": vendor, "property": name, "value": value}


# the stored form of the demo nodes


def test_the_stored_form_of_the_demo_node():
    assert (
        normalise_python(
            _kpis(), service="service-py", reads=["Analytics.Daily_Transactions"]
        )
        == KPIS_STORED
    )


def test_the_csv_node_gets_a_max_length():
    stored = normalise_python(_csv(), service="service-py", reads=[])
    assert stored["schema"][0]["properties"][1] == {
        "name": "customer",
        "physicalType": "VARCHAR(64)",
        "logicalType": "string",
        "logicalTypeOptions": {"maxLength": 64},
    }
    assert "customProperties" not in stored["schema"][0]


def test_the_csv_node_stores_its_service_status_physical_name_and_logical_types():
    stored = normalise_python(_csv(), service="service-py", reads=[])
    assert stored["version"] == "0"
    assert stored["status"] == "active"
    assert stored["schema"][0]["physicalName"] == "demo_orders_csv"
    assert _custom("service", "service-py") in stored["customProperties"]
    assert _custom("kind", "python-csv") in stored["customProperties"]
    assert [c.get("logicalType") for c in stored["schema"][0]["properties"]] == [
        "integer",
        "string",
        "number",
    ]


# contract_hash


def test_contract_hash_ignores_the_four_labels():
    a = dict(KPIS_STORED)
    b = dict(KPIS_STORED, version="7", status="retired")
    assert contract_hash(a) == contract_hash(b)
    assert contract_hash(a).startswith("sha256:") and len(contract_hash(a)) == 71


@pytest.mark.parametrize("label", LABELS)
def test_contract_hash_ignores_each_label_and_its_absence(label):
    changed = dict(KPIS_STORED, **{label: "other"})
    absent = {key: value for key, value in KPIS_STORED.items() if key != label}
    assert contract_hash(changed) == contract_hash(KPIS_STORED)
    assert contract_hash(absent) == contract_hash(KPIS_STORED)


def test_contract_hash_is_the_sha256_of_the_canonical_json_without_the_labels():
    import hashlib

    view = {key: value for key, value in KPIS_STORED.items() if key not in LABELS}
    expected = "sha256:" + hashlib.sha256(canonical_json(view)).hexdigest()
    assert contract_hash(KPIS_STORED) == expected


def test_contract_hash_leaves_out_the_keys_that_fingerprints_yaml_lists(
    monkeypatch,
):
    left_out = fingerprints.contract_hash_exclusions() | {"id"}
    monkeypatch.setattr(
        fingerprints, "contract_hash_exclusions", lambda: left_out
    )
    assert contract_hash(KPIS_STORED) == contract_hash(
        dict(KPIS_STORED, id="analytics.other")
    )
    monkeypatch.undo()
    assert contract_hash(KPIS_STORED) != contract_hash(
        dict(KPIS_STORED, id="analytics.other")
    )


def test_contract_hash_refuses_a_value_canonical_json_cannot_write():
    with pytest.raises(CanonicalJsonError):
        contract_hash(dict(KPIS_STORED, extra=float("nan")))


def _leaves(value, path=()):
    """Yield the path of every scalar under `value`."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, (*path, index))
    else:
        yield path


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value + 1
    return value + "x"


@pytest.mark.parametrize("name", FULL_NAMES)
def test_a_change_to_any_stored_field_moves_the_hash(name):
    stored = _full_stored(name)
    original = contract_hash(stored)
    paths = [path for path in _leaves(stored) if path[0] not in LABELS]
    assert len(paths) > 50
    seen = {original}
    for path in paths:
        changed = copy.deepcopy(stored)
        parent = changed
        for part in path[:-1]:
            parent = parent[part]
        parent[path[-1]] = _changed(parent[path[-1]])
        digest = contract_hash(changed)
        assert digest != original, path
        seen.add(digest)
    assert len(seen) == len(paths) + 1


@pytest.mark.parametrize("name", FULL_NAMES)
def test_dropping_any_stored_key_moves_the_hash(name):
    stored = _full_stored(name)
    original = contract_hash(stored)
    for key in [key for key in stored if key not in LABELS]:
        assert (
            contract_hash({k: v for k, v in stored.items() if k != key})
            != original
        ), key


# spelling does not matter

KPIS_REWRITTEN = """\
kind: 'DataContract'
apiVersion: "v3.2.0"
version: '0'
id: "analytics.py_daily_kpis"
team: {name: "service-py"}
description:
  {purpose: "Daily transaction count and total amount in EUR, rolled up from \\
    analytics.daily_transactions."}
name: 'py_daily_kpis'
schema:
- properties:
  - name: day
    required: true
    physicalType: "DATE"
  - {required: true, physicalType: 'INTEGER', name: "tx_count"}
  - required: true
    name: total_amount
    physicalType: NUMERIC(12,2)
  physicalType: "table"
  name: py_daily_kpis
customProperties:
- property: readQueries
  value: {daily: "select created_at, amount_eur from analytics.daily_transactions"}
  vendor: continuo
- {value: "scripts/py_daily_kpis.py", vendor: 'continuo', property: script}
- {property: criticality, value: "SECONDARY", vendor: continuo}
- {vendor: continuo, value: daily, property: 'schedule'}
"""


def _kpis_spellings():
    text = (DEMO / "py_daily_kpis.odcs.yaml").read_text(encoding="utf-8")
    crlf = text.replace("\n", "\r\n")
    return {
        "as_written": text,
        "crlf": crlf,
        "bom_and_crlf": "\ufeff" + crlf,
        "key_order_and_quoting": KPIS_REWRITTEN,
    }


def test_crlf_bom_key_order_and_quoting_hash_alike():
    spellings = _kpis_spellings()
    assert len(set(spellings.values())) == 4
    stored = {
        name: _stored(yaml_strict.load(text), reads=KPIS_READS)
        for name, text in spellings.items()
    }
    for name, form in stored.items():
        assert form == KPIS_STORED, name
        assert contract_hash(form) == contract_hash(KPIS_STORED), name


def _with_column(doc, index, **fields):
    doc["schema"][0]["properties"][index].update(fields)
    return doc


def _with_quality(doc, **fields):
    doc["schema"][0]["properties"][0]["quality"] = [
        {"metric": "nullValues", "mustBe": 0, **fields}
    ]
    return doc


def _with_relationship(doc, **fields):
    doc["schema"][0]["properties"][0]["relationships"] = [
        {"to": "analytics.days.day", **fields}
    ]
    return doc


def _with_root(doc, **fields):
    doc.update(fields)
    return doc


def _with_property(doc, item):
    doc["customProperties"].append(item)
    return doc


EMPTY_OR_DEFAULT = {
    "root_tags_empty": lambda d: _with_root(d, tags=[]),
    "root_roles_empty": lambda d: _with_root(d, roles=[]),
    "root_domain_empty_string": lambda d: _with_root(d, domain=""),
    "team_members_empty": lambda d: _with_root(
        d, team={"name": "service-py", "members": []}
    ),
    "schema_object_quality_empty": lambda d: (
        d["schema"][0].update(quality=[]) or d
    ),
    "schema_object_deprecated_false": lambda d: (
        d["schema"][0].update(deprecated=False) or d
    ),
    "schema_object_description_blank": lambda d: (
        d["schema"][0].update(description=" \r\n") or d
    ),
    "column_quality_empty": lambda d: _with_column(d, 0, quality=[]),
    "column_tags_empty": lambda d: _with_column(d, 0, tags=[]),
    "column_examples_empty": lambda d: _with_column(d, 0, examples=[]),
    "column_unique_false": lambda d: _with_column(d, 0, unique=False),
    "column_deprecated_false": lambda d: _with_column(d, 0, deprecated=False),
    "column_primary_key_position_minus_one": lambda d: _with_column(
        d, 0, primaryKeyPosition=-1
    ),
    "column_semantic_type_column": lambda d: _with_column(
        d, 0, semanticType="column"
    ),
    "column_partitioned_false": lambda d: _with_column(d, 0, partitioned=False),
    "column_partition_key_position_minus_one": lambda d: _with_column(
        d, 0, partitionKeyPosition=-1
    ),
    "column_critical_data_element_false": lambda d: _with_column(
        d, 0, criticalDataElement=False
    ),
    "column_primary_key_false": lambda d: _with_column(d, 0, primaryKey=False),
    "column_physical_name_equal_to_name": lambda d: _with_column(
        d, 0, physicalName="day"
    ),
    "schema_object_logical_type_object": lambda d: (
        d["schema"][0].update(logicalType="object") or d
    ),
    "column_classification_empty": lambda d: _with_column(
        d, 0, classification=""
    ),
    "extra_columns_raise": lambda d: _with_property(
        d, _custom("extraColumns", "raise")
    ),
    "other_vendor_value_null": lambda d: _with_property(
        d, _custom("glossaryTerm", None, "atlan")
    ),
    "other_vendor_value_empty_string": lambda d: _with_property(
        d, _custom("term", "", "atlan")
    ),
    "other_vendor_value_empty_list": lambda d: _with_property(
        d, _custom("terms", [], "atlan")
    ),
    "other_vendor_value_empty_object": lambda d: _with_property(
        d, _custom("map", {}, "atlan")
    ),
    "config_empty": lambda d: _with_property(d, _custom("config", {})),
    "assets_empty": lambda d: _with_property(d, _custom("assets", [])),
}


@pytest.mark.parametrize("name", sorted(EMPTY_OR_DEFAULT))
def test_an_empty_or_default_value_hashes_like_an_absent_one(name):
    plain = _stored(_kpis(), reads=KPIS_READS)
    written = _stored(EMPTY_OR_DEFAULT[name](_kpis()), reads=KPIS_READS)
    assert written == plain
    assert contract_hash(written) == contract_hash(plain)


@pytest.mark.parametrize(
    ("parent", "key"),
    [
        (lambda d: d, "description"),
        (lambda d: d, "tags"),
        (lambda d: d["schema"][0]["properties"][0], "description"),
        (lambda d: d["schema"][0]["properties"][0], "unique"),
    ],
    ids=["root_description", "root_tags", "column_description", "column_unique"],
)
def test_a_null_odcs_field_is_refused_by_the_schema_and_dropped_by_normalise(
    parent, key
):
    doc = _kpis()
    parent(doc)[key] = None
    issues = validate_python_contract(
        doc, path="contracts/py_daily_kpis.odcs.yaml", service=None
    )
    assert {issue.reason for issue in issues} == {"odcs_schema"}
    absent = _kpis()
    parent(absent).pop(key, None)
    expected = normalise_python(absent, service="service-py", reads=KPIS_READS)
    assert (
        normalise_python(doc, service="service-py", reads=KPIS_READS) == expected
    )


@pytest.mark.parametrize(
    "fields",
    [{"type": "library"}, {"severity": ""}, {"description": "  "}, {"tags": []}],
    ids=["type_library", "severity_empty", "description_blank", "tags_empty"],
)
def test_an_empty_or_default_quality_field_hashes_like_an_absent_one(fields):
    plain = _stored(_with_quality(_kpis()), reads=KPIS_READS)
    written = _stored(_with_quality(_kpis(), **fields), reads=KPIS_READS)
    assert written == plain


def test_a_relationship_of_type_foreign_key_hashes_like_one_without_a_type():
    plain = _stored(_with_relationship(_kpis()), reads=KPIS_READS)
    written = _stored(
        _with_relationship(_kpis(), type="foreignKey"), reads=KPIS_READS
    )
    assert written == plain


def test_a_csv_node_that_replaces_its_table_hashes_like_one_that_does_not_say():
    plain = _stored(_csv(), "demo_orders_csv")
    written = _stored(
        _with_property(_csv(), _custom("modeType", "replace")), "demo_orders_csv"
    )
    assert written == plain
    assert contract_hash(written) == contract_hash(plain)


def test_required_false_hashes_like_an_absent_required():
    plain = _stored(_csv(), "demo_orders_csv")
    written = _stored(_with_column(_csv(), 1, required=False), "demo_orders_csv")
    assert written == plain


@pytest.mark.parametrize(
    "fields",
    [
        {"required": True},
        {"unique": True},
        {"criticalDataElement": True},
        {"semanticType": "measure"},
        {"deprecated": True},
        {"classification": "public"},
    ],
    ids=lambda fields: next(iter(fields)),
)
def test_a_value_other_than_the_default_is_kept(fields):
    stored = _stored(_with_column(_csv(), 1, **fields), "demo_orders_csv")
    assert {**fields}.items() <= stored["schema"][0]["properties"][1].items()


def test_a_non_default_mode_type_and_extra_columns_are_kept():
    doc = _with_property(_kpis(), _custom("modeType", "append"))
    doc = _with_property(doc, _custom("extraColumns", "warn"))
    stored = _stored(doc, reads=KPIS_READS)
    assert _custom("modeType", "append") in stored["customProperties"]
    assert _custom("extraColumns", "warn") in stored["customProperties"]


def test_another_vendors_property_with_a_continuo_default_is_kept():
    doc = _with_property(_kpis(), _custom("modeType", "replace", "lotse"))
    stored = _stored(doc, reads=KPIS_READS)
    assert _custom("modeType", "replace", "lotse") in stored["customProperties"]


def test_the_kind_is_always_written_and_an_old_name_is_stored_as_its_kind():
    assert (
        _custom("kind", "python-node")
        in _stored(_kpis(), reads=KPIS_READS)["customProperties"]
    )
    doc = _with_property(_kpis(), _custom("kind", "python-model"))
    stored = _stored(doc, reads=KPIS_READS)
    assert stored == KPIS_STORED


def test_a_written_service_is_stored_once():
    doc = _with_property(_kpis(), _custom("service", "service-py"))
    assert _stored(doc, reads=KPIS_READS) == KPIS_STORED


def test_the_root_name_and_physical_name_are_filled_when_absent():
    doc = _kpis()
    del doc["name"]
    assert "physicalName" not in doc["schema"][0]
    assert _stored(doc, reads=KPIS_READS) == KPIS_STORED


def test_the_stored_labels_replace_the_written_ones():
    doc = _with_root(_kpis(), version="12", status="draft")
    stored = _stored(doc, reads=KPIS_READS)
    assert stored["version"] == "0"
    assert stored["status"] == "active"


# numbers


def test_a_whole_number_float_is_stored_as_an_integer():
    as_float = _with_property(_kpis(), _custom("config", {"batch_size": 10.0}))
    as_int = _with_property(_kpis(), _custom("config", {"batch_size": 10}))
    stored = _stored(copy.deepcopy(as_float), reads=KPIS_READS)
    config = next(
        p for p in stored["customProperties"] if p["property"] == "config"
    )["value"]
    assert config == {"batch_size": 10}
    assert type(config["batch_size"]) is int
    assert contract_hash(stored) == contract_hash(
        _stored(as_int, reads=KPIS_READS)
    )
    assert projection_hash(node_from_odcs(as_float)) == projection_hash(
        node_from_odcs(as_int)
    )


def test_whole_number_floats_become_integers_everywhere_and_fractions_stay():
    doc = _with_property(
        _kpis(),
        _custom(
            "config", {"nested": [1.0, {"x": -3.0}], "rate": 0.5, "flag": True}
        ),
    )
    doc = _with_column(doc, 0, examples=[2.0, "2.0"])
    doc["schema"][0]["properties"][1]["primaryKeyPosition"] = -1.0
    stored = _stored(doc, reads=KPIS_READS)
    values = {p["property"]: p["value"] for p in stored["customProperties"]}
    assert values["config"] == {
        "nested": [1, {"x": -3}],
        "rate": 0.5,
        "flag": True,
    }
    assert type(values["config"]["nested"][0]) is int
    assert values["config"]["flag"] is True
    assert "primaryKeyPosition" not in stored["schema"][0]["properties"][0]
    assert stored["schema"][0]["properties"][0]["examples"] == [2, "2.0"]


def test_a_whole_number_float_outside_the_safe_integer_range_stays_a_float():
    doc = _with_property(
        _kpis(), _custom("config", {"big": 1e300, "edge": 2.0**53})
    )
    stored = _stored(doc, reads=KPIS_READS)
    config = next(
        p for p in stored["customProperties"] if p["property"] == "config"
    )["value"]
    assert type(config["big"]) is float and type(config["edge"]) is float
    contract_hash(stored)


# logical types


@pytest.mark.parametrize(
    ("physical", "logical"),
    [
        ("INTEGER", {"logicalType": "integer"}),
        ("int", {"logicalType": "integer"}),
        ("BIGINT", {"logicalType": "integer"}),
        ("NUMERIC(12,2)", {"logicalType": "number"}),
        ("decimal(5, 1)", {"logicalType": "number"}),
        ("DOUBLE PRECISION", {"logicalType": "number"}),
        ("TEXT", {"logicalType": "string"}),
        (
            "VARCHAR(64)",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 64}},
        ),
        (
            "char(003)",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 3}},
        ),
        ("DATE", {"logicalType": "date"}),
        ("timestamp", {"logicalType": "timestamp"}),
        ("BOOLEAN", {"logicalType": "boolean"}),
        (
            "VARCHAR(9007199254740991)",
            {
                "logicalType": "string",
                "logicalTypeOptions": {"maxLength": 9007199254740991},
            },
        ),
        ("NUMERIC(12)", None),
        ("JSONB", None),
        ("", None),
        ("VARCHAR(0)", None),
        ("CHAR(000)", None),
        ("VARCHAR(9007199254740992)", None),
        ("CHAR(99999999999999999999)", None),
        ("VARCHAR(" + "9" * 5000 + ")", None),
        (
            "VARCHAR(" + "0" * 5000 + "7)",
            {"logicalType": "string", "logicalTypeOptions": {"maxLength": 7}},
        ),
    ],
    ids=lambda value: value[:30] if isinstance(value, str) else None,
)
def test_logical_type(physical, logical):
    assert logical_type(physical) == logical


def test_logical_type_gives_a_new_dict_each_time():
    first = logical_type("VARCHAR(5)")
    assert first is not None
    first["logicalTypeOptions"]["maxLength"] = 9
    assert logical_type("VARCHAR(5)") == {
        "logicalType": "string",
        "logicalTypeOptions": {"maxLength": 5},
    }


# the physical type


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("date", "DATE"),
        (" DATE", "DATE"),
        ("DATE\n", "DATE"),
        ("int", "INTEGER"),
        ("Integer", "INTEGER"),
        ("decimal(12, 2)", "NUMERIC(12,2)"),
        ("NUMERIC(012,02)", "NUMERIC(12,2)"),
        ("varchar(0010)", "VARCHAR(10)"),
        ("double precision", "DOUBLE PRECISION"),
    ],
)
def test_the_physical_type_is_stored_in_its_canonical_spelling(
    written, canonical
):
    doc = _with_column(_kpis(), 0, physicalType=written)
    stored = _stored(doc, reads=KPIS_READS)
    assert stored["schema"][0]["properties"][0]["physicalType"] == canonical
    clean = _stored(
        _with_column(_kpis(), 0, physicalType=canonical), reads=KPIS_READS
    )
    assert stored == clean
    assert contract_hash(stored) == contract_hash(clean)


def test_two_spellings_of_a_type_give_one_contract_hash_and_one_projection_hash():
    plain = _with_column(_kpis(), 2, physicalType="NUMERIC(12,2)")
    spelled = _with_column(_kpis(), 2, physicalType="decimal(12, 2)")
    assert contract_hash(_stored(plain, reads=KPIS_READS)) == contract_hash(
        _stored(spelled, reads=KPIS_READS)
    )
    assert projection_hash(node_from_odcs(plain)) == projection_hash(
        node_from_odcs(spelled)
    )


def test_a_physical_type_that_is_not_accepted_is_stored_as_written():
    # The contract check refuses it, so no stored form holds it; normalise_python leaves it alone.
    doc = _with_column(_kpis(), 0, physicalType="JSONB")
    stored = normalise_python(doc, service="service-py", reads=[])
    assert stored["schema"][0]["properties"][0]["physicalType"] == "JSONB"
    assert "logicalType" not in stored["schema"][0]["properties"][0]


# the key


def _keyed(position):
    """The demo contract with `day` as its one key column, written with `position` (`None`
    writes none)."""
    doc = _with_column(_kpis(), 0, primaryKey=True)
    if position is not None:
        doc["schema"][0]["properties"][0]["primaryKeyPosition"] = position
    return doc


@pytest.mark.parametrize("position", [1, 7, 1.0, None])
def test_the_one_column_of_a_key_is_stored_without_its_position(position):
    stored = _stored(_keyed(position), reads=KPIS_READS)
    column = stored["schema"][0]["properties"][0]
    assert column["primaryKey"] is True
    assert "primaryKeyPosition" not in column
    assert contract_hash(stored) == contract_hash(
        _stored(_keyed(None), reads=KPIS_READS)
    )


def test_the_columns_of_a_key_of_several_keep_their_positions():
    doc = _with_column(_keyed(2), 1, primaryKey=True, primaryKeyPosition=1)
    stored = _stored(doc, reads=KPIS_READS)
    columns = stored["schema"][0]["properties"]
    assert [column.get("primaryKeyPosition") for column in columns] == [
        2,
        1,
        None,
    ]
    swapped = _with_column(_keyed(1), 1, primaryKey=True, primaryKeyPosition=2)
    assert contract_hash(stored) != contract_hash(
        _stored(swapped, reads=KPIS_READS)
    )


def test_the_position_of_a_column_that_is_not_in_the_key_is_kept():
    doc = _with_column(_keyed(1), 1, primaryKeyPosition=3)
    columns = _stored(doc, reads=KPIS_READS)["schema"][0]["properties"]
    assert "primaryKeyPosition" not in columns[0]
    assert columns[1]["primaryKeyPosition"] == 3


def test_an_authored_logical_type_is_replaced():
    doc = _with_column(_kpis(), 1, logicalType="string")
    stored = _stored(doc, reads=KPIS_READS)
    assert stored == KPIS_STORED


def test_an_authored_logical_type_options_is_refused_by_the_contract_check():
    doc = _with_column(
        _csv(), 1, logicalType="string", logicalTypeOptions={"minLength": 1}
    )
    issues = validate_python_contract(
        doc, path="contracts/demo_orders_csv.odcs.yaml", service=None
    )
    assert [(issue.path, issue.reason) for issue in issues] == [
        ("schema[0].properties[1].logicalTypeOptions", "refused_field")
    ]


def test_a_service_level_value_ext_that_is_null_or_empty_is_dropped():
    plain = _stored(
        _with_root(_kpis(), slaProperties=[{"property": "retention", "value": 3}])
    )
    for empty in (None, ""):
        sla = [{"property": "retention", "value": 3, "valueExt": empty}]
        assert (
            normalise_python(
                _with_root(_kpis(), slaProperties=sla),
                service="service-py",
                reads=[],
            )
            == plain
        )


def test_a_service_level_value_is_kept_even_when_empty():
    stored = _stored(
        _with_root(
            _kpis(), slaProperties=[{"property": "retention", "value": ""}]
        )
    )
    assert stored["slaProperties"] == [{"property": "retention", "value": ""}]


def test_the_schema_object_keeps_its_physical_name_and_a_column_physical_name_other_than_its_name():
    doc = _kpis()
    doc["schema"][0]["physicalName"] = "py_daily_kpis"
    stored = normalise_python(
        _with_column(doc, 0, physicalName="other"), service="service-py", reads=[]
    )
    assert stored["schema"][0]["physicalName"] == "py_daily_kpis"
    assert stored["schema"][0]["properties"][0]["physicalName"] == "other"


# reads


def test_the_reads_list_is_lower_case_sorted_and_unique():
    stored = normalise_python(
        _kpis(),
        service="service-py",
        reads=[
            "core.Orders",
            "Analytics.Daily_Transactions",
            "analytics.daily_transactions",
        ],
    )
    assert stored["schema"][0]["customProperties"] == [
        _custom("reads", ["analytics.daily_transactions", "core.orders"])
    ]


def test_the_order_and_case_of_the_reads_do_not_move_the_hash():
    a = normalise_python(_kpis(), service="service-py", reads=["b.y", "a.x"])
    b = normalise_python(
        _kpis(), service="service-py", reads=["A.X", "b.y", "a.x"]
    )
    assert contract_hash(a) == contract_hash(b)


def test_no_reads_write_no_reads_property():
    stored = normalise_python(_kpis(), service="service-py", reads=[])
    assert "customProperties" not in stored["schema"][0]


def test_the_reads_are_lower_cased_in_ascii_only():
    stored = normalise_python(
        _kpis(),
        service="service-py",
        reads=["Analytics.\u00c4rger_TX", "analytics.\u00c4rger_tx"],
    )
    assert stored["schema"][0]["customProperties"][0]["value"] == [
        "analytics.\u00c4rger_tx",
    ]


@pytest.mark.parametrize(
    "entry",
    [
        "daily_transactions",
        "",
        7,
        ".",
        "a.",
        ".b",
        "a..b",
        " a.b",
        "a.b ",
        "a.b\n",
        "\ta.b",
        "db.analytics.x",
        "a.b.c.d",
        "analytics. x",
        "analytics .x",
        "ana lytics.x",
        "analytics.x\u00a0y",
        "analytics.x\x00",
        "analytics.\x7fx",
    ],
)
def test_a_reads_entry_that_is_not_a_schema_and_a_table_is_refused(entry):
    with pytest.raises(HashInputError, match="reads"):
        normalise_python(
            _kpis(), service="service-py", reads=["analytics.t", entry]
        )


def test_reads_given_as_one_string_is_refused():
    with pytest.raises(HashInputError, match="reads"):
        normalise_python(_kpis(), service="service-py", reads="analytics.t")


@pytest.mark.parametrize("service", ["", " ", "\t\n", "\u00a0", "\u2003"])
def test_an_empty_or_blank_service_is_refused(service):
    with pytest.raises(HashInputError, match="service"):
        normalise_python(_kpis(), service=service, reads=["analytics.t"])


# the input is not changed


@pytest.mark.parametrize("name", FULL_NAMES)
def test_normalise_does_not_change_its_input(name):
    doc = _full(name)
    before = copy.deepcopy(doc)
    stored = normalise_python(
        doc, service="service-py", reads=["analytics.orders"]
    )
    assert doc == before
    stored["customProperties"].clear()
    stored["schema"][0]["properties"][0]["name"] = "changed"
    assert doc == before


# order


def _reordered_full():
    doc = _full("python_node")
    doc["tags"].reverse()
    doc["customProperties"].reverse()
    doc["roles"].append({"role": "finance-reader", "access": "read"})
    doc["roles"].reverse()
    doc["authoritativeDefinitions"].insert(
        0, {"type": "glossary", "url": "https://example.com/glossary"}
    )
    column = doc["schema"][0]["properties"][0]
    column["quality"].insert(0, {"metric": "rowCount", "mustBeGreaterThan": 0})
    column["quality"][1]["customProperties"].reverse()
    column["relationships"].insert(0, {"to": "analytics.archive.order_id"})
    column["tags"] = ["b", "a"]
    return doc


def _sorted_full():
    doc = _full("python_node")
    doc["roles"].append({"role": "finance-reader", "access": "read"})
    doc["authoritativeDefinitions"].append(
        {"type": "glossary", "url": "https://example.com/glossary"}
    )
    column = doc["schema"][0]["properties"][0]
    column["quality"].append({"metric": "rowCount", "mustBeGreaterThan": 0})
    column["relationships"].append({"to": "analytics.archive.order_id"})
    column["tags"] = ["a", "b"]
    return doc


def test_set_like_arrays_sort_but_properties_keep_file_order():
    reordered = _stored(_reordered_full(), "python_node")
    in_order = _stored(_sorted_full(), "python_node")
    assert reordered == in_order
    assert contract_hash(reordered) == contract_hash(in_order)
    for key in ("customProperties", "tags", "roles", "authoritativeDefinitions"):
        items = reordered[key]
        assert items == sorted(items, key=canonical_json), key
    column = reordered["schema"][0]["properties"][0]
    for key in ("quality", "relationships", "tags"):
        assert column[key] == sorted(column[key], key=canonical_json), key

    swapped = _full("python_node")
    columns = swapped["schema"][0]["properties"]
    columns[0], columns[1] = columns[1], columns[0]
    stored = _stored(swapped, "python_node")
    assert [c["name"] for c in stored["schema"][0]["properties"][:2]] == [
        "line_no",
        "order_id",
    ]
    assert contract_hash(stored) != contract_hash(_full_stored("python_node"))


def test_a_free_form_value_keeps_its_content_as_written():
    config = {
        "tags": ["b", "a"],
        "empty": [],
        "nothing": None,
        "blank": "",
        "required": False,
        "description": "  kept  ",
        "customProperties": [{"vendor": "z"}, {"vendor": "a"}],
    }
    doc = _with_property(_kpis(), _custom("config", copy.deepcopy(config)))
    doc = _with_column(doc, 0, examples=["", None, " b ", ["z", "a"]])
    implementation = {"tags": ["y", "x"], "e": {}}
    doc = _with_quality(
        doc, type="custom", engine="soda", implementation=implementation
    )
    del doc["schema"][0]["properties"][0]["quality"][0]["metric"]
    del doc["schema"][0]["properties"][0]["quality"][0]["mustBe"]
    stored = _stored(doc, reads=KPIS_READS)
    values = {p["property"]: p["value"] for p in stored["customProperties"]}
    assert values["config"] == config
    column = stored["schema"][0]["properties"][0]
    assert column["examples"] == ["", None, " b ", ["z", "a"]]
    assert column["quality"][0]["implementation"] == {"tags": ["y", "x"], "e": {}}


def test_a_comparison_value_of_a_quality_rule_is_kept_even_when_empty():
    stored = _stored(_with_quality(_kpis(), mustBe=""), reads=KPIS_READS)
    assert stored["schema"][0]["properties"][0]["quality"][0]["mustBe"] == ""


# descriptions and severity


def test_severity_is_lower_cased_in_ascii_only():
    stored = _stored(
        _with_quality(_kpis(), severity="\u00c9RROR Level"), reads=KPIS_READS
    )
    assert (
        stored["schema"][0]["properties"][0]["quality"][0]["severity"]
        == "\u00c9rror level"
    )


def test_exact_duplicates_in_a_set_like_list_are_stored_once():
    doc = _with_root(_kpis(), tags=["b", "a", "b", "a"])
    doc = _with_property(doc, _custom("term", "x", "atlan"))
    doc = _with_property(doc, _custom("term", "x", "atlan"))
    doc = _with_property(doc, _custom("term", "y", "atlan"))
    doc = _with_column(doc, 0, tags=["x", "x"])
    stored = _stored(doc, reads=KPIS_READS)
    assert stored["tags"] == ["a", "b"]
    assert stored["schema"][0]["properties"][0]["tags"] == ["x"]
    atlan = [p for p in stored["customProperties"] if p["vendor"] == "atlan"]
    assert atlan == [_custom("term", "x", "atlan"), _custom("term", "y", "atlan")]
    once = _with_root(_kpis(), tags=["a", "b"])
    once = _with_property(once, _custom("term", "y", "atlan"))
    once = _with_property(once, _custom("term", "x", "atlan"))
    once = _with_column(once, 0, tags=["x"])
    assert contract_hash(stored) == contract_hash(_stored(once, reads=KPIS_READS))


def test_descriptions_are_stripped_and_severity_lower_cased():
    doc = _kpis()
    doc["description"] = {
        "purpose": "  Daily KPIs.\r\nSecond line. \n",
        "usage": "\tDashboards ",
    }
    doc["schema"][0]["description"] = "\r\n One row per day. "
    doc = _with_column(doc, 0, description=" The day.\r\n")
    doc = _with_quality(doc, severity="ERROR", description=" Never null. ")
    doc["customProperties"].append(
        {
            "vendor": "atlan",
            "property": "term",
            "value": " x ",
            "description": " A term. ",
        }
    )
    stored = _stored(doc, reads=KPIS_READS)
    assert stored["description"] == {
        "purpose": "Daily KPIs.\nSecond line.",
        "usage": "Dashboards",
    }
    assert stored["schema"][0]["description"] == "One row per day."
    column = stored["schema"][0]["properties"][0]
    assert column["description"] == "The day."
    assert column["quality"] == [
        {
            "metric": "nullValues",
            "mustBe": 0,
            "severity": "error",
            "description": "Never null.",
        }
    ]
    assert {
        "vendor": "atlan",
        "property": "term",
        "value": " x ",
        "description": "A term.",
    } in (stored["customProperties"])


def test_a_description_that_differs_only_in_outer_whitespace_hashes_alike():
    a = _kpis()
    b = _kpis()
    b["description"]["purpose"] = "  " + b["description"]["purpose"] + "\r\n"
    assert contract_hash(_stored(a, reads=KPIS_READS)) == contract_hash(
        _stored(b, reads=KPIS_READS)
    )


def test_a_description_that_changes_its_text_moves_the_hash():
    b = _kpis()
    b["description"]["purpose"] = "Something else."
    assert contract_hash(_stored(b, reads=KPIS_READS)) != contract_hash(
        KPIS_STORED
    )


def test_the_full_fixtures_store_and_hash():
    for name in FULL_NAMES:
        stored = _full_stored(name)
        assert stored["version"] == "0" and stored["status"] == "active"
        assert contract_hash(stored).startswith("sha256:")
        for column in stored["schema"][0]["properties"]:
            assert column.get("required", True) is True
            assert "unique" not in column or column["unique"] is True


def test_a_set_like_item_that_canonical_json_cannot_write_is_refused():
    doc = _with_property(_kpis(), _custom("term", float("nan"), "atlan"))
    with pytest.raises(CanonicalJsonError):
        normalise_python(doc, service="service-py", reads=KPIS_READS)
