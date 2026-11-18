"""Build the dbt fixture manifests that the tests read.

Run it with `uv run python scripts/build_dbt_fixtures.py [PROJECT ...]`; with no project it
builds all five. For each project under `tests/fixtures/dbt/projects` it copies the project, and
the local packages it names, to a temporary directory, runs `dbt deps` (when the project has a
`packages.yml`) and `dbt parse` with the pinned dbt in a `TZ=Asia/Tokyo` environment, and writes
the manifest it parsed to `tests/fixtures/dbt/manifests/<project>.json.gz`. The environment is
the caller's less every `DBT_` and `POSTGRES_` variable, so a machine's settings never reach a
manifest.

The pinned dbt runs through `uvx` on Python 3.12, so the script needs no dbt of its own. The
copy lives under the temporary directory of the platform; set `TMPDIR` to build elsewhere. The
manifest is written so that a rebuild gives the same bytes: the run's clock values are fixed,
the keys that hold a time of the build or a path of the build directory are dropped, the keys
are sorted except in the `columns` mappings, which keep the order in which a node declares its
columns, and the gzip header carries no time. It uses the standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "tests" / "fixtures" / "dbt" / "projects"
MANIFESTS = ROOT / "tests" / "fixtures" / "dbt" / "manifests"

NAMES = ("core", "finance", "marketing", "service_1", "cases")
DBT_VERSION = "1.12.0b1"
DBT_COMMAND = (
    "uvx",
    "--python",
    "3.12",
    "--from",
    f"dbt-core=={DBT_VERSION}",
    "--with",
    "dbt-postgres==1.10.0",
    "--with",
    "dbt-adapters==1.24.5",
    "--with",
    "dbt-common==1.39.0",
    "dbt",
)
# A model's `deprecation_date` has no zone, and dbt reads it in the zone of the machine that
# parses. East of UTC that is the hardest case for a reader that wants the date as written.
TIME_ZONE = "Asia/Tokyo"
FIXED_METADATA = {
    "generated_at": "2026-01-01T00:00:00.000000Z",
    "invocation_id": "00000000-0000-0000-0000-000000000000",
    "invocation_started_at": "2026-01-01T00:00:00.000000Z",
    "run_started_at": "2026-01-01T00:00:00.000000+00:00",
}
# The entries of these manifest sections carry the time of the build (`created_at`) or the path
# of the project (`root_path`), so a rebuild would write other bytes.
VOLATILE_KEYS = ("created_at", "root_path")
VOLATILE_SECTIONS = ("nodes", "sources", "macros")
# The mapping under this key lists a node's columns in the order the node declares them, and
# the contract of the node follows that order, so the manifest keeps it.
DECLARED_ORDER_KEY = "columns"
# What dbt leaves in a project directory that it has run in; none of it is part of a project.
BUILD_LEFTOVERS = ("target", "logs", "dbt_packages", "package-lock.yml")
LOCAL_PACKAGE = re.compile(r"^\s*-\s*local:\s*(\.\./[\w.-]+)\s*$", re.MULTILINE)
# The caller's variables a build drops: dbt's own settings, and the Postgres settings that the
# fixture profiles read (`POSTGRES_DB` names the database every node carries), so the profiles'
# defaults apply. The fixture macros read `DBT_TARGET_SCHEMA`, which the first prefix drops.
DROPPED_PREFIXES = ("DBT_", "POSTGRES_")


class BuildError(Exception):
    """A dbt command failed, or the pinned dbt could not be started."""


def _environment() -> dict[str, str]:
    """Return the environment of a build: the caller's, less its dbt and Postgres settings.

    It adds the settings of the build.
    """
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(DROPPED_PREFIXES)
    }
    env.update(
        {
            "TZ": TIME_ZONE,
            "DO_NOT_TRACK": "1",
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            "DBT_USE_COLORS": "false",
        }
    )
    return env


def _run_dbt(command: str, project_dir: Path) -> None:
    """Run one dbt command on `project_dir`; raise `BuildError` when it fails."""
    args = [
        *DBT_COMMAND,
        command,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
    ]
    logger.info("dbt %s %s", command, project_dir.name)
    try:
        done = subprocess.run(
            args,
            env=_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BuildError(f"cannot start {args[0]}: {exc}") from exc
    if done.returncode != 0:
        raise BuildError(
            f"dbt {command} failed with status {done.returncode}\n"
            f"{done.stdout}{done.stderr}"
        )


def _fix_manifest(manifest: dict[str, Any]) -> None:
    """Fix the run's clock values and drop what changes with each build, in place."""
    manifest["metadata"].update(FIXED_METADATA)
    for section in VOLATILE_SECTIONS:
        for entry in manifest.get(section, {}).values():
            for key in VOLATILE_KEYS:
                entry.pop(key, None)


