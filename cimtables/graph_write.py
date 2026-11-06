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
import math
import typing
from types import ModuleType
from typing import Type, TypeVar

from cimgraph.models import GraphModel
from cimprofiles.units.units import CIMUnit

from cimtables import impedance
from cimtables.units import Qty

T = TypeVar("T")


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


def set_attr(
    source_object: object,
    profile_part: ModuleType,
    attribute_name: str,
    attribute_value: object,
    *,
    system_base: CIMUnit | Qty | float | None = None,
) -> None:
    """Set a plain scalar, CIMUnit or Qty onto a live cim-graph object's attribute.

    The value must match the attribute's declared type, as cim-graph's own
    create_value enforces when it reads a model:
    - a bool field takes only a bool (the str 'false' is truthy, so it is
      rejected, not parsed - parsing text is validate.py's job), and a bool is
      never accepted as a number
    - a number on a CIMUnit field is wrapped in that CIMUnit, read as SI base
      (`Voltage(4160)` is 4160 V); on a plain float field it becomes a float
    - a CIMUnit is converted into the field's own CIMUnit class (the classes
      are per profile module); pint raises on a dimension mismatch
    - a Qty is converted from its `(unit)` into the field's CIMUnit. A
      pu/percent Qty on an ohm-typed attribute (r, x, r0, ...) is multiplied
      by impedance.z_base(source_object, system_base) (ohms); on a
      siemens-typed one (b, g, bch, ...) it is divided by it (admittance base
      = 1/z_base). The base comes from the object (its rating pair, a From*
      target's, else its BaseVoltage and `system_base`, the system base power,
      e.g. network.base_power), or always the system base for a Qty with
      base='system'. So the object's ratings, BaseVoltage and From*
      references must be set first. Relative Qtys on other attributes
      (PerCent) are converted like any other unit.
    - anything else must be an instance of a declared type (str, int, UUID,
      an Enum, ...)
    Raises TypeError on a mismatch.

    Raises ValueError if `attr` is not a real field of part's same-named
    class, or if it IS a field but an association (has metadata['inverse'])
    - use set_assc for that."""
    field = _part_field(profile_part, source_object, attribute_name)
    if field.metadata.get("inverse") is not None:
        raise ValueError(
            f"{attribute_name!r} is an association on {profile_part.__name__}.{type(source_object).__name__} "
            f"(inverse={field.metadata['inverse']!r}) - use set_assc, not set_attr"
        )

    if attribute_value is None:
        return

    hint = typing.get_type_hints(type(source_object))[attribute_name]
    declared = [
        t
        for t in typing.get_args(hint) or (hint,)
        if isinstance(t, type) and t is not type(None)
    ]
    cim_unit_type = next((t for t in declared if issubclass(t, CIMUnit)), None)
    where = f"{type(source_object).__name__}.{attribute_name}"

    if isinstance(attribute_value, Qty):
        if cim_unit_type is None:
            raise ValueError(
                f"{where} is not a CIMUnit-typed attribute, but got a Qty ({attribute_value!r})"
            )
        unit_name = cim_unit_type(0).unit.name
        if attribute_value.is_relative() and unit_name in ("ohm", "S"):
            z_base = impedance.z_base(source_object, system_base, on_system_base=attribute_value.base == 'system')
            per_unit = attribute_value.value / 100 if attribute_value.unit == "percent" else attribute_value.value
            si_value = per_unit * z_base if unit_name == "ohm" else per_unit / z_base
            attribute_value = cim_unit_type(si_value)
        elif attribute_value.unit:
            attribute_value = cim_unit_type(attribute_value.value, attribute_value.unit)
        else:
            attribute_value = cim_unit_type(attribute_value.value)
    elif isinstance(attribute_value, CIMUnit) and cim_unit_type is not None:
        attribute_value = cim_unit_type(attribute_value)
    elif isinstance(attribute_value, bool) or bool in declared:
        if not (isinstance(attribute_value, bool) and bool in declared):
            raise TypeError(
                f"{where} expects {[t.__name__ for t in declared]}, got {attribute_value!r}"
            )
    elif isinstance(attribute_value, (int, float)) and cim_unit_type is not None:
        attribute_value = cim_unit_type(attribute_value)
    elif isinstance(attribute_value, (int, float)) and float in declared:
        attribute_value = float(attribute_value)
    elif not isinstance(attribute_value, tuple(declared)):
        raise TypeError(
            f"{where} expects {[t.__name__ for t in declared]}, got {attribute_value!r}"
        )

    setattr(source_object, attribute_name, attribute_value)


