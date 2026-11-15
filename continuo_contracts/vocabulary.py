"""The custom properties that continuo reads from an ODCS contract, and where each may sit.

An ODCS contract carries custom properties as `{vendor, property, value}` items in a
`customProperties` list. The ones with `vendor: continuo` are continuo's own. `PROPERTIES` names
each one the library knows, the place of the contract where it sits and who may write it in a
Python contract. `continuo_values` reads the values of the items of one list.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

VENDOR: Final = "continuo"


@dataclass(frozen=True)
class ContinuoProperty:
    """One custom property of the `continuo` vendor.

    `sits_on` is the object of the contract whose `customProperties` list carries it: `root`
    (the contract), `schema` (the schema object of the table) or `quality` (a quality check).
    `python` says who may write it in a Python contract: `authored` (the author writes it),
    `filled` (continuo writes it and the author may not) or `refused` (no Python contract
    carries it).
    """

    name: str
    sits_on: str
    python: str


def _rows(sits_on: str, python: str, names: str) -> tuple[ContinuoProperty, ...]:
    return tuple(ContinuoProperty(name, sits_on, python) for name in names.split())


_ROWS: Final = (
    *_rows(
        "root",
        "authored",
        "service kind schedule criticality timeoutMinutes script readQueries csvSource "
        "extraColumns config secretRef modeType assets",
    ),
    *_rows(
        "root",
        "refused",
        "filePath materialization contractEnforced access group deprecationDate "
        "freshnessBuildAfter snapshot quoteColumns provenance",
    ),
    *_rows("schema", "filled", "reads"),
    *_rows("quality", "authored", "where warnIf errorIf failCalc"),
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
