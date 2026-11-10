"""RFC 8785 canonical JSON and the text normaliser.

`canonical_json` writes a value as the byte string that RFC 8785 (the JSON Canonicalization
Scheme) defines, so two processes that hold the same value write the same bytes.
`normalise_text` makes a text independent of its line endings and of the whitespace around it.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Final

from continuo_contracts.errors import CanonicalJsonError

# The integers every JSON implementation reads back exactly: -(2**53 - 1) to 2**53 - 1.
_MAX_SAFE_INTEGER: Final = 2**53 - 1

# The deepest nesting `canonical_json` writes: the outermost list, tuple or mapping is level 1, a
# container inside it is level 2, and so on. A container on a deeper level is refused, so the
# result does not depend on how deep the running interpreter can recurse. A scalar has no level
# of its own: a scalar inside a container on level `MAX_DEPTH` is written.
MAX_DEPTH: Final = 100

# ECMA-262 Number::toString writes plain digits while the decimal exponent n of a value
# 0.d * 10**n lies in -6 < n <= 21, and the exponent form outside that range.
_MIN_PLAIN_EXPONENT: Final = -6
_MAX_PLAIN_EXPONENT: Final = 21

# The only escapes RFC 8785 writes: the quote, the backslash and the control characters.
# Every other code point is written as it is.
_STRING_ESCAPES: Final = {
    **{code: f"\\u{code:04x}" for code in range(0x20)},
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}

# Space, tab, line feed, carriage return, vertical tab and form feed.
_ASCII_WHITESPACE: Final = " \t\n\r\x0b\x0c"


def canonical_json(value: object) -> bytes:
    """Return `value` as RFC 8785 canonical JSON, UTF-8 encoded.

    `None`, `bool`, `int` (within -(2**53 - 1) to 2**53 - 1), finite `float`, `str`, `list`,
    `tuple` and `Mapping` with `str` keys are supported. Any other value, a number outside
    the range above, NaN, an infinity, a lone surrogate, a container that contains itself and a
    container nested deeper than `MAX_DEPTH` levels raise `CanonicalJsonError`, whose message
    starts with the JSON path of the bad value (of the container, for the nesting).
    """
    out: list[str] = []
    _write(value, "$", out, set())
    return "".join(out).encode("utf-8")


def normalise_text(text: str) -> str:
    """Return `text` with CRLF and CR turned into LF and ASCII whitespace stripped at both ends.

    Only the six ASCII whitespace characters are stripped, so the result does not depend on
    the Unicode tables of the Python that runs it. Nothing else changes.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip(_ASCII_WHITESPACE)


def _fail(path: str, reason: str) -> CanonicalJsonError:
    return CanonicalJsonError(f"{path}: {reason}")


def _write(value: object, path: str, out: list[str], active: set[int]) -> None:
    if value is None:
        out.append("null")
    elif isinstance(value, bool):
        out.append("true" if value else "false")
    elif isinstance(value, int):
        out.append(_integer(int(value), path))
    elif isinstance(value, float):
        out.append(_float(float(value), path))
    elif isinstance(value, str):
        _check_encodable(value, path, "string")
        out.append(_quote(value))
    elif isinstance(value, list | tuple):
        _write_array(value, path, out, active)
    elif isinstance(value, Mapping):
        _write_object(value, path, out, active)
    else:
        raise _fail(path, f"unsupported type {type(value).__name__}")


def _write_array(items: Sequence[object], path: str, out: list[str], active: set[int]) -> None:
    _enter(items, path, active)
    out.append("[")
    for index, item in enumerate(items):
        if index:
            out.append(",")
        _write(item, f"{path}[{index}]", out, active)
    out.append("]")
    active.remove(id(items))


def _write_object(
    mapping: Mapping[object, object], path: str, out: list[str], active: set[int]
) -> None:
    _enter(mapping, path, active)
    entries: list[tuple[str, object]] = []
    for key, item in mapping.items():
        if not isinstance(key, str):
            raise _fail(path, f"object key of type {type(key).__name__} is not a string")
        _check_encodable(key, path, "object key")
        entries.append((key, item))
    entries.sort(key=lambda entry: entry[0].encode("utf-16-be"))
    out.append("{")
    for index, (key, item) in enumerate(entries):
        if index:
            out.append(",")
        out.append(_quote(key) + ":")
        _write(item, f"{path}.{key}", out, active)
    out.append("}")
    active.remove(id(mapping))


def _enter(container: object, path: str, active: set[int]) -> None:
    if id(container) in active:
        raise _fail(path, "circular reference")
    # `active` holds the containers from the root down to the parent of `container`, so the
    # level of `container` is one more than its size.
    if len(active) >= MAX_DEPTH:
        raise _fail(path, f"nesting is too deep, a container may be at most {MAX_DEPTH} levels down")
    active.add(id(container))


def _check_encodable(text: str, path: str, what: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _fail(path, f"{what} holds a lone surrogate") from None


def _quote(text: str) -> str:
    return '"' + text.translate(_STRING_ESCAPES) + '"'


def _integer(number: int, path: str) -> str:
    if abs(number) > _MAX_SAFE_INTEGER:
        raise _fail(path, "integer is outside -(2**53 - 1) to 2**53 - 1")
    return str(number)


def _float(number: float, path: str) -> str:
    if not math.isfinite(number):
        raise _fail(path, "NaN and the infinities are not JSON numbers")
    if number == 0:
        return "0"
    if number < 0:
        return "-" + _positive_float(-number)
    return _positive_float(number)


def _positive_float(number: float) -> str:
    # The shortest round-trip digits that repr() gives, as the digit string `digits` and the
    # exponent n for which the value is 0.<digits> * 10**n.
    mantissa, _, exponent = repr(number).partition("e")
    whole, _, fraction = mantissa.partition(".")
    all_digits = whole + fraction
    n = len(whole) + (int(exponent) if exponent else 0)
    significant = all_digits.lstrip("0")
    n -= len(all_digits) - len(significant)
    digits = significant.rstrip("0")
    k = len(digits)
    if k <= n <= _MAX_PLAIN_EXPONENT:
        return digits + "0" * (n - k)
    if 0 < n <= _MAX_PLAIN_EXPONENT:
        return digits[:n] + "." + digits[n:]
    if _MIN_PLAIN_EXPONENT < n <= 0:
        return "0." + "0" * -n + digits
    power = n - 1
    sign = "+" if power >= 0 else "-"
    tail = "." + digits[1:] if k > 1 else ""
    return f"{digits[0]}{tail}e{sign}{abs(power)}"
