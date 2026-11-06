"""Phase 3 exit-criterion test (design: CIMTBL_DESIGN.md §8).

"Simplest real classes end-to-end into a live cimgraph model: BaseVoltage,
BaseFrequency, BasePower, EnergySource. network.cim read once; plain scalar
attrs + units land correctly (verified via .to()); objects are keyed by
mRID (cim-graph's index, populated at the add_to_graph call site), and an
mRID reused by another object - in any class - raises."

ieee13.cimtbl is parsed and validated through the real Phase 1/2 pipeline
(no hand-built records) and bound into a real BusBranchModel.
"""

import os
import tempfile
from pathlib import Path

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.databases import XMLFile
from cimgraph.models import BusBranchModel

from cimtables import graph_write
from cimtables.binder import bind_row
from cimtables import parser, validate

SAMPLE_FILE = Path(__file__).parent / 'fixtures' / 'ieee13.cimtbl'
_TARGET_CLASSES = {'EnergySource', 'BaseFrequency', 'BasePower', 'BaseVoltage'}


@pytest.fixture
def network():
    conn = XMLFile(filename=tempfile.mktemp(suffix='.xml'))
    container = cim.Feeder(mRID='test_feeder')
    net = BusBranchModel(connection=conn, container=container, distributed=False)
    return net


def _bind_target_rows(network):
    records, _ = parser.parse_file(SAMPLE_FILE)
    bound = []
    for record in records:
        if record.cim_class not in _TARGET_CLASSES:
            continue
        row = validate.validate(record)
        bound.append(bind_row(network, row))
    return bound


def test_ieee13_target_classes_bind_end_to_end(network):
    bound = _bind_target_rows(network)
    assert len(bound) == 6  # EnergySource, BaseFrequency, BasePower, 3x BaseVoltage
    assert all(obj.identifier is not None for obj in bound)


def test_network_cim_read_once_matches_bound_object_class(network):
    bound = _bind_target_rows(network)
    assert all(type(obj) is getattr(network.cim, type(obj).__name__) for obj in bound)


def test_plain_scalar_and_unit_attrs_land_correctly(network):
    _bind_target_rows(network)

    base_115 = graph_write.resolve(network, cim.BaseVoltage, 'base_115')
    assert base_115.nominalVoltage.to('V') == 115000.0

    base_freq = graph_write.resolve(network, cim.BaseFrequency, 'base_freq')
    assert base_freq.frequency.to('Hz') == 60.0

    sbase = graph_write.resolve(network, cim.BasePower, 'sbase')
    assert sbase.basePower.to('MVA') == 100.0

    energy_source = graph_write.resolve(network, cim.EnergySource, 'IEEE13nodeckt')
    assert energy_source is not None


def test_mrid_indexed_at_add_to_graph_call_site(network):
    bound = _bind_target_rows(network)
    for obj in bound:
        assert network.graph[type(obj)][obj.identifier] is obj
        assert network.find_by_mRID(obj.mRID) is obj


def test_mrid_reused_in_another_class_raises(network):
    # mRIDs are unique within the model (CIM 18), not per class
    _bind_target_rows(network)
    with pytest.raises(ValueError, match=r"mRID 'base_115' .* already used by BaseVoltage"):
        _bind_text(network, 'Object BaseFrequency: mRID=base_115, frequency=60\n')


def test_name_is_optional_data_not_the_key(network):
    [first] = _bind_text(network, 'Object BaseVoltage: mRID=bv_4p16kv, name=4.16 kV\n')
    renamed = cim.BaseVoltage(mRID='bv_4p16kv', name='renamed')
    assert first.name == '4.16 kV'
    # the UUID is seeded from the mRID alone, so a rename is the same object
    assert renamed.identifier == first.identifier


def test_fk_to_object_of_wrong_class_raises(network):
    with pytest.raises(ValueError, match=r"mRID 'bv_1' is a BaseVoltage, not a PerLengthImpedance"):
        _bind_text(network, (
            'Object BaseVoltage: mRID=bv_1\n'
            'Object ACLineSegment: mRID=line_1, PerLengthImpedance=bv_1\n'
        ))


def _bind_text(network, text):
    records, _ = parser.parse_text(text, source_file='<test>')
    return [bind_row(network, validate.validate(r)) for r in records]


