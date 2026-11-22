# Binding a row

A `RawRecord` becomes a live cim-graph object in two steps: `validate.validate` checks and types it against the
CIM profile without touching the graph, and `binder.bind_row` builds the object and wires it into the model.
Neither has a list of classes; both reflect on the profile module.

## `validate`: reflection on the profile

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    participant caller as load_model
    participant v as validate.validate
    participant prof as cim profile module<br/>(network.cim)
    participant rowcls as validate._row_dataclass

    caller ->>+ v: validate(record)
    v ->>+ prof: getattr(cim, record.cim_class)
    prof -->>- v: CIM dataclass (unknown → error)
    v ->> v: _real_fields(cls): every serialized field,<br/>classified quantity / reference / scalar
    v ->> v: every column is a real field, a DSL column<br/>(synthetic.py) or the row key, else error<br/>with the closest names ("did you mean 'q'?")
    v ->>+ rowcls: _row_dataclass('<Class>Row', profile)
    rowcls -->>- v: <Class>Row (built once per profile and class)
    v ->> v: quantity → Qty(float, unit, base)<br/>reference → mRID string (resolved at bind)<br/>scalar → _coerce (bool, int, float, enum)
    v -->>- caller: <Class>Row(mRID, ..., source_file, source_line)
```

- **The row class mirrors the CIM class.** `<Class>Row` is a plain dataclass made with `make_dataclass` from the
  CIM class's own fields, plus the DSL columns from `synthetic.py`. A real CIM attribute wins over a DSL column of
  the same name: on `Terminal`, `phases` is the real `PhaseCode`, not the shorthand.
- **References stay strings.** A reference cell is only checked to be a reference slot here; whether its target
  exists is a question about the graph, answered at bind time.
- **Reflection is cached** per class (`graph_write.type_hints`, `validate._real_fields`). Classes never change
  once imported, so never mutate the cached results.

## `bind_row`

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    participant caller as load_model
    participant b as binder.bind_row
    participant gw as graph_write
    participant conn as connectivity
    participant graph as GraphModel

    caller ->>+ b: bind_row(network, row, update)
    alt update
        b ->> gw: resolve(network, cls, mRID) (must exist)
    else mRID already bound
        b -->> caller: _restate(): same facts → the bound object, else raises
    else new
        b ->> b: cls(mRID=...) (UUID seeded from the mRID)
    end
    loop every set field of the row
        b ->> b: classify from the profile alone:<br/>node*/bus* · phases* · EquipmentContainer ·<br/>reference · relative Qty · plain value
        b ->> gw: set_attr(obj, field, value) for plain values
    end
    loop references
        b ->>+ gw: resolve(network, slot range, mRID)
        gw ->> graph: find_by_mRID(mRID) + isinstance check
        gw -->>- b: target (missing → error)
        b ->> gw: set_assc(obj, slot, target) (both sides)
    end
    opt node / bus columns
        b ->>+ conn: add_connectivity(network, obj, node_cols, bus_cols, container)
        conn -->>- b: Terminals <mRID>_T<n>
    end
    opt phases columns
        b ->> conn: add_phase_children(network, obj, terminals, phases, ...)
    end
    opt wkt
        b ->> b: _bind_wkt(): Location + PositionPoints
    end
    b ->> gw: set_attr(obj, field, pu/percent Qty, system_base)
    b ->> gw: add_to_graph(network, obj)
    b -->>- caller: obj
```

### Classifying fields

Each field the row sets goes to exactly one place, decided by the profile and `synthetic.py`, never by the class:

| Field | Goes to |
|---|---|
| `node`, `node1`, `node2`, `bus`, `bus1`, `bus2` | `connectivity.add_connectivity` |
| `phases`, `phaseSide1`, `phaseSide2`, `sequenceNumbers` | `connectivity.add_phase_children` |
| `EquipmentContainer` | resolved; also the container the Terminals' nodes take |
| any other reference slot | `graph_write.resolve` + `set_assc` |
| a `pu` / `percent` quantity | `set_attr` with the base, **after** everything else |
| anything else, `name` included | `graph_write.set_attr` |

