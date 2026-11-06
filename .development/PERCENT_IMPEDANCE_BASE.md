# Percent impedance base: `baseU²/baseS`

Decision for how `bind_row` converts a `percent` / `pu` impedance column
(`r (percent)`, `x (percent)`, ...) to ohms. Implemented in
`cimtables/impedance.py::z_base` and `graph_write.set_attr`.

## Rule

```
z_base [ohm] = U² / S                    (of the base-source object)
ohm          = percent / 100 × z_base    (pu: value × z_base)
```

The base-source object is:

1. the row's own object, if it carries the ratings (e.g. `PowerTransformerEnd`), else
2. the target of the row's `From*` reference
   (`TransformerMeshImpedance.FromWinding` / `FromTransformerEnd`).

Which ratings are U and S depends on the class:

| class defines `baseU`/`baseS`? | U, S | example |
|---|---|---|
| yes | `baseU`, `baseS`. Both are required, with **no** fallback to rated | `TransformerWinding` |
| no | `ratedU`, `ratedS` | `PowerTransformerEnd` |

There is no fallback because a winding's `ratedU` is phase-to-phase: falling
back to it for a wye or split-phase coil would silently be 3× or 4× wrong.
Instead, conversion fails fast.

## The definitions this assumes

Earlier cim18v17 descriptions (before the 2026-10-26 regeneration) gave `ratedU` two meanings:

- `PowerTransformerEnd.ratedU`: "The **phase-to-phase** rated voltage at no load"
- `TransformerWinding.ratedU`: coil voltage (IEV 421-04-01), "for wye-connected
  windings, this is **phase-to-neutral**"
- `TransformerWinding.baseU`: "Base **phase-to-phase** voltage used for
  calculation of percent impedance"

The `TransformerWinding` descriptions were swapped. They are corrected in the
regenerated cim18v17 profile:

The classes are filled from the nameplate, as asset management records it
(user decision 2026-11-06, which closes CIMHub R13):

| attribute | meaning | 1φ 250 kVA 12470GrdY/7200 – 120/240 | 3φ 1000 kVA 4160Y/2400 – 480 (delta LV) |
|---|---|---|---|
| `ratedU` | nameplate field 1: the phase-to-phase designation (IEEE C57.12.00-2021 Table 7), same as `PowerTransformerEnd.ratedU` | 12470 / 240 / 240 | 4160 / 480 |
| `ratedS` | the winding's own nameplate rating | 250 / 125 / 125 | 1000 / 1000 |
| `baseU` | nameplate field 2: the voltage across the coil (IEV 421-04-01): phase-to-neutral for wye, phase-to-phase for delta | 7200 / 120 / 120 | 2400 / 480 |
| `baseS` | the per-coil kVA the percent impedance is on: `ratedS` for a 1φ primary, `2 · ratedS` for each triplex half (the primary's kVA), `ratedS / 3` for a 3φ coil | 250 / 250 / 250 | 333.3 / 333.3 |

So the phase count lives in the data (`baseS`), not in `z_base`, which stays
`baseU² / baseS` for every connection. The ohms are the coil's own: a wye
coil gives the 3φ base (2400² / 333.3 kVA = 17.28 Ω ≈ 4160² / 1000 kVA, the
gap is the nameplate's rounding of 4160/√3), and a delta coil gives its
branch impedance, 3× the wye equivalent (480² / 333.3 kVA). Nothing infers
the count: one balanced winding, per-phase coils and PhaseEnds all work the
same way.

With these definitions, `baseU`/`baseS` form a coherent per-unit base, and
`baseU²/baseS` is exactly OpenDSS / CIMHub's
`zbase = kV_coil² · 1000 / kVA_winding1`
([OpenDSS: Modeling single-phase transformers](https://opendss.epri.com/ModelingSingle-PhaseTransformers.html);
`CIMHub_2_0/cimhub_opendss/importer/transformers/transformer.py`). OpenDSS
`kVs` = `baseU` and `kVAs=[25 25 25]` = `baseS`. Percent values transcribe
from OpenDSS's Xhl/Xht/Xlt **without rescaling**.

## Evidence

Measured on `CIMTBL-Models/distribution/templates/padmount_1ph_250kva.cimtbl`
against OpenDSS-convention ohms:

| base used | `pad_mesh_12` x | `pad_mesh_23` x |
|---|---|---|
| phase-to-phase voltage² / S | 3.00× | 4.00× |
| ratedS of the From winding (125 kVA) | 1× | 2.00× |
| **baseU² / baseS (corrected definitions)** | **exact** | **exact** |

The first row's factor depends on how the winding is connected, as
(U_LL / U_coil)²: √3² for grounded wye, 1 for delta, 2² for split-phase 120/240.
That's why no rule built on a phase-to-phase voltage can work for every winding.

## IEC WG13 ticket

1. ~~Swap the `TransformerWinding.baseU` and `ratedU` descriptions.~~ Done in
   the regenerated cim18v17.
2. ~~State that `baseS` is the common power base.~~ Done for the split-phase
   center-tap case ("the rating of the primary winding is used as the basis for
   both secondary windings"). It could still be generalized to "all of a tank's
   percent impedances".
3. **Mesh impedance base.** CIM says the From end "determines the voltage
   reference" but not that the percent/per-unit base is the From winding's
   `baseU`/`baseS`. cimtables assumes it is. Ask for explicit wording.


## System base

The winding-base rule above only fits classes that carry - or reference, via
a `From*` FK - their own rating pair (`TransformerWinding`,
`PowerTransformerEnd`). The base is chosen by class shape: a rated class
with an unset rating raises rather than falling back to the system base
(`PowerTransformerEnd` also has a `BaseVoltage`, so a value-based fallback
would silently convert on the wrong base).

A class with no rating pair (`ACLineSegment`, `EnergySource`) uses the
system base: its own `BaseVoltage.nominalVoltage` and the model's
`BasePower` (PSS/E RAW SBASE, MATPOWER baseMVA), which must be bound before
the row. cim-graph's `GraphModel.base_power` default (`CIMG_SYSTEM_MVA_BASE`,
else 100 MVA) is never used by a `.cimtbl` file, so the model depends only on
the text, and a `BasePower` declared after a system-base row cannot leave it
on another base (the row raises). Rows on their own rating need no
`BasePower`.
The same `z_base` converts r/x/r0/x0 (ohm = pu x z_base) and bch/gch/b0ch/g0ch
(siemens = pu / z_base), PSS/E RAW's one SBASE for both. `pu` is the
realistic convention for a line; `percent` is accepted but is a nameplate
convention for transformer tanks.

A column can name its base explicitly instead: `x (pu system)` (or
`(percent system)`) always uses the system base, even on a rated class. This
is for source data that is already on SBASE, e.g. PSS/E transformer
impedances on the system MVA base; on a `PowerTransformerEnd` that is its own
`BaseVoltage` and the `BasePower`. The choice is still made by the
table's shape (its header), never by which values are set, and a plain
`(pu)` keeps the class-shape rule above. `system` is the only base word.

A bound `BasePower` sets `network.base_power` (for builders and exporters);
a second one that disagrees raises.
