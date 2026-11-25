"""The golden dbt contracts, `contract.normalise_dbt` and `dbt.run_facts`."""

import copy
import importlib.util
import itertools
import json

from pathlib import Path

import pytest

from dbt_fixtures import load_manifest, source_sha256_of

from continuo_contracts import dbt
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import contract_hash, normalise_dbt
from continuo_contracts.errors import CanonicalJsonError, HashInputError
from continuo_contracts.validate import validate_dbt_contract
from continuo_contracts.vocabulary import VENDOR, continuo_values

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "dbt" / "golden"
SCRIPT = ROOT / "scripts" / "write_dbt_goldens.py"
spec = importlib.util.spec_from_file_location("write_dbt_goldens", SCRIPT)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)

GOLDEN_FILES = sorted(GOLDEN.rglob("*.json"))
PROJECTS = ("cases", "core", "finance", "marketing", "service_1")


def _golden(project, name):
    return json.loads((GOLDEN / project / f"{name}.json").read_text("ascii"))


def _kept(manifest):
    return writer.kept(manifest)


def _node_of(path):
    """Return the manifest and the manifest key of the node whose golden file is `path`."""
    manifest = load_manifest(path.parent.name)
    (key,) = [
        key
        for key in _kept(manifest)
        if dbt.unique_id(manifest["nodes"][key]) == path.stem
    ]
    return manifest, key


def _stored(document, reads=()):
    return normalise_dbt(document, reads=list(reads))


def _reads_of(stored):
    return continuo_values(stored["schema"][0].get("customProperties")).get(
        "reads"
    )


# The writer and the files.


def test_the_writer_reads_every_fixture_manifest():
    assert tuple(writer.projects()) == PROJECTS


def test_the_bound_projects_have_a_manifest_and_no_golden_file():
    assert writer.UNGOLDEN == ("core_bound", "finance_bound")
    for project in writer.UNGOLDEN:
        assert (writer.MANIFESTS / f"{project}.json.gz").is_file()
        assert not (GOLDEN / project).exists()


def test_every_kept_node_has_a_golden_file_and_no_other_file_is_there():
    assert sorted(writer.golden_texts()) == GOLDEN_FILES
    assert {path.parent.name for path in GOLDEN_FILES} == set(PROJECTS)


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=str)
def test_every_golden_contract_is_generated_again_byte_for_byte(path):
    manifest, key = _node_of(path)
    text = json.dumps(
        writer.golden(manifest, key), sort_keys=True, indent=1, ensure_ascii=True
    )
    assert path.read_bytes() == (text + "\n").encode("ascii")


def test_the_writer_writes_the_same_bytes_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "GOLDEN", tmp_path / "golden")
    monkeypatch.setattr(writer, "VECTOR", tmp_path / "dbt_contracts.json")
    stale = tmp_path / "golden" / "cases" / "analytics.gone.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n")
    assert writer.main() == 0
    assert not stale.exists()
    first = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*.json"))}
    assert writer.main() == 0
    second = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*.json"))}
    assert first == second
    assert len(first) == len(GOLDEN_FILES) + 1
    vector = first.pop(tmp_path / "dbt_contracts.json")
    assert (
        vector
        == (
            ROOT / "continuo_contracts" / "vectors" / "dbt_contracts.json"
        ).read_bytes()
    )
    for path, data in first.items():
        assert (
            data == (GOLDEN / path.relative_to(tmp_path / "golden")).read_bytes()
        ), path


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=str)
def test_every_golden_file_is_ascii_sorted_json(path):
    data = path.read_bytes()
    assert all(b == 10 or 32 <= b < 127 for b in data)
    value = json.loads(data)
    assert set(value) == {
        "generated",
        "flags",
        "source_sha256",
        "stored",
        "contract_hash",
    }
    assert data.decode("ascii") == (
        json.dumps(value, sort_keys=True, indent=1, ensure_ascii=True) + "\n"
    )


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=str)
def test_the_source_sha256_of_every_golden_is_the_hash_of_its_read_fields(path):
    golden = json.loads(path.read_text("ascii"))
    manifest, key = _node_of(path)
    tests = dbt.tests_by_node(manifest).get(key, [])
    assert golden["source_sha256"] == source_sha256_of(
        manifest["nodes"][key], tests, manifest
    )


