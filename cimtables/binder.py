"""The reflective binder: one validated <Class>Row -> one live cim-graph object.

bind_row is the one generic entry point from a validated <Class>Row to a live
cim-graph object, for every class in the selected CIM profile - no per-class
from_table/add_<part> dispatch table to maintain. Every row field is
classified purely from the profile (validate.py), by name, into exactly one
of these buckets:

- a connectivity column (synthetic.CONNECTIVITY_COLUMNS: node/node1/node2
  -> node_cols, bus/bus1/bus2 -> bus_cols; both may be given) -> passed to
  connectivity.add_connectivity
- the synthetic `phases` / `phaseSide1` + `phaseSide2` shorthand, with
  its optional `sequenceNumbers` (only where the class has no real
  attribute of that name) ->
  connectivity.add_phase_children: Terminal.phases
  plus <Class>Phase children
- EquipmentContainer -> resolved by mRID via graph_write.resolve (its range is
  always an abstract base: Feeder, Substation, ... - see validate.py's
  reference_target_class) and used as the container: passed to
  add_connectivity when the row has connectivity, else set directly
- any other reference slot (FK) -> resolved by mRID via graph_write.resolve()
  against the schema's own range class and written with set_assc;
  an unresolved mRID raises
- everything else -> a plain scalar/Qty, written with set_attr

pu/percent impedances/admittances (r, x, bch, ...) are written last,
converted to ohms/siemens against impedance.z_base: the rating pair of the
row's own object if its class is rated (PowerTransformerEnd), else of its
From* reference target (TransformerMeshImpedance's FromWinding/
FromTransformerEnd), else the system base (the row's BaseVoltage and
the bound BasePower, which must be declared before the row) for a class with
no rating pair (ACLineSegment, EnergySource). A `(pu system)` column always
uses the system base.
See .development/PERCENT_IMPEDANCE_BASE.md.

set_attr/set_assc are called with network.cim as `part` throughout - the
documented no-op part-check (graph_write.py's module docstring) appropriate
at a genuinely reflective call site with no class-specific reference module
to check against.
"""

import dataclasses
import logging
import math

from cimgraph.models import GraphModel
from cimprofiles.units.units import CIMUnit
from cimtables import connectivity, graph_write, synthetic, validate, wkt as wkt_parser
from cimtables.units import Qty

_SKIP_FIELDS = {*validate.KEY_FIELDS, 'source_file', 'source_line'}
_CONTAINER_FIELD = 'EquipmentContainer'
_log = logging.getLogger(__name__)


class CimtblBindError(ValueError, TypeError):
    """A row read from a file that failed to bind, as `<file>:<line>: <message>`;
    the original ValueError/TypeError is its __cause__. It subclasses both, so
    an `except ValueError` / `except TypeError` still catches what it did."""


def _new_object(cim_cls: type, row: object) -> object:
    """The row's object, its UUID seeded from the row key: the mRID alone (so a
    rename keeps the UUID), or for an Identity-only class (no mRID field, e.g.
    PhaseImpedanceData) its optional identifier column, seeded by the same mRID
    route without storing it. Neither given: a random UUID."""
    if row.mRID is not None:
        return cim_cls(mRID=row.mRID)
    obj = cim_cls()
    if row.identifier is not None:
        obj.uuid(mRID=row.identifier)
    return obj


