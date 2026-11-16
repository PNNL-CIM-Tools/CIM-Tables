# Requirements on `cimtables` for sharded models

**Status: proposed.** Nothing here is built. These are the requirements the sharded power flow solver
(`PNNL-CIM-Tools/CIM-Sharded-Solver`, `docs/DESIGN.md`) places on `cimtables`. The solver is a downstream
consumer: `cimtables` must not import it.

Each requirement has an identifier (`S1`, `S2`, ...) for reference from issues and commits, a statement of what
is needed, what `cimtables` does today, and how to tell it is met. Syntax shown here is a proposal; the behaviour
is the requirement.

## Background

The solver splits a state-sized model into shards that are staged, hashed and solved independently, and stitched
only at boundary nodes. It needs the source model to be organised the same way.

The organisation is the frame and boundary scheme of the Great Britain Grid Code (Planning Code, Appendix G,
"Organisation of PSM data"):

| Term | Meaning | In `.cimtbl` |
|---|---|---|
| Frame | A grid extent under one party's data mastership | One master file and its `Import` tree |
| Boundary | Exists between frames whose systems connect | One boundary file |
| Model | Grid objects that fit into one frame or boundary, in one profile | A master with its own `Model =` |
| Profile | Structural, Situation or Solution data | Structural rows; Situation as `Update` rows (see S9) |
| Assembly | A collection of models for a purpose | An assembly file (see S1) |

A boundary here is not a boundary of operating responsibility or ownership. A boundary node is a connectivity
node that must be present in two frames so that they snap together when assembled.

In the solver's use, a switch between two areas is declared in the frame of the area that contains both, and the
nodes it shares with the contained areas are the boundary nodes. A feeder frame therefore references a boundary
node at its head and one at each tie, and two feeder frames never reference each other's nodes.

The rules that matter:

- A boundary model holds only connectivity nodes. It has no equipment and so no operating-point data.
- A frame's objects point at boundary nodes, never at another frame's objects. Every connection between two
  frames goes through one boundary.
- A boundary is associated with two or more frames.
- The assembly as a whole has no dangling references. A frame alone does.
- A frame can be replaced by a new version that connects to the same boundary nodes.

The GMDM worked example (`epri-dev/GMDM-IOP`, "Master D-to-SubT-Boundary") shows the failure modes these rules
prevent. Its boundary model holds line segments and re-declares feeders, substations and base voltages; one
boundary node is declared again inside a frame with a different container; and the nodes on the other side of the
boundary are referenced by neither frame. S3, S4 and S8 exist to make each of these an error or a reported
finding.

## What must not change

`Import`, `load`, `load_text`, `load_model`, bind-time reference resolution, generated names and the
`file:line[:col]` error format are a contract with CIMHub. Everything here is additive: a file with no assembly
loads exactly as it does today.

## Requirements

### S1. An assembly level above `Import`

**Need.** A way to name the frame models and boundary models that make up one assembly, without composing them
textually into one network.

**Today.** `Import` is textual composition. `Profile =`, `Authority =` and `Model =` are declared at most once per
`Import` chain, and a conflicting `Import` raises, so two frames with their own `Model =` cannot be imported into
one master.

**Proposal.** An assembly file with two new statements, each naming a master file:

```
Profile = cim18v17

Boundary boundaries/t_d.cimtbl
Frame    transmission/master.cimtbl
Frame    feeders/fdr_0001.cimtbl
Frame    feeders/fdr_0002.cimtbl
```

- A named file is an ordinary master: it may `Import`, and its settings follow today's rules within its own
  chain.
- The statements are lexed only where their shape follows, like the existing keywords, so `Frame` and `Boundary`
  stay usable as cell values.
- The grammar stays profile-agnostic.

**Met when.** An assembly file parses to a list of frame and boundary entries with file and line; a missing file,
a file named twice, and an assembly naming another assembly all raise `CimtblParseError` at the assembly line.

### S2. Settings per model, one profile per assembly

