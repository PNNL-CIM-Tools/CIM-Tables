"""Phase 3/5 graph-write core tests (design: CIMTBL_DESIGN.md §12.3,
cimbuilder/development/GRAPH_WRITE_CONTRACT.md).

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


def test_set_attr_relative_ohm_converts_against_z_base():
    line = cim.ACLineSegment(mRID='line')
    graph_write.set_attr(line, cim, 'r', Qty(2.0, 'percent'), z_base=1000.0)
    assert line.r.to('ohm') == pytest.approx(20.0)


def test_set_attr_relative_ohm_without_z_base_raises():
    line = cim.ACLineSegment(mRID='line')
    with pytest.raises(ValueError, match='needs a z_base'):
        graph_write.set_attr(line, cim, 'r', Qty(2.0, 'percent'))


def test_set_attr_relative_siemens_converts_against_y_base():
    line = cim.ACLineSegment(mRID='line')
    graph_write.set_attr(line, cim, 'bch', Qty(5.0, 'percent'), y_base=1000.0)
    assert line.bch.to('S') == pytest.approx(50.0)


def test_set_attr_relative_siemens_without_y_base_raises():
    line = cim.ACLineSegment(mRID='line')
    with pytest.raises(ValueError, match='needs a y_base'):
        graph_write.set_attr(line, cim, 'bch', Qty(5.0, 'percent'))


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
