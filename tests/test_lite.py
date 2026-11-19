"""The lite check's report: the nodes it skips, the contract problems and the seed headers."""

import copy
import csv
import dataclasses
import json
import re

from pathlib import Path

import pytest

from dbt_fixtures import load_manifest

from continuo_contracts import dbt, lite
from continuo_contracts.contract import contract_hash, normalise_dbt
from continuo_contracts.lite import Line, LiteReport

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dbt"
PROJECTS = ("cases", "core", "finance", "marketing", "service_1")
# The contracts of the `cases` project that fail validation.
INVALID = ("analytics.bad_criticality", "analytics.long_tag")

NO_OWNER = "no owner, continuo skips it"
LOCAL_STUB = "local stub, continuo skips it"
INSTALLED_PACKAGE = "from an installed package and has no tag, continuo skips it"
DORMANT = "no tag: a dormant node, it runs only by hand"
ALL_SKIPPED = (
    "every node of the service is skipped: the release rejects it as "
    "invalid_artifact (manifest contains no model/seed nodes)"
)
UNREADABLE = (
    "the CSV file cannot be read, so its header is not checked against the "
    "declared columns"
)

# The headers the `cases` seeds have for the tests that are about something else than a CSV:
# each is the seed's declared columns.
CASES_HEADERS = {
    "seed.cases.typed_seed": ["id", "amount", "label"],
    "seed.cases.untyped_seed": [],
}

# What the lite check says about the `cases` project, with the headers above.
CASES_LINES = (
    Line(
        "error",
        "model.cases.bad_criticality",
        "customProperties[3].value: `criticality` is 'high', it must be one of "
        "REGULATORY, CORE, SECONDARY",
    ),
    Line("info", "model.cases.dormant", DORMANT),
    Line(
        "error",
        "model.cases.long_tag",
        "customProperties[2].value: `schedule` is "
        "'a_schedule_name_of_fifty_one_characters_in_total_ab', the name of a "
        "schedule must be at most 50 characters long",
    ),
    Line("info", "model.cases.no_owner", NO_OWNER),
    Line("info", "model.cases.stub", LOCAL_STUB),
    Line("info", "model.cases_pkg.pkg_untagged", INSTALLED_PACKAGE),
    Line("info", "snapshot.cases.orders_snapshot", DORMANT),
)


def _csv_header(project):
    """A `read_header` that reads the first row of the CSV of a fixture project's seed."""

    def read(node):
        path = FIXTURES / "projects" / project / node["original_file_path"]
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return next(csv.reader(stream))

    return read


def _declared_header(node):
    return CASES_HEADERS[node["unique_id"]]


def _check(manifest, *, read_header=_declared_header, service=""):
    return lite.check_manifest(
        manifest, declared_service=service, read_header=read_header
    )


def _model(name="orders", *, owner="shop", tags=("daily",), project="shop"):
    return {
        "unique_id": f"model.{project}.{name}",
        "resource_type": "model",
        "name": name,
        "schema": "analytics",
        "fqn": [project, name],
        "original_file_path": f"models/{name}.sql",
        "config": {"materialized": "table", "meta": {"owner": owner}},
        "tags": list(tags),
    }


def _seed(name="rates", *, owner="shop", project="shop", columns=()):
    return {
        "unique_id": f"seed.{project}.{name}",
        "resource_type": "seed",
        "name": name,
        "schema": "analytics",
        "fqn": [project, name],
        "original_file_path": f"seeds/{name}.csv",
        "config": {"materialized": "seed", "meta": {"owner": owner}},
        "tags": [],
        "columns": {column: {"name": column} for column in columns},
    }


def _manifest(*nodes, project="shop"):
    return {
        "metadata": {"project_name": project},
        "nodes": {node["unique_id"]: node for node in nodes},
    }


def _golden_hashes(project):
    """The `contract_hash` that each golden file of `project` pins, by contract id."""
    return {
        path.stem: json.loads(path.read_text("ascii"))["contract_hash"]
        for path in (FIXTURES / "golden" / project).glob("*.json")
    }


# The cases project.