**Need.** Each frame and boundary keeps its own `Authority =` / `Model =`. The profile is one for the assembly.

**Today.** The model scope and the profile are process-global, set by `load` / `load_model` and left set.

**Met when.**
- A frame or boundary whose `Profile =` differs from the assembly's raises.
- Loading a model applies its own scope while its rows bind, including to boundary rows (see S5), and loading a
  second model in the same process is not affected by the first.
- An empty model scope can be written. Today leaving `Model =` out leaves the process-global scope as it was, so
  a file cannot say "no model name". Where a utility's keys are unique across the utility, every frame and
  boundary shares one scope, and a boundary node then has the same UUID in every model with no special handling
  (see S6).

### S3. Boundary models hold connectivity nodes only

**Need.** A file named by `Boundary` may declare `ConnectivityNode` rows and nothing else.

**Today.** No such notion.

**Decided.** There is no artificial container. A boundary node names a real one in `ConnectivityNodeContainer`:
the area that contains both frames, declared before the boundary is read.

**Today.** The container of a node depends on load order. `connectivity.add_connectivity` gives a node without
a container the container of the first equipment that touches it, so an undeclared boundary node, or one
declared bare, ends up in whichever frame loads first. A node declared with a container keeps it. Declaring or
updating a node after equipment has used it raises.

**Met when.** Any other class in a boundary file, and any `Update` row there, raises with file and line. A
boundary node without a `ConnectivityNodeContainer` raises. Boundaries are bound before every frame, whatever
order the assembly lists them in, so a boundary node's container never depends on the order of frames.

### S4. Cross-model references go through boundaries

**Need.** A frame row may reference a boundary node. It may not reference an object of another frame, and it may
not re-declare a boundary node.

**Today.** A reference must already be bound in the network. An undeclared `node` / `bus` cell is auto-vivified,
so a mistyped boundary node silently becomes a new node inside the frame.

**Met when.**
- A frame that declares an mRID also declared in one of its boundaries raises, naming both locations.
- A frame reference that resolves only in another frame raises.
- Auto-vivification is unchanged for ordinary nodes. The mistyped-boundary case is caught by S8.

### S5. Loading one frame alone

**Need.** Load a single frame, with the boundary nodes it uses, into its own model, without reading any other
frame. This is what makes staging parallel across processes.

**Today.** `load_model` loads one master and everything it imports.

**Proposal.** `load_frame(assembly_path, frame)` returns a model holding the frame's objects plus the nodes of
the assembly's boundaries, bound first so that frame rows resolve them. The model type follows `load_model`'s
rule (one `Feeder` gives a `FeederModel`, and so on).

**Met when.**
- Two frames of one assembly load in separate processes with no shared state.
- The loaded model distinguishes boundary nodes from the frame's own objects.
- Boundary nodes the frame never touches are either omitted or marked unused; which is open.

### S6. One identity for a boundary node

**Need.** A boundary node has the same UUID whichever frame loads it.

**Today.** UUIDs are seeded from the mRID under the current model scope, so the same boundary row bound during two
different frames' loads would get two UUIDs.

**Met when.** A boundary node's UUID is seeded under the boundary model's own `Authority =` / `Model =`, and
loading it through two frames gives equal UUIDs.

### S7. Identity across the assembly

**Need.** A defined rule for mRID uniqueness across frames, checkable without binding the whole assembly.

**Today.** mRIDs are unique within a model; reusing one raises.

**Proposal.** An mRID is unique within a frame together with its boundaries. Two frames may reuse an mRID, as bus
`671` in IEEE13 and IEEE123 already do under different `Model =` scopes. Identity across the assembly is the
pair of model and mRID, or the UUID. Boundary mRIDs are unique across all boundaries of the assembly.

**Met when.** A parse-only check of an assembly reports a duplicate boundary mRID, and a frame mRID that collides
with one of its boundaries, with both file and line locations, without binding any row.

### S8. Reporting the boundary terminals of a frame

