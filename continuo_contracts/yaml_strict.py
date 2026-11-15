"""The reader of a contract file: YAML 1.2 core with nothing that JSON cannot hold.

`load` reads one YAML document into plain `dict`, `list`, `str`, `int`, `float`, `bool` and `None`
values. PyYAML does the parsing, with the scalar rules of the YAML 1.2 core schema instead of
its own YAML 1.1 ones, so `on`, `yes` and `NO` are strings, `2026-11-14` and `1:20` are strings,
and `1_000` is a string. The reader refuses what lets one small file mean more than it says or
mean different things to different readers: aliases (`*name`, which let a few lines expand into
a tree of any size), merge keys (`<<`), duplicate keys, keys that are not strings, any tag other
than the seven core ones, a second document, and a value that canonical JSON cannot write.
"""

import math
import re

from collections.abc import Callable, Hashable
from typing import Any, ClassVar, Final

import yaml

from yaml.reader import ReaderError

from continuo_contracts.canonical import MAX_DEPTH, canonical_json
from continuo_contracts.errors import CanonicalJsonError, StrictYamlError

_TAG_PREFIX: Final = "tag:yaml.org,2002:"
_NULL_TAG: Final = _TAG_PREFIX + "null"
_BOOL_TAG: Final = _TAG_PREFIX + "bool"
_INT_TAG: Final = _TAG_PREFIX + "int"
_FLOAT_TAG: Final = _TAG_PREFIX + "float"
_STR_TAG: Final = _TAG_PREFIX + "str"
_SEQ_TAG: Final = _TAG_PREFIX + "seq"
_MAP_TAG: Final = _TAG_PREFIX + "map"

# The plain-scalar forms of the YAML 1.2 core schema. A pattern matches the whole text: the
# resolver of PyYAML only checks the start of a text, so each one ends in `\Z`.
_NULL: Final = re.compile(r"(?:~|null|Null|NULL|)\Z")
_BOOL: Final = re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z")
_INT: Final = re.compile(r"(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)\Z")
_FLOAT: Final = re.compile(
    r"(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))\Z"
)

# The characters from which a plain scalar of each kind can start. The order is the order in
# which a text is tried, so `1` is an integer and `1e3` is a float.
_RESOLVERS: Final = (
    (_NULL_TAG, _NULL, ["~", "n", "N", ""]),
    (_BOOL_TAG, _BOOL, list("tTfF")),
    (_INT_TAG, _INT, list("-+0123456789")),
    (_FLOAT_TAG, _FLOAT, list("-+0123456789.")),
)

# What YAML counts as the end of a line, which a position in the text is counted by.
_LINE_BREAK: Final = re.compile(r"\r\n|[\r\n\x85\u2028\u2029]")
_BYTE_ORDER_MARK: Final = "\ufeff"


def load(text: str) -> object:
    """Return the value of the one YAML document in `text`, or `None` when it holds no document.

    A leading byte order mark is dropped, and CRLF reads like LF. A scalar written after a lone
    `!` is a string, as in YAML 1.2. Raises `StrictYamlError` for a second byte order mark right
    after the first, for YAML that does not parse, for any of the refusals in the module
    description, for nesting deeper than `canonical.MAX_DEPTH` levels, and for a value that
    `canonical_json` refuses (a float that is not finite, an integer outside
    -(2**53 - 1) to 2**53 - 1, a lone surrogate). The error names the line and the column of the
    problem, counted from 1, except for the last kind, which has no position in the text and
    gives 0 for both, its reason starting with the JSON path of the value.
    """
    text = text.removeprefix(_BYTE_ORDER_MARK)
    if text.startswith(_BYTE_ORDER_MARK):
        # PyYAML drops one more mark at the start of its input, so it would read two as one.
        raise StrictYamlError(
            1, 1, "a byte order mark may only start the text once"
        )
    try:
        # PyYAML checks every character of the text when it builds the loader.
        loader = _Loader(text)
        try:
            value: object = loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as error:
        raise _from_yaml(error) from error
    except ReaderError as error:
        raise _from_reader(error, text) from error
    try:
        canonical_json(value)
    except CanonicalJsonError as error:
        raise StrictYamlError(0, 0, str(error)) from error
    return value


def _error(mark: yaml.Mark | None, reason: str) -> StrictYamlError:
    """Return the error for `reason` at the 0-based `mark` of PyYAML, or without a position."""
    if mark is None:
        return StrictYamlError(0, 0, reason)
    return StrictYamlError(mark.line + 1, mark.column + 1, reason)


def _from_yaml(error: yaml.MarkedYAMLError) -> StrictYamlError:
    """Return the error for a refusal of PyYAML, at the mark it names."""
    reason = error.problem or "invalid YAML"
    if error.context:
        reason = f"{error.context}, {reason}"
    return _error(error.problem_mark or error.context_mark, reason)


def _from_reader(error: ReaderError, text: str) -> StrictYamlError:
    """Return the error for a character PyYAML refuses, at the line and column it stands on."""
    breaks = list(_LINE_BREAK.finditer(text, 0, error.position))
    line_start = breaks[-1].end() if breaks else 0
    reason = f"unacceptable character #x{error.character:04x}: {error.reason}"
    return StrictYamlError(
        len(breaks) + 1, error.position - line_start + 1, reason
    )


def _tag_name(tag: str) -> str:
    """Return `tag` as it is written in a YAML text: `!!binary` for a tag of yaml.org."""
    return (
        "!!" + tag.removeprefix(_TAG_PREFIX)
        if tag.startswith(_TAG_PREFIX)
        else tag
    )


