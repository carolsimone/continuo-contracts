from pathlib import Path

import pytest

from continuo_contracts.fingerprints import (
    DBT_FAMILIES,
    PYTHON_FAMILIES,
    SECTIONS,
    load,
    render_markdown,
)

FP = load()
DOCS = Path(__file__).resolve().parents[1] / "docs" / "fingerprints.md"


def test_every_section_declares_entries():
    assert all(FP.entries(section) for section in SECTIONS)


@pytest.mark.parametrize("family", DBT_FAMILIES)
def test_each_dbt_family_has_the_config_rule(family):
    assert [e.id for e in FP.entries(family) if e.rule == "outside_denylist"] == ["other_config"]


@pytest.mark.parametrize("family", DBT_FAMILIES)
@pytest.mark.parametrize("key", sorted(FP.hashed_config_denylist))
def test_every_denylisted_key_has_its_own_entry(family, key):
    entry = FP.classify(family, f"config.{key}")
    assert entry is not None and entry.rule is None


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_every_python_hash_input_is_classified(family):
    names = [f"projection.{k}" for k in FP.projection_keys] + ["ci.source_hash", "ci.shared_code_hash"]
    assert FP.unclassified(family, names) == []


@pytest.mark.parametrize(("family", "name", "entry_id", "fingerprint", "parts"), [
    ("dbt-model", "raw_code", "raw_code", "content", ("source",)),
    ("dbt-model", "compiled_code", "compiled_code", "content", ("compiled",)),
    ("dbt-model", "unit_tests", "unit_tests", "content", ("shared",)),
    ("dbt-model", "project_flags", "service_units", "content", ("shared",)),
    ("dbt-model", "relation.alias", "alias", "both", ("config",)),
    ("dbt-model", "config.persist_docs", "other_config", "content", ("config",)),
    ("dbt-model", "config.metadata", "other_config", "content", ("config",)),
    ("dbt-model", "config.contract", "other_config", "content", ("config",)),
    ("dbt-model", "config.contract.enforced", "contract_enforced", "both", ("config",)),
    ("dbt-model", "config.access", "access_group", "contract", ()),
    ("dbt-model", "config.freshness", "freshness", "contract", ()),
    ("dbt-model", "config.meta", "meta_other", "neither", ()),
    ("dbt-model", "contract.columns", "declared_columns", "contract", ()),
    ("dbt-model", "constraints", "constraints", "neither", ()),
    ("dbt-seed", "seed_bytes", "csv_bytes", "content", ("source",)),
    ("dbt-seed", "seed_checksum", "csv_bytes", "content", ("source",)),
    ("dbt-seed", "config.column_types", "column_types", "both", ("config",)),
    ("dbt-seed", "config.delimiter", "other_config", "content", ("config",)),
    ("dbt-snapshot", "config.check_cols", "snapshot_config", "both", ("config",)),
    ("dbt-snapshot", "functions", "functions", "content", ("shared",)),
    ("dbt-test", "compiled_code", "compiled_code", "content", ("source",)),
    ("dbt-test", "config.severity", "severity", "both", ("config",)),
    ("dbt-test", "relation.schema", "failures_relation", "content", ("config",)),
    ("python-node", "projection.assets", "assets", "both", ("config", "shared")),
    ("python-node", "ci.shared_code_hash", "import_closure", "content", ("shared",)),
    ("python-node", "projection.secret_ref", "refused_csv_secret", "neither", ()),
    ("python-csv", "projection.reads", "csv_uri", "both", ("source", "config")),
    ("python-csv", "ci.shared_code_hash", "refused_script_inputs", "neither", ()),
    ("python-api", "projection.reads", "refused_reads", "neither", ()),
    ("python-api", "ci.source_hash", "repo_inputs", "content", ("source", "shared")),
])
def test_inputs_classify_as_declared(family, name, entry_id, fingerprint, parts):
    entry = FP.classify(family, name)
    assert (entry.id, entry.fingerprint, entry.parts) == (entry_id, fingerprint, parts)


def test_widened_entries_move_the_part_they_widen_to():
    assert FP.classify("dbt-model", "contract.columns").moves("config")
    assert FP.classify("python-api", "projection.primary_key").moves("config")
    assert not FP.classify("dbt-model", "config.tags").moves("config")


def test_inputs_a_family_does_not_hash_stay_unclassified():
    assert FP.unclassified("dbt-test", ["on_run_start", "seed_bytes", "compiled_code"]) == [
        "on_run_start", "seed_bytes"]
    assert FP.classify("python-node", "config.materialized") is None


FIXED_INPUT_NAMES = frozenset({
    "raw_code", "compiled_code", "seed_bytes", "seed_checksum",
    "relation.schema", "relation.alias",
    "contract.columns", "constraints",
    "macros", "functions", "unit_tests", "on_run_start", "on_run_end", "project_flags",
    "generate_schema_name", "generate_alias_name",
    "ci.source_hash", "ci.shared_code_hash",
})


def _is_in_the_input_vocabulary(name):
    """A fixed name, `config.<name>` or `config.*`, or `projection.<key>` for a projection key."""
    if name in FIXED_INPUT_NAMES or name == "config.*":
        return True
    if name.startswith("config."):
        return len(name) > len("config.")
    return name.startswith("projection.") and name.removeprefix("projection.") in FP.projection_keys


@pytest.mark.parametrize("name", [
    "seed_byte", "relation", "projection.nope", "projection.", "projection.config.x", "ci.other",
    "config.", "macro",
])
def test_the_input_vocabulary_check_refuses_a_name_outside_it(name):
    assert not _is_in_the_input_vocabulary(name)


def test_every_declared_input_name_is_in_the_closed_vocabulary():
    outside = [
        f"{section}/{entry.id}: {name}"
        for section in SECTIONS
        for entry in FP.entries(section)
        for name in entry.inputs
        if not _is_in_the_input_vocabulary(name)
    ]
    assert outside == []


ENTRY_COUNTS = {
    "every": 3,
    "dbt-model": 29,
    "dbt-seed": 19,
    "dbt-snapshot": 18,
    "dbt-test": 18,
    "python-node": 16,
    "python-csv": 9,
    "python-api": 10,
}


def test_the_entry_counts_cover_every_section():
    assert set(ENTRY_COUNTS) == set(SECTIONS)


@pytest.mark.parametrize(("section", "count"), sorted(ENTRY_COUNTS.items()))
def test_each_section_declares_its_entries(section, count):
    assert len(FP.entries(section)) == count


def test_the_docs_are_the_rendered_file():
    assert DOCS.read_text(encoding="utf-8") == render_markdown(FP), (
        "docs/fingerprints.md is stale: run `uv run python scripts/render_fingerprints.py`")
