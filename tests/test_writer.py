"""writer.to_documents: a model -> .cimtbl documents that load back into an
equal model (diff_models), Terminals folded into node/phases columns or
explicit, references ordered (Update rows where they can't be), units
chosen per column or configured, and parts split by class."""

import json
import os
from pathlib import Path

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import pytest
from cimgraph.core import get_model_authority, get_model_name

import cimtables
from cimtables import document, writer
from cimtables.diff import diff_models

_FEEDER = '''Profile = cim18v17
Model = writer_test

Object BasePower: mRID=bp, basePower (MVA)=100
Object Substation: mRID=sub
Object Feeder: mRID=fdr, NormalEnergizingSubstation=sub
Table BaseVoltage: mRID, nominalVoltage (kV)
    bv_12,  12.47
    bv_480, 0.48
Object EnergySource: mRID=src, node=sourcebus, BaseVoltage=bv_12, EquipmentContainer=fdr, x (pu system)=0.1
Object PerLengthPhaseImpedance: mRID=mtx, conductorCount=2
Table PhaseImpedanceData: identifier, PhaseImpedance, row, column, r (ohm/mile), x (ohm/mile)
    mtx_1_1, mtx, 1, 1, 0.3, 1.2
    mtx_2_2, mtx, 2, 2, 0.3, 1.2
Table ACLineSegment: mRID, node1, node2, phases, length (ft), PerLengthImpedance, BaseVoltage, EquipmentContainer
    ln_1, sourcebus, n1, CB,   500, mtx, bv_12, fdr
    ln_2, n1,        n2, ,     300, ,    bv_12, fdr
    ln_3, n2,        n3, ABCN, 300, ,    bv_12, fdr
Table EnergyConsumer: mRID, node, phases, phaseConnection, p (kW), q (kvar), EquipmentContainer
    ld_ca,  n2, CA, D, 100, 50,  fdr
    ld_abc, n2, ,   D, 300, 100, fdr
    ld_a,   n3, A,  Y, 10,  5,   fdr
Object LoadBreakSwitch: mRID=sw_1, node1=n1, node2=n4, phaseSide1=AB, phaseSide2=AC, EquipmentContainer=fdr
Object PowerTransformer: mRID=xf, EquipmentContainer=fdr
Table PowerTransformerEnd: mRID, PowerTransformer, endNumber, node, phases, ratedU (kV), ratedS (kVA), BaseVoltage
    xf_1, xf, 1, n3, ABC,  12.47, 500, bv_12
    xf_2, xf, 2, lv, ABCN, 0.48,  500, bv_480
Object TransformerMeshImpedance: mRID=xf_mesh, FromTransformerEnd=xf_1, ToTransformerEnd=xf_2, x (pu)=0.05
Object LinearShuntCompensator: mRID=cap, node=n3, phases=ABC, EquipmentContainer=fdr, bPerSection (S)=0.001
Object RegulatingControl: mRID=rc, Terminal=cap_T1
Update LinearShuntCompensator: mRID=cap, RegulatingControl=rc
'''

_GOLDEN = Path('/home/ande188/CIMHub/cimhub_opendss/tests/goldens/IEEE13_CDPSM/IEEE13_CDPSM.cimtbl')


@pytest.fixture(autouse=True)
def model_scope(monkeypatch):
    """load_model sets the process-global model scope; restore it afterwards."""
    monkeypatch.setenv('CIMG_MODEL_AUTHORITY', get_model_authority())
    monkeypatch.setenv('CIMG_MODEL_NAME', get_model_name())
    yield
    monkeypatch.undo()
    get_model_authority.cache_clear()
    get_model_name.cache_clear()


def _load(tmp_path, text):
    path = tmp_path / 'source.cimtbl'
    path.write_text(text)
    return cimtables.load_model(path)


def _reload(tmp_path, documents):
    out = tmp_path / 'written'
    out.mkdir()
    return cimtables.load_model(document.write_documents(documents, out))


@pytest.mark.parametrize('terminals', ['folded', 'explicit'])
def test_a_written_model_loads_back_equal(tmp_path, terminals):
    original = _load(tmp_path, _FEEDER)
    assert diff_models(original, _reload(tmp_path, writer.to_documents(original, 'feeder', terminals=terminals))) == []


def test_folded_writes_the_connectivity_and_phases_shorthand(tmp_path):
    text = document.format_document(writer.to_documents(_load(tmp_path, _FEEDER), 'feeder').master)
    assert 'Table Terminal' not in text and 'Phase:' not in text
    assert 'ln_1, sourcebus, n1, CB,' in text  # written conductor order kept
    assert 'ln_2, n1,        n2, ,' in text  # balanced: no phases
    assert 'ld_ca,  n2, CA,' in text and 'ld_abc, n2, ,' in text  # delta on CA; balanced delta
    assert 'node1=n1, node2=n4, phaseSide1=AB, phaseSide2=AC' in text
    assert 'xf_2, lv, ABCN, 2' in text  # an end's node and phases


def test_explicit_writes_every_terminal_and_phase_child(tmp_path):
    text = document.format_document(writer.to_documents(_load(tmp_path, _FEEDER), 'feeder', terminals='explicit').master)
    assert 'Table Terminal: mRID, phases, sequenceNumber, ConductingEquipment, ConnectivityNode' in text
    assert 'Table ACLineSegmentPhase:' in text and 'Table SwitchPhase:' in text
    assert 'node1' not in text


