import copy
import json
import re

from pathlib import Path

import pytest

from continuo_contracts import dbt, fingerprints, ontology, yaml_strict
from continuo_contracts.contract import normalise_dbt, normalise_python
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
DBT_VECTORS = ROOT / "continuo_contracts" / "vectors" / "dbt_contracts.json"
GOLDEN = FIXTURES / "dbt" / "golden"
PROJECT = "service-py"


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
        ("dbt-model", "project_flags", "project_units", "content", ("shared",)),
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


# The stored forms the repository checks in: for the Python families, the three demo and the three
# full contracts, and a messy spelling of the python-api one, each normalised as continuo's
# topology-controller does it, with the tables a node reads from the contracts vector, which lists
# them by file name; for the dbt families, the stored form of every golden contract and the one of
# the vector case whose node reads tables.
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


def _dbt_stored_forms():
    """Return `(name, stored)` for the stored form of every golden and of the reads vector case."""
    forms = [
        (
            f"{path.parent.name}/{path.stem}",
            json.loads(path.read_text(encoding="utf-8"))["stored"],
        )
        for path in sorted(GOLDEN.glob("*/*.json"))
    ]
    cases = json.loads(DBT_VECTORS.read_text(encoding="utf-8"))
    forms.append(
        (
            "vector/sql_reads",
            next(c for c in cases if c["name"] == "sql_reads")["stored"],
        )
    )
    return forms


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
                project=PROJECT,
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
                project=PROJECT,
                reads=messy["reads"],
            ),
        )
    )
    return forms + _dbt_stored_forms()


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


def test_the_stored_forms_cover_the_six_kinds():
    kinds = [_kind(stored) for _, stored in STORED_FORMS]
    python = sorted(kind for kind in kinds if kind in PYTHON_FAMILIES)
    assert python == sorted(
        ["python-node", "python-csv", "python-api"] * 2 + ["python-api"]
    )
    assert {kind for kind in kinds if kind not in PYTHON_FAMILIES} == {
        "dbt-model",
        "dbt-seed",
        "dbt-snapshot",
    }


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


DBT_STORED_FORMS = [
    (name, stored)
    for name, stored in STORED_FORMS
    if _kind(stored) in DBT_FAMILIES
]


def _in_the_contract_hash(family, name):
    entry = fingerprints.classify(family, name)
    return entry is not None and entry.fingerprint in ("contract", "both")


@pytest.mark.parametrize("family", ["dbt-model", "dbt-seed", "dbt-snapshot"])
def test_every_stored_dbt_field_is_classified(family):
    forms = [stored for _, stored in DBT_STORED_FORMS if _kind(stored) == family]
    assert forms, family
    names = {name for stored in forms for name in _odcs_inputs(stored)}
    missing = [
        name for name in sorted(names) if not _in_the_contract_hash(family, name)
    ]
    assert not missing, (
        f"{family}: not classified as contract or both: " + ", ".join(missing)
    )


def test_the_dbt_stored_forms_are_the_golden_contracts_and_the_reads_case():
    goldens = sorted(GOLDEN.glob("*/*.json"))
    assert goldens
    assert [name for name, _ in DBT_STORED_FORMS] == [
        f"{path.parent.name}/{path.stem}" for path in goldens
    ] + ["vector/sql_reads"]
    reads = dict(DBT_STORED_FORMS)["vector/sql_reads"]
    assert "odcs.schema.custom.reads" in _odcs_inputs(reads)


def test_the_dbt_test_family_classifies_no_odcs_input():
    declared = [
        name
        for entry in FP.entries("dbt-test")
        for name in entry.inputs
        if name == "odcs" or name.startswith("odcs.")
    ]
    assert declared == []
    names = {
        name for _, stored in DBT_STORED_FORMS for name in _odcs_inputs(stored)
    }
    assert names
    assert FP.unclassified("dbt-test", names) == sorted(names)


