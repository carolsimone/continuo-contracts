"""The ontology rules over contracts in memory, run on a small estate that has no finding."""

import copy
import dataclasses

import pytest
import yaml

from continuo_contracts import ontology
from continuo_contracts.contract import canonical_form
from continuo_contracts.ontology_check import (
    PER_SERVICE_RULES,
    RELEASE_RULES,
    RULES,
    SEVERITIES,
    Finding,
    check,
    ontology_findings,
)
from continuo_contracts.vocabulary import continuo_values

# The texts that `check` writes for a service that the ontology does not declare.
UNDECLARED_SERVICE = (
    "not declared in the ontology: none of this service's contracts is checked"
)
UNDECLARED_BINDINGS = (
    "writes bindings, but its service is not declared in the ontology: "
    "they are kept as written and not checked"
)

# `order` has its own tests.
CHECKED = tuple(rule for rule in RELEASE_RULES if rule != "order")

ONTOLOGY = {
    "principals": {
        "domains": {
            "core": {"owner_team": "core-data"},
            "finance": {"owner_team": "finance-data"},
        },
        "services": {
            "core": {"domain": "core", "kind": "dbt"},
            "finance": {"domain": "finance", "kind": "dbt"},
            "stream": {"domain": "core", "kind": "flink"},
            "app": {"domain": "core", "kind": "microservice"},
        },
        "teams": {
            "core-data": {"idp_group": "okta:core"},
            "finance-data": {"idp_group": "okta:fin"},
        },
    },
    "entities": {
        "User": {
            "owner": "core",
            "key": {"user_id": "int"},
            "properties": {
                "email": {"type": "string", "personal": True},
                "created_at": {"type": "timestamp"},
            },
            "access": [
                {
                    "properties": ["email"],
                    "readers": ["core"],
                    "purpose": "service_operation",
                }
            ],
        },
        "Transaction": {
            "owner": "core",
            "born_in": "analytics.events",
            "key": {"source": "enum[card, fx]", "transaction_id": "int"},
            "properties": {
                "user_id": {"ref": "User"},
                "amount_eur": {"type": "money"},
            },
        },
        "UserRevenue": {
            "owner": "finance",
            "key": {"user_id": "ref User"},
            "properties": {
                "revenue_eur": {
                    "type": "money",
                    "derived_from": ["Transaction.amount_eur"],
                }
            },
        },
        "FxRate": {
            "owner": "finance",
            "key": {"currency": "string"},
            "properties": {"rate_to_eur": {"type": "decimal"}},
        },
    },
}

DATA_QUALITY = {
    "type": "custom",
    "engine": "data-quality",
    "name": "drift",
    "implementation": {
        "enabled": True,
        "granularity": "day",
        "params": {
            "temporal_attribute": "user_id",
            "aggregate_function": "count",
            "drift_attribute": "revenue_eur",
            "drift_threshold": 0.02,
        },
    },
}


def ontology_of(document):
    return ontology.load(yaml.safe_dump(document, sort_keys=False))


ONT = ontology_of(ONTOLOGY)


def _property(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def column(name, binding=None, **fields):
    """A column: `binding` is None, "local", "<Thing>.<field>" or the aggregate mapping."""
    stored = {"name": name, **fields}
    if binding == "local":
        stored["customProperties"] = [_property("local", True)]
    elif isinstance(binding, str):
        stored["customProperties"] = [_property("property", binding)]
    elif binding is not None:
        stored["customProperties"] = [_property("aggregate", binding)]
    return stored


def contract(
    service,
    name,
    *,
    entity=None,
    grain=None,
    columns=(),
    reads=(),
    readers=(),
    fixed=None,
    filter=None,
    quality=None,
    physical_name=None,
):
    """A stored contract of `service`: id `analytics.<name>`, one schema object of `columns`.

    `columns` is a list of `(name, binding)`, as `column` reads them.
    """
    table = {
        "name": name,
        "physicalName": physical_name or name,
        "physicalType": "table",
        "customProperties": [
            _property(key, value)
            for key, value in (
                ("entity", entity),
                ("grain", grain),
                ("fixed", fixed),
                ("filter", filter),
                ("reads", list(reads)),
            )
            if value is not None
        ],
        "properties": [column(*pair) for pair in columns],
    }
    if quality:
        table["quality"] = quality
    stored = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": f"analytics.{name}".lower(),
        "name": name,
        "version": "0",
        "status": "active",
        "customProperties": [
            _property("service", service),
            _property("kind", "dbt-model"),
        ],
        "schema": [table],
    }
    if readers:
        stored["roles"] = [{"role": r, "access": "read"} for r in readers]
    return canonical_form(stored)


