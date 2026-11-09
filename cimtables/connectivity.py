"""The connectivity backend - node/bus/terminal/phase.

The one place that acts on the node/bus columns (synthetic.CONNECTIVITY_COLUMNS)
and the phases shorthand. bind_row and every builder's add_connectivity call
these two functions - no per-class node-vs-bus or phase logic anywhere else.
"""

from __future__ import annotations

import re
import typing

from cimgraph.models import GraphModel

from cimtables import graph_write, synthetic

if typing.TYPE_CHECKING:  # annotations only: the code reflects on network.cim, whatever its profile
    import cimprofiles.cim18.cim18v17.connectivity_unbalanced as CN


def _parent_assoc_field(phase_cls: type, parent: object) -> str:
    """The field on phase_cls whose type is the parent's class (or a base of
    it) - not always type(parent).__name__ (e.g. LinearShuntCompensatorPhase's
    field is 'ShuntCompensator', the base class name, not
    'LinearShuntCompensator'). Same type-hint resolution
    graph_write._cim_unit_type uses for CIMUnit, applied to find the
    parent-association field instead."""
    hints = graph_write.type_hints(phase_cls)
    for field_name, hint in hints.items():
        for arg in typing.get_args(hint) or (hint,):
            if isinstance(arg, type) and isinstance(parent, arg):
                return field_name
    raise ValueError(
        f"{phase_cls.__name__} has no field whose type matches "
        f"{type(parent).__name__}"
    )


def _resolve_node(network: GraphModel, col_name: str, node_mrid: str,
                  node_class_name: str) -> CN.ConnectivityNode | CN.TopologicalNode:
    """The node_class_name (ConnectivityNode/TopologicalNode) for a connectivity column, auto-vivified if new."""
    if synthetic.CONNECTIVITY_COLUMNS.get(col_name, ('', 0))[0] != node_class_name:
        valid = [c for c, (cls_name, _) in synthetic.CONNECTIVITY_COLUMNS.items() if cls_name == node_class_name]
        raise ValueError(
            f"{col_name!r} is not a {node_class_name} connectivity column (one of {valid})"
        )
    node_cls = getattr(network.cim, node_class_name)
    node_obj = graph_write.resolve(network, node_cls, node_mrid)
    if node_obj is None:
        node_obj = node_cls(mRID=node_mrid)
        graph_write.add_to_graph(network, node_obj)
    return node_obj


def end_owner_field(cim: CN, end: CN.TransformerEnd) -> str | None:
    """The end's reference to the ConductingEquipment its Terminal belongs to:
    'PowerTransformer' on a PowerTransformerEnd, 'AutoTransformer' on an
    AutoTransformerEnd, None on an end with none (TransformerTankEnd). Found
    from the type hints, not a per-class table."""
    for field_name, hint in graph_write.type_hints(type(end)).items():
        for arg in typing.get_args(hint) or (hint,):
            if isinstance(arg, type) and issubclass(arg, cim.ConductingEquipment):
                return field_name
    return None


