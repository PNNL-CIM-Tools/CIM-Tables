# First PyPI release: cleanup and TODO

Audit of `rc1` @ 21c41f7 (2026-11-06). State: 223 tests pass on Python 3.10 and 3.13;
`uv build` gives a clean wheel (all 12 modules + `grammar.lark`); the name `cimtables` is free on PyPI.

## P0: blockers (the release can't install without these)

- [ ] **Release cim-graph 0.6.0aN first.** `cim-graph>=0.6.0a0` isn't on PyPI (latest there: 0.5.0a12), so
      `uv pip compile --no-sources pyproject.toml` fails. cimtables uses 0.6-only APIs: `find_by_mRID`,
      `set_model_scope` / `get_model_authority`, `network.base_power`.
- [ ] **Check the cimprofiles pin.** The local CIM-Profiles is 0.1.0; PyPI has 0.1.0a4. Make sure a published
      version has everything used here: `cimprofiles.units.units` (`CIMUnit`, `ureg`),
      `cim18.cim18v17.connectivity_unbalanced`, Identity-only `PhaseImpedanceData`, the
      `EnergyConsumerPhase.phases` extension. Then raise the floor.
- [x] **Add upper bounds** the way cim-graph does (done: `cim-graph>=0.6.0a0,<0.7`, `cimprofiles>=0.1.0a4,<0.2`;
      raise the floors with the two items above).
- [x] **`[tool.uv.sources]` has absolute paths** (done: now `../CIM-Graph`, `../CIM-Profiles`) (`/home/ande188/CIM-Graph`, …). They ship in the sdist and break
      `uv sync` in any other clone. Switch to relative paths (`../CIM-Graph`) or move them out of
      `pyproject.toml`. Verify with `uv build --no-sources` plus a fresh-venv install from PyPI deps.

## P1: package metadata / build

