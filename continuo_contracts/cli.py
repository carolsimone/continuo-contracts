"""The `continuo-contracts` command: check the ODCS contract of a node and print its hash.

The command has four subcommands. `validate` reads one or more contract files and reports every
problem each has. `hash` checks one contract file and prints its `contract_hash`. Both read a
file as `yaml_strict.load` does and check it as `validate.validate_python_contract` does, so a
producer's CI and a person at a terminal see what continuo's topology-controller sees. Both log
which family of contracts they check, so a log line says what a pass of the command covers.

`dbt check` is the producer's lite check of a dbt project. It runs `dbt parse` in the project
with the `dbt` executable it is given, as a new process, so that dbt runs on its own Python and
this package imports none of it, reads the `manifest.json` that dbt writes and the first row of
the CSV file of each seed, and logs the report of `lite.check_manifest`. With `--manifest` it
reads a manifest that already exists and runs nothing. The files the command reads are the files
its arguments name, the profiles file in the profiles folder of `dbt check`, the manifest, the
project's `dbt_project.yml` and the CSV files of the seeds, each only when it is a regular file
in a seed folder that `dbt_project.yml` names. With `--ontology FILE` it also reads that
ontology file, before it runs anything, and the lite check reads the bindings of the contracts
against it, with the rules that need no other project. A warning of that check is logged at
WARNING and never changes the exit status.

`ontology check` reads one ontology file (`ontology.load`) and logs each finding of
`ontology_check.ontology_findings`, the check of the file alone.

Output follows one rule. The human text goes through `logging` to stderr. Stdout carries only the
machine-read result: with `--json` one JSON document, and for `hash` without it the hash alone.
When a command finds nothing to print, as a usage error does, stdout stays empty. For `validate`
and `hash`, exit status 0 means the contracts are valid, 1 means a contract has an issue, and 2
means the command could not run: a usage error (a `--project` that is empty or blank among them),
a path that names no contract file, a path that cannot be looked up, a folder or a file that
cannot be read, or a `--read` value that is not a table. For `dbt check`, 0 means the check found
no error, 1 means it found an error or `dbt parse` failed, and 2 means it could not run: a usage
error, a folder that does not exist, no `dbt` executable, a manifest that cannot be read or is
not the shape of a dbt manifest, or an `--ontology` file that cannot be read or does not load. For
`ontology check`, 0 means the file has no error, 1 means it has an error finding, and 2 means the
command could not run: a file that cannot be read or does not load, as with `--ontology`.

`main` configures logging when it runs, not when the module is imported, and returns the exit
status. The console script `continuo-contracts` calls it.
"""

import argparse
import csv
import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, cast

from continuo_contracts import lite, ontology, yaml_strict
from continuo_contracts.contract import contract_hash, normalise_python
from continuo_contracts.errors import (
    DbtNodeError,
    HashInputError,
    OntologyError,
    StrictYamlError,
)
from continuo_contracts.ontology_check import ontology_findings
from continuo_contracts.validate import Issue, validate_python_contract

_logger = logging.getLogger(__name__)

_FILE_SUFFIX: Final = ".odcs.yaml"
_FAMILY: Final = "this release checks Python contracts"

_VALID: Final = 0
_INVALID: Final = 1
_USAGE: Final = 2

_EXIT_CODES: Final = """\
exit status:
  0  every contract is valid
  1  a contract has an issue
  2  a usage error (an empty or blank --project among them), a path that names no contract
     file, a path that cannot be looked up, a folder or a file that cannot be read, or a
     --read value that is not a table
"""

_ALL_EXIT_CODES: Final = """\
exit status of validate and hash:
  0  every contract is valid
  1  a contract has an issue
  2  the command could not run

exit status of dbt check:
  0  the check found no error
  1  the check found an error, or dbt parse failed
  2  the command could not run

exit status of ontology check:
  0  the file has no error
  1  the file has an error
  2  the command could not run

Each command's --help says what stops it from running.
"""

