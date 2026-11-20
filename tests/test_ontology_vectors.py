"""The `ontology.json` vector: a stored contract and an ontology file, resolved and checked.

Each case holds `ontology`, the text of an ontology file, `stored`, the stored contract of one
node before `resolve`, with the bindings its team wrote, `resolved`, what `resolve` makes of it,
`contract_hash`, the hash of `resolved`, and `findings`. The findings are the ones `check` writes
for `resolved` alone, with the rules of `PER_SERVICE_RULES` (`contract`, `coverage`, `quality` and
`privacy`), the rules that need no other contract, as `{severity, rule, subject, message,
contract_id}`, in the order `check` returns them.
"""

import hashlib
import json

from importlib import resources
from pathlib import Path

import pytest

from ontology_fixtures import stored as bound_stored
from ontology_fixtures import without_filled

from continuo_contracts import ontology
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import canonical_form, contract_hash
from continuo_contracts.ontology_check import PER_SERVICE_RULES, check
from continuo_contracts.validate import schema_issues
from continuo_contracts.vocabulary import continuo_values

VECTORS = resources.files("continuo_contracts") / "vectors"
FILE = VECTORS / "ontology.json"
GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "ontology"
    / "golden"
    / "analytics.fx_transactions_eur.json"
)

# The SHA-256 of the file, which no later change may move.
ONTOLOGY_JSON = "d4a64f5b203ca30f2a534ac35ac0e5131f297505fc863562fabb4562b58d5416"

FIELDS = {"name", "ontology", "stored", "resolved", "contract_hash", "findings"}
FINDING_FIELDS = {"severity", "rule", "subject", "message", "contract_id"}


def _cases():
    return json.loads(FILE.read_text(encoding="utf-8"))


CASES = _cases()
IDS = [case["name"] for case in CASES]


def _kind(document):
    return continuo_values(document["customProperties"])["kind"]


def _service(document):
    return continuo_values(document["customProperties"])["service"]


def _findings(case):
    found = check(
        ontology.load(case["ontology"]),
        [case["resolved"]],
        rules=PER_SERVICE_RULES,
    )
    return [
        {
            "severity": f.severity,
            "rule": f.rule,
            "subject": f.subject,
            "message": f.message,
            "contract_id": f.contract_id,
        }
        for f in found
    ]


def test_the_file_has_its_pinned_bytes():
    assert hashlib.sha256(FILE.read_bytes()).hexdigest() == ONTOLOGY_JSON


def test_the_file_is_ascii_sorted_json_with_one_space_of_indent():
    text = FILE.read_bytes().decode("ascii")

    assert text == json.dumps(CASES, sort_keys=True, indent=1) + "\n"


def test_every_case_holds_the_documented_fields():
    assert len(set(IDS)) == len(IDS)
    for case in CASES:
        assert set(case) == FIELDS, case["name"]
        assert all(set(finding) == FINDING_FIELDS for finding in case["findings"])


def test_the_cases_cover_both_families_and_a_service_the_ontology_does_not_declare():
    families = {_kind(case["stored"]).split("-")[0] for case in CASES}
    undeclared = [
        case
        for case in CASES
        if not ontology.load(case["ontology"]).declares(_service(case["stored"]))
    ]

    assert families == {"dbt", "python"}
    assert [case["name"] for case in undeclared] == ["service_not_declared"]
    assert any(case["findings"] == [] for case in CASES)
    assert any(case["findings"] for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_ontology_vector(case):
    onto = ontology.load(case["ontology"])

    resolved = ontology.resolve(case["stored"], onto)

    assert resolved == case["resolved"]
    assert contract_hash(case["resolved"]) == case["contract_hash"]
    assert _findings(case) == case["findings"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_stored_form_and_its_resolved_form_are_canonical_and_valid_odcs(case):
    for form in (case["stored"], case["resolved"]):
        assert canonical_form(form) == form
        assert schema_issues(form) == []


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_resolve_does_not_change_what_it_is_given_and_is_idempotent(case):
    onto = ontology.load(case["ontology"])
    before = canonical_json(case["stored"])

    once = ontology.resolve(case["stored"], onto)

    assert canonical_json(case["stored"]) == before
    assert ontology.resolve(once, onto) == once


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_stripping_a_resolved_form_removes_the_ontology_part_and_nothing_a_team_wrote(
    case,
):
    resolved, stored = case["resolved"], case["stored"]

    stripped = ontology.strip(resolved)

    assert not ontology.has_bindings(stripped)
    assert ontology.LINK_PREFIX not in canonical_json(stripped).decode("utf-8")
    # `strip` keeps the values `resolve` fills in (the domain, the logical types, the
    # classification, the key and the semantic type): they are not links or bindings. Without
    # them the two forms are the same.
    assert without_filled(resolved) == without_filled(stored)


def test_a_declared_service_gains_what_the_ontology_knows_and_an_undeclared_one_gains_nothing():
    for case in CASES:
        declared = ontology.load(case["ontology"]).declares(
            _service(case["stored"])
        )
        assert (case["resolved"] != case["stored"]) == declared, case["name"]
        if not declared:
            assert case["resolved"] == canonical_form(case["stored"])


def test_the_dbt_cases_are_the_bound_fixtures_and_the_golden_resolved_form():
    by_name = {case["name"]: case for case in CASES}
    fx = by_name["dbt_model_with_a_filter_and_a_join"]
    seed = by_name["dbt_seed_with_personal_columns"]
    golden = json.loads(GOLDEN.read_text("ascii"))

    assert (
        fx["stored"]
        == bound_stored("finance_bound")["analytics.fx_transactions_eur"]
    )
    assert fx["stored"] == golden["stored"]
    assert fx["resolved"] == golden["resolved"]
    assert fx["contract_hash"] == golden["contract_hash"]
    assert seed["stored"] == bound_stored("core_bound")["analytics.seed_users"]
    assert by_name["service_not_declared"]["stored"] == seed["stored"]
