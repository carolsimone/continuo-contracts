import json
import re
from pathlib import Path

import pytest

from continuo_contracts import fingerprints, yaml_strict
from continuo_contracts.contract import normalise_python
from continuo_contracts.fingerprints import (
    DBT_FAMILIES,
    FIXED_INPUT_NAMES,
    PYTHON_FAMILIES,
    SECTIONS,
    load,
    render_markdown,
)
from continuo_contracts.validate import _KIND_RULES
from continuo_contracts.vocabulary import PROPERTIES, continuo_values

FP = load()
ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "fingerprints.md"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VECTORS = ROOT / "continuo_contracts" / "vectors" / "contracts.json"
SERVICE = "service-py"


def test_every_section_declares_entries():
    assert all(FP.entries(section) for section in SECTIONS)


@pytest.mark.parametrize("family", DBT_FAMILIES)
def test_each_dbt_family_has_the_config_rule(family):
    assert [e.id for e in FP.entries(family) if e.rule == "outside_denylist"] == [
        "other_config"
    ]


@pytest.mark.parametrize("family", DBT_FAMILIES)
@pytest.mark.parametrize("key", sorted(FP.hashed_config_denylist))
def test_every_denylisted_key_has_its_own_entry(family, key):
    entry = FP.classify(family, f"config.{key}")
    assert entry is not None and entry.rule is None


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_every_python_hash_input_is_classified(family):
    names = [f"projection.{k}" for k in FP.projection_keys] + [
        "ci.source_hash",
        "ci.shared_code_hash",
    ]
    assert FP.unclassified(family, names) == []


@pytest.mark.parametrize(
    ("family", "name", "entry_id", "fingerprint", "parts"),
    [
        ("dbt-model", "raw_code", "raw_code", "content", ("source",)),
        ("dbt-model", "compiled_code", "compiled_code", "content", ("compiled",)),
        ("dbt-model", "unit_tests", "unit_tests", "content", ("shared",)),
        ("dbt-model", "project_flags", "service_units", "content", ("shared",)),
        ("dbt-model", "relation.alias", "alias", "both", ("config",)),
        (
            "dbt-model",
            "config.persist_docs",
            "other_config",
            "content",
            ("config",),
        ),
        ("dbt-model", "config.metadata", "other_config", "content", ("config",)),
        ("dbt-model", "config.contract", "other_config", "content", ("config",)),
        (
            "dbt-model",
            "config.contract.enforced",
            "contract_enforced",
            "both",
            ("config",),
        ),
        ("dbt-model", "config.access", "access_group", "contract", ()),
        ("dbt-model", "config.freshness", "freshness", "contract", ()),
        ("dbt-model", "config.meta", "meta_other", "neither", ()),
        ("dbt-model", "contract.columns", "declared_columns", "contract", ()),
        ("dbt-model", "constraints", "constraints", "neither", ()),
        ("dbt-seed", "seed_bytes", "csv_bytes", "content", ("source",)),
        ("dbt-seed", "seed_checksum", "csv_bytes", "content", ("source",)),
        ("dbt-seed", "config.column_types", "column_types", "both", ("config",)),
        ("dbt-seed", "config.delimiter", "other_config", "content", ("config",)),
        (
            "dbt-snapshot",
            "config.check_cols",
            "snapshot_config",
            "both",
            ("config",),
        ),
        ("dbt-snapshot", "functions", "functions", "content", ("shared",)),
        ("dbt-test", "compiled_code", "compiled_code", "content", ("source",)),
        ("dbt-test", "config.severity", "severity", "both", ("config",)),
        (
            "dbt-test",
            "relation.schema",
            "failures_relation",
            "content",
            ("config",),
        ),
        (
            "python-node",
            "projection.assets",
            "assets",
            "both",
            ("config", "shared"),
        ),
        (
            "python-node",
            "ci.shared_code_hash",
            "import_closure",
            "content",
            ("shared",),
        ),
        (
            "python-node",
            "projection.secret_ref",
            "refused_csv_secret",
            "neither",
            (),
        ),
        (
            "python-csv",
            "projection.reads",
            "csv_uri",
            "both",
            ("source", "config"),
        ),
        (
            "python-csv",
            "ci.shared_code_hash",
            "refused_script_inputs",
            "neither",
            (),
        ),
        ("python-api", "projection.reads", "refused_reads", "neither", ()),
        (
            "python-api",
            "ci.source_hash",
            "repo_inputs",
            "content",
            ("source", "shared"),
        ),
        ("python-node", "odcs.id", "schema", "both", ("config",)),
        (
            "python-node",
            "odcs.schema.properties.physicalType",
            "column_type",
            "both",
            ("config",),
        ),
        (
            "python-node",
            "odcs.custom.readQueries",
            "read_queries",
            "both",
            ("config",),
        ),
        (
            "python-node",
            "odcs.custom.timeoutMinutes",
            "timeout_minutes",
            "contract",
            (),
        ),
        ("python-node", "odcs.team.name", "team_schedule", "contract", ()),
        ("python-node", "odcs.schema.custom.reads", "reads", "contract", ()),
        (
            "python-node",
            "odcs.schema.properties.logicalType",
            "column_logical_type",
            "contract",
            (),
        ),
        (
            "python-node",
            "odcs.schema.properties.unique",
            "column_unique",
            "contract",
            (),
        ),
        (
            "python-node",
            "odcs.custom.csvSource",
            "refused_csv_secret",
            "neither",
            (),
        ),
        (
            "python-node",
            "odcs.custom.filePath",
            "refused_continuo_properties",
            "neither",
            (),
        ),
        (
            "python-node",
            "odcs.description.usage",
            "standard_field",
            "contract",
            (),
        ),
        (
            "python-csv",
            "odcs.custom.csvSource",
            "csv_uri",
            "both",
            ("source", "config"),
        ),
        (
            "python-csv",
            "odcs.custom.script",
            "refused_script_inputs",
            "neither",
            (),
        ),
        (
            "python-csv",
            "odcs.schema.properties.primaryKey",
            "primary_key",
            "contract",
            (),
        ),
        (
            "python-api",
            "odcs.custom.secretRef",
            "secret_ref",
            "both",
            ("config",),
        ),
        ("python-api", "odcs.custom.readQueries", "refused_reads", "neither", ()),
        (
            "python-api",
            "odcs.custom.modeType",
            "run_settings",
            "both",
            ("config",),
        ),
    ],
)
def test_inputs_classify_as_declared(family, name, entry_id, fingerprint, parts):
    entry = FP.classify(family, name)
    assert (entry.id, entry.fingerprint, entry.parts) == (
        entry_id,
        fingerprint,
        parts,
    )