def test_cases_lists_each_skip_with_its_reason_and_passes_except_the_bad_nodes():
    report = _check(load_manifest("cases"))

    assert report.service == "cases"
    assert report.lines == CASES_LINES
    assert report.ok is False
    assert [line.node_id for line in report.lines] == sorted(
        line.node_id for line in report.lines
    )
    skipped = {
        line.node_id
        for line in report.lines
        if line.message in {NO_OWNER, LOCAL_STUB, INSTALLED_PACKAGE}
    }
    assert skipped == {
        "model.cases.no_owner",
        "model.cases.stub",
        "model.cases_pkg.pkg_untagged",
    }
    errors = {line.node_id for line in report.lines if line.level == "error"}
    assert errors == {"model.cases.bad_criticality", "model.cases.long_tag"}


def test_a_node_that_passes_gets_no_line():
    report = _check(load_manifest("cases"))

    named = {line.node_id for line in report.lines}

    assert "model.cases.a_view" not in named
    assert "model.cases.generic_tests" not in named
    assert "seed.cases.typed_seed" not in named


def test_every_line_has_a_level_of_info_or_error():
    report = _check(load_manifest("cases"))

    assert {line.level for line in report.lines} == {"info", "error"}


# A service with nothing kept.


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"nodes": None},
        {"nodes": {}},
        {
            "nodes": {
                "test.shop.not_null": {"resource_type": "test"},
                "analysis.shop.report": {"resource_type": "analysis"},
            }
        },
    ],
    ids=["no nodes key", "null nodes", "no node", "no model, seed or snapshot"],
)
def test_every_skipped_service_fails(manifest):
    report = _check(manifest, service="shop")

    assert report.lines == (Line("error", "", ALL_SKIPPED),)
    assert report.ok is False
    assert report.contracts == {}


def test_a_service_whose_nodes_are_all_skipped_lists_each_skip_then_fails():
    manifest = _manifest(
        _model("a", owner=""),
        _model("b", tags=("local_stub",)),
        _model("c", project="other", tags=()),
        _seed("d", owner=""),
    )

    report = _check(manifest)

    assert report.lines == (
        Line("info", "model.other.c", INSTALLED_PACKAGE),
        Line("info", "model.shop.a", NO_OWNER),
        Line("info", "model.shop.b", LOCAL_STUB),
        Line("info", "seed.shop.d", NO_OWNER),
        Line("error", "", ALL_SKIPPED),
    )
    assert report.ok is False
    assert report.contracts == {}


def test_a_kept_node_with_a_problem_is_not_a_skipped_service():
    manifest = _manifest(_model("a", owner=""), _model("b", tags=("a", 1)))

    report = _check(manifest)

    assert [line.message for line in report.lines] == [
        NO_OWNER,
        "tags[1] must be a string, got int",
    ]
    assert ALL_SKIPPED not in [line.message for line in report.lines]


# A dormant node.


def test_a_dormant_node_gets_an_information_line():
    manifest = _manifest(_model("orders", tags=()))

    report = _check(manifest)

    assert report.lines == (Line("info", "model.shop.orders", DORMANT),)
    assert report.ok is True
    assert list(report.contracts) == ["analytics.orders"]


def test_a_dormant_model_and_a_dormant_snapshot_get_the_line_and_a_seed_does_not():
    report = _check(load_manifest("cases"))

    dormant = [line.node_id for line in report.lines if line.message == DORMANT]
    # `untyped_seed` has no tag either, and a seed with no tag runs on the `seed` schedule.
    assert dormant == ["model.cases.dormant", "snapshot.cases.orders_snapshot"]


# The seed header check.


def test_seed_header_issues_name_each_column_both_ways():
    assert dbt.seed_header_issues(["a", "b"], ["b", "c"]) == [
        "c is in the CSV header but not declared",
        "a is declared but not in the CSV header",
    ]


def test_seed_header_issues_are_empty_when_the_columns_agree_in_any_order():
    assert dbt.seed_header_issues(["a", "b"], ["b", "a"]) == []
    assert dbt.seed_header_issues([], []) == []


def test_seed_header_issues_come_in_header_order_then_declared_order():
    issues = dbt.seed_header_issues(["z", "y", "m"], ["q", "m", "p"])

    assert issues == [
        "q is in the CSV header but not declared",
        "p is in the CSV header but not declared",
        "z is declared but not in the CSV header",
        "y is declared but not in the CSV header",
    ]


