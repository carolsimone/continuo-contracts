"""Loading an ontology file strictly, and stripping the ontology part from a stored contract."""

import copy
import hashlib
import json
import re

from pathlib import Path

import pytest

from continuo_contracts import ontology
from continuo_contracts.canonical import canonical_json
from continuo_contracts.contract import canonical_form
from continuo_contracts.errors import ContractsError, OntologyError
from continuo_contracts.vocabulary import PROPERTIES, VENDOR

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests" / "fixtures" / "ontology" / "demo.yaml"
VECTORS = ROOT / "continuo_contracts" / "vectors"
GOLDEN = ROOT / "tests" / "fixtures" / "dbt" / "golden"

SCHEMA_BINDINGS = ("entity", "grain", "fixed", "filter")
COLUMN_BINDINGS = ("property", "local", "aggregate")


# Loading.


def test_the_demo_ontology_loads():
    o = ontology.load(DEMO.read_text())

    assert o.declares("core") and o.domain_of("finance") == "finance"
    assert o.sha256 == hashlib.sha256(DEMO.read_bytes()).hexdigest()
    assert o.services() == (
        "card-service",
        "core",
        "finance",
        "flink-transactions",
        "marketing",
        "service-py",
    )
    assert o.document["entities"]["User"]["key"] == {"user_id": "int"}


def test_an_ontology_error_is_a_contracts_error_and_a_value_error():
    assert issubclass(OntologyError, ContractsError)
    assert issubclass(OntologyError, ValueError)


