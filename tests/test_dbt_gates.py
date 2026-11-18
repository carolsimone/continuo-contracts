"""The dbt node gates, the identity of a node and the join of a test to its node."""

import copy
import json
import re

from pathlib import Path

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt
from continuo_contracts.errors import ContractsError, DbtNodeError

GATES = Path(__file__).resolve().parent / "fixtures" / "dbt" / "todays_gates.json"
PROJECTS = ("cases", "core", "finance", "marketing", "service_1")


def _node(**fields):
    """A model of the project `shop` that no gate leaves out, with `fields` changed."""
    node = {
        "unique_id": "model.shop.orders",
        "resource_type": "model",
        "name": "orders",
        "schema": "analytics",
        "fqn": ["shop", "orders"],
        "tags": ["daily"],
        "config": {"meta": {"owner": "data-team"}},
    }
    node.update(fields)
    return node


def test_the_names_have_the_values_continuo_uses():
    assert dbt.RESOURCE_TYPES == ("model", "seed", "snapshot")
    assert dbt.SKIP_REASONS == ("no_owner", "local_stub", "installed_package")
    assert dbt.DEFAULT_CRITICALITY == "SECONDARY"
    assert dbt.SEED_SCHEDULE == "seed"


def test_a_dbt_node_error_is_a_contracts_error():
    assert issubclass(DbtNodeError, ContractsError)


@pytest.mark.parametrize(
    ("node_id", "reason"),
    [
        ("model.cases.no_owner", "no_owner"),
        ("model.cases.stub", "local_stub"),
        ("model.cases_pkg.pkg_untagged", "installed_package"),
        ("model.cases_pkg.pkg_tagged", None),
        ("model.cases.dormant", None),
        ("seed.cases.untyped_seed", None),
    ],
)
def test_skip_reason_names_each_gate(node_id, reason):
    m = load_manifest("cases")
    node = m["nodes"][node_id]
    assert dbt.skip_reason(node, project=dbt.project_service(m, "")) == reason


def test_the_recorded_gates_cover_every_gate_and_the_nodes_that_pass():
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    recorded = [r for nodes in gates["projects"].values() for r in nodes.values()]

    assert re.fullmatch(r"[0-9a-f]{40}", gates["continuo_commit"])
    assert sorted(gates["projects"]) == sorted(PROJECTS)
    assert {r for r in recorded if r} == set(dbt.SKIP_REASONS)
    assert recorded.count(None) > len(recorded) // 2


@pytest.mark.parametrize("project", PROJECTS)
def test_skip_reason_matches_todays_gates_on_every_fixture_node(project):
    # todays_gates.json holds what continuo's parse_manifest decided for each model, seed and
    # snapshot of each fixture manifest: a skip reason, or null for a node it keeps.
    recorded = json.loads(GATES.read_text(encoding="utf-8"))["projects"][project]
    m = load_manifest(project)
    service = dbt.project_service(m, "")
    ours = {
        node_id: dbt.skip_reason(node, project=service)
        for node_id, node in m["nodes"].items()
        if node["resource_type"] in dbt.RESOURCE_TYPES
    }

    assert ours == recorded


def test_no_project_skips_nothing_as_installed_package():
    m = load_manifest("cases")
    node = m["nodes"]["model.cases_pkg.pkg_untagged"]

    assert dbt.skip_reason(node, project="") is None
    assert dbt.skip_reason(node, project="cases") == "installed_package"
    assert dbt.skip_reason(node, project="cases-pkg") is None


def test_a_tagged_node_of_a_package_is_kept():
    node = _node(fqn=["other_pkg", "orders"], tags=["daily"])

    assert dbt.skip_reason(node, project="shop") is None


def test_an_untagged_node_of_the_project_is_kept():
    assert dbt.skip_reason(_node(tags=[]), project="shop") is None