TRANSACTION_COLUMNS = [
    ("transaction_id", "Transaction.transaction_id"),
    ("source", "Transaction.source"),
    ("user_id", "Transaction.user_id"),
    ("amount_eur", "Transaction.amount_eur"),
]


def base():
    """The contracts of an estate with no error and no warning, by table name.

    `app` emits `analytics.events`, `stream` sinks it, `core` builds on the sink and `finance`
    on `core`, which grants `finance` the read of `analytics.txns`.
    """
    contracts = [
        contract(
            "app",
            "events",
            entity="Transaction",
            fixed={"source": "card"},
            columns=[
                ("id", "Transaction.transaction_id"),
                ("usr", "Transaction.user_id"),
                ("amt", "Transaction.amount_eur"),
            ],
        ),
        contract(
            "stream",
            "events_sink",
            entity="Transaction",
            reads=["analytics.events"],
            columns=TRANSACTION_COLUMNS,
        ),
        contract(
            "core",
            "seed_users",
            entity="User",
            columns=[
                ("user_id", "User.user_id"),
                ("email", "User.email"),
                ("created_at", "User.created_at"),
            ],
        ),
        contract(
            "core",
            "txns",
            entity="Transaction",
            reads=["analytics.events_sink"],
            readers=["finance"],
            columns=TRANSACTION_COLUMNS,
        ),
        contract(
            "finance",
            "revenue",
            entity="UserRevenue",
            reads=["analytics.txns"],
            columns=[
                ("user_id", "UserRevenue.user_id"),
                ("revenue_eur", "UserRevenue.revenue_eur"),
            ],
        ),
    ]
    # The personal column carries its classification, as `resolve` writes it.
    contracts[2]["schema"][0]["properties"][1]["classification"] = "personal"
    return {c["name"]: c for c in contracts}


def table(stored):
    return stored["schema"][0]


def moved_to(stored, service):
    """`stored` moved to `service`."""
    moved = copy.deepcopy(stored)
    set_custom(moved, "service", service)
    return moved


def set_custom(element, name, value):
    """Set the `continuo` property `name` of `element`, or remove it for None."""
    element["customProperties"] = [
        item
        for item in element.get("customProperties", [])
        if item["property"] != name
    ]
    if value is not None:
        element["customProperties"].append(_property(name, value))


def run(state, rules=CHECKED, document=None):
    chosen = ONT if document is None else ontology_of(document)
    return check(chosen, list(state.values()), rules=rules)


def ontology_document():
    return copy.deepcopy(ONTOLOGY)


def listing(findings):
    return "\n".join(
        f"  {f.severity} {f.rule} {f.subject}: {f.message}" for f in findings
    )


def assert_finding(findings, severity, rule, subject, contains=""):
    hits = [
        f
        for f in findings
        if (f.severity, f.rule, f.subject) == (severity, rule, subject)
        and contains in f.message
    ]
    assert hits, (
        f"no {severity} {rule} finding on {subject!r} containing {contains!r}; "
        f"got:\n{listing(findings)}"
    )


def assert_no_finding(findings, severity, rule, subject=None):
    hits = [
        f
        for f in findings
        if f.severity == severity
        and f.rule == rule
        and (subject is None or f.subject == subject)
    ]
    assert hits == [], listing(hits)


def problems(findings):
    return [f for f in findings if f.severity in ("error", "warning")]


def test_the_rules_and_severities_are_the_documented_ones():
    assert SEVERITIES == ("error", "warning", "info")
    assert RULES == (
        "ontology",
        "contract",
        "coverage",
        "access",
        "order",
        "drift",
        "quality",
        "privacy",
    )
    assert PER_SERVICE_RULES == ("contract", "coverage", "quality", "privacy")
    assert RELEASE_RULES == (
        "contract",
        "coverage",
        "quality",
        "privacy",
        "access",
        "order",
        "drift",
    )
    assert set(RELEASE_RULES) == set(RULES) - {"ontology"}


def test_a_finding_holds_the_contract_it_is_about():
    assert [field.name for field in dataclasses.fields(Finding)] == [
        "severity",
        "rule",
        "subject",
        "message",
        "contract_id",
    ]
    finding = Finding("error", "contract", "a.b", "text", "a.b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.message = "other"


def test_base_estate_has_no_errors_or_warnings():
    assert problems(run(base())) == []


def test_contracts_resolved_against_the_ontology_have_no_errors_or_warnings():
    resolved = [ontology.resolve(c, ONT) for c in base().values()]

    findings = check(ONT, resolved, rules=CHECKED)

    assert problems(findings) == []
    assert all(c["domain"] for c in resolved)


# The rules of the ontology file, over the file alone.


def ontology_problems(mutate):
    document = ontology_document()
    mutate(document)
    return ontology_findings(ontology_of(document))


def test_a_clean_ontology_has_no_finding():
    assert ontology_findings(ONT) == []


def test_owner_must_be_a_declared_domain():
    def mutate(document):
        document["entities"]["User"]["owner"] = "sales"

    assert_finding(
        ontology_problems(mutate), "error", "ontology", "User", "sales"
    )


def test_property_ref_must_name_a_thing():
    def mutate(document):
        document["entities"]["Transaction"]["properties"]["user_id"] = {
            "ref": "Customer"
        }

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "Transaction.user_id",
        "Customer",
    )


