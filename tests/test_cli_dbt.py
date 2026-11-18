"""`continuo-contracts dbt check`: the producer's lite check of a dbt project, from the terminal."""

import copy
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

import pytest

from dbt_fixtures import MANIFESTS, load_manifest

from continuo_contracts import lite
from continuo_contracts.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_DBT = FIXTURES / "fake_dbt.py"
FINANCE = FIXTURES / "dbt" / "projects" / "finance"
FINANCE_MANIFEST = MANIFESTS / "finance.json.gz"
FAMILY_LINE = "this release checks Python contracts"
PLACEHOLDER = "continuo-lite-check"

FX_SEED = "seed.finance.seed_fx_rates_eur"
COSTS_SEED = "seed.finance.seed_operational_costs"
# The columns of the CSV of `seed_operational_costs` that the project does not declare.
UNDECLARED = ("cost_date", "category", "subcategory", "cost_type")
UNREADABLE = (
    "the CSV file cannot be read, so its header is not checked against the "
    "declared columns"
)
ALL_SKIPPED = (
    "every node of the service is skipped: the release rejects it as "
    "invalid_artifact (manifest contains no model/seed nodes)"
)


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def _infos(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """The folder where the check makes its temporary folder, so a test can see it is empty."""
    folder = tmp_path / "scratch"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


class FakeDbt:
    """The fake `dbt` of one test: where it is, what it does and what it recorded.

    The executable is a wrapper that starts `fake_dbt.py` with this interpreter. What the fake
    does comes from environment variables, which the check passes on to it.
    """

    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        folder.mkdir()
        self.folder = folder
        self.executable = folder / "dbt"
        self.executable.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_DBT))} "$@"\n',
            encoding="utf-8",
        )
        self.executable.chmod(0o755)
        self.record_file = folder / "record.json"
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("FAKE_DBT_RECORD", str(self.record_file))
        self.configure(manifest=FINANCE_MANIFEST)

    def configure(self, *, manifest=None, status=0, stdout="", stderr=""):
        """Choose what the fake writes and with what status it exits."""
        set_ = self._monkeypatch.setenv
        if manifest is None:
            self._monkeypatch.delenv("FAKE_DBT_MANIFEST", raising=False)
        else:
            set_("FAKE_DBT_MANIFEST", str(manifest))
        set_("FAKE_DBT_STATUS", str(status))
        set_("FAKE_DBT_STDOUT", stdout)
        set_("FAKE_DBT_STDERR", stderr)

    @property
    def ran(self):
        return self.record_file.exists()

    @property
    def record(self):
        return json.loads(self.record_file.read_text(encoding="utf-8"))

    @property
    def target(self):
        """The `--target-path` the check passed to the fake."""
        argv = self.record["argv"]
        return Path(argv[argv.index("--target-path") + 1])


@pytest.fixture
def fake_dbt(tmp_path, monkeypatch):
    return FakeDbt(tmp_path / "fake-bin", monkeypatch)


def _check(project, *flags):
    return main(["dbt", "check", str(project), *map(str, flags)])


def _write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _copy_project(tmp_path, name="finance"):
    """A copy of the finance fixture project that a test may change."""
    project = tmp_path / name
    shutil.copytree(FINANCE, project)
    return project


def _without_costs_seed():
    """The finance manifest without the seed whose CSV has columns the project leaves out."""
    manifest = copy.deepcopy(load_manifest("finance"))
    del manifest["nodes"][COSTS_SEED]
    return manifest


def _lines(document, node_id, level="error"):
    return [
        line["message"]
        for line in document["lines"]
        if line["node_id"] == node_id and line["level"] == level
    ]


def test_check_runs_dbt_parse_with_the_project_and_profiles_dirs(fake_dbt):
    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    argv = fake_dbt.record["argv"]
    target = fake_dbt.target
    assert argv == [
        "--write-json",
        "--log-path",
        str(target),
        "parse",
        "--project-dir",
        str(FINANCE),
        "--profiles-dir",
        str(FINANCE),
        "--target-path",
        str(target),
    ]
    # The check makes the target folder, away from the project, before dbt runs.
    assert target.is_absolute()
    assert fake_dbt.record["target_exists"] is True
    assert FINANCE not in target.parents