**Need.** For a loaded frame: each boundary node it connects to, and the terminals on it (owning equipment,
`sequenceNumber`, phases). For an assembly: which frames touch each boundary node.

**Today.** Consumers can walk the bound model, but nothing marks a node as a boundary node.

**Met when.**
- The solver's lowering step can build its boundary table from this report alone.
- A boundary node touched by fewer than two frames is reported. This is a finding, not an error, since an
  assembly may be loaded partially.
- A frame that touches none of its boundaries' nodes is reported.

### S9. Situation data as separate files

**Need.** Operating-point data (switch status, tap positions, load and generation values, setpoints) kept apart
from structural data, so that it can change, and be hashed, separately.

**Today.** `Update` rows add to an object declared earlier and never overwrite, which already fits: a structural
file that leaves an attribute unset, plus a file of `Update` rows that sets it.

**Proposal.** A `Situation <file>.cimtbl for <frame>` entry in the assembly, or an equivalent, bound after its
frame.

**Met when.** A situation file containing anything but `Update` rows raises, and a frame loads with or without
its situation file. `cimtables` does not need to know which attributes are operating-point data; that is the
profile's concern.

### S10. The files a model was read from

**Need.** The list of files read to produce a frame: the master, every file it imports, and the boundary files
bound with it. The solver's shard hash is taken over their bytes.

**Today.** `parse_file` resolves `Import`s but returns only records and the header.

**Met when.** The file list is available from a parse or a frame load, in a deterministic order, without
re-implementing `Import` resolution outside `cimtables`. The `cimtables` and profile versions are already
available to the caller.

### S11. Staging throughput

**Need.** Staging time that scales with the number of processes, and no regression in per-row bind speed.

**Today.** Roughly 3,300 to 3,900 rows per second, serial.

**Met when.** S5 holds, so that frames stage in parallel. A faster path that binds straight to columns without
building cim-graph objects, sharing `connectivity.phase_plan` and `impedance.z_base`, is a possible later step
and is not required now.

### S12. Applying a difference message to a frame

**Need.** Apply a CIM difference message to one loaded frame and write the result back as `.cimtbl`, for
committed changes and for what-if cases.

**Today.** cim-graph's `GraphModel.from_incremental` applies an RDF/XML difference file to a model, all or
nothing. `writer.to_documents` writes a model as `.cimtbl`, but regenerates the whole text.

**Met when.**
- A difference whose objects belong to one frame can be applied to that frame's loaded model and the frame
  re-written.
- A difference that touches a boundary node, or objects of two frames, is reported as such before anything is
  applied.

