import json
import pickle
import time
from importlib import resources

import pytest

from continuo_contracts import yaml_strict
from continuo_contracts.canonical import MAX_DEPTH
from continuo_contracts.errors import ContractsError, StrictYamlError


def _vectors(name):
    return json.loads((resources.files("continuo_contracts") / "vectors" / name).read_text("utf-8"))


def _same(value):
    """Text that tells `1`, `1.0` and `true` apart, which `==` does not."""
    return json.dumps(value, sort_keys=True)


@pytest.mark.parametrize("case", _vectors("strict_yaml.json"), ids=lambda c: c["name"])
def test_strict_yaml_vector(case):
    if case.get("error"):
        with pytest.raises(StrictYamlError):
            yaml_strict.load(case["text"])
    else:
        assert _same(yaml_strict.load(case["text"])) == _same(case["value"])


def test_every_case_has_the_documented_fields_and_a_unique_name():
    cases = _vectors("strict_yaml.json")
    for case in cases:
        refused = set(case) == {"name", "text", "error"} and case["error"] is True
        assert refused or set(case) == {"name", "text", "value"}, case["name"]
    assert len({case["name"] for case in cases}) == len(cases)
    assert any(case.get("error") for case in cases)
    assert any("value" in case for case in cases)


def test_every_vector_text_is_a_whole_document():
    for case in _vectors("strict_yaml.json"):
        assert case["text"].endswith("\n"), case["name"]


def test_an_alias_is_refused():
    with pytest.raises(StrictYamlError, match=r"line 2, column \d+: YAML aliases are not allowed"):
        yaml_strict.load("a: &x 1\nb: *x\n")


def test_a_merge_key_is_refused():
    with pytest.raises(StrictYamlError, match="merge keys are not allowed"):
        yaml_strict.load("base: {a: 1}\nother:\n  <<: {a: 1}\n")


def test_the_billion_laughs_shape_is_refused_before_it_expands():
    text = "a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\n" + "".join(
        f"d{i}: &d{i} [*c, *c]\n" for i in range(30)
    )
    start = time.perf_counter()
    with pytest.raises(StrictYamlError):
        yaml_strict.load(text)
    assert time.perf_counter() - start < 1.0


def test_a_duplicate_key_names_its_line():
    with pytest.raises(StrictYamlError, match="line 3, .*duplicate key 'id'"):
        yaml_strict.load("apiVersion: v3.2.0\nid: a.b\nid: a.c\n")


def test_a_bom_and_crlf_read_like_plain_lf():
    assert yaml_strict.load("\ufeffa: 1\r\nb: two\r\n") == {"a": 1, "b": "two"}


def test_a_quoted_merge_spelling_is_an_ordinary_key():
    assert yaml_strict.load('"<<": 1\n') == {"<<": 1}


def test_the_error_carries_its_one_based_line_and_column():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("a: 1\nb:\n  - x\n  - y\nc:\n  d: *z\n")
    assert (raised.value.line, raised.value.column) == (6, 6)


def test_the_column_of_the_first_line_ignores_a_byte_order_mark():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("\ufeffa: *x\n")
    assert (raised.value.line, raised.value.column) == (1, 4)


def test_a_duplicate_key_after_a_crlf_names_the_line_of_the_second_key():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("\ufeffa: 1\r\na: 2\r\n")
    assert (raised.value.line, raised.value.column) == (2, 1)


def test_a_yaml_syntax_error_names_its_position():
    with pytest.raises(StrictYamlError, match=r"^line 2, column \d+: "):
        yaml_strict.load("a: [1, 2\nb: 3\n")


def test_a_character_yaml_cannot_hold_names_its_position():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("a: 1\nb: \x07\n")
    assert (raised.value.line, raised.value.column) == (2, 4)
    assert str(raised.value).startswith("line 2, column 4: ")


def test_a_value_canonical_json_refuses_has_no_position_and_names_its_path():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("a:\n  b: .inf\n")
    assert (raised.value.line, raised.value.column) == (0, 0)
    assert str(raised.value).startswith("line 0, column 0: $.a.b: ")


def test_an_integer_with_thousands_of_digits_is_refused_not_crashed_on():
    with pytest.raises(StrictYamlError, match="integer is outside"):
        yaml_strict.load("a: " + "9" * 5000 + "\n")


def test_the_error_is_a_contracts_error_and_survives_pickling():
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load("a: 1\na: 2\n")
    assert isinstance(raised.value, ContractsError)
    copy = pickle.loads(pickle.dumps(raised.value))
    assert (copy.line, copy.column, str(copy)) == (2, 1, str(raised.value))


def test_the_order_of_the_keys_is_the_order_of_the_file():
    value = yaml_strict.load("b: 1\na: 2\nc:\n  z: 1\n  y: 2\n")
    assert list(value) == ["b", "a", "c"]
    assert list(value["c"]) == ["z", "y"]


def test_a_text_with_no_document_reads_as_null():
    assert yaml_strict.load("") is None
    assert yaml_strict.load("# nothing but a comment\n") is None


def test_the_reader_returns_plain_python_containers():
    value = yaml_strict.load("a: [1, {b: c}]\n")
    assert type(value) is dict
    assert type(value["a"]) is list
    assert type(value["a"][1]) is dict


def test_nesting_up_to_the_canonical_limit_is_read():
    value = yaml_strict.load("[" * MAX_DEPTH + "]" * MAX_DEPTH)
    for _ in range(MAX_DEPTH - 1):
        assert isinstance(value, list) and len(value) == 1
        value = value[0]
    assert value == []


def test_nesting_one_level_past_the_limit_is_refused_with_its_position():
    text = "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    with pytest.raises(StrictYamlError) as raised:
        yaml_strict.load(text)
    assert str(raised.value).startswith("line 1, column 101: nesting is too deep")
    assert str(MAX_DEPTH) in str(raised.value)


def test_a_hostile_nesting_is_refused_without_a_recursion_error():
    start = time.perf_counter()
    with pytest.raises(StrictYamlError, match="nesting is too deep"):
        yaml_strict.load("[" * 200_000)
    with pytest.raises(StrictYamlError, match="nesting is too deep"):
        yaml_strict.load("".join(f"{' ' * i}a{i}:\n" for i in range(1_000)))
    assert time.perf_counter() - start < 2.0
