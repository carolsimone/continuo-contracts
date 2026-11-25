"""The `order` rule: the order of copies comes from the copies they read, never from the thing."""

import copy

import pytest

from test_ontology_rules import (
    ONT,
    base,
    contract,
    ontology_document,
    ontology_of,
    problems,
    run,
    set_custom,
    table,
)

from continuo_contracts.ontology_check import RELEASE_RULES, SEVERITIES, check

ORDER = ("order",)


def estate_run(state, document=None):
    """The findings of the `order` rule over the contracts of `state`.

    `check` also writes the findings about a project the ontology does not declare, which
    are not the findings of a rule: they are left out.
    """
    return [f for f in run(state, ORDER, document) if f.rule == "order"]


def born_in(relation):
    """The ontology document with `Transaction.born_in` set to `relation`."""
    document = ontology_document()
    document["entities"]["Transaction"]["born_in"] = relation
    return document


def without_birth_copy():
    """The ontology document with no `born_in` on `Transaction`."""
    document = ontology_document()
    del document["entities"]["Transaction"]["born_in"]
    return document


def layers_of(findings):
    """The layers of the one `copy graph` information finding, as lists of contract ids."""
    [layered] = [f for f in findings if f.severity == "info"]
    assert (layered.rule, layered.subject, layered.contract_id) == (
        "order",
        "copy graph",
        None,
    )
    return [
        entry.split(": ", 1)[1].split(", ")
        for entry in layered.message.split(" | ")
    ]


def position_of(layers):
    return {cid: i for i, layer in enumerate(layers) for cid in layer}


def errors_of(findings):
    return [f for f in findings if f.severity == "error"]


# The layers.


def test_the_layers_are_one_information_finding():
    findings = estate_run(base())

    assert [(f.severity, f.rule, f.subject, f.contract_id) for f in findings] == [
        ("info", "order", "copy graph", None)
    ]
    assert findings[0].message == (
        "1: analytics.events, analytics.seed_users"
        " | 2: analytics.events_sink"
        " | 3: analytics.txns"
        " | 4: analytics.revenue"
    )


def test_the_base_estate_has_no_error_and_no_warning():
    assert problems(estate_run(base())) == []


def test_layers_put_the_birth_copy_before_its_batch_copies():
    position = position_of(layers_of(estate_run(base())))

    assert position["analytics.events"] < position["analytics.events_sink"]
    assert position["analytics.events_sink"] < position["analytics.txns"]
    assert position["analytics.txns"] < position["analytics.revenue"]


def test_a_contract_with_no_read_is_placed_first():
    state = {
        "rates_v2": contract(
            "finance",
            "rates_v2",
            entity="FxRate",
            reads=["analytics.fx_rates_csv"],
        ),
        "fx_rates_csv": contract("finance", "fx_rates_csv", entity="FxRate"),
    }
    set_custom(state["fx_rates_csv"], "kind", "python-csv")

    findings = estate_run(state, without_birth_copy())

    assert problems(findings) == []
    assert layers_of(findings) == [
        ["analytics.fx_rates_csv"],
        ["analytics.rates_v2"],
    ]


def test_a_contract_that_is_no_things_birth_copy_may_read_nothing():
    state = base()
    set_custom(state["seed_users"], "kind", "python-api")

    findings = estate_run(state)

    assert problems(findings) == []
    assert "analytics.seed_users" in layers_of(findings)[0]


def test_a_read_reaches_its_copy_by_its_physical_name_whatever_its_case():
    state = base()
    state["orders_v2"] = contract(
        "core", "orders_v2", entity="User", physical_name="orders_v2_alias"
    )
    state["report"] = contract(
        "finance",
        "report",
        entity="FxRate",
        reads=["Analytics.Orders_V2_Alias"],
    )

    position = position_of(layers_of(estate_run(state)))

    assert position["analytics.orders_v2"] < position["analytics.report"]


