# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`cimtables` implements the `.cimtbl` DSL — a hand-editable tabular text format for CIM power-system models — as parser → profile-reflective validator → binder onto live `cim-graph` objects. It was split out of CIM-Builder (`/home/ande188/CIM-Builder`, package `cimbuilder`), which now depends on this package. **`cimtables` must never import `cimbuilder`**; the dependency is strictly `cimprofiles` → `cim-graph` → `cimtables` → `cimbuilder`.

Design history lives in CIM-Builder, not here: `/home/ande188/CIM-Builder/cimbuilder/development/CIMTBL_DESIGN.md` (format spec §3, connectivity §5, phase interface contracts §12, split rationale §8.1), plus `GRAPH_WRITE_CONTRACT.md` and `PROFILE_RESOLUTION.md` in the same directory. Module docstrings cite these by section number.

Git-tracked `.cimtbl` grid models (the target for migrating OpenDSS models) live in the sibling repo `/home/ande188/CIMTBL-Models` (`PNNL-CIM-Tools/CIMTBL-Models`, working branch `feature/1`; `distribution/`, `transmission/`, `tiered_tso_dso/`). It's a plain clone, not a submodule — reference it by path; `tests/fixtures/` stays self-contained.

## Commands

```bash
uv sync --extra dev                                  # install (cim-graph/cimprofiles are editable local paths, see below)
uv run pytest                                        # full suite
uv run pytest tests/test_binder.py                   # one file
uv run pytest tests/test_parser.py::test_name -q     # one test
```

`pyproject.toml` `[tool.uv.sources]` points `cim-graph` and `cimprofiles` at sibling checkouts (`/home/ande188/CIM-Graph`, `/home/ande188/CIM-Profiles`), and `prerelease = "allow"` is required for their pre-release deps. Changes in those repos take effect here immediately.

## Pipeline architecture

```
.cimtbl text ─parser─▶ RawRecord ─validate─▶ <Class>Row dataclass ─bind_row─▶ live cim-graph object in network
```

`cimtables.load(path, network)` in `__init__.py` is the whole pipeline in three lines; it is the ObjectBuilder-free entry point. CIM-Builder's `ObjectBuilder.from_table` delegates to the same `bind_row`.