def add_connectivity(network: GraphModel, equipment: CN.ConductingEquipment|CN.TransformerEnd, *,
                     node_cols: dict[str, str] | None = None, bus_cols: dict[str, str] | None = None,
                     equipment_container: CN.EquipmentContainer | None = None) -> list[CN.Terminal]:
    """node_cols is the {'node1': 'RG60', 'node2': '362'} slice of a <Class>Row
    (ConnectivityNode mRIDs), bus_cols the {'bus1': ..., 'bus2': ...} slice
    (TopologicalNode mRIDs). Give either or both: node1 + bus1 put one
    Terminal on both its ConnectivityNode and its TopologicalNode (node-breaker
    detail plus bus-branch topology, as PSS/E RAW's substation node data
    does). Each column fixes its Terminal sequenceNumber
    (synthetic.CONNECTIVITY_COLUMNS), so header order doesn't matter; a
    column of the wrong kind, or two node (or two bus) columns on one
    sequenceNumber, raise. Auto-vivifies any mRID not found via resolve().
    Returns Terminals in sequenceNumber order.

    A TransformerEnd takes one terminal's worth (a node and/or a bus): the
    Terminal belongs to the equipment the end references (PowerTransformerEnd.
    PowerTransformer, AutoTransformerEnd.AutoTransformer), sequenceNumber =
    endNumber, and end.Terminal points at it. That reference must already be
    set. An end with a real PhaseCode `phase` (AutoTransformerEnd) gives its
    Terminal the same phases.

    Sets equipment.EquipmentContainer from `equipment_container`, falling back
    to network.container when not given (a TransformerEnd's nodes take its
    transformer's container instead). Propagates the same container
    onto every node touched (ConnectivityNodeContainer), without overwriting
    one already set."""
    cim:CN = network.cim # type:ignore
    nodes_by_sequence: dict[int, dict[str, CN.ConnectivityNode | CN.TopologicalNode]] = {}
    for node_class_name, cols in (('ConnectivityNode', node_cols or {}), ('TopologicalNode', bus_cols or {})):
        for col_name, node_mrid in cols.items():
            node_obj = _resolve_node(network, col_name, node_mrid, node_class_name)
            nodes = nodes_by_sequence.setdefault(synthetic.CONNECTIVITY_COLUMNS[col_name][1], {})
            if node_class_name in nodes:
                raise ValueError(
                    f"{equipment.mRID!r}: {list(cols)} put two {node_class_name} columns on terminal "
                    f"{synthetic.CONNECTIVITY_COLUMNS[col_name][1]}"
                )
            nodes[node_class_name] = node_obj
    if not nodes_by_sequence:
        raise ValueError(f"{equipment.mRID!r}: add_connectivity needs at least one node or bus column")

    is_end = isinstance(equipment, cim.TransformerEnd)
    if is_end:
        owner_field = end_owner_field(cim, equipment)
        if owner_field is None:
            raise ValueError(f"{equipment.mRID!r}: {type(equipment).__name__} has no ConductingEquipment reference to put a Terminal on")
        transformer = getattr(equipment, owner_field)
        if transformer is None or equipment.endNumber is None:
            raise ValueError(
                f"{equipment.mRID!r}: a node/bus column on a {type(equipment).__name__} needs its {owner_field} and endNumber"
            )
        if len(nodes_by_sequence) != 1:
            raise ValueError(
                f"{equipment.mRID!r}: a TransformerEnd takes one terminal's node/bus columns, "
                f"got {list(node_cols or {})} + {list(bus_cols or {})}"
            )
        [nodes] = nodes_by_sequence.values()
        nodes_by_sequence = {equipment.endNumber: nodes}
        terminal_owner, container = transformer, transformer.EquipmentContainer
    else:
        terminal_owner = equipment
        container = equipment_container if equipment_container is not None else network.container
    has_container = isinstance(container, cim.EquipmentContainer)

    terminals = []
    for sequence_number in sorted(nodes_by_sequence):
        terminal = cim.Terminal(mRID=f'{terminal_owner.mRID}_T{sequence_number}', sequenceNumber=sequence_number)
        graph_write.set_assc(terminal, cim, 'ConductingEquipment', terminal_owner)
        for node_field, node_obj in nodes_by_sequence[sequence_number].items():  # node class name = Terminal field
            if has_container and node_obj.ConnectivityNodeContainer is None:
                graph_write.set_assc(node_obj, cim, 'ConnectivityNodeContainer', container)
            graph_write.set_assc(terminal, cim, node_field, node_obj)
        graph_write.add_to_graph(network, terminal)
        terminals.append(terminal)

    if is_end:
        graph_write.set_assc(equipment, cim, 'Terminal', terminals[0])
        end_phase = getattr(equipment, 'phase', None)
        if isinstance(end_phase, cim.PhaseCode):
            graph_write.set_attr(terminals[0], cim, 'phases', end_phase)
    elif has_container:
        graph_write.set_assc(equipment, cim, 'EquipmentContainer', container)
    return terminals