def test_a_read_no_checked_contract_holds_is_not_an_order_finding():
    state = base()
    set_custom(
        table(state["revenue"]),
        "reads",
        ["analytics.txns", "analytics.mystery", "kafka.card_events"],
    )

    findings = estate_run(state)

    assert problems(findings) == []
    position = position_of(layers_of(findings))
    assert position["analytics.txns"] < position["analytics.revenue"]


def test_a_contract_of_an_undeclared_project_is_not_in_the_graph():
    state = base()
    state["invoices"] = contract(
        "billing", "invoices", entity="Invoice", reads=["analytics.revenue"]
    )

    findings = estate_run(state)

    assert problems(findings) == []
    assert "analytics.invoices" not in position_of(layers_of(findings))


def test_an_estate_with_no_copy_has_no_layers():
    undeclared = [base()["events"]]

    assert check(ontology_of({}), [], rules=ORDER) == []
    assert [
        f
        for f in check(ontology_of({}), undeclared, rules=ORDER)
        if f.rule == "order"
    ] == []


@pytest.mark.parametrize("read", ["analytics.incr", "Analytics.INCR"])
def test_a_contract_that_reads_its_own_relation_has_no_order_edge(read):
    # An incremental model names itself, and a Python node may read its own previous output: a
    # read of the contract's own relation orders nothing and is no cycle.
    state = {"incr": contract("core", "incr", reads=[read])}

    findings = estate_run(state, without_birth_copy())

    assert problems(findings) == []
    assert layers_of(findings) == [["analytics.incr"]]


def test_a_self_read_of_a_relation_two_contracts_hold_is_no_edge_to_the_other():
    # `a` and `b` are two contracts that write to one physical relation. A read of that relation
    # finds `a`, the lower id, so it is the relation `b` holds too: `b` reads itself, not `a`.
    state = {
        "a": contract("core", "a", physical_name="shared"),
        "b": contract(
            "core", "b", physical_name="shared", reads=["analytics.shared"]
        ),
    }

    findings = estate_run(state, without_birth_copy())

    assert problems(findings) == []
    assert layers_of(findings) == [["analytics.a", "analytics.b"]]


def test_the_read_of_another_contract_beside_a_self_read_keeps_its_edge():
    state = {
        "a": contract("core", "a", physical_name="shared"),
        "b": contract(
            "core",
            "b",
            physical_name="shared",
            reads=["analytics.Shared", "analytics.c"],
        ),
        "c": contract("core", "c"),
    }

    findings = estate_run(state, without_birth_copy())

    assert problems(findings) == []
    assert layers_of(findings) == [
        ["analytics.a", "analytics.c"],
        ["analytics.b"],
    ]


def test_the_lower_id_that_reads_the_shared_relation_is_a_self_read_too():
    state = {
        "a": contract(
            "core", "a", physical_name="shared", reads=["analytics.shared"]
        ),
        "b": contract("core", "b", physical_name="shared"),
    }

    findings = estate_run(state, without_birth_copy())

    assert problems(findings) == []
    assert layers_of(findings) == [["analytics.a", "analytics.b"]]


def test_a_self_reading_copy_is_placed_after_what_it_reads_like_any_other():
    state = base()
    set_custom(
        table(state["txns"]),
        "reads",
        ["analytics.events_sink", "analytics.txns"],
    )

    findings = estate_run(state)

    assert problems(findings) == []
    assert layers_of(findings) == layers_of(estate_run(base()))


def test_a_self_read_beside_a_real_cycle_reports_only_the_cycle():
    state = {
        "ping": contract(
            "core", "ping", reads=["analytics.ping", "analytics.pong"]
        ),
        "pong": contract("core", "pong", reads=["analytics.ping"]),
        "incr": contract("core", "incr", reads=["analytics.incr"]),
    }

    found = errors_of(estate_run(state))

    assert {f.subject: f.message for f in found} == {
        "analytics.ping": "cycle among ['analytics.ping', 'analytics.pong']",
        "analytics.pong": "cycle among ['analytics.ping', 'analytics.pong']",
    }
    assert all(f.contract_id == f.subject for f in found)