def test_widened_entries_move_the_part_they_widen_to():
    assert FP.classify("dbt-model", "contract.columns").moves("config")
    assert FP.classify("python-api", "projection.primary_key").moves("config")
    assert FP.classify(
        "python-node", "odcs.schema.properties.primaryKeyPosition"
    ).moves("config")
    assert not FP.classify(
        "python-csv", "odcs.schema.properties.primaryKey"
    ).moves("config")
    assert not FP.classify("dbt-model", "config.tags").moves("config")


def test_inputs_a_family_does_not_hash_stay_unclassified():
    assert FP.unclassified(
        "dbt-test", ["on_run_start", "seed_bytes", "compiled_code"]
    ) == ["on_run_start", "seed_bytes"]
    assert FP.classify("python-node", "config.materialized") is None


# The stored form of a Python contract: the three demo and the three full contracts, and a messy
# spelling of the python-api one, each normalised as continuo's topology-controller does it.
# The tables a node reads come from the contracts vector, which lists them by file name.
_FREE_FORM = frozenset(
    {
        "examples",
        "implementation",
        "arguments",
        "mustBe",
        "mustNotBe",
        "value",
        "valueExt",
        "logicalTypeOptions",
    }
)


def _stored_forms():
    """Return `(name, stored)` for each stored form of the contracts the repository checks in."""
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))
    reads = {Path(case["path"]).name: case["reads"] for case in cases}
    files = sorted(
        (FIXTURES / "demo" / "service-py" / "contracts").glob("*.odcs.yaml")
    )
    files += sorted((FIXTURES / "full").glob("*.odcs.yaml"))
    forms = [
        (
            path.name,
            normalise_python(
                yaml_strict.load(path.read_text(encoding="utf-8")),
                service=SERVICE,
                reads=reads[path.name],
            ),
        )
        for path in files
    ]
    messy = next(case for case in cases if case["name"] == "messy_python_api")
    forms.append(
        (
            messy["name"],
            normalise_python(
                yaml_strict.load(messy["text"]),
                service=SERVICE,
                reads=messy["reads"],
            ),
        )
    )
    return forms


