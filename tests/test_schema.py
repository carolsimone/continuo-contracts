import hashlib

from importlib import resources

import pytest

from continuo_contracts import schema
from continuo_contracts.errors import ContractsError, SchemaFileError


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Start and leave every test with an empty `load` cache, so a test that changes the bytes
    cannot leak its result into another."""
    schema.load.cache_clear()
    yield
    schema.load.cache_clear()


def test_the_schema_bytes_have_the_pinned_hash():
    data = (
        resources.files("continuo_contracts") / schema.SCHEMA_FILE
    ).read_bytes()
    assert hashlib.sha256(data).hexdigest() == schema.SCHEMA_SHA256


def test_load_returns_the_draft_2019_09_schema():
    s = schema.load()
    assert s["$schema"] == "https://json-schema.org/draft/2019-09/schema"
    assert s["required"] == ["version", "apiVersion", "kind", "id"]
    assert schema.load() is s


def test_changed_bytes_are_refused(monkeypatch):
    monkeypatch.setattr(schema, "_read_bytes", lambda: b"{}")
    with pytest.raises(SchemaFileError, match="odcs-json-schema-v3.2.0.json"):
        schema.load()


def test_the_refusal_names_both_hashes(monkeypatch):
    monkeypatch.setattr(schema, "_read_bytes", lambda: b"{}")
    got = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(SchemaFileError) as raised:
        schema.load()
    assert got in str(raised.value)
    assert schema.SCHEMA_SHA256 in str(raised.value)


def test_a_refused_read_is_not_cached(monkeypatch):
    monkeypatch.setattr(schema, "_read_bytes", lambda: b"{}")
    with pytest.raises(SchemaFileError):
        schema.load()
    monkeypatch.undo()
    assert schema.load()["required"] == ["version", "apiVersion", "kind", "id"]


def test_schema_file_error_is_a_contracts_error():
    assert issubclass(SchemaFileError, ContractsError)