def _set_side(obj: object, attr: str, field: dataclasses.Field, target: object) -> None:
    """One side of an association write - list-append (dedup by identity,
    matching cimgraph's own create_edge) or singular overwrite, per field.type."""
    if "list" in field.type:
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
    inverse = field.metadata.get("inverse")
    if inverse is None:
        raise ValueError(
            f"{assc!r} is a plain attribute on {part.__name__}.{type(obj).__name__} "
            f"(not an association) - use set_attr, not set_assc"
        )

    reverse_cls_name, reverse_attr = inverse.split(".")
    reverse_field = getattr(part, reverse_cls_name).__dataclass_fields__[reverse_attr]

    _set_side(obj, assc, field, target)
    if target is not None:
        _set_side(target, reverse_attr, reverse_field, obj)
    return target


def add_to_graph(network: GraphModel, obj: object) -> None:
    """The single call site (§12.3): add obj to the live graph, which also
    indexes its mRID (cim-graph raises on an mRID already used by another
    object - mRIDs are unique within the model, not per class).

    A BasePower sets network.base_power, the system base every later
    system-base pu/percent value converts against (PSS/E SBASE, MATPOWER
    baseMVA), so declare it before them. One without a basePower, or a second
    one with a different basePower, raises: either would leave pu values
    silently on the wrong base."""
    base_power_cls = getattr(network.cim, 'BasePower', None)
    if base_power_cls is not None and isinstance(obj, base_power_cls):
        if obj.basePower is None:
            raise ValueError(f"BasePower {obj.mRID!r} has no basePower")
        for other in network.graph.get(base_power_cls, {}).values():
            if not math.isclose(other.basePower.to('VA'), obj.basePower.to('VA')):
                raise ValueError(
                    f"BasePower {obj.mRID!r} ({obj.basePower.to('MVA')} MVA) disagrees with "
                    f"BasePower {other.mRID!r} ({other.basePower.to('MVA')} MVA): a model has one system base"
                )
        network.base_power = obj.basePower
    network.add_to_graph(obj)


def resolve(network: GraphModel, cim_cls: type, mRID: str) -> object | None:
    """O(1) mRID -> object lookup. None means not found - callers decide
    whether that's an auto-vivify or an error. cim_cls may be an abstract
    base (EquipmentContainer, PerLengthImpedance, ...): any subclass matches.
    Raises ValueError if the mRID belongs to an object of another class."""
    obj = network.find_by_mRID(mRID)
    if obj is not None and not isinstance(obj, cim_cls):
        raise ValueError(
            f"mRID {mRID!r} is a {type(obj).__name__}, not a {cim_cls.__name__}"
        )
    return obj


def set_assc_from_mrid(
    network: GraphModel,
    source_object: object,
    profile_part: ModuleType,
    association_name: str,
    target_class: Type[T],
    target_mRID: str | None,
) -> T:
    """resolve() + set_assc() in one call, for the common required-FK shape
    every add_<part> method otherwise hand-writes: resolve the target by
    mRID, raise a consistent ValueError if it's missing, else wire it up.
    Returns the resolved target (matching set_assc's own return).

    Leave a multi-target check (e.g. two ends resolved together with one
    combined error message) as free-standing resolve()/set_assc() calls -
    this helper is for the single-target case only."""

    target = resolve(network, target_class, target_mRID)
    if target is None:
        raise ValueError(
            f"{getattr(source_object, 'mRID', source_object)!r}: {association_name} {target_mRID!r} not found in graph "
            f"(build it before the object that references it)"
        )
    return set_assc(source_object, profile_part, association_name, target)
