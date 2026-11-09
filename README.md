# CIM-Tables

**A CIM input file you can write in Notepad.**

`cimtables` reads `.cimtbl`, a plain-text table format for building power-system models in the IEC Common
Information Model (CIM), and turns it into live [cim-graph](https://github.com/PNNL-CIM-Tools/CIM-Graph)
objects that can be saved as standard CIM RDF/XML or JSON-LD.

## Why this exists

CIM is the industry's common language for describing a power grid, but it has never had a format people can
*write*. Its exchange formats, RDF/XML and JSON-LD, are produced and consumed by software: every object is a
block of namespaced XML, every link between objects is a UUID, and adding one load to a feeder means hand-editing
dozens of lines whose meaning is spread across the file. Nobody opens a CIM XML file in a text editor to build a
model.

So in practice, CIM models are built some other way. A model is drawn in a vendor tool, or written in a
simulator's native format (an OpenDSS `.dss` file, a PSS/E `.raw` file) and then run through a converter. Each
converter carries its own mapping from the native format's ideas to CIM's, and the CIM model you end up with is
only as faithful as that mapping. When something is wrong, you debug the converter, not the model.

`.cimtbl` removes the middle step. It is to CIM what a `.dss` file is to OpenDSS, except that its vocabulary
**is** CIM:

- every table is a CIM class (`Table ACLineSegment:`),
- every column header is a CIM attribute or association name (`length`, `BaseVoltage`, `PerLengthImpedance`),
- every row is one CIM object,
- units are written once, on the column header (`length (ft)`, `p (kW)`, `r0 (pu)`), and converted for you.

There is no mapping to maintain because there is nothing to map. If the CIM profile has a class or attribute,
you can write it; if it doesn't, you get an error pointing at the line. The result is a file you can create in
Notepad, read top to bottom, review in a pull request, diff in git, and ask an LLM to edit.

## What it looks like

A small radial feeder: one source, two line segments and two loads.

```
# tiny_feeder.cimtbl

Profile = cim18v17
Authority = https://example.com/models
Model = tiny_feeder

Object BasePower: mRID=sbase, basePower (MVA)=100
Object Substation: mRID=sub
Object Feeder: mRID=fdr, NormalEnergizingSubstation=sub

Table BaseVoltage: mRID, nominalVoltage (kV)
    bv_12kv, 12.47

Object EnergySource: mRID=source, node=sourcebus, phases=ABC, nominalVoltage (kV)=12.47, BaseVoltage=bv_12kv, EquipmentContainer=fdr

Table ACLineSegment: mRID, node1, node2, phases, length (ft), r (ohm), x (ohm), BaseVoltage, EquipmentContainer=fdr
    ln_trunk,  sourcebus, n1, ABC, 2000, 0.38, 0.75, bv_12kv
    ln_branch, n1,        n2, C,   500,  0.12, 0.20, bv_12kv

Table EnergyConsumer: mRID, node, phases, p (kW), q (kVAr), BaseVoltage, EquipmentContainer=fdr
    ld_n1, n1, ABC, 1155, 660, bv_12kv
    ld_n2, n2, C,   170,  80,  bv_12kv
```

Load it, and save it as CIM:

```python
import cimtables
from cimgraph.utils import write_xml

network = cimtables.load_model('tiny_feeder.cimtbl')  # a cim-graph FeederModel for Feeder `fdr`
write_xml(network, 'tiny_feeder.xml')                 # standard CIM RDF/XML
```

That is the whole model. Besides the objects you wrote, the loader made what CIM requires
but no person wants to type: two `Terminal`s per line (`ln_trunk_T1`, `ln_trunk_T2`), one per load and source,
the `ConnectivityNode`s they meet at (`sourcebus`, `n1`, `n2`), the per-phase children (`ln_trunk_A`,
`ln_trunk_B`, `ln_trunk_C`, `ln_branch_C`) and each Terminal's `PhaseCode`. The line lengths are stored as
609.6 m and 152.4 m, and the loads as 1,155,000 W, because CIM holds SI units; you wrote feet and kilowatts.

Larger examples, including the IEEE 13-bus feeder and transmission test cases, are in
[CIMTBL-Models](https://github.com/PNNL-CIM-Tools/CIMTBL-Models).

## How it stays true to CIM

`.cimtbl` is a different *spelling* of a CIM model, not a different model. The rules that keep it that way:

- **The profile is the schema.** `cimtables` has no list of supported classes. It reads the CIM profile itself
  (from [cimprofiles](https://github.com/PNNL-CIM-Tools/CIM-Profiles), through cim-graph) and accepts exactly
  what that profile defines: its classes, attributes, associations and enumeration values. A new class in a new
  profile works with no change here, and nothing outside the profile gets in. `Profile = cim18v17` names which
  profile a file is written against.
- **One row is one CIM object.** A row is what an `rdf:about` block is in CIM XML; a cell is one property; a
  reference cell names its target, as `rdf:resource` does.
- **Identity is CIM identity.** Objects are named by `mRID`, unique across the whole model as CIM 18 requires.
  Each object's UUID is derived from its mRID and the model's `Authority` and `Model`, so loading the same file
  twice gives the same UUIDs, two models can share a bus called `671` without colliding, and renaming an object
  (`name`) never changes its identity.
- **Units are CIM units.** Values are stored as CIM's own unit types in SI, exactly as CIM XML holds them. The
  `(unit)` on a column header only says what you typed. `pu` and `percent` impedances are converted to ohms
  against the equipment's own rating (a transformer winding) or the system base (a line), and the system base is
  the model's `BasePower` object, not a hidden default.
- **Define, then add.** `Object` and `Table` define objects; `Update` adds properties to an object defined
  earlier, possibly in another file. That is CIM's formative and additive split, and how RDF adds properties to
  an existing `rdf:about`.
- **The shorthand expands into normative CIM.** The only columns that aren't CIM names are the connectivity and
  phase conveniences: `node`/`node1`/`node2` (ConnectivityNode), `bus`/`bus1`/`bus2` (TopologicalNode) and
  `phases`. They generate standard Terminals, nodes and per-phase objects following the CIM rules (delta loads
  included). They are optional: you can write `Terminal` and `ConnectivityNode` rows yourself, and a real CIM
  attribute with the same name always wins.
- **Nothing hidden.** Everything that affects the model is in the text: the profile, the model identity, the
  base power. The same file gives the same model on any machine.

## Writing a `.cimtbl` file

The whole format, briefly:

- **`Object <Class>: attribute=value, ...`** for a single object; **`Table <Class>: column, column, ...`**
  followed by indented rows for many.
- **`#`** starts a comment. Blank lines don't matter.
- **A blank cell is unset.** Cells are trimmed. A value containing `,` `#` `(` or `)`, or with leading or
  trailing spaces, is quoted as in CSV: `"#2_ACSR_6/1"`, `"DEFAULT (DC)"`. Source names are kept exactly as
  written.
- **Units go on the column header**, `length (ft)`. A unit-bearing column with no unit is read in SI.
- **A header column can carry a default** for every row, `EquipmentContainer=fdr`. A row may override it by
  adding a cell at the end.
- **References are by mRID, and the target must come first.** Declare `BaseVoltage bv_12kv` before the lines
  that use it. Order your file the way you'd explain the model: bases and catalogs, then equipment.
- **`Import other.cimtbl`** splices another file in at that point, relative to the importing file. Use it to
  share wire and transformer catalogs between models.
- **The same class can appear in several tables** with different columns, for example lines given by
  impedance in one table and by catalog reference in another.
- **Phases are written in conductor order**: `ABCN`, `CB`, `s1s2N`. Leave `phases` blank for a balanced
  three-phase device: CIM reads a Terminal with no phases as ABC.

The full format, including connectivity, phases, delta connections, relative impedance bases and the error
rules, is in [docs/CIMTBL_DESIGN.md](https://github.com/PNNL-CIM-Tools/CIM-Tables/blob/main/docs/CIMTBL_DESIGN.md).

## When something is wrong

Each row is checked against the profile before it touches the model, and every error names the file and line:

```
tiny_feeder.cimtbl:21: EnergyConsumerRow has no attribute 'qq' - did you mean 'q'?
tiny_feeder.cimtbl:14: 'source': BaseVoltage 'bv_13kv' (BaseVoltage) not found in graph (declare it before rows that reference it)
```

Syntax errors, unknown classes and columns, values of the wrong type, references to undeclared objects,
duplicate mRIDs and missing ratings all fail the same way, at the line that caused them. Loading stops at the
first error: nothing is skipped and nothing is guessed.

## Installing

```
pip install --pre cimtables
```

`cimtables` is an alpha release and depends on pre-release versions of `cim-graph` and `cimprofiles`, hence
`--pre`. It needs Python 3.10 or newer.

## Using it from Python

- **`cimtables.load_model(path)`** builds a new model from a file: a `FeederModel` if the file has one
  `Feeder`, else a `NodeBreakerModel` (for a single `Substation`, or no container at all).
  `load_model(path, model_cls=BusBranchModel)` builds a bus-branch model instead.
- **`cimtables.load(path, network)`** adds a file's objects to a cim-graph model you already have, and
  `load_text(text, network)` does the same for a string.
- **`cimtables.document`** writes `.cimtbl` files from Python with stable, aligned formatting. Model converters
  use it to produce `.cimtbl` that a person can then read and edit.
- Once loaded, the model is ordinary cim-graph: query it, modify it, and write it out with cim-graph's
  `write_xml` or `write_json_ld`.

Two settings are process-wide, as they are in cim-graph: `load_model` sets the active CIM profile
(`CIMG_CIM_PROFILE`) from the file's `Profile =`, and both `load` and `load_model` set cim-graph's model scope
from `Authority =` and `Model =`.

## Where it fits

```
cimprofiles  →  cim-graph  →  cimtables  →  CIM-Builder, CIMHub
(the CIM        (the model     (.cimtbl in)   (model builders and
 classes)        in memory)                     format converters)
```

- [CIM-Profiles](https://github.com/PNNL-CIM-Tools/CIM-Profiles) provides the CIM classes and units.
- [CIM-Graph](https://github.com/PNNL-CIM-Tools/CIM-Graph) holds the model and reads and writes CIM XML and
  JSON-LD.
- [CIM-Builder](https://github.com/PNNL-CIM-Tools/CIM-Builder) builds models programmatically on top of
  `cimtables`.
- CIMHub's importers (OpenDSS and CYME so far) translate their native format into `.cimtbl` first, so the
  converted model is something a person can read and check.
- [CIMTBL-Models](https://github.com/PNNL-CIM-Tools/CIMTBL-Models) is a library of grid models written in
  `.cimtbl`.

## Status

This is an early release.

- The CIM 18 profile `cim18v17` is the one that is tested. Other profiles work in principle, because nothing
  here is profile-specific, but they are not yet covered by tests.
- References must point backwards in the file. Forward references are an open question.
- Reading is complete; writing a `.cimtbl` file *from* an arbitrary cim-graph model is not built yet.

## License

BSD 3-Clause; see [LICENSE](https://github.com/PNNL-CIM-Tools/CIM-Tables/blob/main/LICENSE) and [DISCLAIMER.md](https://github.com/PNNL-CIM-Tools/CIM-Tables/blob/main/DISCLAIMER.md). Copyright Battelle Memorial Institute.
Developed at Pacific Northwest National Laboratory.
