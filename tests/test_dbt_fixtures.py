"""The dbt fixture manifests were built by the pinned dbt and a rebuild writes the same bytes.

The tests marked `dbt` rebuild the manifests through `uvx`, which needs the network, so the
default run deselects them (`addopts` holds `-m 'not dbt'`). Run them by hand with
`uv run pytest -m dbt tests/test_dbt_fixtures.py`; a `-m` on the command line replaces the one
in `addopts`.
"""

import gzip
import importlib.util
import json
import logging
import shutil

from pathlib import Path

import pytest

from dbt_fixtures import MANIFESTS, load_manifest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_dbt_fixtures.py"
spec = importlib.util.spec_from_file_location("build_dbt_fixtures", SCRIPT)
build_dbt_fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_dbt_fixtures)

NAMES = ["core", "finance", "marketing", "service_1", "cases"]


@pytest.mark.parametrize("name", NAMES)
def test_the_fixture_manifests_were_built_by_the_pinned_dbt(name):
    m = load_manifest(name)
    assert m["metadata"]["dbt_version"] == "1.12.0b1"
    assert m["metadata"]["adapter_type"] == "postgres"


def test_the_script_names_the_five_projects_and_the_version_the_manifests_carry():
    assert list(build_dbt_fixtures.NAMES) == NAMES
    assert build_dbt_fixtures.DBT_VERSION == "1.12.0b1"


def test_load_manifest_reads_the_gzipped_file_once():
    assert load_manifest("cases") is load_manifest("cases")
    with gzip.open(MANIFESTS / "cases.json.gz", "rt", encoding="utf-8") as stream:
        assert load_manifest("cases") == json.load(stream)


@pytest.mark.parametrize("name", NAMES)
def test_a_manifest_holds_no_time_or_path_of_the_build(name):
    m = load_manifest(name)
    for section in build_dbt_fixtures.VOLATILE_SECTIONS:
        for entry in m[section].values():
            assert not set(build_dbt_fixtures.VOLATILE_KEYS) & set(entry)
    assert {
        key: m["metadata"][key] for key in build_dbt_fixtures.FIXED_METADATA
    } == build_dbt_fixtures.FIXED_METADATA
    assert "continuo-dbt-fixture" not in json.dumps(m)


def test_the_cases_manifest_was_parsed_east_of_utc():
    governed = load_manifest("cases")["nodes"]["model.cases.governed"]
    assert governed["deprecation_date"] == "2026-12-01T00:00:00+09:00"


def test_the_cases_manifest_has_a_node_for_each_rule():
    m = load_manifest("cases")
    nodes = m["nodes"]
    assert nodes["model.cases.customer.v2"]["latest_version"] == 2
    assert nodes["model.cases.dormant"]["tags"] == []
    assert nodes["model.cases.no_owner"]["config"]["meta"]["owner"] == ""
    assert nodes["model.cases.stub"]["tags"] == ["project_tag", "local_stub"]
    assert (
        nodes["model.cases.long_tag"]["tags"][0]
        == "a_schedule_name_of_fifty_one_characters_in_total_ab"
    )
    assert len(nodes["model.cases.long_tag"]["tags"][0]) == 51
    assert nodes["model.cases_pkg.pkg_untagged"]["tags"] == []
    assert nodes["model.cases_pkg.pkg_tagged"]["tags"] == ["daily"]
    assert nodes["seed.cases.typed_seed"]["config"]["column_types"] == {
        "amount": "numeric(12,2)"
    }
    assert (
        nodes["snapshot.cases.orders_snapshot"]["config"]["hard_deletes"]
        == "new_record"
    )
    assert "test.cases.assert_singular_target" in nodes


def test_manifest_bytes_are_sorted_ascii_json_in_a_gzip_without_a_time():
    data = build_dbt_fixtures.manifest_bytes({"b": 1, "a": "\u00e9"})
    assert data[4:8] == bytes(4)
    assert gzip.decompress(data) == b'{\n "a": "\\u00e9",\n "b": 1\n}'


