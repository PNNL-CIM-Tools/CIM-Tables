# A sharded, node-breaker power flow solver on `.cimtbl`

**Status: proposed.** Nothing here is built. This records the architecture agreed in design discussion, the
decisions taken, and the questions still open. It is not part of the `cimtables` design of record
(`docs/CIMTBL_DESIGN.md`), and the solver is a separate package: `cimtables` must not depend on it.

Performance figures in this document are estimates, not measurements, unless stated otherwise.

## Goal

Solve a combined transmission and distribution model of an entire state, from generation to the customer meter,
as one model: a transmission network of about 10,000 buses with hundreds to thousands of distribution feeders of
1,000 to 10,000 nodes each.

CIM is the one format that carries balanced and unbalanced models together, and `.cimtbl`'s `Import` reduces model
assembly to text composition. The solver is designed around that:

- **Sharded from the start.** The global admittance matrix is never assembled as one object. Sharding a codebase
  that was not designed for it is the thing to avoid.
- **Node-breaker native.** No topology processing into bus-branch, no bus renumbering, no PSS/E RAW export.
  Identities are stable from `.cimtbl` to results.
- **Flat data.** Struct-of-arrays tables and integer indices, no nested dictionaries and no object graph in the
  solver.
- **Explicit linear algebra.** Symbolic analysis, numeric factorization, refactorization and solve are separate
  calls the solver controls. Nothing hides behind `A\b`.

## Non-goals

- Co-simulation federation for the power flow itself. Transmission and distribution couple through an equivalent
  injection inside one solver. A federation layer can still wrap the solver for other domains.
- Time integration, governors and AGC. The kernel is a purely algebraic solve; anything with state over time
  belongs to an external stepping driver.
