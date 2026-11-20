"""The custom properties that continuo reads from an ODCS contract, and where each may sit.

An ODCS contract carries custom properties as `{vendor, property, value}` items in a
`customProperties` list. The ones with `vendor: continuo` are continuo's own. `PROPERTIES` names
each one the library knows, the place of the contract where it sits, who may write it in a
Python contract and what a dbt contract does with it. `continuo_values` reads the values of the
items of one list.

The bindings name things and their fields as `<Thing>` and `<Thing>.<field>`. `is_name` says
whether a string can be the name of one of them, so the ontology file, the resolution of a
contract and the validation of a binding all read one rule.
"""

import re

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

VENDOR: Final = "continuo"

# The control characters: C0 (U+0000 to U+001F), DEL (U+007F) and C1 (U+0080 to U+009F).
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class ContinuoProperty:
    """One custom property of the `continuo` vendor.

    `sits_on` is the object of the contract whose `customProperties` list carries it: `root`
    (the contract), `schema` (the schema object of the table), `quality` (a quality check) or
    `column` (a column of the table).
    `python` says who may write it in a Python contract: `authored` (the author writes it),
    `filled` (continuo writes it and the author may not) or `refused` (no Python contract
    carries it).
    `dbt` says what a dbt contract does with it: `written` (the generator writes it), `filled`
    (continuo fills it) or `not_used` (no dbt contract carries it).
    """

    name: str
    sits_on: str
    python: str
    dbt: str


def _rows(
    sits_on: str, python: str, dbt: str, names: str
) -> tuple[ContinuoProperty, ...]:
    return tuple(
        ContinuoProperty(name, sits_on, python, dbt) for name in names.split()
    )


_ROWS: Final = (
    *_rows(
        "root",
        "authored",
        "written",
        "service kind schedule criticality timeoutMinutes",
    ),
    *_rows(
        "root",
        "authored",
        "not_used",
        "script readQueries csvSource extraColumns config secretRef modeType assets",
    ),
    *_rows(
        "root",
        "refused",
        "written",
        "filePath materialization contractEnforced access group deprecationDate "
        "freshnessBuildAfter snapshot quoteColumns",
    ),
    *_rows("root", "refused", "not_used", "provenance"),
    *_rows("schema", "filled", "filled", "reads"),
    *_rows("quality", "authored", "written", "where warnIf errorIf failCalc"),
    # The ontology bindings: which thing the table stands for, and what each column is of it.
    *_rows("schema", "authored", "written", "entity grain fixed filter"),
    *_rows("column", "authored", "written", "property local aggregate"),
)

PROPERTIES: Final[Mapping[str, ContinuoProperty]] = MappingProxyType(
    {row.name: row for row in _ROWS}
)


def continuo_values(custom_properties: object) -> dict[str, Any]:
    """Return the value of each `continuo` item of a `customProperties` list, by property name.

    The names come in the order of their first appearance in the list. An item of another vendor
    is skipped, and so is an item that is not a mapping or has no string `vendor`, no string
    `property` or no `value`. A value is returned as written, whether or not `PROPERTIES` names
    the property. An argument that is not a list gives an empty dict. A name that appears twice
    keeps its last value, in the place of its first appearance, so a caller that must refuse a
    repeat checks the list itself.
    """
    if not isinstance(custom_properties, list):
        return {}
    values: dict[str, Any] = {}
    for item in custom_properties:
        if not isinstance(item, Mapping) or item.get("vendor") != VENDOR:
            continue
        name = item.get("property")
        if isinstance(name, str) and "value" in item:
            values[name] = item["value"]
    return values


def is_name(value: object) -> bool:
    """Say whether `value` can be the name of a thing, or of a field of a thing.

    A name is a string that is not empty and holds no whitespace (what `str.isspace` reads as
    whitespace, the same code points on every supported Python), no control character (U+0000 to
    U+001F and U+007F to U+009F), no dot and no slash. A dot separates the thing from the field
    in `<Thing>.<field>`, so a name that held one could not be written there.
    """
    return (
        isinstance(value, str)
        and bool(value)
        and not any(character.isspace() for character in value)
        and _CONTROL.search(value) is None
        and "." not in value
        and "/" not in value
    )