def test_seed_header_issues_compare_names_exactly_as_written():
    assert dbt.seed_header_issues(["id", " name"], ["ID", "name"]) == [
        "ID is in the CSV header but not declared",
        "name is in the CSV header but not declared",
        "id is declared but not in the CSV header",
        " name is declared but not in the CSV header",
    ]


def test_seed_header_issues_name_a_repeated_column_once():
    assert dbt.seed_header_issues(["a", "a"], ["b", "b", "c"]) == [
        "b is in the CSV header but not declared",
        "c is in the CSV header but not declared",
        "a is declared but not in the CSV header",
    ]


def test_a_seed_whose_csv_cannot_be_read_is_an_error():
    manifest = _manifest(_seed("rates", columns=["a"]), _model("orders"))

    report = _check(manifest, read_header=lambda node: None)

    assert report.lines == (Line("error", "seed.shop.rates", UNREADABLE),)
    assert report.ok is False
    # The contract of the seed is fine: only its CSV is not.
    assert set(report.contracts) == {"analytics.orders", "analytics.rates"}


def test_a_seed_whose_header_matches_its_declared_columns_passes():
    manifest = _manifest(_seed("rates", columns=["a", "b"]))

    report = _check(manifest, read_header=lambda node: ["b", "a"])

    assert report.lines == ()
    assert report.ok is True


def test_a_seed_whose_header_differs_gets_one_error_line_for_each_column():
    manifest = _manifest(_seed("rates", columns=["a", "b"]))

    report = _check(manifest, read_header=lambda node: ["b", "c", "d"])

    assert report.lines == (
        Line(
            "error", "seed.shop.rates", "c is in the CSV header but not declared"
        ),
        Line(
            "error", "seed.shop.rates", "d is in the CSV header but not declared"
        ),
        Line(
            "error", "seed.shop.rates", "a is declared but not in the CSV header"
        ),
    )


def test_a_header_that_differs_in_case_is_two_errors():
    manifest = _manifest(_seed("rates", columns=["id"]))

    report = _check(manifest, read_header=lambda node: ["ID"])

    assert [line.message for line in report.lines] == [
        "ID is in the CSV header but not declared",
        "id is declared but not in the CSV header",
    ]


def test_an_empty_header_leaves_every_declared_column_missing():
    manifest = _manifest(_seed("rates", columns=["a"]))

    report = _check(manifest, read_header=lambda node: [])

    assert [line.message for line in report.lines] == [
        "a is declared but not in the CSV header"
    ]


def test_the_header_is_read_only_for_a_seed_that_gets_a_contract():
    manifest = copy.deepcopy(load_manifest("cases"))
    manifest["nodes"]["seed.cases.untyped_seed"]["config"]["meta"]["owner"] = ""
    asked = []

    def read(node):
        asked.append(node["unique_id"])
        return _declared_header(node)

    report = _check(manifest, read_header=read)

    assert asked == ["seed.cases.typed_seed"]
    assert Line("info", "seed.cases.untyped_seed", NO_OWNER) in report.lines
    assert [line.node_id for line in report.lines if line.level == "error"] == [
        "model.cases.bad_criticality",
        "model.cases.long_tag",
    ]


INSTALLED_SEED = "a seed of an installed package: its CSV header is not checked"


def test_a_kept_seed_of_an_installed_package_gets_an_information_line_and_no_read():
    seed = _seed("rates", project="pkg", columns=["a"])
    seed.update(package_name="pkg", tags=["daily"])
    asked = []

    def read(node):
        asked.append(node["unique_id"])
        return ["other"]

    report = _check(_manifest(seed), read_header=read)

    assert asked == []
    assert report.lines == (Line("info", "seed.pkg.rates", INSTALLED_SEED),)
    assert report.ok is True
    assert set(report.contracts) == {"analytics.rates"}


@pytest.mark.parametrize("package", ["shop", None, "absent"])
def test_a_seed_of_the_project_has_its_header_checked(package):
    seed = _seed("rates", columns=["a"])
    if package != "absent":
        seed["package_name"] = package

    report = _check(_manifest(seed), read_header=lambda node: ["b"])

    assert [line.message for line in report.lines] == [
        "b is in the CSV header but not declared",
        "a is declared but not in the CSV header",
    ]


