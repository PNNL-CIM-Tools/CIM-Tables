"""Phase 3/5: set_attr / set_assc / add_to_graph / resolve
(design: CIMTBL_DESIGN.md §12.3 and GRAPH_WRITE_CONTRACT.md, both in
CIM-Builder's cimbuilder/development/ - this package's design history predates
its split from CIM-Builder into its own repo; see that repo's
CIMTBL_DESIGN.md §8.1 for the split rationale).

The graph-write core: the only place that actually mutates a live cimgraph
object or writes it into a GraphModel. Everything else (the binder, the
connectivity backend, builder add_<part> methods) calls through here rather
than touching `setattr`/`network.add_to_graph` directly, so unit-binding,
profile-part checking, reverse-association writes, and mRID-indexing can
never be forgotten at a call site.

set_attr/set_assc's `part` argument is a reference profile-part module (e.g.
cimgraph.data_profile.cim18gmdm.connectivity) checked purely by name against
type(obj).__name__ - never by isinstance/inheritance. `obj` need not (and
usually won't) be an instance of any class in `part`; `part` is a shape
reference, not `obj`'s actual runtime profile. See GRAPH_WRITE_CONTRACT.md §2
and PROFILE_RESOLUTION.md (CIM-Builder's development docs) for why this is
the right decoupling. Passing the merged `network.cim` module itself as
`part` is a documented no-op check (every field passes, since a merged
profile has no restricted attribute set) - the right choice at a call site
with no genuine part-scoped reference available yet (Phase 3/4 code).
"""

import dataclasses
import typing
from types import ModuleType

from cimprofiles.units.units import CIMUnit
from cimgraph.models import GraphModel

from cimtables.impedance import relative_to_ohm, relative_to_siemens
from cimtables.units import Qty


def _cim_unit_type(obj: object, attr: str) -> type[CIMUnit] | None:
    """The CIMUnit subclass (Voltage, ActivePower, ...) a quantity attr's
    `float | <CIMUnitType>` union carries, or None if the attr isn't
    quantity-typed. Resolved off obj's own class, not `network.cim` - it's
    exactly the module type(obj) was defined in, and set_attr takes no
    network param."""
    hints = typing.get_type_hints(type(obj))
    union_args = typing.get_args(hints.get(attr))
    for arg in union_args:
        if isinstance(arg, type) and issubclass(arg, CIMUnit):
            return arg
    return None


def _part_field(part: ModuleType, obj: object, attr: str) -> dataclasses.Field:
    """The dataclasses.Field for `attr` on part's class of the same name as
    type(obj) - the one runtime check standing in for Pylance's static
    narrowing (GRAPH_WRITE_CONTRACT.md §2). Raises ValueError, naming the
    part module and class, if `attr` isn't a real field there at all."""
    cls_name = type(obj).__name__
    part_cls = getattr(part, cls_name, None)
    if part_cls is None:
        raise ValueError(
            f"{part.__name__} has no class named {cls_name!r} "
            f"(wrong reference part module for this object's type)"
        )
    try:
        return part_cls.__dataclass_fields__[attr]
    except KeyError:
        raise ValueError(
            f"{attr!r} is not a field of {part.__name__}.{cls_name} - "
            f"wrong profile part for this attribute"
        ) from None