class _Loader(yaml.SafeLoader):
    """A PyYAML loader with the YAML 1.2 core scalars and the refusals of the module."""

    # An empty table of resolvers and one of constructors replace the YAML 1.1 ones that
    # `SafeLoader` carries, so only what is registered below exists.
    yaml_implicit_resolvers: dict[Any, Any] = {}
    yaml_constructors: ClassVar[dict[Any, Any]] = {}

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        """Compose a node, refusing an alias and a container nested too deep."""
        if self._next_is(yaml.AliasEvent):
            raise _error(self._next_mark(), "YAML aliases are not allowed")
        is_container = self._next_is(
            yaml.SequenceStartEvent, yaml.MappingStartEvent
        )
        # PyYAML resolves a scalar after a lone `!` like a plain one, so `! 5` is 5. YAML 1.2
        # gives it the tag of a string.
        lone_bang = (
            self._next_is(yaml.ScalarEvent) and self._next_event().tag == "!"
        )
        if is_container:
            if self._depth >= MAX_DEPTH:
                raise _error(
                    self._next_mark(),
                    f"nesting is too deep, a container may be at most {MAX_DEPTH} levels down",
                )
            self._depth += 1
        node: Any = super().compose_node(parent, index)
        if is_container:
            self._depth -= 1
        if lone_bang:
            node.tag = _STR_TAG
        return node

    def construct_mapping(
        self, node: Any, deep: bool = False
    ) -> dict[Hashable, Any]:
        """Construct a mapping, refusing a merge key, a key that is not a string and a repeat."""
        if not isinstance(node, yaml.MappingNode):
            raise _error(
                node.start_mark, f"expected a mapping, but found a {_kind(node)}"
            )
        mapping: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self._construct_key(key_node)
            if key in mapping:
                raise _error(key_node.start_mark, f"duplicate key {key!r}")
            mapping[key] = self._construct(value_node, deep)
        return mapping

    def _construct_key(self, key_node: Any) -> str:
        if not isinstance(key_node, yaml.ScalarNode):
            raise _error(key_node.start_mark, "mapping keys must be strings")
        if key_node.style is None and key_node.value == "<<":
            raise _error(key_node.start_mark, "merge keys are not allowed")
        key = self._construct(key_node, False)
        if not isinstance(key, str):
            raise _error(
                key_node.start_mark, f"mapping key {key!r} is not a string"
            )
        return key

    # PyYAML's stubs leave some of the methods below untyped, so each is called through one
    # typed wrapper.
    def _next_is(self, *events: type[yaml.Event]) -> bool:
        return bool(self.check_event(*events))

    def _next_event(self) -> Any:
        return self.peek_event()  # type: ignore[no-untyped-call]

    def _next_mark(self) -> yaml.Mark:
        mark: yaml.Mark = self._next_event().start_mark
        return mark

    def _construct(self, node: yaml.Node, deep: bool) -> object:
        return self.construct_object(node, deep=deep)


def _kind(node: yaml.Node) -> str:
    """Return `scalar`, `sequence` or `mapping`, what the node is."""
    return str(node.id)  # type: ignore[attr-defined]


def _scalar_text(node: yaml.Node) -> str:
    """Return the text of a scalar node, and refuse a sequence or a mapping."""
    if not isinstance(node, yaml.ScalarNode):
        raise _error(
            node.start_mark, f"expected a scalar, but found a {_kind(node)}"
        )
    text: str = node.value
    return text


def _core_text(node: yaml.Node, pattern: re.Pattern[str], what: str) -> str:
    """Return the text of a scalar node that must have the form `pattern`.

    An implicit scalar has that form, and one written with a tag such as
    `!!int abc` may not.
    """
    text = _scalar_text(node)
    if not pattern.match(text):
        raise _error(node.start_mark, f"{text!r} is not {what}")
    return text


def _construct_null(loader: _Loader, node: yaml.Node) -> None:
    _core_text(node, _NULL, "null")


def _construct_bool(loader: _Loader, node: yaml.Node) -> bool:
    return _core_text(node, _BOOL, "a boolean")[0] in "tT"


def _construct_int(loader: _Loader, node: yaml.Node) -> int:
    text = _core_text(node, _INT, "an integer")
    try:
        if text.startswith("0x"):
            return int(text[2:], 16)
        if text.startswith("0o"):
            return int(text[2:], 8)
        return int(text)
    except ValueError:
        # `int` refuses a decimal text of more than 4300 digits, a number far outside the range
        # that canonical JSON writes.
        raise _error(
            node.start_mark, "integer is outside -(2**53 - 1) to 2**53 - 1"
        ) from None


def _construct_float(loader: _Loader, node: yaml.Node) -> float:
    text = _core_text(node, _FLOAT, "a float")
    if text.lower().endswith(".nan"):
        return math.nan
    if text.lower().endswith(".inf"):
        return -math.inf if text.startswith("-") else math.inf
    return float(text)


def _construct_str(loader: _Loader, node: yaml.Node) -> str:
    return _scalar_text(node)


def _refuse_tag(loader: _Loader, node: yaml.Node) -> object:
    raise _error(node.start_mark, f"the tag {_tag_name(node.tag)} is not allowed")


def _register() -> None:
    for tag, pattern, first in _RESOLVERS:
        _Loader.add_implicit_resolver(tag, pattern, first)
    constructors: dict[str | None, Callable[[_Loader, Any], object]] = {
        _NULL_TAG: _construct_null,
        _BOOL_TAG: _construct_bool,
        _INT_TAG: _construct_int,
        _FLOAT_TAG: _construct_float,
        _STR_TAG: _construct_str,
        _SEQ_TAG: yaml.SafeLoader.construct_yaml_seq,
        _MAP_TAG: yaml.SafeLoader.construct_yaml_map,
        None: _refuse_tag,
    }
    _Loader.yaml_constructors.update(constructors)


_register()