def test_fk_to_abstract_base_resolves_concrete_subclass(network):
    # ACLineSegment.PerLengthImpedance's range is the abstract
    # PerLengthImpedance; the row built a PerLengthPhaseImpedance
    _bind_text(network, (
        'Object PerLengthPhaseImpedance: mRID=mtx601, conductorCount=3\n'
        'Object ACLineSegment: mRID=line_1, PerLengthImpedance=mtx601\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line_1')
    assert line.PerLengthImpedance is graph_write.resolve(network, cim.PerLengthPhaseImpedance, 'mtx601')


def test_node_and_bus_columns_bind_onto_the_same_terminals(network):
    _bind_text(network, (
        'Table ACLineSegment: mRID, node1, bus1, node2, bus2\n'
        'LINE_1_2_1, SUB_1_1_3, TOPO_BUS_1, SUB_2_2_3, TOPO_BUS_2\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'LINE_1_2_1')
    terminals = sorted(line.Terminals, key=lambda t: t.sequenceNumber)
    assert [(t.ConnectivityNode.mRID, t.TopologicalNode.mRID) for t in terminals] == [
        ('SUB_1_1_3', 'TOPO_BUS_1'), ('SUB_2_2_3', 'TOPO_BUS_2'),
    ]


def test_unresolved_fk_raises(network):
    with pytest.raises(ValueError, match=r"PerLengthImpedance 'missing'.*not found"):
        _bind_text(network, 'Object ACLineSegment: mRID=line_1, PerLengthImpedance=missing\n')


_PERCENT_END = (
    'Object PowerTransformerEnd: mRID=end_1, ratedU (V)=4160, ratedS (kVA)=500, r (percent)=1.1\n'
)


def test_percent_impedance_on_own_rating(network):
    _bind_text(network, _PERCENT_END)
    end = graph_write.resolve(network, cim.PowerTransformerEnd, 'end_1')
    z_base = 4160 ** 2 / 500e3
    assert end.r.to('ohm') == pytest.approx(0.011 * z_base)


def test_percent_impedance_on_from_reference_rating(network):
    _bind_text(network, _PERCENT_END + (
        'Object TransformerMeshImpedance: mRID=mesh_1, FromTransformerEnd=end_1, x (percent)=2.0\n'
    ))
    mesh = graph_write.resolve(network, cim.TransformerMeshImpedance, 'mesh_1')
    z_base = 4160 ** 2 / 500e3
    assert mesh.x.to('ohm') == pytest.approx(0.02 * z_base)


def test_percent_impedance_without_base_raises(network):
    with pytest.raises(ValueError, match=r"needs a rated From\* reference"):
        _bind_text(network, 'Object TransformerMeshImpedance: mRID=mesh_1, x (percent)=2.0\n')


def test_percent_impedance_on_unrated_end_raises_instead_of_system_base(network):
    # PowerTransformerEnd also has a BaseVoltage - a missing ratedS must not
    # silently fall back to the system base
    with pytest.raises(ValueError, match=r"needs both ratedU and ratedS"):
        _bind_text(network, _PU_LINE + (
            'Object PowerTransformerEnd: mRID=end_1, BaseVoltage=bv_115kv, ratedU (kV)=115, r (percent)=1.1\n'
        ))


_PU_LINE = 'Object BaseVoltage: mRID=bv_115kv, nominalVoltage (kV)=115\n'
_SYSTEM_BASE = _PU_LINE + 'Object BasePower: mRID=sbase, basePower (MVA)=100\n'


def test_pu_impedance_on_system_base_when_no_rating_pair(network):
    # ACLineSegment has no rating pair: the bound BasePower and the line's own
    # BaseVoltage
    _bind_text(network, _SYSTEM_BASE + (
        'Object ACLineSegment: mRID=line_1, BaseVoltage=bv_115kv, '
        'r (pu)=0.01938, x (pu)=0.05917, bch (pu)=0.0528\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line_1')
    z_base = 115000 ** 2 / 100e6
    assert line.r.to('ohm') == pytest.approx(0.01938 * z_base)
    assert line.x.to('ohm') == pytest.approx(0.05917 * z_base)
    assert line.bch.to('S') == pytest.approx(0.0528 / z_base)


def test_pu_on_system_base_without_a_base_power_raises(network):
    # the model is a function of the text alone: neither CIMG_SYSTEM_MVA_BASE
    # nor a base_power set on the network stands in for a BasePower row
    network.base_power = cim.ApparentPower(10, 'MVA')
    with pytest.raises(ValueError, match=r"'line_1'.*a BasePower declared before this row"):
        _bind_text(network, _PU_LINE + (
            'Object ACLineSegment: mRID=line_1, BaseVoltage=bv_115kv, r (pu)=0.01938\n'
        ))


def test_base_power_after_a_system_base_row_raises(network):
    # CIMHub R2: the end would bind on 100 MVA and the model then report 50
    with pytest.raises(ValueError, match=r"'xf_1_2_end1'.*a BasePower declared before this row"):
        _bind_text(network, _PU_LINE + (
            'Object PowerTransformer: mRID=xf_1_2\n'
            'Table PowerTransformerEnd: mRID, endNumber, BaseVoltage, ratedU (kV), ratedS (MVA), x (pu system), PowerTransformer\n'
            '    xf_1_2_end1, 1, bv_115kv, 115, 50, 0.045, xf_1_2\n'
            'Object BasePower: mRID=sbase, basePower (MVA)=50\n'
        ))


def test_pu_on_own_rating_needs_no_base_power(network):
    _bind_text(network, _PU_LINE + (
        'Object PowerTransformerEnd: mRID=end_1, BaseVoltage=bv_115kv, ratedU (kV)=115, ratedS (MVA)=50, x (pu)=0.1\n'
    ))
    end = graph_write.resolve(network, cim.PowerTransformerEnd, 'end_1')
    assert end.x.to('ohm') == pytest.approx(0.1 * 115000 ** 2 / 50e6)


def test_pu_impedance_without_base_voltage_raises(network):
    with pytest.raises(ValueError, match=r"BaseVoltage with nominalVoltage"):
        _bind_text(network, 'Object ACLineSegment: mRID=line_1, r (pu)=0.01938\n')


def test_pu_system_column_puts_a_rated_end_on_the_system_base(network):
    # PSS/E SBASE impedances: (pu system) skips the end's own ratedU/ratedS
    # for its BaseVoltage and the BasePower; a plain (percent) column on the
    # same row still uses the rating pair
    _bind_text(network, _SYSTEM_BASE + (
        'Object PowerTransformerEnd: mRID=end_1, BaseVoltage=bv_115kv, ratedU (kV)=115, ratedS (MVA)=50, '
        'x (pu system)=0.1, r (percent)=1.1\n'
    ))
    end = graph_write.resolve(network, cim.PowerTransformerEnd, 'end_1')
    assert end.x.to('ohm') == pytest.approx(0.1 * 115000 ** 2 / 100e6)
    assert end.r.to('ohm') == pytest.approx(0.011 * 115000 ** 2 / 50e6)


@pytest.mark.parametrize('unit', ['ohm system', 'pu winding'])
def test_only_pu_or_percent_take_only_the_system_base(network, unit):
    with pytest.raises(validate.CimtblValidationError, match="only pu/percent take a base, and only 'system'"):
        _bind_text(network, f'Object ACLineSegment: mRID=line_1, r ({unit})=0.01\n')


def test_base_power_row_sets_the_system_base(network):
    # SBASE != 100: a bound BasePower must replace the env/default 100 MVA
    _bind_text(network, _PU_LINE + (
        'Object BasePower: mRID=sbase, basePower (MVA)=50\n'
        'Object ACLineSegment: mRID=line_1, BaseVoltage=bv_115kv, r (pu)=0.01938\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line_1')
    assert network.base_power.to('MVA') == pytest.approx(50)
    assert line.r.to('ohm') == pytest.approx(0.01938 * 115000 ** 2 / 50e6)


def test_second_base_power_must_agree(network):
    _bind_text(network, 'Object BasePower: mRID=sbase, basePower (MVA)=50\n'
                        'Object BasePower: mRID=sbase_kva, basePower (kVA)=50000\n')
    with pytest.raises(ValueError, match=r"BasePower 'sbase_100'.*disagrees with BasePower 'sbase'"):
        _bind_text(network, 'Object BasePower: mRID=sbase_100, basePower (MVA)=100\n')


def test_base_power_without_a_value_raises(network):
    with pytest.raises(ValueError, match="BasePower 'sbase' has no basePower"):
        _bind_text(network, 'Object BasePower: mRID=sbase\n')