def test_with_no_project_name_every_kept_seed_has_its_header_checked():
    seed = _seed("rates", project="pkg", columns=["a"])
    seed.update(package_name="pkg", tags=["daily"])
    manifest = {"nodes": {seed["unique_id"]: seed}}

    report = _check(manifest, read_header=lambda node: ["a"], service="shop")

    # The header agrees with the declared columns: the one line is the service check's.
    assert report.lines == (
        Line("error", "seed.pkg.rates", _other_service("pkg", "shop")),
    )


def test_an_installed_seed_with_a_contract_issue_gets_both_lines():
    seed = _seed("rates", project="pkg", columns=["a"])
    seed.update(package_name="pkg", tags=["x" * 51])

    report = _check(_manifest(seed), read_header=lambda node: None)

    assert [line.level for line in report.lines] == ["error", "info"]
    assert report.lines[-1] == Line("info", "seed.pkg.rates", INSTALLED_SEED)
    assert report.contracts == {}


def test_the_cases_seed_without_a_declaration_fails_against_its_csv():
    report = _check(load_manifest("cases"), read_header=_csv_header("cases"))

    seed_lines = [
        line for line in report.lines if line.node_id.startswith("seed.")
    ]
    assert seed_lines == [
        Line(
            "error",
            "seed.cases.untyped_seed",
            "id is in the CSV header but not declared",
        ),
        Line(
            "error",
            "seed.cases.untyped_seed",
            "label is in the CSV header but not declared",
        ),
    ]


def test_core_before_its_declarations_fails_on_three_seeds():
    report = _check(load_manifest("core"), read_header=_csv_header("core"))

    def lines(seed, columns):
        return [
            Line(
                "error",
                f"seed.core.{seed}",
                f"{column} is in the CSV header but not declared",
            )
            for column in columns
        ]

    # `seed_users` declares no column. The two transaction seeds declare only `fee_amount`, which
    # their CSVs have, so it is the one column of each that is not reported.
    assert report.lines == (
        *lines(
            "seed_card_transactions",
            [
                "transaction_id",
                "user_id",
                "amount",
                "payer",
                "receiver",
                "created_at",
            ],
        ),
        *lines(
            "seed_fx_transactions",
            [
                "transaction_id",
                "user_id",
                "amount",
                "currency_from",
                "currency_to",
                "rate",
                "created_at",
            ],
        ),
        *lines(
            "seed_users",
            ["user_id", "name", "email", "birth_year", "created_at"],
        ),
    )
    assert report.ok is False


# The contract of each node.


@pytest.mark.parametrize("project", PROJECTS)
def test_the_report_gives_each_kept_node_its_contract_hash(project):
    manifest = load_manifest(project)

    report = _check(manifest, read_header=_csv_header(project))

    expected = {
        contract_id: hashed
        for contract_id, hashed in _golden_hashes(project).items()
        if contract_id not in INVALID
    }
    assert report.contracts == expected
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in report.contracts.values()
    )


def test_every_valid_kept_node_of_cases_has_the_hash_of_its_stored_form():
    manifest = load_manifest("cases")
    tests = dbt.tests_by_node(manifest)

    report = _check(manifest)

    kept = [
        key
        for key, node in manifest["nodes"].items()
        if node["resource_type"] in dbt.RESOURCE_TYPES
        and dbt.skip_reason(node, project="cases") is None
    ]
    expected = {}
    for key in kept:
        generated = dbt.generate(
            manifest["nodes"][key], tests.get(key, []), manifest=manifest
        )
        expected[generated.document["id"]] = contract_hash(
            normalise_dbt(generated.document, reads=[])
        )
    assert len(expected) == 17
    assert {k: v for k, v in expected.items() if k not in INVALID} == (
        report.contracts
    )
    assert len(report.contracts) == 15


def test_the_hash_is_the_one_of_the_stored_form_with_no_compiled_reads():
    manifest = load_manifest("core")
    node = manifest["nodes"]["model.core.daily_transactions"]
    generated = dbt.generate(
        node,
        dbt.tests_by_node(manifest).get("model.core.daily_transactions", []),
        manifest=manifest,
    )

    report = _check(manifest, read_header=_csv_header("core"))

    assert report.contracts["analytics.daily_transactions"] == contract_hash(
        normalise_dbt(generated.document, reads=[])
    )


