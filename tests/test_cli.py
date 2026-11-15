"""The `continuo-contracts` command: `validate` and `hash` over Python contract files."""

import importlib.metadata
import json
import logging
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest

from continuo_contracts import cli
from continuo_contracts.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = FIXTURES / "demo" / "service-py" / "contracts"
VECTORS = json.loads(
    (
        resources.files("continuo_contracts") / "vectors" / "contracts.json"
    ).read_text("utf-8")
)
FAMILY_LINE = "this release checks Python contracts"


def _case(name):
    return next(case for case in VECTORS if case["name"] == name)


def _put(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _demo_text(name="py_daily_kpis.odcs.yaml"):
    return (DEMO / name).read_text(encoding="utf-8")


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def _infos(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture
def bad_folder(tmp_path):
    """A folder with one valid file, one with a wrong file name and one with a repeated key."""
    _put(
        tmp_path,
        "fx_eur_usd_rates.odcs.yaml",
        _demo_text("fx_eur_usd_rates.odcs.yaml"),
    )
    _put(tmp_path, "a_wrong_name.odcs.yaml", _demo_text())
    _put(tmp_path, "z_repeated.odcs.yaml", "id: a\nid: b\n")
    _put(tmp_path, "ignored.yaml", "not a contract file")
    return tmp_path


def test_validate_passes_the_demo_folder(capsys, caplog):
    assert main(["validate", str(DEMO), "--service", "service-py"]) == 0
    assert capsys.readouterr().out == ""
    assert _errors(caplog) == []


def test_validate_reports_every_issue_as_json(bad_folder, capsys, caplog):
    assert main(["validate", str(bad_folder), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    wrong, valid, repeated = (
        bad_folder / "a_wrong_name.odcs.yaml",
        bad_folder / "fx_eur_usd_rates.odcs.yaml",
        bad_folder / "z_repeated.odcs.yaml",
    )
    # The files come in name order, and a valid file is listed with no issues.
    assert [file["path"] for file in report["files"]] == [
        wrong.as_posix(),
        valid.as_posix(),
        repeated.as_posix(),
    ]
    by_path = {file["path"]: file["issues"] for file in report["files"]}
    assert [(i["path"], i["reason"]) for i in by_path[wrong.as_posix()]] == [
        ("$", "file_name")
    ]
    assert by_path[valid.as_posix()] == []
    (strict,) = by_path[repeated.as_posix()]
    assert (strict["path"], strict["reason"]) == ("$", "strict_yaml")
    assert strict["message"].startswith("line 2, column 1")
    assert set(strict) == {"path", "reason", "message"}
    assert _errors(caplog) == [
        f"{wrong.as_posix()}: $: {by_path[wrong.as_posix()][0]['message']}",
        f"{repeated.as_posix()}: $: {strict['message']}",
    ]


def test_validate_logs_each_issue_without_json(bad_folder, capsys, caplog):
    assert main(["validate", str(bad_folder)]) == 1
    assert capsys.readouterr().out == ""
    assert len(_errors(caplog)) == 2


def test_validate_reads_a_file_and_a_folder_together(tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    _put(other, "py_daily_kpis.odcs.yaml", _demo_text())
    assert (
        main(
            [
                "validate",
                str(DEMO / "demo_orders_csv.odcs.yaml"),
                str(other),
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert [file["path"] for file in report["files"]] == [
        (DEMO / "demo_orders_csv.odcs.yaml").as_posix(),
        (other / "py_daily_kpis.odcs.yaml").as_posix(),
    ]


def test_validate_checks_the_service_only_when_it_is_given(
    tmp_path, capsys, caplog
):
    text = _demo_text().replace(
        "customProperties:\n",
        "customProperties:\n  - {vendor: continuo, property: service, value: other-service}\n",
    )
    _put(tmp_path, "py_daily_kpis.odcs.yaml", text)
    assert main(["validate", str(tmp_path)]) == 0
    assert (
        main(["validate", str(tmp_path), "--service", "service-py", "--json"])
        == 1
    )
    issues = json.loads(capsys.readouterr().out)["files"][0]["issues"]
    assert [issue["reason"] for issue in issues] == ["service"]


def test_validate_reads_the_text_as_written(tmp_path):
    # A byte order mark and CRLF line endings are a valid file.
    text = "\ufeff" + _demo_text().replace("\n", "\r\n")
    _put(tmp_path, "py_daily_kpis.odcs.yaml", text)
    assert main(["validate", str(tmp_path), "--service", "service-py"]) == 0


def test_validate_refuses_an_empty_folder(tmp_path, capsys, caplog):
    assert main(["validate", str(tmp_path)]) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert tmp_path.as_posix() in message and "*.odcs.yaml" in message


def test_validate_does_not_read_a_folder_below_the_one_it_is_given(
    tmp_path, caplog
):
    below = tmp_path / "below"
    below.mkdir()
    _put(below, "py_daily_kpis.odcs.yaml", _demo_text())
    assert main(["validate", str(tmp_path)]) == 2


def test_validate_refuses_a_missing_path(tmp_path, caplog):
    assert main(["validate", str(tmp_path / "missing.odcs.yaml")]) == 2
    assert len(_errors(caplog)) == 1


def test_validate_refuses_a_file_that_is_not_named_odcs_yaml(tmp_path, caplog):
    path = _put(tmp_path, "py_daily_kpis.yaml", _demo_text())
    assert main(["validate", str(path)]) == 2
    assert "*.odcs.yaml" in _errors(caplog)[0]


def test_one_bad_path_stops_the_check_of_every_path(tmp_path, capsys, caplog):
    assert (
        main(
            ["validate", str(DEMO), str(tmp_path / "missing.odcs.yaml"), "--json"]
        )
        == 2
    )
    assert capsys.readouterr().out == ""


def test_validate_refuses_a_file_that_is_not_utf_8(tmp_path, capsys, caplog):
    path = tmp_path / "py_daily_kpis.odcs.yaml"
    path.write_bytes(b"id: \xff\xfe\n")
    assert main(["validate", str(path), "--json"]) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert path.as_posix() in message and "UTF-8" in message


def test_validate_refuses_a_file_it_cannot_read(tmp_path, monkeypatch, caplog):
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())

    def refuse(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert main(["validate", str(path)]) == 2
    assert "Permission denied" in _errors(caplog)[0]


def _without_access(folder, mode):
    """Set `mode` on `folder` and skip the test when the system still lets the process look into
    it, as it does for root and on file systems that ignore the mode."""
    folder.chmod(mode)
    try:
        next(folder.iterdir()).stat()
    except PermissionError:
        return
    folder.chmod(0o755)
    pytest.skip("the system ignores the folder mode for this process")


@pytest.fixture
def locked(tmp_path):
    """A folder without read or search permission that holds a contract file."""
    folder = tmp_path / "locked"
    folder.mkdir()
    _put(folder, "py_daily_kpis.odcs.yaml", _demo_text())
    _without_access(folder, 0o000)
    yield folder
    folder.chmod(0o755)


@pytest.fixture
def unsearchable(tmp_path):
    """A folder that lists its contract file but lets no one look the file up."""
    folder = tmp_path / "unsearchable"
    folder.mkdir()
    _put(folder, "py_daily_kpis.odcs.yaml", _demo_text())
    _without_access(folder, 0o444)
    yield folder
    folder.chmod(0o755)


def _argv(command, path, *flags):
    service = ["--service", "service-py"] if command == "hash" else []
    return [command, str(path), *service, *flags]


@pytest.mark.parametrize("flags", [[], ["--json"]])
@pytest.mark.parametrize("command", ["validate", "hash"])
def test_a_file_below_a_folder_without_access_is_a_usage_error(
    command, flags, locked, capsys, caplog
):
    path = locked / "py_daily_kpis.odcs.yaml"
    assert main(_argv(command, path, *flags)) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot look up the path: ")
    assert "Permission denied" in message


def test_validate_refuses_a_folder_it_cannot_read(locked, capsys, caplog):
    assert main(["validate", str(locked), "--json"]) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{locked.as_posix()}: cannot read the folder: ")
    assert "Permission denied" in message


def test_hash_refuses_a_folder_it_cannot_read_as_a_folder(locked, capsys, caplog):
    assert main(["hash", str(locked), "--service", "service-py"]) == 2
    assert capsys.readouterr().out == ""
    assert _errors(caplog) == [
        f"{locked.as_posix()}: hash takes one contract file, not a folder"
    ]


def test_validate_refuses_a_listed_file_it_cannot_look_up(
    unsearchable, capsys, caplog
):
    path = unsearchable / "py_daily_kpis.odcs.yaml"
    assert main(["validate", str(unsearchable), "--json"]) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot look up the path: ")


def test_a_lookup_error_is_named_with_the_other_bad_paths(
    locked, tmp_path, capsys, caplog
):
    inside = locked / "py_daily_kpis.odcs.yaml"
    missing = tmp_path / "missing.odcs.yaml"
    assert main(["validate", str(inside), str(DEMO), str(missing)]) == 2
    assert capsys.readouterr().out == ""
    assert [m.split(": ")[0] for m in _errors(caplog)] == [
        inside.as_posix(),
        missing.as_posix(),
    ]


@pytest.mark.parametrize("command", ["validate", "hash"])
def test_a_path_with_a_nul_character_is_a_usage_error(
    command, tmp_path, capsys, caplog
):
    path = tmp_path / "py_daily\0kpis.odcs.yaml"
    assert main(_argv(command, path)) == 2
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot look up the path: ")
    assert "null" in message


def test_validate_reads_a_hidden_contract_file_of_a_folder(tmp_path, capsys):
    _put(tmp_path, ".py_daily_kpis.odcs.yaml", _demo_text())
    _put(tmp_path, "py_daily_kpis.ODCS.YAML", _demo_text())
    assert main(["validate", str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [file["path"] for file in report["files"]] == [
        (tmp_path / ".py_daily_kpis.odcs.yaml").as_posix()
    ]


@pytest.mark.parametrize(
    "argv", [[], ["validate"], ["hash"], ["hash", "x.odcs.yaml"]]
)
def test_a_usage_error_exits_2_through_argparse(argv, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(argv)
    assert exit_.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("service", ["", " ", "\t"])
@pytest.mark.parametrize("command", ["validate", "hash"])
def test_an_empty_or_blank_service_is_a_usage_error(
    command, service, tmp_path, capsys
):
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())
    with pytest.raises(SystemExit) as exit_:
        main([command, str(path), "--service", service])
    assert exit_.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--service" in captured.err and "empty or blank" in captured.err


def test_hash_prints_the_vector_hash(tmp_path, capsys):
    case = _case("demo_py_daily_kpis")
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", case["text"])
    argv = ["hash", str(path), "--service", case["service"]]
    for table in case["reads"]:
        argv += ["--read", table]
    assert main(argv) == 0
    assert capsys.readouterr().out == case["contract_hash"] + "\n"


def test_hash_json_prints_the_stored_form_and_the_hash(tmp_path, capsys):
    case = _case("demo_py_daily_kpis")
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", case["text"])
    argv = ["hash", str(path), "--service", case["service"], "--json"]
    assert main([*argv, "--read", "analytics.daily_transactions"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "contract_hash": case["contract_hash"],
        "stored": case["stored"],
    }


def test_hash_without_a_read_hashes_a_stored_form_with_no_reads(tmp_path, capsys):
    case = _case("demo_py_daily_kpis")
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", case["text"])
    assert main(["hash", str(path), "--service", case["service"], "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["contract_hash"] != case["contract_hash"]
    assert "customProperties" not in printed["stored"]["schema"][0]


def test_hash_a_messy_spelling_prints_the_hash_of_the_clean_file(
    tmp_path, capsys
):
    clean, messy = _case("full_python_api"), _case("messy_python_api")
    path = _put(tmp_path, Path(messy["path"]).name, messy["text"])
    argv = ["hash", str(path), "--service", messy["service"]]
    for table in messy["reads"]:
        argv += ["--read", table]
    assert main(argv) == 0
    assert capsys.readouterr().out == clean["contract_hash"] + "\n"


def test_hash_exits_1_for_an_invalid_file_and_prints_nothing(
    tmp_path, capsys, caplog
):
    path = _put(tmp_path, "wrong_name.odcs.yaml", _demo_text())
    for flags in ([], ["--json"]):
        assert main(["hash", str(path), "--service", "service-py", *flags]) == 1
        assert capsys.readouterr().out == ""
    assert len(_errors(caplog)) == 2
    assert _errors(caplog)[0].startswith(f"{path.as_posix()}: $: ")


def test_hash_reports_a_strict_yaml_error_as_an_issue(tmp_path, capsys, caplog):
    path = _put(tmp_path, "x.odcs.yaml", "id: a\nid: b\n")
    assert main(["hash", str(path), "--service", "service-py"]) == 1
    assert "line 2, column 1" in _errors(caplog)[0]


def test_hash_refuses_a_read_value_that_is_not_a_table(tmp_path, capsys, caplog):
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())
    assert (
        main(["hash", str(path), "--service", "service-py", "--read", "daily"])
        == 2
    )
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert "'daily'" in message and "<schema>.<table>" in message


@pytest.mark.parametrize(
    "table", ["db.analytics.x", "analytics. x", "analytics.x\x00"]
)
def test_hash_refuses_a_read_value_that_is_not_exactly_a_schema_and_a_table(
    tmp_path, capsys, caplog, table
):
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())
    assert (
        main(["hash", str(path), "--service", "service-py", "--read", table]) == 2
    )
    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert "exactly two parts" in message


def test_hash_refuses_a_path_that_is_not_a_contract_file(tmp_path, caplog):
    unnamed = _put(tmp_path, "py_daily_kpis.yaml", _demo_text())
    for target in (tmp_path / "missing.odcs.yaml", unnamed, tmp_path):
        caplog.clear()
        assert main(["hash", str(target), "--service", "service-py"]) == 2, target
        assert len(_errors(caplog)) == 1, target


def test_hash_refuses_a_file_that_is_not_utf_8(tmp_path, caplog):
    path = tmp_path / "py_daily_kpis.odcs.yaml"
    path.write_bytes(b"\x80\n")
    assert main(["hash", str(path), "--service", "service-py"]) == 2
    assert "UTF-8" in _errors(caplog)[0]


@pytest.mark.parametrize("command", ["validate", "hash"])
def test_both_commands_name_the_family_they_check(command, tmp_path, caplog):
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())
    argv = [command, str(path)] + (
        ["--service", "service-py"] if command == "hash" else []
    )
    assert main(argv) == 0
    assert FAMILY_LINE in _infos(caplog)
    caplog.clear()
    assert main([*argv, "--json"]) == 0
    assert FAMILY_LINE in _infos(caplog)


def test_validate_logs_how_many_files_it_checked(bad_folder, caplog):
    assert main(["validate", str(bad_folder)]) == 1
    assert "3 contracts checked, 2 with issues" in _infos(caplog)
    caplog.clear()
    assert main(["validate", str(DEMO / "demo_orders_csv.odcs.yaml")]) == 0
    assert "1 contract checked, 0 with issues" in _infos(caplog)


def test_json_mode_prints_only_json_on_stdout(tmp_path, capsys):
    valid = _put(tmp_path, "py_daily_kpis.odcs.yaml", _demo_text())
    invalid = _put(tmp_path, "wrong_name.odcs.yaml", _demo_text())
    for argv, status in (
        (["validate", str(valid), "--json"], 0),
        (["validate", str(invalid), "--json"], 1),
        (["hash", str(valid), "--service", "service-py", "--json"], 0),
    ):
        assert main(argv) == status, argv
        document = json.loads(capsys.readouterr().out)
        assert isinstance(document, dict), argv


def _run(*args):
    """Run `main` in a new interpreter and return the finished process."""
    code = "import sys; from continuo_contracts.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_log_goes_to_stderr_and_stdout_stays_machine_readable(tmp_path):
    case = _case("demo_py_daily_kpis")
    path = _put(tmp_path, "py_daily_kpis.odcs.yaml", case["text"])
    done = _run(
        "hash",
        str(path),
        "--service",
        case["service"],
        "--read",
        "analytics.daily_transactions",
    )
    assert done.returncode == 0
    assert done.stdout == case["contract_hash"] + "\n"
    assert done.stderr == f"INFO {FAMILY_LINE}\n"
    failed = _run("validate", str(tmp_path / "nothing"), "--json")
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert failed.stderr.splitlines()[0] == f"INFO {FAMILY_LINE}"
    assert failed.stderr.splitlines()[1].startswith("ERROR ")


def test_importing_the_module_configures_no_logging():
    code = (
        "import logging; import continuo_contracts.cli; "
        "assert logging.getLogger().handlers == [], logging.getLogger().handlers"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def test_the_console_script_is_declared():
    scripts = importlib.metadata.entry_points(group="console_scripts")
    (entry,) = [entry for entry in scripts if entry.name == "continuo-contracts"]
    assert entry.value == "continuo_contracts.cli:main"
    assert entry.load() is cli.main