def test_the_profiles_dir_and_the_target_are_passed_to_dbt(fake_dbt, tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    assert (
        _check(
            FINANCE,
            "--dbt",
            fake_dbt.executable,
            "--profiles-dir",
            profiles,
            "--target",
            "ci",
        )
        == 1
    )

    assert fake_dbt.record["argv"] == [
        "--write-json",
        "--log-path",
        str(fake_dbt.target),
        "parse",
        "--project-dir",
        str(FINANCE),
        "--profiles-dir",
        str(profiles),
        "--target-path",
        str(fake_dbt.target),
        "--target",
        "ci",
    ]


def test_dbt_is_found_on_the_path_when_dbt_is_not_given(fake_dbt, monkeypatch):
    monkeypatch.setenv("PATH", str(fake_dbt.folder))

    assert _check(FINANCE) == 1

    assert fake_dbt.record["argv"][:4] == [
        "--write-json",
        "--log-path",
        str(fake_dbt.target),
        "parse",
    ]


@pytest.mark.parametrize("name", ["DBT_WRITE_JSON", "DBT_ENGINE_WRITE_JSON"])
def test_a_caller_that_turns_dbts_json_files_off_still_gets_the_manifest(
    fake_dbt, monkeypatch, capsys, name
):
    monkeypatch.setenv(name, "false")

    assert _check(FINANCE, "--dbt", fake_dbt.executable, "--json") == 1

    document = json.loads(capsys.readouterr().out)
    assert document["lite"] is True
    assert document["contracts"]
    # The flag did it, not a change of the environment: dbt still gets the caller's variable.
    assert fake_dbt.record["environ"][name] == "false"


def test_dbt_writes_its_log_in_the_temporary_folder_and_not_in_the_project(
    fake_dbt, tmp_path, scratch
):
    project = _copy_project(tmp_path)
    shutil.rmtree(project / "logs", ignore_errors=True)

    assert _check(project, "--dbt", fake_dbt.executable) == 1

    argv = fake_dbt.record["argv"]
    assert argv[argv.index("--log-path") + 1] == str(fake_dbt.target)
    assert not (project / "logs").exists()
    assert list(scratch.iterdir()) == []


def test_a_relative_project_folder_is_passed_to_dbt_as_given(
    fake_dbt, monkeypatch, tmp_path
):
    shutil.copytree(FINANCE, tmp_path / "finance")
    monkeypatch.chdir(tmp_path)

    assert _check("finance", "--dbt", fake_dbt.executable) == 1

    argv = fake_dbt.record["argv"]
    assert argv[argv.index("--project-dir") + 1] == "finance"
    assert argv[argv.index("--profiles-dir") + 1] == "finance"


def _profiles(project, text):
    (project / "profiles.yml").write_text(text, encoding="utf-8")


def test_an_unset_env_var_without_default_gets_the_placeholder(
    fake_dbt, tmp_path, monkeypatch
):
    project = _copy_project(tmp_path)
    _profiles(
        project,
        "default:\n  outputs:\n    dev:\n"
        "      host: \"{{ env_var('WITHOUT_DEFAULT_A') }}\"\n"
        "      user: '{{ env_var(\"WITHOUT_DEFAULT_B\") }}'\n"
        "      password: \"{{ env_var('WITH_DEFAULT_C', 'secret') }}\"\n"
        "      dbname: \"{{ env_var('ALREADY_SET_D') }}\"\n",
    )
    for name in ("WITHOUT_DEFAULT_A", "WITHOUT_DEFAULT_B", "WITH_DEFAULT_C"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALREADY_SET_D", "the real value")
    monkeypatch.setenv("UNRELATED_E", "left as it is")

    assert _check(project, "--dbt", fake_dbt.executable) == 1

    environ = fake_dbt.record["environ"]
    assert environ["WITHOUT_DEFAULT_A"] == PLACEHOLDER
    assert environ["WITHOUT_DEFAULT_B"] == PLACEHOLDER
    assert "WITH_DEFAULT_C" not in environ
    # A variable that is set keeps its value, and so does every other variable.
    assert environ["ALREADY_SET_D"] == "the real value"
    assert environ["UNRELATED_E"] == "left as it is"
    assert environ["PATH"] == os.environ["PATH"]
    # The check changes the environment of dbt and not its own.
    assert "WITHOUT_DEFAULT_A" not in os.environ


@pytest.mark.parametrize(
    ("call", "placeholder"),
    [
        ("env_var('PROBE')", True),
        ('env_var("PROBE")', True),
        ("env_var(  'PROBE'  )", True),
        ('env_var(\n  "PROBE"\n)', True),
        ("env_var('PROBE') | int", True),
        ("env_var('PROBE', 'x')", False),
        ('env_var("PROBE", "x")', False),
        ("env_var('PROBE', '')", False),
        ("env_var('PROBE',5432)", False),
        ("env_var(\n  'PROBE',\n  'x'\n)", False),
    ],
)
def test_the_placeholder_is_for_each_form_of_env_var_without_a_default(
    call, placeholder, fake_dbt, tmp_path, monkeypatch
):
    project = _copy_project(tmp_path)
    _profiles(project, 'host: "{{ ' + call + ' }}"\n')
    monkeypatch.delenv("PROBE", raising=False)

    _check(project, "--dbt", fake_dbt.executable)

    assert fake_dbt.record["environ"].get("PROBE") == (
        PLACEHOLDER if placeholder else None
    )


def test_a_name_that_is_read_with_and_without_a_default_gets_the_placeholder(
    fake_dbt, tmp_path, monkeypatch
):
    project = _copy_project(tmp_path)
    _profiles(
        project,
        "a: \"{{ env_var('BOTH', 'x') }}\"\nb: \"{{ env_var('BOTH') }}\"\n",
    )
    monkeypatch.delenv("BOTH", raising=False)

    _check(project, "--dbt", fake_dbt.executable)

    assert fake_dbt.record["environ"]["BOTH"] == PLACEHOLDER


def test_the_profiles_file_is_read_from_the_profiles_dir_when_it_is_given(
    fake_dbt, tmp_path, monkeypatch
):
    project = _copy_project(tmp_path)
    _profiles(project, "host: \"{{ env_var('IN_THE_PROJECT') }}\"\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    _profiles(profiles, "host: \"{{ env_var('IN_THE_PROFILES_DIR') }}\"\n")
    for name in ("IN_THE_PROJECT", "IN_THE_PROFILES_DIR"):
        monkeypatch.delenv(name, raising=False)

    _check(project, "--dbt", fake_dbt.executable, "--profiles-dir", profiles)

    environ = fake_dbt.record["environ"]
    assert environ["IN_THE_PROFILES_DIR"] == PLACEHOLDER
    assert "IN_THE_PROJECT" not in environ


@pytest.mark.parametrize("kind", ["missing", "a folder", "not utf-8"])
def test_a_profiles_file_that_cannot_be_read_sets_no_placeholder(
    kind, fake_dbt, tmp_path, monkeypatch
):
    project = _copy_project(tmp_path)
    (project / "profiles.yml").unlink(missing_ok=True)
    if kind == "a folder":
        (project / "profiles.yml").mkdir()
    elif kind == "not utf-8":
        (project / "profiles.yml").write_bytes(
            b"host: \xff env_var('NOT_READ')\n"
        )
    monkeypatch.delenv("NOT_READ", raising=False)

    # dbt runs and says what is wrong with the profile, so the check does not.
    assert _check(project, "--dbt", fake_dbt.executable) == 1

    assert fake_dbt.ran
    assert "NOT_READ" not in fake_dbt.record["environ"]


def test_a_failing_dbt_parse_exits_one_and_logs_its_output(
    fake_dbt, capsys, caplog, scratch
):
    out = "".join(f"out {n}\n" for n in range(1, 26))
    fake_dbt.configure(status=2, stdout=out, stderr="boom\nlast\n")

    assert _check(FINANCE, "--dbt", fake_dbt.executable, "--json") == 1

    assert capsys.readouterr().out == ""
    # The last 20 lines of the output, the error stream among them, in the order dbt wrote them.
    lines = [*(f"out {n}" for n in range(1, 26)), "boom", "last"]
    assert _errors(caplog) == ["dbt parse exited with status 2", *lines[-20:]]
    assert _infos(caplog) == []
    assert list(scratch.iterdir()) == []


def test_a_failing_dbt_parse_with_little_output_logs_all_of_it(fake_dbt, caplog):
    fake_dbt.configure(status=1, stderr="Could not find profile\n")

    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    assert _errors(caplog) == [
        "dbt parse exited with status 1",
        "Could not find profile",
    ]


def test_a_failing_dbt_parse_with_no_output_logs_its_status(fake_dbt, caplog):
    fake_dbt.configure(status=3)

    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    assert _errors(caplog) == ["dbt parse exited with status 3"]


def test_the_colour_codes_of_dbt_are_left_out_of_the_log(fake_dbt, caplog):
    red, reset = "\x1b[31m", "\x1b[0m"
    fake_dbt.configure(
        status=2,
        stdout=f"{reset}18:11:55  [{red}ERROR{reset}]: Encountered an error:\n",
        stderr=f"{red}1;2m{reset} stays\n",
    )

    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    assert _errors(caplog) == [
        "dbt parse exited with status 2",
        "18:11:55  [ERROR]: Encountered an error:",
        "1;2m stays",
    ]


def test_output_that_is_not_utf_8_is_logged_with_a_replacement(fake_dbt, caplog):
    fake_dbt.configure(status=1, stdout="caf\udce9\n")

    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    assert _errors(caplog)[-1].startswith("caf")


def test_dbt_parse_that_writes_no_manifest_is_a_usage_error(
    fake_dbt, capsys, caplog, scratch
):
    fake_dbt.configure(manifest=None)

    assert _check(FINANCE, "--dbt", fake_dbt.executable, "--json") == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    target = fake_dbt.target
    assert message.startswith(
        f"{(target / 'manifest.json').as_posix()}: cannot read the manifest: "
    )
    assert list(scratch.iterdir()) == []


def test_the_temporary_folder_is_removed_when_the_check_passes(
    fake_dbt, tmp_path, scratch
):
    manifest = _write_json(tmp_path / "ok.json", _without_costs_seed())
    fake_dbt.configure(manifest=manifest)

    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 0

    assert fake_dbt.target.parent == scratch
    assert list(scratch.iterdir()) == []


def test_the_temporary_folder_is_removed_when_the_check_finds_an_error(
    fake_dbt, scratch
):
    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    assert fake_dbt.target.parent == scratch
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    "dbt",
    [
        "missing",
        "a folder",
        "not executable",
        "executable but not a program",
    ],
)
def test_no_dbt_executable_exits_two(dbt, tmp_path, capsys, caplog, scratch):
    path = tmp_path / "dbt"
    if dbt == "a folder":
        path.mkdir()
    elif dbt != "missing":
        path.write_text("not a program\n", encoding="utf-8")
        path.chmod(0o755 if dbt.startswith("executable") else 0o644)

    assert _check(FINANCE, "--dbt", path, "--json") == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert str(path) in message and "dbt" in message
    assert list(scratch.iterdir()) == []


def test_no_dbt_on_the_path_exits_two(tmp_path, monkeypatch, capsys, caplog):
    nothing = tmp_path / "nothing"
    nothing.mkdir()
    monkeypatch.setenv("PATH", str(nothing))

    assert _check(FINANCE) == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert "no dbt executable" in message and "--dbt" in message


def test_the_banner_is_printed_first_and_last(fake_dbt, caplog):
    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1

    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert records[0] == (logging.INFO, lite.BANNER)
    assert records[-1] == (logging.INFO, lite.BANNER)
    assert [m for _, m in records].count(lite.BANNER) == 2
    # The table of what only the release does sits between the lines and the closing banner.
    table = records[-1 - len(lite.RELEASE_ONLY) : -1]
    assert table == [
        (logging.INFO, f"  {what}: {why}") for what, why in lite.RELEASE_ONLY
    ]
    assert records[-2 - len(lite.RELEASE_ONLY)] == (
        logging.INFO,
        "What only the release does:",
    )


def test_the_report_logs_each_line_at_its_level(fake_dbt, caplog):
    _check(FINANCE, "--dbt", fake_dbt.executable)

    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (
        logging.ERROR,
        f"{COSTS_SEED}: cost_date is in the CSV header but not declared",
    ) in records
    assert [m for lv, m in records if lv == logging.ERROR] == [
        f"{COSTS_SEED}: {column} is in the CSV header but not declared"
        for column in UNDECLARED
    ]


def test_an_information_line_is_logged_at_info(tmp_path, caplog):
    manifest = _without_costs_seed()
    manifest["nodes"]["model.finance.ltv_per_user"]["config"]["meta"]["owner"] = (
        ""
    )
    path = _write_json(tmp_path / "manifest.json", manifest)

    assert _check(FINANCE, "--manifest", path) == 0

    assert (
        logging.INFO,
        "model.finance.ltv_per_user: no owner, continuo skips it",
    ) in [(r.levelno, r.getMessage()) for r in caplog.records]
    assert _errors(caplog) == []


def test_a_line_about_the_whole_service_is_logged_without_a_node(
    tmp_path, caplog
):
    path = _write_json(tmp_path / "manifest.json", {"nodes": {}})

    assert _check(FINANCE, "--manifest", path) == 1

    assert _errors(caplog) == [ALL_SKIPPED]


def test_the_family_line_of_the_python_commands_is_not_logged(fake_dbt, caplog):
    _check(FINANCE, "--dbt", fake_dbt.executable)

    assert FAMILY_LINE not in _infos(caplog)


def test_json_prints_one_document_and_nothing_else(fake_dbt, capsys, caplog):
    assert _check(FINANCE, "--dbt", fake_dbt.executable, "--json") == 1

    out = capsys.readouterr().out
    document = json.loads(out)
    assert out == json.dumps(document, indent=2) + "\n"
    assert list(document) == ["lite", "ok", "service", "lines", "contracts"]
    assert document["lite"] is True
    assert document["ok"] is False
    assert document["service"] == "finance"
    errors = [line for line in document["lines"] if line["level"] == "error"]
    assert [(line["node_id"], line["message"]) for line in errors] == [
        (COSTS_SEED, f"{column} is in the CSV header but not declared")
        for column in UNDECLARED
    ]
    assert all(
        set(line) == {"level", "node_id", "message"} for line in document["lines"]
    )
    # The contracts are the ones the lite check hashes, by contract id, and a seed whose header
    # differs still has its hash, because its contract is valid.
    expected = lite.check_manifest(
        load_manifest("finance"),
        declared_service="finance",
        read_header=lambda node: None,
    ).contracts
    assert document["contracts"] == dict(expected)
    assert "analytics.fx_transactions_eur" in document["contracts"]
    assert all(h.startswith("sha256:") for h in document["contracts"].values())
    # The log goes on, to stderr.
    assert lite.BANNER in _infos(caplog)


def test_json_says_ok_when_the_check_finds_no_error(tmp_path, capsys):
    path = _write_json(tmp_path / "manifest.json", _without_costs_seed())

    assert _check(FINANCE, "--manifest", path, "--json") == 0

    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert [line for line in document["lines"] if line["level"] == "error"] == []


def test_nothing_is_printed_on_stdout_without_json(tmp_path, fake_dbt, capsys):
    path = _write_json(tmp_path / "manifest.json", _without_costs_seed())

    assert _check(FINANCE, "--manifest", path) == 0
    assert capsys.readouterr().out == ""
    assert _check(FINANCE, "--dbt", fake_dbt.executable) == 1
    assert capsys.readouterr().out == ""


def _run(*args):
    """Run `main` in a new interpreter and return the finished process."""
    code = "import sys; from continuo_contracts.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_new_process_writes_the_log_to_stderr_and_only_the_document_to_stdout(
    tmp_path,
):
    path = _write_json(tmp_path / "manifest.json", load_manifest("finance"))

    plain = _run("dbt", "check", str(FINANCE), "--manifest", str(path))
    machine = _run(
        "dbt", "check", str(FINANCE), "--manifest", str(path), "--json"
    )

    assert plain.returncode == machine.returncode == 1
    assert plain.stdout == ""
    document = json.loads(machine.stdout)
    assert machine.stdout == json.dumps(document, indent=2) + "\n"
    for done in (plain, machine):
        lines = done.stderr.splitlines()
        assert lines[0] == f"INFO {lite.BANNER}"
        assert lines[-1] == f"INFO {lite.BANNER}"
        assert (
            f"ERROR {COSTS_SEED}: cost_date is in the CSV header but not declared"
            in lines
        )
    assert plain.stderr == machine.stderr