def test_key_ref_must_name_a_thing():
    def mutate(document):
        document["entities"]["UserRevenue"]["key"] = {"user_id": "ref Person"}

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "UserRevenue.user_id",
        "Person",
    )


def test_derived_from_must_name_a_real_property():
    def mutate(document):
        document["entities"]["UserRevenue"]["properties"]["revenue_eur"][
            "derived_from"
        ] = ["Transaction.amount"]

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "UserRevenue.revenue_eur",
        "Transaction.amount",
    )


def test_derived_from_must_name_a_thing():
    def mutate(document):
        document["entities"]["UserRevenue"]["properties"]["revenue_eur"][
            "derived_from"
        ] = ["Payment.amount"]

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "UserRevenue.revenue_eur",
        "Payment is not a thing in the ontology",
    )


def test_personal_property_without_access_rule_is_an_error():
    def mutate(document):
        del document["entities"]["User"]["access"]

    assert_finding(
        ontology_problems(mutate), "error", "ontology", "User.email", "personal"
    )


def test_access_rule_must_name_real_properties():
    def mutate(document):
        document["entities"]["User"]["access"][0]["properties"] = [
            "email",
            "phone",
        ]

    assert_finding(
        ontology_problems(mutate), "error", "ontology", "User", "phone"
    )


def test_access_rule_readers_must_be_declared_domains():
    def mutate(document):
        document["entities"]["User"]["access"][0]["readers"] = ["sales"]

    assert_finding(
        ontology_problems(mutate), "error", "ontology", "User", "sales"
    )


def test_service_domain_must_be_declared():
    def mutate(document):
        document["principals"]["services"]["app"]["domain"] = "ops"

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "principals.services.app",
        "ops",
    )


def test_a_service_with_no_domain_is_an_error():
    def mutate(document):
        del document["principals"]["services"]["app"]["domain"]

    assert_finding(
        ontology_problems(mutate),
        "error",
        "ontology",
        "principals.services.app",
        "None",
    )


def test_an_ontology_with_no_principals_and_no_entities_has_no_finding():
    assert ontology_findings(ontology_of({})) == []


def test_ontology_findings_name_no_contract():
    def mutate(document):
        document["entities"]["User"]["owner"] = "sales"
        document["principals"]["services"]["app"]["domain"] = "ops"

    found = ontology_problems(mutate)

    assert len(found) == 2
    assert all(f.contract_id is None for f in found)


# The contract rule.


def test_domain_must_match_the_services_domain():
    state = base()
    state["txns"]["domain"] = "finance"

    assert_finding(run(state), "error", "contract", "analytics.txns", "finance")


def test_a_domain_that_is_the_services_domain_passes():
    state = base()
    state["txns"]["domain"] = "core"

    assert problems(run(state)) == []


def test_entity_must_be_a_declared_thing():
    state = base()
    set_custom(table(state["txns"]), "entity", "Payment")

    assert_finding(run(state), "error", "contract", "analytics.txns", "Payment")


def test_copy_with_both_entity_and_grain_is_an_error():
    state = base()
    set_custom(table(state["txns"]), "grain", ["day"])

    assert_finding(run(state), "error", "contract", "analytics.txns", "grain")


def test_copy_with_neither_entity_nor_grain_is_an_error():
    state = base()
    set_custom(table(state["revenue"]), "entity", None)

    assert_finding(run(state), "error", "contract", "analytics.revenue", "grain")


def test_fixed_must_name_a_key_part():
    state = base()
    set_custom(
        table(state["events"]), "fixed", {"source": "card", "amount_eur": 1}
    )

    assert_finding(
        run(state), "error", "contract", "analytics.events", "amount_eur"
    )


def test_fixed_value_must_be_allowed_by_the_key_enum():
    state = base()
    set_custom(table(state["events"]), "fixed", {"source": "atm"})

    assert_finding(run(state), "error", "contract", "analytics.events", "atm")


def test_fixed_value_of_a_key_part_that_is_not_an_enum_passes():
    state = base()
    set_custom(table(state["seed_users"]), "fixed", {"user_id": 7})

    assert_no_finding(run(state), "error", "contract")


