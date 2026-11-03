"""Phase 4: the connectivity backend - node/bus/terminal/phase (design: CIMTBL_DESIGN.md §5, §12.4).

The one place that acts on §3.5's node/bus columns (synthetic.CONNECTIVITY_COLUMNS) and §3.6's phase-child
synthesis. Every Builder's add_connectivity (Phase 5) is a thin wrapper around
these two functions - no per-class node-vs-bus or phase logic anywhere else.
"""

import re
import typing

import cimprofiles.cim18.cim18v17.connectivity_unbalanced as CN
from cimgraph.models import GraphModel

from cimtables import graph_write, synthetic


def _parent_assoc_field(phase_cls: type, parent: object) -> str:
    """The field on phase_cls whose type is the parent's class (or a base of
    it) - not always type(parent).__name__ (e.g. LinearShuntCompensatorPhase's
    field is 'ShuntCompensator', the base class name, not
    'LinearShuntCompensator'). Same typing.get_type_hints resolution
    graph_write._cim_unit_type uses for CIMUnit, applied to find the
    parent-association field instead."""
    hints = typing.get_type_hints(phase_cls)
    for field_name, hint in hints.items():
        for arg in typing.get_args(hint) or (hint,):
            if isinstance(arg, type) and isinstance(parent, arg):
                return field_name
    raise ValueError(
        f"{phase_cls.__name__} has no field whose type matches "
        f"{type(parent).__name__}"
    )


def _resolve_node(network: GraphModel, col_name: str, node_mrid: str) -> CN.ConnectivityNode | CN.TopologicalNode:
    """The ConnectivityNode/TopologicalNode for a connectivity column, auto-vivified if new."""
    if col_name not in synthetic.CONNECTIVITY_COLUMNS:
        raise ValueError(
            f"{col_name!r} is not a connectivity column (one of {list(synthetic.CONNECTIVITY_COLUMNS)}, "
            f"per CIMTBL_DESIGN.md §3.5)"
        )
    node_cls = getattr(network.cim, synthetic.CONNECTIVITY_COLUMNS[col_name][0])
    node_obj = graph_write.resolve(network, node_cls, node_mrid)
    if node_obj is None:
        node_obj = node_cls(mRID=node_mrid)
        graph_write.add_to_graph(network, node_obj)
    return node_obj


def _add_end_terminal(network: GraphModel, end: CN.TransformerEnd, node_cols: dict[str, str]) -> CN.Terminal:
    """A TransformerEnd row's node (or bus) column: the Terminal belongs to the end's
    PowerTransformer (the ConductingEquipment), sequenceNumber = endNumber,
    and end.Terminal points at it. The PowerTransformer FK must already be set."""
    cim = network.cim
    if len(node_cols) != 1:
        raise ValueError(f"{end.mRID!r}: a TransformerEnd takes exactly one node/bus column, got {list(node_cols)}")
    transformer = getattr(end, 'PowerTransformer', None)
    if transformer is None or end.endNumber is None:
        raise ValueError(f"{end.mRID!r}: a node/bus column on a TransformerEnd needs its PowerTransformer and endNumber")

    [(col_name, node_mrid)] = node_cols.items()
    node_obj = _resolve_node(network, col_name, node_mrid)
    if transformer.EquipmentContainer is not None and node_obj.ConnectivityNodeContainer is None:
        graph_write.set_assc(node_obj, cim, 'ConnectivityNodeContainer', transformer.EquipmentContainer)

    terminal = cim.Terminal(mRID=f'{transformer.mRID}_T{end.endNumber}', sequenceNumber=end.endNumber)
    graph_write.set_assc(terminal, cim, 'ConductingEquipment', transformer)
    graph_write.set_assc(terminal, cim, synthetic.CONNECTIVITY_COLUMNS[col_name][0], node_obj)
    graph_write.set_assc(end, cim, 'Terminal', terminal)
    graph_write.add_to_graph(network, terminal)
    return terminal


def add_connectivity(network: GraphModel, equipment: CN.ConductingEquipment|CN.TransformerEnd, *, node_cols: dict[str, str],
                      equipment_container: CN.EquipmentContainer | None = None) -> list[CN.Terminal]:
    """node_cols is the {'node1': 'RG60', 'node2': '362'} slice of a <Class>Row
    (node/bus mRIDs). Each column fixes its node class and Terminal
    sequenceNumber (synthetic.CONNECTIVITY_COLUMNS), so header order doesn't
    matter; an unknown column or two columns on one sequenceNumber raise.
    Auto-vivifies any mRID not found via resolve(). Returns Terminals in
    sequenceNumber order.

    Sets equipment.EquipmentContainer from `equipment_container`, falling back
    to network.container when not given. Propagates the same container onto
    every node touched (ConnectivityNodeContainer), without overwriting one
    already set."""
    cim:CN = network.cim # type:ignore
    if isinstance(equipment, cim.TransformerEnd):
        return [_add_end_terminal(network, equipment, node_cols)]

    resolved_container = equipment_container if equipment_container is not None else network.container
    has_container = isinstance(resolved_container, cim.EquipmentContainer)

    terminals_by_sequence: dict[int, CN.Terminal] = {}
    for col_name, node_mrid in node_cols.items():
        node_obj = _resolve_node(network, col_name, node_mrid)
        node_field, sequence_number = synthetic.CONNECTIVITY_COLUMNS[col_name]
        if sequence_number in terminals_by_sequence:
            raise ValueError(f"{equipment.mRID!r}: {list(node_cols)} put two columns on terminal {sequence_number}")

        if has_container and node_obj.ConnectivityNodeContainer is None:
            graph_write.set_assc(node_obj, cim, 'ConnectivityNodeContainer', resolved_container)

        terminal = cim.Terminal(mRID=f'{equipment.mRID}_T{sequence_number}', sequenceNumber=sequence_number)
        graph_write.set_assc(terminal, cim, 'ConductingEquipment', equipment)
        graph_write.set_assc(terminal, cim, node_field, node_obj)
        graph_write.add_to_graph(network, terminal)
        terminals_by_sequence[sequence_number] = terminal

    if has_container:
        graph_write.set_assc(equipment, cim, 'EquipmentContainer', resolved_container)

    return [terminals_by_sequence[n] for n in sorted(terminals_by_sequence)]


