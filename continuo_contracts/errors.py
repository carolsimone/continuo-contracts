"""The errors this package raises."""


class ContractsError(ValueError):
    """The base of every error this package raises."""


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
