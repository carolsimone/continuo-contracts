"""The `content_hash` folds and the shared part that goes into them.

A node's `content_hash` is `"sha256:"` followed by the lowercase hex SHA-256 of its parts joined
by `|`, each part encoded as UTF-8:

- `fold3(source, shared, config)` hashes `source|shared|config`. It is the fold of a Python node
  (`python-node`, `python-csv`, `python-api`).
- `fold4(source, shared, config, compiled)` hashes `source|shared|config|compiled`. It is the fold
  of every dbt node. A dbt seed and a dbt test have no compiled part and pass `""`: the last `|`
  stays, so `fold4(s, h, c, "")` is not `fold3(s, h, c)`.

A part is a bare lowercase hex digest, without a `sha256:` prefix, or `""` when the node has no
such part. The folds do not check their parts: a seed in a stored artifact without compile extras
carries dbt's `path` checksum as its source part, so a part is not always hex.

The shared part folds the units a node imports, each unit named by an id or a repo-relative POSIX
path and carrying the digest of its bytes. `shared_fold` writes one line per unit, the key, a
NUL character and the digest, sorts the lines by key, joins them with a line feed, and returns the
lowercase hex SHA-256 of the UTF-8 text. The lines are sorted by Unicode code point of the key,
which is the order of its UTF-8 bytes (not UTF-16 order). The key is part of the line, so two
units that swap their contents change the hash. An empty set of units folds to `""`.
"""

import hashlib
import re
from collections.abc import Mapping

from continuo_contracts.errors import HashInputError

_DIGEST = re.compile(r"[0-9a-f]{64}")


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of `data`."""
    return hashlib.sha256(data).hexdigest()


def fold3(source: str, shared: str, config: str) -> str:
    """Return the `content_hash` of a Python node, which has three parts."""
    return _fold(source, shared, config)


def fold4(source: str, shared: str, config: str, compiled: str) -> str:
    """Return the `content_hash` of a dbt node. A seed or a test passes `""` as `compiled`."""
    return _fold(source, shared, config, compiled)


def shared_fold(unit_digests: Mapping[str, str]) -> str:
    """Fold each unit's key with its digest into the shared part, or `""` when there are none.

    `unit_digests` maps a unit id or repo-relative POSIX path to the 64 lowercase hex characters
    of the SHA-256 of that unit's bytes. The order of the mapping does not matter.

    Raises `HashInputError`, naming the key, for a key that is empty, holds NUL or LF, or cannot
    be encoded as UTF-8, and for a digest that is not 64 lowercase hex characters.
    """
    if not unit_digests:
        return ""
    lines = []
    for key in sorted(unit_digests):
        digest = unit_digests[key]
        _check_unit(key, digest)
        lines.append(f"{key}\x00{digest}")
    return sha256_hex("\n".join(lines).encode("utf-8"))


def shared_hash(units: Mapping[str, bytes]) -> str:
    """Return `shared_fold` of the SHA-256 of each unit's bytes.

    `units` maps a unit id or repo-relative POSIX path to the bytes of that unit, one entry per
    file: a file that is both imported and listed as an asset appears once.
    """
    return shared_fold({key: sha256_hex(data) for key, data in units.items()})


def _fold(*parts: str) -> str:
    return "sha256:" + sha256_hex("|".join(parts).encode("utf-8"))


def _check_unit(key: str, digest: str) -> None:
    if not key:
        raise HashInputError(f"shared unit key {key!r}: the key is empty")
    if "\x00" in key or "\n" in key:
        raise HashInputError(f"shared unit key {key!r}: the key holds NUL or LF")
    try:
        key.encode("utf-8")
    except UnicodeEncodeError as error:
        raise HashInputError(
            f"shared unit key {key!r}: the key cannot be encoded as UTF-8"
        ) from error
    if not _DIGEST.fullmatch(digest):
        raise HashInputError(
            f"shared unit key {key!r}: the digest {digest!r} is not 64 lowercase hex characters"
        )