def test_filter_must_name_a_field_of_the_thing():
    state = base()
    set_custom(table(state["txns"]), "filter", {"status": "open"})

    assert_finding(run(state), "error", "contract", "analytics.txns", "status")


def test_filter_on_a_property_passes():
    state = base()
    set_custom(table(state["txns"]), "filter", {"user_id": 7})

    assert_no_finding(run(state), "error", "contract")


def test_column_property_must_exist():
    state = base()
    table(state["events"])["properties"][2] = column("amt", "Transaction.amount")

    assert_finding(
        run(state), "error", "contract", "analytics.events", "Transaction.amount"
    )


def test_column_on_an_unreferenced_thing_is_a_warning():
    state = base()
    table(state["revenue"])["properties"].append(
        column("rate", "FxRate.rate_to_eur")
    )

    assert_finding(
        run(state), "warning", "contract", "analytics.revenue", "FxRate"
    )


def test_column_on_a_referenced_thing_passes():
    state = base()
    table(state["revenue"])["properties"].append(
        column("acquired_at", "User.created_at")
    )

    assert problems(run(state)) == []


def test_a_column_of_a_contract_with_no_thing_is_not_checked_for_its_join():
    state = base()
    set_custom(table(state["revenue"]), "entity", "Payment")
    table(state["revenue"])["properties"].append(
        column("rate", "FxRate.rate_to_eur")
    )

    assert_no_finding(run(state), "warning", "contract")


def test_a_reference_to_a_missing_thing_does_not_break_the_contract_rule():
    document = ontology_document()
    document["entities"]["Transaction"]["properties"]["user_id"] = {
        "ref": "Customer"
    }

    findings = run(base(), document=document)

    assert problems([f for f in findings if f.rule == "contract"]) == []


def test_aggregate_from_must_name_a_real_property():
    state = base()
    state["daily"] = contract(
        "finance",
        "daily",
        grain=["day"],
        reads=["analytics.txns"],
        columns=[
            ("day", {"period": "day", "from": "Transaction.created"}),
        ],
    )

    assert_finding(
        run(state), "error", "contract", "analytics.daily", "Transaction.created"
    )


def test_aggregate_from_a_thing_must_name_a_declared_thing():
    state = base()
    state["daily"] = contract(
        "finance",
        "daily",
        grain=["day"],
        reads=["analytics.txns"],
        columns=[("n", {"from": "Payment", "agg": "count"})],
    )

    assert_finding(run(state), "error", "contract", "analytics.daily", "Payment")


def test_an_aggregate_of_real_properties_passes():
    state = base()
    state["daily"] = contract(
        "finance",
        "daily",
        grain=["day"],
        reads=["analytics.txns"],
        columns=[
            ("day", {"period": "day"}),
            ("total", {"from": "Transaction.amount_eur", "agg": "sum"}),
            ("n", {"from": "Transaction", "agg": "count"}),
        ],
    )

    assert problems(run(state)) == []


def test_readers_must_be_declared_domains():
    state = base()
    state["txns"]["roles"] = [{"role": "sales", "access": "read"}]

    assert_finding(run(state), "error", "contract", "analytics.txns", "sales")


@pytest.mark.parametrize(
    ("roles", "granted"),
    [
        ([{"role": "finance", "access": "read"}], True),
        ([{"role": "finance", "access": "select"}], False),
        ([{"role": "finance"}], False),
    ],
)
def test_readers_are_the_roles_with_access_read(roles, granted):
    state = base()
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.seed_users"],
    )
    state["seed_users"]["roles"] = roles

    findings = run(state)

    refused = [
        f for f in findings if f.rule == "access" and f.severity == "error"
    ]
    assert (refused == []) is granted, listing(findings)


def test_a_role_that_is_not_a_reader_is_not_checked_as_a_domain():
    state = base()
    state["seed_users"]["roles"] = [{"role": "sales", "access": "select"}]

    assert_no_finding(run(state), "error", "contract")


# The coverage rule.


def test_column_with_no_binding_is_a_warning():
    state = base()
    table(state["seed_users"])["properties"].append({"name": "phone"})

    assert_finding(
        run(state), "warning", "coverage", "analytics.seed_users", "phone"
    )


def test_local_columns_are_explained():
    state = base()
    table(state["seed_users"])["properties"].append(column("_loaded_at", "local"))

    assert problems(run(state)) == []


def test_copy_missing_a_key_part_is_an_error():
    state = base()
    table(state["seed_users"])["properties"].pop(0)

    assert_finding(
        run(state), "error", "coverage", "analytics.seed_users", "user_id"
    )