def test_the_hash_is_of_the_text_as_utf_8():
    text = "entities: {Café: {owner: core}}\n"

    assert (
        ontology.load(text).sha256
        == hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def test_the_ontology_is_frozen():
    o = ontology.load("entities: {}")

    with pytest.raises(AttributeError):
        o.sha256 = "0"


@pytest.mark.parametrize(
    "text, path",
    [
        ("entities: {User: {personnal: true}}", "entities.User.personnal"),
        (
            "principals: {services: {core: {domain: 3}}}",
            "principals.services.core.domain",
        ),
        ("entities: {User: {key: [user_id]}}", "entities.User.key"),
        ("- a list", "<root>"),
        ("", "<root>"),
        ("a string", "<root>"),
        # An unknown key, at each level that names its keys.
        ("flavour: 1", "flavour"),
        ("principals: {clans: {}}", "principals.clans"),
        (
            "principals: {domains: {core: {boss: x}}}",
            "principals.domains.core.boss",
        ),
        (
            "principals: {services: {core: {size: 1}}}",
            "principals.services.core.size",
        ),
        (
            "principals: {teams: {core: {group: x}}}",
            "principals.teams.core.group",
        ),
        (
            "principals: {roles: {core: {owner: x}}}",
            "principals.roles.core.owner",
        ),
        ("principals: {people: {origin: idp}}", "principals.people.origin"),
        ("entities: {User: {note: x}}", "entities.User.note"),
        (
            "entities: {User: {properties: {name: {size: 3}}}}",
            "entities.User.properties.name.size",
        ),
        (
            "entities: {User: {access: [{who: [core]}]}}",
            "entities.User.access[0].who",
        ),
        # A value of the wrong type, at each kind of value.
        ("principals: []", "principals"),
        ("principals: {domains: [core]}", "principals.domains"),
        ("principals: {domains: {core: owner}}", "principals.domains.core"),
        (
            "principals: {domains: {core: {owner_team: 1}}}",
            "principals.domains.core.owner_team",
        ),
        (
            "principals: {services: {core: {kind: [dbt]}}}",
            "principals.services.core.kind",
        ),
        (
            "principals: {teams: {core: {idp_group: null}}}",
            "principals.teams.core.idp_group",
        ),
        (
            "principals: {roles: {core: {idp_group: true}}}",
            "principals.roles.core.idp_group",
        ),
        ("principals: {people: {source: 1.5}}", "principals.people.source"),
        ("principals: {people: idp}", "principals.people"),
        ("entities: [User]", "entities"),
        ("entities: {User: core}", "entities.User"),
        ("entities: {User: {owner: 1}}", "entities.User.owner"),
        ("entities: {User: {born_in: [kafka.x]}}", "entities.User.born_in"),
        ("entities: {User: {key: {user_id: 1}}}", "entities.User.key.user_id"),
        ("entities: {User: {properties: [name]}}", "entities.User.properties"),
        (
            "entities: {User: {properties: {name: string}}}",
            "entities.User.properties.name",
        ),
        (
            "entities: {User: {properties: {name: {type: 1}}}}",
            "entities.User.properties.name.type",
        ),
        (
            "entities: {User: {properties: {name: {ref: [User]}}}}",
            "entities.User.properties.name.ref",
        ),
        (
            "entities: {User: {properties: {name: {personal: 'yes'}}}}",
            "entities.User.properties.name.personal",
        ),
        (
            "entities: {User: {properties: {name: {personal: 1}}}}",
            "entities.User.properties.name.personal",
        ),
        (
            "entities: {User: {properties: {name: {nullable: 'no'}}}}",
            "entities.User.properties.name.nullable",
        ),
        (
            "entities: {User: {properties: {name: {currency: 3}}}}",
            "entities.User.properties.name.currency",
        ),
        (
            "entities: {User: {properties: {name: {derived_from: User.name}}}}",
            "entities.User.properties.name.derived_from",
        ),
        (
            "entities: {User: {properties: {name: {derived_from: [User.name, 3]}}}}",
            "entities.User.properties.name.derived_from[1]",
        ),
        (
            "entities: {User: {access: {properties: [name]}}}",
            "entities.User.access",
        ),
        ("entities: {User: {access: [name]}}", "entities.User.access[0]"),
        (
            "entities: {User: {access: [{properties: name}]}}",
            "entities.User.access[0].properties",
        ),
        (
            "entities: {User: {access: [{readers: [core, 2]}]}}",
            "entities.User.access[0].readers[1]",
        ),
        (
            "entities: {User: {access: [{purpose: [x]}]}}",
            "entities.User.access[0].purpose",
        ),
        # A name that is blank.
        ("entities: {'': {owner: core}}", "entities"),
        ("principals: {services: {' ': {domain: core}}}", "principals.services"),
        ("entities: {User: {key: {'': int}}}", "entities.User.key"),
        ("entities: {User: {properties: {'': {}}}}", "entities.User.properties"),
        # A name that `<Thing>.<field>` cannot carry: whitespace, a dot or a slash.
        ("entities: {'User x': {owner: core}}", "entities"),
        ("entities: {'User.x': {owner: core}}", "entities"),
        ("entities: {'User/x': {owner: core}}", "entities"),
        ("entities: {User: {key: {'user id': int}}}", "entities.User.key"),
        ("entities: {User: {key: {'user.id': int}}}", "entities.User.key"),
        ("entities: {User: {key: {'user/id': int}}}", "entities.User.key"),
        (
            "entities: {User: {properties: {'display name': {}}}}",
            "entities.User.properties",
        ),
        (
            "entities: {User: {properties: {'display.name': {}}}}",
            "entities.User.properties",
        ),
        (
            "entities: {User: {properties: {'display/name': {}}}}",
            "entities.User.properties",
        ),
    ],
)
def test_an_ontology_of_the_wrong_shape_is_refused(text, path):
    with pytest.raises(OntologyError, match=rf"^{re.escape(path)}: "):
        ontology.load(text)


def test_the_message_is_the_path_a_colon_and_the_reason():
    with pytest.raises(OntologyError) as unknown:
        ontology.load("entities: {User: {personnal: true}}")
    with pytest.raises(OntologyError) as wrong_type:
        ontology.load("principals: {services: {core: {domain: 3}}}")
    with pytest.raises(OntologyError) as not_a_mapping:
        ontology.load("- a list")
    with pytest.raises(OntologyError) as blank:
        ontology.load("entities: {' ': {}}")

    assert str(unknown.value) == (
        "entities.User.personnal: unknown key, the keys of a thing are access, "
        "born_in, description, key, owner, properties"
    )
    assert str(wrong_type.value) == (
        "principals.services.core.domain: must be a string, got a number"
    )
    assert str(not_a_mapping.value) == "<root>: must be a mapping, got a list"
    assert str(blank.value) == "entities: a thing name is not blank, got ' '"


@pytest.mark.parametrize(
    "text, message",
    [
        (
            "entities: {'User x': {}}",
            "entities: a thing name has no whitespace, control character, dot or slash, "
            "got 'User x'",
        ),
        (
            "entities: {User: {key: {'user.id': int}}}",
            "entities.User.key: a part name has no whitespace, control character, dot or "
            "slash, got 'user.id'",
        ),
        (
            "entities: {User: {properties: {'a/b': {}}}}",
            "entities.User.properties: a property name has no whitespace, control "
            "character, dot or slash, got 'a/b'",
        ),
    ],
)
def test_a_name_that_a_reference_cannot_carry_is_refused_with_the_path_of_its_mapping(
    text, message
):
    with pytest.raises(OntologyError) as refused:
        ontology.load(text)

    assert str(refused.value) == message


def test_a_domain_a_service_a_team_and_a_role_keep_names_with_whitespace():
    # Only the names that `<Thing>.<field>` carries are bound: a principal is named as its owner
    # writes it.
    o = ontology.load(
        "principals:\n"
        "  domains: {'core data': {owner_team: 'core team'}}\n"
        "  services: {'my service': {domain: 'core data'}}\n"
        "  teams: {'core team': {idp_group: 'okta:core'}}\n"
        "  roles: {'data analyst': {idp_group: 'okta:analysts'}}\n"
    )

    assert o.declares("my service")


@pytest.mark.parametrize(
    "text, reason",
    [
        ("entities: {User: {properties: {a: {personal: 1}}}}", "got a number"),
        ("entities: {User: {owner: null}}", "got null"),
        ("entities: {User: {owner: true}}", "got a boolean"),
        ("entities: {User: {owner: [a]}}", "got a list"),
        ("entities: {User: {owner: {a: b}}}", "got a mapping"),
        ("entities: {User: {properties: {a: {nullable: x}}}}", "got a string"),
    ],
)
def test_the_reason_says_what_the_value_is(text, reason):
    with pytest.raises(OntologyError, match=reason):
        ontology.load(text)


def test_the_first_error_in_file_order_is_the_one_raised():
    text = "entities: {User: {owner: 1, key: [a], flavour: x}}"

    with pytest.raises(OntologyError, match=r"entities\.User\.owner:"):
        ontology.load(text)


def test_a_strict_yaml_error_is_an_ontology_error():
    with pytest.raises(OntologyError) as duplicate:
        ontology.load("entities: {}\nentities: {}\n")
    with pytest.raises(OntologyError) as alias:
        ontology.load("entities: &a {}\nprincipals: *a\n")
    with pytest.raises(OntologyError) as broken:
        ontology.load("entities: [\n")

    for error in (duplicate, alias, broken):
        assert str(error.value).startswith("<root>: line ")
        assert isinstance(error.value.__cause__, ValueError)
    assert "duplicate" in str(duplicate.value)


def test_an_empty_mapping_is_an_ontology_that_declares_nothing():
    o = ontology.load("{}")

    assert o.services() == () and not o.declares("core")
    assert o.domain_of("core") is None


DESCRIBED = """\
description: the estate
principals:
  description: who acts on data
  domains:
    core: {owner_team: core-data, description: the core domain}
  services:
    core: {domain: core, kind: dbt, description: the core service}
  teams:
    core-data: {idp_group: "okta:core-data", description: the core team}
  roles:
    analyst: {idp_group: "okta:analysts", description: the analysts}
  people:
    source: idp
    description: synced
entities:
  User:
    description: a user
    owner: core
    born_in: kafka.users
    key: {user_id: int}
    properties:
      name:
        description: what the user is called
        type: string
        personal: true
        nullable: false
        currency: EUR
        ref: Other
        derived_from: [User.nickname]
    access:
      - description: the core team reads it
        properties: [name]
        readers: [core]
        purpose: service_operation
"""


def test_description_is_allowed_everywhere():
    o = ontology.load(DESCRIBED)

    assert o.document["description"] == "the estate"
    assert o.document["entities"]["User"]["access"][0]["description"]


@pytest.mark.parametrize(
    "text, path",
    [
        ("description: 3", "description"),
        ("principals: {description: [x]}", "principals.description"),
        (
            "principals: {domains: {core: {description: 1}}}",
            "principals.domains.core.description",
        ),
        ("entities: {User: {description: true}}", "entities.User.description"),
        (
            "entities: {User: {properties: {name: {description: 1}}}}",
            "entities.User.properties.name.description",
        ),
        (
            "entities: {User: {access: [{description: 1}]}}",
            "entities.User.access[0].description",
        ),
    ],
)
def test_description_is_a_string(text, path):
    with pytest.raises(OntologyError, match=rf"^{re.escape(path)}: "):
        ontology.load(text)


def test_description_is_a_name_where_a_mapping_lists_names():
    o = ontology.load(
        "entities: {description: {owner: core, key: {description: int}}}"
    )

    assert o.document["entities"]["description"]["key"] == {"description": "int"}


# What the ontology says about a service.


def test_a_service_is_declared_when_the_ontology_names_it():
    o = ontology.load(DEMO.read_text())

    assert o.declares("service-py") and not o.declares("nobody")
    assert o.domain_of("service-py") == "analytics"
    assert o.domain_of("nobody") is None


def test_a_service_with_no_domain_has_none():
    o = ontology.load("principals: {services: {core: {kind: dbt}}}")

    assert o.declares("core") and o.domain_of("core") is None


def test_the_services_come_in_sorted_order_whatever_the_file_order():
    o = ontology.load(
        "principals: {services: {zeta: {domain: a}, alpha: {domain: a}, mid: {}}}"
    )

    assert o.services() == ("alpha", "mid", "zeta")


def test_an_ontology_with_no_principals_declares_no_service():
    o = ontology.load("entities: {User: {owner: core}}")

    assert o.services() == () and not o.declares("core")


# The binding properties.


def test_the_binding_properties_are_the_seven_the_vocabulary_places():
    seven = (*SCHEMA_BINDINGS, *COLUMN_BINDINGS)

    assert seven == ontology.BINDING_PROPERTIES
    for name in SCHEMA_BINDINGS:
        assert PROPERTIES[name].sits_on == "schema"
    for name in COLUMN_BINDINGS:
        assert PROPERTIES[name].sits_on == "column"


def test_the_link_prefix():
    assert ontology.LINK_PREFIX == "continuo-ontology:"


# The ontology part of a stored contract.


def _item(name, value, vendor=VENDOR):
    return {"vendor": vendor, "property": name, "value": value}


def _link(url, kind="ontology"):
    return {"type": kind, "url": url}


def _bound():
    """A stored form that carries each binding, each link and some of the author's own."""
    return {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": "analytics.orders",
        "name": "orders",
        "domain": "core",
        "version": "0",
        "status": "active",
        "customProperties": [
            _item("service", "core"),
            _item("entity", "Order"),
        ],
        "schema": [
            {
                "name": "orders",
                "physicalName": "orders",
                "authoritativeDefinitions": [
                    _link("continuo-ontology:Order"),
                    _link("https://example.com/orders", "businessDefinition"),
                ],
                "customProperties": [
                    _item("entity", "Order"),
                    _item("grain", ["order_id"]),
                    _item("fixed", {"source": "card"}),
                    _item("filter", {"year": 2026}),
                    _item("reads", ["analytics.raw_orders"]),
                    _item("entity", "Order", vendor="acme"),
                ],
                "properties": [
                    {
                        "name": "order_id",
                        "logicalType": "integer",
                        "authoritativeDefinitions": [
                            _link("continuo-ontology:Order.order_id")
                        ],
                        "customProperties": [_item("property", "Order.order_id")],
                    },
                    {
                        "name": "total",
                        "authoritativeDefinitions": [
                            _link("continuo-ontology:Order.amount"),
                            _link(
                                "https://example.com/total", "businessDefinition"
                            ),
                        ],
                        "customProperties": [
                            _item(
                                "aggregate",
                                {"from": "Order.amount", "agg": "sum"},
                            ),
                            _item("local", True),
                            _item("property", "Order.amount", vendor="acme"),
                            _item("other", 1),
                        ],
                    },
                    {"name": "note", "logicalType": "string"},
                ],
            }
        ],
    }


def test_strip_removes_the_bindings_and_the_ontology_links():
    stripped = ontology.strip(_bound())

    table = stripped["schema"][0]
    assert table["customProperties"] == [
        _item("entity", "Order", vendor="acme"),
        _item("reads", ["analytics.raw_orders"]),
    ]
    # An author's own https link stays, whatever its type.
    assert table["authoritativeDefinitions"] == [
        _link("https://example.com/orders", "businessDefinition")
    ]
    first, second, third = table["properties"]
    assert "customProperties" not in first
    assert "authoritativeDefinitions" not in first
    assert second["customProperties"] == [
        _item("other", 1),
        _item("property", "Order.amount", vendor="acme"),
    ]
    assert second["authoritativeDefinitions"] == [
        _link("https://example.com/total", "businessDefinition")
    ]
    assert third == {"name": "note", "logicalType": "string"}


def test_strip_keeps_the_domain_and_every_resolved_value():
    stripped = ontology.strip(_bound())

    assert stripped["domain"] == "core"
    assert stripped["schema"][0]["properties"][0]["logicalType"] == "integer"
    # The root `entity` is not a binding of the schema object, so it stays.
    assert stripped["customProperties"] == [
        _item("entity", "Order"),
        _item("service", "core"),
    ]


def test_strip_drops_a_list_it_empties_and_a_list_with_no_ontology_part_stays():
    stored = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "schema": [
            {
                "name": "t",
                "customProperties": [_item("entity", "T"), _item("grain", ["a"])],
                "authoritativeDefinitions": [_link("continuo-ontology:T")],
                "properties": [
                    {
                        "name": "a",
                        "customProperties": [_item("local", True)],
                        "authoritativeDefinitions": [
                            _link("continuo-ontology:T.a")
                        ],
                    }
                ],
            }
        ],
    }

    assert ontology.strip(stored) == {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "schema": [{"name": "t", "properties": [{"name": "a"}]}],
    }


