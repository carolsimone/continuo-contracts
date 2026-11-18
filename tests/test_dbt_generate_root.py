"""The root of a generated dbt contract and its schema object, read from real manifests."""

import copy
import os
import time

from collections.abc import Mapping

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt
from continuo_contracts.errors import DbtNodeError
from continuo_contracts.validate import schema_issues
from continuo_contracts.vocabulary import PROPERTIES, VENDOR, continuo_values

PROJECTS = ("cases", "core", "finance", "marketing", "service_1")

# The root properties in the order a generated contract lists them.
ROOT_ORDER = (
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
)

# Each field of a manifest node that the generator reads, as a dotted path, with another value for
# it of the right shape: a change to any of them moves `source_sha256`. The paths are exactly the
# fields the generator declares, so a field it starts to read is added here too.
READ_FIELDS = (
    ("resource_type", "snapshot"),
    ("name", "governed_2"),
    ("version", 3),
    ("schema", "reporting"),
    ("alias", "governed_table"),
    ("fqn", ["reporting", "governed"]),
    ("original_file_path", "models/elsewhere/governed.sql"),
    ("description", "Governed."),
    ("tags", ["hourly"]),
    ("deprecation_date", "2027-01-01T00:00:00+00:00"),
    ("config.materialized", "view"),
    ("config.unique_key", "id"),
    ("config.incremental_strategy", "merge"),
    ("config.on_schema_change", "fail"),
    ("config.contract.enforced", True),
    ("config.access", "public"),
    ("config.group", "other_team"),
    ("config.freshness.build_after.count", 8),
    ("config.freshness.build_after.period", "day"),
    ("config.grants", {"select": ["reporter"]}),
    ("config.strategy", "check"),
    ("config.updated_at", "updated_at"),
    ("config.check_cols", ["status"]),
    ("config.hard_deletes", "invalidate"),
    ("config.snapshot_meta_column_names", {"dbt_scd_id": "scd_id"}),
    ("config.dbt_valid_to_current", "to_date('9999-12-31')"),
    ("config.quote_columns", False),
    ("config.meta.owner", "finance"),
    ("config.meta.criticality", "REGULATORY"),
    ("config.meta.timeout_minutes", 30),
    ("config.meta.continuo.readers", ["marketing"]),
    ("config.meta.continuo.reads", ["analytics.orders"]),
    ("primary_key", ["id"]),
    ("config.column_types", {"id": "bigint"}),
    ("config.meta.continuo.tests.*.name", "rows"),
    ("config.meta.continuo.tests.*.enabled", False),
    ("config.meta.continuo.tests.*.granularity", "hourly"),
    ("config.meta.continuo.tests.*.params", {"min": 2}),
    ("columns.*.name", "e_mail"),
    ("columns.*.description", "Mail."),
    ("columns.*.data_type", "text"),
    ("columns.*.tags", ["sensitive"]),
    ("columns.*.meta.continuo.classification", "restricted"),
)


def _concrete(path):
    """Return `path` with each `*` written as a key the governed model has: its column `email`, or
    its first table test."""
    return path.replace("columns.*", "columns.email").replace(
        "tests.*", "tests.0"
    )


def _step(value, key):
    if isinstance(value, list) and key.isdigit():
        return value[int(key)]
    return value.get(key) if isinstance(value, dict) else None


def _get_path(node, path):
    value = node
    for key in _concrete(path).split("."):
        value = _step(value, key)
    return value


def _set_path(node, path, value):
    """Set the field at the dotted `path` of `node`, making each mapping on the way."""
    *parents, last = _concrete(path).split(".")
    target = node
    for key in parents:
        if isinstance(target, list):
            target = target[int(key)]
            continue
        if not isinstance(target.get(key), dict | list):
            target[key] = {}
        target = target[key]
    target[last] = value