_DBT_EXIT_CODES: Final = """\
exit status:
  0  the check found no error
  1  the check found an error, or dbt parse exited with a status other than 0
  2  a usage error (an empty or blank --project among them), a PROJECT_DIR that is not a
     folder, no dbt executable, a manifest that cannot be read or is not the shape of a
     dbt manifest, or an --ontology file that cannot be read or does not load
"""

_ONTOLOGY_EXIT_CODES: Final = """\
exit status:
  0  the file has no error
  1  the file has an error finding
  2  a usage error, a FILE that cannot be read, or a FILE that does not load: it is not
     UTF-8 text, not the YAML of an ontology, or has a key or a value the format does not allow
"""

# The placeholder that dbt gets for a profile variable that nothing sets, and the text scanned
# for those variables: `env_var('NAME')` or `env_var("NAME")`, with nothing after the name but
# the closing parenthesis, which is a variable with no default. NAME is a plain identifier, so a
# name that no environment can hold, such as one with `=` or NUL, gets no placeholder.
_PLACEHOLDER: Final = "continuo-lite-check"
_ENV_VAR_WITHOUT_DEFAULT: Final = re.compile(
    r"""env_var\(\s*(?P<quote>['"])(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)\s*\)"""
)
# The lines of dbt's output that a failed `dbt parse` has logged, and the colour codes that dbt
# writes into them even when its output goes to a pipe.
_OUTPUT_TAIL: Final = 20
_COLOUR: Final = re.compile(r"\x1b\[[0-9;]*m")
# The seed folder of a project whose `dbt_project.yml` names none, as dbt has it, and the most
# bytes of a seed's CSV file that its first row may take.
_SEED_FOLDER: Final = "seeds"
_HEADER_BYTES: Final = 2**20
# The logging level of each level of a `lite.Line` and of each severity of a finding, which
# share their names.
_LEVELS: Final = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
}
_RELEASE_ONLY: Final = "What only the release does:"
# The severity of a finding that fails `ontology check`.
_ERROR_SEVERITY: Final = "error"

_VALIDATE_DESCRIPTION: Final = """\
Check Python contract files and log every issue at ERROR as
'<file>: <path>: <message>'.

With --json, stdout is
  {"valid": bool, "files": [{"path": str, "issues":
    [{"path": str, "reason": str, "message": str}]}]}
and a file with no issue is listed with an empty list.
"""

_HASH_DESCRIPTION: Final = """\
Check one Python contract file and print its contract_hash alone on stdout.

With --json, stdout is {"contract_hash": str, "stored": {...}}, where
"stored" is the stored form that the hash is of.

A file with an issue logs each issue at ERROR, prints nothing on stdout and
exits 1.
"""


_DBT_DESCRIPTION: Final = """\
Run the producer's lite check of a dbt project, without a warehouse. Passing it
does not guarantee that continuo will create the contracts at release time.
"""

_DBT_CHECK_DESCRIPTION: Final = """\
Log the producer's lite check of a dbt project: the banner, one line for each node
the check leaves out or finds a problem in (a problem at ERROR, a warning of the ontology
at WARNING), what only the release does, and the banner again. Passing it does not
guarantee that the release creates the contracts.

With --ontology FILE the check also reads the bindings of the contracts against that
ontology file, with the rules that need no other project. A warning does not fail the
check. A file with an error of its own gives one ERROR line that says so, and the file
is checked alone with `continuo-contracts ontology check`.

Without --manifest the command runs
  <dbt> --write-json --log-path <TMP> parse --project-dir PROJECT_DIR
    --profiles-dir <DIR> --target-path <TMP>
and reads the manifest that dbt writes in <TMP>, a temporary folder that the command
makes and removes, where dbt also writes its log. <DIR> is --profiles-dir, or PROJECT_DIR. <dbt> is --dbt, or dbt on
PATH. Each env_var('NAME') of the profiles file that has no default, when no variable
NAME is set, is set to continuo-lite-check for dbt alone. Nothing else of the
environment changes, and no other dbt command runs.

The header of a seed is the first row of its CSV file in PROJECT_DIR, read as UTF-8
with an optional byte order mark and with the delimiter of the seed's config. Only a
regular file whose real path lies in a folder that seed-paths of
PROJECT_DIR/dbt_project.yml names (seeds by default) is read, and only its first MiB;
any other file is reported as a file that cannot be read.

With --json, stdout is
  {"lite": true, "ok": bool, "project": str, "lines": [{"level": str,
    "node_id": str, "message": str}], "contracts": {"<contract id>": "<contract_hash>"}}
where level is error, warning or info.
"""

