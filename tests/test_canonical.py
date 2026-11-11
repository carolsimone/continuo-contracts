import json
import struct
from datetime import date
from types import MappingProxyType

import pytest

from continuo_contracts.canonical import canonical_json, normalise_text
from continuo_contracts.errors import CanonicalJsonError

RFC_SAMPLE = r'''{
  "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],
  "string": "\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/",
  "literals": [null, true, false]
}'''
RFC_SAMPLE_BYTES = bytes.fromhex("""
7b 22 6c 69 74 65 72 61 6c 73 22 3a 5b 6e 75 6c 6c 2c 74 72
75 65 2c 66 61 6c 73 65 5d 2c 22 6e 75 6d 62 65 72 73 22 3a
5b 33 33 33 33 33 33 33 33 33 2e 33 33 33 33 33 33 33 2c 31
65 2b 33 30 2c 34 2e 35 2c 30 2e 30 30 32 2c 31 65 2d 32 37
5d 2c 22 73 74 72 69 6e 67 22 3a 22 e2 82 ac 24 5c 75 30 30
30 66 5c 6e 41 27 42 5c 22 5c 5c 5c 5c 5c 22 2f 22 7d
""")
NUMBER_SAMPLES = [
    ("0000000000000000", "0"), ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"), ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"), ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"), ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"), ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"), ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"), ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"), ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"), ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


def _float(ieee: str) -> float:
    return struct.unpack(">d", bytes.fromhex(ieee))[0]


def test_rfc8785_section_3_2_2_sample():
    assert canonical_json(json.loads(RFC_SAMPLE)) == RFC_SAMPLE_BYTES


def test_rfc8785_sorting_sample():
    sample = {"\u20ac": "Euro Sign", "\r": "Carriage Return", "\ufb33": "Hebrew Letter Dalet With Dagesh",
              "1": "One", "\U0001f600": "Emoji: Grinning Face", "\u0080": "Control",
              "\u00f6": "Latin Small Letter O With Diaeresis"}
    assert list(json.loads(canonical_json(sample)).values()) == [
        "Carriage Return", "One", "Control", "Latin Small Letter O With Diaeresis",
        "Euro Sign", "Emoji: Grinning Face", "Hebrew Letter Dalet With Dagesh"]


@pytest.mark.parametrize(("ieee", "expected"), NUMBER_SAMPLES)
def test_rfc8785_number_samples(ieee, expected):
    assert canonical_json(_float(ieee)) == expected.encode()


@pytest.mark.parametrize("ieee", ["7fffffffffffffff", "7ff0000000000000", "fff0000000000000"])
def test_nan_and_infinity_are_refused(ieee):
    with pytest.raises(CanonicalJsonError):
        canonical_json(_float(ieee))


def test_integers_inside_the_safe_range():
    assert canonical_json([9007199254740991, -9007199254740991]) == b"[9007199254740991,-9007199254740991]"


def test_integer_outside_the_safe_range_is_refused():
    with pytest.raises(CanonicalJsonError, match=r"^\$\[0\]: "):
        canonical_json([9007199254740992])


def test_booleans_are_not_integers_and_whole_floats_drop_their_point():
    assert canonical_json([True, False, 1, 0, 1.0, -0.0]) == b"[true,false,1,0,1,0]"


def test_strings_escape_only_what_rfc8785_escapes():
    assert canonical_json("\u0000\b\t\n\f\r\u001f\"\\/\u007f\u00e9") == (
        b'"\\u0000\\b\\t\\n\\f\\r\\u001f\\"\\\\/\x7f\xc3\xa9"')


def test_tuples_are_arrays_and_any_mapping_is_an_object():
    assert canonical_json((1, MappingProxyType({"b": 1, "a": 2}))) == b'[1,{"a":2,"b":1}]'


@pytest.mark.parametrize(("value", "path"), [
    ({"config": {"start": date(2024, 1, 1)}}, "$.config.start"),
    ({"a": [1, float("nan")]}, "$.a[1]"),
    ({"a": {1: "x"}}, "$.a"),
    ({"tags": {"x", "y"}}, "$.tags"),
    ("\ud800", "$"),
])
def test_unsupported_value_names_its_path(value, path):
    with pytest.raises(CanonicalJsonError) as err:
        canonical_json(value)
    assert str(err.value).startswith(path + ": ")


def test_a_cycle_is_refused():  # a YAML alias can build one
    loop: list[object] = []
    loop.append(loop)
    with pytest.raises(CanonicalJsonError, match=r"^\$\[0\]: "):
        canonical_json(loop)


@pytest.mark.parametrize(("raw", "expected"), [
    ("select 1\r\nfrom t\r", "select 1\nfrom t"),
    ("a\rb", "a\nb"),
    ("\r\n  select  1  \t\n", "select  1"),
    ("where y = 'a  b'", "where y = 'a  b'"),
    ("\u00a0select 1\u00a0", "\u00a0select 1\u00a0"),
    ("  \n\t ", ""),
    ("", ""),
    (chr(0x0B) + chr(0x0C) + "select 1" + chr(0x0C) + chr(0x0B), "select 1"),
    (chr(0x85) + "select 1" + chr(0x85), chr(0x85) + "select 1" + chr(0x85)),
])
def test_normalise_text(raw, expected):
    assert normalise_text(raw) == expected
    assert normalise_text(expected) == expected


def test_a_container_that_appears_twice_is_not_a_cycle():
    shared = [1]
    assert canonical_json({"a": shared, "b": [shared, shared]}) == b'{"a":[1],"b":[[1],[1]]}'


def test_a_lone_surrogate_key_names_the_object():
    with pytest.raises(CanonicalJsonError) as err:
        canonical_json({"a": {chr(0xD800): 1}})
    assert str(err.value).startswith("$.a: ")


def test_a_huge_integer_is_refused_without_printing_it():
    with pytest.raises(CanonicalJsonError, match=r"^\$: integer is outside"):
        canonical_json(10**5000)
