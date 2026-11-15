"""The `continuo-contracts` command: check the ODCS contract of a Python node and print its hash.

The command has two subcommands. `validate` reads one or more contract files and reports every
problem each has. `hash` checks one contract file and prints its `contract_hash`. Both read a
file as `yaml_strict.load` does and check it as `validate.validate_python_contract` does, so a
producer's CI and a person at a terminal see what continuo's topology-controller sees. Both log
which family of contracts they check, so a log line says what a pass of the command covers.

Output follows one rule. The human text goes through `logging` to stderr. Stdout carries only the
machine-read result: with `--json` one JSON document, and for `hash` without it the hash alone.
When a command finds nothing to print, as a usage error does, stdout stays empty. Exit status 0
means the contracts are valid, 1 means a contract has an issue, and 2 means the command could not
run: a usage error (a `--service` that is empty or blank among them), a path that names no contract
file, a path that cannot be looked up, a folder or a file that cannot be read, or a `--read` value
that is not a table.

`main` configures logging when it runs, not when the module is imported, and returns the exit
status. The console script `continuo-contracts` calls it.
"""

import argparse
import json
import logging
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, cast

from continuo_contracts import yaml_strict
from continuo_contracts.contract import contract_hash, normalise_python
from continuo_contracts.errors import HashInputError, StrictYamlError
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
  2  a usage error (an empty or blank --service among them), a path that names no contract
     file, a path that cannot be looked up, a folder or a file that cannot be read, or a
     --read value that is not a table