_PHASE_TOKEN = re.compile(r's1|s2|[ABCN]')
_PHASE_ORDER = ['A', 'B', 'C', 's1', 's2', 'N']


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
    ordered = ''.join(sorted(tokens, key=_PHASE_ORDER.index))
    return ordered.replace('s1s2', 's12')


def _phase_cls(network: GraphModel, obj: object) -> type:
    """The per-phase child class for obj: <Class>Phase for the nearest class
    in its MRO that has one (LinearShuntCompensator -> LinearShuntCompensatorPhase,
    Breaker -> SwitchPhase)."""
    for cls in type(obj).__mro__:
        phase_cls = getattr(network.cim, f'{cls.__name__}Phase', None)
        if phase_cls is not None:
            return phase_cls
    raise ValueError(f"{type(obj).__name__} has no <Class>Phase child class for a phases column")


# Normative delta rule (SinglePhaseKind): the child connects its phase "to the next
# logical non-neutral phase".
_NEXT_PHASE = {'A': 'B', 'B': 'C', 'C': 'A', 's1': 's2'}


def add_phase_children(network: GraphModel, obj: CN.ConductingEquipment, terminals: list[CN.Terminal], *,
                       phases: str | None = None, phaseSide1: str | None = None,
                       phaseSide2: str | None = None) -> list[CN.PowerSystemResource]:
    """phases='ABCN' -> per-phase children (e.g. ACLineSegmentPhase) per §3.6,
    wired to obj. Every terminal gets the canonical PhaseCode of the set.

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
    cim = network.cim
    if not terminals:
        raise ValueError(f"{obj.mRID!r}: a phases column needs a node/bus column to put the phases on")
    if phases is not None and (phaseSide1 is not None or phaseSide2 is not None):
        raise ValueError(f"{obj.mRID!r}: give either phases or phaseSide1/phaseSide2, not both")
    if phases is not None:
        tokens = side2_tokens = parse_phases(phases)
        for terminal in terminals:
            graph_write.set_attr(terminal, cim, 'phases', cim.PhaseCode(phase_code(tokens)))
    else:
        if phaseSide1 is None or phaseSide2 is None:
            raise ValueError(f"{obj.mRID!r}: phaseSide1 and phaseSide2 must be given together")
        tokens, side2_tokens = parse_phases(phaseSide1), parse_phases(phaseSide2)
        if len(tokens) != len(side2_tokens) or len(terminals) != 2:
            raise ValueError(
                f"{obj.mRID!r}: phaseSide1 {phaseSide1!r} / phaseSide2 {phaseSide2!r} need the same "
                f"number of phases and two terminals (got {len(terminals)})"
            )
        graph_write.set_attr(terminals[0], cim, 'phases', cim.PhaseCode(phase_code(tokens)))
        graph_write.set_attr(terminals[1], cim, 'phases', cim.PhaseCode(phase_code(side2_tokens)))
    if isinstance(obj, cim.TransformerEnd):
        return []  # an end's phases live on its terminal only

    phase_cls = _phase_cls(network, obj)
    parent_field = _parent_assoc_field(phase_cls, obj)
    child_hints = typing.get_type_hints(phase_cls)
    has_sequence_number = 'sequenceNumber' in child_hints
    has_sides = 'phaseSide1' in child_hints
    if tokens != side2_tokens and not has_sides:
        raise ValueError(f"{obj.mRID!r}: {phase_cls.__name__} has one phase per child, so phaseSide1/phaseSide2 can't differ")

    if getattr(obj, 'phaseConnection', None) == cim.PhaseShuntConnectionKind.D:
        from_phases = [t for t in tokens if _NEXT_PHASE.get(t) in tokens]
        if not from_phases:
            raise ValueError(f"{obj.mRID!r}: a delta connection needs two non-neutral phases, got {phases!r}")
        new_children = []
        for t in from_phases:
            branch_code = phase_code([t, _NEXT_PHASE[t]])
            child = phase_cls(mRID=f'{obj.mRID}_{branch_code}', phase=cim.SinglePhaseKind(t))
            if 'phases' in child_hints:
                child.phases = cim.PhaseCode(branch_code)
            new_children.append(child)
    elif has_sides:  # SwitchPhase: phaseSide1/phaseSide2 instead of phase
        new_children = [
            phase_cls(mRID=f'{obj.mRID}_{t1}', phaseSide1=cim.SinglePhaseKind(t1), phaseSide2=cim.SinglePhaseKind(t2))
            for t1, t2 in zip(tokens, side2_tokens)
        ]
    else:
        new_children = [phase_cls(mRID=f'{obj.mRID}_{t}', phase=cim.SinglePhaseKind(t)) for t in tokens]

    children = []
    for i, child in enumerate(new_children, start=1):
        if has_sequence_number:
            child.sequenceNumber = i
        graph_write.set_assc(child, cim, parent_field, obj)
        graph_write.add_to_graph(network, child)
        children.append(child)

    return children