def test_fixed_key_part_counts_as_carried():
    state = base()
    set_custom(table(state["events"]), "fixed", None)

    assert_finding(run(state), "error", "coverage", "analytics.events", "source")


def test_filter_on_a_key_part_counts_as_carried():
    state = base()
    set_custom(table(state["events"]), "fixed", None)
    set_custom(table(state["events"]), "filter", {"source": "card"})

    assert_no_finding(run(state), "error", "coverage")


def test_column_with_two_bindings_is_an_error():
    state = base()
    email = table(state["seed_users"])["properties"][1]
    email["customProperties"].append(_property("local", True))

    assert_finding(
        run(state), "error", "coverage", "analytics.seed_users", "email"
    )


def test_a_column_with_a_local_and_an_aggregate_binding_names_both():
    state = base()
    odd = column("odd", "local")
    odd["customProperties"].append(
        _property("aggregate", {"from": "User.created_at", "agg": "max"})
    )
    table(state["seed_users"])["properties"].append(odd)

    assert_finding(
        run(state),
        "error",
        "coverage",
        "analytics.seed_users",
        "column odd carries more than one binding: ['local', 'aggregate']",
    )


# The access rule.


def test_reading_another_domains_copy_without_a_grant_is_an_error():
    state = base()
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.seed_users"],
    )

    assert_finding(
        run(state),
        "error",
        "access",
        "analytics.revenue",
        "finance reads analytics.seed_users without a grant: add finance to "
        "analytics.seed_users's readers in core's contract",
    )


def test_table_grant_opens_the_copy():
    state = base()
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.seed_users"],
    )
    state["seed_users"]["roles"] = [{"role": "finance", "access": "read"}]

    assert_no_finding(run(state), "error", "access")


def test_owner_domain_reads_every_copy_of_its_things():
    document = ontology_document()
    document["principals"]["services"]["stream"]["domain"] = "finance"

    findings = run(base(), document=document)

    assert_no_finding(findings, "error", "access", "analytics.txns")


def test_a_domain_reads_the_copies_of_its_own_services():
    state = base()
    set_custom(table(state["txns"]), "reads", ["analytics.seed_users"])

    assert_no_finding(run(state), "error", "access")


def test_read_of_an_unbound_table_is_a_warning():
    state = base()
    set_custom(
        table(state["txns"]),
        "reads",
        ["analytics.events_sink", "analytics.mystery"],
    )

    assert_finding(
        run(state),
        "warning",
        "access",
        "analytics.txns",
        "reads analytics.mystery, which no checked contract binds: "
        "its owner and grants are unknown",
    )


def test_a_read_of_an_unbound_table_is_a_warning_for_every_kind_of_contract():
    for kind in ("python-node", "dbt-seed", "dbt-snapshot"):
        state = base()
        set_custom(state["txns"], "kind", kind)
        set_custom(table(state["txns"]), "reads", ["analytics.mystery"])

        findings = run(state)

        assert_finding(
            findings, "warning", "access", "analytics.txns", "analytics.mystery"
        )
        assert [f.contract_id for f in findings if f.rule == "access"] == [
            "analytics.txns"
        ]


def test_a_read_of_a_relation_only_an_undeclared_service_holds_is_a_warning():
    state = base()
    state["ledger"] = contract("billing", "ledger", columns=[("id", None)])
    set_custom(table(state["txns"]), "reads", ["analytics.ledger"])

    assert_finding(
        run(state), "warning", "access", "analytics.txns", "analytics.ledger"
    )


def test_a_read_reaches_the_copy_by_its_physical_name():
    state = base()
    state["orders_v2"] = contract(
        "core",
        "orders_v2",
        entity="Transaction",
        physical_name="orders_v2_alias",
        columns=TRANSACTION_COLUMNS,
    )
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.orders_v2_alias"],
    )

    findings = run(state)

    # The alias is a copy: finance holds no grant on it, so the read is an error, not an
    # unbound table, and it names the contract of the reader.
    [error] = [
        f for f in findings if f.rule == "access" and f.severity == "error"
    ]
    assert error.contract_id == "analytics.revenue"
    assert "analytics.orders_v2_alias without a grant" in error.message
    assert_no_finding(findings, "warning", "access")

    # The contract's own name is no relation of the warehouse.
    set_custom(table(state["revenue"]), "reads", ["analytics.orders_v2"])
    assert_finding(
        run(state),
        "warning",
        "access",
        "analytics.revenue",
        "analytics.orders_v2",
    )