def test_the_contracts_are_listed_in_the_order_of_the_node_ids():
    report = _check(load_manifest("core"), read_header=_csv_header("core"))

    assert list(report.contracts) == [
        "analytics.daily_transactions",
        "analytics.dbt_daily_kpis",
        "analytics.read_order",
        "analytics.read_order_v2",
        "analytics.revenue_per_user",
        "analytics.seed_card_transactions",
        "analytics.seed_fx_transactions",
        "analytics.seed_users",
    ]


def test_a_contract_with_a_validation_issue_has_error_lines_and_no_hash():
    report = _check(load_manifest("cases"))

    for node_id, contract_id in (
        ("model.cases.bad_criticality", "analytics.bad_criticality"),
        ("model.cases.long_tag", "analytics.long_tag"),
    ):
        assert contract_id not in report.contracts
        assert [
            line.level for line in report.lines if line.node_id == node_id
        ] == ["error"]
    assert not set(INVALID) & set(report.contracts)


# Nodes the generator or the gates cannot read.


def test_a_node_the_generator_cannot_read_is_an_error_line_without_its_own_id():
    manifest = _manifest(_model("orders"), _model("sales"))
    manifest["nodes"]["model.shop.orders"]["description"] = 5

    report = _check(manifest)

    assert report.lines == (
        Line(
            "error", "model.shop.orders", "description must be a string, got int"
        ),
    )
    assert list(report.contracts) == ["analytics.sales"]
    assert report.ok is False


def test_a_test_field_the_generator_cannot_read_names_the_test():
    manifest = copy.deepcopy(load_manifest("core"))
    tests = dbt.tests_by_node(manifest)["model.core.daily_transactions"]
    tests[0]["config"]["severity"] = "fatal"

    report = _check(manifest, read_header=_csv_header("core"))

    [line] = [
        line
        for line in report.lines
        if line.node_id == "model.core.daily_transactions"
    ]
    assert line.level == "error"
    assert line.message.startswith(f"test {tests[0]['unique_id']}: ")


def test_a_node_whose_gate_cannot_read_it_is_an_error_line():
    manifest = _manifest(_model("orders"))
    manifest["nodes"]["model.shop.orders"]["tags"] = "daily"

    report = _check(manifest)

    # The node is not skipped, so the service is not all skipped.
    assert report.lines == (
        Line(
            "error",
            "model.shop.orders",
            "tags must be a list of strings, got str",
        ),
    )
    assert report.contracts == {}


def test_a_declared_read_that_is_not_a_table_is_an_error_line_and_has_no_hash():
    manifest = _manifest(_model("orders"), _model("sales"))
    manifest["nodes"]["model.shop.orders"]["config"]["meta"]["continuo"] = {
        "reads": ["nodot"]
    }

    report = _check(manifest)

    [line] = report.lines
    assert line.level == "error"
    assert line.node_id == "model.shop.orders"
    assert line.message.startswith("reads entry 'nodot': a table is written")
    assert list(report.contracts) == ["analytics.sales"]


def test_the_problems_of_one_node_come_in_a_fixed_order():
    node = _seed("rates", columns=["a"])
    node["config"]["meta"]["criticality"] = "high"

    report = _check(_manifest(node), read_header=lambda _: ["b"])

    assert report.contracts == {}
    assert [line.message for line in report.lines] == [
        "customProperties[3].value: `criticality` is 'high', it must be one of "
        "REGULATORY, CORE, SECONDARY",
        "b is in the CSV header but not declared",
        "a is declared but not in the CSV header",
    ]


# The service and the order.


def test_the_service_is_the_one_the_gates_compare_with():
    named = _manifest(_model("orders", project="my_shop"), project="my_shop")

    assert _check(named, service="declared").service == "my-shop"
    assert (
        _check({"nodes": named["nodes"]}, service="declared").service
        == "declared"
    )
    assert _check({}, service="").service == ""


def test_a_node_of_another_package_is_skipped_against_the_declared_service():
    manifest = {
        "nodes": {
            "model.shop.orders": _model("orders"),
            "model.pkg.rates": _model("rates", project="pkg", tags=()),
        }
    }

    report = _check(manifest, service="shop")

    assert report.lines == (Line("info", "model.pkg.rates", INSTALLED_PACKAGE),)


