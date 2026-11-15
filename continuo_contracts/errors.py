"""The errors this package defines."""

from typing import Self


class ContractsError(ValueError):
    """The base of the errors this package defines, each raised for an input it refuses.

    Some errors are not a `ContractsError`. `Fingerprints.entries` raises a plain `ValueError`
    for a section that is not in `SECTIONS`, and `classify` and `unclassified` (the functions and
    the methods of `Fingerprints`) raise one for a family that is not in `FAMILIES`. `fold3` and
    `fold4` raise `UnicodeEncodeError` for a part that UTF-8 cannot encode, such as a lone
    surrogate. An argument of the wrong type can raise Python's own error, such as `TypeError` or
    `AttributeError`, instead of a `ContractsError`.
    """


class CanonicalJsonError(ContractsError):
    """A value that RFC 8785 canonical JSON cannot represent.

    The message starts with the JSON path of the offending value, then ``: `` and the reason.
    """


class HashInputError(ContractsError):
    """A hash input in a form this package refuses.

    Examples are a unit key that is empty, a unit digest that is not 64 lowercase
    hex characters, a `reads` entry of a contract that is not `<schema>.<table>`
    (exactly two parts joined by a dot, neither empty, with no whitespace and no
    control character) or a service of a release that is empty or blank.

    The message names the offending unit key or `reads` entry.
    """


class ProjectionError(ContractsError):
    """A Python node that the code projection refuses, or an SQL type that is not supported.

    The message of an error about a node starts with its relation, `<schema>.<table>`, then
    `: ` and the reason. `canonical_sql_type` has no node, so its messages carry no relation.
    """


class FingerprintsFileError(ContractsError):
    """A fingerprints file in a form this package refuses.

    The message names the location in the file, such as `families.dbt-model[3] (meta_other)`,
    then `: ` and the reason.
    """


class SchemaFileError(ContractsError):
    """The packaged ODCS JSON schema does not have the bytes this package pins.

    The message names the file, then `: ` and the SHA-256 it found next to the pinned one.
    """


class StrictYamlError(ContractsError):
    """A text that `yaml_strict.load` refuses.

    The message is ``line <line>, column <column>: <reason>``. `line` and `column` count from 1
    and are 0 when the reason has no position in the text, as for a value that canonical JSON
    refuses after the whole text was read (its reason then starts with the JSON path of the value).
    """

    line: int
    column: int
    reason: str

    def __init__(self, line: int, column: int, reason: str) -> None:
        super().__init__(f"line {line}, column {column}: {reason}")
        self.line = line
        self.column = column
        self.reason = reason

    def __reduce__(self) -> tuple[type[Self], tuple[int, int, str]]:
        """Rebuild the error from its line, column and reason, to pickle or copy it."""
        return (type(self), (self.line, self.column, self.reason))
