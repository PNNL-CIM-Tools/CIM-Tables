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

from cimtables import graph_write, impedance
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
    with pytest.raises(ValueError, match=r"'percent' value needs a z_base"):
        _bind_text(network, 'Object TransformerMeshImpedance: mRID=mesh_1, x (percent)=2.0\n')


_PU_LINE = 'Object BaseVoltage: mRID=bv_115kv, nominalVoltage (kV)=115\n'


def test_pu_impedance_falls_back_to_system_base_when_no_rating_pair(network, monkeypatch):
    # ACLineSegment has no ratedU/ratedS/baseU/baseS at all - resolve_winding_z_base
    # finds nothing on it, so bind_row must fall back to CIMG_SYSTEM_MVA_BASE +
    # the line's own BaseVoltage, same as cimbuilder's ACLineSegmentBuilder.
    monkeypatch.setenv('CIMG_SYSTEM_MVA_BASE', '100')
    impedance.get_system_mva_base.cache_clear()
    try:
        _bind_text(network, _PU_LINE + (
            'Object ACLineSegment: mRID=line_1, BaseVoltage=bv_115kv, '
            'r (pu)=0.01938, x (pu)=0.05917, bch (pu)=0.0528\n'
        ))
    finally:
        impedance.get_system_mva_base.cache_clear()

    line = graph_write.resolve(network, cim.ACLineSegment, 'line_1')
    z_base = 115000 ** 2 / 100e6
    y_base = 1.0 / z_base
    assert line.r.to('ohm') == pytest.approx(0.01938 * z_base)
    assert line.x.to('ohm') == pytest.approx(0.05917 * z_base)
    assert line.bch.to('S') == pytest.approx(0.0528 * y_base)


def test_pu_impedance_without_system_mva_base_raises(network, monkeypatch):
    monkeypatch.delenv('CIMG_SYSTEM_MVA_BASE', raising=False)
    impedance.get_system_mva_base.cache_clear()
    try:
        with pytest.raises(ValueError, match=r"'pu' value needs a z_base"):
            _bind_text(network, _PU_LINE + (
                'Object ACLineSegment: mRID=line_1, BaseVoltage=bv_115kv, r (pu)=0.01938\n'
            ))
    finally:
        impedance.get_system_mva_base.cache_clear()


def test_pu_impedance_without_base_voltage_raises(network, monkeypatch):
    monkeypatch.setenv('CIMG_SYSTEM_MVA_BASE', '100')
    impedance.get_system_mva_base.cache_clear()
    try:
        with pytest.raises(ValueError, match=r"'pu' value needs a z_base"):
            _bind_text(network, 'Object ACLineSegment: mRID=line_1, r (pu)=0.01938\n')
    finally:
        impedance.get_system_mva_base.cache_clear()