@pytest.mark.parametrize(
    ("project", "service"),
    [("cases", ""), ("core", ""), ("cases", "cases"), ("core", "other")],
)
def test_report_lines_do_not_depend_on_manifest_order(project, service):
    manifest = load_manifest(project)
    before = copy.deepcopy(manifest)
    nodes = list(manifest["nodes"].items())
    reordered = {
        **manifest,
        "nodes": dict(reversed(nodes)),
    }
    shuffled = {
        **manifest,
        "nodes": dict(nodes[1::2] + nodes[::2]),
    }
    expected = _check(manifest, read_header=_csv_header(project), service=service)

    for other in (reordered, shuffled):
        report = _check(other, read_header=_csv_header(project), service=service)

        assert report == expected
        assert list(report.contracts) == list(expected.contracts)
    assert manifest == before


# The release's service check: a kept node of another service than the declared one.


def _other_service(node_service, declared):
    return (
        f"the node's service is {node_service}, the declared service is {declared}: "
        "the release rejects a manifest that holds a node of another service as "
        "invalid_artifact"
    )


def test_a_tagged_package_node_is_an_error_against_the_declared_service():
    manifest = load_manifest("cases")

    report = _check(manifest, service="cases")

    line = Line(
        "error",
        "model.cases_pkg.pkg_tagged",
        _other_service("cases-pkg", "cases"),
    )
    lines = list(CASES_LINES)
    lines.insert(
        lines.index(
            Line("info", "model.cases_pkg.pkg_untagged", INSTALLED_PACKAGE)
        ),
        line,
    )
    assert report.lines == tuple(lines)
    assert report.ok is False
    # The node's contract keeps its hash: the line says what the release does with the manifest.
    assert report.contracts == _check(manifest).contracts
    assert "analytics.pkg_tagged" in report.contracts


def test_every_node_of_the_project_passes_the_service_check_of_its_own_service():
    manifest = copy.deepcopy(load_manifest("cases"))
    for key in [
        key for key in manifest["nodes"] if key.startswith("model.cases_pkg.")
    ]:
        del manifest["nodes"][key]

    report = _check(manifest, service="cases")

    assert report == _check(manifest)
    assert not [
        line for line in report.lines if "declared service" in line.message
    ]


def test_an_empty_declared_service_makes_no_service_check():
    manifest = {"nodes": {"model.pkg.rates": _model("rates", project="pkg")}}

    report = _check(manifest, service="")

    assert report.lines == ()
    assert report.ok is True


def test_a_skipped_node_of_another_service_gets_no_service_line():
    manifest = _manifest(
        _model("orders"), _model("rates", project="pkg", tags=())
    )

    report = _check(manifest, service="shop")

    assert report.lines == (Line("info", "model.pkg.rates", INSTALLED_PACKAGE),)


def test_a_project_node_is_an_error_against_another_declared_service():
    manifest = _manifest(_model("orders"), project="my_shop")

    report = _check(manifest, service="my_shop")

    # The node's service has `_` written `-`, and the declared service is compared as given.
    assert report.lines == (
        Line("error", "model.shop.orders", _other_service("shop", "my_shop")),
    )
    assert list(report.contracts) == ["analytics.orders"]


def test_the_all_skipped_line_is_unchanged_by_the_service_check():
    manifest = _manifest(
        _model("a", owner=""), _model("c", project="other", tags=())
    )

    report = _check(manifest, service="another")

    assert report.lines == (
        Line("info", "model.other.c", INSTALLED_PACKAGE),
        Line("info", "model.shop.a", NO_OWNER),
        Line("error", "", ALL_SKIPPED),
    )


