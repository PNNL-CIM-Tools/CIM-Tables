"""cimtables.document: Document -> .cimtbl text -> the records it describes
(CIMHub's CIMTABLES_REQUESTS R1)."""

import os
import random
import tempfile

import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

import cimprofiles.cim18.cim18v17 as cim
from cimgraph.databases import XMLFile
from cimgraph.models import BusBranchModel

import cimtables
from cimtables import graph_write, parser
from cimtables.document import (
    Column, Document, DocumentSet, Import, ObjectStatement, Row, Table, format_document, write_documents,
)

_TRICKY = ['plain', '', ' lead', 'trail ', 'a,b', 'x#y', 'DEFAULT (DC)', 'say "hi"', '"', 'a=b',
           'Table X: y', 'Object Y:z', 'Profile = p', 'Import parts', 'Model=3', 'Objects', 'Tablet', '1.5e-3']


def _expected_records(table: Table) -> list[tuple[dict, dict]]:
    """(fields, units) per row as the parser must read them back: row cells
    fill the undefaulted columns, extra cells override defaults in order."""
    required = [c for c in table.columns if c.default is None]
    defaulted = [c for c in table.columns if c.default is not None]
    expected = []
    for row in table.rows:
        values = dict(zip([c.name for c in required + defaulted], row.cells))
        fields = {c.name: values.get(c.name, c.default) for c in table.columns}
        expected.append((fields, {c.name: c.unit for c in table.columns}))
    return expected


def test_tricky_cells_round_trip():
    table = Table('IdentifiedObject', [Column('mRID'), Column('name'), Column('description')], [
        Row([f'id_{i}', text, None if i % 3 else text]) for i, text in enumerate(_TRICKY)
    ])
    records, _ = parser.parse_text(format_document(Document('t', [table])))
    assert [(r.fields, r.units) for r in records] == _expected_records(table)


def test_random_tables_round_trip():
    alphabet = ['a', 'Z', '0', ' ', '\t', ',', '#', '(', ')', '"', '=', ':', '.', '/', 'Table ', 'Object X:', 'Profile=']
    rng = random.Random(0)
    for _ in range(50):
        columns = [Column('mRID'), Column('name', default=rng.choice([None, 'dflt', '', 'a, b']))]
        columns += [Column(f'c{i}', unit=rng.choice([None, 'kV', 'pu system'])) for i in range(rng.randint(0, 3))]
        required = sum(c.default is None for c in columns)
        rows = []
        for i in range(rng.randint(1, 5)):
            cells = [f'r{i}'] + [rng.choice([None, ''.join(rng.choices(alphabet, k=rng.randint(0, 6)))])
                                 for _ in range(rng.randint(required, len(columns)) - 1)]
            rows.append(Row(cells, comment=rng.choice([None, 'between\nrows'])))
        table = Table('IdentifiedObject', columns, rows, comment=rng.choice([None, 'above']))
        records, _ = parser.parse_text(format_document(Document('t', [table])))
        assert [(r.fields, r.units) for r in records] == _expected_records(table)


def test_layout_is_padded_and_deterministic():
    document = Document('catalog', [
        ObjectStatement('BasePower', [(Column('mRID'), 'sbase'), (Column('basePower', 'MVA'), '100')]),
        Table('BaseVoltage', [Column('mRID'), Column('nominalVoltage', 'kV'), Column('name', default='bv')], [
            Row(['bv_115kv', '115']),
            Row(['bv_4p16', '4.16', 'four'], comment='distribution\nprimary'),
            Row(['bv_x', None]),
        ], comment='voltages'),
        Import('wires'),
    ], profile='cim18v17', model='ieee13', comment='header')
    text = format_document(document)
    assert text == (
        '# header\n'
        '\n'
        'Profile = cim18v17\n'
        'Model = ieee13\n'
        '\n'
        'Object BasePower: mRID=sbase, basePower (MVA)=100\n'
        '\n'
        '# voltages\n'
        'Table BaseVoltage: mRID, nominalVoltage (kV), name=bv\n'
        '    bv_115kv, 115\n'
        '    # distribution\n'
        '    # primary\n'
        '    bv_4p16,  4.16, four\n'
        '    bv_x,\n'
        '\n'
        'Import wires.cimtbl\n'
    )
    assert format_document(document) == text


def test_object_statement_round_trips():
    statement = ObjectStatement('ACLineSegment', [
        (Column('mRID'), '"#2_ACSR"'), (Column('x', 'pu system'), '0.05'), (Column('name'), ''), (Column('description'), None),
    ])
    [record], header = parser.parse_text(format_document(Document('o', [statement], profile='cim18v17')))
    assert header.profile == 'cim18v17'
    assert record.fields == {'mRID': '"#2_ACSR"', 'x': '0.05', 'name': '', 'description': None}
    assert record.units == {'mRID': None, 'x': 'pu system', 'name': None, 'description': None}


@pytest.mark.parametrize('cells', [['only_mrid'], ['a', 'b', 'c', 'd']])
def test_row_with_wrong_cell_count_raises(cells):
    table = Table('BaseVoltage', [Column('mRID'), Column('nominalVoltage'), Column('name', default='x')], [Row(cells)])
    with pytest.raises(ValueError, match='BaseVoltage row 1 has'):
        format_document(Document('t', [table]))


def test_newline_in_a_cell_raises():
    with pytest.raises(ValueError, match='newline'):
        format_document(Document('t', [Table('BaseVoltage', [Column('mRID')], [Row(['a\nb'])])]))


