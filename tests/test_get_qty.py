"""graph_write.get_qty, the read-side mirror of set_attr (CIMHub R7), and
PerCent attributes holding the percent number (CIMHub R14)."""

import os
import re

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.models import BusBranchModel
from cimgraph.utils import write_xml

import cimtables
from cimtables import graph_write
from cimtables.units import Qty

_SYSTEM_BASE = cim.ApparentPower(100, 'MVA')


def _rated_end():
    end = cim.PowerTransformerEnd(mRID='end_1')
    end.ratedU = cim.Voltage(115, 'kV')
    end.ratedS = cim.ApparentPower(50, 'MVA')
    end.BaseVoltage = cim.BaseVoltage(mRID='bv_115', nominalVoltage=cim.Voltage(115, 'kV'))
    return end


def _mesh():
    winding = cim.TransformerWinding(mRID='wdg_1')
    winding.baseU = cim.Voltage(2400)
    winding.baseS = cim.ApparentPower(500, 'kVA')
    return cim.TransformerMeshImpedance(mRID='mesh_1', FromWinding=winding)


def _line():
    return cim.ACLineSegment(mRID='line_1', BaseVoltage=cim.BaseVoltage(mRID='bv_138', nominalVoltage=cim.Voltage(138, 'kV')))


# (object factory, attribute, Qty set_attr is given); get_qty reads it back in the same unit and base
_ROUND_TRIPS = [
    (_rated_end, 'r', Qty(0.0012, 'pu')),
    (_rated_end, 'x', Qty(4.5, 'percent')),
    (_rated_end, 'b', Qty(0.002, 'pu')),
    (_rated_end, 'x', Qty(0.045, 'pu', base='system')),
    (_rated_end, 'g', Qty(0.1, 'percent', base='system')),
    (_mesh, 'r', Qty(1.1, 'percent')),
    (_mesh, 'x', Qty(0.02, 'pu')),
    (_line, 'r', Qty(0.0194, 'pu')),
    (_line, 'bch', Qty(5.28, 'percent')),
    (_line, 'x', Qty(0.059, 'pu', base='system')),
    (_line, 'length', Qty(2000, 'ft')),
    (_rated_end, 'ratedU', Qty(115, 'kV')),
    (_rated_end, 'ratedS', Qty(50000, 'kVA')),
    (lambda: cim.EnergyConsumer(mRID='ld'), 'q', Qty(10, 'kvar')),
    (lambda: cim.EnergyConsumer(mRID='ld'), 'pfixedPct', Qty(5, 'percent')),
    (lambda: cim.PhaseImpedanceData(), 'r', Qty(0.3, 'ohm/mile')),
]


@pytest.mark.parametrize('make, attr, qty', _ROUND_TRIPS, ids=[f'{a}-{q.unit}-{q.base}' for _, a, q in _ROUND_TRIPS])
def test_get_qty_inverts_set_attr(make, attr, qty):
    obj = make()
    graph_write.set_attr(obj, cim, attr, qty, system_base=_SYSTEM_BASE)
    assert graph_write.get_qty(obj, attr, qty.unit, base=qty.base, system_base=_SYSTEM_BASE) == pytest.approx(qty.value)


def test_get_qty_converts_to_another_unit():
    end = _rated_end()
    graph_write.set_attr(end, cim, 'x', Qty(0.1, 'pu'))
    assert graph_write.get_qty(end, 'x', 'ohm') == pytest.approx(0.1 * 115e3 ** 2 / 50e6)
    assert graph_write.get_qty(end, 'ratedU', 'V') == pytest.approx(115e3)


def test_get_qty_of_an_unset_attribute_is_none():
    assert graph_write.get_qty(_rated_end(), 'r', 'pu') is None


@pytest.mark.parametrize('attr, unit, base, match', [
    ('nonesuch', 'kV', None, 'not a field'),
    ('PowerTransformer', 'kV', None, 'is an association'),
    ('endNumber', 'kV', None, 'not a CIMUnit'),
    ('ratedU', 'kV', 'system', 'only pu/percent take a base'),
    ('r', 'pu', 'winding', 'only pu/percent take a base'),
])
def test_get_qty_rejects(attr, unit, base, match):
    end = _rated_end()
    end.endNumber = 1
    with pytest.raises(ValueError, match=match):
        graph_write.get_qty(end, attr, unit, base=base)