_ONTOLOGY_DESCRIPTION: Final = """\
Check an ontology file.
"""

_ONTOLOGY_CHECK_DESCRIPTION: Final = """\
Check an ontology file alone, without a contract: each project names a declared domain,
each thing an owner that is a declared domain, each reference a thing and a property that
exist, and each personal property an access rule. Log each finding at the level of its
severity, and then how many errors the file has.

With --json, stdout is
  {"ok": bool, "findings": [{"severity": str, "rule": str, "subject": str,
    "message": str}]}
and ok is true when no finding is an error.
"""


class _UsageError(Exception):
    """The command cannot run. `problems` lists what to log, one message each."""

    def __init__(self, *problems: str) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _project_name(value: str) -> str:
    """Return `value`, the project of a release, or refuse one that is blank.

    An empty or blank project names no release, so a hash computed under it
    matches none that a release stores.
    """
    if not value.strip():
        raise argparse.ArgumentTypeError("the project must not be empty or blank")
    return value


def _posix(path: Path) -> str:
    """Write `path` with `/` on every platform, so a report reads the same everywhere."""
    return path.as_posix()


def _cannot(
    path: Path, action: str, error: OSError | ValueError | RecursionError
) -> str:
    """Write the problem of an `action` on `path` that the system refused with `error`."""
    return f"{_posix(path)}: cannot {action}: {getattr(error, 'strerror', None) or error}"


def _mode(path: Path) -> int | None:
    """Return the `st_mode` of what `path` names, or `None` when nothing is there.

    Symbolic links are followed.

    Raises the `OSError` of any other failure to look the path up, such as a folder on the way
    that the process may not search, and the `ValueError` of a path that no file can have, such
    as one with a NUL character, so the caller reports it instead of reading it as a missing
    path.
    """
    try:
        return path.stat().st_mode
    except FileNotFoundError, NotADirectoryError:
        return None


def _folder_files(folder: Path) -> tuple[list[Path], list[str]]:
    """Return the regular `*.odcs.yaml` files directly in `folder`, in name order.

    The result also holds the problems of the folder and of the entries that
    cannot be looked up.
    """
    try:
        entries = sorted(
            item for item in folder.iterdir() if item.name.endswith(_FILE_SUFFIX)
        )
    except OSError as error:
        return [], [_cannot(folder, "read the folder", error)]
    files: list[Path] = []
    problems: list[str] = []
    for entry in entries:
        try:
            mode = _mode(entry)
        except OSError as error:
            problems.append(_cannot(entry, "look up the path", error))
            continue
        if mode is not None and stat.S_ISREG(mode):
            files.append(entry)
    return files, problems


def _contract_files(paths: Sequence[Path]) -> list[Path]:
    """Return the contract files that `paths` name.

    A file stands for itself, and a folder for its `*.odcs.yaml` files in name
    order, without looking into folders below it.

    Raises `_UsageError` naming each path that is missing, is a file with another name, is a
    folder with no contract file, or cannot be looked up or read as a folder, such as a path below
    a folder that the process may not search.
    """
    files: list[Path] = []
    problems: list[str] = []
    for path in paths:
        try:
            mode = _mode(path)
        except (OSError, ValueError) as error:
            problems.append(_cannot(path, "look up the path", error))
            continue
        if mode is not None and stat.S_ISDIR(mode):
            found, unreadable = _folder_files(path)
            files.extend(found)
            problems.extend(unreadable)
            if not found and not unreadable:
                problems.append(
                    f"{_posix(path)}: the folder has no *{_FILE_SUFFIX} file"
                )
        elif mode is None or not stat.S_ISREG(mode):
            problems.append(f"{_posix(path)}: no such file or folder")
        elif path.name.endswith(_FILE_SUFFIX):
            files.append(path)
        else:
            problems.append(
                f"{_posix(path)}: the file is not named *{_FILE_SUFFIX}"
            )
    if problems:
        raise _UsageError(*problems)
    return files