def test_the_command_does_not_import_dbt():
    code = (
        "import sys; import continuo_contracts.cli; "
        "found = [n for n in sys.modules if n == 'dbt' or n.startswith('dbt.')]; "
        "assert not found, found"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr


def test_manifest_skips_running_dbt(fake_dbt, tmp_path, caplog):
    path = _write_json(tmp_path / "manifest.json", load_manifest("finance"))
    missing = tmp_path / "no-dbt-here"

    # With no `--dbt`, an executable that is not there and a profile that is not there.
    status = _check(
        FINANCE,
        "--manifest",
        path,
        "--dbt",
        missing,
        "--profiles-dir",
        tmp_path / "no-profiles-here",
        "--target",
        "nothing",
    )

    assert status == 1
    assert not fake_dbt.ran
    assert _errors(caplog) == [
        f"{COSTS_SEED}: {column} is in the CSV header but not declared"
        for column in UNDECLARED
    ]


def test_manifest_needs_no_dbt_on_the_path(tmp_path, monkeypatch):
    path = _write_json(tmp_path / "manifest.json", _without_costs_seed())
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _check(FINANCE, "--manifest", path) == 0


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"nodes": {}},
        {"nodes": None},
        {"metadata": {"project_name": "finance"}},
    ],
)
def test_a_manifest_without_nodes_has_no_contract_and_exits_one(
    manifest, tmp_path, capsys, caplog
):
    path = _write_json(tmp_path / "manifest.json", manifest)

    assert _check(FINANCE, "--manifest", path, "--json") == 1

    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["contracts"] == {}
    assert document["lines"] == [
        {"level": "error", "node_id": "", "message": ALL_SKIPPED}
    ]
    assert _errors(caplog) == [ALL_SKIPPED]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "Expecting value"),
        (b"{not json", "Expecting property name"),
        (b"\xff\xfe{}", "codec can't decode"),
        (b"[]", "the manifest is not a JSON object"),
        (b'"text"', "the manifest is not a JSON object"),
        (b"null", "the manifest is not a JSON object"),
        (b'{"nodes": []}', "`nodes` is not a JSON object of nodes"),
        (b'{"nodes": 5}', "`nodes` is not a JSON object of nodes"),
        (b'{"nodes": {"model.a.b": 1}}', "`nodes` is not a JSON object of nodes"),
    ],
)
def test_a_manifest_that_is_not_a_dbt_manifest_is_a_usage_error(
    content, reason, tmp_path, capsys, caplog
):
    path = tmp_path / "manifest.json"
    path.write_bytes(content)

    assert _check(FINANCE, "--manifest", path, "--json") == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: ")
    assert reason in message