def test_strip_removes_an_ontology_link_at_the_root_too():
    stored = {
        "apiVersion": "v3.2.0",
        "authoritativeDefinitions": [
            _link("continuo-ontology:Order"),
            _link("https://example.com/a", "businessDefinition"),
        ],
        "schema": [{"name": "t"}],
    }

    assert ontology.strip(stored)["authoritativeDefinitions"] == [
        _link("https://example.com/a", "businessDefinition")
    ]


def test_strip_keeps_an_entry_that_is_no_link_whatever_it_is():
    odd = [
        "not a mapping",
        {"type": "x"},
        {"type": "x", "url": 3},
        {"url": "continuo-ontology"},
    ]
    stored = {"schema": [{"name": "t", "authoritativeDefinitions": odd}]}

    kept = ontology.strip(stored)["schema"][0]["authoritativeDefinitions"]

    assert sorted(kept, key=canonical_json) == sorted(odd, key=canonical_json)


def test_strip_reads_a_binding_by_vendor_and_property_name_only():
    stored = {
        "schema": [
            {
                "name": "t",
                "customProperties": [
                    "not a mapping",
                    {"property": "entity", "value": "T"},
                    {"vendor": "continuo", "property": 3, "value": 1},
                    _item("grain", ["a"]),
                ],
            }
        ]
    }

    kept = ontology.strip(stored)["schema"][0]["customProperties"]

    assert sorted(kept, key=canonical_json) == sorted(
        [
            "not a mapping",
            {"property": "entity", "value": "T"},
            {"vendor": "continuo", "property": 3, "value": 1},
        ],
        key=canonical_json,
    )


