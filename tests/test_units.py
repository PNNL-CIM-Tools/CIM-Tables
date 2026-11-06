"""The `(unit)` spellings importers write (CIMHub's probe, CIMTABLES_REQUESTS
R4), pinned so a units-registry change cannot silently drop one. A Qty's unit
reaches pint through the attribute's CIMUnit class (graph_write.set_attr)."""

import math
import os

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim

_FT = 0.3048
_MILE = 1609.344

# (CIMUnit class, spelling, value of 1 <spelling> in the class's SI unit)
_SPELLINGS = [
    (cim.Length, 'm', 1), (cim.Length, 'km', 1e3), (cim.Length, 'ft', _FT), (cim.Length, 'kft', 1e3 * _FT),
    (cim.Length, 'mile', _MILE), (cim.Length, 'mi', _MILE),
    (cim.Length, 'cm', 0.01), (cim.Length, 'mm', 0.001), (cim.Length, 'in', 0.0254),
    (cim.ResistancePerLength, 'ohm/m', 1), (cim.ResistancePerLength, 'ohm/km', 1e-3),
    (cim.ResistancePerLength, 'ohm/kft', 1 / (1e3 * _FT)), (cim.ResistancePerLength, 'ohm/mile', 1 / _MILE),
    (cim.ResistancePerLength, 'ohm/mi', 1 / _MILE), (cim.ResistancePerLength, 'ohm/ft', 1 / _FT),
    (cim.SusceptancePerLength, 'S/m', 1), (cim.SusceptancePerLength, 'uS/km', 1e-9),
    (cim.SusceptancePerLength, 'uS/mile', 1e-6 / _MILE), (cim.SusceptancePerLength, 'nS/mile', 1e-9 / _MILE),
    (cim.ActivePower, 'W', 1), (cim.ActivePower, 'kW', 1e3), (cim.ActivePower, 'MW', 1e6),
    (cim.ReactivePower, 'VAr', 1), (cim.ReactivePower, 'kVAr', 1e3), (cim.ReactivePower, 'MVAr', 1e6),
    (cim.ReactivePower, 'var', 1), (cim.ReactivePower, 'kvar', 1e3), (cim.ReactivePower, 'Mvar', 1e6),
    (cim.ApparentPower, 'VA', 1), (cim.ApparentPower, 'kVA', 1e3), (cim.ApparentPower, 'MVA', 1e6),
    (cim.Voltage, 'V', 1), (cim.Voltage, 'kV', 1e3),
    (cim.AngleDegrees, 'deg', 1), (cim.AngleDegrees, 'degree', 1), (cim.AngleDegrees, 'rad', 180 / math.pi),
    (cim.CurrentFlow, 'A', 1), (cim.CurrentFlow, 'kA', 1e3),
    (cim.Seconds, 's', 1), (cim.Seconds, 'min', 60),
    (cim.Frequency, 'Hz', 1),
]


@pytest.mark.parametrize('cim_unit, spelling, si_value', _SPELLINGS, ids=[s for _, s, _ in _SPELLINGS])
def test_unit_spelling_converts(cim_unit, spelling, si_value):
    quantity = cim_unit(1, spelling)
    assert quantity.to(cim_unit(0).unit.value) == pytest.approx(si_value)
