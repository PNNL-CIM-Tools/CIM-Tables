"""load_model: a .cimtbl master file -> an in-memory GraphModel, with the file's
own Feeder or Substation as its container and no made-up container leaking in,
and its UUIDs seeded under the file's Authority=/Model= scope."""

import os

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import pytest
from cimgraph.core import get_model_authority, get_model_name, set_model_scope
from cimgraph.databases import XMLFile
from cimgraph.models import FeederModel, NodeBreakerModel
from cimgraph.utils import write_xml

import cimtables


def _write(tmp_path, text):
    path = tmp_path / 'model.cimtbl'
    path.write_text(text)
    return path


def test_feeder_file_gives_a_feeder_model(tmp_path):
    network = cimtables.load_model(_write(tmp_path, (
        'Object Substation: mRID=sub_a\n'
        'Object Feeder: mRID=fdr_a, NormalEnergizingSubstation=sub_a\n'
        'Object BaseVoltage: mRID=bv_12, nominalVoltage (kV)=12.47\n'
        'Object EnergySource: mRID=src, node1=bus_a, BaseVoltage=bv_12, EquipmentContainer=fdr_a\n'
    )))
    assert isinstance(network, FeederModel)
    assert network.connection is None
    assert network.container is network.find_by_mRID('fdr_a')
    assert network.find_by_mRID('src').EquipmentContainer is network.container


def test_substation_file_gives_a_node_breaker_model(tmp_path):
    network = cimtables.load_model(_write(tmp_path, (
        'Object Substation: mRID=sub_a\n'
        'Object BaseVoltage: mRID=bv_115, nominalVoltage (kV)=115\n'
        'Object EnergySource: mRID=src, node1=bus_a, BaseVoltage=bv_115, EquipmentContainer=sub_a\n'
    )))
    assert isinstance(network, NodeBreakerModel)
    assert network.container is network.find_by_mRID('sub_a')


def test_file_without_a_container_sets_no_equipment_container(tmp_path):
    network = cimtables.load_model(_write(tmp_path, (
        'Object BaseVoltage: mRID=bv_12, nominalVoltage (kV)=12.47\n'
        'Object EnergySource: mRID=src, node1=bus_a, BaseVoltage=bv_12\n'
    )))
    assert isinstance(network, NodeBreakerModel)
    assert network.container is None
    source = network.find_by_mRID('src')
    assert source.EquipmentContainer is None
    assert network.find_by_mRID('bus_a').ConnectivityNodeContainer is None


@pytest.fixture
def model_scope(monkeypatch):
    """load_model sets the process-global model scope; restore it afterwards."""
    monkeypatch.setenv('CIMG_MODEL_AUTHORITY', get_model_authority())
    monkeypatch.setenv('CIMG_MODEL_NAME', get_model_name())
    yield
    monkeypatch.undo()
    get_model_authority.cache_clear()
    get_model_name.cache_clear()


_SOURCE = ('Object BaseVoltage: mRID=bv_12, nominalVoltage (kV)=12.47\n'
           'Object EnergySource: mRID=src, node1=bus_a, BaseVoltage=bv_12\n')


def test_model_statement_scopes_the_uuids(tmp_path, model_scope):
    ieee13 = cimtables.load_model(_write(tmp_path, 'Model = ieee13\n' + _SOURCE))
    ieee123 = cimtables.load_model(_write(tmp_path, 'Model = ieee123\n' + _SOURCE))
    assert ieee13.find_by_mRID('src').identifier != ieee123.find_by_mRID('src').identifier
    assert get_model_name() == 'ieee123'


def test_model_scope_survives_an_xml_round_trip(tmp_path, model_scope):
    network = cimtables.load_model(_write(tmp_path, (
        'Authority = https://example.org/utility\n'
        'Model = ieee13\n' + _SOURCE)))
    out = tmp_path / 'out.xml'
    write_xml(network, str(out))
    set_model_scope('https://example.org/other', 'other')  # the reader restores it from the header

    read_back = FeederModel(connection=XMLFile(filename=str(out)), container=None, distributed=False)
    assert (get_model_authority(), get_model_name()) == ('https://example.org/utility', 'ieee13')
    assert read_back.find_by_mRID('src').identifier == network.find_by_mRID('src').identifier


def test_load_text_binds_a_snippet_onto_an_existing_network():
    network = NodeBreakerModel(container=None, connection=None, distributed=False)
    cimtables.load_text("""
Object Feeder: mRID=fdr_ieee13nodeckt
Table PowerElectronicsConnection: mRID
    storage_school
    storage_batidle
Table BatteryUnit: mRID, ratedE (kWh), storedE (kWh), batteryState, PowerElectronicsConnection, EquipmentContainer=fdr_ieee13nodeckt
    storage_school_unit,  200,  100, discharging, storage_school
    storage_batidle_unit, 200,  100, waiting,     storage_batidle
""", network)
    unit = network.find_by_mRID('storage_school_unit')
    assert unit.ratedE.to('kWh') == 200
    assert unit.PowerElectronicsConnection is network.find_by_mRID('storage_school')
    assert unit.EquipmentContainer is network.find_by_mRID('fdr_ieee13nodeckt')


def test_load_text_rejects_an_import():
    network = NodeBreakerModel(container=None, connection=None, distributed=False)
    with pytest.raises(ValueError, match=r"Import 'lines.cimtbl' needs a file"):
        cimtables.load_text('Import lines.cimtbl\n', network)
