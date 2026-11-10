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
