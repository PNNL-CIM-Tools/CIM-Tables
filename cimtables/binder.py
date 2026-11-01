"""Phase 3/5: reflective column -> attr/assoc/connectivity binder (design, in
CIM-Builder's cimbuilder/development/: CIMTBL_DESIGN.md §2.1, §4.2).

bind_row is the one generic entry point from a validated <Class>Row to a live
cim-graph object, for every class in the selected CIM profile - no per-class
from_table/add_<part> dispatch table to maintain. Every row field is
classified purely from the profile (validate.py), by name, into exactly one
of these buckets:

- a connectivity column (node*/bus*, per connectivity.py's naming rule) ->
  collected and passed to connectivity.add_connectivity in column order
- the synthetic `phases` shorthand (only where the class has no real
  `phases` attribute) -> connectivity.add_phase_children: Terminal.phases
  plus <Class>Phase children (§3.6)
- EquipmentContainer -> resolved by mRID via graph_write.resolve (its range is
  always an abstract base: Feeder, Substation, ... - see validate.py's
  reference_target_class) and passed to add_connectivity as the container
  override, not set directly
- any other reference slot (FK) -> resolved by mRID via graph_write.resolve()
  against the schema's own range class and written with set_assc;
  an unresolved mRID raises
- everything else -> a plain scalar/Qty, written with set_attr (unchanged
  from Phase 3)

pu/percent impedances/admittances (r, x, bch, ...) are written last,
converted to ohms/siemens against impedance.z_base: the rating pair of the
row's own object if its class is rated (PowerTransformerEnd), else of its
From* reference target (TransformerMeshImpedance's FromWinding/
FromTransformerEnd), else the system base (the row's BaseVoltage and
network.base_power) for a class with no rating pair (ACLineSegment,
EnergySource). See .development/PERCENT_IMPEDANCE_BASE.md.

set_attr/set_assc are called with network.cim as `part` throughout - the
documented no-op part-check (graph_write.py's module docstring) appropriate
at a genuinely reflective call site with no class-specific reference module
to check against. See CIM-Builder's development/PROFILE_RESOLUTION.md.
"""

import dataclasses

from cimgraph.models import GraphModel
from cimtables import connectivity, graph_write, impedance, synthetic, validate
from cimtables.units import Qty

_SKIP_FIELDS = {'mRID', 'identifier', 'source_file', 'source_line'}
_CONTAINER_FIELD = 'EquipmentContainer'
_FROM_PREFIX = 'From'
_PHASES_FIELD = 'phases'


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


def bind_row(network: GraphModel, row: object, *, obj: object = None, container: object = None) -> object:
    """Validated <Class>Row -> live cim-graph object, added to network.

    `obj` lets a caller (ObjectBuilder.from_table) supply an already-created
    object instead of this function constructing one with cim_cls(mRID=...) -
    so a builder's own create() (which may do more than a bare constructor
    call, e.g. UUID seeding) stays the single place an object comes into
    being. `container` is an explicit EquipmentContainer override (a
    builder's self.container); a row's own EquipmentContainer column still
    takes precedence over it, matching add_connectivity's existing fallback
    order."""
    row_cls_name = type(row).__name__
    ref_slots = validate.reference_slots(row_cls_name)

    if obj is None:
        obj = _new_object(getattr(network.cim, row_cls_name.removesuffix('Row')), row)

    node_cols: dict[str, str] = {}
    phases: str | None = None
    deferred_fks: list[tuple[str, str]] = []
    relative_qtys: list[tuple[str, Qty]] = []

    for f in dataclasses.fields(row):
        if f.name in _SKIP_FIELDS:
            continue
        value = getattr(row, f.name)
        if value is None:
            continue

        if synthetic.is_connectivity_column(f.name):
            node_cols[f.name] = value
        elif f.name == _PHASES_FIELD and _PHASES_FIELD not in obj.__dataclass_fields__:
            phases = value
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

    # FKs first: a TransformerEnd's terminal goes on its PowerTransformer
    from_targets = []
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
        if attr.startswith(_FROM_PREFIX):
            from_targets.append(target)

    has_container_slot = _CONTAINER_FIELD in ref_slots
    terminals = []
    if node_cols:
        terminals = connectivity.add_connectivity(network, obj, node_cols=node_cols, container=container)
    elif has_container_slot:
        resolved_container = container if container is not None else network.container
        if isinstance(resolved_container, network.cim.EquipmentContainer):
            graph_write.set_assc(obj, network.cim, _CONTAINER_FIELD, resolved_container)

    if phases is not None:
        connectivity.add_phase_children(network, obj, terminals, phases=phases)

    if relative_qtys:
        z_base = impedance.z_base(obj, from_targets, network.base_power)
        for attr, qty in relative_qtys:
            graph_write.set_attr(obj, network.cim, attr, qty, z_base=z_base)

    graph_write.add_to_graph(network, obj)
    return obj