def bind_row(network: GraphModel, row: object, *, obj: object = None, container: object = None,
             update: bool = False) -> object:
    """Validated <Class>Row -> live cim-graph object, added to network.

    update=True is an `Update` row (CIM's additive definition, a Description
    class in a profile; Object/Table rows are the formative one): its mRID
    names an object already bound, in this class or a subclass, and its
    columns add attributes and references to it, the inverse sides included.
    It only adds: a single-valued field that is already set raises, as do
    connectivity/phase columns (they would make a second set of Terminals).
    No EquipmentContainer is defaulted onto it.

    A formative row whose mRID (or identifier) is already bound only
    restates it (see _restate): the same facts again are a no-op returning
    the bound object, anything else raises.

    `obj` lets a caller (ObjectBuilder.from_table) supply an already-created
    object instead of this function constructing one with cim_cls(mRID=...) -
    so a builder's own create() (which may do more than a bare constructor
    call, e.g. UUID seeding) stays the single place an object comes into
    being. `container` is an explicit EquipmentContainer override (a
    builder's self.container); a row's own EquipmentContainer column still
    takes precedence over it, matching add_connectivity's existing fallback
    order.

    A ValueError/TypeError binding a row read from a file (row.source_file
    set) is re-raised as CimtblBindError, prefixed with the row's file:line."""
    try:
        return _bind_row(network, row, obj, container, update)
    except (ValueError, TypeError) as exc:
        if not row.source_file:  # a hand-built row has no location to add
            raise
        raise CimtblBindError(f'{row.source_file}:{row.source_line}: {exc}') from exc


def _bind_row(network: GraphModel, row: object, obj: object, container: object, update: bool) -> object:
    """bind_row without the error location."""
    row_cls_name = type(row).__name__
    ref_slots = validate.reference_slots(row_cls_name)
    cim_cls = getattr(network.cim, row_cls_name.removesuffix('Row'))

    if update:
        obj = graph_write.resolve(network, cim_cls, row.mRID) if row.mRID is not None else None
        if obj is None:
            raise ValueError(f"Update {cim_cls.__name__} {row.mRID!r}: no such object (an Update adds to an object defined earlier)")
    elif obj is None:
        existing = _bound_object(network, row, cim_cls)
        if existing is not None:
            return _restate(network, row, existing)
        obj = _new_object(cim_cls, row)

    node_cols: dict[str, str] = {}
    bus_cols: dict[str, str] = {}
    phase_cols: dict[str, str] = {}
    deferred_fks: list[tuple[str, str]] = []
    relative_qtys: list[tuple[str, Qty]] = []

    for f in dataclasses.fields(row):
        if f.name in _SKIP_FIELDS:
            continue
        value = getattr(row, f.name)
        if value is None:
            continue
        if update and f.name not in obj.__dataclass_fields__ and f.name != synthetic.WKT_COLUMN:
            raise ValueError(f"Update {row.mRID!r}: {f.name!r} is connectivity shorthand; set it where the object is defined")
        if update and f.name != synthetic.WKT_COLUMN and getattr(obj, f.name) is not None and not isinstance(getattr(obj, f.name), list):
            raise ValueError(f"Update {row.mRID!r}: {f.name} is already set ({getattr(obj, f.name)!r}); an Update only adds")

        if f.name == synthetic.WKT_COLUMN:
            pass  # deferred: handled after the loop, needs obj to be fully built first
        elif f.name in synthetic.CONNECTIVITY_COLUMNS and f.name not in obj.__dataclass_fields__:
            if synthetic.CONNECTIVITY_COLUMNS[f.name][0] == 'TopologicalNode':
                bus_cols[f.name] = value
            else:
                node_cols[f.name] = value
        elif f.name in synthetic.PHASE_COLUMNS and f.name not in obj.__dataclass_fields__:
            phase_cols[f.name] = value
        elif f.name == _CONTAINER_FIELD:
            container = graph_write.resolve(network, network.cim.EquipmentContainer, value)
            if container is None:
                raise ValueError(
                    f"{row.mRID!r}: {_CONTAINER_FIELD} {value!r} not found in graph "
                    f"(build containers before equipment that references them)"
                )
        elif f.name in ref_slots:
            deferred_fks.append((f.name, value))
        elif isinstance(value, Qty) and value.is_relative():
            relative_qtys.append((f.name, value))
        else:
            graph_write.set_attr(obj, network.cim, f.name, value)

    # FKs first: a TransformerEnd's terminal goes on its PowerTransformer / AutoTransformer
    for attr, mRID in deferred_fks:
        target_cls_name = validate.reference_target_class(row_cls_name, attr)
        # The slot's range is often an abstract base (TransformerEnd, PerLengthImpedance)
        target = graph_write.resolve(network, getattr(network.cim, target_cls_name), mRID)
        if target is None:
            raise ValueError(
                f"{row.mRID!r}: {attr} {mRID!r} ({target_cls_name}) not found in graph "
                f"(declare it before rows that reference it)"
            )
        graph_write.set_assc(obj, network.cim, attr, target)

    has_container_slot = _CONTAINER_FIELD in ref_slots
    terminals = []
    if node_cols or bus_cols:
        terminals = connectivity.add_connectivity(
            network, obj, node_cols=node_cols, bus_cols=bus_cols, equipment_container=container
        )
    elif has_container_slot and update:
        if container is not None:
            graph_write.set_assc(obj, network.cim, _CONTAINER_FIELD, container)
    elif has_container_slot:
        resolved_container = container if container is not None else network.container
        if isinstance(resolved_container, network.cim.EquipmentContainer):
            graph_write.set_assc(obj, network.cim, _CONTAINER_FIELD, resolved_container)

    if phase_cols:
        connectivity.add_phase_children(network, obj, terminals, **phase_cols)

    wkt_value = getattr(row, synthetic.WKT_COLUMN, None)
    if wkt_value is not None:
        _bind_wkt(network, obj, row.mRID, wkt_value)

    # last: their base reads the ratings, BaseVoltage and From* set above. The
    # system base is a bound BasePower's only, so a system-base value never
    # depends on CIMG_SYSTEM_MVA_BASE or converts before its BasePower
    system_base = network.base_power if network.graph.get(getattr(network.cim, 'BasePower', None)) else None
    for attr, qty in relative_qtys:
        graph_write.set_attr(obj, network.cim, attr, qty, system_base=system_base)

    if not update:
        graph_write.add_to_graph(network, obj)
    return obj