# The cycles.


def test_a_cycle_is_an_error_on_each_copy_in_it():
    state = base()
    set_custom(
        table(state["txns"]),
        "reads",
        ["analytics.events_sink", "analytics.revenue"],
    )

    findings = estate_run(state)

    found = errors_of(findings)
    assert [(f.rule, f.subject, f.contract_id) for f in found] == [
        ("order", "analytics.revenue", "analytics.revenue"),
        ("order", "analytics.txns", "analytics.txns"),
    ]
    for finding in found:
        assert finding.message == (
            "cycle among ['analytics.revenue', 'analytics.txns']"
        )
    assert [f for f in findings if f.severity == "info"] == []


def test_a_copy_that_is_only_downstream_of_a_cycle_is_no_part_of_it():
    state = base()
    set_custom(
        table(state["txns"]),
        "reads",
        ["analytics.events_sink", "analytics.revenue"],
    )
    state["report"] = contract(
        "finance", "report", reads=["analytics.revenue", "analytics.events"]
    )

    found = errors_of(estate_run(state))

    assert [f.subject for f in found] == ["analytics.revenue", "analytics.txns"]


def test_two_cycles_are_named_apart():
    state = {
        "ping": contract("core", "ping", reads=["analytics.pong"]),
        "pong": contract("core", "pong", reads=["analytics.ping"]),
        "a": contract("core", "a", reads=["analytics.c"]),
        "b": contract("core", "b", reads=["analytics.a"]),
        "c": contract("core", "c", reads=["analytics.b"]),
        "root": contract("core", "root"),
    }

    found = errors_of(estate_run(state))

    assert {f.subject: f.message for f in found} == {
        "analytics.ping": "cycle among ['analytics.ping', 'analytics.pong']",
        "analytics.pong": "cycle among ['analytics.ping', 'analytics.pong']",
        "analytics.a": "cycle among ['analytics.a', 'analytics.b', 'analytics.c']",
        "analytics.b": "cycle among ['analytics.a', 'analytics.b', 'analytics.c']",
        "analytics.c": "cycle among ['analytics.a', 'analytics.b', 'analytics.c']",
    }
    assert all(f.contract_id == f.subject for f in found)


# The birth copy.


def test_a_birth_copy_outside_the_check_is_a_warning():
    findings = estate_run(
        {"daily": contract("core", "daily_transactions", entity="Transaction")},
        born_in("kafka.card_events"),
    )

    assert [
        (f.severity, f.subject) for f in findings if f.severity != "info"
    ] == [("warning", "Transaction.born_in")]


def test_the_warning_of_a_birth_copy_outside_the_check_names_no_contract():
    findings = estate_run(base(), born_in("kafka.card_events"))

    [found] = [f for f in findings if f.severity == "warning"]

    assert (found.rule, found.subject, found.contract_id) == (
        "order",
        "Transaction.born_in",
        None,
    )
    assert "kafka.card_events" in found.message


def test_a_birth_copy_outside_the_check_leaves_its_copies_unordered():
    state = base()
    set_custom(table(state["txns"]), "reads", [])
    set_custom(table(state["events_sink"]), "reads", [])

    findings = estate_run(state, born_in("kafka.card_events"))

    assert [(f.severity, f.subject) for f in problems(findings)] == [
        ("warning", "Transaction.born_in")
    ]


def test_a_birth_copy_is_found_by_its_relation_whatever_its_case():
    findings = estate_run(base(), born_in("Analytics.Events"))

    assert problems(findings) == []


