"""Graph-write core tests.

set_attr binds a Qty into the right CIMUnit subclass (verified via .to()) or
passes a plain scalar straight through, checking `attr` against a reference
part module first; set_assc writes both the forward and reverse side of an
association, resolved from the part's field metadata; add_to_graph writes to
network.graph, which indexes the mRID; resolve is an O(1) mRID lookup that
matches subclasses and raises only on an object of the wrong class.
"""

import os
import tempfile

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.databases import XMLFile
from cimgraph.models import BusBranchModel

from cimtables import graph_write
from cimtables.units import Qty


@pytest.fixture
def network():
    conn = XMLFile(filename=tempfile.mktemp(suffix='.xml'))
    container = cim.Feeder(mRID='test_feeder')
    net = BusBranchModel(connection=conn, container=container, distributed=False)
    return net


def test_set_attr_none_sets_none():
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_attr(bv, cim, 'nominalVoltage', None)
    assert bv.nominalVoltage is None


def test_set_attr_plain_scalar_passes_through():
    line = cim.ACLineSegment(mRID='line')
    graph_write.set_attr(line, cim, 'circuitNumber', 1)
    assert line.circuitNumber == 1


def test_set_attr_qty_with_unit_binds_cimunit():
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_attr(bv, cim, 'nominalVoltage', Qty(115.0, 'kV'))
    assert bv.nominalVoltage.to('V') == 115000.0


def test_set_attr_qty_blank_unit_assumes_base_si():
    freq = cim.BaseFrequency(mRID='f')
    graph_write.set_attr(freq, cim, 'frequency', Qty(60.0, ''))
    assert freq.frequency.to('Hz') == 60.0


def test_set_attr_qty_on_non_quantity_attr_raises():
    bv = cim.BaseVoltage(mRID='bv')
    with pytest.raises(ValueError, match='not a CIMUnit-typed attribute'):
        graph_write.set_attr(bv, cim, 'name', Qty(1.0, 'V'))


def test_set_attr_relative_ohm_uses_the_objects_rating():
    end = cim.PowerTransformerEnd(mRID='end', ratedU=cim.Voltage(10, 'kV'), ratedS=cim.ApparentPower(100, 'kVA'))
    graph_write.set_attr(end, cim, 'r', Qty(2.0, 'percent'))
    assert end.r.to('ohm') == pytest.approx(0.02 * 10e3 ** 2 / 100e3)


def test_set_attr_relative_siemens_divides_by_the_system_base():
    line = cim.ACLineSegment(mRID='line', BaseVoltage=cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(10, 'kV')))
    graph_write.set_attr(line, cim, 'bch', Qty(5.0, 'percent'), system_base=cim.ApparentPower(100, 'MVA'))
    assert line.bch.to('S') == pytest.approx(0.05 / (10e3 ** 2 / 100e6))


def test_set_attr_relative_on_the_system_base_without_system_base_raises():
    line = cim.ACLineSegment(mRID='line', BaseVoltage=cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(10, 'kV')))
    with pytest.raises(ValueError, match='system_base power'):
        graph_write.set_attr(line, cim, 'r', Qty(2.0, 'percent'))


def test_set_attr_pu_system_qty_skips_the_objects_rating():
    end = cim.PowerTransformerEnd(
        mRID='end', BaseVoltage=cim.BaseVoltage(mRID='bv', nominalVoltage=cim.Voltage(115, 'kV')),
        ratedU=cim.Voltage(115, 'kV'), ratedS=cim.ApparentPower(50, 'MVA'))
    graph_write.set_attr(end, cim, 'x', Qty(0.1, 'pu', base='system'), system_base=cim.ApparentPower(100, 'MVA'))
    assert end.x.to('ohm') == pytest.approx(0.1 * 115e3 ** 2 / 100e6)


def test_set_attr_bool_field_rejects_str():
    # 'false' is truthy - stored as-is it would read as an open switch
    switch = cim.Switch(mRID='sw')
    with pytest.raises(TypeError, match='Switch.normalOpen'):
        graph_write.set_attr(switch, cim, 'normalOpen', 'false')
    with pytest.raises(TypeError):
        graph_write.set_attr(switch, cim, 'normalOpen', 0)
    graph_write.set_attr(switch, cim, 'normalOpen', False)
    assert switch.normalOpen is False


