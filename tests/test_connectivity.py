"""Phase 4 connectivity backend tests (design: CIMTBL_DESIGN.md §5, §12.4).

"ACLineSegment with node1/node2/phases -> terminals + N ACLineSegmentPhase;
undeclared nodes auto-vivified; bus* -> TopologicalNode disambiguation proven
on the line_1_2 sequence row." (§8's Phase 4 exit criterion.)
"""

import os
import tempfile

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.databases import XMLFile
from cimgraph.models import BusBranchModel

from cimtables import connectivity, graph_write


@pytest.fixture
def network():
    conn = XMLFile(filename=tempfile.mktemp(suffix='.xml'))
    container = cim.Feeder(mRID='test_feeder')
    net = BusBranchModel(connection=conn, container=container, distributed=False)
    return net


def test_add_connectivity_auto_vivifies_nodes_and_wires_terminals(network):
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)

    terminals = connectivity.add_connectivity(
        network, line, node_cols={'node1': 'RG60', 'node2': '362'}
    )

    assert len(terminals) == 2
    assert [t.sequenceNumber for t in terminals] == [1, 2]
    assert line.Terminals == terminals

    rg60 = graph_write.resolve(network, cim.ConnectivityNode, 'RG60')
    n362 = graph_write.resolve(network, cim.ConnectivityNode, '362')
    assert rg60 is not None and n362 is not None
    assert terminals[0].ConnectivityNode is rg60
    assert terminals[1].ConnectivityNode is n362
    assert terminals[0] in rg60.Terminals
    assert terminals[1] in n362.Terminals


def test_add_connectivity_sets_equipment_container_from_network_default(network):
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)

    connectivity.add_connectivity(network, line, node_cols={'node1': 'RG60', 'node2': '362'})

    assert line.EquipmentContainer is network.container


def test_add_connectivity_propagates_container_to_nodes(network):
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)

    connectivity.add_connectivity(network, line, node_cols={'node1': 'RG60', 'node2': '362'})

    rg60 = graph_write.resolve(network, cim.ConnectivityNode, 'RG60')
    n362 = graph_write.resolve(network, cim.ConnectivityNode, '362')
    assert rg60.ConnectivityNodeContainer is network.container
    assert n362.ConnectivityNodeContainer is network.container


def test_add_connectivity_does_not_overwrite_existing_node_container(network):
    other_container = cim.Feeder(mRID='other_feeder')
    node = cim.ConnectivityNode(mRID='671')
    graph_write.set_assc(node, cim, 'ConnectivityNodeContainer', other_container)
    graph_write.add_to_graph(network, node)

    line = cim.ACLineSegment(mRID='670671')
    graph_write.add_to_graph(network, line)
    connectivity.add_connectivity(network, line, node_cols={'node1': '670', 'node2': '671'})

    assert node.ConnectivityNodeContainer is other_container


def test_add_connectivity_explicit_container_overrides_network_default(network):
    explicit_container = cim.Feeder(mRID='explicit_feeder')
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)

    connectivity.add_connectivity(
        network, line, node_cols={'node1': 'RG60', 'node2': '362'}, container=explicit_container
    )

    assert line.EquipmentContainer is explicit_container
    rg60 = graph_write.resolve(network, cim.ConnectivityNode, 'RG60')
    assert rg60.ConnectivityNodeContainer is explicit_container


def test_add_connectivity_bus_prefix_disambiguates_topological_node(network):
    line = cim.ACLineSegment(mRID='line_1_2')
    graph_write.add_to_graph(network, line)

    terminals = connectivity.add_connectivity(
        network, line, node_cols={'bus1': 'topo_bus_1', 'bus2': 'topo_bus_2'}
    )

    with pytest.raises(ValueError, match="'topo_bus_1' is a TopologicalNode, not a ConnectivityNode"):
        graph_write.resolve(network, cim.ConnectivityNode, 'topo_bus_1')
    topo_1 = graph_write.resolve(network, cim.TopologicalNode, 'topo_bus_1')
    topo_2 = graph_write.resolve(network, cim.TopologicalNode, 'topo_bus_2')
    assert topo_1 is not None and topo_2 is not None
    assert terminals[0].TopologicalNode is topo_1
    assert terminals[0].ConnectivityNode is None


def test_add_connectivity_reuses_same_node_across_calls(network):
    line_a = cim.ACLineSegment(mRID='362670')
    line_b = cim.ACLineSegment(mRID='670671')
    graph_write.add_to_graph(network, line_a)
    graph_write.add_to_graph(network, line_b)

    connectivity.add_connectivity(network, line_a, node_cols={'node1': 'MID', 'node2': '670'})
    connectivity.add_connectivity(network, line_b, node_cols={'node1': '670', 'node2': '671'})

    n670_via_a = graph_write.resolve(network, cim.ConnectivityNode, '670')
    n670_via_b = line_b.Terminals[0].ConnectivityNode
    assert n670_via_a is n670_via_b
    assert len(n670_via_a.Terminals) == 2