def test_the_golden_fx_transactions_eur_and_seed_users_exist():
    assert (GOLDEN / "finance" / "analytics.fx_transactions_eur.json").is_file()
    assert (GOLDEN / "core" / "analytics.seed_users.json").is_file()


# The stored form.


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=str)
def test_the_stored_form_is_stored_again_unchanged_and_hashes_as_pinned(path):
    golden = json.loads(path.read_text("ascii"))
    assert _stored(golden["stored"]) == golden["stored"]
    assert _stored(golden["generated"]) == golden["stored"]
    assert contract_hash(golden["stored"]) == golden["contract_hash"]


def test_generation_does_not_depend_on_manifest_order():
    m = load_manifest("cases")
    reversed_m = {**m, "nodes": dict(reversed(list(m["nodes"].items())))}
    for node_id in _kept(m):
        a = normalise_dbt(
            dbt.generate(
                m["nodes"][node_id],
                dbt.tests_by_node(m).get(node_id, []),
                manifest=m,
            ).document,
            reads=[],
        )
        b = normalise_dbt(
            dbt.generate(
                reversed_m["nodes"][node_id],
                dbt.tests_by_node(reversed_m).get(node_id, []),
                manifest=reversed_m,
            ).document,
            reads=[],
        )
        assert canonical_json(a) == canonical_json(b)


def _reversed_keys(value):
    """Return `value` with the keys of every mapping in reverse order."""
    if isinstance(value, dict):
        return {key: _reversed_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed_keys(item) for item in value]
    return value


def test_the_stored_form_does_not_depend_on_the_order_of_a_mapping():
    for path in GOLDEN_FILES:
        golden = json.loads(path.read_text("ascii"))
        reordered = _stored(_reversed_keys(golden["generated"]))
        assert canonical_json(reordered) == canonical_json(golden["stored"]), path


def test_normalise_dbt_does_not_change_its_input():
    doc = _golden("cases", "analytics.governed")["generated"]
    before = copy.deepcopy(doc)
    stored = _stored(doc, ["Raw.Events"])
    assert doc == before
    assert stored["schema"][0] is not doc["schema"][0]


def test_sql_reads_and_declared_reads_join_once_in_lower_case():
    doc = _golden("cases", "analytics.governed")["generated"]
    assert continuo_values(doc["schema"][0]["customProperties"]) == {
        "reads": ["analytics.raw_events"]
    }
    reads = [
        "Raw.Events",
        "analytics.RAW_EVENTS",
        "raw.events",
        "Analytics.Orders",
    ]
    expected = ["analytics.orders", "analytics.raw_events", "raw.events"]
    stored = _stored(doc, reads)
    assert _reads_of(stored) == expected
    (item,) = stored["schema"][0]["customProperties"]
    assert item == {"vendor": VENDOR, "property": "reads", "value": expected}
    for order in itertools.permutations(reads):
        assert _stored(doc, [*order, *order]) == stored


def test_sql_reads_alone_are_written_on_a_node_that_declares_none():
    doc = _golden("cases", "analytics.enforced")["generated"]
    assert "customProperties" not in doc["schema"][0]
    assert _reads_of(_stored(doc, ["Raw.Events"])) == ["raw.events"]
    assert "customProperties" not in _stored(doc)["schema"][0]


def test_the_declared_reads_alone_are_written_lower_case_and_sorted():
    doc = copy.deepcopy(_golden("cases", "analytics.governed")["generated"])
    doc["schema"][0]["customProperties"][0]["value"] = ["Z.b", "a.B", "z.B"]
    assert _reads_of(_stored(doc)) == ["a.b", "z.b"]