def test_set_attr_number_field_rejects_bool_and_str():
    switch = cim.Switch(mRID='sw')
    with pytest.raises(TypeError):
        graph_write.set_attr(switch, cim, 'ratedCurrent', True)
    with pytest.raises(TypeError):
        graph_write.set_attr(switch, cim, 'ratedCurrent', '400')


def test_set_attr_int_field_rejects_float_and_bool():
    terminal = cim.Terminal(mRID='t')
    with pytest.raises(TypeError):
        graph_write.set_attr(terminal, cim, 'sequenceNumber', 1.0)
    with pytest.raises(TypeError):
        graph_write.set_attr(terminal, cim, 'sequenceNumber', True)
    graph_write.set_attr(terminal, cim, 'sequenceNumber', 1)
    assert terminal.sequenceNumber == 1


def test_set_attr_number_on_cimunit_field_is_wrapped_as_si():
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_attr(bv, cim, 'nominalVoltage', 4160)
    assert isinstance(bv.nominalVoltage, cim.Voltage)
    assert bv.nominalVoltage.to('kV') == pytest.approx(4.16)


def test_set_attr_cimunit_is_converted_into_field_unit_class():
    import cimprofiles.cim18.cim18v17.connectivity_unbalanced as CN
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_attr(bv, cim, 'nominalVoltage', CN.Voltage(4.16, 'kV'))
    assert type(bv.nominalVoltage) is cim.Voltage
    assert bv.nominalVoltage.to('V') == pytest.approx(4160)


def test_set_attr_cimunit_dimension_mismatch_raises():
    bv = cim.BaseVoltage(mRID='bv')
    with pytest.raises(Exception, match='[Dd]imensionality|Cannot convert'):
        graph_write.set_attr(bv, cim, 'nominalVoltage', cim.ActivePower(1, 'MW'))


def test_set_attr_unknown_field_raises():
    bv = cim.BaseVoltage(mRID='bv')
    with pytest.raises(ValueError, match='not a field'):
        graph_write.set_attr(bv, cim, 'not_a_real_field', 1)


def test_set_attr_association_field_raises():
    line = cim.ACLineSegment(mRID='line')
    with pytest.raises(ValueError, match='use set_assc'):
        graph_write.set_attr(line, cim, 'BaseVoltage', cim.BaseVoltage(mRID='bv'))


def test_set_assc_writes_forward_and_reverse():
    line = cim.ACLineSegment(mRID='line')
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_assc(line, cim, 'BaseVoltage', bv)
    assert line.BaseVoltage is bv
    assert line in bv.ConductingEquipment


def test_set_assc_reverse_append_dedups_by_identity():
    line = cim.ACLineSegment(mRID='line')
    bv = cim.BaseVoltage(mRID='bv')
    graph_write.set_assc(line, cim, 'BaseVoltage', bv)
    graph_write.set_assc(line, cim, 'BaseVoltage', bv)
    assert bv.ConductingEquipment.count(line) == 1


def test_set_assc_scalar_field_raises():
    bv = cim.BaseVoltage(mRID='bv')
    with pytest.raises(ValueError, match='use set_attr'):
        graph_write.set_assc(bv, cim, 'name', 'other')


def test_add_to_graph_populates_graph_and_mrid_index(network):
    bv = cim.BaseVoltage(mRID='bv_1')
    graph_write.add_to_graph(network, bv)
    assert network.graph[cim.BaseVoltage][bv.identifier] is bv
    assert network.find_by_mRID('bv_1') is bv


def test_resolve_returns_none_on_miss(network):
    assert graph_write.resolve(network, cim.BaseVoltage, 'nonexistent') is None


def test_resolve_returns_bound_object_on_hit(network):
    bv = cim.BaseVoltage(mRID='bv_1')
    graph_write.add_to_graph(network, bv)
    assert graph_write.resolve(network, cim.BaseVoltage, 'bv_1') is bv


def test_resolve_matches_subclass_of_abstract_slot(network):
    feeder = cim.Feeder(mRID='fdr_1')
    graph_write.add_to_graph(network, feeder)
    assert graph_write.resolve(network, cim.EquipmentContainer, 'fdr_1') is feeder


def test_resolve_object_of_wrong_class_raises(network):
    graph_write.add_to_graph(network, cim.BaseVoltage(mRID='bv_1'))
    with pytest.raises(ValueError, match="'bv_1' is a BaseVoltage, not a Feeder"):
        graph_write.resolve(network, cim.Feeder, 'bv_1')
