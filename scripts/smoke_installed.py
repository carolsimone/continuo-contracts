"""Exercise an installed `continuo_contracts` from outside the source tree.

Install the wheel into a clean virtual environment, then run this script with that environment's
interpreter in isolated mode, from a directory that is not the checkout:
`<venv>/bin/python -I scripts/smoke_installed.py`. The package must come from a `site-packages`
directory, `fingerprints.yaml` must load with entries in every section, the packaged ODCS schema
must load with its pinned bytes, every case of the eight vector files must pass when read through
`importlib.resources`, canonical JSON must give the documented bytes, and the installed console
script `continuo-contracts` must run `dbt check` on a manifest and refuse a folder that does not
exist. Exit status 0 means all of it held, 1 means a failure was logged.
"""

import json
import logging
import shutil
import subprocess
import sys
import tempfile

from collections.abc import Mapping
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any, cast

import continuo_contracts

from continuo_contracts import ontology, schema, yaml_strict
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import (
    contract_hash,
    normalise_dbt,
    normalise_python,
)
from continuo_contracts.dbt import generate
from continuo_contracts.errors import (
    CanonicalJsonError,
    ContractsError,
    HashInputError,
    ProjectionError,
    StrictYamlError,
)
from continuo_contracts.fingerprints import SECTIONS, load
from continuo_contracts.hashing import fold3, fold4, shared_fold, shared_hash
from continuo_contracts.ontology_check import PER_PROJECT_RULES, check
from continuo_contracts.python import (
    PythonNode,
    canonical_sql_type,
    code_projection,
    projection_hash,
)
from continuo_contracts.validate import (
    validate_dbt_contract,
    validate_python_contract,
)

logger = logging.getLogger(__name__)

# "e acute" and its two UTF-8 bytes, built from numbers so that the file stays ASCII.
NON_ASCII = chr(0xE9)
NON_ASCII_UTF8 = bytes([0xC3, 0xA9])

# The smallest manifest that `dbt check` turns into a contract: one model with an owner and a tag.
DBT_MANIFEST = {
    "metadata": {"project_name": "smoke_project"},
    "nodes": {
        "model.smoke_project.orders": {
            "resource_type": "model",
            "unique_id": "model.smoke_project.orders",
            "name": "orders",
            "schema": "analytics",
            "fqn": ["smoke_project", "orders"],
            "tags": ["daily"],
            "original_file_path": "models/orders.sql",
            "config": {"materialized": "table", "meta": {"owner": "data-team"}},
        }
    },
}


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
    return [
        f"continuo_contracts was imported from {location}, not from a site-packages directory"
    ]


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
            failures.append(
                f"projection vector {name!r}: the code projection differs"
            )
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
            failures.append(
                f"sql type vector {raw!r}: expected {expected!r}, got {got!r}"
            )
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
            failures.append(
                f"canonical json vector {name!r}: the value is not refused"
            )
        elif not refused and written != case["canonical"].encode("utf-8"):
            failures.append(
                f"canonical json vector {name!r}: written as {written!r}"
            )
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
        return shared_hash(
            {
                key: bytes.fromhex(hex_)
                for key, hex_ in inputs["units_hex"].items()
            }
        )
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
                failures.append(
                    f"hashing vector {name!r}: the inputs are not refused"
                )
        elif got != case["expected"]:
            failures.append(
                f"hashing vector {name!r}: expected {case['expected']!r}, got {got!r}"
            )
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
                failures.append(
                    f"strict yaml vector {name!r}: the text is refused"
                )
            continue
        if case.get("error"):
            failures.append(
                f"strict yaml vector {name!r}: the text is not refused"
            )
        # `json.dumps` tells `1`, `1.0` and `true` apart, which `==` does not.
        elif json.dumps(value, sort_keys=True) != json.dumps(
            case["value"], sort_keys=True
        ):
            failures.append(f"strict yaml vector {name!r}: read as {value!r}")
    return failures


