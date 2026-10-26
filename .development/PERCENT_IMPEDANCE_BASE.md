# Percent impedance base: `baseU²/baseS`, with the winding descriptions flipped

Decision for how `bind_row` converts a `percent` / `pu` impedance column
(`r (percent)`, `x (percent)`, ...) to ohms. Implemented in
`cimtables/binder.py::_winding_z_base` and `graph_write.set_attr`.

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

## The definitions this assumes (flipped from cim18v17)

The cim18v17 descriptions give `ratedU` two meanings:

- `PowerTransformerEnd.ratedU`: "The **phase-to-phase** rated voltage at no load"
- `TransformerWinding.ratedU`: coil voltage (IEV 421-04-01), "for wye-connected
  windings, this is **phase-to-neutral**"
- `TransformerWinding.baseU`: "Base **phase-to-phase** voltage used for
  calculation of percent impedance"

The `TransformerWinding` descriptions were swapped. Corrected, the attributes are:

| attribute | meaning | 1φ 250 kVA 12470GrdY/7200 – 120/240 |
|---|---|---|
| `ratedU` | phase-to-phase nameplate designation (IEEE C57.12.00-2021 Table 7), same as `PowerTransformerEnd.ratedU` | 12470 / 240 / 240 |
| `ratedS` | the winding's own normal rating | 250 / 125 / 125 |
| `baseU` | voltage across the winding's own terminals (IEV 421-04-01) | 7200 / 120 / 120 |
| `baseS` | common percent-impedance base for the tank (already described as "used for calculation of percent impedance") | 250 / 250 / 250 |

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

1. **Swap the `TransformerWinding.baseU` and `ratedU` descriptions.**
   - `ratedU` becomes phase-to-phase per the nameplate designation, consistent
     with `PowerTransformerEnd.ratedU`.
   - `baseU` becomes the voltage between the winding's own terminals
     (IEV 421-04-01; phase-to-neutral for wye-connected windings), used with
     `baseS` for percent impedance.
2. **State that `baseS` is the common power base** for all of a tank's percent
   impedances, which is OpenDSS's winding-1 kVA.
3. **Mesh impedance base.** CIM says the From end "determines the voltage
   reference" but not that the percent/per-unit base is the From winding's
   `baseU`/`baseS`. cimtables assumes it is. Ask for explicit wording.

Until the profile changes, `.cimtbl` files follow the corrected definitions and
say so in a comment (see the template above).

## Not implemented

System base (`pu` on `BasePower` / `BaseVoltage`, the design default for `pu`).
