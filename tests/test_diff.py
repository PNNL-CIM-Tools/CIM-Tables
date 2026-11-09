"""cimtables.diff.diff_models (CIMHub R8)."""

import os

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

from cimgraph.core import get_model_authority, get_model_name
from cimgraph.models import NodeBreakerModel

import cimtables
from cimtables.diff import diff_models

_MODEL = (
    'Object BaseVoltage: mRID=bv_4p16, nominalVoltage (kV)=4.16\n'
    'Object BaseVoltage: mRID=bv_480, nominalVoltage (V)=480\n'
    'Object PerLengthPhaseImpedance: mRID=lc_1, conductorCount=1\n'
    'Object PhaseImpedanceData: identifier=lc_1_11, PhaseImpedance=lc_1, row=1, column=1, r (ohm/mile)=0.3\n'
    'Table EnergyConsumer: mRID, node, phases, p (kW), BaseVoltage\n'
    '    ld_1, n1, AB, 10, bv_4p16\n'
)


@pytest.fixture
def model_scope(monkeypatch):
    """A Model= statement sets the process-global model scope; restore it afterwards."""
    monkeypatch.setenv('CIMG_MODEL_AUTHORITY', get_model_authority())
    monkeypatch.setenv('CIMG_MODEL_NAME', get_model_name())
    yield
    monkeypatch.undo()
    get_model_authority.cache_clear()
    get_model_name.cache_clear()


def _model(text):
    network = NodeBreakerModel(container=None, connection=None, distributed=False)
    cimtables.load_text(text, network)
    return network


def test_identical_models_have_no_differences():
    assert diff_models(_model(_MODEL), _model(_MODEL)) == []


def test_unit_round_trip_noise_is_within_tolerance():
    assert diff_models(_model(_MODEL), _model(_MODEL.replace('p (kW), BaseVoltage\n    ld_1, n1, AB, 10,',
                                                             'p (W), BaseVoltage\n    ld_1, n1, AB, 10000.0000001,'))) == []


def test_mRID_matching_ignores_the_model_scope(model_scope):
    # Identity-only PhaseImpedanceData has no mRID: it matches only within one scope
    text = _MODEL.replace("Object PhaseImpedanceData: identifier=lc_1_11, PhaseImpedance=lc_1, row=1, column=1, r (ohm/mile)=0.3\n", '')
    assert diff_models(_model('Model = a\n' + text), _model('Model = b\n' + text)) == []


def test_differences_are_reported():
    actual = _model(_MODEL.replace('p (kW)', 'p (MW)').replace('bv_4p16\n', 'bv_480\n')
                    + 'Object BaseVoltage: mRID=bv_12, nominalVoltage (kV)=12.47\n')
    expected = _model(_MODEL + 'Object BaseVoltage: mRID=bv_69, nominalVoltage (kV)=69\n')
    assert diff_models(expected, actual) == [
        'BaseVoltage bv_12: extra',
        'BaseVoltage bv_69: missing',
        "EnergyConsumer ld_1.BaseVoltage: expected 'bv_4p16', got 'bv_480'",
        'EnergyConsumer ld_1.p: expected 10000.0 watt, got 10000000.0 watt',
    ]


def test_list_associations_compare_by_target_keys():
    feeders = 'Object Feeder: mRID=fdr_1\nObject Feeder: mRID=fdr_2\n'
    expected = _model(feeders + 'Object Substation: mRID=sub, NormalEnergizingFeeder=fdr_1\n')
    actual = _model(feeders + 'Object Substation: mRID=sub, NormalEnergizingFeeder=fdr_2\n')
    assert diff_models(expected, actual) == ["Substation sub.NormalEnergizingFeeder: expected ['fdr_1'], got ['fdr_2']"]


def test_generated_children_compare_like_other_objects():
    differences = diff_models(_model(_MODEL), _model(_MODEL.replace('AB, 10', 'ABC, 10')))
    assert 'EnergyConsumerPhase ld_1_C: extra' in differences
    assert any(d.startswith('Terminal ld_1_T1.phases: expected') for d in differences)