def test_the_fixed_values_replace_the_clock_and_the_build_paths():
    manifest = {
        "metadata": dict.fromkeys(build_dbt_fixtures.FIXED_METADATA, "now"),
        "nodes": {"n": {"created_at": 1.5, "root_path": "/tmp/x", "name": "n"}},
        "sources": {"s": {"created_at": 2.5, "name": "s"}},
        "macros": {"m": {"created_at": 3.5, "name": "m"}},
        "docs": {"d": {"name": "d"}},
    }
    build_dbt_fixtures._fix_manifest(manifest)
    assert manifest == {
        "metadata": build_dbt_fixtures.FIXED_METADATA,
        "nodes": {"n": {"name": "n"}},
        "sources": {"s": {"name": "s"}},
        "macros": {"m": {"name": "m"}},
        "docs": {"d": {"name": "d"}},
    }


def test_the_build_environment_drops_the_callers_dbt_settings(monkeypatch):
    monkeypatch.setenv("DBT_TARGET", "prod")
    monkeypatch.setenv("TZ", "UTC")
    env = build_dbt_fixtures._environment()
    assert "DBT_TARGET" not in env
    assert env["TZ"] == "Asia/Tokyo"
    assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"


def test_main_builds_all_five_projects_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(build_dbt_fixtures, "MANIFESTS", tmp_path / "out")
    monkeypatch.setattr(build_dbt_fixtures, "build", lambda name: name.encode())
    assert build_dbt_fixtures.main([]) == 0
    written = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert written == sorted(f"{name}.json.gz" for name in NAMES)
    assert (tmp_path / "out" / "cases.json.gz").read_bytes() == b"cases"


def test_main_builds_only_the_projects_it_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(build_dbt_fixtures, "MANIFESTS", tmp_path)
    monkeypatch.setattr(build_dbt_fixtures, "build", lambda name: name.encode())
    assert build_dbt_fixtures.main(["core", "cases"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "cases.json.gz",
        "core.json.gz",
    ]


def test_main_refuses_an_unknown_project(capsys):
    with pytest.raises(SystemExit) as raised:
        build_dbt_fixtures.main(["nope"])
    assert raised.value.code == 2
    assert "unknown project ['nope']" in capsys.readouterr().err


def test_main_logs_a_failed_build_and_returns_one(tmp_path, monkeypatch, caplog):
    def fail(name):
        raise build_dbt_fixtures.BuildError("dbt parse failed with status 2")

    monkeypatch.setattr(build_dbt_fixtures, "MANIFESTS", tmp_path)
    monkeypatch.setattr(build_dbt_fixtures, "build", fail)
    with caplog.at_level(logging.ERROR):
        assert build_dbt_fixtures.main(["core"]) == 1
    assert "core: dbt parse failed with status 2" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_a_command_that_cannot_start_is_a_build_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        build_dbt_fixtures, "DBT_COMMAND", ("no-such-executable-uvx",)
    )
    with pytest.raises(build_dbt_fixtures.BuildError, match="cannot start"):
        build_dbt_fixtures._run_dbt("parse", tmp_path)


def test_a_command_that_fails_is_a_build_error_with_its_output(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build_dbt_fixtures, "DBT_COMMAND", ("false",))
    with pytest.raises(
        build_dbt_fixtures.BuildError, match="parse failed with status 1"
    ):
        build_dbt_fixtures._run_dbt("parse", tmp_path)


@pytest.mark.dbt
@pytest.mark.skipif(
    shutil.which("uvx") is None, reason="the rebuild runs dbt through uvx"
)
@pytest.mark.parametrize("name", NAMES)
def test_a_rebuild_writes_the_same_bytes(name, tmp_path):
    rebuilt = build_dbt_fixtures.build(name, workdir=tmp_path)
    assert rebuilt == (MANIFESTS / f"{name}.json.gz").read_bytes()