def _read_texts(files: Sequence[Path]) -> list[tuple[Path, str]]:
    """Return each file with its text, decoded from UTF-8 and left as written.

    A byte order mark and CRLF line endings reach the reader.

    Raises `_UsageError` naming each file that cannot be read.
    """
    texts: list[tuple[Path, str]] = []
    problems: list[str] = []
    for path in files:
        try:
            texts.append((path, path.read_bytes().decode("utf-8")))
        except OSError as error:
            problems.append(
                f"{_posix(path)}: cannot read the file: {error.strerror or error}"
            )
        except UnicodeDecodeError as error:
            problems.append(
                f"{_posix(path)}: the file is not UTF-8 text ({error.reason} at byte {error.start})"
            )
    if problems:
        raise _UsageError(*problems)
    return texts


def _check(
    path: Path, text: str, project: str | None
) -> tuple[object, list[Issue]]:
    """Read and check one contract file, and return the document and its issues.

    A text that `yaml_strict.load` refuses has no document and gives the one issue `strict_yaml`
    at `$`, which carries the line and the column of the refusal.
    """
    try:
        doc = yaml_strict.load(text)
    except StrictYamlError as error:
        return None, [Issue("$", "strict_yaml", str(error))]
    return doc, validate_python_contract(doc, path=_posix(path), project=project)


def _log_issues(path: Path, issues: Sequence[Issue]) -> None:
    for issue in issues:
        _logger.error("%s: %s", _posix(path), issue)


def _print_json(document: object) -> None:
    print(json.dumps(document, indent=2))  # noqa: T201 - stdout carries the result


def _validate(args: argparse.Namespace) -> int:
    texts = _read_texts(_contract_files(args.paths))
    results: list[tuple[Path, list[Issue]]] = []
    for path, text in texts:
        _, issues = _check(path, text, args.project)
        results.append((path, issues))
    for path, issues in results:
        _log_issues(path, issues)
    invalid = sum(1 for _, issues in results if issues)
    _logger.info(
        "%d %s checked, %d with issues",
        len(results),
        "contract" if len(results) == 1 else "contracts",
        invalid,
    )
    if args.json:
        _print_json(
            {
                "valid": invalid == 0,
                "files": [
                    {
                        "path": _posix(path),
                        "issues": [
                            {
                                "path": i.path,
                                "reason": i.reason,
                                "message": i.message,
                            }
                            for i in issues
                        ],
                    }
                    for path, issues in results
                ],
            }
        )
    return _INVALID if invalid else _VALID


def _hash(args: argparse.Namespace) -> int:
    path: Path = args.file
    try:
        mode = _mode(path)
    except (OSError, ValueError) as error:
        raise _UsageError(_cannot(path, "look up the path", error)) from error
    if mode is not None and stat.S_ISDIR(mode):
        raise _UsageError(
            f"{_posix(path)}: hash takes one contract file, not a folder"
        )
    ((path, text),) = _read_texts(_contract_files([path]))
    doc, issues = _check(path, text, args.project)
    if issues:
        _log_issues(path, issues)
        return _INVALID
    try:
        # No issue means the document is a mapping, which the schema check guarantees.
        stored = normalise_python(
            cast(Mapping[str, Any], doc), project=args.project, reads=args.read
        )
    except HashInputError as error:
        raise _UsageError(str(error)) from error
    result = contract_hash(stored)
    if args.json:
        _print_json({"contract_hash": result, "stored": stored})
    else:
        print(result)  # noqa: T201 - stdout carries the result
    return _VALID


