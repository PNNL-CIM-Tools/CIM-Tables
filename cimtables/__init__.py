"""cimtables: the .cimtbl DSL - parser, profile-reflective validator, and
graph-write/binder core for cim-graph. Split out of CIM-Builder (see that
repo's cimbuilder/development/CIMTBL_DESIGN.md §8.1 for why); has never
depended on CIM-Builder's builder layer.

`load()` is the package's own minimal, ObjectBuilder-free entry point: parse
a .cimtbl file, validate every record against the network's active CIM
profile, and bind each one onto the network via bind_row - the same function
a hand-written builder's from_table() delegates to, with no builder
subclassing required at all. A consumer who only wants ".cimtbl in, live
cim-graph objects out" never needs anything else from this package."""

from cimtables import connectivity, graph_write, synthetic, validate
from cimtables.binder import bind_row
from cimtables.parser import parse_file, parse_text
from cimtables.records import RawRecord
from cimtables.units import Qty
from cimtables.validate import CimtblValidationError

__all__ = [
    'bind_row', 'connectivity', 'graph_write', 'synthetic', 'validate',
    'parse_file', 'parse_text', 'RawRecord', 'Qty',
    'CimtblValidationError', 'load',
]


def load(path, network):
    """Parse, validate, and bind every record in a .cimtbl file onto an
    already-constructed network; returns the live cim-graph objects in file
    order. No ObjectBuilder subclass involved - every row goes through the
    same bind_row() a builder's from_table delegates to."""
    records, _profile = parse_file(path)
    return [bind_row(network, validate.validate(r)) for r in records]
