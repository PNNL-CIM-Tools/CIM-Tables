"""Impedance/admittance-base derivation and pu/percent -> ohm/siemens
conversion - the one canonical home for this math (supersedes binder.py's
and cimbuilder's by_template.py's independent _winding_z_base copies).

.development/PERCENT_IMPEDANCE_BASE.md documents the winding-base rule
resolve_winding_z_base/resolve_winding_y_base implement (baseU/baseS, else
ratedU/ratedS, on the object itself or its From*-reference target - no
fallback between the two rating pairs, since a winding's ratedU is
phase-to-phase and would silently be 3x/4x wrong as a stand-in for baseU).
That same doc covers the system-base path (CIMG_SYSTEM_MVA_BASE +
get_system_mva_base, paired with an object's own BaseVoltage) for equipment
(ACLineSegment) with no self-contained rating pair of its own - the same
base drives both z_base (r/x/r0/x0) and y_base (bch/gch/b0ch/g0ch, via
y_base = 1/z_base) for a given object, matching PSS/E RAW's convention of
one SBASE for both.

resolve_winding_z_base/resolve_winding_y_base are deliberately NOT universal
"resolve any equipment's base" entry points - they only fit classes that
carry (or reference) their own rating pair. Lines/EnergySource don't have
one (use the system-base path instead); shunt compensators have no
rating-pair field in this profile at all - a future shunt builder needs the
system-base path too, same as lines."""

from cimtables.units import Qty


def z_base(rated_u_v: float, rated_s_va: float) -> float:
    """Impedance base in ohms: V^2 / S (rated_u_v line-to-line, in volts;
    rated_s_va in VA). The one arithmetic primitive every impedance-base
    path below reduces to. Callers extract plain floats via CIMUnit.to()
    themselves - this function takes no CIMUnit/Qty, just numbers, so it's
    usable from any already-resolved rating pair."""
    return rated_u_v ** 2 / rated_s_va


def y_base(rated_u_v: float, rated_s_va: float) -> float:
    """Admittance base in siemens: S / V^2 = 1 / z_base, on the SAME rating
    pair (one base drives both impedance and admittance conversion for a
    given object - PSS/E RAW convention: a branch's B is pu on the same
    SBASE as its R/X)."""
    return 1.0 / z_base(rated_u_v, rated_s_va)


def _rating(obj: object, attr: str, unit: str) -> float | None:
    """obj.<attr> in `unit`, or None if obj has no such rating set. Uses
    getattr, not direct attribute access, since candidates are often
    runtime network.cim-resolved classes, not static imports."""
    value = getattr(obj, attr, None)
    return value.to(unit) if value is not None else None


def _resolve_winding_rating_pair(candidates: list[object]) -> tuple[float, float] | None:
    """(u_volts, s_va) from the first candidate carrying a full rating pair -
    baseU/baseS (TransformerWinding) checked before ratedU/ratedS
    (PowerTransformerEnd), per PERCENT_IMPEDANCE_BASE.md's no-fallback rule.
    None if no candidate has both. Shared by resolve_winding_z_base and
    resolve_winding_y_base - one rating pair, two bases."""
    for candidate in candidates:
        u_attr, s_attr = ('baseU', 'baseS') if hasattr(candidate, 'baseU') else ('ratedU', 'ratedS')
        u = _rating(candidate, u_attr, 'V')
        s = _rating(candidate, s_attr, 'VA')
        if u and s:
            return u, s
    return None


def resolve_winding_z_base(candidates: list[object]) -> float | None:
    """z_base in ohms from the first candidate carrying a full rating pair -
    see _resolve_winding_rating_pair. None if no candidate has both (set_attr
    then fails fast if a relative value actually needed it).

    `candidates` is typically [obj] for a single winding/end carrying its
    own value, or [obj, from_target] when obj's own value is relative to
    whatever its From* reference points at (TransformerMeshImpedance's
    FromWinding/FromTransformerEnd - CIM: "the 'from' end ... determines
    the voltage reference")."""
    pair = _resolve_winding_rating_pair(candidates)
    return z_base(*pair) if pair is not None else None


def resolve_winding_y_base(candidates: list[object]) -> float | None:
    """y_base in siemens, same candidate-walk and rating pair as
    resolve_winding_z_base (the magnetizing-branch b/g counterpart to r/x -
    e.g. PowerTransformerEnd.b/.g). None if no candidate has both."""
    pair = _resolve_winding_rating_pair(candidates)
    return y_base(*pair) if pair is not None else None


def relative_to_ohm(qty: Qty, z_base_ohm: float) -> float:
    """(moved from units.py, unchanged) a pu/percent impedance in ohms, on
    an already-resolved z_base."""
    per_unit = qty.value / 100 if qty.unit == 'percent' else qty.value
    return per_unit * z_base_ohm


def relative_to_siemens(qty: Qty, y_base_s: float) -> float:
    """A pu/percent admittance (b/g/bch/gch) in siemens, on an
    already-resolved y_base - the susceptance/conductance counterpart to
    relative_to_ohm."""
    per_unit = qty.value / 100 if qty.unit == 'percent' else qty.value
    return per_unit * y_base_s