def _require_folder(path: Path) -> None:
    """Return when `path` is a folder.

    Raises `_UsageError` when it is not one, or cannot be looked up.
    """
    try:
        mode = _mode(path)
    except (OSError, ValueError) as error:
        raise _UsageError(_cannot(path, "look up the path", error)) from error
    if mode is None or not stat.S_ISDIR(mode):
        raise _UsageError(f"{_posix(path)}: no such folder")


def _load_ontology(path: Path) -> ontology.Ontology:
    """Return the ontology in the file `path`.

    The file is read as UTF-8 text and loaded as `ontology.load` does.

    Raises `_UsageError` when the file cannot be read, is not UTF-8 text, or does not load, with
    the message of the `OntologyError` after the path of the file.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, ValueError) as error:
        raise _UsageError(
            _cannot(path, "read the ontology file", error)
        ) from error
    try:
        return ontology.load(text)
    except OntologyError as error:
        raise _UsageError(f"{_posix(path)}: {error}") from error


def _read_manifest(path: Path) -> dict[str, Any]:
    """Return the manifest in the JSON file `path`.

    Raises `_UsageError` when the file cannot be read, is not JSON, is nested deeper than the JSON
    reader goes, is not an object, or has a `nodes` that is not an object of objects.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        raise _UsageError(_cannot(path, "read the manifest", error)) from error
    if not isinstance(document, dict):
        raise _UsageError(f"{_posix(path)}: the manifest is not a JSON object")
    nodes = document.get("nodes")
    if nodes is not None and not (
        isinstance(nodes, dict)
        and all(isinstance(n, dict) for n in nodes.values())
    ):
        raise _UsageError(
            f"{_posix(path)}: `nodes` is not a JSON object of nodes"
        )
    return document


def _seed_folders(project_dir: Path) -> list[Path]:
    """Return the real paths of the seed folders of the dbt project `project_dir`.

    They are the folders that the list `seed-paths` of `project_dir/dbt_project.yml` names, each
    read from `project_dir` (an absolute one as it is), with its symbolic links resolved. An entry
    that is not a string or that holds a NUL names no folder. The file is read as
    `yaml_strict.load` reads it, and the folder is `seeds` when the file is missing or cannot be
    read, is not a mapping, or has no string in a list under `seed-paths`, as dbt's default is.
    """
    try:
        document = yaml_strict.load(
            (project_dir / "dbt_project.yml").read_text(encoding="utf-8")
        )
    except OSError, UnicodeError, StrictYamlError:
        document = None
    listed = document.get("seed-paths") if isinstance(document, dict) else None
    names = (
        [name for name in listed if isinstance(name, str)]
        if isinstance(listed, list)
        else []
    )
    return [
        Path(os.path.realpath(project_dir / name))
        for name in names or [_SEED_FOLDER]
        if "\0" not in name
    ]


def _first_row(path: Path, delimiter: str) -> list[str]:
    """Return the first row of the CSV file `path`, read from its first `_HEADER_BYTES` bytes.

    Raises `ValueError` when the row, with its line ending, does not end in those bytes and the
    file goes on, and the errors of reading the file and of `csv`.
    """
    with path.open("rb") as stream:
        head = stream.read(_HEADER_BYTES)
        longer = stream.read(1) != b""
    text = io.TextIOWrapper(io.BytesIO(head), encoding="utf-8-sig", newline="")
    last = [""]

    def lines() -> Iterator[str]:
        for line in text:
            last[0] = line
            yield line

    row = next(csv.reader(lines(), delimiter=delimiter), [])
    if longer and not last[0].endswith(("\n", "\r")):
        raise ValueError(f"the first row is longer than {_HEADER_BYTES} bytes")
    return row