def test_a_contract_with_upper_case_letters_in_its_name_is_reached_by_its_relation():
    state = base()
    # The id is in lower case and the name keeps its case, as a dbt node writes them. No
    # grant is given to finance, so the read is an error, not an unbound table.
    state["txns"] = contract(
        "core",
        "Txns",
        entity="Transaction",
        reads=["analytics.events_sink"],
        columns=TRANSACTION_COLUMNS,
    )
    assert state["txns"]["id"] == "analytics.txns"
    assert table(state["txns"])["name"] == "Txns"

    findings = run(state)

    [error] = [
        f for f in findings if f.rule == "access" and f.severity == "error"
    ]
    assert error.contract_id == "analytics.revenue"
    assert "analytics.txns without a grant" in error.message
    assert_no_finding(findings, "warning", "access")


def test_a_read_is_matched_to_its_copy_without_regard_to_case():
    state = base()
    set_custom(
        table(state["revenue"]),
        "reads",
        ["Analytics.Txns", "Analytics.Seed_Users"],
    )

    findings = run(state)

    # `analytics.txns` grants finance and `analytics.seed_users` does not, so the second read
    # is an error that shows the read as written, and neither is an unbound table.
    [error] = [
        f for f in findings if f.rule == "access" and f.severity == "error"
    ]
    assert error.contract_id == "analytics.revenue"
    assert "Analytics.Seed_Users without a grant" in error.message
    assert_no_finding(findings, "warning", "access")


def test_a_finding_about_an_aliased_contract_names_the_contract_id():
    state = base()
    state["orders_v2"] = contract(
        "core",
        "orders_v2",
        entity="Transaction",
        physical_name="orders_v2_alias",
        columns=[*TRANSACTION_COLUMNS, ("phone", None)],
    )

    findings = run(state)

    [warned] = [f for f in problems(findings) if f.rule == "coverage"]
    assert warned.subject == "analytics.orders_v2"
    assert warned.contract_id == "analytics.orders_v2"


def test_a_contract_with_no_physical_name_is_reached_by_its_name():
    state = base()
    del table(state["txns"])["physicalName"]

    assert problems(run(state)) == []

    set_custom(table(state["txns"]), "reads", ["analytics.nothing"])
    assert_finding(
        run(state), "warning", "access", "analytics.txns", "analytics.nothing"
    )


def test_a_relation_two_contracts_hold_belongs_to_the_first_by_id():
    state = base()
    state["a"] = contract(
        "core",
        "a",
        entity="Transaction",
        physical_name="shared",
        columns=TRANSACTION_COLUMNS,
    )
    state["b"] = contract(
        "finance",
        "b",
        entity="Transaction",
        physical_name="shared",
        columns=TRANSACTION_COLUMNS,
    )
    set_custom(table(state["revenue"]), "reads", ["analytics.shared"])

    forward = run(state)
    backward = check(ONT, list(reversed(list(state.values()))), rules=CHECKED)

    # `analytics.a` is core's, which holds no grant for finance.
    assert_finding(
        forward, "error", "access", "analytics.revenue", "without a grant"
    )
    assert forward == backward


# The quality rule.


def test_params_naming_real_columns_pass():
    state = base()
    table(state["revenue"])["quality"] = [DATA_QUALITY]

    assert problems(run(state)) == []


def test_param_naming_a_missing_column_is_an_error():
    state = base()
    bad = {
        **DATA_QUALITY,
        "implementation": {
            **DATA_QUALITY["implementation"],
            "params": {
                **DATA_QUALITY["implementation"]["params"],
                "drift_attribute": "case_id",
            },
        },
    }
    table(state["revenue"])["quality"] = [bad]

    assert_finding(run(state), "error", "quality", "analytics.revenue", "case_id")


def test_string_quality_implementation_is_a_quality_error():
    state = base()
    table(state["revenue"])["quality"] = [
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "drift",
            "implementation": "check.yml",
        }
    ]

    assert_finding(
        run(state), "error", "quality", "analytics.revenue", "implementation"
    )


def test_non_mapping_quality_params_is_a_quality_error():
    state = base()
    table(state["revenue"])["quality"] = [
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "drift",
            "implementation": {"enabled": True, "params": ["user_id"]},
        }
    ]

    assert_finding(run(state), "error", "quality", "analytics.revenue", "params")


def test_a_test_of_another_engine_is_not_checked():
    state = base()
    table(state["revenue"])["quality"] = [
        {
            "type": "custom",
            "engine": "dbt",
            "name": "is_positive",
            "implementation": "is_positive",
        }
    ]

    assert_no_finding(run(state), "error", "quality")


def test_a_data_quality_test_with_no_params_passes():
    state = base()
    table(state["revenue"])["quality"] = [
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "rows",
            "implementation": {"enabled": True},
        }
    ]

    assert_no_finding(run(state), "error", "quality")


# The privacy rule.