def test_the_gates_run_in_order():
    stub = ["local_stub"]
    package = ["other_pkg", "orders"]
    no_owner = {"meta": {}}

    both = _node(config=no_owner, tags=stub, fqn=package)
    assert dbt.skip_reason(both, project="shop") == "no_owner"
    stub_of_package = _node(tags=stub, fqn=package)
    assert dbt.skip_reason(stub_of_package, project="shop") == "local_stub"
    untagged_package = _node(tags=[], fqn=package)
    assert (
        dbt.skip_reason(untagged_package, project="shop") == "installed_package"
    )


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"meta": None},
        {"meta": {}},
        {"meta": []},
        {"meta": {"owner": None}},
        {"meta": {"owner": ""}},
    ],
)
def test_a_node_without_an_owner_is_skipped_whatever_its_config_holds(config):
    node = _node(config=config)

    assert dbt.skip_reason(node, project="shop") == "no_owner"


def test_a_node_without_a_config_key_has_no_owner():
    node = _node()
    del node["config"]

    assert dbt.skip_reason(node, project="shop") == "no_owner"


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ("daily", "tags must be a list of strings, got str"),
        (None, "tags must be a list of strings, got NoneType"),
        ({"a": 1}, "tags must be a list of strings, got dict"),
        (("daily",), "tags must be a list of strings, got tuple"),
        (["daily", 1], "tags[1] must be a string, got int"),
        ([None], "tags[0] must be a string, got NoneType"),
    ],
)
def test_tags_that_are_not_a_list_of_strings_are_refused(tags, message):
    node = _node(tags=tags)

    with pytest.raises(DbtNodeError) as caught:
        dbt.skip_reason(node, project="shop")
    assert str(caught.value) == f"model.shop.orders: {message}"


def test_a_node_without_tags_has_none():
    node = _node()
    del node["tags"]

    assert dbt.skip_reason(node, project="shop") is None
    assert dbt.schedule(node) == ""


def test_a_node_without_an_owner_is_skipped_before_its_tags_are_read():
    node = _node(config={}, tags="not a list", fqn="not a list")

    assert dbt.skip_reason(node, project="shop") == "no_owner"


@pytest.mark.parametrize(
    ("fqn", "message"),
    [
        ("shop.orders", "fqn must be a list, got str"),
        (5, "fqn must be a list, got int"),
        ({"a": "b"}, "fqn must be a list, got dict"),
        (("shop", "orders"), "fqn must be a list, got tuple"),
        ([1, "orders"], "fqn[0] must be a string, got int"),
        ([None], "fqn[0] must be a string, got NoneType"),
    ],
)
def test_an_fqn_that_is_not_a_list_of_strings_is_refused(fqn, message):
    # the node has a tag, so only the service of the node reads its fqn
    node = _node(fqn=fqn, tags=["daily"])

    with pytest.raises(DbtNodeError) as caught:
        dbt.skip_reason(node, project="shop")
    assert str(caught.value) == f"model.shop.orders: {message}"
    with pytest.raises(DbtNodeError):
        dbt.node_service(node)


def test_an_fqn_is_not_read_for_a_node_that_a_gate_has_left_out():
    node = _node(fqn="not a list", tags=["local_stub"])

    assert dbt.skip_reason(node, project="shop") == "local_stub"


def test_a_node_without_fqn_has_no_service():
    m = load_manifest("cases")
    node = copy.deepcopy(m["nodes"]["model.cases.dormant"])
    del node["fqn"]

    assert dbt.node_service(node) == ""
    assert dbt.node_service(_node(fqn=None)) == ""
    assert dbt.node_service(_node(fqn=[])) == ""
    # with no service and no tag, the node is not the project's
    assert dbt.skip_reason(node, project="cases") == "installed_package"
    assert dbt.skip_reason(node, project="") is None


def test_a_node_service_is_the_first_fqn_entry_with_dashes():
    assert dbt.node_service(_node(fqn=["my_shop_pkg", "a_b"])) == "my-shop-pkg"


def test_the_project_is_the_project_name_then_the_declared_service():
    m = load_manifest("cases")

    assert m["metadata"]["project_name"] == "cases"
    assert dbt.project_service(m, "declared") == "cases"
    assert dbt.project_service(
        {"metadata": {"project_name": "my_shop"}}, "x"
    ) == ("my-shop")
    for metadata in ({}, None, {"project_name": ""}, {"project_name": None}):
        manifest = {"metadata": metadata}
        assert dbt.project_service(manifest, "my_declared") == "my_declared"
        assert dbt.project_service(manifest, "") == ""
    assert dbt.project_service({}, "") == ""