def test_strip_of_a_contract_with_no_schema_is_the_contract():
    assert ontology.strip({"apiVersion": "v3.2.0", "name": "x"}) == {
        "apiVersion": "v3.2.0",
        "name": "x",
    }


def test_strip_gives_a_new_dict_and_leaves_its_input_alone():
    stored = _bound()
    before = copy.deepcopy(stored)

    stripped = ontology.strip(stored)

    assert stored == before
    assert stripped is not stored
    stripped["schema"][0]["name"] = "changed"
    stripped["customProperties"].append("x")
    assert stored == before


def test_strip_is_idempotent():
    once = ontology.strip(_bound())

    assert ontology.strip(once) == once


# Each place a binding may not sit on is left alone: the schema object keeps `property`
# and a column keeps `entity`, which the contract check refuses elsewhere.
def test_strip_removes_a_binding_only_where_it_sits():
    stored = {
        "schema": [
            {
                "name": "t",
                "customProperties": [
                    _item("property", "T.a"),
                    _item("entity", "T"),
                ],
                "properties": [
                    {
                        "name": "a",
                        "customProperties": [
                            _item("entity", "T"),
                            _item("property", "T.a"),
                        ],
                    }
                ],
            }
        ]
    }

    stripped = ontology.strip(stored)["schema"][0]

    assert stripped["customProperties"] == [_item("property", "T.a")]
    assert stripped["properties"][0]["customProperties"] == [_item("entity", "T")]