@pytest.mark.parametrize("kind", ["missing", "a folder"])
def test_a_manifest_that_cannot_be_read_is_a_usage_error(
    kind, tmp_path, capsys, caplog
):
    path = tmp_path / "manifest.json"
    if kind == "a folder":
        path.mkdir()

    assert _check(FINANCE, "--manifest", path, "--json") == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot read the manifest: ")


def test_no_such_folder_exits_two(tmp_path, fake_dbt, capsys, caplog):
    missing = tmp_path / "missing"

    assert _check(missing, "--dbt", fake_dbt.executable, "--json") == 2

    assert capsys.readouterr().out == ""
    assert _errors(caplog) == [f"{missing.as_posix()}: no such folder"]
    assert not fake_dbt.ran


def test_no_such_folder_exits_two_when_a_manifest_is_given_too(
    tmp_path, capsys, caplog
):
    path = _write_json(tmp_path / "manifest.json", load_manifest("finance"))
    missing = tmp_path / "missing"

    assert _check(missing, "--manifest", path, "--json") == 2

    assert capsys.readouterr().out == ""
    assert _errors(caplog) == [f"{missing.as_posix()}: no such folder"]


def test_a_file_is_not_a_project_folder(tmp_path, fake_dbt, caplog):
    path = _write_json(tmp_path / "manifest.json", {})

    assert _check(path, "--dbt", fake_dbt.executable) == 2

    assert _errors(caplog) == [f"{path.as_posix()}: no such folder"]