def test_personal_column_without_classification_is_an_error():
    state = base()
    del table(state["seed_users"])["properties"][1]["classification"]

    assert_finding(
        run(state), "error", "privacy", "analytics.seed_users", "email"
    )


def test_personal_classification_on_a_non_personal_column_is_a_warning():
    state = base()
    table(state["seed_users"])["properties"][2]["classification"] = "personal"

    assert_finding(
        run(state), "warning", "privacy", "analytics.seed_users", "created_at"
    )


# The drift rule.


def test_property_no_copy_holds_is_a_warning():
    document = ontology_document()
    document["entities"]["Transaction"]["properties"]["status"] = {
        "type": "string"
    }

    findings = run(base(), document=document)

    assert_finding(
        findings,
        "warning",
        "drift",
        "Transaction.status",
        "no copy holds it (checked ['analytics.events', 'analytics.events_sink', "
        "'analytics.txns'])",
    )


def test_thing_with_no_copy_is_info():
    assert_finding(run(base()), "info", "drift", "FxRate")


def test_a_thing_whose_domain_has_no_checked_service_is_not_reported():
    state = {name: c for name, c in base().items() if name != "revenue"}

    findings = run(state)

    assert_no_finding(findings, "info", "drift", "FxRate")
    assert_no_finding(findings, "info", "drift", "UserRevenue")


def test_an_aggregate_is_not_a_copy_of_a_thing():
    state = base()
    state["daily"] = contract(
        "finance",
        "daily",
        grain=["day"],
        reads=["analytics.txns"],
        columns=[("day", {"period": "day"})],
    )

    findings = run(state)

    assert problems(findings) == []
    assert [f.subject for f in findings if f.rule == "drift"] == ["FxRate"]


def test_drift_findings_name_no_contract():
    document = ontology_document()
    document["entities"]["Transaction"]["properties"]["status"] = {
        "type": "string"
    }

    found = [f for f in run(base(), document=document) if f.rule == "drift"]

    assert {f.severity for f in found} == {"warning", "info"}
    assert all(f.contract_id is None for f in found)


# The scope: only the contracts of declared services are checked.


def about_service(findings, state, service):
    """The findings about `service` itself or about one of its contracts."""
    ids = {
        c["id"]
        for c in state.values()
        if continuo_values(c["customProperties"])["service"] == service
    }
    return [f for f in findings if f.subject == service or f.contract_id in ids]


def test_an_undeclared_service_is_one_information_finding():
    state = base()
    state["invoices"] = contract("billing", "invoices", columns=[("id", None)])
    state["ledger"] = contract("billing", "ledger", columns=[("id", None)])

    findings = run(state)

    assert about_service(findings, state, "billing") == [
        Finding("info", "contract", "billing", UNDECLARED_SERVICE, None)
    ]
    assert problems(findings) == []


def test_a_binding_in_an_undeclared_service_is_a_warning_and_is_not_checked():
    state = base()
    # An entity that is no thing and a column bound to no property are errors in a declared
    # service.
    invoices = contract(
        "billing",
        "invoices",
        entity="Invoice",
        columns=[("total", "Invoice.total"), ("note", None)],
    )
    state["invoices"] = invoices
    state["ledger"] = contract("billing", "ledger", columns=[("id", None)])
    state["audit"] = contract(
        "billing", "audit", grain=["day"], columns=[("day", "local")]
    )

    findings = run(state)

    elsewhere = run({**base(), "invoices": moved_to(invoices, "core")})
    assert {f.rule for f in elsewhere if f.severity == "error"} == {"contract"}
    assert [f for f in findings if f.severity == "error"] == []
    assert sorted(
        (f.severity, f.rule, f.subject, f.message, f.contract_id)
        for f in about_service(findings, state, "billing")
    ) == [
        ("info", "contract", "billing", UNDECLARED_SERVICE, None),
        (
            "warning",
            "contract",
            "analytics.audit",
            UNDECLARED_BINDINGS,
            "analytics.audit",
        ),
        (
            "warning",
            "contract",
            "analytics.invoices",
            UNDECLARED_BINDINGS,
            "analytics.invoices",
        ),
    ]


def test_the_scope_findings_come_with_any_rules():
    state = base()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")

    findings = run(state, rules=("privacy",))

    assert [(f.severity, f.rule) for f in findings] == [
        ("warning", "contract"),
        ("info", "contract"),
    ]


def test_the_texts_of_an_undeclared_service_are_exact():
    state = base()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")

    texts = {f.message for f in about_service(run(state), state, "billing")}

    assert texts == {UNDECLARED_SERVICE, UNDECLARED_BINDINGS}


# Every error names a contract; the rules need no other service; progress.


