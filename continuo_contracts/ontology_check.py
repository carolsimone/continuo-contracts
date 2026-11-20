"""The rules that check contracts against the ontology, over contracts in memory.

`check` takes an `Ontology` and stored contracts and returns the `Finding` of each rule it is
asked to run. `ontology_findings` runs the one rule that needs no contract, the `ontology` rule
over the file alone. Nothing is read from a file: the caller loads the ontology (`ontology.load`),
stores and resolves the contracts (`ontology.resolve`) and passes them in.

A `Finding` has a `severity` (`SEVERITIES`), the `rule` that wrote it (`RULES`), a `subject`, a
`message` and the `contract_id` of the contract it is about. The subject of a finding about a
contract is its `id`, and the subject of any other finding is a thing, `<Thing>.<property>`, a
service or a path of the ontology file. Every error of a rule in `RELEASE_RULES` names a contract:
the findings with no `contract_id` are the ones of the `ontology` rule, the information and the
warnings of `drift` and the information about a service.

====================  ==================================================================
rule                  what it checks
====================  ==================================================================
`ontology`            the file: owners, domains, references, `derived_from`, access rules
`contract`            a contract's domain, its entity or grain, `fixed`, `filter`, each
                      column's binding and the domains of its readers
`coverage`            each column has one binding, and a copy carries every key part
`quality`             a data-quality test names columns of the contract
`privacy`             a personal property is classified personal, and only one is
`access`              a contract reads only what its domain may read
`drift`               each property and each thing is held by a copy
====================  ==================================================================

`PER_SERVICE_RULES` are the rules that need no other service: run over one service's contracts,
they give the findings that the same rules give over the whole estate. `RELEASE_RULES` adds the
rules that need the estate, `access` and `drift` among them.

Only the contracts of a service that the ontology declares are copies, and the rules read only
those. A service with contracts that the ontology does not declare gets one `info` finding of
the rule `contract`, and each of its contracts that writes a binding gets one `warning` of that
rule. `check` writes these two for any `rules`. The contracts passed to `check` are stored forms
that `validate` accepted, already resolved where their service is declared: `check` does not
resolve them.

A copy is a contract of a declared service. Copies are found by their physical relation, the
`id` of the contract without its last name part, then `.` and the `physicalName` of its schema
object, in lower case, which is what another contract's `reads` names. When two contracts hold
one relation, the one with the lower `id` is the copy. `check` gives the same findings whatever
the order of `contracts`.
"""

import re

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts.ontology import Ontology, has_bindings
from continuo_contracts.vocabulary import continuo_values

SEVERITIES: Final[tuple[str, ...]] = ("error", "warning", "info")

# The rules, in the order the documentation lists them.
RULES: Final[tuple[str, ...]] = (
    "ontology",
    "contract",
    "coverage",
    "access",
    "order",
    "drift",
    "quality",
    "privacy",
)
# The rules that need nothing from another service.
PER_SERVICE_RULES: Final[tuple[str, ...]] = (
    "contract",
    "coverage",
    "quality",
    "privacy",
)
# The rules that a release runs over the whole estate: every rule but `ontology`.
RELEASE_RULES: Final[tuple[str, ...]] = (
    *PER_SERVICE_RULES,
    "access",
    "order",
    "drift",
)

_UNDECLARED_SERVICE: Final = (
    "not declared in the ontology: none of this service's contracts is checked"
)
_UNDECLARED_BINDINGS: Final = (
    "writes bindings, but its service is not declared in the ontology: "
    "they are kept as written and not checked"
)

# The binding properties of a column, in the order that picks the one a rule reads.
_COLUMN_BINDINGS: Final = ("property", "local", "aggregate")

_ENUM: Final = re.compile(r"enum\[(.*)\]")
_REF: Final = re.compile(r"ref\s+(\S+)")