Whether the write-back must preserve the hand-edited layout and comments of the source files is open; see
[Open questions](#open-questions).

### S13. Writing a model as an assembly

**Need.** Write one bound model as an assembly of frames and boundaries, given which container each frame is and
which nodes are boundary nodes. This is how existing single-file models, and models read from CIM XML, are
migrated.

**Today.** `to_documents(..., parts=...)` splits a model into imported parts, and parts that reference each other
raise.

**Met when.** A model written as an assembly and loaded frame by frame compares equal, by `diff_models`, to the
original within each frame.

### S14. Computed topology as additive files

**Need.** The areas are computed, not authored: `Switch.areaBoundary` is the input flag, and a topology processor
derives the `ConnectedArea` objects, their nesting and `Terminal.BoundedConnectedArea`. The computed objects must
be kept apart from the hand-written source, so that they can be regenerated without touching it.

**Today.** `Update` rows already add to objects declared elsewhere, and `document.Table` writes them.

**Proposal.** The processor's output is a generated `.cimtbl` file per frame: `ConnectedArea` rows, and
`Update Terminal: mRID=<equipment>_T<n>, BoundedConnectedArea=...` rows on the generated terminals. It is bound
after the frame, like a situation file (S9).

**Met when.** A frame loads with or without its computed file; the computed file can be deleted and regenerated
with no change to the source files; and a test covers `Update Terminal` on a generated terminal setting
`BoundedConnectedArea`.

### S15. Solver settings for an assembly

**Need.** One `PowerFlowSettings` object for an assembly, readable without loading a frame. The solver takes its
opt-in slack-bus mode from it (`slackDistributionKind = singleReferenceMachine`; the default is distributed
slack by frequency droop), along with `flatStart`, iteration limits and tolerances.

**Today.** `PowerFlowSettings` binds reflectively like any class, but every object belongs to some model, and an
assembly has no place for an object that belongs to no frame.

**Proposal.** A `Settings <file>.cimtbl` entry in the assembly, naming a file that may declare only
`PowerFlowSettings`. It is loaded on its own.

**Met when.** The settings load without any frame, a second `PowerFlowSettings` in the assembly raises, and a
frame loads identically with or without a settings file.

### S16. Reference models

**Need.** Objects every frame may point at and that belong to no frame: `BaseVoltage`, `BasePower`,
`CoordinateSystem`, wire and equipment catalogues, and `PowerFlowSettings` (which makes S15 a case of this).
Frames must not each declare their own copy, or two frames cannot be bound together.

**Today.** A shared file can be imported by several masters, but each load then declares the objects again under
that master's scope.

**Proposal.** A `Reference <file>.cimtbl` entry in the assembly. It is bound before any boundary or frame, and
may refer only to other reference models.

**Met when.** A frame referring to a reference object loads alone (S5) with the reference model bound first; a
frame that declares an mRID the reference model declares raises; and a reference model that refers to a frame
or boundary object raises.

### S17. Edge models

**Need.** A model that puts an artificial source on a boundary node, standing in for a frame left out of an
assembly. A feeder frame has no source of its own, since the head device and source are in the substation frame,
so it loads and solves alone only with one.

**Today.** Nothing distinguishes such a file.

**Proposal.** An `Edge <file>.cimtbl for <frame>` entry. An edge model may refer to the nodes of exactly one
boundary model and to reference models.

**Met when.** An assembly that names a frame and an edge model for the same frame raises, and an edge model that
touches two boundaries raises.

## Order of work

1. S1, S2, S3, S6: the assembly file and boundary models. Nothing else can be tried without them.
2. S4, S5, S7, S8: loading and checking a frame alone. This unblocks the solver's lowering step.
3. S10: the file list for hashing.
4. S9, S14, S15, S16, S17: situation files, computed-topology files, solver settings, reference and edge models.
5. S13: migration of existing models.
6. S12: difference messages.
7. S11: only if measured staging time requires it.

## From the profile, not `cimtables`

Binding is reflective, so these need no `cimtables` change beyond a test. As of cim-profiles 0.2.0a3, cim18v17
has all of them:

- `Switch.areaBoundary`, the input flag.
- `ConnectedArea` with `kind` (including `substationServiceArea`) and `ContainedWithin`, and
  `Terminal.BoundedConnectedArea`: computed, see S14.
- `PowerFlowSettings`, see S15.

`pyproject.toml` pins `cim-profiles<0.2`, so the pin must be raised to use them. The test suite passes against
0.2.0a3 as it stands.

## Shared files in feeder packages

Published feeders do not use edge models. Each feeder package is self-contained and repeats what it shares with
other packages in byte-identical shared files (CIMTBL-MODELS-INTERNAL `PACKAGE_SPEC.md`). Joining packages
needs each shared file bound once, which a reader can decide from the checksums in the packages' manifests.
S17 remains for the solver's own use.

## Open questions

- **Unused boundary nodes** in a frame load: omitted or marked?
- **Write-back for S12.** Regenerating a frame's text loses hand-written layout and comments in git-tracked
  models. The alternative is a row-level patch of the source files, which is a larger piece of work.
- **Nested frames.** The solver nests shards by `ConnectedArea.ContainedWithin`. Whether an assembly may name
  another assembly, which S1 forbids for now, depends on whether nesting is expressed in files or only in
  instance data.
- **Assembly file extension and name.** `.cimtbl` with the new statements, or a distinct extension.