- **`grammar.lark` / `parser.py`** — fixed, profile-agnostic Lark grammar: knows only `Profile =`, `Import <file>.cimtbl`, `Object <Class>: k=v, ...`, `Table <Class>: header, ...` + rows, `(unit)` suffixes, `#` comments, blank cell = unset. Knows **no** CIM class names — never add CIM-specific rules here. `Import` is resolved inline (textual composition), and `Profile=` is consumed before records are built; `parse_file` returns `(records, profile)`.
- **`records.py`** — `RawRecord`: all cells still `str | None`, plus per-column units and source file/line for line-numbered errors.
- **`validate.py`** — reflects on the active CIM profile module (no LinkML / no curated schema) to build a `<Class>Row` dataclass on demand per (profile, class), coerces cells, wraps unit-bearing fields in `Qty`, and raises `CimtblValidationError` (with closest-attribute suggestions) *before* any graph mutation. Profile comes from `CIMG_CIM_PROFILE`, resolved through cim-graph's `get_cim_profile()` so the module is identical to `network.cim` (comma-spec merged profiles included). FK fields remain unresolved mRID strings at this stage. The row key is reflective: an `IdentifiedObject` row requires `mRID`; a CIM 18 Identity-only class (no `mRID` field, e.g. `PhaseImpedanceData`) takes an optional `identifier` column instead.
- **`synthetic.py`** — the only non-reflective naming rule: DSL-only columns (`node*` → ConnectivityNode, `bus*` → TopologicalNode, `phases`, `Template`).
- **`binder.py`** — `bind_row` builds the object from the row key only (`cim_cls(mRID=...)`, so the UUID is seeded from the mRID and a rename keeps it; an Identity-only row's `identifier` seeds the UUID the same way via `obj.uuid(mRID=...)`, and without one the UUID is random). It then classifies every row field purely from the profile into: connectivity column → `connectivity.add_connectivity`; `EquipmentContainer` → `graph_write.resolve` + container override; other reference slot → `graph_write.resolve` + `set_assc` (unresolved mRID raises — references must be declared before use); everything else, `name` included, → `set_attr`. No per-class dispatch table — keep it that way.
- **`connectivity.py`** — Terminal synthesis (one per connectivity column, `sequenceNumber` = column order), auto-vivification of undeclared nodes/buses, and `phases` shorthand. Phases are tokens in conductor order (`CB`, `s1s2N`). Every Terminal gets the canonical PhaseCode (`BC`, `s12N`), and one `<Class>Phase` child is made per token. The class is found by walking the MRO (`Breaker` → `SwitchPhase`, which uses `phaseSide1/2`). Exception: a delta shunt whose child has the `phases` PhaseCode extension (cim18v17 `EnergyConsumerPhase` etc.) gets one child per line-to-line branch. Generated objects are keyed off the owner's mRID: Terminals `<mRID>_T<n>`, phase children `<mRID>_<phase>` (delta branches `<mRID>_<BC>`); auto-vivified nodes/buses take the cell value as mRID. A `TransformerEnd` row's `node`/`bus` puts its Terminal on the end's `PowerTransformer` (`sequenceNumber` = `endNumber`); `bind_row` resolves FKs before connectivity for this. The shorthand only applies where the class has no real `phases` attribute (`Terminal.phases`, `EnergyConsumerPhase.phases` are set literally).
- **`graph_write.py`** — the **only** module that mutates cim-graph objects or the network (`set_attr`, `set_assc`, `add_to_graph`, `resolve`). `resolve(network, cls, mRID)` is cim-graph's `find_by_mRID` plus an `isinstance` check: any subclass of an abstract slot class matches, and an mRID belonging to another class raises. Other code must route through it rather than calling `setattr` / `network.add_to_graph` directly. `set_attr`/`set_assc` take a `part` profile-module checked by class *name* only; passing `network.cim` is the documented no-op check used at reflective call sites.
- **mRID index** — lives in cim-graph (`GraphModel.find_by_mRID` / `mrid_index` / `list_identities`), kept by `GraphModel.add_to_graph`. mRIDs are unique **within the model, not per class** (CIM 18); reusing one in any class raises. `name` is optional, non-unique data. Identity-only objects have no mRID and are not indexed (nothing references them).
- **`units.py`** — `Qty(value, unit)` carrier from the `(unit)` header suffix; converted to `CIMUnit` in `set_attr`.
- **Relative impedances** — a `percent`/`pu` Qty on an ohm-typed attribute is converted in `set_attr` against a winding `z_base = U²/S` that `bind_row` resolves after FKs: from the row's own object if rated (`PowerTransformerEnd`), else from its `From*` reference target (`TransformerMeshImpedance.FromWinding`). On a class with `baseU`/`baseS` (`TransformerWinding`: baseU = coil voltage, baseS = common tank kVA) those are required; otherwise (`PowerTransformerEnd`) `ratedU`/`ratedS`. Rationale and open IEC WG questions: `.development/PERCENT_IMPEDANCE_BASE.md`. System base (`pu` on `BasePower`) is not implemented.
- **`writer.py`** — cimgraph → `.cimtbl` writer; currently an unimplemented stub.

## Format conventions worth knowing

- References are by `mRID`. Prefixes keep mRIDs unique across classes (`ld_671` vs bus `671`); the convention is the translator's (CIMTBL-Models' `cimtbl-translate` skill), not enforced beyond uniqueness. The design (CIMTBL_DESIGN §3.3) calls for second-pass resolution so forward references are legal, but `bind_row` resolves immediately: declare targets first. Where the profile serializes only one side of an association (e.g. cim18v17 `TransformerWinding.FromTransformerLug` is `serialize=False`), write it from the other side.
- The same class may appear with different header shapes (e.g. `ACLineSegment` by `bus1/bus2` + `r (pu)` vs `node1/node2` + `phases` + `PerLengthImpedance`); presence/absence of fields is the dispatch signal — rows carry no shape flag.
- Matrix impedance is literal `PhaseImpedanceData` rows only; compact `rmatrix`/`xmatrix` sugar is deliberately not supported.
- `Object` is for singletons, `Table` for many instances (even ragged ones).

## Tests

Tests target `cim18v17` only (cimhub_2026 and gmdm are out of scope). They set `os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'` at module import, build a `BusBranchModel` over an `XMLFile` connection, and parse real `.cimtbl` text rather than hand-built records. Beware: `parser.parse_file` writes a file's `Profile =` into `os.environ` (process-global), so test text with a `Profile =` line changes the profile for later tests. Fixtures (`ieee13.cimtbl`, `ieee14.cimtbl`, `wire_infos.cimtbl`) are in `tests/fixtures/`.

`tests/test_bind_cim18v17.py` uses a `FeederModel` and sets the profile per test via `monkeypatch.setenv` + `get_cim_profile.cache_clear()`, including a comma-spec merged profile (`cim18v17.connectivity_unbalanced,cim18v17.electrical_unbalanced`) bound end to end.
