"""bind_row behavior on a FeederModel: the `phases` PhaseCode extension on
shunt phase children (delta branches), a TransformerEnd's node column, the
identifier key of Identity-only classes, and a comma-spec merged profile.
The profile is set per test via monkeypatch (cim18v17, or a comma spec of its
sub-profiles), and get_cim_profile's cache cleared on both sides."""

import importlib
import tempfile

import pytest
from cimgraph.core.env_vars import get_cim_profile
from cimgraph.databases import XMLFile
from cimgraph.models import FeederModel

from cimtables import graph_write, parser, validate
from cimtables.binder import bind_row


@pytest.fixture
def cim(monkeypatch):
    monkeypatch.setenv('CIMG_CIM_PROFILE', 'cim18v17')
    get_cim_profile.cache_clear()
    yield importlib.import_module('cimprofiles.cim18.cim18v17')
    get_cim_profile.cache_clear()


@pytest.fixture
def network(cim):
    net = FeederModel(connection=XMLFile(filename=tempfile.mktemp(suffix='.xml')),
                      container=cim.Feeder(mRID='test_feeder'), distributed=False)
    return net


def _bind_text(network, text):
    records, _ = parser.parse_text(text, source_file='<test>')
    return [bind_row(network, validate.validate(r)) for r in records]


def test_delta_load_children_are_line_to_line_branches(network, cim):
    _bind_text(network, (
        'Table EnergyConsumer: mRID, node, phases, phaseConnection\n'
        '    ld_646, 646, BC,  D\n'
        '    ld_692, 692, CA,  D\n'
        '    ld_671, 671, ABC, D\n'
        '    ld_645, 645, B,   Y\n'
    ))
    branches = {
        mRID: [c.phases.value if c.phases else c.phase.value
               for c in graph_write.resolve(network, cim.EnergyConsumer, mRID).EnergyConsumerPhase]
        for mRID in ('ld_646', 'ld_692', 'ld_671', 'ld_645')
    }
    assert branches == {'ld_646': ['BC'], 'ld_692': ['AC'], 'ld_671': ['AB', 'BC', 'AC'], 'ld_645': ['B']}
    assert graph_write.resolve(network, cim.EnergyConsumer, 'ld_692').Terminals[0].phases == cim.PhaseCode.AC


def test_transformer_end_node_and_phases_go_on_power_transformer_terminal(network, cim):
    _bind_text(network, (
        'Object PowerTransformer: mRID=tpoletop\n'
        'Table PowerTransformerEnd: mRID, node, phases, endNumber, PowerTransformer\n'
        '    tpoletop_1, 670,   B,     1, tpoletop\n'
        '    tpoletop_2, house, s1s2N, 2, tpoletop\n'
    ))
    xfmr = graph_write.resolve(network, cim.PowerTransformer, 'tpoletop')
    assert [(t.mRID, t.sequenceNumber, t.phases.value, t.ConnectivityNode.mRID) for t in xfmr.Terminals] == [
        ('tpoletop_T1', 1, 'B', '670'), ('tpoletop_T2', 2, 's12N', 'house'),
    ]
    assert graph_write.resolve(network, cim.PowerTransformerEnd, 'tpoletop_2').Terminal is xfmr.Terminals[1]


def test_transformer_end_node_needs_its_power_transformer(network):
    with pytest.raises(ValueError, match='needs its PowerTransformer'):
        _bind_text(network, (
            'Table PowerTransformerEnd: mRID, node, endNumber\n'
            '    orphan_1, 670, 1\n'
        ))


