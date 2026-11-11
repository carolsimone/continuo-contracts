import json
import math
from importlib import resources

import pytest

from continuo_contracts.canonical import MAX_DEPTH, canonical_json
from continuo_contracts.errors import CanonicalJsonError

VECTOR_FILE = resources.files("continuo_contracts") / "vectors" / "canonical_json.json"
CASES = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
WRITTEN = [case for case in CASES if "error" not in case]
REFUSED = [case for case in CASES if case.get("error")]
BY_NAME = {case["name"]: case for case in CASES}


def _utf16_order(keys):
    return sorted(keys, key=lambda key: key.encode("utf-16-be"))


def _code_point_order(keys):
    return sorted(keys)


def _object_keys(text):
    keys = []
    json.loads(text, object_pairs_hook=lambda pairs: keys.append([k for k, _ in pairs]))
    return keys


@pytest.mark.parametrize("case", WRITTEN, ids=lambda case: case["name"])
def test_canonical_json_vector(case):
    assert canonical_json(case["value"]) == case["canonical"].encode("utf-8")


@pytest.mark.parametrize("case", REFUSED, ids=lambda case: case["name"])
def test_canonical_json_refuses_the_value_of_an_error_vector(case):
    with pytest.raises(CanonicalJsonError):
        canonical_json(json.loads(case["value_text"]))


def test_every_case_has_the_documented_fields_and_a_unique_name():
    for case in WRITTEN:
        assert set(case) == {"name", "value", "canonical"}, case["name"]
    for case in REFUSED:
        assert set(case) == {"name", "value_text", "error"}, case["name"]
    assert len(BY_NAME) == len(CASES)
    assert WRITTEN and REFUSED


@pytest.mark.parametrize("case", WRITTEN, ids=lambda case: case["name"])
def test_the_canonical_text_reads_back_as_the_value(case):
    # A whole float is written without a point, so the text reads back as an integer.
    assert json.loads(case["canonical"], parse_int=float) == case["value"]


def test_the_object_key_trap_cases_sort_by_utf16_and_not_by_code_point():
    names = [
        "keys_sort_by_utf16_code_unit_not_by_code_point",
        "keys_sort_by_utf16_code_unit_at_every_level",
        "rfc8785_sort_sample_3_2_3",
    ]
    for name in names:
        written = _object_keys(BY_NAME[name]["canonical"])
        assert written, name
        assert all(keys == _utf16_order(keys) for keys in written), name
        assert any(keys != _code_point_order(keys) for keys in written), name


def test_the_number_cases_hold_both_forms_of_a_float_and_a_negative_zero():
    canonical = {case["canonical"] for case in WRITTEN if case["name"].startswith("number_")}
    assert {"1e+21", "1e-7", "123456789012345680000", "0.002", "4.5"} <= canonical
    assert math.copysign(1.0, BY_NAME["number_negative_zero"]["value"]) == -1.0


def test_an_integer_in_a_value_is_inside_the_safe_range():
    # A consumer whose JSON reader turns every number into a double reads the same value.
    def numbers(value):
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from numbers(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from numbers(item)

    assert all(abs(n) <= 2**53 - 1 for case in WRITTEN for n in numbers(case["value"]))


def _levels(value):
    levels = 0
    while isinstance(value, list):
        levels += 1
        value = value[0] if value else None
    return levels


def test_the_nesting_cases_sit_on_either_side_of_the_limit():
    accepted = BY_NAME["nesting_100_accepted"]
    refused = BY_NAME["nesting_101_refused"]
    assert _levels(accepted["value"]) == MAX_DEPTH
    assert accepted["canonical"] == "[" * MAX_DEPTH + "]" * MAX_DEPTH
    assert _levels(json.loads(refused["value_text"])) == MAX_DEPTH + 1