def test_has_bindings():
    assert ontology.has_bindings(_bound())
    assert not ontology.has_bindings(ontology.strip(_bound()))


@pytest.mark.parametrize("name", SCHEMA_BINDINGS)
def test_has_bindings_sees_each_schema_property(name):
    stored = {"schema": [{"name": "t", "customProperties": [_item(name, "x")]}]}

    assert ontology.has_bindings(stored)


@pytest.mark.parametrize("name", COLUMN_BINDINGS)
def test_has_bindings_sees_each_column_property(name):
    column = {"name": "a", "customProperties": [_item(name, "x")]}
    stored = {"schema": [{"name": "t", "properties": [column]}]}

    assert ontology.has_bindings(stored)


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {"schema": []},
        {"schema": [{"name": "t"}]},
        {"schema": [{"name": "t", "properties": [{"name": "a"}]}]},
        # A binding that sits on the other object, a vendor that is not continuo, a
        # property that is not one and a root property say nothing of an ontology.
        {"schema": [{"customProperties": [_item("property", "T.a")]}]},
        {
            "schema": [
                {"properties": [{"customProperties": [_item("entity", "T")]}]}
            ]
        },
        {"schema": [{"customProperties": [_item("entity", "T", vendor="acme")]}]},
        {"schema": [{"customProperties": [_item("reads", ["a.b"])]}]},
        {"customProperties": [_item("entity", "T")], "schema": [{"name": "t"}]},
        {"authoritativeDefinitions": [_link("continuo-ontology:T")]},
    ],
)
def test_has_bindings_is_false_without_a_binding(stored):
    assert not ontology.has_bindings(stored)