def test_all_blank_row_raises():
    with pytest.raises(ValueError, match='all blank'):
        format_document(Document('t', [Table('BaseVoltage', [Column('mRID')], [Row([None])])]))


def _master_and_part():
    part = Document('voltages', [Table('BaseVoltage', [Column('mRID'), Column('nominalVoltage', 'kV')], [Row(['bv_1', '4.16'])])],
                    profile='cim18v17')
    master = Document('master', [Import('voltages')], profile='cim18v17')
    return master, part


def test_write_documents_writes_master_and_parts(tmp_path):
    master, part = _master_and_part()
    path = write_documents(DocumentSet(master, [part]), tmp_path / 'out')
    assert path == tmp_path / 'out' / 'master.cimtbl'
    records, _ = parser.parse_file(path)
    assert [r.fields['mRID'] for r in records] == ['bv_1']


def test_write_documents_twice_overwrites_its_own_files(tmp_path):
    master, part = _master_and_part()
    write_documents(DocumentSet(master, [part]), tmp_path)
    write_documents(DocumentSet(master, [part]), tmp_path)


def test_write_documents_rejects_an_unknown_import(tmp_path):
    master, part = _master_and_part()
    master.statements.append(Import('missing'))
    with pytest.raises(ValueError, match="imports 'missing'"):
        write_documents(DocumentSet(master, [part]), tmp_path)


def test_write_documents_rejects_a_part_never_imported(tmp_path):
    master, part = _master_and_part()
    with pytest.raises(ValueError, match=r"never imported: \['orphan'\]"):
        write_documents(DocumentSet(master, [part, Document('orphan', [])]), tmp_path)


def test_write_documents_rejects_a_stale_file_and_writes_nothing(tmp_path):
    (tmp_path / 'old_part.cimtbl').write_text('')
    master, part = _master_and_part()
    with pytest.raises(ValueError, match=r"not in the document set: \['old_part.cimtbl'\]"):
        write_documents(DocumentSet(master, [part]), tmp_path)
    assert not (tmp_path / 'master.cimtbl').exists()


def test_xfm1_xfo_catalog_loads():
    # CIMHub's acceptance snapshot (CIMTBL_REWRITE_PLAN.md §3.2)
    document = Document('transformer_db', [
        Table('TransformerTankConfiguration', [
            Column('mRID'), Column('name'), Column('description'), Column('percentImpedance'),
            Column('percentMagnetizingCurrent'), Column('noLoadLoss', 'kW'),
        ], [Row(['xc_XFM1_XFO', 'XFM1_XFO', 'XFM1_XFO', '2.2825', '0.0', '0.0'])]),
        Table('TransformerTankEnd', [
            Column('mRID'), Column('endNumber'), Column('phases'), Column('connectionKind'), Column('grounded'),
            Column('rground', 'ohm'), Column('xground', 'ohm'), Column('TankConfiguration'),
        ], [Row([f'xc_XFM1_XFO_end_{n}', str(n), 'ABCN', 'Yn', 'true', '0.0', '0.0', 'xc_XFM1_XFO']) for n in (1, 2)],
            comment='TransformerConnection Yg_Yg -> one end per side; TransfoType ThreePhase + Yg -> ABCN'),
        Table('TransformerWinding', [
            Column('mRID'), Column('ratedU', 'kV'), Column('baseU', 'kV'), Column('ratedS', 'kVA'), Column('baseS', 'kVA'),
            Column('FromTransformerEnd'), Column('TankConfiguration'),
        ], [Row([f'xc_XFM1_XFO_wdg_{n}', kv, kv, '500.00102', '500.00102', f'xc_XFM1_XFO_end_{n}', 'xc_XFM1_XFO'])
            for n, kv in ((1, '4.16'), (2, '0.48'))]),
        Table('TransformerCore', [Column('mRID'), Column('form'), Column('TankConfiguration')],
              [Row(['xc_XFM1_XFO_core', 'shellForm', 'xc_XFM1_XFO'])]),
        Table('TransformerMeshImpedance', [
            Column('mRID'), Column('r', 'percent'), Column('x', 'percent'), Column('r0', 'percent'), Column('x0', 'percent'),
            Column('FromWinding'), Column('ToWinding'),
        ], [Row(['xc_XFM1_XFO_mesh_12', '1.100017550601472', '1.9999419087485364', '1.100017550601472',
                 '1.9999419087485364', 'xc_XFM1_XFO_wdg_1', 'xc_XFM1_XFO_wdg_2'])],
            comment='derived (CIM has no X/R): r = Z / sqrt(1 + XR^2), x = r * XR'),
    ], profile='cim18v17', comment='CYME TransformerDB -> TransformerTankConfiguration (catalog)')
    network = BusBranchModel(connection=XMLFile(filename=tempfile.mktemp(suffix='.xml')),
                             container=cim.Feeder(mRID='f'), distributed=False)
    bound = cimtables.load_text(format_document(document), network)
    assert len(bound) == 7
    mesh = graph_write.resolve(network, cim.TransformerMeshImpedance, 'xc_XFM1_XFO_mesh_12')
    assert mesh.r.to('ohm') == pytest.approx(0.38073, abs=1e-5)


def test_write_documents_writes_utf8_with_lf_line_endings(tmp_path):
    master = Document('master', [Table('BaseVoltage', [Column('mRID'), Column('name')], [Row(['bv_1', 'Zürich']), Row(['bv_2', 'b'])])])
    path = write_documents(DocumentSet(master, []), tmp_path)
    assert path.read_bytes() == format_document(master).encode('utf-8')
    assert b'\r' not in path.read_bytes()