def _node(project, node_id):
    """Return a copy of the node `node_id` of the fixture manifest `project`."""
    return copy.deepcopy(load_manifest(project)["nodes"][node_id])


def _generate(node, project):
    manifest = load_manifest(project)
    tests = dbt.tests_by_node(manifest).get(node["unique_id"], [])
    return dbt.generate(node, tests, manifest=manifest)


def _generated(project, node_id):
    return _generate(_node(project, node_id), project)


def _doc(project, node_id):
    return _generated(project, node_id).document


def _root(project, node_id):
    return continuo_values(_doc(project, node_id)["customProperties"])


def _kept(project):
    manifest = load_manifest(project)
    service = dbt.project_service(manifest, "")
    return sorted(
        node_id
        for node_id, node in manifest["nodes"].items()
        if node["resource_type"] in dbt.RESOURCE_TYPES
        and dbt.skip_reason(node, project=service) is None
    )


def test_fx_transactions_eur_root_is_the_designs():
    m = load_manifest("finance")
    node = m["nodes"]["model.finance.fx_transactions_eur"]
    doc = dbt.generate(
        node, dbt.tests_by_node(m).get(node["unique_id"], []), manifest=m
    ).document
    assert doc["id"] == "analytics.fx_transactions_eur"
    assert doc["team"] == {"name": "finance"}
    assert continuo_values(doc["customProperties"]) == {
        "service": "finance",
        "kind": "dbt-model",
        "schedule": "daily",
        "criticality": "CORE",
        "materialization": {"materialized": "table"},
        "filePath": "models/fx_transactions_eur.sql",
    }
    assert doc["schema"][0]["physicalType"] == "table"


def test_fx_transactions_eur_envelope_and_schema_object():
    doc = _doc("finance", "model.finance.fx_transactions_eur")
    purpose = "FX transactions with amount restated in EUR via seed_fx_rates_eur."
    assert {
        key: doc[key]
        for key in ("apiVersion", "kind", "name", "version", "status", "tags")
    } == {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "name": "fx_transactions_eur",
        "version": "0",
        "status": "active",
        "tags": ["daily"],
    }
    assert doc["description"] == {"purpose": purpose}
    assert "roles" not in doc
    assert len(doc["schema"]) == 1
    schema = doc["schema"][0]
    assert {
        key: schema[key]
        for key in ("name", "physicalName", "physicalType", "description")
    } == {
        "name": "fx_transactions_eur",
        "physicalName": "fx_transactions_eur",
        "physicalType": "table",
        "description": purpose,
    }
    assert "customProperties" not in schema


def test_every_root_item_is_a_continuo_item_in_the_order_of_the_vocabulary():
    for project in PROJECTS:
        for node_id in _kept(project):
            items = _doc(project, node_id)["customProperties"]
            names = [item["property"] for item in items]
            assert names == [name for name in ROOT_ORDER if name in names]
            for item in items:
                assert set(item) == {"vendor", "property", "value"}
                assert item["vendor"] == VENDOR
                row = PROPERTIES[item["property"]]
                assert (row.sits_on, row.dbt) == ("root", "written")


def test_root_order_is_every_root_property_a_dbt_contract_writes():
    written = {
        row.name
        for row in PROPERTIES.values()
        if row.sits_on == "root" and row.dbt == "written"
    }
    assert set(ROOT_ORDER) == written


def test_a_seed_with_no_tag_has_schedule_seed_and_no_tags():
    doc = _doc("core", "seed.core.seed_users")
    assert continuo_values(doc["customProperties"]) == {
        "service": "core",
        "kind": "dbt-seed",
        "schedule": "seed",
        "criticality": "CORE",
        "materialization": {"materialized": "seed"},
        "filePath": "seeds/seed_users.csv",
    }
    assert "tags" not in doc
    assert "description" not in doc
    assert doc["id"] == "analytics.seed_users"
    assert doc["team"] == {"name": "core"}
    schema = doc["schema"][0]
    assert {
        key: schema[key] for key in ("name", "physicalName", "physicalType")
    } == {
        "name": "seed_users",
        "physicalName": "seed_users",
        "physicalType": "table",
    }
    assert "description" not in schema


