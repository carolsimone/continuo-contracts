"""Exercise an installed `continuo_contracts` from outside the source tree.

Install the wheel into a clean virtual environment, then run this script with that environment's
interpreter in isolated mode, from a directory that is not the checkout:
`<venv>/bin/python -I scripts/smoke_installed.py`. The package must come from a `site-packages`
directory, `fingerprints.yaml` must load with entries in every section, every case of both vector
files must pass when read through `importlib.resources`, and canonical JSON must give the
documented bytes. Exit status 0 means all of it held, 1 means a failure was logged.
"""

import json
import logging
import sys
from importlib import resources
from pathlib import Path
from typing import Any

import continuo_contracts
from continuo_contracts.canonical import canonical_json
from continuo_contracts.errors import ProjectionError
from continuo_contracts.fingerprints import SECTIONS, load
from continuo_contracts.python import (
    PythonNode,
    canonical_sql_type,
    code_projection,
    projection_hash,
)

logger = logging.getLogger(__name__)

# "e acute" and its two UTF-8 bytes, built from numbers so that the file stays ASCII.
NON_ASCII = chr(0xE9)
NON_ASCII_UTF8 = bytes([0xC3, 0xA9])


def _vector_cases(name: str) -> list[dict[str, Any]]:
    """Return the cases of the packaged vector file `name`."""
    resource = resources.files("continuo_contracts") / "vectors" / name
    cases: list[dict[str, Any]] = json.loads(resource.read_text(encoding="utf-8"))
    return cases


def _installed_failures() -> list[str]:
    """Return a problem when the package was not imported from a `site-packages` directory."""
    location = Path(continuo_contracts.__file__)
    if "site-packages" in location.parts:
        return []
    return [f"continuo_contracts was imported from {location}, not from a site-packages directory"]


def _fingerprint_failures() -> list[str]:
    """Return the sections of the packaged fingerprints file that have no entries."""
    fingerprints = load()
    return [
        f"fingerprints.yaml section {section!r} has no entries"
        for section in SECTIONS
        if not fingerprints.entries(section)
    ]


def _projection_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the Python projection vector cases whose projection or hash differs."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            node = PythonNode.from_mapping(case["node"])
            projection = code_projection(node)
            digest = projection_hash(node)
        except ProjectionError as error:
            failures.append(f"projection vector {name!r}: refused: {error}")
            continue
        if projection != case["projection"]:
            failures.append(f"projection vector {name!r}: the code projection differs")
        if digest != case["projection_hash"]:
            failures.append(f"projection vector {name!r}: the hash is {digest}")
    return failures


def _sql_type_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the SQL type vector cases that canonicalise to another spelling."""
    failures: list[str] = []
    for case in cases:
        raw, expected = case["raw"], case["canonical"]
        try:
            got: str | None = canonical_sql_type(raw)
        except ProjectionError:
            got = None
        if got != expected:
            failures.append(f"sql type vector {raw!r}: expected {expected!r}, got {got!r}")
    return failures


def _canonical_json_failures() -> list[str]:
    """Return a problem when `canonical_json` does not give the documented bytes."""
    expected = b'{"a":true,"b":[1,"' + NON_ASCII_UTF8 + b'"]}'
    got = canonical_json({"b": [1.0, NON_ASCII], "a": True})
    if got == expected:
        return []
    return [f"canonical_json gave {got!r}, expected {expected!r}"]


def main() -> int:
    """Run every check and return the exit status."""
    projections = _vector_cases("projection.json")
    sql_types = _vector_cases("sql_types.json")
    failures = [
        *_installed_failures(),
        *_fingerprint_failures(),
        *_projection_failures(projections),
        *_sql_type_failures(sql_types),
        *_canonical_json_failures(),
    ]
    if not projections:
        failures.append("projection.json holds no cases")
    if not sql_types:
        failures.append("sql_types.json holds no cases")
    for failure in failures:
        logger.error("%s", failure)
    if failures:
        return 1
    logger.info(
        "continuo-contracts %s installed at %s: %d projection and %d sql type cases pass",
        continuo_contracts.__version__,
        Path(continuo_contracts.__file__).parent,
        len(projections),
        len(sql_types),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
