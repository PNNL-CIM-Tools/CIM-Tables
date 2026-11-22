# The format

A `.cimtbl` file is a list of statements, one per line. Indented lines under a `Table` are its rows.

```text title="ieee13_excerpt.cimtbl"
Profile = cim18v17
Authority = https://example.com/models
Model = ieee13

Import wire_infos.cimtbl

Object BasePower: mRID=sbase, basePower (MVA)=100

Table ACLineSegment: mRID, node1, node2, phases, length (ft), PerLengthImpedance, BaseVoltage, EquipmentContainer=fdr
    650632, RG60, 632, ABCN, 2000, mtx601, base_4160
    632645, 632,  645, BC,   500,  mtx603, base_4160
```

## Statements

| Statement | Meaning |
|---|---|
| `Profile = cim18v17` | The CIM profile the rows are written against. |
| `Authority = ...`, `Model = ...` | The model's identity. Every UUID is derived from an object's mRID under these two, so the same file always gives the same UUIDs, and two models can each have a bus `671` without colliding. |
| `Import other.cimtbl` | Splice another file in at this point. The path is relative to the importing file. |
| `Object <Class>: attr=value, ...` | One object. |
| `Table <Class>: column, column, ...` | Many objects, one indented row each. |
| `Update <Class>: mRID=..., attr=value, ...` | Add properties to an object defined earlier. `Update Table <Class>:` is the table form. |
| `# ...` | A comment, on its own line or after content. |

Each setting (`Profile`, `Authority`, `Model`) appears at most once in a file. An imported file may declare them
too; its value fills one the importing file left unset, and one that disagrees raises.

`Object` and `Table` build exactly the same objects. Use `Object` for a singleton (`BasePower`, an
`EnergySource`) and `Table` for many, even when the rows are ragged: blank cells are fine.

## Cells

- **A blank cell is unset.** Cells are trimmed.
- **Quote a cell as in CSV** when it holds `,` `#` `(` `)` or has leading or trailing spaces: `"#2_ACSR_6/1"`,
  `"DEFAULT (DC)"`. Inside quotes, `""` is one quote character, and a quoted `""` is the empty string rather than
  unset. Names are kept exactly as written, never sanitized.
- **Booleans** are `true`, `false`, `1` or `0`.
- **Enumerations** are the enumeration's value: `phaseConnection=D`, `phaseConnection=Y`.
- A cell can't span lines.

## Header defaults

A header column can carry a default for every row, as `EquipmentContainer=fdr` does above. Rows fill the columns
without defaults, in order; extra cells at the end of a row override the defaulted columns, in order.

```text
Table EnergyConsumer: mRID, node, p (kW), q (kVAr), BaseVoltage, EquipmentContainer=fdr
    load_671, 671,       1155, 660,  base_4160
    load_sub, SourceBus, 2000, 1000, base_115, sub      # this one is in the substation
```

## Identity: `mRID`

Every row of an `IdentifiedObject` class needs an `mRID`, and mRIDs are unique across the whole model, not per
class. A common convention is a short prefix per kind of object (`ld_671` for the load at bus `671`), but any
unique string works.

`name` is ordinary, optional data. Renaming an object never changes its identity or UUID.

A few CIM 18 classes have no mRID (`PhaseImpedanceData`). Their rows take an optional `identifier` column
instead, which gives the object a stable UUID the same way.

**Declaring an object twice.** A second `Object`/`Table` row with the same mRID, class and values changes
nothing. That lets every feeder file declare the `BaseVoltage`s it uses, and a catalog imported from two places
load cleanly. A second row with *different* values raises; adding values is what `Update` is for.

## References

A reference column is named after the CIM association (`BaseVoltage`, `PerLengthImpedance`,
`PowerTransformer`) and its cell holds the target's mRID. Where the association points to an abstract class
(`EquipmentContainer`, `PerLengthImpedance`), any subclass is accepted.

!!! tip "Declare targets first"
    References are resolved as each row loads, so the target must appear earlier in the file (or in an earlier
    `Import`). Order a file the way you would explain the model: bases and catalogs, then containers, then
    equipment.

A reference that would point forward, such as a regulating control whose terminal belongs to equipment declared
after it, is written with `Update`:

```text
Object LinearShuntCompensator: mRID=cap_c1, node=675, phases=ABC, bPerSection (uS)=34.6, BaseVoltage=base_4160
Object RegulatingControl: mRID=rc_c1, Terminal=cap_c1_T1, mode=voltage
Update LinearShuntCompensator: mRID=cap_c1, RegulatingControl=rc_c1
```

An `Update` only adds. It never overwrites a value that is already set, and it can't use the `node` or `phases`
shorthand (that would make a second set of Terminals).

## The same class, different columns

One class can appear in several tables with different columns. A line can be given by its impedance in one
table and by a catalog reference in another:

```text
Table ACLineSegment: mRID, bus1, bus2, r (pu), x (pu), bch (pu), BaseVoltage
    line_1_2, bus_1, bus_2, 0.01938, 0.05917, 0.0528, bv_138

Table ACLineSegment: mRID, node1, node2, phases, length (ft), PerLengthImpedance, BaseVoltage
    632645, 632, 645, BC, 500, mtx603, base_4160
```

Which columns are present decides the representation; rows carry no type flag.

## Catalogs and templates

Share wire, cable and transformer data between models with `Import`: put the catalog in its own file and import
it where it's used. A reusable template is a real CIM association, not a special column:
`ACLineSegment.WireConfiguration` for a line, `TransformerTank.TankConfiguration` for a transformer.

Matrix impedances are written out element by element, one `PhaseImpedanceData` row each, so every element stays
visible and addressable.

## Writing the boilerplate yourself

The only column names that aren't CIM names are the connectivity and phase shorthand (`node`, `bus`, `phases`
and a few more), described in [connectivity and phases](connectivity.md). All of it is optional: `Terminal` and
`ConnectivityNode` rows can be written out like any other class, and a real CIM attribute with the same name as a
shorthand column always wins.
