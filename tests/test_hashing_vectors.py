import json
from importlib import resources

import pytest

from continuo_contracts.errors import HashInputError
from continuo_contracts.hashing import fold3, fold4, shared_fold, shared_hash

VECTOR_FILE = resources.files("continuo_contracts") / "vectors" / "hashing.json"
CASES = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
HASHED = [case for case in CASES if "error" not in case]
REFUSED = [case for case in CASES if case.get("error")]
BY_NAME = {case["name"]: case for case in CASES}
FUNCTIONS = ("fold3", "fold4", "shared_fold", "shared_hash")


def _run(case):
    inputs = case["inputs"]
    match case["function"]:
        case "fold3":
            return fold3(**inputs)
        case "fold4":
            return fold4(**inputs)
        case "shared_fold":
            return shared_fold(inputs["unit_digests"])
        case "shared_hash":
            units = {key: bytes.fromhex(text) for key, text in inputs["units_hex"].items()}
            return shared_hash(units)
    raise AssertionError(f"{case['name']}: unknown function {case['function']!r}")


def _keys(case):
    inputs = case["inputs"]
    return list(inputs["unit_digests"] if "unit_digests" in inputs else inputs["units_hex"])


@pytest.mark.parametrize("case", HASHED, ids=lambda case: case["name"])
def test_hashing_vector(case):
    assert _run(case) == case["expected"]


@pytest.mark.parametrize("case", REFUSED, ids=lambda case: case["name"])
def test_an_error_vector_is_refused(case):
    with pytest.raises(HashInputError):
        _run(case)


def test_every_case_has_the_documented_fields_and_a_unique_name():
    for case in CASES:
        assert case["function"] in FUNCTIONS, case["name"]
        wanted = {"name", "function", "inputs", "error" if case.get("error") else "expected"}
        assert set(case) == wanted, case["name"]
    assert len(BY_NAME) == len(CASES)
    assert HASHED and REFUSED


def test_every_function_has_cases_that_hash_and_the_fold4_cases_cover_both_compiled_parts():
    assert {case["function"] for case in HASHED} == set(FUNCTIONS)
    compiled = [case["inputs"]["compiled"] for case in HASHED if case["function"] == "fold4"]
    assert "" in compiled
    assert any(compiled)


def test_a_fold4_without_a_compiled_part_is_not_the_fold3_of_the_same_parts():
    three = BY_NAME["fold3_two_digests_and_no_shared_part"]
    four = BY_NAME["fold4_without_a_compiled_part_keeps_its_bar"]
    assert four["inputs"] == {**three["inputs"], "compiled": ""}
    assert four["expected"] != three["expected"]


def test_the_empty_set_of_units_folds_to_the_empty_text():
    assert BY_NAME["shared_fold_no_units"]["expected"] == ""
    assert BY_NAME["shared_hash_no_units"]["expected"] == ""


def test_the_key_order_trap_cases_sort_by_code_point_and_not_by_utf16_code_unit():
    names = [
        "shared_fold_sorts_by_code_point_not_by_utf16_code_unit",
        "shared_hash_sorts_by_code_point_not_by_utf16_code_unit",
    ]
    for name in names:
        keys = _keys(BY_NAME[name])
        assert sorted(keys, key=lambda key: key.encode("utf-16-be")) != sorted(keys), name
        assert sorted(keys) == sorted(keys, key=lambda key: key.encode("utf-8")), name


def test_the_trap_cases_list_their_keys_in_utf16_order_so_that_a_sort_must_move_them():
    for name in (
        "shared_fold_sorts_by_code_point_not_by_utf16_code_unit",
        "shared_hash_sorts_by_code_point_not_by_utf16_code_unit",
    ):
        keys = _keys(BY_NAME[name])
        assert keys == sorted(keys, key=lambda key: key.encode("utf-16-be")), name
        assert keys != sorted(keys), name
