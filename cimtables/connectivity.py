"""Phase 4: the connectivity backend - node/bus/terminal/phase (design: CIMTBL_DESIGN.md §5, §12.4).

The one place that knows §3.5's node*/bus* vocabulary and §3.6's phase-child
synthesis. Every Builder's add_connectivity (Phase 5) is a thin wrapper around
these two functions - no per-class node-vs-bus or phase logic anywhere else.
"""

import re
import typing

from cimgraph.models import GraphModel

from cimtables import graph_write


def _node_cls(network: GraphModel, col_name: str) -> type:
    """§3.5's one non-reflective naming rule: node* -> ConnectivityNode,
    bus* -> TopologicalNode. Fails fast on anything else - no silent
    fallback for a column that isn't part of the fixed vocabulary."""
    if col_name.startswith('node'):
        return network.cim.ConnectivityNode
    if col_name.startswith('bus'):
        return network.cim.TopologicalNode
    raise ValueError(
        f"{col_name!r} is not a connectivity column (must start with "
        f"'node' or 'bus', per CIMTBL_DESIGN.md §3.5)"
    )


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


def _resolve_node(network: GraphModel, col_name: str, node_mrid: str) -> object:
    """The ConnectivityNode/TopologicalNode with this mRID, auto-vivified if new."""
    node_cls = _node_cls(network, col_name)
    node_obj = graph_write.resolve(network, node_cls, node_mrid)
    if node_obj is None:
        node_obj = node_cls(mRID=node_mrid)
        graph_write.add_to_graph(network, node_obj)
    return node_obj


def _terminal_node_field(network: GraphModel, node_obj: object) -> str:
    """The Terminal field a node/bus object attaches through."""
    return 'ConnectivityNode' if isinstance(node_obj, network.cim.ConnectivityNode) else 'TopologicalNode'


def _add_end_terminal(network: GraphModel, end: object, node_cols: dict[str, str]) -> object:
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
    graph_write.set_assc(terminal, cim, _terminal_node_field(network, node_obj), node_obj)
    graph_write.set_assc(end, cim, 'Terminal', terminal)
    graph_write.add_to_graph(network, terminal)
    return terminal


def add_connectivity(network: GraphModel, obj: object, *, node_cols: dict[str, str],
                      container: object = None) -> list[object]:
    """node_cols is the already-disambiguated {'node1': 'busA', 'bus2': 'busB', ...}
    slice of a <Class>Row (node/bus mRIDs) - the caller picks which of the row's
    fields are connectivity columns. Auto-vivifies any mRID not found via resolve().
    Returns Terminals in column order (sequenceNumber = list index + 1).

    Sets obj.EquipmentContainer from `container`, falling back to
    network.container when not given - the one unambiguous container
    available today (.cimtbl has no per-row/per-table container syntax yet).
    Propagates the same container onto every node touched
    (ConnectivityNodeContainer), without overwriting one already set."""
    cim = network.cim
    if isinstance(obj, cim.TransformerEnd):
        return [_add_end_terminal(network, obj, node_cols)]

    resolved_container = container if container is not None else network.container
    has_container = isinstance(resolved_container, cim.EquipmentContainer)

    terminals = []
    for i, (col_name, node_mrid) in enumerate(node_cols.items(), start=1):
        node_obj = _resolve_node(network, col_name, node_mrid)

        if has_container and node_obj.ConnectivityNodeContainer is None:
            graph_write.set_assc(node_obj, cim, 'ConnectivityNodeContainer', resolved_container)

        terminal = cim.Terminal(mRID=f'{obj.mRID}_T{i}', sequenceNumber=i)
        graph_write.set_assc(terminal, cim, 'ConductingEquipment', obj)

        graph_write.set_assc(terminal, cim, _terminal_node_field(network, node_obj), node_obj)

        graph_write.add_to_graph(network, terminal)
        terminals.append(terminal)

    if has_container:
        graph_write.set_assc(obj, cim, 'EquipmentContainer', resolved_container)

    return terminals


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


def _delta_pairs(tokens: list[str]) -> list[list[str]]:
    """Line-to-line branches of a delta shunt: ['B', 'C'] -> [BC];
    ['A', 'B', 'C'] -> [AB, BC, CA]."""
    if len(tokens) == 2:
        return [tokens]
    return [[tokens[i], tokens[(i + 1) % len(tokens)]] for i in range(len(tokens))]


def add_phase_children(network: GraphModel, obj: object, terminals: list[object],
                        *, phases: str) -> list[object]:
    """phases='ABCN' -> per-phase children (e.g. ACLineSegmentPhase) per §3.6,
    wired to obj. Every terminal gets the canonical PhaseCode of the set.

    One child per token in written order, given SinglePhaseKind +
    sequenceNumber - except a delta shunt (phaseConnection=D) whose child
    class carries the `phases` PhaseCode extension (EnergyConsumerPhase,
    ShuntCompensatorPhase, ...): there each child is one line-to-line branch,
    so a single-phase delta load on BC is one child with phases=BC."""
    cim = network.cim
    if not terminals:
        raise ValueError(f"{obj.mRID!r}: a phases column needs a node/bus column to put the phases on")
    tokens = parse_phases(phases)
    for terminal in terminals:
        graph_write.set_attr(terminal, cim, 'phases', cim.PhaseCode(phase_code(tokens)))
    if isinstance(obj, cim.TransformerEnd):
        return []  # an end's phases live on its terminal only

    phase_cls = _phase_cls(network, obj)
    parent_field = _parent_assoc_field(phase_cls, obj)
    child_hints = typing.get_type_hints(phase_cls)
    has_sequence_number = 'sequenceNumber' in child_hints
    is_delta = getattr(obj, 'phaseConnection', None) == cim.PhaseShuntConnectionKind.D
    if is_delta and 'phases' in child_hints:
        branches = [phase_code(pair) for pair in _delta_pairs(tokens)]
        new_children = [phase_cls(mRID=f'{obj.mRID}_{code}', phases=cim.PhaseCode(code)) for code in branches]
    else:
        # SwitchPhase has phaseSide1/phaseSide2 instead of phase: same phase both sides
        phase_attrs = ['phase'] if 'phase' in child_hints else ['phaseSide1', 'phaseSide2']
        new_children = [
            phase_cls(mRID=f'{obj.mRID}_{t}', **{attr: cim.SinglePhaseKind(t) for attr in phase_attrs})
            for t in tokens
        ]

    children = []
    for i, child in enumerate(new_children, start=1):
        if has_sequence_number:
            child.sequenceNumber = i
        graph_write.set_assc(child, cim, parent_field, obj)
        graph_write.add_to_graph(network, child)
        children.append(child)

    return children
