"""DSL-only columns that are not real CIM attributes under any profile
(design: CIMTBL_DESIGN.md §3.5-§3.7) - connectivity shorthand, the phases
and the phases compound string. Fixed regardless of which CIM version
Profile= selects; every other column is resolved by reflecting on the live
profile module (validate.py) instead of a curated allowlist. A reusable
equipment "template" is a real association, not a DSL column: cim18v17's
ACLineSegment.WireConfiguration for lines, TransformerTank.TankConfiguration
for transformers."""

SYNTHETIC_COLUMNS: dict[str, type] = {
    'node': str, 'node1': str, 'node2': str,
    'bus': str, 'bus1': str, 'bus2': str,
    'phases': str,
}


def is_connectivity_column(name: str) -> bool:
    """§3.5's naming rule: node* -> ConnectivityNode, bus* -> TopologicalNode."""
    return name.startswith('node') or name.startswith('bus')