def test_a_birth_copy_is_found_by_its_physical_name():
    state = base()
    state["events"] = contract(
        "app",
        "events",
        entity="Transaction",
        physical_name="events_stream",
        columns=[("id", "Transaction.transaction_id")],
    )
    set_custom(table(state["events_sink"]), "reads", ["analytics.events_stream"])

    findings = estate_run(state, born_in("analytics.events_stream"))

    assert problems(findings) == []
    assert problems(estate_run(state, born_in("analytics.events"))) != []


def test_a_birth_copy_of_another_thing_is_an_error_on_that_copy():
    findings = estate_run(base(), born_in("analytics.seed_users"))

    [found] = errors_of(findings)

    assert (found.rule, found.subject, found.contract_id) == (
        "order",
        "analytics.seed_users",
        "analytics.seed_users",
    )
    assert (
        "Transaction.born_in names analytics.seed_users, "
        "which is a copy of User, not a copy of Transaction" in found.message
    )
    assert [f for f in problems(findings) if f is not found] == []


def test_a_birth_copy_that_is_an_aggregate_is_an_error_on_that_copy():
    state = base()
    state["daily"] = contract("core", "daily", grain=["day"])

    [found] = errors_of(estate_run(state, born_in("analytics.daily")))

    assert found.subject == found.contract_id == "analytics.daily"
    assert found.message == (
        "Transaction.born_in names analytics.daily, "
        "which is an aggregate, not a copy of Transaction"
    )


def test_a_copy_upstream_of_its_birth_copy_is_an_error():
    findings = estate_run(base(), born_in("analytics.events_sink"))

    [found] = errors_of(findings)

    assert (found.rule, found.subject, found.contract_id) == (
        "order",
        "analytics.events",
        "analytics.events",
    )
    assert found.message == (
        "is upstream of Transaction's birth copy analytics.events_sink"
    )
    assert [f for f in problems(findings) if f is not found] == []


def test_a_copy_upstream_through_a_chain_of_reads_is_an_error():
    findings = estate_run(base(), born_in("analytics.txns"))

    assert [f.subject for f in errors_of(findings)] == [
        "analytics.events",
        "analytics.events_sink",
    ]


def test_a_copy_not_downstream_of_its_birth_copy_is_a_warning():
    state = base()
    set_custom(table(state["txns"]), "reads", [])

    findings = estate_run(state)

    [found] = problems(findings)
    assert (found.severity, found.rule, found.subject, found.contract_id) == (
        "warning",
        "order",
        "analytics.txns",
        "analytics.txns",
    )
    assert found.message == (
        "copy of Transaction not downstream of its birth copy analytics.events: "
        "its order relative to the birth copy is not derivable"
    )


def test_a_copy_downstream_of_its_birth_copy_through_a_chain_is_ordered():
    state = base()
    state["later"] = contract(
        "core",
        "later",
        entity="Transaction",
        reads=["analytics.txns"],
        columns=[("id", "Transaction.transaction_id")],
    )

    assert problems(estate_run(state)) == []


def test_an_aggregate_is_not_ordered_against_a_birth_copy():
    state = base()
    state["daily"] = contract("core", "daily", grain=["day"])

    assert problems(estate_run(state)) == []


def test_a_thing_with_no_birth_copy_declared_is_not_ordered():
    state = base()
    set_custom(table(state["txns"]), "reads", [])

    assert problems(estate_run(state, without_birth_copy())) == []


def test_a_cycle_through_the_birth_copy_is_also_upstream_of_it():
    state = base()
    set_custom(table(state["events"]), "reads", ["analytics.events_sink"])
    cycle = "cycle among ['analytics.events', 'analytics.events_sink']"

    findings = estate_run(state)

    assert sorted((f.subject, f.message) for f in errors_of(findings)) == [
        ("analytics.events", cycle),
        ("analytics.events_sink", cycle),
        (
            "analytics.events_sink",
            "is upstream of Transaction's birth copy analytics.events",
        ),
    ]
    assert all(f.contract_id == f.subject for f in errors_of(findings))


# What every finding of the rule holds.