def test_a_folder_path_with_a_nul_character_is_a_usage_error(
    tmp_path, fake_dbt, capsys, caplog
):
    path = tmp_path / "fin\0ance"

    assert _check(path, "--dbt", fake_dbt.executable) == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot look up the path: ")
    assert not fake_dbt.ran


@pytest.mark.parametrize(
    "argv",
    [
        ["dbt"],
        ["dbt", "check"],
        ["dbt", "check", ".", "--service", " "],
        ["dbt", "check", ".", "--service", ""],
        ["dbt", "other"],
    ],
)
def test_a_usage_error_of_the_arguments_exits_2_through_argparse(argv, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(argv)

    assert exit_.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_help_of_the_program_lists_the_dbt_command(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["--help"])

    assert exit_.value.code == 0
    out = capsys.readouterr().out
    assert "{validate,hash,dbt}" in out
    assert "the producer's lite check of a dbt project" in out


def test_the_help_of_the_command_names_its_flags(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["dbt", "check", "--help"])

    assert exit_.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "PROJECT_DIR",
        "--dbt",
        "--profiles-dir",
        "--target",
        "--manifest",
        "--service",
        "--json",
    ):
        assert flag in out
    assert "exit status" in out and "dbt parse" in out


def test_the_service_is_the_folder_name_with_underscores_written_as_dashes(
    tmp_path, capsys
):
    # A manifest that names no project leaves the declared service to decide it.
    manifest = copy.deepcopy(load_manifest("finance"))
    del manifest["metadata"]["project_name"]
    path = _write_json(tmp_path / "manifest.json", manifest)
    project = tmp_path / "my_finance_service"
    project.mkdir()

    _check(project, "--manifest", path, "--json")

    assert json.loads(capsys.readouterr().out)["service"] == "my-finance-service"


def test_the_service_of_the_current_folder_is_its_name(
    tmp_path, monkeypatch, capsys
):
    manifest = copy.deepcopy(load_manifest("finance"))
    del manifest["metadata"]["project_name"]
    path = _write_json(tmp_path / "manifest.json", manifest)
    project = tmp_path / "a_b"
    project.mkdir()
    monkeypatch.chdir(project)

    _check(".", "--manifest", path, "--json")

    assert json.loads(capsys.readouterr().out)["service"] == "a-b"


def test_service_names_the_declared_service(tmp_path, capsys):
    manifest = copy.deepcopy(load_manifest("finance"))
    del manifest["metadata"]["project_name"]
    path = _write_json(tmp_path / "manifest.json", manifest)

    _check(FINANCE, "--manifest", path, "--service", "other_service", "--json")

    # As given: the gates compare it with the service of each node.
    assert json.loads(capsys.readouterr().out)["service"] == "other_service"


def test_the_service_that_the_manifest_names_wins_over_the_folder(
    tmp_path, capsys
):
    path = _write_json(tmp_path / "manifest.json", load_manifest("finance"))
    project = tmp_path / "other_name"
    project.mkdir()

    _check(project, "--manifest", path, "--service", "declared", "--json")

    assert json.loads(capsys.readouterr().out)["service"] == "finance"


ABSENT = object()


def _seed_project(folder, header_bytes, **config):
    """A project in `folder` whose fx seed has the CSV `header_bytes`, and its manifest.

    Every other seed keeps the CSV of the fixture project. Returns the project and the manifest
    file, whose fx seed has the `config` given here, a key that is `ABSENT` removed from it.
    """
    project = _copy_project(folder)
    (project / "seeds" / "seed_fx_rates_eur.csv").write_bytes(header_bytes)
    manifest = copy.deepcopy(load_manifest("finance"))
    seed_config = manifest["nodes"][FX_SEED]["config"]
    for key, value in config.items():
        if value is ABSENT:
            del seed_config[key]
        else:
            seed_config[key] = value
    return project, _write_json(folder / "manifest.json", manifest)


def _fx_lines(project, manifest, capsys):
    _check(project, "--manifest", manifest, "--json")
    return _lines(json.loads(capsys.readouterr().out), FX_SEED)


def test_a_seed_header_that_matches_its_declared_columns_has_no_line(
    tmp_path, capsys
):
    project, manifest = _seed_project(
        tmp_path, b"currency,rate_date,rate_to_eur\nAUD,2023-02-17,0.6\n"
    )

    assert _fx_lines(project, manifest, capsys) == []


def test_a_header_that_differs_is_reported_column_by_column(tmp_path, capsys):
    project, manifest = _seed_project(
        tmp_path, b"currency,day,rate_to_eur,extra\n"
    )

    assert _fx_lines(project, manifest, capsys) == [
        "day is in the CSV header but not declared",
        "extra is in the CSV header but not declared",
        "rate_date is declared but not in the CSV header",
    ]