@dataclass(frozen=True)
class Finding:
    """One thing a rule found.

    `severity` is one of `SEVERITIES` and `rule` one of `RULES`. `subject` is the `id` of the
    contract the finding is about, or a thing, `<Thing>.<property>`, a service or a path of the
    ontology file. `contract_id` is the `id` of that contract, and `None` for a finding that is
    not about one contract.
    """

    severity: str
    rule: str
    subject: str
    message: str
    contract_id: str | None


@dataclass(frozen=True)
class _Copy:
    """What the rules read of one contract of a declared service.

    `entity` is empty and `grain` has no item for a contract that writes none. `bindings` holds
    the columns that write a binding, with the first of `property`, `local` and `aggregate`
    that the column writes, and `kinds` lists, for each column, every one it writes.
    """

    id: str
    relation: str
    service: str
    domain: str | None
    entity: str
    grain: Sequence[Any]
    fixed: Mapping[str, Any]
    filter_: Mapping[str, Any]
    reads: Sequence[str]
    readers: Sequence[str]
    columns: Sequence[str]
    bindings: Mapping[str, Mapping[str, Any]]
    kinds: Mapping[str, Sequence[str]]
    classification: Mapping[str, Any]
    quality: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class _Estate:
    """The ontology and the copies that the rules of one `check` read.

    `copies` is sorted by `id`, and `by_relation` finds a copy by its physical relation.
    """

    ontology: Ontology
    entities: Mapping[str, Any]
    domains: Mapping[str, Any]
    copies: Sequence[_Copy]
    by_relation: Mapping[str, _Copy]


type _Pass = Callable[[_Estate, Callable[[], None]], list[Finding]]
type _CopyRule = Callable[[_Estate, _Copy], list[Finding]]


def _no_progress() -> None:
    """Do nothing: the default `progress` of `check`."""


def check(
    ontology: Ontology,
    contracts: Sequence[Mapping[str, Any]],
    *,
    rules: Sequence[str],
    progress: Callable[[], None] = _no_progress,
) -> list[Finding]:
    """Return the findings of `rules` over the stored `contracts`, sorted.

    `rules` names rules of `RULES` except `ontology`, which `ontology_findings` runs, and a rule
    is run once however often it is named. Raises `ValueError`, before any rule runs, for a name
    that is not a rule `check` can run. `contracts` are stored forms that `validate` accepted,
    resolved where their service is declared; the module docstring says which of them the rules
    read and what is said about the others. `progress` is called with no argument after each
    contract of each rule that runs.

    The findings are sorted by severity (`SEVERITIES`), rule, subject and message, and do not
    depend on the order of `contracts`. `contracts` is not changed.
    """
    selected = set(rules)
    unknown = sorted(selected - _PASSES.keys())
    if unknown:
        raise ValueError(
            f"check has no pass for the rule(s) {', '.join(unknown)}: "
            f"it runs {', '.join(_PASSES)}"
        )
    declared: list[Mapping[str, Any]] = []
    undeclared: dict[str, list[Mapping[str, Any]]] = {}
    for stored in sorted(contracts, key=_contract_id):
        service = _service(stored)
        if ontology.declares(service):
            declared.append(stored)
        else:
            undeclared.setdefault(service, []).append(stored)
    findings: list[Finding] = []
    for service, members in undeclared.items():
        findings.append(
            Finding("info", "contract", service, _UNDECLARED_SERVICE, None)
        )
        findings.extend(
            Finding(
                "warning",
                "contract",
                _contract_id(stored),
                _UNDECLARED_BINDINGS,
                _contract_id(stored),
            )
            for stored in members
            if has_bindings(stored)
        )
    estate = _estate(ontology, declared)
    for name, run in _PASSES.items():
        if name in selected:
            findings.extend(run(estate, progress))
    findings.sort(
        key=lambda f: (SEVERITIES.index(f.severity), f.rule, f.subject, f.message)
    )
    return findings


