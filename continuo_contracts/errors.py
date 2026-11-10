"""The errors this package raises."""


class ContractsError(ValueError):
    """The base of every error this package raises."""


class CanonicalJsonError(ContractsError):
    """A value that RFC 8785 canonical JSON cannot represent.

    The message starts with the JSON path of the offending value, then ``: `` and the reason.
    """