STORED_FORMS = _stored_forms()
FULL_FILES = {
    "python-node": "python_node.odcs.yaml",
    "python-csv": "python_csv.odcs.yaml",
    "python-api": "python_api.odcs.yaml",
}


def _full_stored(family):
    return dict(STORED_FORMS)[FULL_FILES[family]]


def _kind(stored):
    return continuo_values(stored["customProperties"])["kind"]


def _walk(node, path, names):
    """Add to `names` the `odcs.<path>` of each field under `node`, array indexes dropped.

    A `continuo` custom property is `<path>.custom.<property>`, another vendor's is
    `<path>.customProperties`, and the walk stops at a free-form value.
    """
    if isinstance(node, list):
        for item in node:
            _walk(item, path, names)
        return
    for key, value in node.items():
        if key == "customProperties":
            for item in value:
                where = (
                    f"{path}.custom.{item['property']}"
                    if item["vendor"] == "continuo"
                    else (f"{path}.customProperties")
                )
                names.add(where)
        elif key not in _FREE_FORM and _holds_objects(value):
            _walk(value, f"{path}.{key}", names)
        else:
            names.add(f"{path}.{key}")


def _holds_objects(value):
    return isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(item, dict) for item in value)
    )


def _odcs_inputs(stored):
    """Return the `odcs.<path>` of every field of `stored` that `contract_hash` hashes."""
    left_out = fingerprints.contract_hash_exclusions()
    names = set()
    _walk(
        {key: value for key, value in stored.items() if key not in left_out},
        "odcs",
        names,
    )
    return names


def test_the_contract_hash_exclusions():
    assert fingerprints.contract_hash_exclusions() == frozenset(
        {"apiVersion", "kind", "status", "version"}
    )


@pytest.mark.parametrize(
    ("name", "stored"), STORED_FORMS, ids=[n for n, _ in STORED_FORMS]
)
def test_the_excluded_keys_hold_constants_in_every_stored_form(name, stored):
    assert (
        stored["apiVersion"],
        stored["kind"],
        stored["version"],
        stored["status"],
    ) == ("v3.2.0", "DataContract", "0", "active")


def test_the_stored_forms_cover_the_three_kinds():
    assert sorted(_kind(stored) for _, stored in STORED_FORMS) == sorted(
        ["python-node", "python-csv", "python-api"] * 2 + ["python-api"]
    )


def test_the_walk_names_the_hashed_fields_and_leaves_out_the_excluded_keys():
    names = _odcs_inputs(_full_stored("python-node"))
    assert {
        "odcs.id",
        "odcs.name",
        "odcs.team.name",
        "odcs.tags",
        "odcs.description.purpose",
        "odcs.custom.timeoutMinutes",
        "odcs.custom.readQueries",
        "odcs.customProperties",
        "odcs.schema.name",
        "odcs.schema.physicalName",
        "odcs.schema.physicalType",
        "odcs.schema.custom.reads",
        "odcs.schema.properties.logicalType",
        "odcs.schema.properties.logicalTypeOptions",
        "odcs.schema.properties.examples",
        "odcs.schema.properties.quality.custom.where",
        "odcs.schema.properties.relationships.to",
        "odcs.slaProperties.value",
        "odcs.support.channel",
        "odcs.roles.role",
    } <= names
    assert not {
        name
        for name in names
        if name.split(".")[1] in fingerprints.contract_hash_exclusions()
    }


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_every_stored_python_field_is_classified(family):
    stored = _full_stored(family)
    assert _kind(stored) == family
    for name in sorted(_odcs_inputs(stored)):
        entry = fingerprints.classify(family, name)
        assert entry is not None, name
        assert entry.fingerprint in ("contract", "both"), name


def test_every_field_of_every_stored_form_is_classified_for_its_kind():
    for file, stored in STORED_FORMS:
        family = _kind(stored)
        for name in sorted(_odcs_inputs(stored)):
            entry = fingerprints.classify(family, name)
            assert entry is not None and entry.fingerprint in (
                "contract",
                "both",
            ), (file, name)


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_the_excluded_keys_are_classified_neither(family):
    for key in sorted(fingerprints.contract_hash_exclusions()):
        entry = fingerprints.classify(family, f"odcs.{key}")
        assert entry is not None and entry.fingerprint == "neither", key
        assert entry.rule is None, key