def test_add_connectivity_unknown_column_prefix_raises(network):
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)

    with pytest.raises(ValueError, match='not a connectivity column'):
        connectivity.add_connectivity(network, line, node_cols={'terminal1': 'RG60'})


def test_add_phase_children_creates_correct_phase_kinds_and_sequence(network):
    line = cim.ACLineSegment(mRID='650632')
    graph_write.add_to_graph(network, line)
    terminals = connectivity.add_connectivity(
        network, line, node_cols={'node1': 'RG60', 'node2': '362'}
    )

    children = connectivity.add_phase_children(
        network, line, terminals, phases='ABCN'
    )

    assert len(children) == 4
    assert [c.phase for c in children] == [
        cim.SinglePhaseKind.A, cim.SinglePhaseKind.B,
        cim.SinglePhaseKind.C, cim.SinglePhaseKind.N,
    ]
    assert [c.sequenceNumber for c in children] == [1, 2, 3, 4]
    assert all(c.ACLineSegment is line for c in children)


def test_add_phase_children_resolves_parent_field_name_not_equal_to_class_name(network):
    shunt = cim.LinearShuntCompensator(mRID='cap1')
    graph_write.add_to_graph(network, shunt)

    terminals = connectivity.add_connectivity(network, shunt, node_cols={'node': '675'})

    children = connectivity.add_phase_children(network, shunt, terminals, phases='BC')

    assert all(type(c) is cim.LinearShuntCompensatorPhase for c in children)
    assert all(c.ShuntCompensator is shunt for c in children)


def test_add_phase_children_keeps_written_order_and_sets_canonical_terminal_phases(network):
    line = cim.ACLineSegment(mRID='632645')
    graph_write.add_to_graph(network, line)
    terminals = connectivity.add_connectivity(network, line, node_cols={'node1': '632', 'node2': '645'})

    children = connectivity.add_phase_children(network, line, terminals, phases='CB')

    assert [(c.phase, c.sequenceNumber) for c in children] == [
        (cim.SinglePhaseKind.C, 1), (cim.SinglePhaseKind.B, 2),
    ]
    assert [t.phases for t in terminals] == [cim.PhaseCode.BC, cim.PhaseCode.BC]


def test_add_phase_children_splits_secondary_tokens(network):
    load = cim.EnergyConsumer(mRID='ld_house')
    graph_write.add_to_graph(network, load)
    terminals = connectivity.add_connectivity(network, load, node_cols={'node': 'house'})

    children = connectivity.add_phase_children(network, load, terminals, phases='s1s2')

    assert [c.phase for c in children] == [cim.SinglePhaseKind.s1, cim.SinglePhaseKind.s2]
    assert terminals[0].phases == cim.PhaseCode.s12


def test_add_phase_children_uses_nearest_base_phase_class(network):
    breaker = cim.Breaker(mRID='brkr1')
    graph_write.add_to_graph(network, breaker)
    terminals = connectivity.add_connectivity(network, breaker, node_cols={'node1': '650', 'node2': 'brkr'})

    children = connectivity.add_phase_children(network, breaker, terminals, phases='ABC')

    assert all(type(c) is cim.SwitchPhase and c.Switch is breaker for c in children)
    assert [(c.phaseSide1, c.phaseSide2) for c in children][0] == (cim.SinglePhaseKind.A, cim.SinglePhaseKind.A)


@pytest.mark.parametrize('phases', ['ABX', 'AA', 'abc'])
def test_bad_phases_string_raises(phases):
    with pytest.raises(ValueError, match='ordered set'):
        connectivity.parse_phases(phases)


def test_add_phase_children_without_terminals_raises(network):
    load = cim.EnergyConsumer(mRID='floating')
    with pytest.raises(ValueError, match='needs a node/bus column'):
        connectivity.add_phase_children(network, load, [], phases='A')


def test_transformer_end_node_puts_terminal_on_power_transformer(network):
    xfmr = cim.PowerTransformer(mRID='xfm1')
    end = cim.PowerTransformerEnd(mRID='xfm1_2', endNumber=2)
    graph_write.set_assc(end, cim, 'PowerTransformer', xfmr)

    [terminal] = connectivity.add_connectivity(network, end, node_cols={'node': '634'})
    connectivity.add_phase_children(network, end, [terminal], phases='ABCN')

    assert terminal.ConductingEquipment is xfmr
    assert end.Terminal is terminal
    assert terminal.sequenceNumber == 2
    assert terminal.ConnectivityNode.mRID == '634'
    assert terminal.phases == cim.PhaseCode.ABCN
