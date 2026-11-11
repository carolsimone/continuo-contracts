"""Refuse a built distribution that would install without part of the package.

Run it on a dist directory that holds one wheel and one sdist:
`uv run --no-project --python 3.12 scripts/check_wheel.py dist`. It uses the standard library
only, so any Python 3.12 runs it. The wheel must be pure Python, carry every module and data file
the package reads at run time, carry the licence files, and declare the metadata the package
promises. Exit status 0 means the wheel passed, 1 means at least one problem was logged.
"""

from __future__ import annotations

import argparse
import email.parser
import logging
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

PACKAGE = "continuo_contracts"
WHEEL_SUFFIX = "-py3-none-any.whl"
SDIST_SUFFIX = ".tar.gz"
DIST_INFO_SUFFIX = ".dist-info"
REQUIRES_PYTHON = ">=3.12"
LICENSE_EXPRESSION = "Apache-2.0"
REQUIRES_DIST = ["pyyaml>=6.0"]
LICENSE_FILES = ("LICENSE", "NOTICE")

REQUIRED_MEMBERS: tuple[str, ...] = tuple(
    f"{PACKAGE}/{name}"
    for name in (
        "__init__.py",
        "canonical.py",
        "errors.py",
        "fingerprints.py",
        "fingerprints.yaml",
        "hashing.py",
        "python.py",
        "py.typed",
        "vectors/canonical_json.json",
        "vectors/hashing.json",
        "vectors/projection.json",
        "vectors/sql_types.json",
    )
)


def wheel_problems(wheel: Path) -> list[str]:
    """Return one string for each way `wheel` falls short, or an empty list when it passes."""
    problems: list[str] = []
    if not wheel.name.endswith(WHEEL_SUFFIX):
        problems.append(f"{wheel.name}: the file name must end in {WHEEL_SUFFIX}")
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            for member in REQUIRED_MEMBERS:
                if member not in names:
                    problems.append(f"{wheel.name}: missing member {member}")
            problems.extend(_dist_info_problems(wheel.name, archive, names))
    except zipfile.BadZipFile as error:
        problems.append(f"{wheel.name}: not a readable zip archive: {error}")
    return problems


def _dist_info_problems(label: str, archive: zipfile.ZipFile, names: set[str]) -> list[str]:
    """Return the problems of the licence files and the METADATA of the wheel's `.dist-info`."""
    top_levels = {name.split("/")[0] for name in names}
    directories = sorted(d for d in top_levels if d.endswith(DIST_INFO_SUFFIX))
    if len(directories) != 1:
        return [f"{label}: expected exactly one {DIST_INFO_SUFFIX} directory, found {directories}"]
    info = directories[0]
    problems: list[str] = []
    for license_file in LICENSE_FILES:
        member = f"{info}/licenses/{license_file}"
        if member not in names:
            problems.append(f"{label}: missing member {member}")
    metadata = f"{info}/METADATA"
    if metadata not in names:
        problems.append(f"{label}: missing member {metadata}")
    else:
        problems.extend(_metadata_problems(label, archive.read(metadata).decode("utf-8")))
    return problems


def _metadata_problems(label: str, text: str) -> list[str]:
    """Return the problems of the `METADATA` text of a wheel."""
    message = email.parser.Parser().parsestr(text)
    problems: list[str] = []
    requires_python = str(message.get("Requires-Python", "")).strip()
    if requires_python != REQUIRES_PYTHON:
        problems.append(
            f"{label}: METADATA Requires-Python is {requires_python!r}, "
            f"must be {REQUIRES_PYTHON!r}")
    license_expression = str(message.get("License-Expression", "")).strip()
    if license_expression != LICENSE_EXPRESSION:
        problems.append(
            f"{label}: METADATA License-Expression is {license_expression!r}, "
            f"must be {LICENSE_EXPRESSION!r}")
    declared = message.get_all("Requires-Dist", [])
    requires_dist = [str(requirement).replace(" ", "").lower() for requirement in declared]
    if requires_dist != REQUIRES_DIST:
        problems.append(
            f"{label}: METADATA Requires-Dist is {requires_dist}, must be {REQUIRES_DIST}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the wheel in the dist directory named by `argv` and return the exit status."""
    parser = argparse.ArgumentParser(description="Check the wheel in a dist directory.")
    parser.add_argument("dist", type=Path, help="directory that holds one .whl and one .tar.gz")
    dist: Path = parser.parse_args(argv).dist
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob(f"*{SDIST_SUFFIX}"))
    problems: list[str] = []
    if len(wheels) != 1:
        problems.append(f"{dist}: expected exactly one .whl, found {[w.name for w in wheels]}")
    if len(sdists) != 1:
        found = [s.name for s in sdists]
        problems.append(f"{dist}: expected exactly one {SDIST_SUFFIX}, found {found}")
    if len(wheels) == 1:
        problems.extend(wheel_problems(wheels[0]))
    for problem in problems:
        logger.error("%s", problem)
    if problems:
        return 1
    logger.info("%s passed", wheels[0].name)
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
