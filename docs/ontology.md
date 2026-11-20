# The ontology

How do I describe my data in an ontology file, bind each contract to it and check the bindings? An
[ontology](glossary.md#ontology) names who acts on data and what the data is about. A contract's
[bindings](glossary.md#binding) say which [thing](glossary.md#thing) its table is a
[copy](glossary.md#copy) of and which property of that thing each column holds. `ontology.load`
reads the file, `ontology.resolve` [fills](#resolve) a stored contract with what the file knows,
and `ontology_check.check` [checks](#the-rules) contracts against it.

## The file

An ontology file is one YAML document in two parts, read as
[strict YAML](python-contracts.md#strict-yaml) reads it. `principals` names who acts on data, and
`entities` names what the data is about: each thing has an owner, a key, properties and access
rules. `ontology.load(text)` returns an `ontology.Ontology` and refuses any key outside this table,
or a value of the wrong type, with an `OntologyError` ([errors](errors.md)). `description`, a
string, is allowed on every mapping in the table.

| Mapping | Keys |
|---|---|
| root | `principals`, `entities` |
| `principals` | `domains`, `services`, `teams`, `roles`, `people`, each a mapping of names |
| a domain | `owner_team` |
| a service | `domain`, `kind` |
| a team or a role | `idp_group` |
| `people` | `source` |
| a thing, under `entities` | `owner`, `born_in`, `key`, `properties`, `access` |
| `key` | each key part, with its type as a string |
| a property | `type`, `ref`, `personal`, `currency`, `derived_from`, `nullable` |
| an access rule | `properties`, `readers`, `purpose` |

Every value is a string except `personal` and `nullable` (true or false), the lists `derived_from`,
`properties` and `readers` (of strings) and `access` (of access rules). A name is not blank, and
the name of a thing, of a key part and of a property is one that `<Thing>.<field>` can carry, so
`vocabulary.is_name` accepts it: no whitespace, control character, dot or slash. The mappings of
names (`domains`, `services`, `teams`, `roles`, `entities`, `key` and `properties`) take no
`description`, because it would be a name. A type is `int`, `string`, `timestamp`, `date`,
`money`, `decimal`, `bool`, `enum[a, b]` or `ref <Thing>`, and a property may write `ref: <Thing>`
instead of a type. Both spellings are one reference, to the rules and to `resolve` alike, and when
a property writes both, `ref` wins.

```python
>>> from continuo_contracts import ontology
>>> text = """\
... principals:
...   domains:
...     shop: {owner_team: shop-data}
...   services:
...     shop: {domain: shop, kind: python}
... entities:
...   Customer:
...     owner: shop
...     key: {customer_id: int}
...     properties:
...       email: {type: string, personal: true}
...       plan: {type: "enum[free, paid]"}
...     access:
...       - {properties: [email], readers: [shop]}
... """
>>> onto = ontology.load(text)
>>> onto.services(), onto.declares("shop"), onto.declares("web"), onto.domain_of("shop")
(('shop',), True, False, 'shop')
>>> ontology.load("entities: {User: {owner: core, personnal: {}}}")
Traceback (most recent call last):
  ...
continuo_contracts.errors.OntologyError: entities.User.personnal: unknown key, the keys of a thing are access, born_in, description, key, owner, properties
>>> ontology.load("entities: {User: {properties: {'display name': {}}}}")
Traceback (most recent call last):
  ...
continuo_contracts.errors.OntologyError: entities.User.properties: a property name has no whitespace, control character, dot or slash, got 'display name'

```

`Ontology.document` is the parsed file and `Ontology.sha256` the SHA-256 of its text. `declares`
says whether the file names a service under `principals.services`, `domain_of` gives its domain,
and `services` lists them. The message of an `OntologyError` is `<path>: <reason>` with a dotted
path (`entities.User.personnal`, `[<index>]` for a list item, `<root>` for the document itself,
where a text that strict YAML refuses is reported). `load` reports the first failure in file order.

## Bind a contract

A contract's bindings are seven `continuo` [custom properties](validation.md#custom-properties),
named in `ontology.BINDING_PROPERTIES`. Four sit on the schema object, which is the table, and
three on a column. A binding is written in camelCase, `vendor: continuo`.

| Binding | Sits on | Value | Says |
|---|---|---|---|
| `entity` | the table | the name of a thing | the table is a copy of that thing |
| `grain` | the table | a non-empty list of strings | the table is an aggregate with these dimensions |
| `fixed` | the table | key part to a string, number or boolean | the copy holds only rows with these key parts |
| `filter` | the table | field to a string, number or boolean | the copy holds only rows with these values |
| `property` | a column | `<Thing>.<field>` | the column holds that key part or property |
| `local` | a column | `true` | no thing owns the column |
| `aggregate` | a column | a mapping of `from`, `agg`, `where`, `period` | the column aggregates a thing's field |

A table declares exactly one of `entity` and `grain`. A column writes one of `property`, `local`
and `aggregate`, and `aggregate` has a `from` (`<Thing>` or `<Thing>.<field>`) or a `period`,
`agg` and `period` being strings and `where` a mapping of field to value. `validate` checks the
shape of each value, for Python and dbt contracts alike ([validation](validation.md)); whether a
thing or a field exists is for the [rules](#the-rules).

- **A Python author** writes the properties in the `customProperties` of the schema object and of
  the column ([Python contracts](python-contracts.md#bind-to-an-ontology)).
- **A dbt team** writes `entity`, `grain`, `fixed` and `filter` under the node's
  `config.meta.continuo` and `property`, `local`, `from`, `agg`, `where` and `period` under the
  column's `meta.continuo`. The generator turns `from`, `agg`, `where` and `period` into the one
  `aggregate` value ([dbt contracts](dbt-contracts.md#bind-to-an-ontology)).

## Resolve

`ontology.resolve(stored, ontology)` returns the [stored form](glossary.md#stored-form) filled with
what the ontology knows, a new dict. It fills a contract only when its `service` property is a
service the ontology declares. For any other contract, one with no service included, it returns
`stored` in the canonical form and nothing more. The result is always in the canonical form
([contract hash](contract-hash.md#walk)), so the caller hashes it as it hashes any stored form.

`resolve` fills a gap and never replaces a value, so a declared value wins and `resolve` of its own
result is the same result. A stored form holds no ODCS default, so a default counts as no value. It
fills:

- the root `domain`: the domain of the service;
- on a table whose `entity` is a thing, a link to the thing, the `authoritativeDefinitions` entry
  `{type: ontology, url: "continuo-ontology:<Thing>"}` (`ontology.LINK_PREFIX` is the prefix);
- on a column with no binding, when the thing has a key part or a property of that name, the
  binding `property: <Thing>.<name>` (a column whose name `vocabulary.is_name` refuses gets none);
- on a column whose `property` names a field of a thing that exists, the link
  `continuo-ontology:<Thing>.<field>`, the `logicalType` of the field's type, `classification:
  personal` for a personal field, and for a key part of the table's own thing, when no column
  has `primaryKey: true`, `primaryKey: true` and its `primaryKeyPosition`, the place of the part in
  the thing's `key`, from 1. A key part is looked up before a property of the same name, which
  gives the type and the place, and it is personal when that property is;
- on a column with an `aggregate` that has a `from` or a `period` and whose `from` exists,
  `semanticType: measure` and the `logicalType` of `from` when it has an `agg` (`integer` for
  `count`), or `semanticType: dimension` and `logicalType: date` when it has a `period` and no
  `agg`.

| Type | `logicalType` |
|---|---|
| `int` | `integer` |
| `string`, `enum[...]` | `string` |
| `timestamp` | `timestamp` |
| `date` | `date` |
| `money`, `decimal` | `number` |
| `bool` | `boolean` |
| `ref <Thing>` | the type of that thing's key, when the key has one part |

Any other type gives no `logicalType`. A key of one column carries no `primaryKeyPosition`, whatever
filled the key, as every stored form has it. A binding that does not resolve, a thing or a field
the ontology lacks or a value of the wrong shape, stays as written and fills nothing, and so does a
column that holds more than one of `property`, `local` and `aggregate`: `resolve` fills and the
[rules](#the-rules) judge. A `Thing.` with no field, an `aggregate` with neither a `from` nor a
`period` and an `agg` that is not a string are of the wrong shape. It reads the top-level columns
only.

The contract below binds `customers` to `Customer`. The author writes `entity` and `local`, and
`resolve` finds the key, the personal column and the types:

```python
>>> from continuo_contracts import contract, yaml_strict
>>> doc = yaml_strict.load("""\
... apiVersion: v3.2.0
... kind: DataContract
... id: analytics.customers
... version: "0"
... team: {name: shop-data}
... customProperties:
...   - {vendor: continuo, property: schedule, value: daily}
...   - {vendor: continuo, property: criticality, value: CORE}
...   - {vendor: continuo, property: script, value: scripts/customers.py}
...   - {vendor: continuo, property: readQueries, value: {c: select * from raw.customers}}
... schema:
...   - name: customers
...     physicalType: table
...     customProperties:
...       - {vendor: continuo, property: entity, value: Customer}
...     properties:
...       - {name: customer_id, physicalType: int}
...       - {name: email, physicalType: text}
...       - name: signups
...         physicalType: int
...         customProperties: [{vendor: continuo, property: local, value: true}]
... """)
>>> stored = contract.normalise_python(doc, service="shop", reads=["raw.customers"])
>>> resolved = ontology.resolve(stored, onto)
>>> resolved["domain"], resolved["schema"][0]["authoritativeDefinitions"]
('shop', [{'type': 'ontology', 'url': 'continuo-ontology:Customer'}])
>>> for column in resolved["schema"][0]["properties"]:
...     print(column["name"], column.get("primaryKey"), column.get("classification"))
customer_id True None
email None personal
signups None None
>>> ontology.resolve(resolved, onto) == resolved
True
>>> contract.contract_hash(resolved) != contract.contract_hash(stored)
True

```

## Strip

`ontology.strip(stored)` returns the stored form without its ontology part, a new dict in the
canonical form, and `ontology.has_bindings(stored)` says whether a stored form writes any of the
seven bindings. The ontology part is the seven bindings and every `authoritativeDefinitions` entry
whose `url` starts with `continuo-ontology:`, on the contract, the schema object or a column. An
entry with another `url`, such as an author's own link, stays, and so does every value `resolve`
filled: `strip` removes what a team or the ontology wrote as a binding or a link, and fills nothing.
A stored form with no ontology part comes back as it is. It reads the top-level columns only.

```python
>>> ontology.has_bindings(stored), ontology.has_bindings(ontology.strip(resolved))
(True, False)
>>> ontology.strip(resolved)["schema"][0]["properties"][1]["classification"]
'personal'

```

## The rules

`ontology_check.check(ontology, contracts, rules=...)` returns the `Finding`s of the named rules
over stored contracts, sorted by severity, rule, subject and message, whatever the order of
`contracts`. A finding has a `severity` (one of `ontology_check.SEVERITIES`: `error`, `warning`,
`info`), the `rule`, a `subject`, a `message` and the `contract_id` of the contract it is about.
The contracts are stored forms that `validate` accepted, resolved where their service is declared:
`check` does not resolve them. `ontology_check.RULES` lists the rules, and `rules` names those to
run, except `ontology`; any other name raises `ValueError` before a rule runs.

| Rule | Checks | Findings |
|---|---|---|
| `ontology` | the file alone: each service names a declared domain, each thing an owner that is a declared domain, each `ref` (either spelling) a thing, each `derived_from` a thing or a `Thing.field` that exists, each access rule properties of its thing and readers that are domains, each personal property an access rule | errors |
| `contract` | the domain is the service's; exactly one of `entity` and `grain`; the thing exists; `fixed` names key parts, with a value an `enum` allows; `filter` names fields; each binding names a field that exists; each reader is a declared domain | errors, and a warning for a column that comes from a thing the entity does not reach through a reference |
| `coverage` | each column has a binding, no column has two, and a copy carries each key part or fixes or filters it | a warning for unbound columns, errors for the others |
| `quality` | a data-quality test's `*_attribute` parameter names a column of the contract, for a test on the table or on a column, and its `params` is a mapping | errors |
| `privacy` | a column that holds a personal property is classified `personal`, and no other column is | an error for the first, a warning for the second |
| `access` | a contract reads a table only when its service's domain is the table's domain or its thing's owner, or the table's contract lists that domain as a reader | an error for a read without a grant, a warning for a table no checked contract holds |
| `order` | the copies fall into layers by what they read, none in a cycle (a contract's read of its own relation, as an incremental model makes, is no edge), and a thing's copies follow its birth copy, the one `born_in` names | errors for a cycle, a copy upstream of the birth copy or a `born_in` that names a copy of another thing, warnings for a copy with no path from the birth copy or a `born_in` no contract holds, and one `info` finding with the layers |
| `drift` | each property of a thing is held by a copy | a warning for a property no copy holds, an `info` finding for a thing that no contract binds, when its owner is a domain of the checked services |

`ontology_check.ontology_findings(ontology)` runs the `ontology` rule, which needs no contract. The
rules fall in two sets. `PER_SERVICE_RULES` (`contract`, `coverage`, `quality`, `privacy`) need no
other service: over one service's contracts they give the findings that the whole estate gives.
`RELEASE_RULES` adds `access`, `order` and `drift`, which need the contracts of every service, so
only continuo's [release](glossary.md#release) runs them. The [lite check](check-your-project.md)
runs `PER_SERVICE_RULES` and reports the errors of `ontology` as one line.

A copy is a contract of a declared service. Copies are found by their physical relation, the
`<schema>.<table>` that another contract's `reads` names, and the case of a name or a read does not
matter. In the example below the contract's domain is not the one its service has, and `resolve`
leaves it (a declared value wins), so `contract` reports it:

```python
>>> from continuo_contracts import ontology_check
>>> doc["domain"] = "billing"
>>> wrong = ontology.resolve(contract.normalise_python(doc, service="shop", reads=[]), onto)
>>> for finding in ontology_check.check(onto, [wrong], rules=ontology_check.PER_SERVICE_RULES):
...     print(finding.severity, finding.rule, finding.subject, finding.message)
error contract analytics.customers domain 'billing' is not shop's domain 'shop'
>>> ontology_check.PER_SERVICE_RULES, ontology_check.RELEASE_RULES[-3:]
(('contract', 'coverage', 'quality', 'privacy'), ('access', 'order', 'drift'))

```

## Scope

Only the contracts of a service that the ontology declares are copies, and the rules read only
those. A contract of any other service is kept as written. `check` writes, whatever `rules` names,
one `info` finding of the rule `contract` for each such service, `not declared in the ontology: none
of this service's contracts is checked`, and one `warning` for each of its contracts that writes a
binding, `writes bindings, but its service is not declared in the ontology: they are kept as written
and not checked`. `resolve` returns such a contract unfilled.

## The commands

`continuo-contracts ontology check FILE [--json]` loads one ontology file and runs the `ontology`
rule alone. It logs each finding at the level of its severity as `<subject>: <message>`, then how
many errors the file has:

```bash
$ continuo-contracts ontology check ontology.yaml
ERROR principals.services.web: domain 'web' is not declared
ERROR Customer.email: personal property with no access rule: nobody can read it
INFO 2 errors in the ontology file
```

With `--json` stdout holds one document and nothing else, `ok` being true when no finding is an
error. Logs go to stderr.

```json
{"ok": false, "findings": [
  {"severity": "error", "rule": "ontology", "subject": "principals.services.web",
   "message": "domain 'web' is not declared"},
  {"severity": "error", "rule": "ontology", "subject": "Customer.email",
   "message": "personal property with no access rule: nobody can read it"}]}
```

| Exit | Meaning |
|---|---|
| 0 | the file has no error |
| 1 | the file has an error finding |
| 2 | the command could not run: a usage error, a file it cannot read, or a file that does not load (not UTF-8, not strict YAML, or a key or value the format refuses) |

`continuo-contracts dbt check ... --ontology FILE` reads the bindings of a dbt project's contracts
against the file, with `PER_SERVICE_RULES` ([check your project](check-your-project.md#bindings)).
A file that does not load stops the command with status 2 before dbt runs. A file that loads but
has errors gives the one line `the ontology file has <n> error(s): run continuo-contracts ontology
check <file>` and no contract is read against it.