def test_another_vendors_property_on_the_schema_object_is_kept():
    doc = copy.deepcopy(_golden("cases", "analytics.governed")["generated"])
    other = {"vendor": "atlan", "property": "reads", "value": ["x"]}
    doc["schema"][0]["customProperties"].append(other)
    custom = _stored(doc, ["raw.events"])["schema"][0]["customProperties"]
    assert other in custom
    assert continuo_values(custom) == {
        "reads": ["analytics.raw_events", "raw.events"]
    }


@pytest.mark.parametrize(
    "reads",
    [
        ["orders"],
        ["analytics.orders.v2"],
        ["analytics."],
        [".orders"],
        ["analytics. orders"],
        ["analytics.or\x00ders"],
        [7],
        "analytics.orders",
    ],
    ids=repr,
)
def test_a_read_that_is_not_two_parts_raises_hash_input_error(reads):
    doc = _golden("cases", "analytics.governed")["generated"]
    with pytest.raises(HashInputError, match="reads"):
        normalise_dbt(doc, reads=reads)


def test_a_declared_read_that_is_not_two_parts_raises_hash_input_error():
    doc = copy.deepcopy(_golden("cases", "analytics.governed")["generated"])
    doc["schema"][0]["customProperties"][0]["value"] = ["raw_events"]
    with pytest.raises(HashInputError, match="raw_events"):
        normalise_dbt(doc, reads=[])


def _with_key(doc, names):
    doc = copy.deepcopy(doc)
    for position, column in enumerate(doc["schema"][0]["properties"], start=1):
        if column["name"] in names:
            column["primaryKey"] = True
            column["primaryKeyPosition"] = position
    return doc


def test_a_single_key_column_has_no_position():
    doc = _golden("cases", "analytics.enforced")["generated"]
    stored = _stored(_with_key(doc, {"id"}))
    (key,) = [c for c in stored["schema"][0]["properties"] if c.get("primaryKey")]
    assert key == {
        "name": "id",
        "physicalType": "integer",
        "logicalType": "integer",
        "primaryKey": True,
    }


def test_the_columns_of_a_key_of_two_keep_their_positions():
    doc = _golden("cases", "analytics.enforced")["generated"]
    stored = _stored(_with_key(doc, {"id", "created_at"}))
    keys = [c for c in stored["schema"][0]["properties"] if c.get("primaryKey")]
    assert [(c["name"], c["primaryKeyPosition"]) for c in keys] == [
        ("id", 1),
        ("created_at", 3),
    ]


def test_the_stored_form_sorts_the_root_items_and_drops_empty_values():
    doc = copy.deepcopy(_golden("cases", "analytics.governed")["generated"])
    doc["description"] = {"purpose": "  "}
    doc["tags"] = ["z", "a", "z"]
    stored = _stored(doc)
    assert "description" not in stored
    assert stored["tags"] == ["a", "z"]
    items = stored["customProperties"]
    assert items == sorted(items, key=canonical_json)
    assert items != doc["customProperties"]


# The run facts.


def _expected_facts(node):
    config = node["config"]
    meta = config["meta"]
    criticality = meta.get("criticality") or dbt.DEFAULT_CRITICALITY
    return dbt.RunFacts(
        unique_id=dbt.unique_id(node),
        schema=node["schema"].lower(),
        table=dbt.table_name(node),
        physical_name=node.get("alias") or dbt.table_name(node),
        project=dbt.node_project(node),
        kind=f"dbt-{node['resource_type']}",
        schedule=dbt.schedule(node),
        criticality=criticality,
        timeout_minutes=meta.get("timeout_minutes"),
        file_path=node["original_file_path"],
        owner=meta["owner"],
        materialized=config["materialized"],
    )


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=str)
def test_run_facts_read_back_what_the_manifest_node_says(path):
    golden = json.loads(path.read_text("ascii"))
    manifest, key = _node_of(path)
    facts = dbt.run_facts(golden["generated"])
    assert dbt.run_facts(golden["stored"]) == facts
    assert facts == _expected_facts(manifest["nodes"][key])


