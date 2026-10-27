"""bind_row behavior that needs cim18v17 rather than cimhub_2026: the
`phases` PhaseCode extension on shunt phase children (delta branches) and a
TransformerEnd's node column. The profile is set per test via monkeypatch so
the cimhub_2026 modules elsewhere in the suite are unaffected."""

import importlib
import tempfile

import pytest
from cimgraph.core.env_vars import get_cim_profile
from cimgraph.databases import XMLFile
from cimgraph.models import FeederModel

from cimtables import graph_write, parser, validate
from cimtables.binder import bind_row
from cimtables.name_index import NameIndex


@pytest.fixture
def cim(monkeypatch):
    monkeypatch.setenv('CIMG_CIM_PROFILE', 'cim18v17')
    get_cim_profile.cache_clear()
    yield importlib.import_module('cimprofiles.cim18.cim18v17')
    get_cim_profile.cache_clear()


@pytest.fixture
def network(cim):
    net = FeederModel(connection=XMLFile(filename=tempfile.mktemp(suffix='.xml')),
                      container=cim.Feeder(name='test_feeder'), distributed=False)
    net.name_index = NameIndex()
    return net


def _bind_text(network, text):
    records, _ = parser.parse_text(text, source_file='<test>')
    return [bind_row(network, validate.validate(r)) for r in records]


def test_delta_load_children_are_line_to_line_branches(network, cim):
    _bind_text(network, (
        'Table EnergyConsumer: name, node, phases, phaseConnection\n'
        '    646, 646, BC,  D\n'
        '    692, 692, CA,  D\n'
        '    671, 671, ABC, D\n'
        '    645, 645, B,   Y\n'
    ))
    branches = {
        name: [c.phases.value if c.phases else c.phase.value
               for c in graph_write.resolve(network, cim.EnergyConsumer, name).EnergyConsumerPhase]
        for name in ('646', '692', '671', '645')
    }
    assert branches == {'646': ['BC'], '692': ['AC'], '671': ['AB', 'BC', 'AC'], '645': ['B']}
    assert graph_write.resolve(network, cim.EnergyConsumer, '692').Terminals[0].phases == cim.PhaseCode.AC


def test_transformer_end_node_and_phases_go_on_power_transformer_terminal(network, cim):
    _bind_text(network, (
        'Object PowerTransformer: name=tpoletop\n'
        'Table PowerTransformerEnd: name, node, phases, endNumber, PowerTransformer\n'
        '    tpoletop_1, 670,   B,     1, tpoletop\n'
        '    tpoletop_2, house, s1s2N, 2, tpoletop\n'
    ))
    xfmr = graph_write.resolve(network, cim.PowerTransformer, 'tpoletop')
    assert [(t.name, t.sequenceNumber, t.phases.value, t.ConnectivityNode.name) for t in xfmr.Terminals] == [
        ('tpoletop_T1', 1, 'B', '670'), ('tpoletop_T2', 2, 's12N', 'house'),
    ]
    assert graph_write.resolve(network, cim.PowerTransformerEnd, 'tpoletop_2').Terminal is xfmr.Terminals[1]


def test_transformer_end_node_needs_its_power_transformer(network):
    with pytest.raises(ValueError, match='needs its PowerTransformer'):
        _bind_text(network, (
            'Table PowerTransformerEnd: name, node, endNumber\n'
            '    orphan_1, 670, 1\n'
        ))


def test_unnamed_rows_do_not_collide(network, cim):
    bound = _bind_text(network, (
        'Object PerLengthPhaseImpedance: name=mtx605, conductorCount=1\n'
        'Table PhaseImpedanceData: PhaseImpedance, row, column, r (ohm/mile)\n'
        '    mtx605, 1, 1, 1.3292\n'
        '    mtx605, 2, 1, 0.2066\n'
    ))
    assert len(graph_write.resolve(network, cim.PerLengthPhaseImpedance, 'mtx605').PhaseImpedanceData) == 2
    assert len(bound) == 3


def test_bus_column_wires_terminal_to_topological_node(network, cim):
    _bind_text(network, (
        'Object TopologicalNode: name=feeder_head_bus\n'
        'Object EnergyConsumer: name=aggregate_load, bus=feeder_head_bus, p (kW)=3000\n'
    ))
    [terminal] = graph_write.resolve(network, cim.EnergyConsumer, 'aggregate_load').Terminals
    assert terminal.TopologicalNode is graph_write.resolve(network, cim.TopologicalNode, 'feeder_head_bus')


def test_transformer_end_bus_column_wires_topological_node(network, cim):
    _bind_text(network, (
        'Object PowerTransformer: name=sub3\n'
        'Object PowerTransformerEnd: name=sub3_2, bus=650, endNumber=2, PowerTransformer=sub3\n'
    ))
    terminal = graph_write.resolve(network, cim.PowerTransformerEnd, 'sub3_2').Terminal
    assert terminal.TopologicalNode is graph_write.resolve(network, cim.TopologicalNode, '650')
