"""Impedance base for pu/percent -> ohm/siemens conversion
(design: .development/PERCENT_IMPEDANCE_BASE.md). graph_write.set_attr does
the conversion itself: ohm = pu * z_base, siemens = pu / z_base (one base for
impedance and admittance, PSS/E RAW's convention)."""

from cimprofiles.units.units import CIMUnit, ureg

from cimtables.units import Qty


def z_base(obj: object, system_base: CIMUnit | Qty | float | None, *, on_system_base: bool = False,
           power_base: CIMUnit | Qty | float | None = None) -> float:
    """Impedance base U²/S in ohms for obj's pu/percent values.

    Which U and S is decided by class shape, never by which values happen to
    be set, so a missing rating fails instead of silently using another base:
    1. obj itself, else the target of one of its From* references
       (TransformerMeshImpedance's FromWinding/FromTransformerEnd: CIM's "the
       'from' end ... determines the voltage reference"), if that class is
       rated: for each of U and S, the base attribute where the class has it,
       else the rating - baseU/baseS on a TransformerWinding (coil voltage and
       per-coil kVA, e.g. ratedS/3 on a 3-phase coil, so no phase count is
       needed here), ratedU/ratedS on a PowerTransformerEnd, baseU/ratedS on an
       AutoTransformerEnd (coil voltage, through rating). Both must be set.
    2. else the system base (ACLineSegment, EnergySource): obj's own
       BaseVoltage.nominalVoltage, or nomU where the class has it
       (ShuntCompensator; it must be set), and `system_base`, the system base
       power (network.base_power, a CIMUnit, a Qty such as Qty(100, 'MVA'), or
       a plain number in VA).
    `on_system_base=True` (a Qty with base='system', a `(pu system)` column)
    skips step 1, e.g. a PowerTransformerEnd's BaseVoltage on PSS/E's SBASE.
    `power_base` replaces S (not U) on either path: a mesh impedance on another
    power at its From winding's own voltage, as DSS/GLM put every percent on
    winding 1's kVA (CIMHub U12; get_qty only, set_attr never passes it).
    Raises ValueError naming what is missing."""

    # the caller's power in VA: power_base on either path, else system_base on the system path
    power = power_base if power_base is not None else system_base
    if isinstance(power, Qty):
        power_va = ureg.Quantity(power.value, power.unit).to('VA').magnitude
    elif isinstance(power, CIMUnit):
        power_va = power.to('VA')
    else:
        power_va = None if power is None else float(power)

    # single-valued From* references only: a list one (TransformerEnd.
    # FromMeshImpedance) is the inverse side and never the base source
    from_targets = []
    for name in getattr(obj, '__dataclass_fields__', {}):
        target = getattr(obj, name)
        if name.startswith('From') and target is not None and not isinstance(target, list):
            from_targets.append(target)
    for candidate in [] if on_system_base else [obj, *from_targets]:
        # each side: the base attribute where the class has one, else the rating
        # (AutoTransformerEnd has baseU but only ratedS)
        u_attr = 'baseU' if hasattr(candidate, 'baseU') else 'ratedU'
        s_attr = 'baseS' if hasattr(candidate, 'baseS') else 'ratedS'
        if not (hasattr(candidate, u_attr) and hasattr(candidate, s_attr)):
            continue
        u, s = getattr(candidate, u_attr), getattr(candidate, s_attr)
        if u is None or (s is None and power_base is None):
            raise ValueError(
                f"{type(candidate).__name__} {getattr(candidate, 'mRID', candidate)!r}: "
                f"a pu/percent impedance needs both {u_attr} and {s_attr} set"
            )
        return u.to('V') ** 2 / (s.to('VA') if power_base is None else power_va)

    if hasattr(obj, 'nomU'):
        nominal_voltage = obj.nomU
        voltage_source = 'nomU'
    else:
        nominal_voltage = getattr(getattr(obj, 'BaseVoltage', None), 'nominalVoltage', None)
        voltage_source = 'a BaseVoltage with nominalVoltage'
    if nominal_voltage is None or power_va is None:
        raise ValueError(
            f"{type(obj).__name__} {getattr(obj, 'mRID', obj)!r}: a pu/percent impedance needs "
            f"a rated From* reference, or {voltage_source} and a system_base power "
            f"(in .cimtbl: a BasePower declared before this row)"
        )
    return nominal_voltage.to('V') ** 2 / power_va