def test_identity_only_rows_are_keyed_by_optional_identifier(network, cim):
    # PhaseImpedanceData is not an IdentifiedObject: no mRID, and nothing references it
    _, keyed, unkeyed = _bind_text(network, (
        'Object PerLengthPhaseImpedance: mRID=mtx605, conductorCount=1\n'
        'Table PhaseImpedanceData: identifier, PhaseImpedance, row, column, r (ohm/mile)\n'
        '    mtx605_1_1, mtx605, 1, 1, 1.3292\n'
        '              , mtx605, 2, 1, 0.2066\n'
    ))
    assert len(graph_write.resolve(network, cim.PerLengthPhaseImpedance, 'mtx605').PhaseImpedanceData) == 2
    # a given identifier seeds a deterministic UUID; without one it is random
    assert keyed.identifier == cim.PhaseImpedanceData().uuid(mRID='mtx605_1_1')
    assert keyed.identifier.version == 5 and unkeyed.identifier.version == 4


def test_identity_only_class_has_no_mrid_column(network):
    with pytest.raises(validate.CimtblValidationError, match=r"PhaseImpedanceData is not an IdentifiedObject .* identifier column"):
        _bind_text(network, 'Object PhaseImpedanceData: mRID=mtx605_1_1, row=1\n')


def test_identified_object_row_needs_an_mrid(network):
    with pytest.raises(validate.CimtblValidationError, match='BaseVoltage row has no mRID'):
        _bind_text(network, 'Object BaseVoltage: name=4.16 kV\n')


def test_comma_spec_merged_profile_binds_end_to_end(monkeypatch):
    # CIMG_CIM_PROFILE may be a comma-separated list of sub-profiles merged at
    # runtime: validate and network.cim must be the same merged module, and the
    # binder's reflection (get_type_hints) must work on its classes
    monkeypatch.setenv('CIMG_CIM_PROFILE', 'cim18v17.connectivity_unbalanced,cim18v17.electrical_unbalanced')
    get_cim_profile.cache_clear()
    try:
        _, merged = get_cim_profile()
        network = FeederModel(connection=XMLFile(filename=tempfile.mktemp(suffix='.xml')),
                              container=merged.Feeder(mRID='fdr'), distributed=False)
        assert network.cim is merged
        _bind_text(network, (
            'Object BaseVoltage: mRID=bv_4p16kv, nominalVoltage (kV)=4.16\n'
            'Object PerLengthPhaseImpedance: mRID=mtx601, conductorCount=3\n'
            'Object ACLineSegment: mRID=ln_650632, node1=rg60, node2=632, phases=ABC, '
            'length (ft)=2000, PerLengthImpedance=mtx601, BaseVoltage=bv_4p16kv\n'
        ))
        line = network.find_by_mRID('ln_650632')
        assert type(line) is merged.ACLineSegment
        assert line.PerLengthImpedance is network.find_by_mRID('mtx601')
        assert line.BaseVoltage.nominalVoltage.to('kV') == 4.16
        assert len(line.ACLineSegmentPhases) == 3
        # a field named like its type (BaseVoltage) resolves to the class, not its None default
        assert 'BaseVoltage' in validate.reference_slots('EnergyConsumerRow')
    finally:
        get_cim_profile.cache_clear()


def test_bus_column_wires_terminal_to_topological_node(network, cim):
    _bind_text(network, (
        'Object TopologicalNode: mRID=feeder_head_bus\n'
        'Object EnergyConsumer: mRID=aggregate_load, bus=feeder_head_bus, p (kW)=3000\n'
    ))
    [terminal] = graph_write.resolve(network, cim.EnergyConsumer, 'aggregate_load').Terminals
    assert terminal.TopologicalNode is graph_write.resolve(network, cim.TopologicalNode, 'feeder_head_bus')


def test_transformer_end_bus_column_wires_topological_node(network, cim):
    _bind_text(network, (
        'Object PowerTransformer: mRID=sub3\n'
        'Object PowerTransformerEnd: mRID=sub3_2, bus=650, endNumber=2, PowerTransformer=sub3\n'
    ))
    terminal = graph_write.resolve(network, cim.PowerTransformerEnd, 'sub3_2').Terminal
    assert terminal.TopologicalNode is graph_write.resolve(network, cim.TopologicalNode, '650')