def broken():
    """An estate with an error of each rule that has a pass, and one of each severity."""
    state = base()
    set_custom(table(state["events"]), "fixed", {"source": "atm"})  # contract
    table(state["seed_users"])["properties"].pop(0)  # coverage
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.seed_users", "analytics.mystery"],
    )  # access
    table(state["revenue"])["quality"] = [
        {
            "type": "custom",
            "engine": "data-quality",
            "name": "drift",
            "implementation": {"params": {"x_attribute": "nope"}},
        }
    ]  # quality
    del table(state["seed_users"])["properties"][0]["classification"]  # privacy
    return state


def test_every_error_names_a_contract():
    state = broken()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")
    ids = {c["id"] for c in state.values()}

    findings = run(state)

    errors = [f for f in findings if f.severity == "error"]
    assert {f.rule for f in errors} == {
        "contract",
        "coverage",
        "access",
        "quality",
        "privacy",
    }, listing(findings)
    for finding in errors:
        assert finding.contract_id in ids, finding
        assert finding.subject == finding.contract_id


def test_a_broken_ontology_has_errors_that_name_no_contract():
    def mutate(document):
        document["entities"]["User"]["owner"] = "sales"
        del document["entities"]["User"]["access"]

    errors = [f for f in ontology_problems(mutate) if f.severity == "error"]

    assert len(errors) == 2
    assert {f.contract_id for f in errors} == {None}


def test_per_service_rules_need_no_other_service():
    state = broken()
    state["daily"] = contract(
        "finance",
        "daily",
        grain=["day"],
        reads=["analytics.nowhere"],
        columns=[("day", {"period": "day", "from": "Transaction.created"})],
    )
    contracts = list(state.values())

    together = check(ONT, contracts, rules=PER_SERVICE_RULES)

    assert {f.rule for f in together} >= {"contract", "coverage", "quality"}
    for service in ("app", "stream", "core", "finance"):
        mine = [
            c
            for c in contracts
            if continuo_values(c["customProperties"])["service"] == service
        ]
        ids = {c["id"] for c in mine}

        alone = check(ONT, mine, rules=PER_SERVICE_RULES)

        assert alone == [f for f in together if f.contract_id in ids], service
    assert {f.contract_id for f in together} == {
        "analytics.events",
        "analytics.seed_users",
        "analytics.revenue",
        "analytics.daily",
    }


def progress_calls(contracts, rules):
    """How many times `check` calls `progress` for `rules` over `contracts`."""
    calls = []
    check(ONT, contracts, rules=rules, progress=lambda: calls.append(1))
    return len(calls)


def test_progress_is_called_after_each_contract_of_each_rule():
    state = base()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")
    contracts = list(state.values())
    declared = len(contracts) - 1

    assert {rule: progress_calls(contracts, (rule,)) for rule in CHECKED} == (
        dict.fromkeys(CHECKED, declared)
    )
    assert progress_calls(contracts, CHECKED) == declared * len(CHECKED)


def test_progress_is_called_once_per_contract_when_a_rule_is_named_twice():
    contracts = list(base().values())

    assert progress_calls(contracts, ("contract", "contract")) == len(contracts)


def test_progress_is_not_called_when_no_rule_runs():
    contracts = list(base().values())

    assert progress_calls(contracts, ()) == 0
    assert check(ONT, contracts, rules=()) == []


@pytest.mark.parametrize("rule", ["order", "ontology", "nonsense"])
def test_check_refuses_a_rule_it_has_no_pass_for(rule):
    with pytest.raises(ValueError, match=rule):
        check(ONT, list(base().values()), rules=("contract", rule))


def test_check_raises_before_it_runs_any_rule():
    calls = []

    with pytest.raises(ValueError, match="nonsense"):
        check(
            ONT,
            list(base().values()),
            rules=("contract", "nonsense"),
            progress=lambda: calls.append(1),
        )

    assert calls == []


def test_findings_are_sorted_by_severity_rule_subject_and_message():
    state = broken()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")

    findings = run(state)

    assert {f.severity for f in findings} == set(SEVERITIES)
    assert findings == sorted(
        findings,
        key=lambda f: (
            SEVERITIES.index(f.severity),
            f.rule,
            f.subject,
            f.message,
        ),
    )


def test_findings_do_not_depend_on_the_order_of_the_contracts():
    state = broken()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")
    contracts = list(state.values())

    forward = check(ONT, contracts, rules=CHECKED)

    assert forward
    assert check(ONT, list(reversed(contracts)), rules=CHECKED) == forward
    assert check(ONT, contracts[2:] + contracts[:2], rules=CHECKED) == forward


def test_check_does_not_change_the_contracts():
    state = broken()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")
    before = copy.deepcopy(state)

    run(state)

    assert state == before