def broken_order():
    """An estate with a cycle, a birth copy of another thing and a copy upstream of its birth."""
    state = base()
    set_custom(
        table(state["txns"]),
        "reads",
        ["analytics.events_sink", "analytics.revenue"],
    )
    state["daily"] = contract("core", "daily", grain=["day"])
    return state


def test_every_error_of_the_rule_names_a_contract():
    ids = set()
    for relation in ("analytics.seed_users", "analytics.events_sink"):
        for state in (base(), broken_order()):
            for finding in estate_run(state, born_in(relation)):
                if finding.severity == "error":
                    ids.add(finding.contract_id)
                    assert finding.contract_id == finding.subject, finding
    assert None not in ids
    assert ids >= {"analytics.seed_users", "analytics.events", "analytics.txns"}


def test_every_severity_of_the_rule_is_written():
    state = base()
    set_custom(table(state["txns"]), "reads", [])

    severities = {
        f.severity
        for document in (None, born_in("analytics.seed_users"))
        for f in estate_run(state, document)
    }

    assert severities == set(SEVERITIES)


# The findings do not depend on how the estate was written down.


def test_findings_do_not_depend_on_the_order_of_the_contracts():
    state = broken_order()
    contracts = list(state.values())
    document = born_in("analytics.events_sink")

    forward = check(ontology_of(document), contracts, rules=ORDER)

    assert errors_of(forward)
    assert check(ontology_of(document), contracts[::-1], rules=ORDER) == forward
    assert (
        check(ontology_of(document), contracts[2:] + contracts[:2], rules=ORDER)
        == forward
    )


def test_findings_do_not_depend_on_the_order_of_the_things():
    document = born_in("analytics.events_sink")
    document["entities"]["Order"] = {
        "owner": "core",
        "born_in": "kafka.orders",
        "key": {"order_id": "int"},
    }
    reversed_document = copy.deepcopy(document)
    reversed_document["entities"] = dict(reversed(document["entities"].items()))
    state = broken_order()

    forward = estate_run(state, document)

    assert [f for f in forward if f.subject == "Order.born_in"]
    assert estate_run(state, reversed_document) == forward


def test_findings_do_not_depend_on_the_order_of_the_reads():
    state = base()
    reads = ["analytics.txns", "analytics.seed_users", "analytics.mystery"]
    set_custom(table(state["revenue"]), "reads", reads)
    before = estate_run(state)
    set_custom(table(state["revenue"]), "reads", reads[::-1])

    assert estate_run(state) == before


# `progress` and the other rules.


def test_progress_is_called_once_after_each_contract_of_the_rule():
    state = base()
    state["invoices"] = contract("billing", "invoices", entity="Invoice")
    calls = []

    check(
        ONT, list(state.values()), rules=ORDER, progress=lambda: calls.append(1)
    )

    assert len(calls) == len(state) - 1


def test_progress_is_called_for_each_contract_when_the_graph_has_a_cycle():
    state = broken_order()
    calls = []

    check(
        ONT, list(state.values()), rules=ORDER, progress=lambda: calls.append(1)
    )

    assert len(calls) == len(state)


def test_the_rule_runs_with_the_other_rules_of_a_release_and_changes_none_of_them():
    state = broken_order()
    contracts = list(state.values())
    others = tuple(rule for rule in RELEASE_RULES if rule != "order")

    together = check(ONT, contracts, rules=RELEASE_RULES)

    assert "order" in RELEASE_RULES
    assert [f for f in together if f.rule == "order"] == estate_run(state)
    assert [f for f in together if f.rule != "order"] == check(
        ONT, contracts, rules=others
    )


@pytest.mark.parametrize("relation", ["analytics.events", "kafka.card_events"])
def test_the_rule_does_not_change_the_contracts(relation):
    state = broken_order()
    before = copy.deepcopy(state)

    estate_run(state, born_in(relation))

    assert state == before