REFUSED_PROPERTIES = sorted(
    name for name, prop in PROPERTIES.items() if prop.python != "authored"
)


def test_the_refused_continuo_properties_are_the_eleven_of_a_python_contract():
    assert REFUSED_PROPERTIES == sorted(
        "provenance filePath materialization contractEnforced access group deprecationDate "
        "freshnessBuildAfter snapshot quoteColumns reads".split()
    )


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_every_refused_continuo_property_is_classified_neither(family):
    for name in REFUSED_PROPERTIES:
        entry = fingerprints.classify(family, f"odcs.custom.{name}")
        assert entry is not None and entry.fingerprint == "neither", name
        assert entry.rule is None, name


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_the_filled_reads_property_is_contract_on_the_schema_object(family):
    entry = fingerprints.classify(family, "odcs.schema.custom.reads")
    assert (
        entry is not None
        and entry.fingerprint == "contract"
        and entry.id == "reads"
    )


def _quality_paths(name):
    return [
        f"odcs.schema.quality.custom.{name}",
        f"odcs.schema.properties.quality.custom.{name}",
    ]


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_each_authored_continuo_property_is_classified_as_validate_allows_it(
    family,
):
    refused = _KIND_RULES[family].refused
    for name, prop in PROPERTIES.items():
        if prop.python != "authored":
            continue
        paths = {
            "root": [f"odcs.custom.{name}"],
            "quality": _quality_paths(name),
        }[prop.sits_on]
        for path in paths:
            entry = fingerprints.classify(family, path)
            assert entry is not None, path
            expected = ("neither",) if name in refused else ("contract", "both")
            assert entry.fingerprint in expected, path


def test_an_unknown_continuo_property_is_not_classified_by_the_rule():
    assert fingerprints.classify("python-node", "odcs.custom.madeUp") is None


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
@pytest.mark.parametrize(
    "path",
    [
        "odcs.custom.madeUp",
        "odcs.schema.custom.madeUp",
        "odcs.schema.properties.custom.madeUp",
        "odcs.schema.quality.custom.madeUp",
        "odcs.schema.properties.quality.custom.madeUp",
    ],
)
def test_a_continuo_property_no_entry_names_stays_unclassified(family, path):
    assert fingerprints.classify(family, path) is None


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
@pytest.mark.parametrize(
    "path",
    [
        "odcs.description.usage",
        "odcs.description.limitations",
        "odcs.domain",
        "odcs.context",
        "odcs.dataProduct",
        "odcs.slaDefaultElement",
        "odcs.authoritativeDefinitions.url",
        "odcs.schema.quality.customProperties",
        "odcs.schema.businessName",
        "odcs.schema.properties.businessName",
        "odcs.schema.properties.synonyms",
        "odcs.schema.properties.deprecated",
        "odcs.schema.properties.enum",
        "odcs.schema.properties.criticalDataElement",
        "odcs.schema.properties.transformLogic",
        "odcs.schema.properties.authoritativeDefinitions.url",
        "odcs.slaProperties.element",
    ],
)
def test_a_standard_field_no_entry_names_is_contract_by_rule(family, path):
    entry = fingerprints.classify(family, path)
    assert (
        entry is not None
        and entry.rule == "standard_field"
        and entry.fingerprint == "contract"
    )


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
@pytest.mark.parametrize(
    "path",
    [
        "odcs.customProperties",
        "odcs.schema.customProperties",
        "odcs.schema.properties.customProperties",
        "odcs.price.priceAmount",
        "odcs.slaProperties.property",
        "odcs.support.channel",
        "odcs.roles.role",
        "odcs.schema.properties.classification",
        "odcs.schema.properties.relationships.to",
        "odcs.schema.properties.quality.metric",
    ],
)
def test_a_contract_field_with_an_entry_of_its_own_is_not_left_to_the_rule(
    family, path
):
    entry = fingerprints.classify(family, path)
    assert (
        entry is not None
        and entry.rule is None
        and entry.fingerprint == "contract"
    )


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
@pytest.mark.parametrize(
    "path",
    [
        "odcs.servers",
        "odcs.tenant",
        "odcs.contractCreatedTs",
        "odcs.version",
        "odcs.status",
        "odcs.apiVersion",
        "odcs.kind",
        "odcs.schema.properties.properties",
        "odcs.schema.properties.items",
        "odcs.schema.properties.map",
        "odcs.schema.properties.partitioned",
        "odcs.schema.properties.partitionKeyPosition",
        "odcs.schema.properties.physicalName",
    ],
)
def test_a_standard_field_a_python_contract_refuses_or_drops_is_not_left_to_the_rule(
    family, path
):
    entry = fingerprints.classify(family, path)
    assert (
        entry is not None
        and entry.fingerprint == "neither"
        and entry.rule is None
    )


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_each_python_family_has_one_standard_field_rule(family):
    assert [e.id for e in FP.entries(family) if e.rule == "standard_field"] == [
        "standard_field"
    ]
    assert FP.classify(family, "odcs.*").rule == "standard_field"