def test_a_bom_and_crlf_header_reads_like_a_plain_one(tmp_path, capsys):
    plain_folder, messy_folder = tmp_path / "plain", tmp_path / "messy"
    plain_folder.mkdir()
    messy_folder.mkdir()
    plain, plain_manifest = _seed_project(
        plain_folder, b"currency,rate_date,rate_to_eur\nAUD,2023-02-17,0.6\n"
    )
    messy, messy_manifest = _seed_project(
        messy_folder,
        b"\xef\xbb\xbfcurrency,rate_date,rate_to_eur\r\nAUD,2023-02-17,0.6\r\n",
    )
    # The other seed, whose CSV the two projects share, gets a BOM and CRLF in the messy one.
    costs = messy / "seeds" / "seed_operational_costs.csv"
    costs.write_bytes(
        b"\xef\xbb\xbf" + costs.read_bytes().replace(b"\n", b"\r\n")
    )

    documents = []
    for project, manifest in ((plain, plain_manifest), (messy, messy_manifest)):
        _check(project, "--manifest", manifest, "--json")
        documents.append(json.loads(capsys.readouterr().out))

    assert documents[0] == documents[1]
    assert _lines(documents[1], FX_SEED) == []
    # A BOM left in the header would have made `cost_id`, the first column, a difference.
    assert _lines(documents[1], COSTS_SEED) == [
        f"{column} is in the CSV header but not declared" for column in UNDECLARED
    ]


def test_the_delimiter_of_a_seed_comes_from_its_config(tmp_path, capsys):
    project, manifest = _seed_project(
        tmp_path,
        b"currency;rate_date;rate_to_eur\r\nAUD;2023-02-17;0.6\r\n",
        delimiter=";",
    )

    assert _fx_lines(project, manifest, capsys) == []


@pytest.mark.parametrize("delimiter", [ABSENT, None, ""])
def test_a_seed_with_no_delimiter_in_its_config_is_read_with_a_comma(
    delimiter, tmp_path, capsys
):
    project, manifest = _seed_project(
        tmp_path, b"currency,rate_date,rate_to_eur\n", delimiter=delimiter
    )

    assert _fx_lines(project, manifest, capsys) == []


def test_a_header_read_with_the_wrong_delimiter_is_a_difference(tmp_path, capsys):
    project, manifest = _seed_project(
        tmp_path, b"currency;rate_date;rate_to_eur\n", delimiter=","
    )

    assert _fx_lines(project, manifest, capsys) == [
        "currency;rate_date;rate_to_eur is in the CSV header but not declared",
        "currency is declared but not in the CSV header",
        "rate_date is declared but not in the CSV header",
        "rate_to_eur is declared but not in the CSV header",
    ]


def test_an_empty_csv_file_has_an_empty_header(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, b"")

    assert _fx_lines(project, manifest, capsys) == [
        "currency is declared but not in the CSV header",
        "rate_date is declared but not in the CSV header",
        "rate_to_eur is declared but not in the CSV header",
    ]


@pytest.mark.parametrize(
    ("content", "delimiter"),
    [
        (b"\xff\xfe\x00c\n", None),
        (b"a" * 200_000 + b"\n", None),
        (b"currency;rate_date\n", ";;"),
        (b"currency;rate_date\n", 5),
    ],
    ids=[
        "not utf-8",
        "a field over the csv limit",
        "a long delimiter",
        "a number",
    ],
)
def test_a_csv_file_that_cannot_be_read_gives_the_unreadable_line(
    content, delimiter, tmp_path, capsys
):
    project, manifest = _seed_project(tmp_path, content, delimiter=delimiter)

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_csv_file_that_is_not_there_gives_the_unreadable_line(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, b"")
    (project / "seeds" / "seed_fx_rates_eur.csv").unlink()

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_skipped_seed_has_no_header_check(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, b"", meta={})
    (project / "seeds" / "seed_fx_rates_eur.csv").unlink()

    assert _fx_lines(project, manifest, capsys) == []


def test_the_seeds_are_read_from_the_project_folder(tmp_path, capsys):
    # The project folder that the check is given holds the CSV files, wherever it is run from.
    project = tmp_path / "elsewhere"
    shutil.copytree(FINANCE / "seeds", project / "seeds")
    path = _write_json(tmp_path / "manifest.json", load_manifest("finance"))

    _check(project, "--manifest", path, "--json")

    document = json.loads(capsys.readouterr().out)
    assert _lines(document, COSTS_SEED) != []
    assert _lines(document, FX_SEED) == []


# Where a seed's CSV file may be: only a regular file inside a folder of `seed-paths`.

SECRET = b"db_password,aws_secret_access_key\nhunter2,abc\n"
MATCHING = b"currency,rate_date,rate_to_eur\nAUD,2023-02-17,0.6\n"


def _seed_at(manifest, original_file_path):
    """Point the fx seed of the manifest file `manifest` at `original_file_path`."""
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["nodes"][FX_SEED]["original_file_path"] = original_file_path
    return _write_json(manifest, document)


def _seed_paths(project, text):
    """Replace the `seed-paths` line of the project file with `text`."""
    file = project / "dbt_project.yml"
    written = file.read_text(encoding="utf-8")
    assert 'seed-paths: ["seeds"]\n' in written
    file.write_text(
        written.replace('seed-paths: ["seeds"]\n', text), encoding="utf-8"
    )


def _shows_the_secret(project, manifest, capsys):
    _check(project, "--manifest", manifest, "--json")
    return any(
        name in capsys.readouterr().out for name in ("hunter2", "password")
    )


@pytest.mark.parametrize(
    "where",
    [
        "../outside.csv",
        "seeds/../../outside.csv",
        "absolute",
        ".env",
        "dbt_project.yml",
        "profiles.yml",
    ],
)
def test_a_seed_path_outside_the_seed_folders_gives_the_unreadable_line(
    where, tmp_path, capsys
):
    project, manifest = _seed_project(tmp_path, MATCHING)
    (tmp_path / "outside.csv").write_bytes(SECRET)
    (project / ".env").write_bytes(b"DB_PASSWORD=hunter2\n")
    target = str(tmp_path / "outside.csv") if where == "absolute" else where
    manifest = _seed_at(manifest, target)

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]
    assert not _shows_the_secret(project, manifest, capsys)