def test_a_reference_cycle_becomes_an_update(tmp_path):
    text = document.format_document(writer.to_documents(_load(tmp_path, _FEEDER), 'feeder').master)
    assert text.rstrip().endswith('Update LinearShuntCompensator: mRID=cap, RegulatingControl=rc')
    assert 'Object RegulatingControl: mRID=rc, Terminal=cap_T1' in text


_ODD_TERMINAL = '''Profile = cim18v17
Object BaseVoltage: mRID=bv_12, nominalVoltage (kV)=12.47
Object ConnectivityNode: mRID=n1
Object EnergySource: mRID=src, BaseVoltage=bv_12
Object Terminal: mRID=t_odd, ConductingEquipment=src, ConnectivityNode=n1, sequenceNumber=1, name=source terminal
Object RegulatingControl: mRID=rc, Terminal=t_odd
'''


def test_explicit_keeps_terminal_mrids(tmp_path):
    original = _load(tmp_path, _ODD_TERMINAL)
    assert diff_models(original, _reload(tmp_path, writer.to_documents(original, 'm', terminals='explicit'))) == []


def test_folded_renames_terminals_and_their_references(tmp_path):
    original = _load(tmp_path, _ODD_TERMINAL)
    documents = writer.to_documents(original, 'm')
    text = document.format_document(documents.master)
    assert 'Object EnergySource: mRID=src, node=n1, BaseVoltage=bv_12' in text
    assert 'Update Terminal: mRID=src_T1, name=source terminal' in text  # what binding doesn't set
    reloaded = _reload(tmp_path, documents)
    assert reloaded.find_by_mRID('rc').Terminal is reloaded.find_by_mRID('src_T1')
    assert reloaded.find_by_mRID('src_T1').name == 'source terminal'
    assert diff_models(original, reloaded) == [
        "RegulatingControl rc.Terminal: expected 't_odd', got 'src_T1'",
        'Terminal src_T1: extra',
        'Terminal t_odd: missing',
    ]


def test_units_are_chosen_per_column(tmp_path):
    text = document.format_document(writer.to_documents(_load(tmp_path, _FEEDER), 'feeder').master)
    assert 'Table BaseVoltage: mRID, nominalVoltage (kV)\n    bv_12,  12.47\n    bv_480, 0.48\n' in text
    assert 'p (kW), q (kvar)' in text
    assert 'length,' in text  # 152.4 m has fewer digits than 0.1524 km


def test_units_on_a_tie_stay_si(tmp_path):
    model = _load(tmp_path, 'Profile = cim18v17\nTable BaseVoltage: mRID, nominalVoltage (V)\n    bv_120, 120\n    bv_240, 240\n')
    assert 'Table BaseVoltage: mRID, nominalVoltage\n' in document.format_document(writer.to_documents(model, 'm').master)


def test_configured_units_round_trip(tmp_path):
    units = {'ACLineSegment.length': 'ft', 'TransformerEnd.ratedS': 'kVA', 'TransformerMeshImpedance.x': 'percent',
             'EnergySource.x': 'pu system', 'PhaseImpedanceData.r': 'ohm/mile'}
    original = _load(tmp_path, _FEEDER)
    documents = writer.to_documents(original, 'feeder', units=units)
    text = document.format_document(documents.master)
    assert 'length (ft)' in text and 'ratedS (kVA)' in text and 'r (ohm/mile)' in text
    assert 'x (percent)=5' in text and 'x (pu system)=0.1' in text
    assert diff_models(original, _reload(tmp_path, documents)) == []

    config = tmp_path / 'units.json'
    config.write_text(json.dumps(units))
    assert document.format_document(writer.to_documents(original, 'feeder', units=config).master) == text


def test_parts_are_imported_in_order(tmp_path):
    original = _load(tmp_path, _FEEDER)
    documents = writer.to_documents(original, 'feeder', parts={'catalog': ['BaseVoltage', 'PerLengthPhaseImpedance', 'PhaseImpedanceData']})
    assert [part.name for part in documents.parts] == ['catalog']
    assert document.Import('catalog') in documents.master.statements
    assert diff_models(original, _reload(tmp_path, documents)) == []


def test_parts_that_reference_each_other_raise(tmp_path):
    original = _load(tmp_path, _FEEDER)
    with pytest.raises(ValueError, match=r"parts \['controls', 'shunts'\] would import each other"):
        writer.to_documents(original, 'feeder', parts={'shunts': ['LinearShuntCompensator'], 'controls': ['RegulatingControl']})
    with pytest.raises(ValueError, match="is in parts 'a' and 'b'"):
        writer.to_documents(original, 'feeder', parts={'a': ['BaseVoltage'], 'b': ['BaseVoltage']})


def test_output_is_deterministic(tmp_path):
    original = _load(tmp_path, _FEEDER)
    first, second = (document.format_document(writer.to_documents(original, 'feeder').master) for _ in range(2))
    assert first == second


def test_unknown_terminal_mode_raises(tmp_path):
    with pytest.raises(ValueError, match="'folded' or 'explicit'"):
        writer.to_documents(_load(tmp_path, _FEEDER), 'feeder', terminals='inline')


@pytest.mark.skipif(not _GOLDEN.exists(), reason='needs the CIMHub checkout')
@pytest.mark.parametrize('terminals', ['folded', 'explicit'])
def test_cimhub_ieee13_round_trips(tmp_path, terminals):
    original = cimtables.load_model(_GOLDEN)
    assert diff_models(original, _reload(tmp_path, writer.to_documents(original, 'ieee13', terminals=terminals))) == []