def ontology_findings(ontology: Ontology) -> list[Finding]:
    """Return the findings of the `ontology` rule over the file alone, in file order.

    The rule checks that each service names a declared domain, each thing an owner that is a
    declared domain, every `ref` and `derived_from` a thing and a property of it that exist,
    each access rule properties of its thing and readers that are declared domains, and that a
    personal property is named by an access rule. No finding names a contract: `contract_id` is
    `None`.
    """
    document = ontology.document
    principals: Mapping[str, Any] = document.get("principals", {})
    domains: Mapping[str, Any] = principals.get("domains", {})
    entities: Mapping[str, Any] = document.get("entities", {})
    findings = [
        _ontology_error(
            f"principals.services.{service}",
            f"domain {spec.get('domain')!r} is not declared",
        )
        for service, spec in principals.get("services", {}).items()
        if spec.get("domain") not in domains
    ]
    for name, thing in entities.items():
        findings.extend(_thing_findings(name, thing, entities, domains))
    return findings


def _ontology_error(subject: str, message: str) -> Finding:
    return Finding("error", "ontology", subject, message, None)


def _thing_findings(
    name: str,
    thing: Mapping[str, Any],
    entities: Mapping[str, Any],
    domains: Mapping[str, Any],
) -> list[Finding]:
    """Return the `ontology` findings about the thing `name`."""
    findings: list[Finding] = []
    if thing.get("owner") not in domains:
        findings.append(
            _ontology_error(
                name, f"owner {thing.get('owner')!r} is not a declared domain"
            )
        )
    for part in _key_parts(thing):
        target = _ref_of(thing, part)
        if target and target not in entities:
            findings.append(
                _ontology_error(
                    f"{name}.{part}",
                    f"key references {target}, which is not a thing",
                )
            )
    properties = _properties(thing)
    for prop, spec in properties.items():
        if spec.get("ref") and spec["ref"] not in entities:
            findings.append(
                _ontology_error(
                    f"{name}.{prop}",
                    f"references {spec['ref']}, which is not a thing",
                )
            )
        for source in spec.get("derived_from", []):
            why = _unresolved(entities, source)
            if why:
                findings.append(
                    _ontology_error(
                        f"{name}.{prop}", f"derived_from {source}: {why}"
                    )
                )
    covered: set[str] = set()
    for rule in thing.get("access", []):
        for prop in rule.get("properties", []):
            if prop not in properties:
                findings.append(
                    _ontology_error(
                        name,
                        f"access rule names {prop!r}, which is not a property",
                    )
                )
            covered.add(prop)
        findings.extend(
            _ontology_error(
                name, f"access rule reader {reader!r} is not a declared domain"
            )
            for reader in rule.get("readers", [])
            if reader not in domains
        )
    findings.extend(
        _ontology_error(
            f"{name}.{prop}",
            "personal property with no access rule: nobody can read it",
        )
        for prop, spec in properties.items()
        if spec.get("personal") and prop not in covered
    )
    return findings


# What the rules read of a thing.


def _key_parts(thing: Mapping[str, Any]) -> dict[str, str]:
    return {part: str(kind) for part, kind in thing.get("key", {}).items()}