@pytest.mark.parametrize("family", ["dbt-model", "dbt-seed", "dbt-snapshot"])
def test_the_reads_of_a_dbt_contract_are_classified_once_as_a_contract_fact(
    family,
):
    entry = fingerprints.classify(family, "odcs.schema.custom.reads")
    assert entry is not None
    assert (entry.id, entry.fingerprint, entry.parts, entry.when) == (
        "sql_reads",
        "contract",
        (),
        None,
    )
    assert entry.field == "Tables the compiled SQL names"
    assert entry.source == "`compiled_code`, listed by topology-controller"
    assert [
        e.id for e in FP.entries(family) if "odcs.schema.custom.reads" in e.inputs
    ] == ["sql_reads"]


# The ontology part and what an ontology fills in. A binding is a `continuo` custom property: on the
# schema object it is `odcs.schema.custom.<name>`, on a column `odcs.schema.properties.custom.<name>`.
# A link to the ontology is an `authoritativeDefinitions` entry, which the input names take down to
# its `type` and `url`.
BINDINGS = {
    "schema": ("entity", "grain", "fixed", "filter"),
    "column": ("property", "local", "aggregate"),
}
BINDING_PATHS = (
    *(f"odcs.schema.custom.{name}" for name in BINDINGS["schema"]),
    *(f"odcs.schema.properties.custom.{name}" for name in BINDINGS["column"]),
)
LINK_PATHS = (
    "odcs.schema.authoritativeDefinitions.type",
    "odcs.schema.authoritativeDefinitions.url",
    "odcs.schema.properties.authoritativeDefinitions.type",
    "odcs.schema.properties.authoritativeDefinitions.url",
)
PRIMARY_KEY_PATHS = (
    "odcs.schema.properties.primaryKey",
    "odcs.schema.properties.primaryKeyPosition",
)
CONTRACT_FAMILIES = ("dbt-model", "dbt-seed", "dbt-snapshot", *PYTHON_FAMILIES)
DBT_CONTRACT_FAMILIES = CONTRACT_FAMILIES[:3]