def _bound_object(network: GraphModel, row: object, cim_cls: type) -> object | None:
    """The object a formative row's key is already bound to, or None: found by
    mRID, or for an Identity-only row by the UUID its identifier seeds (in any
    class, so a class mismatch is reported rather than missed)."""
    if row.mRID is not None:
        return network.find_by_mRID(row.mRID)
    if row.identifier is None:
        return None
    identifier = _new_object(cim_cls, row).identifier
    for objects in network.graph.values():
        if identifier in objects:
            return objects[identifier]
    return None


def _restate(network: GraphModel, row: object, existing: object) -> object:
    """A formative row (Object/Table) whose key (mRID, or an Identity-only
    row's identifier) is already bound: the same facts stated again - a
    BaseVoltage every feeder file declares, a node the equipment that first
    touched it created, a file imported from two branches - leave the model
    as it is and return the bound object.

    Every cell the row sets must equal what is bound (CIMUnits within 1e-9
    relative, a list-valued reference must contain the target); blank cells
    say nothing. A different class, a different value, or a value the object
    doesn't have yet raises: a repeat never changes the model, so load order
    can't, and adding a value is Update's job. The phases and wkt shorthand
    can't be restated."""
    cim = network.cim
    cim_cls = getattr(cim, type(row).__name__.removesuffix('Row'))
    key_name, key = ('mRID', row.mRID) if row.mRID is not None else ('identifier', row.identifier)
    if type(existing) is not cim_cls:
        raise ValueError(
            f"{key_name} {key!r} of {cim_cls.__name__} is already used by {type(existing).__name__} "
            f"(an {key_name} must be unique within the model)"
        )
    ref_slots = validate.reference_slots(type(row).__name__)
    system_base = network.base_power if network.graph.get(getattr(cim, 'BasePower', None)) else None
    row_values = _new_object(cim_cls, row)  # never added to the graph: the row's plain cells, converted by set_attr

    for f in dataclasses.fields(row):
        value = getattr(row, f.name)
        if f.name in _SKIP_FIELDS or value is None:
            continue
        is_shorthand = f.name not in existing.__dataclass_fields__
        if is_shorthand and f.name in synthetic.CONNECTIVITY_COLUMNS:
            node_field, sequence_number = synthetic.CONNECTIVITY_COLUMNS[f.name]
            if isinstance(existing, cim.TransformerEnd):
                terminal = existing.Terminal
            else:
                terminal = next((t for t in existing.Terminals if t.sequenceNumber == sequence_number), None)
            node = getattr(terminal, node_field) if terminal is not None else None
            bound = None if node is None else node.mRID
            same = bound == value
        elif is_shorthand:
            raise ValueError(
                f"{cim_cls.__name__} {key!r} is already defined, and its {f.name} shorthand "
                f"can't be restated: write it only where the object is defined"
            )
        elif f.name in ref_slots:
            target = getattr(existing, f.name)
            if isinstance(target, list):
                bound = [t.mRID for t in target]
                same = value in bound
            else:
                bound = None if target is None else target.mRID
                same = bound == value
        elif isinstance(value, Qty) and value.is_relative():
            bound = graph_write.get_qty(existing, f.name, value.unit, base=value.base, system_base=system_base)
            same = bound is not None and math.isclose(bound, value.value, rel_tol=1e-9)
        else:
            graph_write.set_attr(row_values, cim, f.name, value)
            want, bound = getattr(row_values, f.name), getattr(existing, f.name)
            if isinstance(want, CIMUnit) and isinstance(bound, CIMUnit):
                same = math.isclose(float(want), float(bound), rel_tol=1e-9)
            else:
                same = want == bound
        if not same:
            raise ValueError(
                f"{cim_cls.__name__} {key!r} is already defined with {f.name} "
                f"{'unset' if bound is None else repr(bound)}, not {value!r} "
                f"(a repeated definition must match it; an Update adds a value)"
            )

    _log.debug('%s:%s: %s %r restated', row.source_file, row.source_line, cim_cls.__name__, key)
    return existing


