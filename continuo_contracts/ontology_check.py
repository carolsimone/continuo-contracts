"""The rules that check contracts against the ontology, over contracts in memory.

`check` takes an `Ontology` and stored contracts and returns the `Finding` of each rule it is
asked to run. `ontology_findings` runs the one rule that needs no contract, the `ontology` rule
over the file alone. Nothing is read from a file: the caller loads the ontology (`ontology.load`),
stores and resolves the contracts (`ontology.resolve`) and passes them in.

A `Finding` has a `severity` (`SEVERITIES`), the `rule` that wrote it (`RULES`), a `subject`, a
`message` and the `contract_id` of the contract it is about. The subject of a finding about a
contract is its `id`, and the subject of any other finding is a thing, `<Thing>.<property>`, a
project or a path of the ontology file. Every error of a rule in `RELEASE_RULES` names a contract:
the findings with no `contract_id` are the ones of the `ontology` rule, the information and the
warnings of `drift`, the layers and the warning about a `born_in` of `order`, and the
information about a project.

====================  ==================================================================
rule                  what it checks
====================  ==================================================================
`ontology`            the file: owners, domains, references, `derived_from`, access rules
`contract`            a contract's domain, its entity or grain, `fixed`, `filter`, each
                      column's binding and the domains of its readers
`coverage`            each column has one binding, and a copy carries every key part
`quality`             a data-quality test, on the table or on a column, names columns of
                      the contract
`privacy`             a personal property is classified personal, and only one is
`access`              a contract reads only what its domain may read
`order`               the copies are in layers, none in a cycle, and a thing's copies follow
                      its birth copy
`drift`               each property and each thing is held by a copy
====================  ==================================================================

`PER_PROJECT_RULES` are the rules that need no other project: run over one project's contracts,
they give the findings that the same rules give over the whole estate. `RELEASE_RULES` adds the
rules that need the whole estate: `access`, `order` and `drift`.

Only the contracts of a project that the ontology declares are copies, and the rules read only
those. A project with contracts that the ontology does not declare gets one `info` finding of
the rule `contract`, and each of its contracts that writes a binding gets one `warning` of that
rule. `check` writes these two for any `rules`. The contracts passed to `check` are stored forms
that `validate` accepted, already resolved where their project is declared: `check` does not
resolve them.

A copy is a contract of a declared project. Copies are found by their physical relation, the
`id` of the contract without its last name part, then `.` and the `physicalName` of its schema
object, which is what another contract's `reads` names. The case of the `id`, of the names and of
a read does not matter: all are compared in lower case. When two contracts hold one relation,
both are copies, and a read of that relation finds the one with the lower `id`. `check` gives the
same findings whatever the order of `contracts`.

The `order` rule reads the `reads` of each copy, whatever its kind: a copy that reads nothing,
as a Python node does, is in the first layer. A read that no checked contract holds adds no edge
and no finding of this rule, because the `access` rule warns about it, and neither does a read of
the contract's own relation, which an incremental model makes. A copy that is in a cycle of two
or more copies is an error. When a thing names its birth copy in `born_in`, the other copies of the
thing must be downstream of it: one upstream of it is an error and one with no path from it is
a warning. A `born_in` that no checked contract holds is one warning on the thing, and the
copies of that thing are not ordered against a birth copy. The layers are one `info` finding,
`copy graph`, written when no copy is in or downstream of a cycle.
"""

import re

from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts.ontology import Ontology, _reference, has_bindings
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
# The rules that need nothing from another project.
PER_PROJECT_RULES: Final[tuple[str, ...]] = (
    "contract",
    "coverage",
    "quality",
    "privacy",
)
# The rules that a release runs over the whole estate: every rule but `ontology`.
RELEASE_RULES: Final[tuple[str, ...]] = (
    *PER_PROJECT_RULES,
    "access",
    "order",
    "drift",
)

_UNDECLARED_PROJECT: Final = (
    "not declared in the ontology: none of this project's contracts is checked"
)
_UNDECLARED_BINDINGS: Final = (
    "writes bindings, but its project is not declared in the ontology: "
    "they are kept as written and not checked"
)

# The binding properties of a column, in the order that picks the one a rule reads.
_COLUMN_BINDINGS: Final = ("property", "local", "aggregate")

_ENUM: Final = re.compile(r"enum\[(.*)\]")


