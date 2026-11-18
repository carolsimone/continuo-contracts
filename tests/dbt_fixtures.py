"""The dbt manifests that `scripts/build_dbt_fixtures.py` builds from the fixture projects."""

import functools
import gzip
import json

from pathlib import Path
from typing import Any

MANIFESTS = Path(__file__).resolve().parent / "fixtures" / "dbt" / "manifests"


@functools.cache
def load_manifest(name: str) -> dict[str, Any]:
    """Return the manifest of the fixture project `name`.

    The result is cached and shared by every caller, so a test that changes it works on a
    `copy.deepcopy` of it.
    """
    with gzip.open(
        MANIFESTS / f"{name}.json.gz", "rt", encoding="utf-8"
    ) as stream:
        manifest: dict[str, Any] = json.load(stream)
    return manifest