def _contract_result(case: dict[str, Any]) -> dict[str, Any]:
    """Return the stored form and the hash of the contract vector `case`.

    A contract with issues returns them as `{path, reason}` instead, and a strict
    YAML refusal is the issue `{"path": "$", "reason": "strict_yaml"}`.
    """
    try:
        doc = yaml_strict.load(case["text"])
    except StrictYamlError:
        return {"issues": [{"path": "$", "reason": "strict_yaml"}]}
    issues = validate_python_contract(
        doc, path=case["path"], project=case["project"]
    )
    if issues:
        return {
            "issues": [
                {"path": issue.path, "reason": issue.reason} for issue in issues
            ]
        }
    # The contract check accepts only a mapping, so `doc` is one here.
    stored = normalise_python(
        cast(Mapping[str, Any], doc), project=case["project"], reads=case["reads"]
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
            failures.append(
                f"contract vector {name!r}: issues {result['issues']!r}"
            )
        else:
            if result["stored"] != case["stored"]:
                failures.append(
                    f"contract vector {name!r}: the stored form differs"
                )
            if result["contract_hash"] != case["contract_hash"]:
                failures.append(
                    f"contract vector {name!r}: the hash is {result['contract_hash']}"
                )
    return failures


def _dbt_contract_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the dbt contract vector cases whose contract, flags, hashes or issues differ.

    Every case pins the `source_sha256` of its inputs. A case with `issues` is a node whose
    generated contract is not valid, and `issues` lists what `validate_dbt_contract` finds in it.
    The generated contract of any other case is valid, and the case pins its flags, its stored
    form and its `contract_hash`.
    """
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            generated = generate(
                case["node"], case["tests"], manifest=case["manifest"]
            )
            issues = [
                {"path": issue.path, "reason": issue.reason}
                for issue in validate_dbt_contract(generated.document)
            ]
            if generated.source_sha256 != case["source_sha256"]:
                failures.append(
                    f"dbt contract vector {name!r}: the source_sha256 is "
                    f"{generated.source_sha256}"
                )
            if "issues" in case:
                if issues != case["issues"]:
                    failures.append(
                        f"dbt contract vector {name!r}: the issues are {issues!r}"
                    )
                continue
            stored = normalise_dbt(generated.document, reads=case["reads"])
            digest = contract_hash(stored)
        except ContractsError as error:
            failures.append(f"dbt contract vector {name!r}: refused: {error}")
            continue
        if issues:
            failures.append(
                f"dbt contract vector {name!r}: the generated contract has issues {issues!r}"
            )
        if generated.document != case["generated"]:
            failures.append(
                f"dbt contract vector {name!r}: the generated contract differs"
            )
        if list(generated.flags) != case["flags"]:
            failures.append(
                f"dbt contract vector {name!r}: the flags are {generated.flags!r}"
            )
        if stored != case["stored"]:
            failures.append(
                f"dbt contract vector {name!r}: the stored form differs"
            )
        if digest != case["contract_hash"]:
            failures.append(f"dbt contract vector {name!r}: the hash is {digest}")
    return failures


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the installed console script `script` with `args` and return the finished process."""
    return subprocess.run(
        [script, *args], capture_output=True, text=True, check=False
    )


def _dbt_check_failures() -> list[str]:
    """Return what the installed console script gets wrong when it runs `dbt check`.

    It must find no error in a manifest that holds one model with an owner and a tag, print
    one JSON document that says so with one `contract_hash`, and exit with status 2, printing
    nothing, for a project folder that does not exist. It runs no `dbt`: it reads the manifest.
    """
    script = shutil.which(
        "continuo-contracts", path=str(Path(sys.executable).parent)
    )
    if script is None:
        return [
            "the console script continuo-contracts is not installed beside this Python"
        ]
    with tempfile.TemporaryDirectory() as folder:
        project = Path(folder) / "smoke_project"
        project.mkdir()
        manifest = Path(folder) / "manifest.json"
        manifest.write_text(json.dumps(DBT_MANIFEST), encoding="utf-8")
        check = ("dbt", "check")
        checked = _run_script(
            script, *check, str(project), "--manifest", str(manifest), "--json"
        )
        refused = _run_script(
            script, *check, str(project / "absent"), "--manifest", str(manifest)
        )
    failures: list[str] = []
    if checked.returncode != 0:
        failures.append(
            f"dbt check exited with {checked.returncode}: {checked.stderr!r}"
        )
    else:
        try:
            document = json.loads(checked.stdout)
        except ValueError:
            return [
                *failures,
                f"dbt check --json printed {checked.stdout!r}, not JSON",
            ]
        hashes = list(document.get("contracts", {}).values())
        if document.get("lite") is not True or document.get("ok") is not True:
            failures.append(f"dbt check did not say lite and ok: {document!r}")
        if len(hashes) != 1 or not str(hashes[0]).startswith("sha256:"):
            failures.append(
                f"dbt check gave the contracts {hashes!r}, not one hash"
            )
    if refused.returncode != 2 or refused.stdout:
        failures.append(
            f"dbt check of a missing folder exited with {refused.returncode} and printed "
            f"{refused.stdout!r}, not 2 and nothing"
        )
    return failures


def _ontology_failures(cases: list[dict[str, Any]]) -> list[str]:
    """Return the ontology vector cases whose resolved form, hash or findings differ."""
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        try:
            loaded = ontology.load(case["ontology"])
            resolved = ontology.resolve(case["stored"], loaded)
            found = [
                asdict(finding)
                for finding in check(loaded, [resolved], rules=PER_PROJECT_RULES)
            ]
        except ContractsError as error:
            failures.append(f"ontology vector {name!r}: refused: {error}")
            continue
        if resolved != case["resolved"]:
            failures.append(
                f"ontology vector {name!r}: the resolved form differs"
            )
        if contract_hash(resolved) != case["contract_hash"]:
            failures.append(
                f"ontology vector {name!r}: the hash is {contract_hash(resolved)}"
            )
        if found != case["findings"]:
            failures.append(
                f"ontology vector {name!r}: the findings are {found!r}"
            )
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
    dbt_contracts = _vector_cases("dbt_contracts.json")
    ontologies = _vector_cases("ontology.json")
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
        *_dbt_contract_failures(dbt_contracts),
        *_ontology_failures(ontologies),
        *_canonical_json_failures(),
        *_dbt_check_failures(),
    ]
    for name, cases in (
        ("projection.json", projections),
        ("sql_types.json", sql_types),
        ("canonical_json.json", canonical_vectors),
        ("hashing.json", hashing),
        ("strict_yaml.json", strict_yaml),
        ("contracts.json", contracts),
        ("dbt_contracts.json", dbt_contracts),
        ("ontology.json", ontologies),
    ):
        if not cases:
            failures.append(f"{name} holds no cases")
    for failure in failures:
        logger.error("%s", failure)
    if failures:
        return 1
    logger.info(
        "continuo-contracts %s installed at %s: %d projection, %d sql type, %d canonical json, "
        "%d hashing, %d strict yaml, %d contract, %d dbt contract and %d ontology cases pass",
        continuo_contracts.__version__,
        Path(continuo_contracts.__file__).parent,
        len(projections),
        len(sql_types),
        len(canonical_vectors),
        len(hashing),
        len(strict_yaml),
        len(contracts),
        len(dbt_contracts),
        len(ontologies),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    raise SystemExit(main())