def _header_reader(
    project_dir: Path,
) -> Callable[[Mapping[str, Any]], list[str] | None]:
    """Return the `read_header` of the lite check for the seeds of the dbt project `project_dir`.

    A seed's header is the first row of its CSV file `project_dir/<original_file_path>`, read as
    UTF-8 with an optional byte order mark and with the delimiter of the seed's `config` (a comma
    when it has none), so a CRLF line ending changes nothing. An empty file has an empty header.
    Only a regular file whose real path, its symbolic links resolved, lies in a folder of
    `_seed_folders(project_dir)` is read, and only its first MiB. Any other file gives `None`, as
    does a file that cannot be read: one that is missing, whose path holds a NUL, that is not UTF-8,
    whose first row is longer than a MiB, that has a field over the limit of `csv`, or whose
    delimiter is not one character or is one `csv` refuses, such as a quote, a newline or a
    carriage return. A manifest therefore cannot name another file for the check to show.
    """
    folders = _seed_folders(project_dir)

    def read(node: Mapping[str, Any]) -> list[str] | None:
        # A node that the lite check reads a header of is a kept seed, so `config` is a mapping
        # and `original_file_path` a non-empty string.
        delimiter = node["config"].get("delimiter") or ","
        try:
            real = Path(
                os.path.realpath(project_dir / node["original_file_path"])
            )
            if not real.is_file() or not any(
                real.is_relative_to(folder) for folder in folders
            ):
                return None
            return _first_row(real, delimiter)
        except OSError, ValueError, csv.Error, TypeError:
            return None

    return read


def _folder_project(folder: Path) -> str:
    """Return the name of `folder`, with `_` written `-`: the default project."""
    return Path(os.path.abspath(folder)).name.replace("_", "-")


def _env_vars_without_default(text: str) -> list[str]:
    """Return the names that `text` reads with `env_var('NAME')` and no default, once each.

    The text is scanned, not evaluated: Jinja is not run, so a call that a condition or a
    comment would leave out is found all the same.
    """
    names = (m["name"] for m in _ENV_VAR_WITHOUT_DEFAULT.finditer(text))
    return list(dict.fromkeys(names))


def _placeholders(profiles_dir: Path) -> dict[str, str]:
    """Return the placeholder for each variable the profiles file reads with no default.

    Only a variable that the environment does not set gets one. A profiles file that cannot be
    read gives none, and dbt then says what is wrong with it.
    """
    try:
        text = (profiles_dir / "profiles.yml").read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return {}
    return {
        name: _PLACEHOLDER
        for name in _env_vars_without_default(text)
        if name not in os.environ
    }


def _dbt_executable(requested: str | None) -> str:
    """Return the `dbt` executable to run: `requested`, or `dbt` on `PATH`.

    Raises `_UsageError` when there is none.
    """
    name = requested or "dbt"
    found = shutil.which(name)
    if found is None:
        raise _UsageError(
            f"no dbt executable: {name} is not an executable file or a name on PATH, "
            "so pass --dbt PATH"
        )
    return found