def _continuo(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def _link(url):
    return [{"type": "ontology", "url": url}]


DEMO_ONTOLOGY = ontology.load(
    (FIXTURES / "ontology" / "demo.yaml").read_text(encoding="utf-8")
)


def _bound_stored():
    """Return the stored form of a contract of the `core` project that binds a thing.

    It writes every binding a team can write and none of what an ontology fills in: the column
    `source` is left to the implicit binding, and no column declares a key.
    """
    return {
        "customProperties": [
            _continuo("project", "core"),
            _continuo("kind", "dbt-model"),
        ],
        "schema": [
            {
                "name": "transactions_monthly",
                "customProperties": [
                    _continuo("entity", "Transaction"),
                    _continuo("grain", ["month"]),
                    _continuo("fixed", {"source": "card"}),
                    _continuo("filter", {"currency": "EUR"}),
                ],
                "properties": [
                    {"name": "source"},
                    {
                        "name": "transaction_id",
                        "customProperties": [
                            _continuo("property", "Transaction.transaction_id")
                        ],
                    },
                    {
                        "name": "buyer_email",
                        "customProperties": [_continuo("property", "User.email")],
                    },
                    {
                        "name": "note",
                        "customProperties": [_continuo("local", True)],
                    },
                    {
                        "name": "total",
                        "customProperties": [
                            _continuo(
                                "aggregate",
                                {"from": "Transaction.amount_eur", "agg": "sum"},
                            )
                        ],
                    },
                ],
            }
        ],
    }


def _ontology_stored():
    """Return the stored form of a contract that binds a thing, as the demo ontology fills it in."""
    return ontology.resolve(_bound_stored(), DEMO_ONTOLOGY)


# The goldens and the vector do not exercise every field the generator writes: a composite primary
# key, a column type with a length, and the settings of a test on a column or on the schema object.
# `_full_dbt_stored` generates the contract of one vector node of each dbt kind with all of them set.
_SETTINGS = {
    "severity": "WARN",
    "where": "id > 0",
    "warn_if": ">1",
    "error_if": ">2",
    "fail_calc": "max(id)",
}


def _dbt_test(
    node_id, name, column, generic, kwargs=None, extra_nodes=(), strict=False
):
    """Return a test node; a `strict` one has dbt's default severity, `where` and thresholds.

    dbt resolved its generic test to dbt's own macro of that name, so one of dbt's tests is dbt's.
    """
    return {
        "name": name,
        "unique_id": f"test.cases.{name}.0",
        "attached_node": node_id,
        "column_name": column,
        "depends_on": {
            "macros": [f"macro.dbt.test_{generic}"],
            "nodes": [*extra_nodes, node_id],
        },
        "description": "checks the table",
        "tags": ["quality"],
        "test_metadata": {
            "name": generic,
            "namespace": None,
            "kwargs": kwargs or {},
        },
        "config": {
            "severity": "ERROR",
            "where": None,
            "warn_if": "!= 0",
            "error_if": "!= 0",
            "fail_calc": "count(*)",
        }
        if strict
        else dict(_SETTINGS),
    }


def _full_dbt_stored(case_name):
    cases = {c["name"]: c for c in json.loads(DBT_VECTORS.read_text("utf-8"))}
    node = copy.deepcopy(cases[case_name]["node"])
    node_id = node["unique_id"]
    node["description"] = "A node."
    node["tags"] = ["daily"]
    node["columns"] = {
        "id": {
            "name": "id",
            "data_type": "varchar(10)",
            "tags": ["key"],
            "meta": {"continuo": {"property": "Order.id"}},
        },
        "ref_id": {
            "name": "ref_id",
            "description": "A reference.",
            "meta": {"continuo": {"classification": "personal", "local": True}},
        },
        "total": {
            "name": "total",
            "meta": {
                "continuo": {
                    "from": "Order.amount",
                    "agg": "sum",
                    "where": {"status": "open"},
                    "period": "day",
                }
            },
        },
    }
    config = node["config"]
    config["grants"] = {"select": ["analyst"]}
    config["group"] = "finance"
    config["contract"] = {"enforced": True}
    config["meta"] = {
        "owner": "cases",
        "criticality": "CORE",
        "timeout_minutes": 30,
        "continuo": {
            "readers": ["reader"],
            "reads": ["raw.orders"],
            "entity": "Order",
            "grain": ["id"],
            "fixed": {"region": "eu"},
            "filter": {"status": "open"},
            "tests": [
                {"name": "rows", "granularity": "daily", "params": {"min": 1}}
            ],
        },
    }
    if node["resource_type"] == "model":
        node["primary_key"] = ["id", "ref_id"]
        node["deprecation_date"] = "2030-01-01"
        config["access"] = "public"
        config["freshness"] = {"build_after": {"count": 1, "period": "day"}}
    target = "model.cases.customer.v2"
    manifest = cases["model_with_tests"]["manifest"]
    tests = [
        _dbt_test(node_id, "not_null_id", "id", "not_null", strict=True),
        _dbt_test(node_id, "unique_id", "id", "unique", strict=True),
        _dbt_test(
            node_id,
            "values_ref_id",
            "ref_id",
            "accepted_values",
            {"values": ["a"]},
        ),
        _dbt_test(
            node_id, "positive_ref_id", "ref_id", "is_positive", {"min_value": 0}
        ),
        _dbt_test(
            node_id,
            "ref_id_customer",
            "ref_id",
            "relationships",
            {"to": "ref('customer')", "field": "id"},
            [target],
            strict=True,
        ),
        _dbt_test(node_id, "not_null_ghost", "ghost", "not_null"),
        _dbt_test(
            node_id, "positive_ghost", "ghost", "is_positive", {"min_value": 0}
        ),
        _dbt_test(node_id, "assert_rows", None, ""),
    ]
    document = dbt.generate(node, tests, manifest=manifest).document
    return normalise_dbt(document, reads=["raw.events"])


@pytest.mark.parametrize(
    ("case", "family"),
    [
        ("model_with_tests", "dbt-model"),
        ("seed_with_column_types", "dbt-seed"),
        ("snapshot", "dbt-snapshot"),
    ],
)
def test_every_field_a_dbt_contract_can_hold_is_classified(case, family):
    stored = _full_dbt_stored(case)
    assert _kind(stored) == family
    names = _odcs_inputs(stored)
    assert {
        "odcs.custom.contractEnforced",
        "odcs.schema.custom.reads",
        "odcs.schema.properties.logicalTypeOptions",
        "odcs.schema.properties.quality.custom.failCalc",
        "odcs.schema.properties.quality.description",
        "odcs.schema.quality.custom.warnIf",
        "odcs.schema.quality.severity",
        "odcs.schema.quality.tags",
        "odcs.schema.quality.metric",
        "odcs.schema.quality.engine",
        "odcs.roles.access",
        *BINDING_PATHS,
    } <= names
    missing = [
        name for name in sorted(names) if not _in_the_contract_hash(family, name)
    ]
    assert not missing, (
        f"{family}: not classified as contract or both: " + ", ".join(missing)
    )


@pytest.mark.parametrize(
    ("case", "family"),
    [
        ("model_with_tests", "dbt-model"),
        ("seed_with_column_types", "dbt-seed"),
        ("snapshot", "dbt-snapshot"),
    ],
)
def test_every_odcs_input_a_dbt_family_names_is_written_by_a_contract(
    case, family
):
    written = _odcs_inputs(_full_dbt_stored(case))
    for _, stored in DBT_STORED_FORMS:
        if _kind(stored) == family:
            written |= _odcs_inputs(stored)
    # The generator does not write what an ontology fills in, so what `ontology.resolve` writes
    # into a bound contract stands for it.
    written |= _odcs_inputs(_ontology_stored())
    declared = {
        name
        for entry in FP.entries(family)
        for name in entry.inputs
        if name.startswith("odcs.")
    }
    assert sorted(declared - written) == []


@pytest.mark.parametrize(
    ("case", "family"),
    [
        ("model_with_tests", "dbt-model"),
        ("seed_with_column_types", "dbt-seed"),
        ("snapshot", "dbt-snapshot"),
    ],
)
def test_the_enforced_flag_of_every_dbt_kind_is_both(case, family):
    stored = _full_dbt_stored(case)
    assert continuo_values(stored["customProperties"])["contractEnforced"] is True
    for name in ("config.contract.enforced", "odcs.custom.contractEnforced"):
        entry = fingerprints.classify(family, name)
        assert entry is not None, name
        assert (entry.id, entry.fingerprint, entry.parts) == (
            "contract_enforced",
            "both",
            ("config",),
        )
    assert "odcs.custom.contractEnforced" in _odcs_inputs(stored)


def test_a_composite_primary_key_is_classified_for_a_model():
    names = _odcs_inputs(_full_dbt_stored("model_with_tests"))
    assert "odcs.schema.properties.primaryKeyPosition" in names
    assert _in_the_contract_hash(
        "dbt-model", "odcs.schema.properties.primaryKeyPosition"
    )


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
    eleven = [
        "provenance",
        "filePath",
        "materialization",
        "contractEnforced",
        "access",
        "group",
        "deprecationDate",
        "freshnessBuildAfter",
        "snapshot",
        "quoteColumns",
        "reads",
    ]
    assert sorted(eleven) == REFUSED_PROPERTIES


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
            "schema": [f"odcs.schema.custom.{name}"],
            "column": [f"odcs.schema.properties.custom.{name}"],
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
        "odcs.schema.authoritativeDefinitions.url",
        "odcs.schema.properties.authoritativeDefinitions.url",
        "odcs.schema.properties.semanticType",
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


# Terms the input-names section explains in prose: the `enforced` key of the dbt contract settings,
# the functions `shared_hash` and `contract_hash` and the list `contract_hash_exclusions`. They
# are written in backticks and are not input names.
_PROSE_TERMS = {
    "enforced",
    "shared_hash",
    "contract_hash",
    "contract_hash_exclusions",
}
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


def test_the_bindings_are_the_authored_properties_of_the_schema_object_and_a_column():
    assert {
        place: tuple(
            name
            for name, prop in PROPERTIES.items()
            if prop.python == "authored" and prop.sits_on == place
        )
        for place in BINDINGS
    } == BINDINGS


@pytest.mark.parametrize("family", CONTRACT_FAMILIES)
def test_every_binding_and_resolved_field_is_classified_contract(family):
    for path in BINDING_PATHS:
        entry = fingerprints.classify(family, path)
        assert entry is not None, path
        assert (entry.id, entry.fingerprint, entry.parts, entry.when) == (
            "ontology_bindings",
            "contract",
            (),
            None,
        ), path
    for path in (*LINK_PATHS, "odcs.schema.properties.semanticType"):
        entry = fingerprints.classify(family, path)
        assert entry is not None, path
        assert (entry.id, entry.fingerprint, entry.parts, entry.when) == (
            "ontology_resolved",
            "contract",
            (),
            None,
        ), path


@pytest.mark.parametrize("family", DBT_CONTRACT_FAMILIES)
def test_the_domain_an_ontology_fills_in_is_classified_in_a_dbt_family(family):
    entry = fingerprints.classify(family, "odcs.domain")
    assert entry is not None and entry.fingerprint == "contract"
    assert entry.id == "ontology_resolved" and entry.rule is None


@pytest.mark.parametrize("family", ["dbt-seed", "dbt-snapshot"])
def test_the_key_an_ontology_fills_in_is_classified_in_a_seed_and_a_snapshot(
    family,
):
    for path in PRIMARY_KEY_PATHS:
        entry = fingerprints.classify(family, path)
        assert entry is not None, path
        assert (entry.id, entry.fingerprint, entry.parts) == (
            "ontology_resolved",
            "contract",
            (),
        ), path


@pytest.mark.parametrize("family", PYTHON_FAMILIES)
def test_the_domain_an_ontology_fills_in_is_left_to_the_rule_in_a_python_family(
    family,
):
    entry = fingerprints.classify(family, "odcs.domain")
    assert entry is not None and entry.rule == "standard_field"


@pytest.mark.parametrize("family", CONTRACT_FAMILIES)
def test_a_contract_with_the_ontology_part_and_what_it_fills_in_is_classified(
    family,
):
    names = _odcs_inputs(_ontology_stored())
    assert {
        *BINDING_PATHS,
        *LINK_PATHS,
        *PRIMARY_KEY_PATHS,
        "odcs.domain",
        "odcs.schema.properties.semanticType",
    } <= names
    missing = [
        name for name in sorted(names) if not _in_the_contract_hash(family, name)
    ]
    assert not missing, (
        f"{family}: not classified as contract or both: " + ", ".join(missing)
    )


def test_the_dbt_test_family_classifies_no_ontology_input():
    names = _odcs_inputs(_ontology_stored())
    assert FP.unclassified("dbt-test", names) == sorted(names)


SNAPSHOT_COLUMN_ENTRIES = (
    "declared_columns",
    "column_data_type",
    "column_description_tags",
    "constraints",
    "column_quote",
)


def _entry(family, entry_id):
    return next(e for e in FP.entries(family) if e.id == entry_id)


@pytest.mark.parametrize("entry_id", SNAPSHOT_COLUMN_ENTRIES)
def test_a_snapshot_classifies_its_columns_like_a_model(entry_id):
    model, snapshot = (
        _entry("dbt-model", entry_id),
        _entry("dbt-snapshot", entry_id),
    )
    assert (
        snapshot.fingerprint,
        snapshot.parts,
        snapshot.when,
        snapshot.inputs,
    ) == (
        model.fingerprint,
        model.parts,
        model.when,
        model.inputs,
    )


def test_an_enforced_snapshot_classifies_its_columns_and_constraints_like_a_model():
    assert fingerprints.classify("dbt-snapshot", "contract.columns") is not None
    constraints = fingerprints.classify("dbt-snapshot", "constraints")
    assert constraints.fingerprint == "neither"
    assert constraints.when.fingerprint == "content"
    assert constraints.moves("config")
    for name in (
        "contract.columns",
        "odcs.schema.properties.name",
        "odcs.schema.properties.physicalType",
        "odcs.schema.properties.logicalType",
        "odcs.schema.properties.logicalTypeOptions",
    ):
        assert fingerprints.classify("dbt-snapshot", name).moves("config"), name
    for name in (
        "odcs.schema.properties.description",
        "odcs.schema.properties.tags",
    ):
        entry = fingerprints.classify("dbt-snapshot", name)
        assert entry.fingerprint == "contract" and not entry.moves("config"), name


ENTRY_COUNTS = {
    "every": 3,
    "dbt-model": 41,
    "dbt-seed": 29,
    "dbt-snapshot": 30,
    "dbt-test": 18,
    "python-node": 41,
    "python-csv": 34,
    "python-api": 35,
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