def test_the_ontology_links_alone_are_not_bindings():
    stored = {
        "schema": [
            {
                "name": "t",
                "authoritativeDefinitions": [_link("continuo-ontology:T")],
            }
        ]
    }

    assert not ontology.has_bindings(stored)
    assert ontology.strip(stored) == {"schema": [{"name": "t"}]}


# Strip over every stored form that the package pins.


def _every_stored_form_of_every_vector_and_golden():
    """Yield each stored form of the vector files and of the dbt goldens, with its origin."""
    for path in sorted(VECTORS.glob("*.json")):
        entries = json.loads(path.read_text("utf-8"))
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            stored = entry.get("stored") if isinstance(entry, dict) else None
            if isinstance(stored, dict):
                yield f"{path.name}[{index}]", stored
    for path in sorted(GOLDEN.rglob("*.json")):
        stored = json.loads(path.read_text("utf-8")).get("stored")
        if isinstance(stored, dict):
            yield str(path.relative_to(GOLDEN)), stored


def test_the_vectors_and_goldens_give_stored_forms_to_strip():
    found = dict(_every_stored_form_of_every_vector_and_golden())

    assert len(found) >= 60
    assert any(origin.startswith("contracts.json") for origin in found)
    assert any(origin.startswith("dbt_contracts.json") for origin in found)
    assert any("/" in origin for origin in found)


def test_strip_is_the_identity_on_every_vector():
    for origin, stored in _every_stored_form_of_every_vector_and_golden():
        if origin.startswith("ontology.json"):
            # The stored forms of this vector carry bindings on purpose: it is the one that pins
            # what resolve makes of them.
            continue
        assert canonical_json(ontology.strip(stored)) == canonical_json(stored), (
            origin
        )


def test_the_canonical_form_of_every_stored_form_is_itself():
    for origin, stored in _every_stored_form_of_every_vector_and_golden():
        assert canonical_json(canonical_form(stored)) == canonical_json(stored), (
            origin
        )