@pytest.mark.parametrize("family", (*DBT_FAMILIES, "every"))
def test_no_other_section_has_the_standard_field_rule(family):
    assert [e.id for e in FP.entries(family) if e.rule == "standard_field"] == []


@pytest.mark.parametrize("family", DBT_FAMILIES)
def test_the_standard_field_rule_does_not_classify_a_dbt_family(family):
    assert FP.classify(family, "odcs.description.usage") is None


# `odcs.<path>` is `odcs` and then ODCS or continuo names as written, camelCase allowed.
ODCS_INPUT = re.compile(r"odcs(\.([A-Za-z][A-Za-z0-9]*|\*))+")


def _is_in_the_input_vocabulary(name):
    """A fixed name, `config.<name>` or `config.*`, `projection.<key>` for a projection key, or
    `odcs.<path>`, where `*` is only the whole name `odcs.*`."""
    if name in FIXED_INPUT_NAMES or name == "config.*" or name == "odcs.*":
        return True
    if name.startswith("odcs."):
        return ODCS_INPUT.fullmatch(name) is not None and "*" not in name
    if name.startswith("config."):
        return len(name) > len("config.")
    return (
        name.startswith("projection.")
        and name.removeprefix("projection.") in FP.projection_keys
    )


@pytest.mark.parametrize(
    "name",
    [
        "seed_byte",
        "relation",
        "projection.nope",
        "projection.",
        "projection.config.x",
        "ci.other",
        "config.",
        "macro",
        "odcs",
        "odcs.",
        "odcs.1a",
        "odcs.a_b",
        "odcs.a-b",
        "odcs.schema.*",
        "odcs.*.name",
    ],
)
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


def test_the_fixed_input_names_are_distinct():
    assert len(set(FIXED_INPUT_NAMES)) == len(FIXED_INPUT_NAMES)


# Terms the input-names section explains in prose: the `enforced` key of the dbt contract settings
# and the function `shared_hash`. They are written in backticks and are not input names.
_PROSE_TERMS = {"enforced", "shared_hash"}
_PATTERN_PREFIXES = ("config.", "projection.", "odcs.")


def _backticked_in_the_input_names_section():
    lines = render_markdown(FP).split("\n")
    start = lines.index("The names in the *Inputs* column are these:")
    end = next(
        i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")
    )
    return re.findall(r"`([^`]+)`", "\n".join(lines[start:end]))


def test_every_fixed_input_name_is_written_in_the_rendered_input_names():
    written = set(_backticked_in_the_input_names_section())
    assert [name for name in FIXED_INPUT_NAMES if name not in written] == []


def test_every_fixed_name_in_the_rendered_input_names_is_a_fixed_input_name():
    fixed = [
        token
        for token in _backticked_in_the_input_names_section()
        if not token.startswith(_PATTERN_PREFIXES) and token not in _PROSE_TERMS
    ]
    assert [token for token in fixed if token not in FIXED_INPUT_NAMES] == []


ENTRY_COUNTS = {
    "every": 3,
    "dbt-model": 29,
    "dbt-seed": 19,
    "dbt-snapshot": 18,
    "dbt-test": 18,
    "python-node": 39,
    "python-csv": 32,
    "python-api": 33,
}


def test_the_entry_counts_cover_every_section():
    assert set(ENTRY_COUNTS) == set(SECTIONS)


@pytest.mark.parametrize(("section", "count"), sorted(ENTRY_COUNTS.items()))
def test_each_section_declares_its_entries(section, count):
    assert len(FP.entries(section)) == count


def test_the_docs_are_the_rendered_file():
    assert DOCS.read_text(encoding="utf-8") == render_markdown(FP), (
        "docs/fingerprints.md is stale: run `uv run python scripts/render_fingerprints.py`"
    )
