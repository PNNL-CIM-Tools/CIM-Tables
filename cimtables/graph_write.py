"""The graph-write core - set_attr / set_assc / add_to_graph / resolve: the
only place that actually mutates a live cimgraph object or writes it into a
GraphModel. Everything else (the binder, the
connectivity backend, builder add_<part> methods) calls through here rather
than touching `setattr`/`network.add_to_graph` directly, so unit-binding,
profile-part checking, reverse-association writes, and mRID-indexing can
never be forgotten at a call site.

set_attr/set_assc's `part` argument is a reference profile-part module (e.g.
cimprofiles.cim18.cim18gmdm.connectivity) checked purely by name against
type(obj).__name__ - never by isinstance/inheritance. `obj` need not (and
usually won't) be an instance of any class in `part`; `part` is a shape
reference, not `obj`'s actual runtime profile, so a builder's add_<part>
method can check it writes only its profile slice's fields. Passing the
merged `network.cim` module itself as `part` is a documented no-op check
(every field passes, since a merged profile has no restricted attribute
set) - the right choice at a reflective call site (binder, connectivity),
where validate.py has already decided which columns a row may carry.
"""

import dataclasses
import functools
import math
import typing
from types import ModuleType
from typing import TypeVar

from cimgraph.models import GraphModel
from cimprofiles.units.units import CIMUnit

from cimtables import impedance
from cimtables.units import Qty

T = TypeVar("T")

# A CIM class's resolved annotations, once per class: re-resolving them on
# every row was 75% of bind time (CIMHub G7). Classes never change once
# imported, so the cache never goes stale; callers must not mutate the dict.
type_hints = functools.cache(typing.get_type_hints)

# A CIMUnit class's SI unit name ('ohm', 'S', ...), once per class: `unit` is an
# instance property, and building a throwaway instance (a pint Quantity) per
# value doubled the pint work of every (unit) cell.
_si_unit_name = functools.cache(lambda cim_unit_type: cim_unit_type(0).unit.name)


def _part_field(part: ModuleType, obj: object, attr: str) -> dataclasses.Field:
    """The dataclasses.Field for `attr` on part's class of the same name as
    type(obj) - a runtime check, since static typing can't narrow a merged
    profile's classes to one slice. Raises ValueError, naming the
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
      references must be set first.
    - on a PerCent attribute (percentImpedance, pfixedPct, ...) a unitless
      or 'percent' Qty is stored as the percent number (5 % -> PerCent(5.0));
      any other unit raises.
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

    hint = type_hints(type(source_object))[attribute_name]
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
        unit_name = _si_unit_name(cim_unit_type)
        if cim_unit_type.__name__ == "PerCent":
            # PerCent holds the percent number itself (5 % -> 5.0, written as
            # 5 in CIM XML); pint would scale 'percent' to the fraction 0.05
            if attribute_value.unit not in (None, "", "percent") or attribute_value.base is not None:
                raise ValueError(f"{where} is a PerCent: give it unitless or (percent), not {attribute_value!r}")
            attribute_value = cim_unit_type(attribute_value.value)
        elif attribute_value.is_relative() and unit_name in ("ohm", "S"):
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


def get_qty(
    source_object: object,
    attribute_name: str,
    unit: str,
    *,
    base: str | None = None,
    system_base: CIMUnit | Qty | float | None = None,
    power_base: CIMUnit | Qty | float | None = None,
) -> float | None:
    """The read-side mirror of set_attr: a CIMUnit attribute's value in
    `unit` ('kV', 'ohm/mile', 'MVAr'), or None if it is unset.

    'pu'/'percent' on an ohm- or siemens-typed attribute is set_attr's
    conversion inverted, through the same impedance.z_base (the object's
    rating, a From* target's, else its BaseVoltage and `system_base`);
    base='system' forces the system base, as `(pu system)` does. `power_base`
    reads it on another power at the same voltage (impedance.z_base): a mesh
    impedance's percent on winding 1's kVA, x on the From winding's baseU and
    power_base. On a PerCent
    attribute 'percent' (or '') returns the stored percent number. Any other
    unit converts through pint. Raises ValueError for a name that is not a
    field, an association, a non-CIMUnit attribute, a base on a non-relative
    unit, or a missing rating/BaseVoltage/system_base; pint raises on a
    dimension mismatch."""
    where = f"{type(source_object).__name__}.{attribute_name}"
    field = getattr(source_object, "__dataclass_fields__", {}).get(attribute_name)
    if field is None:
        raise ValueError(f"{attribute_name!r} is not a field of {type(source_object).__name__}")
    if field.metadata.get("inverse") is not None:
        raise ValueError(f"{where} is an association, not a quantity")
    if base not in (None, "system") or ((base, power_base) != (None, None) and unit not in ("pu", "percent")):
        raise ValueError(f"{where}: only pu/percent take a base or power_base, and the only base is 'system' "
                         f"(got {unit!r}, base={base!r}, power_base={power_base!r})")

    value = getattr(source_object, attribute_name)
    if value is None:
        return None
    if not isinstance(value, CIMUnit):
        raise ValueError(f"{where} is not a CIMUnit-typed attribute (holds {value!r})")

    unit_name = value.unit.name
    if type(value).__name__ == "PerCent":
        if unit not in ("", "percent") or base is not None:
            raise ValueError(f"{where} is a PerCent: read it as 'percent', not {unit!r}")
        return value.value
    if unit in ("pu", "percent") and unit_name in ("ohm", "S"):
        z_base = impedance.z_base(source_object, system_base, on_system_base=base == "system", power_base=power_base)
        si_value = value.to(unit_name)
        per_unit = si_value / z_base if unit_name == "ohm" else si_value * z_base
        return per_unit * 100 if unit == "percent" else per_unit
    return value.to(unit)


def _set_side(obj: object, attr: str, target: object, *, known_new: bool = False) -> None:
    """One side of an association write - list-append (dedup by identity,
    matching cimgraph's own create_edge, unless the caller knows target is
    not on the list yet) or singular overwrite, by the field's current value."""
    current = getattr(obj, attr)
    if isinstance(current, list):
        if known_new or not any(x is target for x in current):
            current.append(target)
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

    reverse_attr = inverse.split(".")[1]

    # A singular side that did not already point at target means obj is not
    # on target's reverse list yet, so skip its O(n) dedup scan: those lists
    # (BaseVoltage.ConductingEquipment, Feeder.Equipments) grow with the model
    current = getattr(obj, assc)
    reverse_known_new = not isinstance(current, list) and current is not target
    _set_side(obj, assc, target)
    if target is not None:
        _set_side(target, reverse_attr, obj, known_new=reverse_known_new)
    return target


def add_to_graph(network: GraphModel, obj: object) -> None:
    """The single call site: add obj to the live graph, which also
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
    target_class: type[T],
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