def test_a_dormant_model_has_no_schedule():
    doc = _doc("cases", "model.cases.dormant")
    root = continuo_values(doc["customProperties"])
    assert "schedule" not in root
    assert root["kind"] == "dbt-model"
    assert "tags" not in doc
    assert doc["description"] == {"purpose": "A model with an owner and no tag."}


def test_project_tags_come_first():
    # The gates leave `stub` out for its `local_stub` tag, but it is the model that adds a tag of
    # its own to the project tag, and the generator writes the tags in the order dbt lists them.
    doc = _doc("cases", "model.cases.stub")
    assert doc["tags"] == ["project_tag", "local_stub"]
    assert doc["tags"][0] == "project_tag"
    assert continuo_values(doc["customProperties"])["schedule"] == "project_tag"


def test_a_versioned_model_names_its_alias_as_physical_name():
    doc = _doc("cases", "model.cases.customer.v2")
    assert doc["id"] == "analytics.customer.v2"
    assert doc["name"] == "customer.v2"
    assert doc["schema"][0]["name"] == "customer.v2"
    assert doc["schema"][0]["physicalName"] == "customer_v2"


def test_a_node_with_no_alias_has_its_table_as_physical_name():
    node = _node("cases", "model.cases.customer.v1")
    del node["alias"]
    assert _generate(node, "cases").document["schema"][0]["physicalName"] == (
        "customer.v1"
    )


def test_ephemeral_and_view_physical_types():
    physical = {
        node_id: _doc("cases", node_id)["schema"][0]["physicalType"]
        for node_id in (
            "model.cases.eph",
            "model.cases.a_view",
            "model.cases.incr",
            "seed.cases.typed_seed",
            "snapshot.cases.orders_snapshot",
        )
    }
    assert physical == {
        "model.cases.eph": "ephemeral",
        "model.cases.a_view": "view",
        "model.cases.incr": "table",
        "seed.cases.typed_seed": "table",
        "snapshot.cases.orders_snapshot": "table",
    }


def test_materialization_keys_and_on_schema_change_default():
    assert _root("cases", "model.cases.incr")["materialization"] == {
        "materialized": "incremental",
        "uniqueKey": "id",
        "incrementalStrategy": "merge",
        "onSchemaChange": "append_new_columns",
    }
    # `on_schema_change: ignore` is dbt's default, so it is left out.
    node = _node("cases", "model.cases.incr")
    assert node["config"]["on_schema_change"] == "append_new_columns"
    node["config"]["on_schema_change"] = "ignore"
    root = continuo_values(_generate(node, "cases").document["customProperties"])
    assert "onSchemaChange" not in root["materialization"]
    assert _root("finance", "model.finance.fx_transactions_eur")[
        "materialization"
    ] == {"materialized": "table"}


def test_kinds_follow_the_resource_type():
    assert _root("cases", "model.cases.incr")["kind"] == "dbt-model"
    assert _root("cases", "seed.cases.typed_seed")["kind"] == "dbt-seed"
    assert _root("cases", "snapshot.cases.orders_snapshot")["kind"] == (
        "dbt-snapshot"
    )


def test_access_is_left_out_when_protected_and_kept_when_private():
    assert "access" not in _root("cases", "model.cases.default_access")
    assert _root("cases", "model.cases.governed")["access"] == "private"


def test_group_freshness_grants_readers_timeout():
    doc = _doc("cases", "model.cases.governed")
    root = continuo_values(doc["customProperties"])
    assert root["group"] == "finance_team"
    assert root["freshnessBuildAfter"] == {"count": 4, "period": "hour"}
    assert root["timeoutMinutes"] == 90
    assert root["criticality"] == "CORE"
    assert doc["roles"] == [
        {"role": "core", "access": "read"},
        {"role": "reporter", "access": "select"},
        {"role": "analyst", "access": "select"},
    ]