def _bind_wkt(network: GraphModel, obj: object, mrid: str, wkt_text: str) -> None:
    """Create a Location and one PositionPoint per coordinate, wired to obj.

    Location mRID: ``<mrid>_loc``. PositionPoint identifiers: ``<mrid>_loc_<seq>``.
    The object must be a PowerSystemResource (have a Location slot); anything
    else raises ValueError so the caller's CimtblBindError wraps it with its
    file:line context."""
    cim = network.cim
    if not hasattr(cim, 'Location') or not hasattr(cim, 'PositionPoint'):
        raise ValueError("wkt column requires a CIM profile that includes Location and PositionPoint")
    loc_field = getattr(type(obj), '__dataclass_fields__', {}).get('Location')
    if loc_field is None:
        raise ValueError(
            f"wkt column on {type(obj).__name__} {mrid!r}: {type(obj).__name__} has no Location slot "
            f"(only PowerSystemResource subclasses support wkt)"
        )

    try:
        geom = wkt_parser.parse(wkt_text)
    except ValueError as exc:
        raise ValueError(f"wkt column on {type(obj).__name__} {mrid!r}: {exc}") from exc

    loc = cim.Location(mRID=f'{mrid}_loc')
    graph_write.add_to_graph(network, loc)
    graph_write.set_assc(obj, cim, 'Location', loc)
    # Wire up a CoordinateSystem if one is already in the graph (declared before wkt rows).
    crs_cls = getattr(cim, 'CoordinateSystem', None)
    if crs_cls is not None:
        existing_crs = next(iter(network.graph.get(crs_cls, {}).values()), None)
        if existing_crs is not None and hasattr(loc, 'CoordinateSystem'):
            graph_write.set_assc(loc, cim, 'CoordinateSystem', existing_crs)

    for seq, coord in enumerate(geom.coordinates):
        pp = cim.PositionPoint()
        pp.uuid(mRID=f'{mrid}_loc_{seq}')
        pp.sequenceNumber = seq
        pp.xPosition = coord.x
        pp.yPosition = coord.y
        if coord.z is not None:
            pp.zPosition = coord.z
        graph_write.set_assc(pp, cim, 'Location', loc)
        graph_write.add_to_graph(network, pp)

