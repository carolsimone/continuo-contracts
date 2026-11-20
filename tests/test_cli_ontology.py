"""`continuo-contracts ontology check`: an ontology file checked alone, from the terminal."""

import json
import logging
import subprocess
import sys

import pytest

from ontology_fixtures import BROKEN_TEXT, DEMO_PATH

from continuo_contracts import cli
from continuo_contracts.cli import main
from continuo_contracts.ontology_check import Finding

BROKEN_FINDINGS = [
    {
        "severity": "error",
        "rule": "ontology",
        "subject": "principals.services.core",
        "message": "domain 'elsewhere' is not declared",
    },
    {
        "severity": "error",
        "rule": "ontology",
        "subject": "Thing",
        "message": "owner 'nobody' is not a declared domain",
    },
]
# What the logging of a finding says, by level.
UNKNOWN_KEY = "entities:\n  Thing:\n    owners: x\n"


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO)


def _records(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records]


def _errors(caplog):
    return [m for level, m in _records(caplog) if level == logging.ERROR]


def _put(tmp_path, text, name="ontology.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _check(*args):
    return main(["ontology", "check", *map(str, args)])


def test_a_file_with_no_finding_exits_0_and_logs_no_error(capsys, caplog):
    assert _check(DEMO_PATH) == 0

    assert capsys.readouterr().out == ""
    assert _records(caplog) == [(logging.INFO, "0 errors in the ontology file")]


def test_ontology_check_exits_1_on_an_error_and_0_on_warnings(
    tmp_path, monkeypatch, caplog
):
    broken = _put(tmp_path, BROKEN_TEXT)

    assert _check(broken) == 1

    assert _records(caplog) == [
        (
            logging.ERROR,
            "principals.services.core: domain 'elsewhere' is not declared",
        ),
        (logging.ERROR, "Thing: owner 'nobody' is not a declared domain"),
        (logging.INFO, "2 errors in the ontology file"),
    ]
    # A finding of another severity is logged at its level and never fails the command.
    caplog.clear()
    monkeypatch.setattr(
        cli,
        "ontology_findings",
        lambda _: [
            Finding("warning", "ontology", "Thing", "a warning", None),
            Finding("info", "ontology", "Thing", "an information", None),
        ],
    )

    assert _check(DEMO_PATH) == 0

    assert _records(caplog) == [
        (logging.WARNING, "Thing: a warning"),
        (logging.INFO, "Thing: an information"),
        (logging.INFO, "0 errors in the ontology file"),
    ]


def test_one_error_is_logged_in_the_singular(tmp_path, caplog):
    path = _put(tmp_path, BROKEN_TEXT.replace("owner: nobody", "owner: core"))

    assert _check(path) == 1

    assert _records(caplog)[-1] == (logging.INFO, "1 error in the ontology file")


def _unreadable(case, tmp_path):
    """Return the path of a file that `ontology check` cannot read or load, by `case`."""
    if case == "missing":
        return tmp_path / "nothing.yaml"
    if case == "folder":
        return tmp_path
    if case == "nul":
        return tmp_path / "bad\0name.yaml"
    if case == "not utf-8":
        path = tmp_path / "latin.yaml"
        path.write_bytes(b"name: caf\xe9\n")
        return path
    return _put(tmp_path, "a: [" if case == "not yaml" else UNKNOWN_KEY)


@pytest.mark.parametrize("flags", [[], ["--json"]])
@pytest.mark.parametrize(
    "case",
    ["missing", "folder", "nul", "not utf-8", "not yaml", "unknown key"],
)
def test_ontology_check_with_an_unreadable_file_exits_2(
    case, flags, tmp_path, capsys, caplog
):
    path = _unreadable(case, tmp_path)

    assert _check(path, *flags) == 2

    assert capsys.readouterr().out == ""
    [message] = _errors(caplog)
    assert _records(caplog) == [(logging.ERROR, message)]
    if case == "unknown key":
        assert message == (
            f"{path.as_posix()}: entities.Thing.owners: unknown key, the keys "
            "of a thing are access, born_in, description, key, owner, "
            "properties"
        )
    elif case == "not yaml":
        assert message.startswith(f"{path.as_posix()}: <root>: line 1, column 5")
    else:
        assert message.startswith(
            f"{path.as_posix()}: cannot read the ontology file: "
        )


def test_ontology_check_json_is_the_only_stdout(tmp_path, capsys, caplog):
    broken = _put(tmp_path, BROKEN_TEXT)

    assert _check(broken, "--json") == 1

    out = capsys.readouterr().out
    document = json.loads(out)
    assert out == json.dumps(document, indent=2) + "\n"
    assert document == {"ok": False, "findings": BROKEN_FINDINGS}
    assert list(document) == ["ok", "findings"]
    # The human text still goes to the log.
    assert len(_errors(caplog)) == 2


def test_ontology_check_json_of_a_clean_file_has_no_finding(capsys):
    assert _check(DEMO_PATH, "--json") == 0

    assert json.loads(capsys.readouterr().out) == {"ok": True, "findings": []}


def test_a_warning_is_in_the_json_and_the_document_is_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "ontology_findings",
        lambda _: [Finding("warning", "ontology", "Thing", "a warning", None)],
    )

    assert _check(DEMO_PATH, "--json") == 0

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "findings": [
            {
                "severity": "warning",
                "rule": "ontology",
                "subject": "Thing",
                "message": "a warning",
            }
        ],
    }


def test_the_log_goes_to_stderr_and_stdout_holds_the_document_alone(tmp_path):
    broken = _put(tmp_path, BROKEN_TEXT)
    code = (
        "import sys; from continuo_contracts.cli import main; "
        "sys.exit(main(sys.argv[1:]))"
    )

    done = subprocess.run(
        [sys.executable, "-c", code, "ontology", "check", str(broken), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1
    assert json.loads(done.stdout)["ok"] is False
    assert done.stderr.splitlines() == [
        "ERROR principals.services.core: domain 'elsewhere' is not declared",
        "ERROR Thing: owner 'nobody' is not a declared domain",
        "INFO 2 errors in the ontology file",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["ontology"],
        ["ontology", "check"],
        ["ontology", "other"],
        ["ontology", "check", "a.yaml", "b.yaml"],
    ],
)
def test_a_usage_error_of_the_arguments_exits_2_through_argparse(argv, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(argv)

    assert exit_.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_help_lists_the_command_and_its_exit_codes(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "{validate,hash,dbt,ontology}" in out
    assert "an ontology file" in out

    with pytest.raises(SystemExit):
        main(["ontology", "check", "--help"])
    out = capsys.readouterr().out
    for word in ("FILE", "--json", "exit status", "does not load"):
        assert word in out