"""

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


class _UsageError(Exception):
    """The command cannot run. `problems` lists what to log, one message each."""

    def __init__(self, *problems: str) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _service(value: str) -> str:
    """Return `value`, the service of a release, and refuse one that is empty or blank: it names
    no release, so a hash computed under it matches none that a release stores."""
    if not value.strip():
        raise argparse.ArgumentTypeError("the service must not be empty or blank")
    return value


def _posix(path: Path) -> str:
    """Write `path` with `/` on every platform, so a report reads the same everywhere."""
    return path.as_posix()


def _cannot(path: Path, action: str, error: OSError) -> str:
    """Write the problem of an `action` on `path` that the system refused with `error`."""
    return f"{_posix(path)}: cannot {action}: {error.strerror or error}"


def _mode(path: Path) -> int | None:
    """Return the `st_mode` of what `path` names, following symbolic links, or `None` when
    nothing is there.

    Raises the `OSError` of any other failure to look the path up, such as a folder on the way
    that the process may not search, so the caller reports it instead of reading it as a missing
    path.
    """
    try:
        return path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None


def _folder_files(folder: Path) -> tuple[list[Path], list[str]]:
    """Return the regular `*.odcs.yaml` files directly in `folder`, in name order, and the
    problems of the folder and of the entries that cannot be looked up."""
    try:
        entries = sorted(item for item in folder.iterdir() if item.name.endswith(_FILE_SUFFIX))
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
    """Return the contract files that `paths` name: each file as it is, and for each folder its
    `*.odcs.yaml` files in name order, without looking into folders below it.

    Raises `_UsageError` naming each path that is missing, is a file with another name, is a
    folder with no contract file, or cannot be looked up or read as a folder, such as a path below
    a folder that the process may not search.
    """
    files: list[Path] = []
    problems: list[str] = []
    for path in paths:
        try:
            mode = _mode(path)
        except OSError as error:
            problems.append(_cannot(path, "look up the path", error))
            continue
        if mode is not None and stat.S_ISDIR(mode):
            found, unreadable = _folder_files(path)
            files.extend(found)
            problems.extend(unreadable)
            if not found and not unreadable:
                problems.append(f"{_posix(path)}: the folder has no *{_FILE_SUFFIX} file")
        elif mode is None or not stat.S_ISREG(mode):
            problems.append(f"{_posix(path)}: no such file or folder")
        elif path.name.endswith(_FILE_SUFFIX):
            files.append(path)
        else:
            problems.append(f"{_posix(path)}: the file is not named *{_FILE_SUFFIX}")
    if problems:
        raise _UsageError(*problems)
    return files


def _read_texts(files: Sequence[Path]) -> list[tuple[Path, str]]:
    """Return each file with its text, decoded from UTF-8 and otherwise as written, so a byte
    order mark and CRLF line endings reach the reader. Raises `_UsageError` naming each file
    that cannot be read."""
    texts: list[tuple[Path, str]] = []
    problems: list[str] = []
    for path in files:
        try:
            texts.append((path, path.read_bytes().decode("utf-8")))
        except OSError as error:
            problems.append(f"{_posix(path)}: cannot read the file: {error.strerror or error}")
        except UnicodeDecodeError as error:
            problems.append(
                f"{_posix(path)}: the file is not UTF-8 text ({error.reason} at byte {error.start})"
            )
    if problems:
        raise _UsageError(*problems)
    return texts


def _check(path: Path, text: str, service: str | None) -> tuple[object, list[Issue]]:
    """Read and check one contract file, and return the document and its issues.

    A text that `yaml_strict.load` refuses has no document and gives the one issue `strict_yaml`
    at `$`, which carries the line and the column of the refusal.
    """
    try:
        doc = yaml_strict.load(text)
    except StrictYamlError as error:
        return None, [Issue("$", "strict_yaml", str(error))]
    return doc, validate_python_contract(doc, path=_posix(path), service=service)


def _log_issues(path: Path, issues: Sequence[Issue]) -> None:
    for issue in issues:
        _logger.error("%s: %s", _posix(path), issue)


def _print_json(document: object) -> None:
    # Stdout is the machine-read result of the command, so this is a print and not a log line.
    print(json.dumps(document, indent=2))


def _validate(args: argparse.Namespace) -> int:
    texts = _read_texts(_contract_files(args.paths))
    results: list[tuple[Path, list[Issue]]] = []
    for path, text in texts:
        _, issues = _check(path, text, args.service)
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
                            {"path": i.path, "reason": i.reason, "message": i.message}
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
    except OSError as error:
        raise _UsageError(_cannot(path, "look up the path", error)) from error
    if mode is not None and stat.S_ISDIR(mode):
        raise _UsageError(f"{_posix(path)}: hash takes one contract file, not a folder")
    ((path, text),) = _read_texts(_contract_files([path]))
    doc, issues = _check(path, text, args.service)
    if issues:
        _log_issues(path, issues)
        return _INVALID
    try:
        # No issue means the document is a mapping, which the schema check guarantees.
        stored = normalise_python(
            cast(Mapping[str, Any], doc), service=args.service, reads=args.read
        )
    except HashInputError as error:
        raise _UsageError(str(error)) from error
    result = contract_hash(stored)
    if args.json:
        _print_json({"contract_hash": result, "stored": stored})
    else:
        print(result)
    return _VALID


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="continuo-contracts",
        description="Check the ODCS contract of a Python node and print its contract_hash.",
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="{validate,hash}")
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
        "--service",
        type=_service,
        help=(
            "the service of the release: a `service` property that names another one is an "
            "issue. Without it that check is skipped"
        ),
    )
    validate.set_defaults(handler=_validate)

    hash_ = commands.add_parser(
        "hash",
        parents=[common],
        help="check one contract file and print its contract_hash",
        description=_HASH_DESCRIPTION,
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hash_.add_argument(
        "file", type=Path, metavar="FILE", help=f"the contract file, named *{_FILE_SUFFIX}"
    )
    hash_.add_argument(
        "--service",
        type=_service,
        required=True,
        help="the service of the release that carries the contract",
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
    hash_.set_defaults(handler=_hash)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the `continuo-contracts` command with the arguments `argv` and return its exit status.

    `argv` defaults to `sys.argv[1:]`. The status is 0 when every contract is valid, 1 when a
    contract has an issue and 2 when the command could not run. A usage error of the argument
    parser exits the process with status 2 through `argparse`, and so does `--help` with 0.
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    _logger.info("%s", _FAMILY)
    try:
        status: int = args.handler(args)
    except _UsageError as error:
        for problem in error.problems:
            _logger.error("%s", problem)
        return _USAGE
    return status
