# Connectivity and phases

In CIM, equipment never connects to a bus directly. Each piece of equipment has `Terminal`s, each `Terminal`
sits on a `ConnectivityNode`, and per-phase data lives in child objects such as `ACLineSegmentPhase`. Writing
all of that by hand is most of what makes CIM tedious. A few shorthand columns generate it for you, following the
CIM rules.

## Nodes and buses

| Column | The Terminal goes on a | Terminal `sequenceNumber` |
|---|---|---|
| `node`, `node1` | `ConnectivityNode` | 1 |
| `node2` | `ConnectivityNode` | 2 |
| `bus`, `bus1` | `TopologicalNode` | 1 |
| `bus2` | `TopologicalNode` | 2 |

```text
Table ACLineSegment: mRID, node1, node2, length (ft), BaseVoltage
    ln_trunk, sourcebus, n1, 2000, bv_12kv
```

This makes the Terminals `ln_trunk_T1` (on `sourcebus`) and `ln_trunk_T2` (on `n1`). A node that wasn't
declared is created on first use, with the cell value as its mRID. Each node takes the equipment's
`EquipmentContainer` unless it already has one.

Use `node` columns for a node-breaker or distribution model and `bus` columns for a bus-branch model. Give both
(`node1` and `bus1`) to put one Terminal on a `ConnectivityNode` and a `TopologicalNode` at once, as PSS/E RAW's
node-breaker substation data does.

!!! note "A node and a bus are two objects"
    They need different mRIDs, since mRIDs are unique across the model. A source with a single ID per node has
    to make up the second one, for example `671` for the node and `tn_671` for the bus.

### Transformer ends

A `PowerTransformerEnd` row's `node` puts its Terminal on the end's transformer, numbered by its `endNumber`:

```text
Object PowerTransformer: mRID=xf_sub, EquipmentContainer=sub
Table PowerTransformerEnd: mRID, PowerTransformer, endNumber, node, ratedU (kV), ratedS (kVA), connectionKind, BaseVoltage
    xf_sub_1, xf_sub, 1, sourcebus, 115,  5000, D, bv_115
    xf_sub_2, xf_sub, 2, 650,       4.16, 5000, Y, bv_4160
```

The Terminals are `xf_sub_T1` and `xf_sub_T2`.

## Phases

`phases` lists the conductors in order: `ABCN`, `CB`, `s1s2N`. The order matters: it is the row order of the
line's phase impedance matrix.

```text
Table ACLineSegment: mRID, node1, node2, phases, length (ft), PerLengthImpedance, BaseVoltage
    632645, 632, 645, CB, 500, mtx603, base_4160
```

For each token this makes one per-phase child, named `<mRID>_<phase>` (`632645_C`, `632645_B`), with its
`phase` and a `sequenceNumber` where the class has one. Every Terminal of the equipment gets the set's PhaseCode
in canonical order (`BC`).

!!! info "No `phases` means balanced three-phase"
    Leave `phases` blank for a balanced device. That makes no per-phase children and leaves `Terminal.phases`
    unset, which CIM defines as ABC. `phases=ABC` means something else: a per-phase device with three separate
    phases.

### Split-phase secondaries

Triplex and other split-phase service uses `s1` and `s2`: `phases=s1s2N` gives children `_s1`, `_s2`, `_N` and
the PhaseCode `s12N`.

### Conductor positions on a shared spacing

On a line that uses a `WireSpacing`, each conductor's `sequenceNumber` is its position on the spacing. When those
aren't simply 1, 2, 3, ..., give them in a `sequenceNumbers` column, space-separated, one per phase in the same
order:

```text
Table ACLineSegment: mRID, node1, node2, phases, sequenceNumbers, WireSpacing, length (ft), BaseVoltage
    ln_c1, p1, p2, ABCN,  ,         sp_double, 300, base_12kv    # positions 1 2 3 4
    ln_c2, p1, p2, ABCN,  8 9 10 4, sp_double, 300, base_12kv    # second circuit, shared neutral
    ln_s1, p2, h7, s1s2N, 5 6 7,    sp_double, 80,  base_240
```

A blank cell means 1..n. A count that doesn't match `phases`, a repeated number or one below 1 is an error.

### Delta connections

A delta-connected load or capacitor (`phaseConnection=D`) has one per-phase child per line-to-line branch, and
`N` is ignored. Following the CIM definition ("from the indicated phase to the next logical non-neutral phase"),
each child's `phase` is the branch's from-phase:

```text
Table EnergyConsumer: mRID, node, phases, phaseConnection, p (kW), q (kVAr), BaseVoltage
    ld_692, 692, CA, D, 170, 151, base_4160
```

makes one child `ld_692_AC` (named by the branch's canonical PhaseCode) with `phase=C` and, in profiles that
have the extension, `phases=AC`.

### Switches with different phases on each side

A switch whose two sides carry different phases takes `phaseSide1` and `phaseSide2` instead of `phases`. The
sides pair by position, and each Terminal gets its own side's PhaseCode:

```text
Table Breaker: mRID, node1, node2, phaseSide1, phaseSide2, normalOpen
    brk_1, n1, n2, AB, AC, false        # A to A, B to C
```

## Writing it out yourself

The shorthand is never the only way. Write `Terminal`, `ConnectivityNode` and per-phase rows like any other class
when you need to, for example to give a Terminal its own mRID or to put per-phase data on a load:

```text
Table Terminal: mRID, ConductingEquipment, ConnectivityNode, sequenceNumber
    t_ld_1, ld_n1, n1, 1
```

A column whose name is a real CIM attribute of the class (`Terminal.phases`, `EnergyConsumerPhase.phases`) is
always that attribute, never the shorthand.