def _dbt_parse(
    executable: str, args: argparse.Namespace, target_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run `dbt parse` in the project of `args`, writing its manifest to `target_path`.

    `--write-json` and `--log-path` come before `parse`, where every dbt from 1.5 reads them:
    the manifest is written even when the environment or the project turns dbt's JSON files
    off, and dbt's log goes to `target_path`, not to the project. The program starts without a
    shell, with the standard input closed. Its output, the standard
    error with the standard output, is captured as UTF-8 text, which holds the colour codes dbt
    writes. It gets the environment of this
    process and, for each variable of the profiles file that nothing sets, the placeholder.

    Raises `_UsageError` when the system cannot start the program.
    """
    profiles: Path = args.profiles_dir or args.project_dir
    command = [
        executable,
        "--write-json",
        "--log-path",
        str(target_path),
        "parse",
        "--project-dir",
        str(args.project_dir),
        "--profiles-dir",
        str(profiles),
        "--target-path",
        str(target_path),
    ]
    if args.target is not None:
        command += ["--target", args.target]
    try:
        return subprocess.run(
            command,
            env={**os.environ, **_placeholders(profiles)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise _UsageError(
            f"{executable}: cannot run dbt: {error.strerror or error}"
        ) from error


def _log_report(report: lite.LiteReport) -> None:
    """Log the banner, the lines of `report`, what only the release does and the banner."""
    _logger.info("%s", lite.BANNER)
    for line in report.lines:
        text = f"{line.node_id}: {line.message}" if line.node_id else line.message
        _logger.log(_LEVELS[line.level], "%s", text)
    _logger.info("%s", _RELEASE_ONLY)
    for what, why in lite.RELEASE_ONLY:
        _logger.info("  %s: %s", what, why)
    _logger.info("%s", lite.BANNER)


def _report_document(report: lite.LiteReport) -> dict[str, Any]:
    """Return the JSON document of `report`."""
    return {
        "lite": True,
        "ok": report.ok,
        "project": report.project,
        "lines": [
            {
                "level": line.level,
                "node_id": line.node_id,
                "message": line.message,
            }
            for line in report.lines
        ],
        "contracts": dict(report.contracts),
    }


def _dbt_check(args: argparse.Namespace) -> int:
    project_dir: Path = args.project_dir
    _require_folder(project_dir)
    # The ontology is read before anything runs, so a file that does not load costs no dbt run.
    onto = _load_ontology(args.ontology) if args.ontology is not None else None
    if args.manifest is not None:
        manifest = _read_manifest(args.manifest)
    else:
        executable = _dbt_executable(args.dbt)
        with tempfile.TemporaryDirectory(prefix="continuo-lite-check-") as folder:
            target_path = Path(folder)
            done = _dbt_parse(executable, args, target_path)
            if done.returncode != 0:
                _logger.error("dbt parse exited with status %d", done.returncode)
                for output in done.stdout.splitlines()[-_OUTPUT_TAIL:]:
                    _logger.error("%s", _COLOUR.sub("", output))
                return _INVALID
            manifest = _read_manifest(target_path / "manifest.json")
    try:
        report = lite.check_manifest(
            manifest,
            declared_project=args.project or _folder_project(project_dir),
            read_header=_header_reader(project_dir),
            ontology=onto,
        )
    except DbtNodeError as error:
        # A field of a node is reported on the node's line, so this is a field of the manifest.
        raise _UsageError(
            f"the manifest is not the shape of a dbt manifest: {error}"
        ) from error
    _log_report(report)
    if args.json:
        _print_json(_report_document(report))
    return _VALID if report.ok else _INVALID


def _ontology_check(args: argparse.Namespace) -> int:
    findings = ontology_findings(_load_ontology(args.file))
    for finding in findings:
        _logger.log(
            _LEVELS[finding.severity], "%s: %s", finding.subject, finding.message
        )
    errors = sum(finding.severity == _ERROR_SEVERITY for finding in findings)
    _logger.info(
        "%d %s in the ontology file", errors, "error" if errors == 1 else "errors"
    )
    if args.json:
        _print_json(
            {
                "ok": errors == 0,
                "findings": [
                    {
                        "severity": finding.severity,
                        "rule": finding.rule,
                        "subject": finding.subject,
                        "message": finding.message,
                    }
                    for finding in findings
                ],
            }
        )
    return _INVALID if errors else _VALID


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="continuo-contracts",
        description=(
            "Check the ODCS contract of a Python node and print its contract_hash, run "
            "the lite check of a dbt project, or check an ontology file."
        ),
        epilog=_ALL_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="{validate,hash,dbt,ontology}"
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        help="print one JSON document on stdout, and nothing else",
    )

    validate = commands.add_parser(
        "validate",
        parents=[common],
        help="check contract files and list every issue",
        description=_VALIDATE_DESCRIPTION,
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    validate.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help=(
            f"a *{_FILE_SUFFIX} file, or a folder whose *{_FILE_SUFFIX} files are read, in name "
            "order and without the folders below it"
        ),
    )
    validate.add_argument(
        "--project",
        type=_project_name,
        help=(
            "the project of the release: a `project` property that names another one is an "
            "issue. Without it that check is skipped"
        ),
    )
    validate.set_defaults(handler=_validate, family=_FAMILY)

    hash_ = commands.add_parser(
        "hash",
        parents=[common],
        help="check one contract file and print its contract_hash",
        description=_HASH_DESCRIPTION,
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hash_.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help=f"the contract file, named *{_FILE_SUFFIX}",
    )
    hash_.add_argument(
        "--project",
        type=_project_name,
        required=True,
        help="the project of the release that carries the contract",
    )
    hash_.add_argument(
        "--read",
        action="append",
        default=[],
        metavar="TABLE",
        help=(
            "a table the node's read queries name, written <schema>.<table>: exactly two parts "
            "joined by a dot, neither empty, with no whitespace and no control character; repeat "
            "it for each table. topology-controller passes these at release time, and the hash "
            "covers them, so pass the same ones to get the hash it stores"
        ),
    )
    hash_.set_defaults(handler=_hash, family=_FAMILY)

    dbt = commands.add_parser(
        "dbt",
        help="the producer's lite check of a dbt project",
        description=_DBT_DESCRIPTION,
        epilog=_DBT_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dbt_commands = dbt.add_subparsers(
        dest="dbt_command", required=True, metavar="{check}"
    )
    check = dbt_commands.add_parser(
        "check",
        parents=[common],
        help="run dbt parse and check what continuo will make of the project",
        description=_DBT_CHECK_DESCRIPTION,
        epilog=_DBT_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument(
        "project_dir",
        type=Path,
        metavar="PROJECT_DIR",
        help="the dbt project folder, which holds dbt_project.yml and the seeds' CSV files",
    )
    check.add_argument(
        "--dbt",
        metavar="PATH",
        help="the dbt executable, a path or a name on PATH (default: dbt)",
    )
    check.add_argument(
        "--profiles-dir",
        type=Path,
        metavar="DIR",
        help="the folder of profiles.yml, which dbt parse is given (default: PROJECT_DIR)",
    )
    check.add_argument(
        "--target",
        metavar="NAME",
        help="the target of the profile, which dbt parse is given (default: the profile's own)",
    )
    check.add_argument(
        "--manifest",
        type=Path,
        metavar="FILE",
        help=(
            "check this manifest.json instead of running dbt parse; --dbt, --profiles-dir and "
            "--target then have no effect, and the seeds' CSV files are still read from PROJECT_DIR"
        ),
    )
    check.add_argument(
        "--project",
        type=_project_name,
        help=(
            "the project the release names: the check compares the project of every node it "
            "keeps with it, as the release does, so a node of another project is an error; it "
            "is used as the project when the manifest's metadata.project_name is empty "
            "(default: the name of PROJECT_DIR, with _ written -)"
        ),
    )
    check.add_argument(
        "--ontology",
        type=Path,
        metavar="FILE",
        help=(
            "read the bindings of the contracts against this ontology file, with the rules "
            "that need no other project; a file with an error of its own is one error line, "
            "and one that does not load stops the command (default: no binding is checked "
            "against an ontology)"
        ),
    )
    check.set_defaults(handler=_dbt_check, family=None)

    onto = commands.add_parser(
        "ontology",
        help="check an ontology file",
        description=_ONTOLOGY_DESCRIPTION,
        epilog=_ONTOLOGY_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    onto_commands = onto.add_subparsers(
        dest="ontology_command", required=True, metavar="{check}"
    )
    onto_check = onto_commands.add_parser(
        "check",
        parents=[common],
        help="check an ontology file alone and list every finding",
        description=_ONTOLOGY_CHECK_DESCRIPTION,
        epilog=_ONTOLOGY_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    onto_check.add_argument(
        "file",
        type=Path,
        metavar="FILE",
        help="the ontology file, one YAML document",
    )
    onto_check.set_defaults(handler=_ontology_check, family=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the `continuo-contracts` command with the arguments `argv` and return its exit status.

    `argv` defaults to `sys.argv[1:]`. The status is 0 when every contract is valid (for `dbt
    check`, when the check found no error), 1 when a contract has an issue (an error, or a
    failed `dbt parse`) and 2 when the command could not run. A usage error of the argument
    parser exits the process with status 2 through `argparse`, and so does `--help` with 0.
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    if args.family is not None:
        _logger.info("%s", args.family)
    try:
        status: int = args.handler(args)
    except _UsageError as error:
        for problem in error.problems:
            _logger.error("%s", problem)
        return _USAGE
    return status