@dataclass(frozen=True)
class Finding:
    """One thing a rule found.

    `severity` is one of `SEVERITIES` and `rule` one of `RULES`. `subject` is the `id` of the
    contract the finding is about, or a thing, `<Thing>.<property>`, a project or a path of the
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
    """What the rules read of one contract of a declared project.

    `entity` is empty and `grain` has no item for a contract that writes none. `bindings` holds
    the columns that write a binding, with the first of `property`, `local` and `aggregate`
    that the column writes, and `kinds` lists, for each column, every one it writes. `quality`
    lists each quality rule of the table and of its columns with the name of the column it sits
    on, `None` for a rule of the table.
    """

    id: str
    relation: str
    project: str
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
    quality: Sequence[tuple[str | None, Mapping[str, Any]]]


@dataclass(frozen=True)
class _Estate:
    """The ontology and the copies that the rules of one `check` read.

    `copies` is sorted by `id`, and `by_relation` finds a copy by its physical relation. `graph`
    links each thing to the things it references and to those that reference it, and is built
    once for the estate. `reachable` answers from it, once for each thing that it is asked about.
    """

    ontology: Ontology
    entities: Mapping[str, Any]
    domains: Mapping[str, Any]
    copies: Sequence[_Copy]
    by_relation: Mapping[str, _Copy]
    graph: Mapping[str, Set[str]]
    _reached: dict[str, Set[str]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def reachable(self, start: str) -> Set[str]:
        """Return the things reachable from `start` through references, in either direction."""
        found = self._reached.get(start)
        if found is None:
            seen, todo = {start}, [start]
            while todo:
                for following in self.graph.get(todo.pop(), ()):
                    if following not in seen:
                        seen.add(following)
                        todo.append(following)
            found = self._reached[start] = frozenset(seen)
        return found


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
    resolved where their project is declared; the module docstring says which of them the rules
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
        project = _project(stored)
        if ontology.declares(project):
            declared.append(stored)
        else:
            undeclared.setdefault(project, []).append(stored)
    findings: list[Finding] = []
    for project, members in undeclared.items():
        findings.append(
            Finding("info", "contract", project, _UNDECLARED_PROJECT, None)
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

    The rule checks that each project names a declared domain, each thing an owner that is a
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
            f"principals.projects.{project}",
            f"domain {spec.get('domain')!r} is not declared",
        )
        for project, spec in principals.get("projects", {}).items()
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
    for part, referenced in _key_references(thing).items():
        if referenced not in entities:
            findings.append(
                _ontology_error(
                    f"{name}.{part}",
                    f"key references {referenced}, which is not a thing",
                )
            )
    properties = _properties(thing)
    references = _property_references(thing)
    for prop, spec in properties.items():
        target = references.get(prop)
        if target is not None and target not in entities:
            findings.append(
                _ontology_error(
                    f"{name}.{prop}",
                    f"references {target}, which is not a thing",
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


def _key_references(thing: Mapping[str, Any]) -> dict[str, str]:
    """Return the thing that each key part references, by part, for the parts that reference one."""
    return {
        part: target
        for part, kind in _key_parts(thing).items()
        if (target := _reference(kind))
    }


def _property_references(thing: Mapping[str, Any]) -> dict[str, str]:
    """Return the thing that each property references, by property, in either spelling."""
    return {
        prop: target
        for prop, spec in _properties(thing).items()
        if (target := _reference(spec.get("type"), spec.get("ref")))
    }


def _references(thing: Mapping[str, Any]) -> set[str]:
    return {
        *_key_references(thing).values(),
        *_property_references(thing).values(),
    }


def _reference_graph(entities: Mapping[str, Any]) -> dict[str, Set[str]]:
    """Return, for each thing, the things it references and the things that reference it."""
    links: dict[str, set[str]] = {name: set() for name in entities}
    for name, thing in entities.items():
        for target in _references(thing):
            if target in links:
                links[name].add(target)
                links[target].add(name)
    return dict(links)


def _unresolved(entities: Mapping[str, Any], dotted: str) -> str | None:
    """Return `None` when `Thing` or `Thing.field` exists, else why it does not.

    A dot with nothing after it is a field with no name, not the thing alone.
    """
    name, dot, prop = dotted.partition(".")
    if name not in entities:
        return f"{name} is not a thing in the ontology"
    if dot and prop not in _fields(entities[name]):
        return f"{dotted} is not a property or key part of {name}"
    return None


def _is_personal(entities: Mapping[str, Any], dotted: str) -> bool:
    name, _, prop = dotted.partition(".")
    return bool(_properties(entities.get(name, {})).get(prop, {}).get("personal"))


# What the rules read of a contract.


def _contract_id(stored: Mapping[str, Any]) -> str:
    contract_id: str = stored["id"]
    return contract_id


def _project(stored: Mapping[str, Any]) -> str:
    project: str = continuo_values(stored.get("customProperties")).get(
        "project", ""
    )
    return project


def _estate(ontology: Ontology, declared: Sequence[Mapping[str, Any]]) -> _Estate:
    """Return the estate of the contracts `declared`, which are sorted by `id`."""
    copies = [_copy(stored) for stored in declared]
    by_relation: dict[str, _Copy] = {}
    for copy in copies:
        by_relation.setdefault(copy.relation, copy)
    principals: Mapping[str, Any] = ontology.document.get("principals", {})
    entities: Mapping[str, Any] = ontology.document.get("entities", {})
    return _Estate(
        ontology,
        entities,
        principals.get("domains", {}),
        copies,
        by_relation,
        _reference_graph(entities),
    )


def _copy(stored: Mapping[str, Any]) -> _Copy:
    contract_id = _contract_id(stored)
    table = stored["schema"][0]
    name = table["name"]
    # A dbt node writes its id in lower case and keeps the case of its name.
    schema = contract_id.lower().removesuffix(f".{name}".lower())
    relation = f"{schema}.{table.get('physicalName') or name}".lower()
    values = continuo_values(table.get("customProperties"))
    raw_columns = table.get("properties", [])
    bindings: dict[str, Mapping[str, Any]] = {}
    kinds: dict[str, tuple[str, ...]] = {}
    quality: list[tuple[str | None, Mapping[str, Any]]] = [
        (None, rule) for rule in table.get("quality", [])
    ]
    for column in raw_columns:
        quality.extend(
            (column["name"], rule) for rule in column.get("quality", [])
        )
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
        project=_project(stored),
        domain=stored.get("domain"),
        entity=values.get("entity", ""),
        grain=values.get("grain", []),
        fixed=values.get("fixed", {}),
        filter_=values.get("filter", {}),
        reads=values.get("reads", []),
        # The canonical form drops an empty `role`, so a reader may have no name here: the
        # `contract` rule reports it.
        readers=[
            role.get("role", "")
            for role in stored.get("roles", [])
            if role.get("access") == "read"
        ],
        columns=[column["name"] for column in raw_columns],
        bindings=bindings,
        kinds=kinds,
        classification={
            column["name"]: column.get("classification") for column in raw_columns
        },
        quality=quality,
    )


def _error(rule: str, copy: _Copy, message: str) -> Finding:
    return Finding("error", rule, copy.id, message, copy.id)


def _warning(rule: str, copy: _Copy, message: str) -> Finding:
    return Finding("warning", rule, copy.id, message, copy.id)


# The `contract` rule.


def _contract_rule(estate: _Estate, copy: _Copy) -> list[Finding]:
    findings: list[Finding] = []
    entities = estate.entities
    expected = estate.ontology.domain_of(copy.project)
    if copy.domain is not None and copy.domain != expected:
        findings.append(
            _error(
                "contract",
                copy,
                f"domain {copy.domain!r} is not {copy.project}'s domain {expected!r}",
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
    linked: Set[str] = frozenset()
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
            linked = estate.reachable(copy.entity)
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
    reader = estate.ontology.domain_of(copy.project)
    for relation in copy.reads:
        target = estate.by_relation.get(relation.lower())
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
            reader in (estate.ontology.domain_of(target.project), owner)
            or reader in target.readers
        ):
            continue
        findings.append(
            _error(
                "access",
                copy,
                f"{reader} reads {relation} without a grant: add {reader} to "
                f"{relation}'s readers in {target.project}'s contract",
            )
        )
    return findings


# The `order` rule: copies are placed from the copies they read, never from the thing. It reads
# the whole graph, so `progress` is called as each copy's reads are resolved to edges. A read of
# the copy's own relation is no edge: an incremental model names itself, and dbt itself refuses
# a `ref` that closes a cycle.


def _closure(start: str, step: Mapping[str, set[str]]) -> set[str]:
    """Return the copies reached from `start` by one or more `step`s."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        for following in step[todo.pop()]:
            if following not in seen:
                seen.add(following)
                todo.append(following)
    return seen