- A native C implementation of `.cimtbl`. The format's semantics stay in `cimtables`; see
  [Technology stack](#technology-stack).
- Equipment changes inside a running solve. They go through the build, or between solves through a
  [shard swap](#what-if-cases-by-shard-swap); see [Build time and runtime](#build-time-and-runtime).

## Architecture

```
BUILD (Python, offline, nightly)                         RUNTIME (compiled, no Python)

.cimtbl frames ─cimtables─▶ bound model ─lowering─▶ IR shards ─▶ kernel ─▶ result sidecars
 + boundary file            (per shard,   (per shard)  (Parquet,    ▲
 + assembly manifest         in parallel)               hashed)     │
                                                                    operating-point updates
                                                                    time-series sidecars
```

Three components:

- **Model compiler** (Python): `cimtables` parses, validates and binds each shard; a new lowering step turns the
  bound model into solver tables. It runs once per model change and is process-parallel across shards.
- **IR**: sharded columnar files. This is the only interface the kernel sees.
- **Kernel** (compiled): assembles each shard's matrix from the IR, solves, and writes result arrays.

## Build time and runtime

Model validity is a model-management problem that is finished before runtime. This follows ADMS practice, where
equipment changes arrive through a nightly build.

| | Build time | Runtime |
|---|---|---|
| Equipment changes (a line impedance, new equipment) | Applied to the `.cimtbl` text; changed shards re-staged | Only as a shard swap between solves |
| Operating-point changes (breaker status, taps, load and generation P/Q, setpoints) | Initial values staged | Applied as updates |
| Bad data | Shard quarantined, see [Lowering](#lowering) | Shard carries a failed status |
| Tools present | Python, `cimtables`, lowering | Kernel and shards only |

A what-if equipment change, such as adding a line in a planning study, means building a variant. The per-shard
hash limits the rebuild to the shards that changed, and the variant is put in service by a
[shard swap](#what-if-cases-by-shard-swap).

The deliverable is a container image with every shard already staged. A two-stage image keeps Python out of the
runtime stage.

## Shards and boundaries

A shard is a static partition of the model with its own matrix, factors, state and controls. Only the boundary
table crosses between shards.

**Invariants**

- Every node is `(shard, local index)`. No global dense index exists anywhere in the kernel.
- A shard owns its matrix, factors, state and controls.
- Shards are static. Every tie that can ever connect two shards is declared a boundary in the model in advance;
  shards are never merged at runtime.
- Results are keyed by mRID, never by solver index.

**Where the boundaries are**

- **Feeder head.** The boundary is the feeder-head recloser, downstream of the substation transformer and its tap
  changer. It is marked in instance data with a proposed `Switch.isBoundary` boolean. The substation transformer
  and tap changer belong to the upstream shard, which must therefore be three-phase at least from that transformer
  down.
- **Containment.** Shard nesting comes from a proposed `ConnectedArea.ContainedWithin` self-association,
  preprocessed at build time. The feeder-area definitions may need adjustment.
- **Secondaries.** Radial service stars are not shards; they are load models inside the feeder shard. See
  [Secondary stars as load models](#secondary-stars-as-load-models).

The boundary switch is an ordinary [switch constraint row](#switches-as-constraint-rows). Its current unknown is
the equivalent injection and its upstream node voltage is the boundary voltage. An open boundary recloser marks
its shard dead.

The ENTSO-E frame and boundary-model drafts describe the same idea: each frame a shard, with boundary nodes as the
stitch points. Their class-level mapping to this design has not been checked.

**The shard graph is not always a tree.** A spot network is fed from several primary feeders, so it is contained
in none of them. It must hang from the common ancestor (the substation shard) with a declared boundary to each
feeder, which makes the shard graph a DAG. Spot networks are [deferred](#deferred).

## The IR

One struct-of-arrays table per element kind, integer indices in place of mRIDs, ragged matrices as an offset array
plus a flat value array, all values in SI. The container is Parquet.

The IR is split into three layers, following CIM's separation of equipment, steady-state inputs and solved state:

| Layer | Holds | Changes |
|---|---|---|
| Model | Topology, element parameters, per-length impedance matrices, the stamp map | At build time only |
| Operating point | Switch status, tap positions, load and generation P/Q, setpoints, control state | At runtime, every step |
| Solution | Conductor voltages, switch currents, island frequency, shard status | Written by the kernel |

This is the dispatcher-training-simulator arrangement: the factors are cached, and a time step touches only the
operating-point arrays. A scenario is a thin operating-point overlay on shared model shards, not a copy of the
model.

**Elements are not always CIM objects.** Two circuits on a shared spacing are two `ACLineSegment` objects but one
coupled primitive, so the IR has a coupled-group element.

**Stamp map.** For each element whose admittance can change at runtime, the IR records its positions in the
matrix value array. Only switches and tap changers need one; everything else that changes at runtime is on the
right-hand side.

**Time series** live in Parquet sidecars keyed by mRID, for input (load curves) and for output (voltages and
powers). They do not belong in `.cimtbl`.

## Lowering

Lowering turns one bound shard into its IR tables. It is new work, in Python, and is where CIM semantics become
specific per class, unlike the reflective validator and binder.

- **Per-element.** Each row lowers to its primitive admittance as a pure function. The only global pass is index
  assignment.
- **Values.** Read through `graph_write.get_qty`, in one unit system.
- **Derived parameters.** Line constants from spacing and wire data are computed here, not in the kernel.
- **Supported classes.** An explicit list per profile, limited at first to basic equipment. Anything electrically
  relevant that is not on it raises: STATCOM, HVDC, SVC and phase shifters are examples. They are added one at a
  time as enhancements. Silent omission is the failure mode to rule out.
- **Sanity checks.** A neutral conductor with no path to ground raises. A secondary with no voltage reference
  raises where it can be detected.
- **Quarantine.** With thousands of real feeders, raising on every data error means the state never loads. A shard
  that fails lowering is replaced by a static injection at its boundary and carries a failed status: warn and
  replace. This is the same mechanism as the solve-time fallback.
- **Build report.** The quarantined shards and the reasons are an artifact of the build and are visible at runtime
  as shard status.
- **Versioning.** The IR schema carries a version stamp.

## Kernel formulation

### Unknowns

An unknown is the voltage of one conductor at one node, in rectangular coordinates, stored as real 2×2 blocks.

- Conductors are explicit, including neutrals and grounds. Nothing is reduced away by default.
- A node has a variable number of conductor slots. The target is seven: two circuits with a shared neutral, which
  CIM 18 supports through `sequenceNumber` 1 to 7 and `cimtables` already writes through `sequenceNumbers`.
- The kernel knows `(node, conductor slot)` and never phase names. Phase labels exist only in lowering and in
  results.
- Loads and transformer windings attach to pairs of slots, which covers phase-to-neutral and line-to-line
  connections generically.

### Primitive admittance stamping

Each element contributes a primitive admittance matrix over its conductor slots, stamped into the shard matrix.
The element kinds are a closed set, each a table with a vectorized stamp. There is no open class hierarchy with
per-device virtual calls.

One assembly serves both solution methods:

- **Fixed-admittance iteration.** Loads are current injections; the matrix changes only on switch and tap
  operations. The factors are cached and each iteration is a back-substitution. This is the method for stepping
  through a time series.
- **Newton, current-injection form.** The same matrix plus load-dependent diagonal blocks. The sparsity pattern is
  fixed, so symbolic analysis happens once and each iteration is a numeric refactorization. This is the method for
  cold starts and for steps where the fixed-admittance iteration stalls.

Voltage-controlled nodes are the known awkward case in current-injection Newton and need an extra reactive-power
unknown per regulated node. This is expected to be where convergence work on the transmission side goes.

### Switches as constraint rows

Each switch `k` between nodes `i` and `j` has its own unknown, the current `i_k` through it, and its own equation:

```
node i:       ... + i_k = 0
node j:       ... − i_k = 0
switch row:   closed:  V_i − V_j − z_sw·i_k = 0
              open:    i_k = 0
```

Every entry position exists in both states, so a status change edits values only. Switch currents are solved
quantities, so breaker flows are available directly.

- **Closed loops of switches** (a ring bus, a breaker-and-a-half bay) make ideal closed-switch equations
  dependent. The small real impedance `z_sw` on the closed row fixes this while keeping the row scaled near 1.
- **Islands and dead nodes.** A node with every switch open has no equation and a de-energized section has no
  reference. A connectivity pass over integer arrays flags dead nodes, which are pinned to zero, and picks the
  angle reference of each island. Every node has a structural diagonal entry so that pinning is values-only. This
  pass runs in the kernel, because breaker operations arrive at runtime. It only sets flags; it never rewrites the
  model.
- **Pivoting.** A status change moves a diagonal value between about 0 and 1, so the previous pivot order may not
  be safe. The symbolic analysis is reused, and the numeric factorization is redone with pivoting. Parameter-only
  changes use the cheaper refactorization. That the symbolic block ordering stays valid across status changes is
  believed but not yet tested.

### Secondary stars as load models

A radial star of a few meters (typically three to seven) behind one service transformer is treated as a load
model of the feeder shard, not as a shard.

- The passive part, the transformer and the service drops, is linear. It is eliminated exactly at assembly into an
  equivalent admittance at the primary node plus two small dense matrices per star.
- The equivalent is stamped on the primary node's own diagonal block, so the feeder's sparsity pattern is
  unchanged.
- Each feeder iteration updates only the load currents: meter voltages from the primary voltage and load currents,
  then the current reflected to the primary.
- Meter voltages are recovered by back-substitution through the stored matrices. They are computed on demand or
  for a selected set; only primary-side results are stored by default, because meter-level output for millions of
  meters at every step would dominate the output sidecar.
- Stars are grouped by shape for layout. Service-drop lengths differ, so the matrices are per instance.
- **Eligibility.** One primary connection and no secondary mesh. Anything else raises as unmodelled.

### Frequency and slack

There is no slack bus. Every generator has frequency droop, 5% by default, and the mismatch is shared through
frequency.

- Frequency is one unknown per synchronous island, coupled to every droop generator's equation. It is handled as
  a border on the system, outside the sparse factorization.
- One angle per island is pinned as the reference.
- Frequency is a solution variable and an input, stored in the solution layer and carried in the boundary table,
  so that droop on distribution DER can be added without changing the boundary.

Distributed slack is easy to build in at the start and very hard to retrofit, which is why it is decided now.

### Scaling

The IR is in SI. The kernel scales each shard by nominal voltage, so conditioning across voltage levels is handled
in one place.

### Linear solver interface

The kernel calls its sparse solver through four separate operations: `analyze`, `factor`, `refactor`, `solve`.
The reference solver implements the same interface on SciPy.

## Coupling shards

Shards couple by equivalent injection. There is no federation and no shared matrix.

- A shard is solved with its boundary voltage imposed and returns `(injection, optional sensitivity, status)`.
- The injection is current per conductor slot for the given slot voltages. Per-phase P and Q is ambiguous where
  return current flows in a neutral, so P and Q are derived for reporting and for the fallback only.
- The sensitivity, the derivative of the injection with respect to boundary voltage, is optional. Ignored, the
  scheme is a plain equivalent-injection iteration. Used, the upstream solve gets near-Newton coupling through the
  same interface. Shards with several boundaries, such as spot networks, are expected to need it.
- **Fallback.** If a shard fails to solve, the upstream shard uses its last P and Q injection with zero
  sensitivity. One failing feeder never stops the solve.

## Convergence

Three nested loops, each with its own test:

1. **Inside a shard.** The largest current mismatch at any node, in per-unit on the shard base, is below
   tolerance. The test is on the residual, not the step size.
2. **At the boundary.** After the upstream solve, each shard re-solves at the new boundary voltage; the test is
   the change in its injection. The per-boundary tolerance is the transmission mismatch tolerance divided by the
   number of feeders, because errors aggregate: a thousand feeders each off by 1 kW is 1 MW upstream. The inner
   tolerance is about two orders tighter.
3. **Controls.** A pass with no tap, capacitor or limit change. Discrete controls and reactive capability limits
   run in this outer loop, with a cap on passes and a rule against flipping the same device back and forth.

Rules that make the result reproducible:

- **Parallel updates only.** Every shard solves against the upstream voltages of the previous outer iteration,
  never against a neighbour's fresh result. Boundary sums accumulate in shard-id order. Results do not depend on
  thread count or scheduling.
- **Shard status** is one of converged, fallback (with the age of the stale injection), dead, or diverged.
  Divergence is declared on residual growth, voltage outside a sane band, or an iteration cap.
- **Global status** distinguishes "converged with N fallbacks" from "converged".
- **Final global check.** Once the loops exit, the mismatch is evaluated at every node of every shard, boundaries
  included, from the final state. That number, with its location, is the exit criterion; the loop tests only
  decide when to stop iterating.

## Cold start

A flat start fails across delta-wye phase shifts and mixed voltage levels in three-phase. The cold start is a
no-load linear solve with sources only, so the kernel supports a linear-only mode from the beginning.

## Runtime updates

Runtime updates are operating-point changes only.

| Update | Effect |
|---|---|
| Load and generation P/Q, setpoints | Right-hand side only |
| Tap position | Matrix values through the stamp map, then refactorization |
| Switch status | Matrix values through the stamp map, island flags recomputed, numeric factorization with pivoting |

A solve needs a fixed view of the operating point while updates arrive; updates create a new version rather than
mutating in place.

## What-if cases by shard swap

Planning needs a snapshot power flow between what-if cases. The kernel's contract for this is a shard swap. The
kernel never interprets a difference message; the model compiler does, and the kernel only sees the resulting
shards.

### The kernel contract

- **Request.** The root hash the change applies to, and a map of shard id to new shard hash. The map may add or
  remove shards and may carry a new boundary table.
- **Sequence.** Stop accepting solves and let the running one finish; verify the base hash; load and check the new
  shards; swap; start.
- **Atomic.** If any new shard fails its check, nothing changes and the previous case stays in service.
- **Reversible.** Shards are kept in a content-addressed store, so returning to a case is a swap to its hashes.
  The kernel needs no reverse differences. Cases share every shard they did not change.

### What carries over a swap

- **Untouched shards** keep their factors and solution.
- **Swapped shards** warm-start by mRID where nodes survive and cold-start otherwise.
- **The operating point** is rebound by mRID. Entries for removed equipment are dropped with a warning; new
  equipment takes its staged values.
- **Results** are stamped with the new root hash.

### The compiler side

A planning deployment includes the build stage, or runs a staging service beside the kernel. It turns a change
into new shards and classifies it:

| Change | Result |
|---|---|
| Operating point only | No swap; an ordinary [runtime update](#runtime-updates) |
| Equipment inside one shard | That shard is re-staged |
| Crosses or moves a boundary | Both shards and the boundary table, in one atomic swap |

The first version treats a case as edited `.cimtbl` text and lets the hash cache find the changed shards.
Accepting a CIM difference message directly needs a way to apply one to a bound model, which `cimtables` does not
have: `Update` is additive only.

Re-staging one feeder is estimated at seconds to tens of seconds at the current bind rate; the swap itself at
milliseconds.

## Cuts, clamps and jumpers

They are supported only through the shard swap, never as a runtime operation, and need no kernel concept of
their own.

- **Placing a cut or clamp is structural.** It splits a segment at a distance along it and adds an internal node.
  Lowering does this as plain segment splitting with length-proportional impedance, so the kernel sees shorter
  lines.
- **Operating one is a runtime update.** `Cut` and `Jumper` are believed to be `Switch` subclasses, with `Clamp`
  as the attachment point; once placed they are ordinary [switch constraint rows](#switches-as-constraint-rows).
- **Coupled circuits.** A cut on one circuit of a shared spacing splits the whole coupled group at that point.
- **A jumper between two feeders** breaks the static-shard rule unless a boundary is declared there. It goes
  through the two-shard swap, or it raises.
- **Reassigning a span to another feeder** is a boundary move: both shards and the boundary table in one swap.

Until the swap exists, `Cut`, `Clamp` and `Jumper` raise as unmodelled; see [Deferred](#deferred).

## Persistence and hashing

Only the IR is persisted. Primitive matrices, star reductions, the assembled matrix, the ordering and the factors
are rebuilt in memory at load, deterministically from the IR. One persisted artifact means one invalidation
problem.

- **Hash inputs.** The bytes of every file in the shard's `Import` subtree, the boundary file, the profile and
  `cimtables` versions, the lowering code version, the IR schema version and the unit configuration. Inputs only,
  never floating-point outputs.
- **Structure.** One hash per shard and a root hash over the manifest.
- **Role.** The `.cimtbl` text is the source of truth. A committed change is applied to the text, and the shard
  hash keeps the staged shard in sync with it. At runtime the hash is a safety net: the root hash is checked at
  startup and stamped into every results file, so a result traces to the model that produced it.

## Validation

- **OpenDSS** is the oracle for each distribution shard.
- **GridPACK** is the oracle for transmission, through the existing topology-processing and RAW export path. It is
  a reference, not a component.
- **The reference solver** (NumPy and SciPy, one shard) is where device models are debugged against OpenDSS. The
  compiled kernel is validated against it on the same IR.
- Tolerances are fixed before device models are written.

## Technology stack

| Layer | Choice | Reason |
|---|---|---|
| Model compiler | Python: `cimtables` plus lowering, process-parallel | Reuses all format semantics; runs at build time |
| IR | Sharded Parquet | The runtime never imports Python or cim-graph |
| Kernel | C++20, KLU per shard, task parallelism (one shard per task), standalone library | HPC toolchains and SuiteSparse are first-class in C++ |
| Outer parallelism | MPI or scheduler job arrays over time steps and scenarios | Trivially parallel |
| Reference solver | NumPy and SciPy on a single shard | Correctness, not speed |

Notes:

- **No distributed-memory linear algebra at first.** A single snapshot of the full model is estimated to fit on
  one many-core node. The HPC need comes from time series and scenarios. This should be measured before anything
  more is built.
- **GridPACK and PETSc** are not the foundation. GridPACK is bus-branch and closely tied to the RAW format.
- **A native `.cimtbl` reader** is not planned. The grammar is the cheap part; the validate and bind semantics are
  the cost, and a second implementation would drift. If staging speed becomes the bottleneck, the first step is a
  Python backend that binds straight to columns using the same rule functions.
- **Licensing.** The project is to be open-source. KLU is LGPL, which should be compatible; confirm with the
  release office.

## What this needs from `cimtables` and the profile

These are gaps today.

- **An assembly level above `Import`.** `Import` is textual composition into one network, and the settings are
  once per `Import` chain, so frames with their own `Model =` cannot be imported into one master. Shards need a
  manifest of frames plus the boundary file, with each frame loadable alone. A boundary file imported by both
  sides already works, since a file imported from two branches is not a cycle.
- **Boundary identity.** UUIDs are seeded per model scope, so a boundary node loaded in two frames gets two UUIDs.
  Whether boundaries stitch by mRID string or by UUID must be decided.
- **Partition-aware loading.** Binding one shard alone needs stubs for the boundary nodes it references, and
  mRID uniqueness must be checked across shards.
- **Bind speed.** Binding is measured at roughly 3,300 to 3,900 rows per second. A state-sized model is hours
  serially, so staging must be parallel across shards and cached by hash.
- **Profile extensions.** `Switch.isBoundary` and `ConnectedArea.ContainedWithin`.
- **Applying a difference message.** Needed only to accept CIM difference messages for
  [what-if cases](#what-if-cases-by-shard-swap); edited text works without it.

## Deferred

- Open wye–open delta transformer banks.
- Spot networks and area grid networks (shards with several boundaries; network protectors as a directional
  discrete control).
- STATCOM, HVDC, SVC, phase shifters and other equipment outside the basic list.
- `Cut`, `Clamp` and `Jumper`, which depend on the shard swap.
- CIM difference messages as the input to a what-if case.
- Coupling by sensitivity (the optional term in the shard interface).
- Distributed-memory solves.
- Droop on distribution DER.

## Open questions

- Is transmission modelled in three-phase from its sequence data throughout, or positive-sequence above
  three-phase substations? The upstream side of every feeder boundary must be three-phase either way.
- What is the first supported class list, exactly?
- What static injection replaces a quarantined shard, and where does its value come from?
- What form does the assembly manifest take: a new `.cimtbl` statement or a separate file?
- Do the ENTSO-E frame and boundary drafts map onto the manifest and boundary file as assumed?
- Does the reuse of symbolic analysis across switch status changes hold in practice with KLU?
- What are the validation tolerances against OpenDSS and GridPACK?
- Are `Cut` and `Jumper` `Switch` subclasses in cim18v17, as assumed?
- Does a planning deployment carry the build stage in its image, or run a separate staging service?
- Where does the solver live: which repository and package name?
