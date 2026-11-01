"""cimtables.impedance.z_base tests (design: .development/PERCENT_IMPEDANCE_BASE.md).

The base is chosen by class shape: a rated class (own or From* target) uses
its rating pair and fails if unset; only an unrated class uses the system base.
"""

import os
from types import SimpleNamespace

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim

from cimtables import impedance

_SYSTEM = cim.ApparentPower(100, 'MVA')


def test_base_ratings_win_over_rated_on_a_winding():
    # TransformerWinding-shaped: baseU = coil voltage, baseS = common tank kVA;
    # ratedU is phase-to-phase and must not be used
    half_winding = SimpleNamespace(
        baseU=cim.Voltage(120, 'V'), baseS=cim.ApparentPower(250, 'kVA'),
        ratedU=cim.Voltage(240, 'V'), ratedS=cim.ApparentPower(125, 'kVA'))
    assert impedance.z_base(half_winding, [], _SYSTEM) == pytest.approx(120 ** 2 / 250e3)


def test_unset_base_rating_raises_without_fallback_to_rated():
    half_winding = SimpleNamespace(
        baseU=cim.Voltage(120, 'V'), baseS=None,
        ratedU=cim.Voltage(240, 'V'), ratedS=cim.ApparentPower(125, 'kVA'))
    with pytest.raises(ValueError, match='baseU and baseS'):
        impedance.z_base(half_winding, [], _SYSTEM)


def test_rated_ratings_on_a_class_without_base_ratings():
    end = cim.PowerTransformerEnd(mRID='end', ratedU=cim.Voltage(12470, 'V'), ratedS=cim.ApparentPower(200, 'kVA'))
    assert impedance.z_base(end, [], _SYSTEM) == pytest.approx(12470 ** 2 / 200e3)


def test_unrated_object_uses_from_target_rating():
    mesh = cim.TransformerMeshImpedance(mRID='mesh')
    from_end = cim.PowerTransformerEnd(mRID='end', ratedU=cim.Voltage(4160, 'V'), ratedS=cim.ApparentPower(500, 'kVA'))
    assert impedance.z_base(mesh, [from_end], _SYSTEM) == pytest.approx(4160 ** 2 / 500e3)


def test_rated_class_with_unset_rating_raises_instead_of_system_base():
    bv = cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(115, 'kV'))
    end = cim.PowerTransformerEnd(mRID='end', BaseVoltage=bv, ratedU=cim.Voltage(115, 'kV'))
    with pytest.raises(ValueError, match='ratedU and ratedS'):
        impedance.z_base(end, [], _SYSTEM)


def test_unrated_class_uses_system_base():
    bv = cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(115, 'kV'))
    line = cim.ACLineSegment(mRID='line', BaseVoltage=bv)
    assert impedance.z_base(line, [], _SYSTEM) == pytest.approx(115e3 ** 2 / 100e6)


def test_system_base_needs_base_voltage_and_base_power():
    bv = cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(115, 'kV'))
    with pytest.raises(ValueError, match='BaseVoltage with nominalVoltage'):
        impedance.z_base(cim.ACLineSegment(mRID='line'), [], _SYSTEM)
    with pytest.raises(ValueError, match='base_power'):
        impedance.z_base(cim.ACLineSegment(mRID='line', BaseVoltage=bv), [], None)