def test_get_qty_dimension_mismatch_raises():
    end = _rated_end()
    with pytest.raises(Exception, match='Cannot convert'):
        graph_write.get_qty(end, 'ratedU', 'ohm')


def test_get_qty_on_system_base_without_system_base_raises():
    line = _line()
    graph_write.set_attr(line, cim, 'r', Qty(0.0194, 'pu'), system_base=_SYSTEM_BASE)
    with pytest.raises(ValueError, match='system_base power'):
        graph_write.get_qty(line, 'r', 'pu')


# --- U12: power_base, the same voltage on another power ---------------------

def test_get_qty_power_base_moves_a_mesh_percent_to_another_kva():
    mesh = _mesh()  # From winding: baseU 2400 V, baseS 500 kVA
    graph_write.set_attr(mesh, cim, 'x', Qty(2.0, 'percent'))
    # winding 1 at 1500 kVA: 3x the power on the same voltage triples the percent
    assert graph_write.get_qty(mesh, 'x', 'percent', power_base=Qty(1500, 'kVA')) == pytest.approx(6.0)
    assert graph_write.get_qty(mesh, 'x', 'percent', power_base=cim.ApparentPower(1.5, 'MVA')) == pytest.approx(6.0)


def test_get_qty_power_base_moves_an_admittance_the_other_way():
    end = _rated_end()  # ratedS 50 MVA
    graph_write.set_attr(end, cim, 'g', Qty(0.3, 'percent'))
    assert graph_write.get_qty(end, 'g', 'percent', power_base=150e6) == pytest.approx(0.1)


def test_get_qty_power_base_replaces_the_system_power():
    line = _line()
    graph_write.set_attr(line, cim, 'x', Qty(0.059, 'pu'), system_base=_SYSTEM_BASE)
    assert graph_write.get_qty(line, 'x', 'pu', power_base=Qty(50, 'MVA')) == pytest.approx(0.0295)


def test_get_qty_power_base_on_a_non_relative_unit_raises():
    with pytest.raises(ValueError, match='only pu/percent take a base or power_base'):
        graph_write.get_qty(_rated_end(), 'ratedU', 'kV', power_base=Qty(1, 'MVA'))


# --- R14: PerCent holds the percent number -----------------------------

@pytest.mark.parametrize('value', [5, 5.0, Qty(5, 'percent'), Qty(5, '')])
def test_percent_attribute_stores_the_percent_number(value):
    tank = cim.TransformerTankConfiguration(mRID='xc_x')
    graph_write.set_attr(tank, cim, 'percentImpedance', value)
    assert tank.percentImpedance.value == 5.0
    assert graph_write.get_qty(tank, 'percentImpedance', 'percent') == 5.0


@pytest.mark.parametrize('qty', [Qty(0.05, 'pu'), Qty(5, 'percent', base='system'), Qty(5, 'ohm')])
def test_percent_attribute_rejects_other_units(qty):
    with pytest.raises(ValueError, match='is a PerCent'):
        graph_write.set_attr(cim.TransformerTankConfiguration(mRID='xc_x'), cim, 'percentImpedance', qty)


def test_percent_columns_bind_and_write_as_the_percent_number(tmp_path):
    network = BusBranchModel(connection=None,
                             container=cim.Feeder(mRID='f'), distributed=False)
    cimtables.load_text(
        'Table TransformerTankConfiguration: mRID, percentImpedance (percent), percentMagnetizingCurrent\n'
        '    xc_a, 5, 5\n', network)
    tank = graph_write.resolve(network, cim.TransformerTankConfiguration, 'xc_a')
    assert tank.percentImpedance.value == 5.0
    assert tank.percentMagnetizingCurrent.value == 5.0
    xml_path = tmp_path / 'model.xml'
    write_xml(network, str(xml_path))
    xml = xml_path.read_text()
    assert re.search(r'TransformerTankConfiguration\.percentImpedance[^>]*>5\.0<', xml)
    assert re.search(r'TransformerTankConfiguration\.percentMagnetizingCurrent[^>]*>5\.0<', xml)