The order inside `bind_row` is load-bearing: references before connectivity (a `TransformerEnd`'s Terminal goes
on the transformer it references), and relative impedances last (their base reads the ratings, `BaseVoltage` and
`From*` references the row just set).

### Restating an object

A formative row (`Object`/`Table`) whose mRID is already bound is a **restatement**. If its class is the same and
every cell it sets equals what is bound, `bind_row` returns the bound object and changes nothing; anything else
raises. That is what lets every feeder file declare its own `BaseVoltage`s and a catalog be imported from two
branches. `Update` rows are the only way to add to an existing object.

### Errors carry the row's location

Any `ValueError` or `TypeError` while binding a row read from a file is re-raised as `CimtblBindError`
(a subclass of both) prefixed `<file>:<line>:`, with the original as `__cause__`. A row built in code (no
`source_file`) raises the original unchanged.

## Connectivity and phases

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    participant b as bind_row
    participant conn as connectivity
    participant gw as graph_write
    participant graph as GraphModel

    b ->>+ conn: add_connectivity(network, line, node_cols={'node1': 'sourcebus', 'node2': 'n1'})
    loop each node column
        conn ->> gw: resolve(network, ConnectivityNode, 'sourcebus')
        opt not declared yet
            conn ->> gw: add_to_graph(ConnectivityNode(mRID='sourcebus'))
        end
    end
    loop each sequenceNumber, from the column name
        conn ->> conn: Terminal(mRID='ln_trunk_T1', sequenceNumber=1)
        conn ->> gw: set_assc(terminal, ConductingEquipment, line)
        conn ->> gw: set_assc(terminal, ConnectivityNode, node)<br/>node takes the container if it has none
        conn ->> gw: add_to_graph(terminal)
    end
    conn -->>- b: [ln_trunk_T1, ln_trunk_T2]

    b ->>+ conn: add_phase_children(network, line, terminals, phases='ABC')
    conn ->> conn: phase_plan(): PhaseCode per Terminal and one child per token<br/>(child class by MRO: ACLineSegment → ACLineSegmentPhase)
    conn ->> gw: set_attr(terminal, phases, PhaseCode.ABC) for each Terminal
    loop each child
        conn ->> conn: ACLineSegmentPhase(mRID='ln_trunk_A', phase=A, sequenceNumber=1)
        conn ->> gw: set_assc(child, ACLineSegment, line)
        conn ->> gw: add_to_graph(child)
    end
    conn -->>- b: children
```

`phase_plan` is the single synthesis rule. `add_phase_children` uses it to create children, and the writer uses
it to decide whether a model's children can be folded back into a `phases` column: if the plan for the candidate
cells reproduces exactly the children in the model, they fold. That shared rule is what keeps loading and writing
inverse to each other. The rules themselves (delta branches, split phases, `phaseSide1`/`phaseSide2`,
`sequenceNumbers`) are in the design record's [Phases](../CIMTBL_DESIGN.md#phases) section.

## `graph_write`: the one mutation point

Every change to a CIM object or to the network goes through four functions:

| Function | Does |
|---|---|
| `set_attr(obj, part, attr, value, system_base=None)` | Type-checks a scalar against the field. Turns a `Qty` into the field's `CIMUnit` in SI; a `pu`/`percent` impedance is multiplied by `impedance.z_base(obj)`. |
| `set_assc(obj, part, assc, target)` | Sets an association and its inverse side (appending to a list side without duplicates). |
| `add_to_graph(network, obj)` | Adds the object and indexes its mRID. A `BasePower` also sets `network.base_power`, and a second one that disagrees raises. |
| `resolve(network, cls, mRID)` | `find_by_mRID` plus an `isinstance` check: any subclass of the slot's class matches, another class raises. |

`get_qty(obj, attr, unit, base=None)` is `set_attr`'s read side, used by exporters: the same base rule, inverted.
Keeping every write here means units, inverse associations and the mRID index can't be forgotten at a call site.