def ordered(value: Any, declared: bool = False) -> Any:
    """Return `value` with every mapping in sorted key order, except the declared ones.

    A mapping under a `columns` key keeps its key order, which is the order in which the node
    declares its columns, and the dict of each column in it is sorted as any other mapping.
    `declared` says that `value` is such a mapping. Lists keep their order and their items are
    ordered in turn; the argument is not changed.
    """
    if isinstance(value, dict):
        keys = list(value) if declared else sorted(value)
        return {
            key: ordered(value[key], not declared and key == DECLARED_ORDER_KEY)
            for key in keys
        }
    if isinstance(value, list):
        return [ordered(item) for item in value]
    return value


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the gzipped JSON of `manifest` in `ordered` form, the same bytes every time."""
    text = json.dumps(ordered(manifest), indent=1)
    return gzip.compress(text.encode("ascii"), mtime=0)


def build(name: str, workdir: Path | None = None) -> bytes:
    """Parse the project `name` with the pinned dbt and return its manifest file's bytes.

    The copy of the project lives in a new directory under `workdir`, or under the temporary
    directory of the platform when `workdir` is `None`.

    Raises
    ------
    BuildError
        When a dbt command fails or `uvx` cannot start it.
    """
    ignore = shutil.ignore_patterns(*BUILD_LEFTOVERS)
    with tempfile.TemporaryDirectory(
        prefix="continuo-dbt-fixture-", dir=workdir
    ) as tmp:
        project = Path(tmp) / name
        shutil.copytree(PROJECTS / name, project, ignore=ignore)
        packages = project / "packages.yml"
        if packages.is_file():
            for local in LOCAL_PACKAGE.findall(
                packages.read_text(encoding="utf-8")
            ):
                shutil.copytree(
                    (PROJECTS / name / local).resolve(),
                    (project / local).resolve(),
                    ignore=ignore,
                )
            _run_dbt("deps", project)
        _run_dbt("parse", project)
        manifest = json.loads(
            (project / "target" / "manifest.json").read_text(encoding="utf-8")
        )
    _fix_manifest(manifest)
    return manifest_bytes(manifest)


def main(argv: list[str] | None = None) -> int:
    """Build the manifests of the projects named by `argv` and return the exit status."""
    parser = argparse.ArgumentParser(
        description="Build the dbt fixture manifests with the pinned dbt."
    )
    parser.add_argument(
        "projects",
        nargs="*",
        metavar="PROJECT",
        help=f"one of {', '.join(NAMES)}; default: all of them",
    )
    names: list[str] = parser.parse_args(argv).projects or list(NAMES)
    unknown = [name for name in names if name not in NAMES]
    if unknown:
        parser.error(f"unknown project {unknown}; choose from {list(NAMES)}")
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            data = build(name)
        except BuildError as exc:
            logger.error("%s: %s", name, exc)
            return 1
        target = MANIFESTS / f"{name}.json.gz"
        target.write_bytes(data)
        logger.info("wrote %s (%d bytes)", target, len(data))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    raise SystemExit(main())