_PHASE_TOKEN = re.compile(r's1|s2|[ABCN]')
PHASE_ORDER = ['A', 'B', 'C', 's1', 's2', 'N']


def parse_phases(phases: str) -> list[str]:
    """'CB' -> ['C', 'B'], 's1s2' -> ['s1', 's2']: SinglePhaseKind tokens in
    the order written (= conductor order, e.g. matrix row order for a line)."""
    tokens = _PHASE_TOKEN.findall(phases)
    if ''.join(tokens) != phases or len(set(tokens)) != len(tokens):
        raise ValueError(f"phases {phases!r} is not an ordered set of A/B/C/s1/s2/N")
    return tokens


def phase_code(tokens: list[str]) -> str:
    """Canonical PhaseCode value for a token set: ['C', 'B'] -> 'BC',
    ['s1', 's2', 'N'] -> 's12N'."""
    ordered = ''.join(sorted(tokens, key=PHASE_ORDER.index))
    return ordered.replace('s1s2', 's12')


def phase_cls(cim: CN, obj: object) -> type | None:
    """The per-phase child class for obj: <Class>Phase for the nearest class
    in its MRO that has one (LinearShuntCompensator -> LinearShuntCompensatorPhase,
    Breaker -> SwitchPhase), or None (VoltageRegulator, a TransformerEnd)."""
    for cls in type(obj).__mro__:
        child_cls = getattr(cim, f'{cls.__name__}Phase', None)
        if child_cls is not None:
            return child_cls
    return None


# Normative delta rule (SinglePhaseKind): the child connects its phase "to the next
# logical non-neutral phase".
NEXT_PHASE = {'A': 'B', 'B': 'C', 'C': 'A', 's1': 's2'}