def test_the_service_line_comes_first_among_the_lines_of_a_node():
    dormant = _aliased("orders", "orders_b", project="pkg")
    dormant["tags"] = []
    dormant["config"]["meta"]["criticality"] = "high"
    broken = _model("sales", project="pkg")
    broken["config"]["materialized"] = 5
    # The gates compare each node with `pkg`, so the dormant node of `pkg` is kept.
    manifest = _manifest(
        _aliased("orders", "orders_a"), dormant, broken, project="pkg"
    )

    report = _check(manifest, service="shop")

    assert [line.message for line in report.lines] == [
        _other_service("pkg", "shop"),
        DORMANT,
        "customProperties[2].value: `criticality` is 'high', it must be one of "
        "REGULATORY, CORE, SECONDARY",
        _SHARED.format("analytics.orders", "model.shop.orders"),
        _other_service("pkg", "shop"),
        "config.materialized must be a non-empty string, got int",
        _SHARED.format("analytics.orders", "model.pkg.orders"),
    ]
    assert [line.node_id for line in report.lines] == [
        *["model.pkg.orders"] * 4,
        *["model.pkg.sales"] * 2,
        "model.shop.orders",
    ]


def test_the_lines_come_in_the_order_of_the_node_ids():
    report = _check(load_manifest("core"), read_header=_csv_header("core"))

    ids = [line.node_id for line in report.lines]

    assert ids == sorted(ids)
    assert ids[0] == "seed.core.seed_card_transactions"


def test_the_check_prints_and_logs_nothing(capsys, caplog):
    caplog.set_level("DEBUG")

    _check(load_manifest("cases"))

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []


# The report.


def test_a_report_is_ok_when_it_has_no_error_line():
    assert LiteReport("shop", (), {}).ok is True
    assert (
        LiteReport("shop", (Line("info", "model.shop.a", NO_OWNER),), {}).ok
        is True
    )


def test_a_report_with_an_error_line_is_not_ok():
    lines = (Line("info", "model.shop.a", NO_OWNER), Line("error", "", "x"))

    assert LiteReport("shop", lines, {}).ok is False


def test_a_line_and_a_report_cannot_be_changed():
    line = Line("info", "model.shop.a", NO_OWNER)
    report = LiteReport("shop", (line,), {})

    with pytest.raises(dataclasses.FrozenInstanceError):
        line.message = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.service = "x"


def test_a_line_and_a_report_name_their_fields():
    assert [f.name for f in dataclasses.fields(Line)] == [
        "level",
        "node_id",
        "message",
    ]
    assert [f.name for f in dataclasses.fields(LiteReport)] == [
        "service",
        "lines",
        "contracts",
    ]


def test_the_banner_says_what_passing_does_not_guarantee():
    assert lite.BANNER == (
        "LITE CHECK. Passing it does not guarantee that continuo will create "
        "this service's contracts at release time."
    )


def test_release_only_names_the_six_things_only_the_release_does():
    assert [what for what, _ in lite.RELEASE_ONLY] == [
        "dbt runs in the team's image",
        "the whole estate is parsed",
        "the tables a node reads",
        "the install's library version",
        "install settings",
        "the install's ontology",
    ]
    for what, why in lite.RELEASE_ONLY:
        assert what and why
        assert why.endswith(".")


def test_release_only_carries_no_reference_code():
    text = " ".join(" ".join(row) for row in lite.RELEASE_ONLY)

    assert re.search(r"\b[A-Z]{1,2}\d+[a-z]?\b", text) is None


# A value the JSON reader accepts but canonical JSON cannot write is a node's error line.


def _nested(depth):
    value = 1
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda manifest: manifest["nodes"][
                "test.cases.unique_generic_tests_id.ade3165569"
            ].update(description="\ud800"),
            "test test.cases.unique_generic_tests_id.ade3165569: description: "
            "string holds a lone surrogate",
        ),
        (
            lambda manifest: manifest["nodes"]["model.cases.generic_tests"][
                "config"
            ].update(unique_key=_nested(97)),
            "contract.customProperties[",
        ),
    ],
    ids=["a lone surrogate", "a value too deep for the contract"],
)
def test_a_value_canonical_json_cannot_write_is_one_error_line(change, message):
    manifest = copy.deepcopy(load_manifest("cases"))
    change(manifest)
    report = lite.check_manifest(
        manifest, declared_service="cases", read_header=lambda node: []
    )
    (line,) = [
        line
        for line in report.lines
        if line.node_id == "model.cases.generic_tests"
    ]
    assert line.level == "error"
    assert line.message.startswith(message)
    assert "analytics.generic_tests" not in report.contracts
    assert "analytics.governed" in report.contracts


# Two kept nodes whose contracts have one id: the release rejects the manifest as duplicate_table.

