"""Exercise an installed `continuo_contracts` from outside the source tree.

Install the wheel into a clean virtual environment, then run this script with that environment's
interpreter in isolated mode, from a directory that is not the checkout:
`<venv>/bin/python -I scripts/smoke_installed.py`. The package must come from a `site-packages`
directory, `fingerprints.yaml` must load with entries in every section, the packaged ODCS schema
must load with its pinned bytes, every case of the six vector files must pass when read through
`importlib.resources`, and canonical JSON must give the documented bytes. Exit status 0 means all
of it held, 1 means a failure was logged.
"""

import json
import logging
import sys
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, cast

import continuo_contracts
from continuo_contracts import schema, yaml_strict
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import contract_hash, normalise_python
from continuo_contracts.errors import (
    CanonicalJsonError,
    ContractsError,
    HashInputError,
    ProjectionError,
    StrictYamlError,
)
from continuo_contracts.fingerprints import SECTIONS, load
from continuo_contracts.hashing import fold3, fold4, shared_fold, shared_hash
from continuo_contracts.python import (
    PythonNode,
    canonical_sql_type,
    code_projection,
    projection_hash,
)
from continuo_contracts.validate import validate_python_contract

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


def _schema_failures() -> list[str]:
    """Return a problem when the packaged ODCS schema does not load."""
    try:
        loaded = schema.load()
    except ContractsError as error:
        return [f"the packaged ODCS schema does not load: {error}"]
    if "$defs" not in loaded:
        return ["the packaged ODCS schema has no $defs"]
    return []


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


def _canonical_vector_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the canonical JSON vector cases that are written differently or not refused."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        refused = bool(case.get("error"))
        value = json.loads(case["value_text"]) if refused else case["value"]
        try:
            written: bytes | None = canonical_json(value)
        except CanonicalJsonError:
            written = None
        if refused and written is not None:
            failures.append(f"canonical json vector {name!r}: the value is not refused")
        elif not refused and written != case["canonical"].encode("utf-8"):
            failures.append(f"canonical json vector {name!r}: written as {written!r}")
    return failures


def _hash_vector(case: dict[str, Any]) -> str:
    """Return what the function named by the hashing vector `case` gives for its inputs."""
    function, inputs = case["function"], case["inputs"]
    if function == "fold3":
        return fold3(**inputs)
    if function == "fold4":
        return fold4(**inputs)
    if function == "shared_fold":
        return shared_fold(inputs["unit_digests"])
    if function == "shared_hash":
        return shared_hash({key: bytes.fromhex(hex_) for key, hex_ in inputs["units_hex"].items()})
    raise ValueError(f"unknown function {function!r}")


def _hashing_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the hashing vector cases that give another hash or are not refused."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            got: str | None = _hash_vector(case)
        except HashInputError:
            got = None
        except ValueError as error:
            failures.append(f"hashing vector {name!r}: {error}")
            continue
        if case.get("error"):
            if got is not None:
                failures.append(f"hashing vector {name!r}: the inputs are not refused")
        elif got != case["expected"]:
            failures.append(f"hashing vector {name!r}: expected {case['expected']!r}, got {got!r}")
    return failures


def _strict_yaml_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the strict YAML vector cases that read as another value or are not refused."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            value = yaml_strict.load(case["text"])
        except StrictYamlError:
            if not case.get("error"):
                failures.append(f"strict yaml vector {name!r}: the text is refused")
            continue
        if case.get("error"):
            failures.append(f"strict yaml vector {name!r}: the text is not refused")
        # `json.dumps` tells `1`, `1.0` and `true` apart, which `==` does not.
        elif json.dumps(value, sort_keys=True) != json.dumps(case["value"], sort_keys=True):
            failures.append(f"strict yaml vector {name!r}: read as {value!r}")
    return failures


def _contract_result(case: dict[str, Any]) -> dict[str, Any]:
    """Return the stored form and the hash of the contract vector `case`, or its issues as
    `{path, reason}`, a strict YAML refusal being the issue `{"path": "$", "reason":
    "strict_yaml"}`."""
    try:
        doc = yaml_strict.load(case["text"])
    except StrictYamlError:
        return {"issues": [{"path": "$", "reason": "strict_yaml"}]}
    issues = validate_python_contract(doc, path=case["path"], service=case["service"])
    if issues:
        return {"issues": [{"path": issue.path, "reason": issue.reason} for issue in issues]}
    # The contract check accepts only a mapping, so `doc` is one here.
    stored = normalise_python(
        cast(Mapping[str, Any], doc), service=case["service"], reads=case["reads"]
    )
    return {"stored": stored, "contract_hash": contract_hash(stored)}


def _contract_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the contract vector cases whose stored form, hash or issues differ."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            result = _contract_result(case)
        except ContractsError as error:
            failures.append(f"contract vector {name!r}: refused: {error}")
            continue
        if "issues" in case:
            if result != {"issues": case["issues"]}:
                failures.append(f"contract vector {name!r}: gave {result!r}")
        elif "issues" in result:
            failures.append(f"contract vector {name!r}: issues {result['issues']!r}")
        else:
            if result["stored"] != case["stored"]:
                failures.append(f"contract vector {name!r}: the stored form differs")
            if result["contract_hash"] != case["contract_hash"]:
                failures.append(f"contract vector {name!r}: the hash is {result['contract_hash']}")
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
    canonical_vectors = _vector_cases("canonical_json.json")
    hashing = _vector_cases("hashing.json")
    strict_yaml = _vector_cases("strict_yaml.json")
    contracts = _vector_cases("contracts.json")
    failures = [
        *_installed_failures(),
        *_fingerprint_failures(),
        *_schema_failures(),
        *_projection_failures(projections),
        *_sql_type_failures(sql_types),
        *_canonical_vector_failures(canonical_vectors),
        *_hashing_failures(hashing),
        *_strict_yaml_failures(strict_yaml),
        *_contract_failures(contracts),
        *_canonical_json_failures(),
    ]
    for name, cases in (
        ("projection.json", projections),
        ("sql_types.json", sql_types),
        ("canonical_json.json", canonical_vectors),
        ("hashing.json", hashing),
        ("strict_yaml.json", strict_yaml),
        ("contracts.json", contracts),
    ):
        if not cases:
            failures.append(f"{name} holds no cases")
    for failure in failures:
        logger.error("%s", failure)
    if failures:
        return 1
    logger.info(
        "continuo-contracts %s installed at %s: %d projection, %d sql type, %d canonical json, "
        "%d hashing, %d strict yaml and %d contract cases pass",
        continuo_contracts.__version__,
        Path(continuo_contracts.__file__).parent,
        len(projections),
        len(sql_types),
        len(canonical_vectors),
        len(hashing),
        len(strict_yaml),
        len(contracts),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
