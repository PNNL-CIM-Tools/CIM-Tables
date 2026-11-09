"""bind_row behavior on a FeederModel: the `phases` PhaseCode extension on
shunt phase children (delta branches), a TransformerEnd's node column, the
identifier key of Identity-only classes, and a comma-spec merged profile.
The profile is set per test via monkeypatch (cim18v17, or a comma spec of its
sub-profiles), and get_cim_profile's cache cleared on both sides."""

import importlib

import pytest
from cimgraph.core.env_vars import get_cim_profile
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
    net = FeederModel(connection=None,
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
        mRID: [(c.phase.value, c.phases.value if c.phases else None)
               for c in graph_write.resolve(network, cim.EnergyConsumer, mRID).EnergyConsumerPhase]
        for mRID in ('ld_646', 'ld_692', 'ld_671', 'ld_645')
    }
    # normative phase = from-phase of the branch (to the next logical phase); phases = extension
    assert branches == {
        'ld_646': [('B', 'BC')],
        'ld_692': [('C', 'AC')],
        'ld_671': [('A', 'AB'), ('B', 'BC'), ('C', 'AC')],
        'ld_645': [('B', None)],
    }
    assert graph_write.resolve(network, cim.EnergyConsumer, 'ld_692').Terminals[0].phases == cim.PhaseCode.AC


def test_delta_ignores_neutral_and_needs_two_line_phases(network, cim):
    _bind_text(network, 'Object EnergyConsumer: mRID=ld_d, node=671, phases=ABCN, phaseConnection=D\n')
    load = graph_write.resolve(network, cim.EnergyConsumer, 'ld_d')
    assert [c.phase.value for c in load.EnergyConsumerPhase] == ['A', 'B', 'C']
    with pytest.raises(ValueError, match='two non-neutral phases'):
        _bind_text(network, 'Object EnergyConsumer: mRID=ld_d1, node=671, phases=BN, phaseConnection=D\n')


def test_bus_prefixed_cim_attributes_are_not_connectivity_columns(network, cim):
    _bind_text(network, 'Object TopologicalNode: mRID=topo_bus_1, busName=BUS 1, busNumber=1\n')
    bus = graph_write.resolve(network, cim.TopologicalNode, 'topo_bus_1')
    assert (bus.busName, bus.busNumber, bus.Terminal) == ('BUS 1', 1, [])


def test_switch_row_with_different_phases_per_side(network, cim):
    _bind_text(network, (
        'Table Breaker: mRID, node1, node2, phaseSide1, phaseSide2\n'
        '    brkr_1, 671, 692, AB, AC\n'
    ))
    breaker = graph_write.resolve(network, cim.Breaker, 'brkr_1')
    assert [(c.phaseSide1.value, c.phaseSide2.value) for c in breaker.SwitchPhase] == [('A', 'A'), ('B', 'C')]
    assert [t.phases.value for t in breaker.Terminals] == ['AB', 'AC']


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


def test_auto_transformer_end_node_and_phase_go_on_auto_transformer_terminal(network, cim):
    _bind_text(network, (
        'Object VoltageRegulator: mRID=vr_1, construction=typeA\n'
        'Table AutoTransformerEnd: mRID, node, phase, endNumber, AutoTransformer\n'
        '    vr_1_end1, 650,  AN, 1, vr_1\n'
        '    vr_1_end2, rg60, AN, 2, vr_1\n'
    ))
    regulator = graph_write.resolve(network, cim.VoltageRegulator, 'vr_1')
    assert [(t.mRID, t.sequenceNumber, t.phases.value, t.ConnectivityNode.mRID) for t in regulator.Terminals] == [
        ('vr_1_T1', 1, 'AN', '650'), ('vr_1_T2', 2, 'AN', 'rg60'),
    ]
    end2 = graph_write.resolve(network, cim.AutoTransformerEnd, 'vr_1_end2')
    assert end2.Terminal is regulator.Terminals[1] and end2.phase.value == 'AN'


def test_auto_transformer_end_node_needs_its_auto_transformer(network):
    with pytest.raises(ValueError, match='AutoTransformerEnd needs its AutoTransformer'):
        _bind_text(network, (
            'Table AutoTransformerEnd: mRID, node, endNumber\n'
            '    orphan_1, 650, 1\n'
        ))


def test_auto_transformer_end_takes_phase_not_phases(network):
    with pytest.raises(ValueError, match='real phase attribute'):
        _bind_text(network, (
            'Object VoltageRegulator: mRID=vr_1\n'
            'Table AutoTransformerEnd: mRID, node, phases, endNumber, AutoTransformer\n'
            '    vr_1_end1, 650, AN, 1, vr_1\n'
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
        network = FeederModel(connection=None,
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


def test_wire_configuration_is_a_plain_reference(network, cim):
    """A line template is the cim18v17 association ACLineSegment.WireConfiguration,
    bound like any other reference column (no DSL `Template` column)."""
    _bind_text(network, (
        'Object WireSpacing: mRID=spc_500601\n'
        'Object WireConfiguration: mRID=wc_601, numberOfPhases=3, WireSpacing=spc_500601\n'
        'Table ACLineSegment: mRID, node1, node2, phases, length (ft), WireConfiguration\n'
        '    ln_650632, rg60, 632, ABC, 2000, wc_601\n'
        '    ln_632671, 632,  671, ABC, 2000, wc_601\n'
    ))
    template = graph_write.resolve(network, cim.WireConfiguration, 'wc_601')
    lines = [graph_write.resolve(network, cim.ACLineSegment, mRID) for mRID in ('ln_650632', 'ln_632671')]
    assert all(line.WireConfiguration is template for line in lines)
    assert template.WireSpacing is graph_write.resolve(network, cim.WireSpacing, 'spc_500601')


def test_template_column_is_rejected(network, cim):
    with pytest.raises(validate.CimtblValidationError, match="no attribute 'Template'"):
        _bind_text(network, 'Object ACLineSegment: mRID=ln_1, Template=wc_601\n')