def set_attr(obj: object, part: ModuleType, attr: str, value: object, *,
             z_base: float | None = None, y_base: float | None = None) -> None:
    """Set a plain scalar or Qty onto a live cim-graph object's attribute.

    A pu/percent Qty on an ohm-typed attribute (r, x, r0, ...) is converted
    to ohms against `z_base` (ohms, resolved by the caller); raises if none
    was given. A pu/percent Qty on a siemens-typed attribute (b, g, bch,
    gch, ...) is converted to siemens against `y_base` (siemens) the same
    way; raises if none was given. Relative Qtys on non-impedance/
    non-admittance attributes (PerCent) pass through unchanged.

    Raises ValueError if `attr` is not a real field of part's same-named
    class, or if it IS a field but an association (has metadata['inverse'])
    - use set_assc for that."""
    field = _part_field(part, obj, attr)
    if field.metadata.get('inverse') is not None:
        raise ValueError(
            f"{attr!r} is an association on {part.__name__}.{type(obj).__name__} "
            f"(inverse={field.metadata['inverse']!r}) - use set_assc, not set_attr"
        )

    if value is None:
        setattr(obj, attr, None)
        return

    if isinstance(value, Qty):
        cim_unit_type = _cim_unit_type(obj, attr)
        if cim_unit_type is None:
            raise ValueError(
                f"{type(obj).__name__}.{attr} is not a CIMUnit-typed attribute, "
                f"but got a Qty ({value!r})"
            )
        unit_name = cim_unit_type(0).unit.name
        if value.is_relative() and unit_name in ('ohm', 'S'):
            base, base_unit, convert = (
                (z_base, 'z_base', relative_to_ohm) if unit_name == 'ohm'
                else (y_base, 'y_base', relative_to_siemens)
            )
            if base is None:
                raise ValueError(
                    f"{type(obj).__name__}.{attr}: {value.unit!r} value needs a {base_unit}, "
                    f"but none could be resolved for {getattr(obj, 'mRID', obj)!r}"
                )
            setattr(obj, attr, cim_unit_type(convert(value, base), unit_name))
            return
        if value.unit:
            setattr(obj, attr, cim_unit_type(value.value, value.unit))
        else:
            setattr(obj, attr, cim_unit_type(value.value))
        return

    setattr(obj, attr, value)


def _set_side(obj: object, attr: str, field: dataclasses.Field, target: object) -> None:
    """One side of an association write - list-append (dedup by identity,
    matching cimgraph's own create_edge) or singular overwrite, per field.type."""
    if 'list' in field.type:
        obj_list = getattr(obj, attr)
        if not any(x is target for x in obj_list):
            obj_list.append(target)
    else:
        setattr(obj, attr, target)


def set_assc(obj: object, part: ModuleType, assc: str, target: object) -> object:
    """Set obj.<assc> = target AND write the reverse side onto target,
    resolved from part's field metadata['inverse'] - the one call site for
    establishing any association, so the graph is never one-sided. Returns
    `target`, so a caller (or set_assc_from_mrid) can chain off it.

    Raises ValueError if `assc` is not a real field of part's same-named
    class, or if it IS a field but a plain scalar (metadata['inverse'] is
    None) - use set_attr for that."""
    field = _part_field(part, obj, assc)
    inverse = field.metadata.get('inverse')
    if inverse is None:
        raise ValueError(
            f"{assc!r} is a plain attribute on {part.__name__}.{type(obj).__name__} "
            f"(not an association) - use set_attr, not set_assc"
        )

    reverse_cls_name, reverse_attr = inverse.split('.')
    reverse_field = getattr(part, reverse_cls_name).__dataclass_fields__[reverse_attr]

    _set_side(obj, assc, field, target)
    if target is not None:
        _set_side(target, reverse_attr, reverse_field, obj)
    return target


def add_to_graph(network: GraphModel, obj: object) -> None:
    """The single call site (§12.3): add obj to the live graph, which also
    indexes its mRID (cim-graph raises on an mRID already used by another
    object - mRIDs are unique within the model, not per class)."""
    network.add_to_graph(obj)


def resolve(network: GraphModel, cim_cls: type, mRID: str) -> object | None:
    """O(1) mRID -> object lookup. None means not found - callers decide
    whether that's an auto-vivify or an error. cim_cls may be an abstract
    base (EquipmentContainer, PerLengthImpedance, ...): any subclass matches.
    Raises ValueError if the mRID belongs to an object of another class."""
    obj = network.find_by_mRID(mRID)
    if obj is not None and not isinstance(obj, cim_cls):
        raise ValueError(f"mRID {mRID!r} is a {type(obj).__name__}, not a {cim_cls.__name__}")
    return obj


def set_assc_from_mrid(network: GraphModel, obj: object, part: ModuleType, assc: str,
                        cim_cls: type, mRID: str) -> object:
    """resolve() + set_assc() in one call, for the common required-FK shape
    every add_<part> method otherwise hand-writes: resolve the target by
    mRID, raise a consistent ValueError if it's missing, else wire it up.
    Returns the resolved target (matching set_assc's own return).

    Leave a multi-target check (e.g. two ends resolved together with one
    combined error message) as free-standing resolve()/set_assc() calls -
    this helper is for the single-target case only."""
    target = resolve(network, cim_cls, mRID)
    if target is None:
        raise ValueError(
            f"{getattr(obj, 'mRID', obj)!r}: {assc} {mRID!r} not found in graph "
            f"(build it before the object that references it)"
        )
    return set_assc(obj, part, assc, target)
