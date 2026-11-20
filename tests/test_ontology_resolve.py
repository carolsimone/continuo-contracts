"""Resolving a stored contract against an ontology: what `resolve` fills in and what it leaves."""

import copy
import json

from pathlib import Path

import pytest

from continuo_contracts import ontology, yaml_strict
from continuo_contracts.contract import (
    canonical_form,
    contract_hash,
    normalise_python,
)
from continuo_contracts.validate import (
    schema_issues,
    validate_dbt_contract,
    validate_python_contract,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEMO = ontology.load((FIXTURES / "ontology" / "demo.yaml").read_text())
PY_DEMO = (
    FIXTURES / "demo" / "service-py" / "contracts" / "py_daily_kpis.odcs.yaml"
)
GOLDEN = FIXTURES / "dbt" / "golden" / "cases" / "analytics.generic_tests.json"


def _p(name, value):
    return {"vendor": "continuo", "property": name, "value": value}


def _column(name, *bindings, **fields):
    column = {"name": name, **fields}
    if bindings:
        column["customProperties"] = list(bindings)
    return column


def _contract(columns, *, entity=None, service="core", schema=(), **root):
    """A stored contract of `service` with one schema object that holds `columns`."""
    table = {"name": "t", "properties": list(columns)}
    custom = [_p(name, value) for name, value in schema]
    if entity is not None:
        custom.append(_p("entity", entity))
    if custom:
        table["customProperties"] = custom
    properties = [_p("kind", "dbt-model")]
    if service is not None:
        properties.append(_p("service", service))
    return canonical_form(
        {"customProperties": properties, "schema": [table], **root}
    )


def _resolve(stored, ont=DEMO):
    return ontology.resolve(stored, ont)


def _columns(resolved):
    return {
        column["name"]: column for column in resolved["schema"][0]["properties"]
    }


def _link(url):
    return {"type": "ontology", "url": url}


def _without_custom(column):
    return {
        key: value for key, value in column.items() if key != "customProperties"
    }


def _bindings(column):
    return {
        item["property"]: item["value"] for item in column["customProperties"]
    }


# The five fills.


def test_resolve_fills_the_domain_the_links_the_types_and_the_personal_mark():
    stored = _contract(
        [
            _column("email", _p("property", "User.email")),
            _column("birth_year", _p("property", "User.birth_year")),
            _column("created_at", _p("property", "User.created_at")),
            # A property that refs a thing, and a key part of another thing that refs one.
            _column("buyer", _p("property", "Transaction.user_id")),
            _column("revenue_owner", _p("property", "UserRevenue.user_id")),
            _column("amount", _p("property", "Transaction.amount_eur")),
            _column("source", _p("property", "Transaction.source")),
            _column("rate", _p("property", "FxRate.rate_to_eur")),
            _column("rate_day", _p("property", "FxRate.rate_date")),
        ],
        entity="User",
    )

    resolved = _resolve(stored)

    assert resolved["domain"] == "core"
    assert resolved["schema"][0]["authoritativeDefinitions"] == [
        _link("continuo-ontology:User")
    ]
    columns = _columns(resolved)
    assert {
        name: _without_custom(column) for name, column in columns.items()
    } == {
        "email": {
            "name": "email",
            "logicalType": "string",
            "classification": "personal",
            "authoritativeDefinitions": [_link("continuo-ontology:User.email")],
        },
        "birth_year": {
            "name": "birth_year",
            "logicalType": "integer",
            "classification": "personal",
            "authoritativeDefinitions": [
                _link("continuo-ontology:User.birth_year")
            ],
        },
        "created_at": {
            "name": "created_at",
            "logicalType": "timestamp",
            "authoritativeDefinitions": [
                _link("continuo-ontology:User.created_at")
            ],
        },
        "buyer": {
            "name": "buyer",
            "logicalType": "integer",
            "authoritativeDefinitions": [
                _link("continuo-ontology:Transaction.user_id")
            ],
        },
        "revenue_owner": {
            "name": "revenue_owner",
            "logicalType": "integer",
            "authoritativeDefinitions": [
                _link("continuo-ontology:UserRevenue.user_id")
            ],
        },
        "amount": {
            "name": "amount",
            "logicalType": "number",
            "authoritativeDefinitions": [
                _link("continuo-ontology:Transaction.amount_eur")
            ],
        },
        "source": {
            "name": "source",
            "logicalType": "string",
            "authoritativeDefinitions": [
                _link("continuo-ontology:Transaction.source")
            ],
        },
        "rate": {
            "name": "rate",
            "logicalType": "number",
            "authoritativeDefinitions": [
                _link("continuo-ontology:FxRate.rate_to_eur")
            ],
        },
        "rate_day": {
            "name": "rate_day",
            "logicalType": "date",
            "authoritativeDefinitions": [
                _link("continuo-ontology:FxRate.rate_date")
            ],
        },
    }


def test_the_domain_is_the_domain_of_the_service():
    assert (
        _resolve(_contract([_column("a")], service="finance"))["domain"]
        == "finance"
    )
    assert (
        _resolve(_contract([_column("a")], service="service-py"))["domain"]
        == "analytics"
    )


def test_a_service_with_no_domain_gets_no_domain():
    ont = ontology.load("principals: {services: {core: {kind: dbt}}}")

    resolved = _resolve(_contract([_column("a")]), ont)

    assert "domain" not in resolved
    assert resolved == _contract([_column("a")])


def test_the_schema_object_links_to_the_thing_its_entity_names():
    resolved = _resolve(_contract([_column("a")], entity="Transaction"))

    assert resolved["schema"][0]["authoritativeDefinitions"] == [
        _link("continuo-ontology:Transaction")
    ]


def test_a_binding_item_is_kept_as_written():
    stored = _contract(
        [_column("email", _p("property", "User.email"), _p("note", 1))],
        entity="User",
        schema=[("grain", ["day"]), ("fixed", {"source": "card"})],
    )

    resolved = _resolve(stored)

    assert _bindings(_columns(resolved)["email"]) == {
        "property": "User.email",
        "note": 1,
    }
    custom = {
        item["property"]: item["value"]
        for item in resolved["schema"][0]["customProperties"]
    }
    assert custom == {
        "entity": "User",
        "grain": ["day"],
        "fixed": {"source": "card"},
    }


# The implicit binding.


def test_a_column_named_like_a_property_is_bound_to_it():
    stored = _contract(
        [
            _column("name"),
            _column("birth_year"),
            _column("created_at"),
            _column("not_a_field"),
        ],
        entity="User",
    )

    columns = _columns(_resolve(stored))

    assert _bindings(columns["name"]) == {"property": "User.name"}
    assert columns["name"]["classification"] == "personal"
    assert columns["name"]["logicalType"] == "string"
    assert columns["name"]["authoritativeDefinitions"] == [
        _link("continuo-ontology:User.name")
    ]
    assert _bindings(columns["birth_year"]) == {"property": "User.birth_year"}
    assert columns["created_at"]["logicalType"] == "timestamp"
    assert columns["not_a_field"] == {"name": "not_a_field"}


def test_a_column_named_like_a_key_part_is_bound_to_it():
    columns = _columns(_resolve(_contract([_column("user_id")], entity="User")))

    assert _bindings(columns["user_id"]) == {"property": "User.user_id"}
    assert columns["user_id"]["logicalType"] == "integer"


@pytest.mark.parametrize(
    "binding",
    [
        _p("property", "User.email"),
        _p("local", True),
        _p("aggregate", {"from": "User.email", "agg": "count"}),
    ],
    ids=["property", "local", "aggregate"],
)
def test_a_column_with_a_binding_is_not_bound_by_its_name(binding):
    columns = _columns(
        _resolve(_contract([_column("name", binding)], entity="User"))
    )

    assert "User.name" not in [
        item["value"] for item in columns["name"]["customProperties"]
    ]
    assert not any(
        link["url"] == "continuo-ontology:User.name"
        for link in columns["name"].get("authoritativeDefinitions", [])
    )


def test_a_column_is_bound_by_its_name_only_when_the_contract_has_a_thing():
    stored = _contract([_column("name")], entity="Nothing")
    assert _columns(_resolve(stored))["name"] == {"name": "name"}

    stored = _contract([_column("name")])
    assert _columns(_resolve(stored))["name"] == {"name": "name"}


def test_a_key_part_is_looked_up_before_a_property_of_the_same_name():
    ont = ontology.load(
        """
principals: {services: {core: {domain: core}}}
entities:
  Thing:
    key: {id: int}
    properties:
      id: {type: string, personal: true}
"""
    )

    columns = _columns(_resolve(_contract([_column("id")], entity="Thing"), ont))

    assert columns["id"]["logicalType"] == "integer"
    assert "classification" not in columns["id"]


# The key.


def test_the_ontology_key_fills_a_primary_key_only_when_none_is_declared():
    stored = _contract(
        [
            _column("transaction_id"),
            _column("source"),
            _column("amount_eur"),
        ],
        entity="Transaction",
    )

    columns = _columns(_resolve(stored))

    # The key is `{source, transaction_id}`: the position is the part's place in that mapping.
    assert columns["source"]["primaryKey"] is True
    assert columns["source"]["primaryKeyPosition"] == 1
    assert columns["transaction_id"]["primaryKey"] is True
    assert columns["transaction_id"]["primaryKeyPosition"] == 2
    assert "primaryKey" not in columns["amount_eur"]
    assert "primaryKeyPosition" not in columns["amount_eur"]

    declared = _contract(
        [
            _column("transaction_id"),
            _column("source"),
            _column("amount_eur", primaryKey=True),
        ],
        entity="Transaction",
    )
    columns = _columns(_resolve(declared))

    assert columns["amount_eur"]["primaryKey"] is True
    for name in ("source", "transaction_id"):
        assert "primaryKey" not in columns[name]
        assert "primaryKeyPosition" not in columns[name]
        # They are still bound and typed.
        assert "logicalType" in columns[name]


def test_the_position_counts_the_key_in_file_order_from_one():
    ont = ontology.load(
        """
principals: {services: {core: {domain: core}}}
entities:
  Thing:
    key: {zeta: int, alpha: int, mid: string}
"""
    )
    stored = _contract(
        [_column("mid"), _column("alpha"), _column("zeta")], entity="Thing"
    )

    columns = _columns(_resolve(stored, ont))

    assert [
        columns[name]["primaryKeyPosition"] for name in ("zeta", "alpha", "mid")
    ] == [
        1,
        2,
        3,
    ]


def test_a_part_alone_is_a_primary_key_with_no_position():
    stored = _contract([_column("transaction_id")], entity="Transaction")

    column = _columns(_resolve(stored))["transaction_id"]

    assert column["primaryKey"] is True
    assert "primaryKeyPosition" not in column


def test_a_key_of_one_part_is_a_primary_key_with_no_position():
    column = _columns(_resolve(_contract([_column("user_id")], entity="User")))[
        "user_id"
    ]

    assert column["primaryKey"] is True
    assert "primaryKeyPosition" not in column


def test_a_lone_ontology_key_and_a_declared_lone_key_resolve_to_the_same_form():
    filled = _resolve(
        _contract([_column("user_id"), _column("name")], entity="User")
    )
    declared = _resolve(
        _contract(
            [
                _column("user_id", primaryKey=True, primaryKeyPosition=1),
                _column("name"),
            ],
            entity="User",
        )
    )

    assert filled == declared
    assert contract_hash(filled) == contract_hash(declared)
    assert "primaryKeyPosition" not in _columns(filled)["user_id"]
    assert _resolve(filled) == filled


def test_a_key_of_two_columns_keeps_the_position_of_each_part():
    stored = _contract(
        [_column("transaction_id"), _column("source")], entity="Transaction"
    )

    columns = _columns(_resolve(stored))

    assert [
        (columns[name]["primaryKey"], columns[name]["primaryKeyPosition"])
        for name in ("source", "transaction_id")
    ] == [(True, 1), (True, 2)]


def test_the_key_fills_only_for_a_key_part_of_the_entity_of_the_contract():
    stored = _contract(
        [
            # A key part of another thing.
            _column("user_id", _p("property", "User.user_id")),
            # A property of the entity, not a key part.
            _column("amount_eur"),
        ],
        entity="Transaction",
    )

    columns = _columns(_resolve(stored))

    assert "primaryKey" not in columns["user_id"]
    assert "primaryKey" not in columns["amount_eur"]
    # A contract with no entity has no key to fill.
    stored = _contract([_column("k", _p("property", "User.user_id"))])
    assert "primaryKey" not in _columns(_resolve(stored))["k"]


def test_the_key_is_decided_for_each_schema_object():
    stored = {
        "customProperties": [_p("service", "core")],
        "schema": [
            {
                "name": "a",
                "customProperties": [_p("entity", "User")],
                "properties": [{"name": "user_id"}],
            },
            {
                "name": "b",
                "customProperties": [_p("entity", "FxRate")],
                "properties": [
                    {"name": "currency"},
                    {"name": "rate_date", "primaryKey": True},
                ],
            },
        ],
    }

    first, second = _resolve(stored)["schema"]

    assert first["properties"][0]["primaryKey"] is True
    assert "primaryKey" not in second["properties"][0]
    assert second["properties"][1]["primaryKey"] is True


# What the contract declares wins.


def test_declared_values_win_over_the_ontology():
    stored = _contract(
        [
            _column(
                "email",
                _p("property", "User.email"),
                logicalType="string",
                classification="internal",
            ),
            _column(
                "created_at",
                _p("property", "User.created_at"),
                logicalType="date",
            ),
            _column(
                "user_id", _p("property", "User.user_id"), primaryKeyPosition=7
            ),
            _column("name", primaryKey=True, classification="public"),
        ],
        entity="User",
        domain="marketing",
    )

    resolved = _resolve(stored)
    columns = _columns(resolved)

    assert resolved["domain"] == "marketing"
    assert columns["email"]["classification"] == "internal"
    assert columns["created_at"]["logicalType"] == "date"
    # A key is declared on `name`, so `user_id` is not made a key part.
    assert "primaryKey" not in columns["user_id"]
    assert columns["user_id"]["primaryKeyPosition"] == 7
    assert columns["name"]["classification"] == "public"
    assert columns["name"]["primaryKey"] is True


def test_a_declared_position_stays_when_the_ontology_makes_the_key():
    stored = _contract(
        [
            _column("source", primaryKeyPosition=4),
            _column("transaction_id"),
        ],
        entity="Transaction",
    )

    columns = _columns(_resolve(stored))

    assert (
        columns["source"]["primaryKey"],
        columns["source"]["primaryKeyPosition"],
    ) == (True, 4)
    assert (
        columns["transaction_id"]["primaryKey"],
        columns["transaction_id"]["primaryKeyPosition"],
    ) == (True, 2)


def test_a_link_the_contract_holds_is_not_added_twice():
    stored = _contract(
        [
            _column(
                "email",
                _p("property", "User.email"),
                authoritativeDefinitions=[
                    {"type": "ontology", "url": "continuo-ontology:User.email"}
                ],
            )
        ],
        entity="User",
    )
    stored["schema"][0]["authoritativeDefinitions"] = [
        {"url": "continuo-ontology:User", "type": "other", "description": "mine"}
    ]

    resolved = _resolve(stored)

    assert resolved["schema"][0]["authoritativeDefinitions"] == [
        {"url": "continuo-ontology:User", "type": "other", "description": "mine"}
    ]
    assert _columns(resolved)["email"]["authoritativeDefinitions"] == [
        _link("continuo-ontology:User.email")
    ]


def test_an_authors_own_link_stays_beside_the_ontology_link():
    mine = {"type": "wiki", "url": "https://wiki.example/user"}
    stored = _contract(
        [
            _column(
                "email",
                _p("property", "User.email"),
                authoritativeDefinitions=[mine],
            )
        ],
        entity="User",
    )

    links = _columns(_resolve(stored))["email"]["authoritativeDefinitions"]

    assert mine in links
    assert _link("continuo-ontology:User.email") in links
    assert len(links) == 2


# Aggregates.


def test_aggregate_columns_become_measures_and_dimensions():
    stored = _contract(
        [
            _column(
                "total",
                _p("aggregate", {"from": "Transaction.amount_eur", "agg": "sum"}),
            ),
            _column(
                "orders", _p("aggregate", {"from": "Transaction", "agg": "count"})
            ),
            _column(
                "buyers",
                _p("aggregate", {"from": "Transaction.user_id", "agg": "count"}),
            ),
            _column(
                "hits",
                _p(
                    "aggregate",
                    {
                        "from": "MarketingSpend.clicks",
                        "agg": "max",
                        "where": {"channel": "x"},
                    },
                ),
            ),
            _column("month", _p("aggregate", {"period": "month"})),
            _column(
                "seen_month",
                _p(
                    "aggregate",
                    {"from": "Transaction.created_at", "period": "month"},
                ),
            ),
            _column(
                "no_type", _p("aggregate", {"from": "Transaction", "agg": "sum"})
            ),
            _column(
                "only_from", _p("aggregate", {"from": "Transaction.amount_eur"})
            ),
        ],
    )

    columns = _columns(_resolve(stored))

    def shape(name):
        column = columns[name]
        return column.get("semanticType"), column.get("logicalType")

    assert shape("total") == ("measure", "number")
    assert shape("orders") == ("measure", "integer")
    assert shape("buyers") == ("measure", "integer")
    assert shape("hits") == ("measure", "integer")
    assert shape("month") == ("dimension", "date")
    assert shape("seen_month") == ("dimension", "date")
    # A thing alone has no type, and a `from` with no `agg` and no `period` is neither.
    assert shape("no_type") == ("measure", None)
    assert shape("only_from") == (None, None)
    # Aggregates are linked to nothing.
    assert all(
        "authoritativeDefinitions" not in column for column in columns.values()
    )
    assert "authoritativeDefinitions" not in _resolve(stored)["schema"][0]


def test_a_count_is_an_integer_whatever_it_counts():
    stored = _contract(
        [
            _column(
                "n",
                _p(
                    "aggregate",
                    {"from": "Transaction.amount_eur", "agg": "count"},
                ),
            )
        ]
    )

    assert _columns(_resolve(stored))["n"]["logicalType"] == "integer"


def test_an_aggregate_gives_a_measure_when_it_has_an_agg_and_a_period():
    stored = _contract(
        [
            _column(
                "n",
                _p(
                    "aggregate",
                    {
                        "from": "Transaction.amount_eur",
                        "agg": "sum",
                        "period": "day",
                    },
                ),
            )
        ]
    )

    column = _columns(_resolve(stored))["n"]

    assert (column["semanticType"], column["logicalType"]) == (
        "measure",
        "number",
    )


def test_a_declared_type_of_an_aggregate_column_wins():
    stored = _contract(
        [
            _column(
                "total",
                _p("aggregate", {"from": "Transaction.amount_eur", "agg": "sum"}),
                logicalType="integer",
                semanticType="dimension",
            ),
            _column(
                "month",
                _p("aggregate", {"period": "month"}),
                logicalType="string",
            ),
        ]
    )

    columns = _columns(_resolve(stored))

    assert (
        columns["total"]["semanticType"],
        columns["total"]["logicalType"],
    ) == (
        "dimension",
        "integer",
    )
    assert (
        columns["month"]["semanticType"],
        columns["month"]["logicalType"],
    ) == (
        "dimension",
        "string",
    )


@pytest.mark.parametrize(
    "aggregate",
    [
        {"from": "Nothing.amount", "agg": "sum"},
        {"from": "Transaction.nothing", "agg": "sum"},
        {"from": "Nothing", "agg": "count"},
        {"from": "", "agg": "count"},
        {"from": 5, "agg": "sum"},
        {"from": "Transaction.amount_eur.x", "agg": "sum"},
        {"agg": ""},
        {},
        {"period": ""},
        "sum",
        None,
    ],
)
def test_an_aggregate_that_does_not_resolve_is_left_as_written(aggregate):
    stored = _contract([_column("x", _p("aggregate", aggregate))])

    assert _resolve(stored) == {**stored, "domain": "core"}


# What does not resolve.


@pytest.mark.parametrize(
    "value",
    [
        "Nothing.name",
        "User.nothing",
        "User",
        "User.name.x",
        ".name",
        "User.",
        7,
        ["User.name"],
    ],
)
def test_an_unresolvable_binding_is_left_as_written(value):
    stored = _contract([_column("name", _p("property", value))], entity="User")

    resolved = _resolve(stored)

    column = _columns(resolved)["name"]
    assert column == _columns(stored)["name"]
    # The schema object is still linked: its own binding resolves.
    assert resolved["schema"][0]["authoritativeDefinitions"] == [
        _link("continuo-ontology:User")
    ]


def test_an_entity_that_is_no_thing_is_left_as_written_and_its_columns_are_not_bound():
    stored = _contract(
        [
            _column("name"),
            # An explicit binding resolves by itself.
            _column("email", _p("property", "User.email")),
        ],
        entity="Nothing",
    )

    resolved = _resolve(stored)

    table = resolved["schema"][0]
    assert "authoritativeDefinitions" not in table
    assert table["customProperties"] == stored["schema"][0]["customProperties"]
    columns = _columns(resolved)
    assert columns["name"] == {"name": "name"}
    assert columns["email"]["logicalType"] == "string"
    assert "primaryKey" not in columns["email"]


@pytest.mark.parametrize("entity", [7, ["User"], ""])
def test_a_malformed_entity_is_left_as_written(entity):
    stored = _contract([_column("name")])
    stored["schema"][0]["customProperties"] = [_p("entity", entity)]

    resolved = _resolve(canonical_form(stored))

    assert _columns(resolved)["name"] == {"name": "name"}
    assert "authoritativeDefinitions" not in resolved["schema"][0]


def test_a_column_with_more_than_one_binding_is_left_as_written():
    stored = _contract(
        [
            _column("a", _p("property", "User.email"), _p("local", True)),
            _column(
                "b",
                _p("property", "User.email"),
                _p("aggregate", {"from": "User.email", "agg": "count"}),
            ),
            _column("c", _p("local", True), _p("aggregate", {"period": "day"})),
        ],
        entity="User",
    )

    columns = _columns(_resolve(stored))

    assert columns == _columns(stored)


def test_a_service_property_that_is_no_text_leaves_the_contract_unchanged():
    for value in (7, ["core"], {"core": 1}, None):
        stored = _contract([_column("name")], entity="User", service=None)
        stored["customProperties"].append(_p("service", value))
        stored = canonical_form(stored)

        assert _resolve(stored) == stored


# A service the ontology does not declare.


def test_resolve_leaves_an_undeclared_service_unchanged():
    stored = _contract(
        [
            _column("name"),
            _column("email", _p("property", "User.email")),
            _column("n", _p("aggregate", {"period": "day"})),
        ],
        entity="User",
        service="somewhere-else",
    )

    resolved = _resolve(stored)

    assert resolved == stored
    assert contract_hash(resolved) == contract_hash(stored)


def test_a_contract_with_no_service_is_left_unchanged():
    stored = _contract([_column("name")], entity="User", service=None)

    assert _resolve(stored) == stored


def test_an_undeclared_service_comes_back_in_canonical_form():
    stored = {
        "customProperties": [_p("service", "somewhere-else")],
        "description": {"purpose": "  padded \r\n"},
        "tags": ["b", "a", "b"],
        "schema": [
            {"name": "t", "properties": [{"name": "c", "required": False}]}
        ],
    }

    assert _resolve(stored) == canonical_form(stored)
    assert _resolve(stored)["tags"] == ["a", "b"]


def test_an_ontology_that_declares_nothing_changes_nothing():
    stored = _contract([_column("name")], entity="User")

    assert _resolve(stored, ontology.load("entities: {}")) == stored


# A new dict, and the input is left alone.


def test_resolve_gives_a_new_dict_and_leaves_its_input_alone():
    stored = _contract(
        [_column("name"), _column("total", _p("aggregate", {"period": "day"}))],
        entity="User",
    )
    before = copy.deepcopy(stored)

    resolved = _resolve(stored)

    assert stored == before
    assert resolved is not stored
    resolved["schema"][0]["properties"][0]["logicalType"] = "changed"
    resolved["schema"][0]["customProperties"].clear()
    assert stored == before


def test_resolve_of_an_undeclared_service_is_a_new_dict_too():
    stored = _contract([_column("name")], service="elsewhere")
    before = copy.deepcopy(stored)

    resolved = _resolve(stored)
    resolved["schema"][0]["properties"][0]["x"] = 1

    assert resolved is not stored
    assert stored == before


def test_resolve_reads_a_mapping_that_is_not_a_dict_and_tuples():
    stored = _contract([_column("name")], entity="User")
    stored["schema"][0]["properties"] = tuple(stored["schema"][0]["properties"])

    assert (
        _resolve(stored)["schema"][0]["properties"][0]["logicalType"] == "string"
    )


# Idempotence, and the canonical form.


def _everything():
    return _contract(
        [
            _column("transaction_id"),
            _column("source"),
            _column("email", _p("property", "User.email")),
            _column(
                "n", _p("aggregate", {"from": "Transaction", "agg": "count"})
            ),
            _column("month", _p("aggregate", {"period": "month"})),
            _column("scratch", _p("local", True)),
            _column("broken", _p("property", "Nothing.x")),
        ],
        entity="Transaction",
        schema=[("grain", ["month"]), ("fixed", {"source": "card"})],
    )


def test_resolve_is_idempotent():
    once = _resolve(_everything())

    assert _resolve(once) == once
    assert contract_hash(_resolve(once)) == contract_hash(once)


@pytest.mark.parametrize("service", ["core", "service-py", "elsewhere"])
def test_resolve_is_idempotent_for_each_kind_of_service(service):
    once = _resolve(_contract([_column("name")], entity="User", service=service))

    assert _resolve(once) == once


def test_the_result_is_in_the_canonical_form():
    resolved = _resolve(_everything())

    assert canonical_form(resolved) == resolved


def test_resolve_does_not_depend_on_the_order_of_the_lists_it_reads():
    stored = _everything()
    shuffled = copy.deepcopy(stored)
    shuffled["customProperties"].reverse()
    shuffled["schema"][0]["customProperties"].reverse()
    for column in shuffled["schema"][0]["properties"]:
        column.get("customProperties", []).reverse()

    assert _resolve(shuffled) == _resolve(stored)


# The resolved form is a valid ODCS contract of its family.


def test_the_resolved_form_of_a_bound_python_contract_is_valid():
    doc = yaml_strict.load(PY_DEMO.read_text(encoding="utf-8"))
    table = doc["schema"][0]
    table["customProperties"] = [
        _p("entity", "Order"),
        _p("grain", ["day"]),
        _p("filter", {"currency": "EUR"}),
    ]
    day, count, total = table["properties"]
    day["customProperties"] = [_p("aggregate", {"period": "day"})]
    count["customProperties"] = [
        _p("aggregate", {"from": "Order", "agg": "count"})
    ]
    total["customProperties"] = [
        _p("aggregate", {"from": "Order.amount", "agg": "sum"})
    ]
    table["properties"].append(
        {"name": "customer", "physicalType": "TEXT", "required": True}
    )
    stored = normalise_python(doc, service="service-py", reads=[])

    resolved = _resolve(stored)

    assert resolved["domain"] == "analytics"
    assert resolved != stored
    columns = _columns(resolved)
    assert columns["customer"]["authoritativeDefinitions"] == [
        _link("continuo-ontology:Order.customer")
    ]
    assert columns["total_amount"]["semanticType"] == "measure"
    assert schema_issues(resolved) == []
    assert (
        validate_python_contract(
            resolved,
            path="contracts/py_daily_kpis.odcs.yaml",
            service="service-py",
        )
        == []
    )


def test_the_resolved_form_of_a_bound_dbt_contract_is_valid():
    stored = json.loads(GOLDEN.read_text("ascii"))["stored"]
    stored["customProperties"] = [
        item
        for item in stored["customProperties"]
        if item["property"] != "service"
    ] + [_p("service", "core")]
    table = stored["schema"][0]
    table["customProperties"] = [
        _p("entity", "Transaction"),
        _p("grain", ["month"]),
        _p("fixed", {"source": "card"}),
    ]
    table["properties"] = [
        {"name": "source", "physicalType": "text"},
        {"name": "transaction_id"},
        {"name": "user_id", "customProperties": [_p("property", "User.user_id")]},
        {"name": "email", "customProperties": [_p("property", "User.email")]},
        {"name": "scratch", "customProperties": [_p("local", True)]},
        {
            "name": "total",
            "customProperties": [
                _p("aggregate", {"from": "Transaction.amount_eur", "agg": "sum"})
            ],
        },
    ]
    stored = canonical_form(stored)
    assert validate_dbt_contract(stored) == []

    resolved = _resolve(stored)

    assert resolved != stored
    columns = _columns(resolved)
    assert columns["source"]["primaryKey"] is True
    assert columns["transaction_id"]["primaryKeyPosition"] == 2
    assert columns["email"]["classification"] == "personal"
    assert columns["total"]["semanticType"] == "measure"
    assert schema_issues(resolved) == []
    assert validate_dbt_contract(resolved) == []


# The type of a field.


def test_a_ref_gives_the_type_of_the_key_it_points_at():
    ont = ontology.load(
        """
principals: {services: {core: {domain: core}}}
entities:
  A: {key: {id: int}}
  B: {key: {a: "ref A"}}
  C: {key: {b: "ref B"}}
  Pair: {key: {x: int, y: int}}
  Loop1: {key: {k: "ref Loop2"}}
  Loop2: {key: {k: "ref Loop1"}}
  Self: {key: {k: "ref Self"}}
  Holder:
    key: {id: int}
    properties:
      by_ref:        {ref: A}
      by_type:       {type: "ref A"}
      by_chain:      {ref: C}
      to_pair:       {ref: Pair}
      to_nothing:    {ref: Nothing}
      to_loop:       {ref: Loop1}
      to_self:       {type: "ref Self"}
      wide:          {type: "ref   A"}
      padded:        {type: " int "}
      choice:        {type: "enum[a, b]"}
      flag:          {type: bool}
      text:          {type: string}
      fixed_point:   {type: decimal}
      stamp:         {type: timestamp}
      unknown:       {type: uuid}
      empty:         {}
"""
    )
    columns = [
        "by_ref", "by_type", "by_chain", "to_pair", "to_nothing", "to_loop",
        "to_self", "wide", "padded", "choice", "flag", "text", "fixed_point",
        "stamp", "unknown", "empty",
    ]  # fmt: skip
    stored = _contract([_column(name) for name in columns], entity="Holder")

    resolved = _columns(_resolve(stored, ont))

    assert {name: resolved[name].get("logicalType") for name in columns} == {
        "by_ref": "integer",
        "by_type": "integer",
        "by_chain": "integer",
        "to_pair": None,
        "to_nothing": None,
        "to_loop": None,
        "to_self": None,
        "wide": "integer",
        "padded": "integer",
        "choice": "string",
        "flag": "boolean",
        "text": "string",
        "fixed_point": "number",
        "stamp": "timestamp",
        "unknown": None,
        "empty": None,
    }
    # A column of a type with no logical type is still bound and linked.
    assert resolved["unknown"]["authoritativeDefinitions"] == [
        _link("continuo-ontology:Holder.unknown")
    ]


def test_the_ontology_type_map():
    ont = ontology.load(
        """
principals: {services: {core: {domain: core}}}
entities:
  T:
    key: {a: int, b: string, c: timestamp, d: date, e: money, f: decimal, g: bool, h: "enum[x]"}
"""
    )
    stored = _contract([_column(name) for name in "abcdefgh"], entity="T")

    columns = _columns(_resolve(stored, ont))

    assert [columns[name]["logicalType"] for name in "abcdefgh"] == [
        "integer",
        "string",
        "timestamp",
        "date",
        "number",
        "number",
        "boolean",
        "string",
    ]


def test_only_a_property_marked_personal_is_personal():
    ont = ontology.load(
        """
principals: {services: {core: {domain: core}}}
entities:
  T:
    key: {id: int}
    properties:
      yes: {type: string, personal: true}
      no: {type: string, personal: false}
      unsaid: {type: string}
"""
    )
    stored = _contract(
        [_column(name) for name in ("yes", "no", "unsaid")], entity="T"
    )

    columns = _columns(_resolve(stored, ont))

    assert columns["yes"]["classification"] == "personal"
    assert "classification" not in columns["no"]
    assert "classification" not in columns["unsaid"]


def test_a_contract_with_no_schema_gets_only_its_domain():
    stored = canonical_form({"customProperties": [_p("service", "core")]})

    assert _resolve(stored) == {**stored, "domain": "core"}


def test_a_table_with_no_columns_gets_only_its_link():
    stored = canonical_form(
        {
            "customProperties": [_p("service", "core")],
            "schema": [{"name": "t", "customProperties": [_p("entity", "User")]}],
        }
    )

    resolved = _resolve(stored)

    assert resolved["schema"][0]["authoritativeDefinitions"] == [
        _link("continuo-ontology:User")
    ]
    assert "properties" not in resolved["schema"][0]
