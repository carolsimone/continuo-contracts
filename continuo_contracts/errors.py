"""The errors this package raises."""


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
    """A hash input in a form this package refuses, such as a unit key that is empty or a unit
    digest that is not 64 lowercase hex characters.

    The message names the offending unit key.
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
