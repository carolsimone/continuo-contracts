"""The ODCS v3.2.0 JSON schema that ships with the package, and the check of its bytes."""

import functools
import hashlib
import json
from collections.abc import Mapping
from importlib import resources
from typing import Any, Final

from continuo_contracts.errors import SchemaFileError

SCHEMA_FILE: Final = "odcs-json-schema-v3.2.0.json"
SCHEMA_SHA256: Final = "edb41f33ec46e84780e99872ab2bd67f074959d2bf3e9c9fc54e61f8982b0d93"


def _read_bytes() -> bytes:
    """Return the bytes of the packaged schema file."""
    return (resources.files("continuo_contracts") / SCHEMA_FILE).read_bytes()


@functools.cache
def load() -> Mapping[str, Any]:
    """Return the ODCS v3.2.0 JSON schema, the same object on every call.

    The first call checks that the file has the pinned SHA-256 and raises `SchemaFileError` when
    it does not, so a changed schema never validates a contract. A call that raises is not cached.
    """
    data = _read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != SCHEMA_SHA256:
        raise SchemaFileError(f"{SCHEMA_FILE}: SHA-256 {got} is not the pinned {SCHEMA_SHA256}")
    schema: Mapping[str, Any] = json.loads(data)
    return schema