def test_a_link_in_the_seed_folder_to_a_file_outside_it_gives_the_unreadable_line(
    tmp_path, capsys
):
    project, manifest = _seed_project(tmp_path, MATCHING)
    (tmp_path / "outside.csv").write_bytes(SECRET)
    (project / "seeds" / "leak.csv").symlink_to(tmp_path / "outside.csv")
    manifest = _seed_at(manifest, "seeds/leak.csv")

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]
    assert not _shows_the_secret(project, manifest, capsys)


def test_a_link_in_the_seed_folder_to_a_file_in_it_is_read(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, MATCHING)
    (project / "seeds" / "alias.csv").symlink_to(
        project / "seeds" / "seed_fx_rates_eur.csv"
    )
    manifest = _seed_at(manifest, "seeds/alias.csv")

    assert _fx_lines(project, manifest, capsys) == []


def test_a_csv_file_in_a_folder_below_a_seed_folder_is_read(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, MATCHING)
    (project / "seeds" / "fx").mkdir()
    (project / "seeds" / "fx" / "rates.csv").write_bytes(MATCHING)
    manifest = _seed_at(manifest, "seeds/fx/rates.csv")

    assert _fx_lines(project, manifest, capsys) == []


def test_a_seed_that_is_a_folder_or_a_pipe_gives_the_unreadable_line(
    tmp_path, capsys
):
    project, manifest = _seed_project(tmp_path, MATCHING)
    pipe = project / "seeds" / "pipe.csv"
    os.mkfifo(pipe)
    # A writer holds the pipe open with data in it, so a reader that opened it would read the
    # data instead of waiting.
    writer = os.open(pipe, os.O_RDWR)
    try:
        os.write(writer, SECRET)
        for where in ("seeds", "seeds/pipe.csv"):
            manifest = _seed_at(manifest, where)
            assert _fx_lines(project, manifest, capsys) == [UNREADABLE]
    finally:
        os.close(writer)


@pytest.mark.parametrize("named", ["relative", "absolute"])
def test_a_seed_folder_that_the_project_names_outside_it_is_read(
    named, tmp_path, capsys
):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "seed_fx_rates_eur.csv").write_bytes(MATCHING)
    project, manifest = _seed_project(tmp_path, SECRET)
    folder = "../shared" if named == "relative" else str(shared)
    _seed_paths(project, f'seed-paths: ["{folder}"]\n')
    manifest = _seed_at(manifest, f"{folder}/seed_fx_rates_eur.csv")

    assert _fx_lines(project, manifest, capsys) == []
    # `seeds` is no seed folder of this project any more.
    manifest = _seed_at(manifest, "seeds/seed_fx_rates_eur.csv")
    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_seed_folder_that_is_a_link_is_followed(tmp_path, capsys):
    shared = tmp_path / "shared"
    shared.mkdir()
    project, manifest = _seed_project(tmp_path, MATCHING)
    shutil.move(project / "seeds", shared / "seeds")
    (project / "seeds").symlink_to(shared / "seeds")

    assert _fx_lines(project, manifest, capsys) == []


@pytest.mark.parametrize(
    "project_file",
    [
        None,
        b"name: [\n",
        b"\xff\xfe",
        b"base: &a {x: 1}\nother: *a\nseed-paths: [elsewhere]\n",
        b"seed-paths: seeds_elsewhere\n",
        b"seed-paths: [1, null]\n",
        b"seed-paths: []\n",
        b"- seed-paths\n",
    ],
    ids=[
        "missing",
        "not yaml",
        "not text",
        "refused by the strict reader",
        "not a list",
        "no string",
        "empty",
        "not a mapping",
    ],
)
def test_the_seed_folder_is_seeds_unless_the_project_file_names_others(
    project_file, tmp_path, capsys
):
    project, manifest = _seed_project(tmp_path, MATCHING)
    file = project / "dbt_project.yml"
    if project_file is None:
        file.unlink()
    else:
        file.write_bytes(project_file)

    assert _fx_lines(project, manifest, capsys) == []


def test_an_entry_of_seed_paths_that_is_not_a_string_or_holds_a_nul_is_left_out(
    tmp_path, capsys
):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "seed_fx_rates_eur.csv").write_bytes(MATCHING)
    project, manifest = _seed_project(tmp_path, MATCHING)
    _seed_paths(project, 'seed-paths: [1, "a\\0b", "../shared"]\n')

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]
    manifest = _seed_at(manifest, "../shared/seed_fx_rates_eur.csv")
    assert _fx_lines(project, manifest, capsys) == []


