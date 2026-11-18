#!/usr/bin/env python3
"""A stand-in for the `dbt` executable, for the tests of `continuo-contracts dbt check`.

The check runs `dbt parse --project-dir ... --profiles-dir ... --target-path <folder>` and
reads `<folder>/manifest.json`. This script does what the check needs of that run, and nothing
else: it records how it was called, writes a manifest and exits with a status the test chooses.
It uses the standard library only, so any Python runs it. The tests start it through a small
wrapper that names the interpreter, so the shebang line is not relied on.

The test sets these environment variables, which the check passes through to its child:

- `FAKE_DBT_RECORD`: the file that receives, as JSON, `argv` (without the program name),
  `environ`, `cwd` and `target_exists` (whether the `--target-path` folder is there when the
  script starts). It is written before anything else.
- `FAKE_DBT_MANIFEST`: a manifest file, `.json` or `.json.gz`, that the script copies to
  `<--target-path>/manifest.json` when it exits with status 0. Without it no manifest is written.
- `FAKE_DBT_STATUS`: the exit status, 0 by default. A script that exits with another status
  writes no manifest.
- `FAKE_DBT_STDOUT` and `FAKE_DBT_STDERR`: the text the script writes to its standard output and
  to its standard error, in that order. A byte that is not UTF-8 is written as an escaped byte,
  a lone surrogate from U+DC80 to U+DCFF.
"""

import gzip
import json
import os
import shutil
import sys

from pathlib import Path


def _target_path(argv: list[str]) -> Path:
    """Return the value of `--target-path`."""
    return Path(argv[argv.index("--target-path") + 1])


def _write_manifest(source: Path, target: Path) -> None:
    """Copy the manifest `source`, decompressed when it is gzipped, into `target`."""
    target.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".gz":
        with gzip.open(source, "rb") as stream:
            (target / "manifest.json").write_bytes(stream.read())
    else:
        shutil.copyfile(source, target / "manifest.json")


def main(argv: list[str]) -> int:
    """Record the call, write what the environment asks for and return the exit status."""
    target = _target_path(argv)
    record = {
        "argv": argv,
        "environ": dict(os.environ),
        "cwd": os.getcwd(),
        "target_exists": target.is_dir(),
    }
    Path(os.environ["FAKE_DBT_RECORD"]).write_text(
        json.dumps(record), encoding="utf-8"
    )
    # This is the output of the program the check runs, which the check captures. The bytes are
    # those of the variable, so that a test can write output that is not UTF-8 with an escaped
    # byte, which `os.environ` reads as a lone surrogate.
    for stream, name in (
        (sys.stdout, "FAKE_DBT_STDOUT"),
        (sys.stderr, "FAKE_DBT_STDERR"),
    ):
        text = os.environ.get(name, "")
        stream.buffer.write(text.encode("utf-8", "surrogateescape"))
        stream.flush()
    status = int(os.environ.get("FAKE_DBT_STATUS", "0"))
    manifest = os.environ.get("FAKE_DBT_MANIFEST")
    if status == 0 and manifest:
        _write_manifest(Path(manifest), target)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
