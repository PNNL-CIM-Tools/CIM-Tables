"""Impedance base for pu/percent -> ohm/siemens conversion
(design: .development/PERCENT_IMPEDANCE_BASE.md). graph_write.set_attr does
the conversion itself: ohm = pu * z_base, siemens = pu / z_base (one base for
impedance and admittance, PSS/E RAW's convention)."""

from cimprofiles.units.units import CIMUnit, ureg

from cimtables.units import Qty


def z_base(
    obj: object, from_targets: list[object], base_power: CIMUnit | Qty | float | None, *, system_base: bool = False
) -> float:
    """Impedance base U²/S in ohms for obj's pu/percent values.

    Which U and S is decided by class shape, never by which values happen to
    be set, so a missing rating fails instead of silently using another base:
    1. obj itself, else its first From* target (TransformerMeshImpedance's
       FromWinding/FromTransformerEnd: CIM's "the 'from' end ... determines
       the voltage reference"), if that class is rated: baseU/baseS where the
       class has them (TransformerWinding: coil voltage, common tank kVA),
       else ratedU/ratedS (PowerTransformerEnd). Both must be set.
    2. else the system base (ACLineSegment, EnergySource): obj's own
       BaseVoltage.nominalVoltage, or nomU where the class has it
       (ShuntCompensator; it must be set), and `base_power` (network.base_power, a
       CIMUnit, a Qty such as Qty(100, 'MVA'), or a plain number in VA).
    `system_base=True` (a `(pu system)` column, Qty.base == 'system') skips
    step 1, e.g. a PowerTransformerEnd's BaseVoltage on PSS/E's SBASE.
    Raises ValueError naming what is missing."""

    for candidate in [] if system_base else [obj, *from_targets]:
        u_attr, s_attr = ('baseU', 'baseS') if hasattr(candidate, 'baseU') else ('ratedU', 'ratedS')
        if not hasattr(candidate, s_attr):
            continue
        u, s = getattr(candidate, u_attr), getattr(candidate, s_attr)
        if u is None or s is None:
            raise ValueError(
                f"{type(candidate).__name__} {getattr(candidate, 'mRID', candidate)!r}: "
                f"a pu/percent impedance needs both {u_attr} and {s_attr} set"
            )
        return u.to('V') ** 2 / s.to('VA')

    if hasattr(obj, 'nomU'):
        nominal_voltage = obj.nomU
        voltage_source = 'nomU'
    else:
        nominal_voltage = getattr(getattr(obj, 'BaseVoltage', None), 'nominalVoltage', None)
        voltage_source = 'a BaseVoltage with nominalVoltage'
    if nominal_voltage is None or base_power is None:
        raise ValueError(
            f"{type(obj).__name__} {getattr(obj, 'mRID', obj)!r}: a pu/percent impedance needs "
            f"a rated From* reference, or {voltage_source} and a network base_power"
        )
    if isinstance(base_power, Qty):
        base_va = ureg.Quantity(base_power.value, base_power.unit).to('VA').magnitude
    elif isinstance(base_power, CIMUnit):
        base_va = base_power.to('VA')
    else:
        base_va = float(base_power)
    return nominal_voltage.to('V') ** 2 / base_va