def test_schedule_is_the_first_tag_then_seed_then_empty():
    tags = ["daily", "other"]

    assert dbt.schedule(_node(tags=tags)) == "daily"
    assert dbt.schedule(_node(resource_type="seed", tags=tags)) == "daily"
    assert dbt.schedule(_node(resource_type="seed", tags=[])) == "seed"
    assert dbt.schedule(_node(resource_type="model", tags=[])) == ""
    assert dbt.schedule(_node(resource_type="snapshot", tags=[])) == ""


def test_schedule_refuses_tags_that_are_not_a_list_of_strings():
    with pytest.raises(DbtNodeError, match="^model.shop.orders: tags must be"):
        dbt.schedule(_node(tags="daily"))


def test_schedule_of_the_fixture_nodes():
    m = load_manifest("cases")
    nodes = m["nodes"]

    assert dbt.schedule(nodes["model.cases.a_view"]) == "project_tag"
    assert dbt.schedule(nodes["model.cases.dormant"]) == ""
    assert dbt.schedule(nodes["seed.cases.typed_seed"]) == "seed"
    assert dbt.schedule(nodes["snapshot.cases.orders_snapshot"]) == ""
    assert dbt.schedule(nodes["model.cases_pkg.pkg_tagged"]) == "daily"


def test_a_versioned_model_is_name_dot_v_version():
    m = load_manifest("cases")
    node = m["nodes"]["model.cases.customer.v2"]
    assert (dbt.table_name(node), dbt.unique_id(node)) == (
        "customer.v2",
        "analytics.customer.v2",
    )


@pytest.mark.parametrize(
    ("version", "table"),
    [
        (None, "orders"),
        ("", "orders"),
        (1, "orders.v1"),
        ("2", "orders.v2"),
        ("beta", "orders.vbeta"),
        (1.5, "orders.v1.5"),
        (0, "orders.v0"),
    ],
)
def test_the_table_is_the_name_with_the_version_when_there_is_one(version, table):
    node = _node(version=version)

    assert dbt.table_name(node) == table
    assert dbt.unique_id(node) == f"analytics.{table}"


def test_a_node_without_a_version_key_is_not_versioned():
    assert dbt.table_name(_node()) == "orders"


def test_the_identity_is_lower_case_and_the_table_name_keeps_its_case():
    node = _node(name="Orders", schema="Analytics_DB", version=2)

    assert dbt.table_name(node) == "Orders.v2"
    assert dbt.unique_id(node) == "analytics_db.orders.v2"


def test_the_alias_is_not_part_of_the_identity():
    node = _node(alias="orders_alias")

    assert dbt.unique_id(node) == "analytics.orders"


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        ({"name": None}, "name must be a non-empty string, got NoneType"),
        ({"name": ""}, "name must be a non-empty string, got an empty string"),
        ({"name": 5}, "name must be a non-empty string, got int"),
        ({"version": True}, "version must be a string or a number, got bool"),
        ({"version": ["1"]}, "version must be a string or a number, got list"),
    ],
)
def test_a_table_field_of_the_wrong_shape_is_refused(changed, message):
    node = _node(**changed)

    for call in (dbt.table_name, dbt.unique_id):
        with pytest.raises(DbtNodeError) as caught:
            call(node)
        assert str(caught.value) == f"model.shop.orders: {message}"


@pytest.mark.parametrize(
    ("schema", "shown"),
    [(None, "NoneType"), ("", "an empty string"), (["analytics"], "list")],
)
def test_a_schema_of_the_wrong_shape_is_refused(schema, shown):
    node = _node(schema=schema)

    with pytest.raises(DbtNodeError) as caught:
        dbt.unique_id(node)
    assert str(caught.value) == (
        f"model.shop.orders: schema must be a non-empty string, got {shown}"
    )
    # the table does not depend on the schema
    assert dbt.table_name(node) == "orders"


