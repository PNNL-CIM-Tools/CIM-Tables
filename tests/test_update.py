"""`Update` statements: CIM's additive definition (a Description class), more
attributes on an object an Object/Table row defined earlier (CIMHub R11)."""

import os

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.models import NodeBreakerModel

import cimtables
from cimtables import graph_write, parser
from cimtables.document import Column, Document, Row, Table, format_document

_CAP = 'Object LinearShuntCompensator: mRID=cap_c1, node=n1\n'


def _network():
    return NodeBreakerModel(container=None, connection=None, distributed=False)


def test_a_control_monitors_its_own_compensators_terminal(tmp_path):
    # R11: the compensator names its control, the control its Terminal; neither
    # can come first, so the compensator's reference is added by an Update
    (tmp_path / 'connectivity.cimtbl').write_text('Profile = cim18v17\n' + _CAP)
    (tmp_path / 'controls.cimtbl').write_text(
        'Profile = cim18v17\n'
        'Object RegulatingControl: mRID=rc_c1, Terminal=cap_c1_T1\n'
        'Update LinearShuntCompensator: mRID=cap_c1, RegulatingControl=rc_c1\n'
    )
    (tmp_path / 'master.cimtbl').write_text('Profile = cim18v17\nImport connectivity.cimtbl\nImport controls.cimtbl\n')
    network = cimtables.load_model(tmp_path / 'master.cimtbl')
    cap, control = network.find_by_mRID('cap_c1'), network.find_by_mRID('rc_c1')
    assert cap.RegulatingControl is control
    assert cap in control.RegulatingCondEq
    assert control.Terminal is network.find_by_mRID('cap_c1_T1')
    assert len(network.graph[cim.LinearShuntCompensator]) == 1


def test_update_table_adds_to_many_objects():
    network = _network()
    cimtables.load_text(
        'Object LoadResponseCharacteristic: mRID=zip_pq, exponentModel=false\n'
        'Table EnergyConsumer: mRID, p (kW)\n'
        '    ld_1, 10\n'
        '    ld_2, 20\n'
        'Update Table EnergyConsumer: mRID, LoadResponse, q (kVAr)\n'
        '    ld_1, zip_pq, 1\n'
        '    ld_2, zip_pq, 2\n', network)
    loads = [network.find_by_mRID(m) for m in ('ld_1', 'ld_2')]
    assert [ld.q.to('VAr') for ld in loads] == [1000, 2000]
    assert all(ld.LoadResponse is network.find_by_mRID('zip_pq') for ld in loads)
    assert network.find_by_mRID('zip_pq').EnergyConsumer == loads


def test_update_converts_pu_on_the_existing_rating():
    network = _network()
    cimtables.load_text(
        'Object PowerTransformerEnd: mRID=end_1, ratedU (kV)=115, ratedS (MVA)=50\n'
        'Update PowerTransformerEnd: mRID=end_1, x (pu)=0.1\n', network)
    assert network.find_by_mRID('end_1').x.to('ohm') == pytest.approx(0.1 * 115e3 ** 2 / 50e6)


@pytest.mark.parametrize('text, match', [
    ('Update EnergyConsumer: mRID=ld_9, p (kW)=1\n', r"<text>:\d+: Update EnergyConsumer 'ld_9': no such object"),
    ('Update EnergyConsumer: mRID=ld_1, p (kW)=1\n', r"<text>:\d+: Update 'ld_1': p is already set"),
    ('Update EnergyConsumer: mRID=ld_1, node=n2\n', r"'node' is connectivity shorthand"),
    ('Update EnergyConsumer: mRID=ld_1, phases=AB\n', r"'phases' is connectivity shorthand"),
    ('Update BaseVoltage: mRID=ld_1, nominalVoltage (kV)=4.16\n', r"mRID 'ld_1' is a EnergyConsumer, not a BaseVoltage"),
])
def test_update_only_adds_to_an_existing_object(text, match):
    network = _network()
    cimtables.load_text('Object EnergyConsumer: mRID=ld_1, p (kW)=10\n', network)
    with pytest.raises(ValueError, match=match):
        cimtables.load_text(text, network)


def test_update_defaults_no_container():
    network = _network()
    cimtables.load_text('Object EnergyConsumer: mRID=ld_1\n', network)
    network.container = cim.Feeder(mRID='fdr')
    cimtables.load_text('Update EnergyConsumer: mRID=ld_1, p (kW)=10\n', network)
    assert network.find_by_mRID('ld_1').EquipmentContainer is None


def test_update_statements_parse_and_format():
    records, _ = parser.parse_text(
        'Update LinearShuntCompensator: mRID=cap_c1, RegulatingControl=rc_c1\n'
        'Update Table EnergyConsumer: mRID, LoadResponse\n'
        '    ld_1, zip\n'
        'Table IdentifiedObject: mRID, name\n'
        '    Update, Update Table X\n')
    assert [(r.form, r.cim_class) for r in records] == [
        ('Update', 'LinearShuntCompensator'), ('Update', 'EnergyConsumer'), ('Table', 'IdentifiedObject')]
    assert records[2].fields == {'mRID': 'Update', 'name': 'Update Table X'}

    text = format_document(Document('d', [
        Table('EnergyConsumer', [Column('mRID'), Column('LoadResponse')], [Row(['ld_1', 'zip'])], update=True),
        Table('IdentifiedObject', [Column('mRID')], [Row(['Update EnergyConsumer: x'])]),
    ]))
    assert text.startswith('Update Table EnergyConsumer: mRID, LoadResponse\n')
    records, _ = parser.parse_text(text)
    assert [r.form for r in records] == ['Update', 'Table']
    assert records[1].fields['mRID'] == 'Update EnergyConsumer: x'