def _layers(edges: Mapping[str, set[str]]) -> list[list[str]]:
    """Return the layers of the copies: layer n reads only from layers before n.

    A copy in a cycle, or downstream of one, has no layer.
    """
    pending = dict(edges)
    layers: list[list[str]] = []
    done: set[str] = set()
    while pending:
        ready = sorted(
            copy_id for copy_id, reads in pending.items() if reads <= done
        )
        if not ready:
            break
        layers.append(ready)
        done.update(ready)
        for copy_id in ready:
            del pending[copy_id]
    return layers


def _cycles(
    edges: Mapping[str, set[str]], unplaced: set[str]
) -> dict[str, list[str]]:
    """Return the copies of the cycle of each copy in a cycle, among the `unplaced` copies."""
    reach = {copy_id: _closure(copy_id, edges) for copy_id in unplaced}
    return {
        copy_id: sorted(
            other
            for other in reach[copy_id]
            if other in reach and copy_id in reach[other]
        )
        for copy_id in reach
        if copy_id in reach[copy_id]
    }


def _order_rule(estate: _Estate, progress: Callable[[], None]) -> list[Finding]:
    edges: dict[str, set[str]] = {}
    for copy in estate.copies:
        edges[copy.id] = {
            target.id
            for relation in copy.reads
            if relation.lower() != copy.relation
            and (target := estate.by_relation.get(relation.lower()))
        }
        progress()
    layers = _layers(edges)
    placed = {copy_id for layer in layers for copy_id in layer}
    findings: list[Finding] = []
    if len(placed) < len(edges):
        cycles = _cycles(edges, set(edges) - placed)
        findings.extend(
            _error("order", copy, f"cycle among {cycles[copy.id]}")
            for copy in estate.copies
            if copy.id in cycles
        )
    elif layers:
        listing = " | ".join(
            f"{number}: {', '.join(layer)}"
            for number, layer in enumerate(layers, start=1)
        )
        findings.append(Finding("info", "order", "copy graph", listing, None))
    downstream: dict[str, set[str]] = {copy_id: set() for copy_id in edges}
    for copy_id, reads in edges.items():
        for read in reads:
            downstream[read].add(copy_id)
    for name, thing in estate.entities.items():
        if thing.get("born_in"):
            findings.extend(
                _birth_findings(estate, name, thing["born_in"], edges, downstream)
            )
    return findings