- [x] `pyproject.toml`: add `license = "BSD-3-Clause"`, `license-files`, `authors`/`maintainers`, `keywords`,
      `classifiers` (Development Status :: 3 - Alpha, Python 3.10–3.13, Topic :: Scientific/Engineering),
      and `[project.urls]` (https://github.com/PNNL-CIM-Tools/CIM-Tables, Issues, CIMTBL-Models).
- [x] (done: `.gitignore` remains, hatchling always adds it) sdist currently ships `CLAUDE.md`, `uv.lock`, `.development/` and `.gitignore`. Add
      `[tool.hatch.build.targets.sdist]` include/exclude (keep `tests/` + fixtures, LICENSE, README).
- [x] Add `cimtables/py.typed`. The package is fully annotated, and CIMHub/CIM-Builder import it.
- [x] Add `__version__` (`importlib.metadata.version('cimtables')`) to `__init__.py`.
- [x] Move `pytest`/`pre-commit` from the published `dev` extra to `[dependency-groups] dev` (PEP 735). Then update
      CLAUDE.md's `uv sync --extra dev`.
- [x] `pre-commit` is a dev dep but there's no `.pre-commit-config.yaml`. Add one (e.g. ruff) or drop the dep
      (done: dropped; add it back with a config when a linter is chosen).
- [x] Pick the version (done 2026-11-09: `pyproject.toml` holds the release target `0.1.0`; the release workflow
      derives `0.1.0aN` on `develop` and `0.1.0` on `main` from PyPI, as CIM-Profiles does).
- [x] PNNL release paperwork: confirm the copyright holder (`PNNL-CIM-Tools` vs Battelle Memorial Institute) and
      whether a DISCLAIMER/NOTICE file is required for a public PyPI release (done: Battelle Memorial Institute in
      LICENSE, PNNL's standard `DISCLAIMER.md`, both shipped as license files).

## P1: documentation (PyPI users can't see CIM-Builder's design docs)

- [x] (done: rewritten for people, why first; links are absolute so they work on PyPI) **README is two lines**, and it's the PyPI long description. It needs: install, a 10-line `load_model`
      example, the format at a glance (settings, `Import`, `Object`/`Table`, defaults, quoting, `(unit)`,
      `(pu system)`), connectivity columns (`node*`/`bus*`), `phases`/`phaseSide*`, the pu/percent base
      rules, `BasePower`, the supported profiles (cim18v17 tested; others reflective, untested), the
      process-global side effects (`load_model` writes `CIMG_CIM_PROFILE`; `load`/`load_model` set the model
      scope), and a link to CIMTBL-Models.
- [x] Bring the format spec into this repo (done: `docs/CIMTBL_DESIGN.md`, rewritten as built), or publish it somewhere
      linkable. Every module docstring cites `CIMTBL_DESIGN.md §…`, `GRAPH_WRITE_CONTRACT.md` or
      `PROFILE_RESOLUTION.md`, and none of them ship.
- [x] A user-facing error message cites a private doc: `connectivity._resolve_node` "per CIMTBL_DESIGN.md §3.5".
- [x] Scrub the "Phase 1/2/3/4/5/5b/R" labels from docstrings (`parser`, `records`, `validate`, `binder`,
      `graph_write`, `connectivity`, `grammar.lark`). They're build-plan history, not API. `units.py`'s docstring
      is stale: it mentions to_cimunit/z_base helpers that live in `graph_write`/`impedance`.
- [ ] Add `CHANGELOG.md`.

## P1: correctness bugs found while probing

- [x] **Import cycles crash with `RecursionError`.** `a.cimtbl: Import b.cimtbl` plus `b.cimtbl: Import a.cimtbl`.
      Track the import chain in `parse_file` and raise `CimtblParseError` naming the cycle.
- [x] **`load(path, network)` silently accepts a `Profile =` that differs from `network.cim`.** Verified:
      `Profile = cim17v40` loads into a cim18v17 network with no error. It validates against the file's profile
      but binds against `network.cim`, and `binder` calls `validate.reference_slots` with the *env* profile.
      This should fail fast on a mismatch.
- [x] **Bind-time errors have no `file:line`**, e.g. `'l1': BaseVoltage 'bv_missing' (BaseVoltage) not found
      in graph`, even though every row carries `source_file`/`source_line`. Parse and validation errors
      already give one. Prefix them in `bind_row`; CIMHub's errors contract (CIMTABLES_REQUESTS.md) should
      welcome this, but flag it there.
- [x] **Text I/O uses the platform encoding**: `parser.py:12,216` `read_text()` and `document.py:182`
      `write_text(text)`. On Windows that's cp1252 with `\r\n`, which breaks non-ASCII source names and the
      byte-for-byte goldens CIMHub diffs. Use `encoding='utf-8'`, plus `newline='\n'` on write.
- [x] An Import setting mismatch (`parser._merge_header`) raises a plain `ValueError` with no line. Make it
      `CimtblParseError` with `file:line`, like the other parser errors.
- [x] (found while fixing the profile check) `validate._qualify` mapped a short explicit profile name to the
      deprecated `cimgraph.data_profile.<name>` shim, a different module from `network.cim`. It now resolves
      through `cimprofiles.profile_module`, like cim-graph's `get_cim_profile`.
- [ ] Tell CIMHub (CIMTABLES_REQUESTS.md): bind errors are now `CimtblBindError` (still a `ValueError`) prefixed
      `file:line: `; Import mismatches are `CimtblParseError` with the new message
      `<file>:<line>: Import 'x': Model mismatch: ...`; `parse_text` returns an `Import` as an `ImportRecord` (`.path`),
      not a `RawRecord` with `cim_class='__import__'`, so `cimhub_core.cimtbl_testing._keyed_records` must handle it.

## P2: API cleanup (decide before the names are on PyPI)

- [x] (done: `records.ImportRecord(path, source_file, source_line)`, returned in place by `parse_text`) **The `__import__` sentinel leaks out of the public `parse_text`.** Import statements come back as a
      `RawRecord(form='Table', cim_class='__import__')`, so the `form: Literal['Object','Table']` type is
      wrong, and `load_text` has to know the magic name. Use a separate `ImportRecord` type, or return the
      imports separately.
- [x] (done: `impedance` and `ImportRecord` added to `__all__`; `document` was already there) Define the public surface. `__all__` lists `synthetic`/`validate` but leaves out `impedance` and
      `document`, which downstream code does use (`cimtables.impedance.z_base`, `cimtables.document.*`,
      `graph_write.set_assc_from_mrid`, `connectivity`). Add what's public and underscore or document the rest.
- [x] (done) `connectivity.py` imports `cimprofiles.cim18.cim18v17.connectivity_unbalanced as CN` at runtime only for
      type hints. That ties a profile-agnostic module to one profile's internal path. Move it under
      `TYPE_CHECKING` (with `from __future__ import annotations`) and drop `cim: CN = network.cim  # type:ignore`.
- [x] (done; the design doc keeps the planned writer) `writer.py` is a one-line docstring stub. Remove it before release (re-add with the implementation) so
      it isn't shipped as an empty public module.
- [x] (done: `validate.KEY_FIELDS`; `_set_side` checks the current value; `type[T]`) Minor: `_SKIP_FIELDS` is duplicated in `binder`/`validate`; `graph_write._set_side` tests list-ness with
      `"list" in field.type` (only works because cim-graph annotations are strings); `graph_write` still uses
      `typing.Type`/`TypeVar` where `type[T]` would do.

## P2: CI / release automation

- [x] (done 2026-11-09: CIM-Profiles' chain, `.github/workflows/release.yml` + `tests.yml`, `dependabot.yml`,
      `.github/scripts/raise_security_floors.py`; CI takes cim-graph/cimprofiles from PyPI with `--no-sources`.
      Simulated without sibling checkouts: version derivation for each PyPI state, `uv build`, and 296 tests on
      Python 3.10 against wheels of the local cim-graph 0.6.0a1 / cimprofiles 0.1.0. Like CIM-Profiles it tests
      3.10 only, from the source tree, not the installed wheel.) Add GitHub Actions: pytest on 3.10–3.13 with
      `uv sync --no-sources` (real PyPI deps), `uv build`, and a test run against the *installed wheel*.
- [x] (done: branch-triggered instead of tag-triggered, with PyPI trusted publishing and a GitHub release + tag
      per version) A tag-triggered publish job with PyPI trusted publishing.
- [ ] One-time setup, by hand: a PyPI *pending* trusted publisher for `cimtables` (owner PNNL-CIM-Tools, repo
      CIM-Tables, workflow `release.yml`, environment `pypi`); a GitHub environment `pypi`; `develop` as the
      default branch (Dependabot needs it); "Allow GitHub Actions to create and approve pull requests" (the
      Dependabot auto-merge).
- [ ] Release: merge `rc1` → `develop` (publishes `0.1.0a1` once cim-graph 0.6 and cimprofiles are on PyPI), then
      `develop` → `main` for `0.1.0`.
- [ ] Dependabot's `uv` updates may fail while `[tool.uv.sources]` points at sibling paths it can't see; drop the
      sources block once cim-graph/cimprofiles are on PyPI if so (the same note as CIM-Graph's CI).
- [x] (already covered: `test_parser.py` import cycle/self-import and UTF-8 read, `test_load_model.py` profile
      mismatch and bind errors with `file:line`, `test_document.py` UTF-8/LF write) Tests for the P1 fixes.

## P3: nice to have

- [x] (done 2026-11-07: the `get_type_hints` caching was already in (`graph_write.type_hints`,
      `validate._real_fields`); the 0.8 ms/object was a cold first load. Measured warm: IEEE13_OCHRE 0.07 s,
      a generated 48k-object feeder 2.3 s, 192k objects 10.5 s, i.e. linear at ~50 µs/object, and no memory
      retained across repeated loads. Fixed the one remaining waste: `set_attr` built a throwaway CIMUnit per
      `(unit)` cell just to read its unit name, now cached per class (`_si_unit_name`), 11-16 % faster.
      What's left is upstream or inherent: lark parsing ~27 %, pint ~25 %, cimprofiles' seeded UUID ~16 %
      (`model_authority_namespace` recomputes a uuid5 per object; cacheable there), cim-graph's
      `_object_count` on every `add_to_graph`/`mrid_index` call ~6 % (O(classes) per call, not quadratic),
      and Python's cyclic GC ~20 % on big models (a caller can `gc.disable()`/`gc.freeze()` around a huge
      load; the library shouldn't flip process-global GC).) Performance: IEEE13_OCHRE (838 objects) takes 0.69 s in `load_model`, ~0.8 ms/object, so a 100k-object
      transmission model would take ~80 s. `typing.get_type_hints` is recomputed on every `set_attr` /
      `_real_fields` / `_parent_assoc_field` call. Cache it per class (`functools.cache`).
- [x] (checked: the grammar builds in 12 ms, so lark's `cache=True` isn't worth it; 0.58 s of the 0.77 s is
      `cimgraph.models`, via its SPARQL database imports, for cim-graph to make lazy) `import cimtables` takes ~1 s, mostly cim-graph's model imports. Lark's `cache=True` could trim the
      grammar build.
- [x] (done 2026-11-09, `feature/writer`: `writer.to_documents`, folded or explicit Terminals, reference
      ordering with `Update` for cycles, per-column units or a JSON config, `parts`; CIMHub's IEEE13_CDPSM golden
      round-trips exactly both ways) The cim-graph → `.cimtbl` writer.