def test_the_run_facts_of_the_goldens_cover_every_kind_of_value():
    facts = [
        dbt.run_facts(json.loads(path.read_text("ascii"))["stored"])
        for path in GOLDEN_FILES
    ]
    assert {f.kind for f in facts} == {"dbt-model", "dbt-seed", "dbt-snapshot"}
    assert "" in {f.schedule for f in facts}
    assert {None, 90} <= {f.timeout_minutes for f in facts}
    assert {"CORE", "high"} <= {f.criticality for f in facts}
    assert any(f.physical_name != f.table for f in facts)


def test_the_run_facts_of_a_versioned_model():
    facts = dbt.run_facts(_golden("cases", "analytics.customer.v2")["stored"])
    assert facts == dbt.RunFacts(
        unique_id="analytics.customer.v2",
        schema="analytics",
        table="customer.v2",
        physical_name="customer_v2",
        project="cases",
        kind="dbt-model",
        schedule="project_tag",
        criticality="CORE",
        timeout_minutes=None,
        file_path="models/tagged/customer_v2.sql",
        owner="cases",
        materialized="table",
    )


def test_the_run_facts_of_a_dormant_node_have_the_empty_schedule():
    doc = _golden("cases", "analytics.dormant")["stored"]
    assert "schedule" not in continuo_values(doc["customProperties"])
    assert dbt.run_facts(doc).schedule == ""


def test_the_schema_of_the_run_facts_is_the_id_without_the_table():
    doc = copy.deepcopy(_golden("cases", "analytics.enforced")["stored"])
    doc["id"] = "mart_eu.enforced"
    facts = dbt.run_facts(doc)
    assert (facts.schema, facts.table) == ("mart_eu", "enforced")


def test_a_table_whose_lower_case_depends_on_its_place_in_the_id():
    # `unique_id` lowers `<schema>.<table>` as one string, so a final capital sigma becomes the
    # final form, and the check and the run facts read the id the same way.
    manifest = copy.deepcopy(load_manifest("cases"))
    node = manifest["nodes"]["model.cases.enforced"]
    node["name"] = "\u03a3"
    node["alias"] = None
    generated = dbt.generate(node, [], manifest=manifest)
    assert generated.document["id"] == "analytics.\u03c2"
    assert validate_dbt_contract(generated.document) == []
    stored = _stored(generated.document)
    assert validate_dbt_contract(stored) == []
    facts = dbt.run_facts(generated.document)
    assert dbt.run_facts(stored) == facts
    assert (facts.unique_id, facts.schema, facts.table) == (
        "analytics.\u03c2",
        "analytics",
        "\u03a3",
    )


def test_a_schema_object_with_no_physical_name_runs_on_its_table():
    doc = copy.deepcopy(_golden("cases", "analytics.enforced")["stored"])
    del doc["schema"][0]["physicalName"]
    assert dbt.run_facts(doc).physical_name == "enforced"


def test_a_contract_with_no_criticality_runs_as_secondary():
    doc = copy.deepcopy(_golden("cases", "analytics.enforced")["stored"])
    doc["customProperties"] = [
        item
        for item in doc["customProperties"]
        if item["property"] != "criticality"
    ]
    assert (
        dbt.run_facts(doc).criticality == dbt.DEFAULT_CRITICALITY == "SECONDARY"
    )


def test_a_stored_contract_with_a_number_canonical_json_cannot_write_is_refused():
    doc = copy.deepcopy(_golden("cases", "analytics.generic_tests")["stored"])
    rule = doc["schema"][0]["properties"][0]["quality"][0]
    rule["mustBe"] = 2**60
    assert validate_dbt_contract(doc) == []
    with pytest.raises(CanonicalJsonError, match="mustBe"):
        normalise_dbt(doc, reads=[])