def phase_plan(cim: CN, obj: object, terminal_count: int, *, phases: str | None = None,
               phaseSide1: str | None = None, phaseSide2: str | None = None
               ) -> tuple[list[str], type | None, list[tuple[str, dict]]]:
    """What a phases (or phaseSide1 + phaseSide2) cell makes on obj, without
    making it: (the PhaseCode value of each terminal, the child class, and
    [(child mRID, {field: value})] in sequence order). The child class is None
    on a TransformerEnd, whose phases live on its terminal only. Raises
    ValueError where binding the cell would. add_phase_children builds from
    it; the writer folds children back into a phases cell by comparing them
    against it.

    phaseSide1='AB', phaseSide2='AC' instead of phases: a switch whose sides
    differ (only a child class with phaseSide1/phaseSide2, i.e. SwitchPhase).
    The sides pair by written position (A-A, B-C); terminal 1 gets side 1's
    PhaseCode, terminal 2 side 2's.

    Every child gets the normative SinglePhaseKind (`phase`, or SwitchPhase's
    phaseSide1/phaseSide2) and a sequenceNumber where the class has one:
    - default (wye, lines, switches): one child per token in written order.
    - delta shunt (phaseConnection=D): one child per line-to-line branch, whose
      `phase` is the branch's from-phase (normative CIM: "connected from the
      indicated phase to the next logical non-neutral phase"), plus the branch
      PhaseCode in the `phases` extension where the class has it: a delta load
      on BC is one child phase=B, phases=BC; on CA it's phase=C, phases=AC."""
    if not terminal_count:
        raise ValueError(f"{obj.mRID!r}: a phases column needs a node/bus column to put the phases on")
    if phases is not None and (phaseSide1 is not None or phaseSide2 is not None):
        raise ValueError(f"{obj.mRID!r}: give either phases or phaseSide1/phaseSide2, not both")
    if isinstance(obj, cim.TransformerEnd) and 'phase' in graph_write.type_hints(type(obj)):
        raise ValueError(f"{obj.mRID!r}: {type(obj).__name__} has a real phase attribute; its Terminal takes that, not a phases column")
    if phases is not None:
        tokens = side2_tokens = parse_phases(phases)
        codes = [phase_code(tokens)] * terminal_count
    else:
        if phaseSide1 is None or phaseSide2 is None:
            raise ValueError(f"{obj.mRID!r}: phaseSide1 and phaseSide2 must be given together")
        tokens, side2_tokens = parse_phases(phaseSide1), parse_phases(phaseSide2)
        if len(tokens) != len(side2_tokens) or terminal_count != 2:
            raise ValueError(
                f"{obj.mRID!r}: phaseSide1 {phaseSide1!r} / phaseSide2 {phaseSide2!r} need the same "
                f"number of phases and two terminals (got {terminal_count})"
            )
        codes = [phase_code(tokens), phase_code(side2_tokens)]
    if isinstance(obj, cim.TransformerEnd):
        return codes, None, []  # an end's phases live on its terminal only

    child_cls = phase_cls(cim, obj)
    if child_cls is None:
        raise ValueError(f"{type(obj).__name__} has no <Class>Phase child class for a phases column")
    child_hints = graph_write.type_hints(child_cls)
    has_sides = 'phaseSide1' in child_hints
    if tokens != side2_tokens and not has_sides:
        raise ValueError(f"{obj.mRID!r}: {child_cls.__name__} has one phase per child, so phaseSide1/phaseSide2 can't differ")

    if getattr(obj, 'phaseConnection', None) == cim.PhaseShuntConnectionKind.D:
        from_phases = [t for t in tokens if NEXT_PHASE.get(t) in tokens]
        if not from_phases:
            raise ValueError(f"{obj.mRID!r}: a delta connection needs two non-neutral phases, got {phases!r}")
        children = []
        for t in from_phases:
            branch_code = phase_code([t, NEXT_PHASE[t]])
            fields = {'phase': cim.SinglePhaseKind(t)}
            if 'phases' in child_hints:
                fields['phases'] = cim.PhaseCode(branch_code)
            children.append((f'{obj.mRID}_{branch_code}', fields))
    elif has_sides:  # SwitchPhase: phaseSide1/phaseSide2 instead of phase
        children = [(f'{obj.mRID}_{t1}', {'phaseSide1': cim.SinglePhaseKind(t1), 'phaseSide2': cim.SinglePhaseKind(t2)})
                    for t1, t2 in zip(tokens, side2_tokens)]
    else:
        children = [(f'{obj.mRID}_{t}', {'phase': cim.SinglePhaseKind(t)}) for t in tokens]

    if 'sequenceNumber' in child_hints:
        for i, (_, fields) in enumerate(children, start=1):
            fields['sequenceNumber'] = i
    return codes, child_cls, children


def add_phase_children(network: GraphModel, obj: CN.ConductingEquipment, terminals: list[CN.Terminal], *,
                       phases: str | None = None, phaseSide1: str | None = None,
                       phaseSide2: str | None = None) -> list[CN.PowerSystemResource]:
    """phases='ABCN' (or phaseSide1 + phaseSide2) -> per-phase children (e.g.
    ACLineSegmentPhase) wired to obj, and every terminal's PhaseCode, as
    phase_plan describes. Returns the children (none on a TransformerEnd)."""
    cim = network.cim
    codes, child_cls, plan = phase_plan(cim, obj, len(terminals), phases=phases,
                                        phaseSide1=phaseSide1, phaseSide2=phaseSide2)
    for terminal, code in zip(terminals, codes):
        graph_write.set_attr(terminal, cim, 'phases', cim.PhaseCode(code))
    if child_cls is None:
        return []
    parent_field = _parent_assoc_field(child_cls, obj)
    children = []
    for mRID, fields in plan:
        child = child_cls(mRID=mRID, **fields)
        graph_write.set_assc(child, cim, parent_field, obj)
        graph_write.add_to_graph(network, child)
        children.append(child)
    return children
