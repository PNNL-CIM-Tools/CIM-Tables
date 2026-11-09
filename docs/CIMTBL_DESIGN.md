# The `.cimtbl` format and the `cimtables` package

This is the design of record for `.cimtbl` and for the `cimtables` package that implements it. It describes the
code as built. It replaces CIM-Builder's `cimbuilder/development/CIMTBL_DESIGN.md`, which was written before the
implementation and before `cimtables` was split out of CIM-Builder; that file's git history is the record of how
the design got here. The changes are summarized under [Changes from the original design](#changes-from-the-original-design).

## Goals

In priority order: **auditability**, **understandability**, **ease of use for humans and LLMs**.

A `.cimtbl` file is to CIM what a `.dss` file is to OpenDSS, except that its vocabulary *is* the CIM profile.
Column headers are literally CIM attribute and association names, units are on the column header, and each row is
one CIM object. There is no native-to-CIM mapping to maintain, and a model can be read, diffed and hand-edited as
text.

## Where it sits

```
cimprofiles  →  cim-graph  →  cimtables  →  cimbuilder
(CIM classes,   (GraphModel,   (.cimtbl      (ObjectBuilders, feeder/
 CIMUnit)        mRID index,    parse/bind,   substation assembly)
                 model scope)   documents)
```

`cimtables` must never import `cimbuilder`. Its consumers:

- **CIM-Builder**: `ObjectBuilder.from_table` delegates to `bind_row`, and builders write through `graph_write`.
- **CIMHub importers**: they build `.cimtbl` artifacts with `document.py` and load them with `load_model`.
  The behaviours they depend on are listed in CIMHub's
  `cimhub_core/src/cimhub_core/development/CIMTABLES_REQUESTS.md` ("Please keep these stable").
- **CIMTBL-Models** (`PNNL-CIM-Tools/CIMTBL-Models`): git-tracked `.cimtbl` grid models.

## Pipeline

```
.cimtbl text ──parser──▶ RawRecord ──validate──▶ <Class>Row ──bind_row──▶ live cim-graph object
               (fixed      (cells still    (typed, checked      (graph_write is the
                grammar)    text)           against the          only thing that
                                            profile)             mutates the graph)
```

| Module | Role |
|---|---|
| `grammar.lark`, `parser.py` | The fixed grammar; text → `RawRecord`s plus a `Header` of settings; resolves `Import`. |
| `records.py` | `RawRecord`: one row, every cell still `str` or `None`, plus units and source file/line. `ImportRecord`: an `Import` as `parse_text` returns it, before `parse_file` splices the file in. |
| `validate.py` | Reflects on the CIM profile to build a `<Class>Row` dataclass; coerces cells; raises before any graph change. |
| `synthetic.py` | The only non-reflective vocabulary: the DSL's connectivity and phase columns. |
| `binder.py` | `bind_row`: one validated row → one live object, classifying every field from the profile alone. |
| `connectivity.py` | Terminals, ConnectivityNodes/TopologicalNodes, PhaseCodes and per-phase children. |
| `graph_write.py` | `set_attr`, `set_assc`, `add_to_graph`, `resolve`: the only functions that mutate objects or the network. |
| `impedance.py` | The base for pu/percent impedances. |
| `units.py` | `Qty(value, unit, base)`, the carrier for a `(unit)` column. |
| `document.py` | The parser's inverse: a `Document` of cell text → deterministic `.cimtbl` text. |

## Load-bearing decisions

| Decision | Choice | Why |
|---|---|---|
| Grammar scope | Fixed and profile-agnostic: settings, `Import`, `Object`, `Table`, `(unit)` suffixes, cells, comments. It knows no CIM class names. | It never changes when the profile does. Never add CIM-specific rules to it. |
| Validation | Reflection on the live profile module: no LinkML, no curated schema. Any class in the profile is legal. | There's no second schema to drift from the profile. |
| Fail before mutating | A row is validated into a typed `<Class>Row` before anything touches the graph; errors carry `file:line`. | A bad file is rejected with a line number, not half-built. |
| One generic binder | `bind_row` classifies every field from the profile. There is no per-class dispatch table. | New classes and new profiles work with no code. |
| One mutation point | Only `graph_write` calls `setattr` or `network.add_to_graph`. | Unit binding, reverse associations and mRID indexing can't be forgotten at a call site. |
| Identity | Rows are keyed and referenced by `mRID`, unique within the model (any class). The UUID is seeded from the mRID under the file's model scope. | Renaming (`name`) never changes a UUID; references are unambiguous. |
| A model is a function of its text | Everything that affects the result is in the file: profile, model scope, `BasePower`. | No hidden dependence on env vars or library defaults. |
| Value + unit | Every unit-bearing column carries its own `Qty`; pu/percent are converted once, in `graph_write`. | No hand-scaled `%Z → ohm`; `r`, `x` and `bch` each keep their own unit. |

## The format

### Statements

```
Profile = cim18v17
Authority = https://example.com/models
Model = ieee13

Import wire_infos.cimtbl

Object BasePower: mRID=sbase, basePower (MVA)=100

Table ACLineSegment: mRID, node1, node2, phases, length (ft), PerLengthImpedance, BaseVoltage, EquipmentContainer=fdr
    650632, RG60, 362, ABCN, 2000, mtx601, base_4160
    632645, 632,  645, BC,   500,  mtx603, base_4160
```

- **Settings**: `Profile =` (a profile short name such as `cim18v17`), `Authority =` and `Model =` (the model
  scope UUIDs are seeded under). Each may be declared at most once per file and Import chain. See
  [Settings](#settings).
- **`Import <file>.cimtbl`**: textual composition. The imported file's records are spliced in at that point. The
  path is relative to the importing file.
- **`Object <Class>: k=v, ...`**: one object.
- **`Table <Class>: col, col, ...`** then one indented row per object.
- **`#`** starts a comment, either on its own line or after content. Blank and comment-only lines are ignored
  everywhere.

A keyword is only lexed as one where its statement's shape follows (`Table X:`, `Profile =`, `Import x`), so a
row cell such as `Object_1`, `Profiles` or `Tablet` is just a value.

**`Object` vs `Table` is about cardinality, not raggedness.** Use `Object` for a singleton (`EnergySource`,
`BasePower`) and `Table` for many objects, even ragged ones, since blank cells are legal. The two forms build
exactly the same objects.

### Cells

- A **blank** cell is unset. So is a cell that is only whitespace. Unquoted cells are trimmed.
- A **quoted** cell is CSV-style: `"..."` takes `,` `#` `(` `)` literally, `""` is one embedded quote, and a
  quoted `""` is the empty string, not unset. Source names are kept verbatim, never sanitized:
  `"#2_ACSR_6/1"`, `"DEFAULT (DC)"`.
- A cell can't hold a newline.
- Booleans are `true`/`false`/`1`/`0` (any case). Enum cells are the enum's value (`D`, `Y`, `discharging`).

### Table header defaults

A header column may carry a default, `EquipmentContainer=fdr`. A row's cells fill the columns *without* defaults,
in header order. Any extra trailing cells then override the defaulted columns, also in header order:

```
Table EnergyConsumer: mRID, node, p (kW), q (kVAr), BaseVoltage, EquipmentContainer=fdr
    load_671, 671,       1155, 660,  base_4160
    load_sub, SourceBus, 2000, 1000, base_115, sub      # overrides EquipmentContainer
```

### Units

The unit is on the column header and applies to every cell in it: `basePower (MVA)`, `r (ohm/mile)`,
`length (ft)`. A unit-bearing column with no `(unit)` is read in SI base units (W, V, ohm, S, m).

`(pu)` and `(percent)` on an impedance or admittance column are relative. Their base is described under
[Relative impedances](#relative-impedances). `(pu system)` / `(percent system)` put that column on the system base
even on a rated class. `system` is the only base word, and only `pu`/`percent` take it.

### Row key and identity

An `IdentifiedObject` row must have an `mRID`. The object is built from it (`cim_cls(mRID=...)`), so its UUID is
seeded from the mRID and the model scope; renaming it (`name`) keeps the UUID. mRIDs are unique **within the
model, not per class**, and reusing one in any class raises. Translators prefix them to keep them distinct (bus
`671` vs load `ld_671`). That convention is the translator's, not enforced beyond uniqueness.

A CIM 18 Identity-only class has no `mRID` (e.g. `PhaseImpedanceData`). It takes an optional `identifier`
column instead, which seeds the UUID the same way. Without one the UUID is random. Identity-only objects aren't
indexed, because nothing references them.

`name` is optional, non-unique data like any other attribute.

### References

A reference column is named by the association (`BaseVoltage`, `PerLengthImpedance`, `PowerTransformer`) and its
cell holds the target's mRID. The association's range is often an abstract class (`PerLengthImpedance`,
`EquipmentContainer`): any subclass matches, and an mRID that belongs to an unrelated class raises.

**Targets must be declared before the rows that reference them.** References are resolved as each row is bound.
Where the profile serializes only one side of an association (e.g. cim18v17's
`TransformerWinding.FromTransformerLug` is `serialize=False`), write it from the other side.

### Same class, several header shapes

One class may appear in several tables with different columns. For example, an `ACLineSegment` can be given
by `bus1, bus2, r (pu), x (pu), bch (pu)` or by `node1, node2, phases, length, PerLengthImpedance`. Which fields
are present is what selects the representation; rows carry no shape flag.

### Connectivity columns

These are the only column names that aren't CIM attributes (`synthetic.py`). They are exact names only: a
prefix rule would capture real attributes such as `TopologicalNode.busName`.

| Column | Terminal goes to | Terminal `sequenceNumber` |
|---|---|---|
| `node`, `node1` | ConnectivityNode | 1 |
| `node2` | ConnectivityNode | 2 |
| `bus`, `bus1` | TopologicalNode | 1 |
| `bus2` | TopologicalNode | 2 |

- The sequence number comes from the column name, not header order.
- `node1` and `bus1` together put one Terminal on both a ConnectivityNode and a TopologicalNode, as PSS/E RAW's
  node-breaker substation data does.
- A node or bus that was never declared is created on first use, with the cell value as its mRID.
- Terminals are named `<mRID>_T<n>`.
- A `TransformerEnd` row's `node`/`bus` puts its Terminal on the end's transformer, with
  `sequenceNumber = endNumber`, and points `end.Terminal` at it. The transformer is the end's one
  ConductingEquipment reference, found by type hint: `PowerTransformerEnd.PowerTransformer`,
  `AutoTransformerEnd.AutoTransformer`. That reference and `endNumber` must be set.
- **Containers.** The equipment's `EquipmentContainer` is the row's own column, else the container
  `bind_row` was given, else `network.container`. Every node touched gets that container as its
  `ConnectivityNodeContainer` unless it already has one. A TransformerEnd's nodes take its transformer's
  container.

A real CIM attribute of the same name always wins over a DSL column.

The connectivity columns are shorthand, never the only way. Terminals may be written out as their own rows,
either after their equipment (`Table Terminal: mRID, ConductingEquipment, ConnectivityNode, sequenceNumber`) or
first, with an `Update Table Terminal: mRID, ConductingEquipment` once the equipment exists. A consumer that
needs a model's Terminals reads them from the bound model, not from the text: the `.cimtbl` contract is what
binding makes.

### Phases

`phases` lists conductors in order: `ABCN`, `CB`, `s1s2N`. The order is the conductor order, e.g. a phase
impedance matrix's row order.

**No `phases` is balanced three-phase.** A blank or absent `phases` cell makes no `<Class>Phase` children and
leaves `Terminal.phases` unset, which CIM defines as ABC ("If the attribute is missing, three phases (ABC) shall
be assumed", `Terminal.phases`). That is how a balanced device is written, on any class, including one with no
`<Class>Phase` child class (`SeriesCompensator`). A consumer that wants every Terminal's phases explicit can fill
the missing ones in after loading. `phases=ABC` means three separate phases, a per-phase device.

- Every Terminal gets the canonical `PhaseCode` of the set (`CB` → `BC`, `s1s2N` → `s12N`).
- One `<Class>Phase` child is made per token, carrying the normative `SinglePhaseKind` and a `sequenceNumber`
  where the class has one. The child class is found by walking the MRO (`LinearShuntCompensator` →
  `LinearShuntCompensatorPhase`, `Breaker` → `SwitchPhase`). Children are named `<mRID>_<phase>`.
- **Delta shunts** (`phaseConnection=D`) get one child per line-to-line branch, `N` ignored. The child's
  `phase` is the from-phase (normative CIM: "connected from the indicated phase to the next logical non-neutral
  phase", A→B→C→A, s1→s2). Where the class has the `phases` extension (cim18v17 `EnergyConsumerPhase`), it also
  gets the branch PhaseCode: branch CA is `phase=C, phases=AC`. Children are named `<mRID>_<BC>`.
- **Switches whose sides differ** take `phaseSide1=AB, phaseSide2=AC` instead of `phases`. The sides pair by
  position (A→A, B→C) and each Terminal gets its own side's PhaseCode. On a class whose child has a single
  `phase`, differing sides raise.
- A `TransformerEnd`'s phases go on its Terminal only. An `AutoTransformerEnd` has a real `phase` (a PhaseCode),
  so it takes `phase` instead of `phases`, and its Terminal gets the same PhaseCode; a `phases` column on it
  raises. The `VoltageRegulator` itself has no per-phase child class, so `phases` on it raises (a delta bank
  would be the case that needs one).
- The shorthand applies only where the class has no real `phases` attribute. `Terminal.phases` and
  `EnergyConsumerPhase.phases` are set literally.

Per-phase children may also be written as their own table (`EnergyConsumerPhase` rows with an `EnergyConsumer`
reference) for per-phase data.

### Matrix impedance is literal

Matrix impedance is one `PhaseImpedanceData` row per matrix element, referencing its
`PerLengthPhaseImpedance`. Compact `rmatrix`/`xmatrix` sugar is deliberately not supported: every element stays
a visible, addressable row, and the format stays purely reflective.

### Templates are associations

There is no `Template` column. A reusable template is a real CIM association: `ACLineSegment.WireConfiguration`
for lines, `TransformerTank.TankConfiguration` for transformers.

### Relative impedances

A `pu`/`percent` value on an ohm-typed attribute is multiplied by `z_base = U²/S`; on a siemens-typed one it is
divided by it (one base for both). Relative units on other attributes (`PerCent`) convert like any other unit.

The base is chosen by **class shape, never by which values happen to be set**:

1. **A rated class** uses its own rating: the row's object if its class is rated (`PowerTransformerEnd`), else
   the target of one of its single-valued `From*` references (`TransformerMeshImpedance.FromWinding`). A class
   with `baseU`/`baseS` uses those (`TransformerWinding`: coil voltage, common tank kVA), otherwise
   `ratedU`/`ratedS`, each side on its own: `AutoTransformerEnd` has `baseU` but only `ratedS`, so it uses
   `baseU`/`ratedS` (coil voltage, through rating). Both must be set.
2. **Any other class** (`ACLineSegment`, `EnergySource`) uses the system base: its `BaseVoltage` (or `nomU`
   where the class has it) and the system base power.

`(pu system)` always takes step 2, for source data already on SBASE (PSS/E transformer impedances).

The system base power comes from a bound `Object BasePower: basePower (MVA)=...`, which must be declared **before**
any row that uses it. A `BasePower` with no `basePower`, or a second one that disagrees, raises. The result never
depends on cim-graph's `CIMG_SYSTEM_MVA_BASE` or its 100 MVA default.

The ratings, `BaseVoltage` and `From*` references must be set before the conversion, so `bind_row` writes
relative values last. The rationale and the open IEC WG13 question are in
`.development/PERCENT_IMPEDANCE_BASE.md`.

### Settings

The parser reads the settings but applies none of them; `load` / `load_model` do.

- **Within an Import chain**, a setting is declared at most once per file. An imported file's setting fills one
  the importer left unset. One that disagrees with an already-set value raises at the Import line, as does an
  Import cycle. A file imported from two branches is not a cycle (but its duplicate mRIDs raise at bind).
- **`Profile =`** names the CIM profile the rows are written against. `load_model` writes it to
  `CIMG_CIM_PROFILE` before building its model. `load` / `load_text` require it to be the network's own profile.
  With no `Profile =`, the environment's `CIMG_CIM_PROFILE` is used; with neither, validation fails. Only short
  names fit the grammar, so a comma-spec merged profile must come from the environment.
- **`Authority =` / `Model =`** replace that half of cim-graph's model scope (`set_model_scope`) before binding,
  so UUIDs are seeded per model: bus `671` in IEEE13 and in IEEE123 get different UUIDs. `write_xml`'s model
  header carries the scope and the XML reader restores it. An undeclared half is left as it is.

Both the profile and the model scope are **process-global**.

## Validation

`validate(record, profile=None)` turns a `RawRecord` into a `<Class>Row` or raises `CimtblValidationError`.

**Profile resolution.** The active profile (`CIMG_CIM_PROFILE`, or an explicit `profile` equal to it) is resolved
through cim-graph's `get_cim_profile()`, so the module is the same object as `network.cim`, comma-spec merged
profiles included. An explicit profile that differs from the environment is imported directly, resolved by
`cimprofiles.profile_module` as cim-graph does, without touching process state.

**Reflection.** Every dataclass field of the CIM class is a column, except:

- reverse/derived associations (`metadata['serialize'] is False`, e.g. `Terminals`), which are excluded;
- `mRID`/`identifier`, which are the row key.

Each remaining field is classified from its type hint:

- **quantity**: the hint contains a `CIMUnit` subclass. The cell becomes a `Qty(value, unit, base)`.
- **reference**: the hint contains a CIM dataclass. The cell stays an unresolved mRID string.
- **scalar**: anything else. The cell is coerced to `str`/`int`/`float`/`bool`/the enum.

The synthetic columns are added to every row class, unless the class has a real attribute of that name. Row
classes are built on demand and cached per (profile, class).

**Errors** name the file and line: an unknown class, an unknown column (with the closest valid name suggested), a
missing `mRID`, an `mRID` on an Identity-only class, a cell that doesn't coerce, a base word on a non-relative
unit.

## Binding

`bind_row(network, row, *, obj=None, container=None)` builds the object from the row key (unless a builder passes
`obj`), then sorts every set field into exactly one bucket, purely from the profile:

| Field | Goes to |
|---|---|
| a connectivity column | `connectivity.add_connectivity` |
| `phases` / `phaseSide1` / `phaseSide2` | `connectivity.add_phase_children` |
| `EquipmentContainer` | resolved, then used as the container |
| any other reference | resolved, then `set_assc` (an unresolved mRID raises) |
| a relative `Qty` | `set_attr`, deferred to the end |
| everything else, `name` included | `set_attr` |

The order is fixed: scalars, then references (a TransformerEnd's Terminal needs its PowerTransformer), then
connectivity and phases, then relative values (their base needs the ratings and references), then
`add_to_graph`.

A `ValueError`/`TypeError` binding a row read from a file is re-raised as `CimtblBindError`, prefixed with the
row's `file:line`. It subclasses both, so existing `except` clauses still catch it, and it keeps the original as
`__cause__`.

## The graph-write core

`graph_write` holds the only functions that mutate a cim-graph object or the network. Every other module, and
CIM-Builder's builders, route through it.

- **`set_attr(obj, part, attr, value, *, system_base=None)`** enforces the field's declared type, as cim-graph's
  own `create_value` does. A bool field takes only a `bool` (never the string `'false'`), and a bool is never a
  number. A number on a CIMUnit field is read as SI base. A CIMUnit is converted into the field's own
  (per-profile) CIMUnit class, and pint raises on a dimension mismatch. A `Qty` is converted from its unit; a
  relative one goes through the impedance base. Text parsing is not its job: that's `validate`.
- **`set_assc(obj, part, assc, target)`** writes both sides of an association, finding the reverse from the
  field's `metadata['inverse']`, so the graph is never one-sided. List sides append once (dedup by identity),
  scalar sides overwrite. `set_attr` on an association, or `set_assc` on a plain attribute, raises.
- **`add_to_graph(network, obj)`** adds the object and indexes its mRID; cim-graph raises on a reused mRID. A
  `BasePower` also sets `network.base_power`, under the rules above.
- **`resolve(network, cls, mRID)`** is cim-graph's `find_by_mRID` plus an `isinstance` check.
  `set_assc_from_mrid` is `resolve` + `set_assc` for builders.

**The `part` argument** is a profile module checked *by class name only*: `attr` must be a field of `part`'s
class named `type(obj).__name__`. A part-scoped module (a profile's `connectivity` slice) lets a builder's
`add_<part>` method enforce that it writes only that slice's fields. Passing the merged `network.cim` is the
documented no-op check used at the reflective call sites (`binder`, `connectivity`), where `validate` has already
decided what a row may carry.

## Entry points

- **`load(path, network)`**: parse, validate and bind a file onto an existing network. Returns the bound objects
  in file order.
- **`load_text(text, network, source_file='<text>')`**: the same for a string. An `Import` raises, since it needs a
  file to resolve against.
- **`load_model(path, model_cls=None)`**: builds the model too. It applies `Profile =` and the model scope, then
  binds every row into an in-memory `NodeBreakerModel` (`connection=None`) that has **no container**, so
  `network.container` never leaks into an `EquipmentContainer` the file didn't declare. The result:
  - the file's one `Feeder` → a `FeederModel` with it as container;
  - else its one `Substation` → a `NodeBreakerModel` with it as container;
  - else a `NodeBreakerModel` with no container (e.g. a lone padmount).

  `model_cls=BusBranchModel` builds a `BusBranchModel` instead, its container the file's one
  `GeographicalRegion`, else None.
- **`bind_row`**: for builders that produce rows themselves (`ObjectBuilder.from_table`).

### Errors

| Error | Raised for | Location |
|---|---|---|
| `CimtblParseError` (`ValueError`) | syntax; a missing Import, an Import cycle, a conflicting Import setting, a duplicated setting | `<file>:<line>:<col>:`, or `<file>:<line>:` at the Import. `<file>` is the file the error is in, imported files included. |
| `CimtblValidationError` (`ValueError`) | unknown class/column, missing key, bad cell; a `Profile =` that isn't the network's | `<file>:<line>:` |
| `CimtblBindError` (`ValueError`, `TypeError`) | unresolved reference, mRID reused, missing rating or `BasePower`, type mismatch, … | `<file>:<line>:` |

## Writing `.cimtbl`

### Documents (`document.py`)

An importer builds a `Document` of cell *text* (`Table`, `ObjectStatement`, `Import`; `None` is blank, `''` is
quoted empty, optional comments on documents, tables and rows). Turning typed values into text (`repr(float)`,
`true`/`false`, enum names) is the importer's job. `format_document` gives deterministic text:

- the order is the document comment, the settings, then the statements, with one blank line between each and a
  trailing newline;
- table rows are indented 4 spaces, and each cell but the last is padded after its comma to the column's widest
  cell;
- a cell is quoted if it holds any of `,()#"`, has leading or trailing whitespace, is `''`, or starts like a
  statement; a newline raises.

`write_documents(DocumentSet(master, parts), dir)` writes `<name>.cimtbl` for each document (UTF-8, `\n`) and
returns the master's path. It writes nothing if a name repeats, an Import is unknown, a part is never imported, or
`dir` holds a stale `.cimtbl`. CIMHub diffs this output byte for byte.

### The cim-graph → `.cimtbl` writer (not built yet)

There is no writer module yet. The intended design:

- **multi-file**, ordered by reference dependency (catalogs → templates → instances), so every file's references
  are declared by an earlier `Import`;
- **`Object`** for a class with one instance and **`Table`** for more, never split by raggedness;
- **units on export** via `.to(header_unit)`, never manual scaling;
- **connectivity inverse**: Terminals back to `node*`/`bus*` columns, collapsing synthesized phase children back
  to `phases` where they match the synthesis rule;
- **deterministic**, built on `document.py`;
- **round-trip test**: `.cimtbl → graph → .cimtbl` byte-identical for the fixtures, and `graph → .cimtbl → graph`
  isomorphic.

## Open questions

- **Forward references.** The original design resolved references in a second pass so files could be in any
  order. `bind_row` resolves immediately, and CIMHub depends on bind-time resolution, so changing this is a
  contract change.
- **Profiles beyond cim18v17.** Binding is reflective, but only cim18v17 is tested.
- **The percent-impedance base** for windings is an open IEC WG13 question (`.development/PERCENT_IMPEDANCE_BASE.md`).

## Changes from the original design

For readers of CIM-Builder's original `CIMTBL_DESIGN.md`:

| Original design | Now |
|---|---|
| A LinkML schema + `gen-python` validates rows | Reflection on the live profile module |
| Per-class Builders execute every row (`Table ACLineSegment` → `ACLineSegmentBuilder`) | One generic `bind_row`; builders are optional and delegate to it |
| References by `name`, a `NameIndex` per class, names unique within a class | References by `mRID`; cim-graph's mRID index; mRIDs unique within the model |
| Second-pass resolution, forward references legal | Declare before use (see [Open questions](#open-questions)) |
| `Template` column, `TransformerAssembly` matched on `endNumber` | No `Template` column; real associations (`WireConfiguration`, `TankConfiguration`) |
| `baseKind` column, `percent` → winding / `pu` → system defaults | The base is chosen by class shape; `(pu system)` is the only override |
| Terminal `sequenceNumber` follows column order | Fixed by the column name |
| The parser sets `CIMG_CIM_PROFILE`; defaults to `cimhub_2026` | The parser applies nothing; `load_model` sets it, `load` checks it; no default |
| `Qty.to_cimunit` does the conversion | `graph_write.set_attr` + `impedance.z_base` |
| `dsl/` and `core/` sub-packages of `cimbuilder` | The flat `cimtables` package |

These parts of the original are out of scope here and stay in CIM-Builder's history: the builder API, the VS Code
extension, the CIM-Repair / CIM-Asset-Manager strategy and the phased build plan.
