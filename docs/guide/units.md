# Units and impedance bases

## Units go on the column header

```text
Table ACLineSegment: mRID, node1, node2, length (ft), r (ohm), x (ohm), BaseVoltage
Object BasePower: mRID=sbase, basePower (MVA)=100
```

The unit applies to every cell in the column. Any unit the value's CIM type can convert from works: `kV`, `kW`,
`MVAr`, `ohm/mile`, `uS`, `ft`. A unit-bearing column written without a unit is read in SI base units (W, V, ohm,
S, m).

The model always holds CIM's own SI types, exactly as CIM XML does: `length (ft)=2000` is stored as 609.6 m, and
`p (kW)=1155` as 1,155,000 W. Read a value back in any unit with cim-graph's `.to()`:

```python
line.length.to('ft')     # 2000.0
load.p.to('kW')          # 1155.0
```

Percent-typed CIM attributes (`percentImpedance`, `pfixedPct`) hold the percent number itself: `5`, with or
without `(percent)`, is stored as 5.

## `pu` and `percent` impedances

A `(pu)` or `(percent)` value on an impedance (ohm) or admittance (siemens) column is converted to ohms or
siemens against a base, `z_base = U² / S`. Which base depends on the class, never on which values a row happens
to set:

| Class | Base |
|---|---|
| A rated class, such as `PowerTransformerEnd` | Its own rating: `ratedU` and `ratedS`. |
| A class that points at a rated one, such as `TransformerMeshImpedance` | The rating of its `From...` end. |
| Anything else, such as `ACLineSegment` or `EnergySource` | The system base: its `BaseVoltage` and the model's `BasePower`. |

```text
Object BasePower: mRID=sbase, basePower (MVA)=100
Object BaseVoltage: mRID=bv_4160, nominalVoltage (kV)=4.16
Object PowerTransformer: mRID=xf

Table PowerTransformerEnd: mRID, PowerTransformer, endNumber, ratedU (kV), ratedS (kVA), r (percent), BaseVoltage
    xf_1, xf, 1, 4.16, 500, 1, bv_4160                     # 1% of 4.16² / 0.5 = 0.346 ohm

Object TransformerMeshImpedance: mRID=z_12, FromTransformerEnd=xf_1, x (percent)=5     # on xf_1's rating
```

### The system base

The system base power is the model's `BasePower` object, never a library default. Declare it before any row that
needs it; a system-base value before it is an error, as is a second `BasePower` that disagrees with the first.

### Data already on the system base

Source data that is on the system base even for a rated class, such as PSS/E transformer impedances, uses
`(pu system)` or `(percent system)`:

```text
Table PowerTransformerEnd: mRID, PowerTransformer, endNumber, ratedU (kV), ratedS (kVA), x (pu system), BaseVoltage
    xf_1, xf, 1, 4.16, 500, 0.1, bv_4160                   # 0.1 × 4.16² / 100 = 0.0173 ohm
```

`system` is the only base word, and only `pu` and `percent` take it.

### What a rated class needs

Every value a relative impedance depends on is set before the conversion, wherever it appears in the row: the
ratings, the `BaseVoltage` and the `From...` reference. If one of them is missing, the row raises rather than
guessing:

```text
model.cimtbl:2: PowerTransformerEnd 'xf_1': a pu/percent impedance needs both ratedU and ratedS set
```