def _birth_findings(
    estate: _Estate,
    name: str,
    birth: str,
    edges: Mapping[str, set[str]],
    downstream: Mapping[str, set[str]],
) -> list[Finding]:
    """Return the findings about the birth copy `birth` of the thing `name`."""
    subject = f"{name}.born_in"
    found = estate.by_relation.get(birth.lower())
    if found is None:
        return [
            Finding(
                "warning",
                "order",
                subject,
                f"{birth} is not held by any checked contract: the copies of "
                f"{name} are not ordered against a birth copy",
                None,
            )
        ]
    if found.entity != name:
        what = f"a copy of {found.entity}" if found.entity else "an aggregate"
        return [
            _error(
                "order",
                found,
                f"{subject} names {birth}, which is {what}, not a copy of {name}",
            )
        ]
    findings: list[Finding] = []
    before = _closure(found.id, edges)
    after = _closure(found.id, downstream)
    for copy in estate.copies:
        if copy.entity != name or copy.id == found.id:
            continue
        if copy.id in before:
            findings.append(
                _error(
                    "order",
                    copy,
                    f"is upstream of {name}'s birth copy {birth}",
                )
            )
        elif copy.id not in after:
            findings.append(
                _warning(
                    "order",
                    copy,
                    f"copy of {name} not downstream of its birth copy {birth}: "
                    "its order relative to the birth copy is not derivable",
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
    scope = {estate.ontology.domain_of(copy.project) for copy in estate.copies}
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
    for column, test in copy.quality:
        if test.get("engine") != "data-quality":
            continue
        where = "" if column is None else f" on column {column}"
        implementation = test.get("implementation")
        if not isinstance(implementation, Mapping):
            findings.append(
                _error(
                    "quality",
                    copy,
                    f"test {test.get('name')!r}{where}: the data-quality engine needs an "
                    "implementation mapping with enabled, granularity and params, "
                    f"not a {type(implementation).__name__}",
                )
            )
            continue
        params = implementation.get("params")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            findings.append(
                _error(
                    "quality",
                    copy,
                    f"test {test.get('name')!r}{where}: params must be a mapping, "
                    f"not a {type(params).__name__}",
                )
            )
            continue
        findings.extend(
            _error(
                "quality",
                copy,
                f"test {test.get('name')!r}{where}: {key} names {value!r}, "
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
        "order": _order_rule,
        "drift": _drift_rule,
        "quality": _per_copy(_quality_rule),
        "privacy": _per_copy(_privacy_rule),
    }
)
