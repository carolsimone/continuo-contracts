import importlib.metadata

import continuo_contracts

DIST = "continuo-contracts"


def test_version_comes_from_the_installed_distribution():
    assert continuo_contracts.__version__ == importlib.metadata.version(DIST)


def test_distribution_supports_python_3_14_and_later():
    assert importlib.metadata.metadata(DIST)["Requires-Python"] == ">=3.14"


def test_distribution_is_apache_2_0_and_needs_only_jsonschema_and_pyyaml():
    meta = importlib.metadata.metadata(DIST)
    assert meta["License-Expression"] == "Apache-2.0"
    assert sorted(
        r.replace(" ", "").lower() for r in meta.get_all("Requires-Dist")
    ) == [
        "jsonschema>=4.26",
        "pyyaml>=6.0",
    ]