_SHARED = "contract id {} is also the id of {}: the release rejects it as duplicate_table"


def _aliased(name, alias, *, project="shop"):
    node = _model(name, project=project)
    node["alias"] = alias
    return node


def test_two_nodes_that_write_one_contract_id_are_errors_and_get_no_hash():
    manifest = _manifest(
        _aliased("orders", "orders_a"),
        _aliased("orders", "orders_b", project="pkg"),
        _model("sales"),
    )
    report = _check(manifest)
    assert report.lines == (
        Line(
            "error",
            "model.pkg.orders",
            _SHARED.format("analytics.orders", "model.shop.orders"),
        ),
        Line(
            "error",
            "model.shop.orders",
            _SHARED.format("analytics.orders", "model.pkg.orders"),
        ),
    )
    assert list(report.contracts) == ["analytics.sales"]
    assert report.ok is False


def test_names_that_differ_only_in_case_are_one_contract_id():
    report = _check(_manifest(_model("Orders"), _model("orders")))
    assert [(line.level, line.node_id) for line in report.lines] == [
        ("error", "model.shop.Orders"),
        ("error", "model.shop.orders"),
    ]
    assert report.contracts == {}


def test_two_nodes_of_one_versioned_model_name_share_the_id_of_the_version():
    one, two = _model("customer"), _model("customer")
    other = _aliased("customer", "customer_pkg_v1", project="pkg")
    for node, version in ((one, 1), (two, 2), (other, 1)):
        node["version"] = version
        node["unique_id"] += f".v{version}"
    report = _check(_manifest(one, two, other))
    assert [line.node_id for line in report.lines] == [
        "model.pkg.customer.v1",
        "model.shop.customer.v1",
    ]
    assert list(report.contracts) == ["analytics.customer.v2"]


def test_a_shared_id_of_three_nodes_names_the_other_two_in_id_order():
    report = _check(
        _manifest(*(_aliased("t", f"t_{p}", project=p) for p in "cab"))
    )
    assert report.lines == (
        Line(
            "error",
            "model.a.t",
            _SHARED.format("analytics.t", "model.b.t, model.c.t"),
        ),
        Line(
            "error",
            "model.b.t",
            _SHARED.format("analytics.t", "model.a.t, model.c.t"),
        ),
        Line(
            "error",
            "model.c.t",
            _SHARED.format("analytics.t", "model.a.t, model.b.t"),
        ),
    )
    assert report.contracts == {}


def test_the_lines_of_a_shared_id_follow_the_node_ids_whatever_the_manifest_order():
    nodes = [
        _aliased("orders", "orders_a", project="a"),
        _model("middle", project="m"),
        _aliased("orders", "orders_b", project="z"),
    ]
    nodes[1]["config"]["meta"]["criticality"] = "high"
    report = _check(_manifest(*nodes))
    assert _check(_manifest(*reversed(nodes))) == report
    ids = [line.node_id for line in report.lines]
    assert ids == ["model.a.orders", "model.m.middle", "model.z.orders"]


def test_a_node_whose_contract_has_an_issue_still_shares_its_id():
    bad = _aliased("orders", "orders_b", project="pkg")
    bad["config"]["meta"]["criticality"] = "high"
    report = _check(_manifest(_aliased("orders", "orders_a"), bad))
    assert [(line.node_id, line.level) for line in report.lines] == [
        ("model.pkg.orders", "error"),
        ("model.pkg.orders", "error"),
        ("model.shop.orders", "error"),
    ]
    assert report.lines[1].message == _SHARED.format(
        "analytics.orders", "model.shop.orders"
    )
    assert report.contracts == {}


def test_a_node_the_library_cannot_read_shares_no_id():
    broken = _aliased("orders", "orders_b", project="pkg")
    broken["config"]["materialized"] = 5
    report = _check(_manifest(_aliased("orders", "orders_a"), broken))
    assert [line.node_id for line in report.lines] == ["model.pkg.orders"]
    assert list(report.contracts) == ["analytics.orders"]


@pytest.mark.parametrize("project", PROJECTS)
def test_no_fixture_project_has_a_shared_id(project):
    report = _check(load_manifest(project), read_header=_csv_header(project))
    assert not [
        line for line in report.lines if line.message.endswith("duplicate_table")
    ]