@pytest.mark.parametrize("missing", ["name", "schema"])
def test_a_missing_identity_field_is_refused(missing):
    node = _node()
    del node[missing]

    with pytest.raises(DbtNodeError) as caught:
        dbt.unique_id(node)
    assert f": {missing} must be a non-empty string, got NoneType" in str(
        caught.value
    )


def test_an_error_names_the_node_by_its_id_then_its_name():
    named = _node(tags=5)
    del named["unique_id"]
    anonymous = _node(tags=5)
    del anonymous["unique_id"]
    del anonymous["name"]

    with pytest.raises(DbtNodeError, match="^orders: tags "):
        dbt.schedule(named)
    with pytest.raises(DbtNodeError, match="^<unknown node>: tags "):
        dbt.schedule(anonymous)


def test_tests_join_like_test_count():
    # a test counts for its attached_node when it has one, else for every depends_on node
    manifest = {
        "nodes": {
            "model.shop.a": {"resource_type": "model"},
            "test.shop.t1": {
                "resource_type": "test",
                "attached_node": "model.shop.a",
                "depends_on": {"nodes": ["model.shop.a", "model.shop.b"]},
            },
            "test.shop.t2": {
                "resource_type": "test",
                "attached_node": None,
                "depends_on": {"nodes": ["model.shop.b", "model.shop.a"]},
            },
            "seed.shop.s": {
                "resource_type": "seed",
                "attached_node": "model.shop.a",
            },
            "test.shop.t3": {
                "resource_type": "test",
                "depends_on": {"nodes": ["model.shop.a", "model.shop.a"]},
            },
            "test.shop.t4": {"resource_type": "test"},
            "test.shop.t5": {"resource_type": "test", "depends_on": None},
        }
    }
    nodes = manifest["nodes"]

    joined = dbt.tests_by_node(manifest)

    assert joined == {
        "model.shop.a": [
            nodes["test.shop.t1"],
            nodes["test.shop.t2"],
            nodes["test.shop.t3"],
        ],
        "model.shop.b": [nodes["test.shop.t2"]],
    }
    assert list(joined) == ["model.shop.a", "model.shop.b"]


def test_the_tests_of_a_manifest_without_nodes_are_none():
    assert dbt.tests_by_node({}) == {}
    assert dbt.tests_by_node({"nodes": None}) == {}
    assert dbt.tests_by_node({"nodes": {}}) == {}


def test_the_tests_of_the_cases_project_join_to_the_node_they_are_attached_to():
    m = load_manifest("cases")
    joined = dbt.tests_by_node(m)
    names = {
        node_id: [t["name"] for t in tests] for node_id, tests in joined.items()
    }

    # a relationships test depends on its target as well, and counts for the attached node only
    assert names["model.cases.customer.v2"] == ["unique_customer_v2_id"]
    assert names["model.cases.customer.v1"] == ["unique_customer_v1_id"]
    assert "source.cases.raw.events" not in names
    # a singular test has no attached node: it counts for every node it depends on
    assert names["model.cases.singular_target"] == ["assert_singular_target"]
    assert len(names["model.cases.generic_tests"]) == 11
    assert sorted(names) == [
        "model.cases.customer.v1",
        "model.cases.customer.v2",
        "model.cases.generic_tests",
        "model.cases.singular_target",
    ]


@pytest.mark.parametrize("project", PROJECTS)
def test_every_test_of_a_fixture_manifest_is_joined_once_per_target(project):
    m = load_manifest(project)
    joined = dbt.tests_by_node(m)
    test_ids = [k for k, n in m["nodes"].items() if n["resource_type"] == "test"]
    expected = 0
    for test_id in test_ids:
        node = m["nodes"][test_id]
        targets = (
            [node["attached_node"]]
            if node.get("attached_node")
            else node["depends_on"]["nodes"]
        )
        expected += len(set(targets))
        for target in set(targets):
            assert node in joined[target]

    assert sum(len(tests) for tests in joined.values()) == expected
    assert all(n["resource_type"] == "test" for ts in joined.values() for n in ts)
    # the tests of one target keep the order of the manifest's keys
    for tests in joined.values():
        positions = [test_ids.index(t["unique_id"]) for t in tests]
        assert positions == sorted(positions)
