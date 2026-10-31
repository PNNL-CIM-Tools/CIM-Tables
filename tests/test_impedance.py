"""cimtables.impedance tests (design: .development/PERCENT_IMPEDANCE_BASE.md).

z_base is the pure V^2/S arithmetic; resolve_winding_z_base is the
candidate-walk promoted from binder.py's/cimbuilder's independent
_winding_z_base copies (moved from tests/test_binder.py's
test_z_base_prefers_base_ratings_when_class_has_them, which imported the
private binder._winding_z_base directly - that function no longer exists,
superseded by this module); get_system_mva_base is the new system-base path
for equipment (ACLineSegment) with no self-contained rating pair.
"""

import os
from types import SimpleNamespace

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim

from cimtables import impedance
from cimtables.units import Qty


def test_z_base_is_v_squared_over_s():
    assert impedance.z_base(4160, 500e3) == pytest.approx(4160 ** 2 / 500e3)


def test_y_base_is_reciprocal_of_z_base():
    assert impedance.y_base(4160, 500e3) == pytest.approx(1.0 / (4160 ** 2 / 500e3))


def test_resolve_winding_z_base_prefers_base_ratings_when_class_has_them():
    # TransformerWinding-shaped: baseU = coil voltage, baseS = common tank kVA;
    # ratedU is phase-to-phase and must not be used
    half_winding = SimpleNamespace(
        baseU=cim.Voltage(120, 'V'), baseS=cim.ApparentPower(250, 'kVA'),
        ratedU=cim.Voltage(240, 'V'), ratedS=cim.ApparentPower(125, 'kVA'))
    assert impedance.resolve_winding_z_base([half_winding]) == pytest.approx(120 ** 2 / 250e3)

    half_winding.baseS = None  # no silent fallback to ratedS
    assert impedance.resolve_winding_z_base([half_winding]) is None


def test_resolve_winding_z_base_falls_back_to_rated_ratings_when_class_has_no_base():
    # PowerTransformerEnd-shaped: no baseU/baseS field at all
    end = SimpleNamespace(ratedU=cim.Voltage(12470, 'V'), ratedS=cim.ApparentPower(200, 'kVA'))
    assert impedance.resolve_winding_z_base([end]) == pytest.approx(12470 ** 2 / 200e3)


def test_resolve_winding_z_base_falls_back_to_second_candidate():
    # obj's own value is relative to its From* target's rating, not its own
    own = SimpleNamespace()
    from_target = SimpleNamespace(ratedU=cim.Voltage(4160, 'V'), ratedS=cim.ApparentPower(500, 'kVA'))
    assert impedance.resolve_winding_z_base([own, from_target]) == pytest.approx(4160 ** 2 / 500e3)


def test_resolve_winding_z_base_none_when_no_candidate_has_a_full_pair():
    assert impedance.resolve_winding_z_base([SimpleNamespace()]) is None


def test_resolve_winding_y_base_is_reciprocal_of_z_base_on_same_pair():
    end = SimpleNamespace(ratedU=cim.Voltage(12470, 'V'), ratedS=cim.ApparentPower(200, 'kVA'))
    z = impedance.resolve_winding_z_base([end])
    y = impedance.resolve_winding_y_base([end])
    assert y == pytest.approx(1.0 / z)


def test_resolve_winding_y_base_none_when_no_candidate_has_a_full_pair():
    assert impedance.resolve_winding_y_base([SimpleNamespace()]) is None


def test_get_system_mva_base_none_when_unset(monkeypatch):
    monkeypatch.delenv('CIMG_SYSTEM_MVA_BASE', raising=False)
    impedance.get_system_mva_base.cache_clear()
    assert impedance.get_system_mva_base() is None
    impedance.get_system_mva_base.cache_clear()


def test_get_system_mva_base_reads_env_var(monkeypatch):
    monkeypatch.setenv('CIMG_SYSTEM_MVA_BASE', '100')
    impedance.get_system_mva_base.cache_clear()
    assert impedance.get_system_mva_base() == pytest.approx(100.0)
    impedance.get_system_mva_base.cache_clear()


def test_relative_to_ohm_percent_divides_by_100():
    assert impedance.relative_to_ohm(Qty(2.5, 'percent'), 1000.0) == pytest.approx(25.0)


def test_relative_to_ohm_pu_is_used_as_is():
    assert impedance.relative_to_ohm(Qty(0.025, 'pu'), 1000.0) == pytest.approx(25.0)


def test_relative_to_siemens_percent_divides_by_100():
    assert impedance.relative_to_siemens(Qty(5.0, 'percent'), 1000.0) == pytest.approx(50.0)


def test_relative_to_siemens_pu_is_used_as_is():
    assert impedance.relative_to_siemens(Qty(0.05, 'pu'), 1000.0) == pytest.approx(50.0)