def _properties(thing: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    properties: Mapping[str, Mapping[str, Any]] = thing.get("properties", {})
    return properties


def _fields(thing: Mapping[str, Any]) -> set[str]:
    return set(_key_parts(thing)) | set(_properties(thing))


def _enum_values(type_text: str) -> list[str] | None:
    """Return the values of an `enum[...]` type, or `None` for any other type."""
    found = _ENUM.fullmatch(type_text.strip())
    return (
        [value.strip() for value in found.group(1).split(",")] if found else None
    )


def _ref_of(thing: Mapping[str, Any], name: str) -> str | None:
    """Return the thing that the key part or property `name` references, if any."""
    parts = _key_parts(thing)
    if name in parts:
        found = _REF.fullmatch(parts[name].strip())
        return found.group(1) if found else None
    ref: str | None = _properties(thing).get(name, {}).get("ref")
    return ref


def _references(thing: Mapping[str, Any]) -> set[str]:
    return {ref for name in _fields(thing) if (ref := _ref_of(thing, name))}


def _reachable(entities: Mapping[str, Any], start: str) -> set[str]:
    """Return the things reachable from `start` through references, in either direction."""
    links: dict[str, set[str]] = {name: set() for name in entities}
    for name, thing in entities.items():
        for target in _references(thing):
            if target in links:
                links[name].add(target)
                links[target].add(name)
    seen, todo = {start}, [start]
    while todo:
        for following in links.get(todo.pop(), ()):
            if following not in seen:
                seen.add(following)
                todo.append(following)
    return seen


def _unresolved(entities: Mapping[str, Any], dotted: str) -> str | None:
    """Return `None` when `Thing` or `Thing.field` exists, else why it does not."""
    name, _, prop = dotted.partition(".")
    if name not in entities:
        return f"{name} is not a thing in the ontology"
    if prop and prop not in _fields(entities[name]):
        return f"{dotted} is not a property or key part of {name}"
    return None


def _is_personal(entities: Mapping[str, Any], dotted: str) -> bool:
    name, _, prop = dotted.partition(".")
    return bool(_properties(entities.get(name, {})).get(prop, {}).get("personal"))


# What the rules read of a contract.


def _contract_id(stored: Mapping[str, Any]) -> str:
    contract_id: str = stored["id"]
    return contract_id


def _service(stored: Mapping[str, Any]) -> str:
    service: str = continuo_values(stored.get("customProperties")).get(
        "service", ""
    )
    return service


def _estate(ontology: Ontology, declared: Sequence[Mapping[str, Any]]) -> _Estate:
    """Return the estate of the contracts `declared`, which are sorted by `id`."""
    copies = [_copy(stored) for stored in declared]
    by_relation: dict[str, _Copy] = {}
    for copy in copies:
        by_relation.setdefault(copy.relation, copy)
    principals: Mapping[str, Any] = ontology.document.get("principals", {})
    return _Estate(
        ontology,
        ontology.document.get("entities", {}),
        principals.get("domains", {}),
        copies,
        by_relation,
    )


def _copy(stored: Mapping[str, Any]) -> _Copy:
    contract_id = _contract_id(stored)
    table = stored["schema"][0]
    name = table["name"]
    relation = (
        f"{contract_id.removesuffix(f'.{name}')}."
        f"{table.get('physicalName') or name}"
    ).lower()
    values = continuo_values(table.get("customProperties"))
    raw_columns = table.get("properties", [])
    bindings: dict[str, Mapping[str, Any]] = {}
    kinds: dict[str, tuple[str, ...]] = {}
    for column in raw_columns:
        written = continuo_values(column.get("customProperties"))
        column_kinds = tuple(
            kind
            for kind in _COLUMN_BINDINGS
            if written.get(kind) is not None and written.get(kind) is not False
        )
        kinds[column["name"]] = column_kinds
        if column_kinds:
            first = column_kinds[0]
            bindings[column["name"]] = (
                written[first]
                if first == "aggregate"
                else {first: written[first]}
            )
    return _Copy(
        id=contract_id,
        relation=relation,
        service=_service(stored),
        domain=stored.get("domain"),
        entity=values.get("entity", ""),
        grain=values.get("grain", []),
        fixed=values.get("fixed", {}),
        filter_=values.get("filter", {}),
        reads=values.get("reads", []),
        readers=[
            role["role"]
            for role in stored.get("roles", [])
            if role.get("access") == "read"
        ],
        columns=[column["name"] for column in raw_columns],
        bindings=bindings,
        kinds=kinds,
        classification={
            column["name"]: column.get("classification") for column in raw_columns
        },
        quality=table.get("quality", []),
    )


def _error(rule: str, copy: _Copy, message: str) -> Finding:
    return Finding("error", rule, copy.id, message, copy.id)


def _warning(rule: str, copy: _Copy, message: str) -> Finding:
    return Finding("warning", rule, copy.id, message, copy.id)


# The `contract` rule.


def _contract_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    entities = estate.entities
    expected = estate.ontology.domain_of(copy.service)
    if copy.domain is not None and copy.domain != expected:
        findings.append(
            _error(
                "contract",
                copy,
                f"domain {copy.domain!r} is not {copy.service}'s domain {expected!r}",
            )
        )
    if bool(copy.entity) == bool(copy.grain):
        findings.append(
            _error(
                "contract",
                copy,
                "declare exactly one of continuo.entity (a copy of a thing) "
                "or continuo.grain (an aggregate)",
            )
        )
    thing: Mapping[str, Any] | None = None
    linked: set[str] = set()
    if copy.entity:
        if copy.entity not in entities:
            findings.append(
                _error(
                    "contract",
                    copy,
                    f"entity {copy.entity} is not a thing in the ontology",
                )
            )
        else:
            thing = entities[copy.entity]
            linked = _reachable(entities, copy.entity)
    if thing is not None:
        findings.extend(_selection_findings(copy, thing))
    for column, binding in copy.bindings.items():
        dotted = binding.get("property") or binding.get("from")
        if not dotted:
            continue
        why = _unresolved(entities, dotted)
        if why:
            findings.append(
                _error("contract", copy, f"column {column} -> {dotted}: {why}")
            )
        elif (
            thing is not None
            and "property" in binding
            and dotted.split(".")[0] not in linked
        ):
            findings.append(
                _warning(
                    "contract",
                    copy,
                    f"column {column} comes from {dotted.split('.')[0]}, which "
                    f"{copy.entity} does not reach through any reference: "
                    "the join is not a declared relationship",
                )
            )
    findings.extend(
        _error("contract", copy, f"reader {reader!r} is not a declared domain")
        for reader in copy.readers
        if reader not in estate.domains
    )
    return findings


def _selection_findings(copy: _Copy, thing: Mapping[str, Any]) -> list[Finding]:
    """Return the findings about the `fixed` and `filter` of a copy of `thing`."""
    findings: list[Finding] = []
    keys = _key_parts(thing)
    for part, value in copy.fixed.items():
        if part not in keys:
            findings.append(
                _error(
                    "contract",
                    copy,
                    f"fixed {part!r} is not a key part of {copy.entity}",
                )
            )
            continue
        allowed = _enum_values(keys[part])
        if allowed is not None and str(value) not in allowed:
            findings.append(
                _error(
                    "contract",
                    copy,
                    f"fixed {part}={value!r} is not one of {allowed}",
                )
            )
    fields = _fields(thing)
    findings.extend(
        _error(
            "contract",
            copy,
            f"filter on {part}, which is not a property of {copy.entity}",
        )
        for part in copy.filter_
        if part not in fields
    )
    return findings


# The `coverage` rule: each column has one binding, and a copy carries every key part.


def _held(thing_name: str, copy: _Copy) -> set[str]:
    """Return the key parts and properties of `thing_name` that `copy` carries or selects."""
    held = {
        binding["property"].partition(".")[2]
        for binding in copy.bindings.values()
        if binding.get("property", "").partition(".")[0] == thing_name
    }
    return held | set(copy.fixed) | set(copy.filter_)


def _coverage_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    unbound = [name for name in copy.columns if name not in copy.bindings]
    if unbound:
        findings.append(
            _warning(
                "coverage",
                copy,
                f"columns {unbound} carry no continuo.property, "
                "continuo.local or aggregate binding",
            )
        )
    thing = estate.entities.get(copy.entity)
    if thing is not None:
        held = _held(copy.entity, copy)
        missing = [part for part in _key_parts(thing) if part not in held]
        if missing:
            findings.append(
                _error(
                    "coverage",
                    copy,
                    f"carries no column for key part(s) {missing} of {copy.entity}, "
                    "and does not fix or filter them",
                )
            )
    findings.extend(
        _error(
            "coverage",
            copy,
            f"column {name} carries more than one binding: {list(kinds)}",
        )
        for name, kinds in copy.kinds.items()
        if len(kinds) > 1
    )
    return findings


# The `access` rule.


def _access_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    reader = estate.ontology.domain_of(copy.service)
    for relation in copy.reads:
        target = estate.by_relation.get(relation)
        if target is None:
            findings.append(
                _warning(
                    "access",
                    copy,
                    f"reads {relation}, which no checked contract binds: "
                    "its owner and grants are unknown",
                )
            )
            continue
        owner = estate.entities.get(target.entity, {}).get("owner")
        if (
            reader in (estate.ontology.domain_of(target.service), owner)
            or reader in target.readers
        ):
            continue
        findings.append(
            _error(
                "access",
                copy,
                f"{reader} reads {relation} without a grant: add {reader} to "
                f"{relation}'s readers in {target.service}'s contract",
            )
        )
    return findings


# The `drift` rule: what no copy holds. It reads the copies of a thing together, so it
# collects first and reports after.


def _drift_rule(estate: _Estate, progress: Callable[[], None]) -> list[Finding]:
    held: dict[str, set[str]] = {}
    copies: dict[str, list[str]] = {}
    for copy in estate.copies:
        if copy.entity:
            held.setdefault(copy.entity, set()).update(_held(copy.entity, copy))
            copies.setdefault(copy.entity, []).append(copy.id)
        progress()
    scope = {estate.ontology.domain_of(copy.service) for copy in estate.copies}
    findings: list[Finding] = []
    for name, thing in estate.entities.items():
        if name not in copies:
            if thing.get("owner") in scope:
                findings.append(
                    Finding(
                        "info",
                        "drift",
                        name,
                        "no checked contract binds this thing",
                        None,
                    )
                )
            continue
        findings.extend(
            Finding(
                "warning",
                "drift",
                f"{name}.{prop}",
                f"no copy holds it (checked {copies[name]})",
                None,
            )
            for prop in _properties(thing)
            if prop not in held[name]
        )
    return findings


# The `quality` rule: a data-quality test's parameters that end in `_attribute` name columns.


def _quality_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    for test in copy.quality:
        if test.get("engine") != "data-quality":
            continue
        implementation = test.get("implementation")
        if not isinstance(implementation, Mapping):
            findings.append(
                _error(
                    "quality",
                    copy,
                    f"test {test.get('name')!r}: the data-quality engine needs an "
                    "implementation mapping with enabled, granularity and params, "
                    f"not a {type(implementation).__name__}",
                )
            )
            continue
        params = implementation.get("params") or {}
        if not isinstance(params, Mapping):
            findings.append(
                _error(
                    "quality",
                    copy,
                    f"test {test.get('name')!r}: params must be a mapping, "
                    f"not a {type(params).__name__}",
                )
            )
            continue
        findings.extend(
            _error(
                "quality",
                copy,
                f"test {test.get('name')!r}: {key} names {value!r}, "
                f"which is not a column of {copy.id}",
            )
            for key, value in params.items()
            if key.endswith("_attribute") and value not in copy.columns
        )
    return findings


# The `privacy` rule.


def _privacy_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    for name in copy.columns:
        personal = _is_personal(
            estate.entities, copy.bindings.get(name, {}).get("property", "")
        )
        classified = copy.classification.get(name) == "personal"
        if personal and not classified:
            findings.append(
                _error(
                    "privacy",
                    copy,
                    f"column {name} holds a personal property but is not "
                    "classified personal: column grants would not protect it",
                )
            )
        elif classified and not personal:
            findings.append(
                _warning(
                    "privacy",
                    copy,
                    f"column {name} is classified personal but holds no "
                    "personal property of the ontology",
                )
            )
    return findings


def _per_copy(rule: _CopyRule) -> _Pass:
    """Return the pass that runs `rule` over each copy and calls `progress` after each."""

    def run(estate: _Estate, progress: Callable[[], None]) -> list[Finding]:
        findings: list[Finding] = []
        for copy in estate.copies:
            findings.extend(rule(estate, copy))
            progress()
        return findings

    return run


# The rules `check` can run, by name.
_PASSES: Final[Mapping[str, _Pass]] = MappingProxyType(
    {
        "contract": _per_copy(_contract_rule),
        "coverage": _per_copy(_coverage_rule),
        "access": _per_copy(_access_rule),
        "drift": _drift_rule,
        "quality": _per_copy(_quality_rule),
        "privacy": _per_copy(_privacy_rule),
    }
)
