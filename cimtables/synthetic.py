"""DSL-only columns that are not real CIM attributes under any profile
(design: CIMTBL_DESIGN.md §3.5-§3.7) - connectivity shorthand, the phases
and the phases compound string. Fixed regardless of which CIM version
Profile= selects; every other column is resolved by reflecting on the live
profile module (validate.py) instead of a curated allowlist. A reusable
equipment "template" is a real association, not a DSL column: cim18v17's
ACLineSegment.WireConfiguration for lines, TransformerTank.TankConfiguration
for transformers."""

# The fixed connectivity vocabulary: column -> (node class, Terminal
# sequenceNumber). Exact names only - a prefix rule would capture real CIM
# attributes (TopologicalNode.busName/busNumber, Bay.busBarConfiguration).
# The node class name is also the Terminal field it attaches through.
CONNECTIVITY_COLUMNS: dict[str, tuple[str, int]] = {
    'node': ('ConnectivityNode', 1), 'node1': ('ConnectivityNode', 1), 'node2': ('ConnectivityNode', 2),
    'bus': ('TopologicalNode', 1), 'bus1': ('TopologicalNode', 1), 'bus2': ('TopologicalNode', 2),
}

# phases (same on every terminal) or phaseSide1 + phaseSide2 (a SwitchPhase
# switch whose sides differ) -> connectivity.add_phase_children.
PHASE_COLUMNS = ('phases', 'phaseSide1', 'phaseSide2')

SYNTHETIC_COLUMNS: dict[str, type] = {**dict.fromkeys(CONNECTIVITY_COLUMNS, str), **dict.fromkeys(PHASE_COLUMNS, str)}
