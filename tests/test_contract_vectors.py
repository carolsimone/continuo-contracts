"""The `contracts.json` vectors: each case's text, read strictly, checked, stored and hashed."""

import json
from importlib import resources
from pathlib import Path

import pytest

from continuo_contracts import yaml_strict
from continuo_contracts.contract import contract_hash, normalise_python
from continuo_contracts.errors import StrictYamlError
from continuo_contracts.validate import validate_python_contract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILE = resources.files("continuo_contracts") / "vectors" / "contracts.json"
CASES = json.loads(FILE.read_text(encoding="utf-8"))
VALID = [case for case in CASES if "issues" not in case]
INVALID = [case for case in CASES if "issues" in case]

# Each valid case whose text is a fixture of this repository, with that fixture.
FIXTURE_OF = {
    "demo_py_daily_kpis": FIXTURES / "demo/service-py/contracts/py_daily_kpis.odcs.yaml",
    "demo_orders_csv": FIXTURES / "demo/service-py/contracts/demo_orders_csv.odcs.yaml",
    "demo_fx_eur_usd_rates": FIXTURES / "demo/service-py/contracts/fx_eur_usd_rates.odcs.yaml",
    "full_python_node": FIXTURES / "full/python_node.odcs.yaml",
    "full_python_csv": FIXTURES / "full/python_csv.odcs.yaml",
    "full_python_api": FIXTURES / "full/python_api.odcs.yaml",
}


def _run(case):
    """Return the stored form and the hash of `case`, or its issues as `{path, reason}`."""
    try:
        doc = yaml_strict.load(case["text"])
    except StrictYamlError:
        return {"issues": [{"path": "$", "reason": "strict_yaml"}]}
    issues = validate_python_contract(doc, path=case["path"], service=case["service"])
    if issues:
        return {"issues": [{"path": issue.path, "reason": issue.reason} for issue in issues]}
    stored = normalise_python(doc, service=case["service"], reads=case["reads"])
    return {"stored": stored, "contract_hash": contract_hash(stored)}


# The case that writes the full python-api contract in every spelling the canonical form removes.
MESSY = "messy_python_api"
# What its text writes, each something the stored form of the clean contract does not hold.
MESSY_SPELLINGS = [
    "\ufeff",
    "\r\n",
    "tags: [rates, api, rates]",
    "severity: ERROR",
    "type: library",
    "logicalType: object",
    "primaryKey: false",
    "physicalName: currency",
    'valueExt: ""',
    '"  Exchange rates',
    "{vendor: atlan, property: glossaryTerm, value: Exchange rate}",
]


def test_the_file_holds_the_seven_valid_and_four_invalid_cases():
    assert sorted(case["name"] for case in VALID) == sorted([*FIXTURE_OF, MESSY])
    assert len(INVALID) == 4
    assert len({case["name"] for case in CASES}) == len(CASES)


def test_every_case_has_the_documented_fields():
    for case in VALID:
        assert set(case) == {
            "name", "path", "service", "reads", "text", "stored", "contract_hash"
        }, case["name"]
    for case in INVALID:
        assert set(case) == {"name", "path", "service", "reads", "text", "issues"}, case["name"]


@pytest.mark.parametrize("case", VALID, ids=lambda case: case["name"])
def test_a_valid_case_stores_and_hashes_as_pinned(case):
    result = _run(case)
    assert "issues" not in result, result
    assert result["stored"] == case["stored"]
    assert result["contract_hash"] == case["contract_hash"]


@pytest.mark.parametrize("case", VALID, ids=lambda case: case["name"])
def test_the_pinned_hash_is_the_hash_of_the_pinned_stored_form(case):
    assert contract_hash(case["stored"]) == case["contract_hash"]


@pytest.mark.parametrize("case", INVALID, ids=lambda case: case["name"])
def test_an_invalid_case_gives_the_pinned_issues(case):
    assert _run(case) == {"issues": case["issues"]}


@pytest.mark.parametrize("name", sorted(FIXTURE_OF))
def test_the_text_of_a_case_is_its_fixture(name):
    case = next(case for case in VALID if case["name"] == name)
    assert case["text"] == FIXTURE_OF[name].read_text(encoding="utf-8")


def test_no_invalid_case_is_refused_by_the_json_schema():
    reasons = {issue["reason"] for case in INVALID for issue in case["issues"]}
    assert "odcs_schema" not in reasons
    assert reasons == {"column_type", "refused_field", "file_name", "strict_yaml"}


def _case(name):
    return next(case for case in CASES if case["name"] == name)


def test_the_messy_case_writes_every_spelling_it_is_there_for():
    text = _case(MESSY)["text"]
    assert text.startswith("\ufeff")
    assert "\n" not in text.replace("\r\n", "")
    for spelling in MESSY_SPELLINGS:
        assert spelling in text, spelling
    atlan = "{vendor: atlan, property: glossaryTerm, value: Exchange rate}"
    assert text.count(atlan) == 2
    assert _case(MESSY)["path"].endswith("/analytics.python_api.odcs.yaml")


def test_the_messy_case_stores_and_hashes_like_the_clean_contract():
    messy, clean = _case(MESSY), _case("full_python_api")
    assert messy["text"] != clean["text"]
    assert messy["stored"] == clean["stored"]
    assert messy["contract_hash"] == clean["contract_hash"]


def test_the_clean_api_contract_has_a_lower_case_severity():
    column = _case("full_python_api")["stored"]["schema"][0]["properties"][0]
    assert column["quality"][0]["severity"] == "error"


def test_the_demo_node_is_stored_with_its_reads():
    case = next(case for case in VALID if case["name"] == "demo_py_daily_kpis")
    assert case["reads"] == ["analytics.daily_transactions"]
    assert case["stored"]["schema"][0]["customProperties"] == [
        {"property": "reads", "value": ["analytics.daily_transactions"], "vendor": "continuo"}
    ]