def test_grants_are_read_in_the_order_of_their_privileges_and_take_one_grantee():
    node = _node("cases", "model.cases.governed")
    node["config"]["grants"] = {"select": "reporter", "insert": ["loader"]}
    node["config"]["meta"]["continuo"].pop("readers")
    assert _generate(node, "cases").document["roles"] == [
        {"role": "loader", "access": "insert"},
        {"role": "reporter", "access": "select"},
    ]


def test_an_undeclared_criticality_is_secondary():
    node = _node("cases", "model.cases.incr")
    del node["config"]["meta"]["criticality"]
    root = continuo_values(_generate(node, "cases").document["customProperties"])
    assert root["criticality"] == dbt.DEFAULT_CRITICALITY


@pytest.fixture(
    params=["UTC", "Asia/Tokyo", "America/Los_Angeles", "Pacific/Kiritimati"]
)
def local_zone(request):
    """Run the test with the process's local time zone set to the parameter."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    if before is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = before
    time.tzset()


def test_deprecation_date_is_the_written_date_in_a_zone_east_of_utc(local_zone):
    node = load_manifest("cases")["nodes"]["model.cases.governed"]
    assert node["deprecation_date"] == "2026-12-01T00:00:00+09:00"
    assert (
        continuo_values(
            _doc("cases", "model.cases.governed")["customProperties"]
        )["deprecationDate"]
        == "2026-12-01"
    )


def test_a_deprecation_date_with_no_zone_keeps_its_date(local_zone):
    node = _node("cases", "model.cases.governed")
    node["deprecation_date"] = "2026-12-01T23:30:00"
    root = continuo_values(_generate(node, "cases").document["customProperties"])
    assert root["deprecationDate"] == "2026-12-01"


def test_model_only_properties_are_left_out_of_a_seed_and_a_snapshot():
    for resource_type in ("seed", "snapshot"):
        node = _node("cases", "model.cases.governed")
        node["resource_type"] = resource_type
        root = continuo_values(
            _generate(node, "cases").document["customProperties"]
        )
        assert root["kind"] == f"dbt-{resource_type}"
        assert root["group"] == "finance_team"
        for name in ("access", "deprecationDate", "freshnessBuildAfter"):
            assert name not in root


def test_a_model_with_no_build_after_has_no_freshness():
    node = _node("cases", "model.cases.governed")
    node["config"]["freshness"] = {"build_after": None}
    root = continuo_values(_generate(node, "cases").document["customProperties"])
    assert "freshnessBuildAfter" not in root


def test_snapshot_settings_quote_columns_and_an_enforced_contract():
    assert _root("cases", "snapshot.cases.orders_snapshot")["snapshot"] == {
        "strategy": "timestamp",
        "uniqueKey": "id",
        "updatedAt": "updated_at",
        "hardDeletes": "new_record",
        "metaColumnNames": {"dbt_valid_from": "valid_from"},
    }
    assert _root("cases", "seed.cases.typed_seed")["quoteColumns"] is True
    assert "quoteColumns" not in _root("cases", "seed.cases.untyped_seed")
    assert _root("cases", "model.cases.enforced")["contractEnforced"] is True
    assert "contractEnforced" not in _root("cases", "model.cases.incr")


def test_every_snapshot_setting_is_written_under_its_name():
    node = _node("cases", "snapshot.cases.orders_snapshot")
    node["config"].update(
        strategy="check",
        updated_at=None,
        check_cols=["status"],
        dbt_valid_to_current="to_date('9999-12-31')",
        snapshot_meta_column_names=None,
    )
    assert continuo_values(_generate(node, "cases").document["customProperties"])[
        "snapshot"
    ] == {
        "strategy": "check",
        "uniqueKey": "id",
        "checkCols": ["status"],
        "hardDeletes": "new_record",
        "validToCurrent": "to_date('9999-12-31')",
    }


def test_snapshot_settings_and_quote_columns_stay_on_their_own_resource_type():
    node = _node("cases", "seed.cases.typed_seed")
    node["resource_type"] = "model"
    node["config"]["strategy"] = "timestamp"
    root = continuo_values(_generate(node, "cases").document["customProperties"])
    assert "quoteColumns" not in root
    assert "snapshot" not in root


def test_declared_reads_are_on_the_schema_object():
    schema = _doc("cases", "model.cases.governed")["schema"][0]
    assert schema["customProperties"] == [
        {"vendor": VENDOR, "property": "reads", "value": ["analytics.raw_events"]}
    ]
    assert PROPERTIES["reads"].sits_on == "schema"
    assert (
        "customProperties" not in _doc("cases", "model.cases.incr")["schema"][0]
    )


def test_descriptions_are_trimmed():
    node = _node("cases", "model.cases.dormant")
    node["description"] = "  Two lines.\r\nThe second.\n "
    doc = _generate(node, "cases").document
    assert doc["description"] == {"purpose": "Two lines.\nThe second."}
    assert doc["schema"][0]["description"] == "Two lines.\nThe second."


def test_a_generated_contract_has_no_flags_for_its_root():
    generated = _generated("cases", "model.cases.governed")
    assert generated.flags == ()
    assert dbt.FLAGS == (
        "jinja_in_test_arguments",
        "test_column_not_declared",
        "primary_key_column_not_declared",
    )


def test_every_kept_node_of_every_manifest_passes_the_odcs_schema():
    for project in PROJECTS:
        for node_id in _kept(project):
            assert schema_issues(_doc(project, node_id)) == [], (project, node_id)


def test_source_sha256_ignores_created_at_and_compiled_code():
    node = _node("finance", "model.finance.fx_transactions_eur")
    before = _generate(node, "finance").source_sha256
    assert len(before) == 64
    assert set(before) <= set("0123456789abcdef")
    node["created_at"] = 1767225600.0
    node["compiled_code"] = "select 1"
    node["raw_code"] = "select 2"
    node["patch_path"] = "finance://models/elsewhere.yml"
    assert _generate(node, "finance").source_sha256 == before


def test_read_fields_are_the_fields_the_generator_declares():
    assert sorted(path for path, _ in READ_FIELDS) == sorted(dbt._READ_PATHS)


@pytest.mark.parametrize(("field", "value"), READ_FIELDS)
def test_source_sha256_moves_with_each_field_it_reads(field, value):
    node = _node("cases", "model.cases.governed")
    before = _generate(node, "cases").source_sha256
    assert _get_path(node, field) != value
    _set_path(node, field, value)
    assert _generate(node, "cases").source_sha256 != before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config.persist_docs", {"relation": True}),
        ("config.post-hook", [{"sql": "grant select on {{ this }} to x"}]),
        ("config.contract.alias_types", False),
        ("config.freshness.build_after.updates_on", "all"),
        ("config.meta.unrelated", "a note"),
        ("config.meta.ticket", 2**70),
        ("config.meta.continuo.entity", "Transaction"),
        ("checksum", {"name": "sha256", "checksum": "0" * 64}),
        ("unrendered_config", {"materialized": "view"}),
    ],
)
def test_a_field_the_generator_does_not_read_moves_nothing(field, value):
    node = _node("cases", "model.cases.governed")
    before = _generate(node, "cases")
    _set_path(node, field, value)
    after = _generate(node, "cases")
    assert after.source_sha256 == before.source_sha256
    assert after.document == before.document


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "config.meta.timeout_minutes",
            2**70,
            "config.meta.timeout_minutes: integer is outside -(2**53 - 1) to 2**53 - 1",
        ),
        (
            "config.meta.timeout_minutes",
            float("nan"),
            "config.meta.timeout_minutes: NaN and the infinities are not JSON numbers",
        ),
        (
            "config.unique_key",
            ["id", 2**70],
            "config.unique_key[1]: integer is outside -(2**53 - 1) to 2**53 - 1",
        ),
    ],
)
def test_a_read_value_canonical_json_cannot_write_raises_dbt_node_error(
    field, value, message
):
    node = _node("cases", "model.cases.governed")
    _set_path(node, field, value)
    with pytest.raises(DbtNodeError) as raised:
        _generate(node, "cases")
    assert str(raised.value) == f"model.cases.governed: {message}"


class _Recording(Mapping):
    """A read-only view of a mapping that records the path of every key read from it.

    A path is the tuple of the keys from the top, so a key with a dot in it, such as a node id, is
    one step. A value that is a mapping comes back as a `_Recording` too, so reads at any depth
    count. A list comes back as it is: the generator checks that a list is a `list`, so the reads
    inside one are not recorded.
    """

    def __init__(self, data, seen, path=()):
        self._data = data
        self._seen = seen
        self._path = path

    def __getitem__(self, key):
        path = (*self._path, key)
        self._seen.add(path)
        value = self._data[key]
        if isinstance(value, Mapping):
            return _Recording(value, self._seen, path)
        return value

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _pattern(field):
    """Return the declared dotted `field` as a tuple of keys, where `*` stands for any one key."""
    return tuple(field.split("."))


def _matches(path, pattern):
    return len(path) == len(pattern) and all(
        part in ("*", key) for key, part in zip(path, pattern, strict=True)
    )


def _undeclared(seen, declared):
    """Return, as dotted strings, the paths of `seen` that are neither a declared field, a field
    below one, nor a mapping on the way to one. `unique_id` is read only to name a node in an
    error."""
    patterns = [_pattern(field) for field in (*declared, "unique_id")]

    def inside(path):
        return any(
            _matches(path[: len(pattern)], pattern[: len(path)])
            for pattern in patterns
        )

    return sorted(".".join(path) for path in seen if not inside(path))


# The declared fields that sit inside a list, which `_Recording` cannot see into: the reverse
# check looks for a read of the list itself.
LISTS = (("config", "meta", "continuo", "tests"),)


def _visible(field):
    """Return the part of the declared `field` that a recording view can see read."""
    pattern = _pattern(field)
    for prefix in LISTS:
        if pattern[: len(prefix)] == prefix:
            return prefix
    return pattern


def _reads_of(project, node_id):
    """Run `generate` on recording views of a node, its tests and its manifest, and return what it
    read of each."""
    manifest = load_manifest(project)
    node_seen, test_seen, manifest_seen = set(), set(), set()
    node = _Recording(manifest["nodes"][node_id], node_seen)
    tests = [
        _Recording(test, test_seen)
        for test in dbt.tests_by_node(manifest).get(node_id, [])
    ]
    generated = dbt.generate(
        node, tests, manifest=_Recording(manifest, manifest_seen)
    )
    assert generated == _generated(project, node_id)
    return node_seen, test_seen, manifest_seen


GUARDED_NODES = sorted(
    {
        ("finance", "model.finance.fx_transactions_eur"),
        ("core", "seed.core.seed_users"),
        ("cases", "model.cases.governed"),
        ("cases", "model.cases.incr"),
        ("cases", "model.cases.enforced"),
        ("cases", "model.cases.generic_tests"),
        ("cases", "model.cases.singular_target"),
        ("cases", "seed.cases.typed_seed"),
        ("cases", "snapshot.cases.orders_snapshot"),
        *(
            (project, node_id)
            for project in PROJECTS
            for node_id in _kept(project)
        ),
    }
)


@pytest.mark.parametrize(("project", "node_id"), GUARDED_NODES)
def test_the_generator_reads_only_the_fields_it_declares(project, node_id):
    node_seen, test_seen, manifest_seen = _reads_of(project, node_id)
    assert _undeclared(node_seen, dbt._READ_PATHS) == []
    assert _undeclared(test_seen, dbt._TEST_FIELDS) == []
    assert _undeclared(manifest_seen, dbt._MANIFEST_PATHS) == []
    unread_kwargs = {
        ("test_metadata", "kwargs", key) for key in dbt._UNREAD_KWARGS
    }
    assert test_seen & unread_kwargs == set()


def test_every_declared_field_is_read_for_some_fixture_node(monkeypatch):
    # With the hash stubbed out, only the reads that build the document are recorded, so a
    # declared field that nothing reads any more shows up here.
    monkeypatch.setattr(dbt, "_source_sha256", lambda *args: "")
    seen = {"node": set(), "test": set(), "manifest": set()}
    for project, node_id in GUARDED_NODES:
        for place, paths in zip(seen, _reads_of(project, node_id), strict=True):
            seen[place] |= paths
    for place, declared in (
        ("node", dbt._READ_PATHS),
        ("test", dbt._TEST_FIELDS),
        ("manifest", dbt._MANIFEST_PATHS),
    ):
        unread = [
            field
            for field in declared
            if not any(_matches(path, _visible(field)) for path in seen[place])
        ]
        assert unread == [], place


def test_the_read_guard_finds_a_field_that_is_not_declared():
    _, test_seen, _ = _reads_of("cases", "model.cases.generic_tests")
    declared = [
        path for path in dbt._READ_PATHS if path != "config.meta.timeout_minutes"
    ]
    node_seen, _, _ = _reads_of("cases", "model.cases.governed")
    assert _undeclared(node_seen, declared) == ["config.meta.timeout_minutes"]
    assert ("config", "meta", "ticket") not in node_seen
    without_where = [path for path in dbt._TEST_FIELDS if path != "config.where"]
    assert "config.where" in _undeclared(test_seen, without_where)
    without_columns = [
        path for path in dbt._READ_PATHS if not path.startswith("columns.")
    ]
    assert "columns.id.name" in _undeclared(node_seen, without_columns)


def test_source_sha256_does_not_depend_on_key_order():
    node = _node("cases", "model.cases.governed")
    reordered = dict(reversed(list(node.items())))
    reordered["config"] = dict(reversed(list(node["config"].items())))
    assert (
        _generate(reordered, "cases").source_sha256
        == _generate(node, "cases").source_sha256
    )


def test_the_document_does_not_depend_on_key_order():
    node = _node("cases", "model.cases.governed")
    reordered = dict(reversed(list(node.items())))
    reordered["config"] = dict(reversed(list(node["config"].items())))
    reordered["config"]["grants"] = dict(
        reversed(list(node["config"]["grants"].items()))
    )
    assert (
        _generate(reordered, "cases").document
        == _generate(node, "cases").document
    )


def test_a_meta_that_is_not_a_mapping_raises_dbt_node_error():
    node = _node("finance", "model.finance.fx_transactions_eur")
    node["config"]["meta"] = ["owner", "finance"]
    with pytest.raises(DbtNodeError) as raised:
        _generate(node, "finance")
    assert str(raised.value) == (
        "model.finance.fx_transactions_eur: config.meta must be a mapping, got list"
    )


def _set(path, value):
    """Return a change to a node that sets the key at `path`, a tuple of keys, to `value`."""

    def change(node):
        target = node
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set(("config",), "table"), "config must be a mapping, got str"),
        (
            _set(("config", "meta", "owner"), ["finance"]),
            "config.meta.owner must be a non-empty string, got list",
        ),
        (
            _set(("config", "meta", "owner"), ""),
            "config.meta.owner must be a non-empty string, got an empty string",
        ),
        (
            _set(("resource_type",), "analysis"),
            "resource_type must be one of model, seed, snapshot, got str",
        ),
        (
            _set(("original_file_path",), None),
            "original_file_path must be a non-empty string, got NoneType",
        ),
        (
            _set(("config", "materialized"), ""),
            "config.materialized must be a non-empty string, got an empty string",
        ),
        (
            _set(("config", "contract"), True),
            "config.contract must be a mapping, got bool",
        ),
        (_set(("alias",), 7), "alias must be a string, got int"),
        (_set(("description",), 7), "description must be a string, got int"),
        (
            _set(("config", "meta", "continuo"), "core"),
            "config.meta.continuo must be a mapping, got str",
        ),
        (
            _set(("config", "meta", "continuo", "readers"), "core"),
            "config.meta.continuo.readers must be a list of strings, got str",
        ),
        (
            _set(("config", "meta", "continuo", "readers"), ["core", 1]),
            "config.meta.continuo.readers[1] must be a string, got int",
        ),
        (
            _set(("config", "meta", "continuo", "reads"), {"a": "b"}),
            "config.meta.continuo.reads must be a list of strings, got dict",
        ),
        (
            _set(("config", "grants"), ["select"]),
            "config.grants must be a mapping, got list",
        ),
        (
            _set(("config", "grants", "select"), 3),
            "config.grants.select must be a string or a list of strings, got int",
        ),
        (
            _set(("config", "grants", "select"), ["reporter", None]),
            "config.grants.select[1] must be a string, got NoneType",
        ),
        (
            _set(("config", "freshness"), "4h"),
            "config.freshness must be a mapping, got str",
        ),
        (
            _set(("config", "freshness", "build_after"), 4),
            "config.freshness.build_after must be a mapping, got int",
        ),
        (_set(("tags",), "daily"), "tags must be a list of strings, got str"),
        (_set(("tags",), ["daily", 1]), "tags[1] must be a string, got int"),
        (_set(("fqn",), "cases"), "fqn must be a list, got str"),
        (_set(("fqn",), [1]), "fqn[0] must be a string, got int"),
        (
            _set(("version",), [2]),
            "version must be a string or a number, got list",
        ),
        (
            _set(("deprecation_date",), 20261201),
            "deprecation_date must be an ISO 8601 date and time, got int",
        ),
        (
            _set(("deprecation_date",), "next year"),
            "deprecation_date must be an ISO 8601 date and time, got str",
        ),
    ],
)
def test_a_field_of_the_wrong_shape_raises_dbt_node_error(change, message):
    node = _node("cases", "model.cases.governed")
    change(node)
    with pytest.raises(DbtNodeError) as raised:
        _generate(node, "cases")
    assert str(raised.value) == f"model.cases.governed: {message}"


def test_a_node_with_no_fqn_has_no_service():
    for fqn in (None, []):
        node = _node("cases", "model.cases.governed")
        node["fqn"] = fqn
        root = continuo_values(
            _generate(node, "cases").document["customProperties"]
        )
        assert "service" not in root


def test_a_node_with_no_id_and_no_name_is_named_unknown():
    node = _node("cases", "model.cases.governed")
    node["unique_id"] = None
    node["name"] = ""
    with pytest.raises(DbtNodeError) as raised:
        dbt.generate(node, [], manifest=load_manifest("cases"))
    assert str(raised.value) == (
        "<unknown node>: name must be a non-empty string, got an empty string"
    )


def test_snapshot_meta_column_names_that_are_not_a_mapping_raise_dbt_node_error():
    node = _node("cases", "snapshot.cases.orders_snapshot")
    node["config"]["snapshot_meta_column_names"] = ["valid_from"]
    with pytest.raises(DbtNodeError) as raised:
        _generate(node, "cases")
    assert str(raised.value) == (
        "snapshot.cases.orders_snapshot: config.snapshot_meta_column_names must be "
        "a mapping, got list"
    )