def _row_of(size):
    """A first row of `size` bytes with its line ending: the declared columns, then others."""
    row = b"currency,rate_date,rate_to_eur"
    extra = [b"x" * 99] * ((size - len(row)) // 100 - 1)
    last = b"y" * (size - len(row) - 100 * len(extra) - len(b",\n"))
    return b",".join([row, *extra, last]) + b"\n"


def test_a_first_row_of_up_to_one_mebibyte_is_read(tmp_path, capsys):
    # The first row fills the first MiB exactly, with its line ending, and more rows follow.
    first = _row_of(2**20)
    assert len(first) == 2**20
    project, manifest = _seed_project(
        tmp_path, first + b"AUD,2023-02-17,0.6\n" * 10
    )

    lines = _fx_lines(project, manifest, capsys)
    assert lines == [
        f"{name} is in the CSV header but not declared"
        for name in ("x" * 99, first.rstrip(b"\n").rsplit(b",", 1)[1].decode())
    ]


@pytest.mark.parametrize(
    "rows",
    [b"", b"AUD,2023-02-17,0.6\n"],
    ids=["the whole file", "more rows after it"],
)
def test_a_first_row_longer_than_one_mebibyte_gives_the_unreadable_line(
    rows, tmp_path, capsys
):
    first = _row_of(2**20 + 1)
    project, manifest = _seed_project(tmp_path, first + rows)

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_device_file_gives_the_unreadable_line_at_once(tmp_path, capsys):
    project, manifest = _seed_project(tmp_path, MATCHING)
    (project / "seeds" / "zero.csv").symlink_to("/dev/zero")
    manifest = _seed_at(manifest, "seeds/zero.csv")

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_lone_surrogate_in_a_test_of_the_manifest_is_an_error_line(
    tmp_path, capsys
):
    manifest = copy.deepcopy(load_manifest("finance"))
    (test,) = (
        node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test"
        and node["name"] == "unique_fx_transactions_eur_transaction_id"
    )
    test["description"] = "\ud800"
    path = _write_json(tmp_path / "manifest.json", manifest)

    assert _check(FINANCE, "--manifest", path, "--json") == 1

    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is False
    assert _lines(document, "model.finance.fx_transactions_eur") == [
        f"test {test['unique_id']}: description: string holds a lone surrogate"
    ]
    assert "analytics.fx_transactions_eur" not in document["contracts"]


# A manifest whose fields are not the shape a dbt manifest has.


def _reshaped(change):
    manifest = copy.deepcopy(load_manifest("finance"))
    change(manifest)
    return manifest


def _first_test(manifest):
    return next(
        node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test" and node.get("attached_node")
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda m: m.update(metadata="x"),
            "manifest: metadata must be a mapping, got str",
        ),
        (
            lambda m: m["metadata"].update(project_name=5),
            "manifest: metadata.project_name must be a string, got int",
        ),
        (
            lambda m: _first_test(m).update(depends_on="x"),
            "depends_on must be a mapping, got str",
        ),
        (
            lambda m: _first_test(m).update(depends_on={"nodes": "x"}),
            "depends_on.nodes must be a list of strings, got str",
        ),
        (
            lambda m: _first_test(m).update(depends_on={"nodes": [{"a": 1}]}),
            "depends_on.nodes[0] must be a string, got dict",
        ),
        (
            lambda m: _first_test(m).update(attached_node=["x"]),
            "attached_node must be a string, got list",
        ),
    ],
    ids=["metadata", "project_name", "depends_on", "nodes", "node", "attached"],
)
def test_a_manifest_field_of_the_wrong_shape_is_a_usage_error(
    change, message, tmp_path, capsys, caplog
):
    path = _write_json(tmp_path / "manifest.json", _reshaped(change))

    assert _check(FINANCE, "--manifest", path, "--json") == 2

    assert capsys.readouterr().out == ""
    (error,) = _errors(caplog)
    assert error.endswith(message)
    assert lite.BANNER not in _infos(caplog)


def test_sources_of_the_wrong_shape_are_an_error_line_of_the_node_that_reads_them(
    tmp_path, capsys, caplog
):
    # Only a relationships test to a source reads `sources`, so the node gets an error line.
    manifest = copy.deepcopy(load_manifest("cases"))
    manifest["sources"] = "x"
    path = _write_json(tmp_path / "manifest.json", manifest)

    assert _check(FINANCE, "--manifest", path) == 1

    assert capsys.readouterr().out == ""
    assert any(
        error.startswith("model.cases.generic_tests: test ")
        and error.endswith(": sources must be a mapping, got str")
        for error in _errors(caplog)
    )


def test_a_manifest_nested_too_deep_for_the_json_reader_is_a_usage_error(
    tmp_path, capsys, caplog
):
    path = tmp_path / "manifest.json"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")

    assert _check(FINANCE, "--manifest", path, "--json") == 2

    assert capsys.readouterr().out == ""
    (message,) = _errors(caplog)
    assert message.startswith(f"{path.as_posix()}: cannot read the manifest: ")


@pytest.mark.parametrize(
    "delimiter",
    ['"', "\n", "\r"],
    ids=["a quote", "a newline", "a carriage return"],
)
def test_a_delimiter_csv_refuses_gives_the_unreadable_line(
    delimiter, tmp_path, capsys
):
    project, manifest = _seed_project(
        tmp_path, b"currency,rate_date,rate_to_eur\n", delimiter=delimiter
    )

    assert _fx_lines(project, manifest, capsys) == [UNREADABLE]


def test_a_seed_path_with_a_nul_gives_the_unreadable_line(tmp_path, capsys):
    project, path = _seed_project(tmp_path, b"currency,rate_date,rate_to_eur\n")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["nodes"][FX_SEED]["original_file_path"] = (
        "seeds/seed_fx\x00rates_eur.csv"
    )
    _write_json(path, manifest)

    assert _fx_lines(project, path, capsys) == [UNREADABLE]


@pytest.mark.parametrize(
    "name", ["A=B", "A\x00B", "1A", "A-B", "A B", ""], ids=repr
)
def test_an_env_var_that_is_not_a_plain_name_gets_no_placeholder(
    name, fake_dbt, tmp_path
):
    project = _copy_project(tmp_path)
    _profiles(project, "host: \"{{ env_var('" + name + "') }}\"\n")

    assert _check(project, "--dbt", fake_dbt.executable) == 1

    environ = fake_dbt.record["environ"]
    assert PLACEHOLDER not in environ.values()


def test_the_project_folder_and_the_target_reach_dbt_as_written(
    fake_dbt, tmp_path
):
    # The command runs dbt with no shell, so no character of an argument is read by one.
    project = tmp_path / 'a b; echo x $(x) `y` it\'s "q" | z'
    shutil.copytree(FINANCE, project)
    target = "a b; echo x $(x) 'it''s' \"q\" > out"

    assert _check(project, "--dbt", fake_dbt.executable, "--target", target) == 1

    argv = fake_dbt.record["argv"]
    assert argv[argv.index("--project-dir") + 1] == str(project)
    assert argv[argv.index("--profiles-dir") + 1] == str(project)
    assert argv[argv.index("--target") + 1] == target
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "argv", [["--help"], ["dbt", "--help"]], ids=["program", "dbt"]
)
def test_the_help_calls_dbt_check_a_lite_check(argv, capsys):
    with pytest.raises(SystemExit):
        main(argv)

    out = " ".join(capsys.readouterr().out.split())
    assert "lite check of a dbt project" in out
    assert "the way continuo does at release time" not in out
