"""Phase 1 grammar/parser tests (design: CIMTBL_DESIGN.md §8 exit criterion).

Covers every construct in isolation (Object, Table, Import, comments, blank
lines, unit-suffixed columns, blank cells) plus the real sample files
(ieee13.cimtbl, ieee14.cimtbl, wire_infos.cimtbl) end to end, including
through Phase 2's validate() so the whole pipe is exercised with a real
parser instead of hand-built RawRecords.
"""

import os
from pathlib import Path

import lark
import pytest

os.environ['CIMG_CIM_PROFILE'] = 'cim18v17'

from cimtables import parser, validate

SAMPLES_DIR = Path(__file__).parent / 'fixtures'


def _parse(text: str):
    records, header = parser.parse_text(text, source_file='<test>')
    return records, header


# --- isolated constructs ---------------------------------------------------

def test_object_form():
    records, _ = _parse('Object EnergySource: name=IEEE13nodeckt\n')
    assert len(records) == 1
    rec = records[0]
    assert rec.form == 'Object'
    assert rec.cim_class == 'EnergySource'
    assert rec.fields == {'name': 'IEEE13nodeckt'}


def test_object_form_with_unit_and_spaces_around_equals():
    records, _ = _parse('Object PowerTransformer: name = padmount_2341, Template = padmount_template\n')
    rec = records[0]
    assert rec.fields == {'name': 'padmount_2341', 'Template': 'padmount_template'}


def test_object_form_unit_suffix():
    records, _ = _parse('Object BasePower: name=sbase, basePower (MVA)=100\n')
    rec = records[0]
    assert rec.fields == {'name': 'sbase', 'basePower': '100'}
    assert rec.units['basePower'] == 'MVA'


def test_table_form_multi_row():
    records, _ = _parse(
        'Table BaseVoltage: name, nominalVoltage (V)\n'
        '    base_115, 115000\n'
        '    base_4160, 4160\n'
    )
    assert len(records) == 2
    assert records[0].fields == {'name': 'base_115', 'nominalVoltage': '115000'}
    assert records[0].units['nominalVoltage'] == 'V'
    assert records[1].fields == {'name': 'base_4160', 'nominalVoltage': '4160'}


def test_table_form_header_constant_applies_to_every_row():
    records, _ = _parse(
        'Table ACLineSegment: name, bus1, bus2, EquipmentContainer=ieee_13_debug\n'
        '    line_1, topo_1, topo_2\n'
        '    line_2, topo_3, topo_4\n'
    )
    assert len(records) == 2
    assert records[0].fields == {
        'name': 'line_1', 'bus1': 'topo_1', 'bus2': 'topo_2',
        'EquipmentContainer': 'ieee_13_debug',
    }
    assert records[1].fields == {
        'name': 'line_2', 'bus1': 'topo_3', 'bus2': 'topo_4',
        'EquipmentContainer': 'ieee_13_debug',
    }


def test_table_form_bare_columns_unaffected_by_header_constants():
    # Regression guard: a header with no `=` cells behaves exactly like
    # today (test_table_form_multi_row's shape), even after adding support
    # for defaulted columns elsewhere in the grammar.
    records, _ = _parse(
        'Table BaseVoltage: name, nominalVoltage (V)\n'
        '    base_115, 115000\n'
    )
    assert records[0].fields == {'name': 'base_115', 'nominalVoltage': '115000'}


def test_table_form_mixed_header_shapes_coexist():
    # plain column, unit-suffixed plain column, defaulted column - all three
    # header_cell shapes in one header_list.
    records, _ = _parse(
        'Table ACLineSegment: name, length (ft), EquipmentContainer=ieee_13_debug\n'
        '    line_1, 2000\n'
    )
    rec = records[0]
    assert rec.fields == {
        'name': 'line_1', 'length': '2000', 'EquipmentContainer': 'ieee_13_debug',
    }
    assert rec.units['length'] == 'ft'


def test_table_form_row_overrides_header_constant():
    # A row with an extra trailing cell beyond its plain columns supplies its
    # own value for a defaulted column - the row's value wins.
    records, _ = _parse(
        'Table EnergyConsumer: name, node, EquipmentContainer=ieee_13_debug\n'
        '    load_671, 671\n'
        '    load_sub, SourceBus, sub\n'
    )
    load_671 = next(r for r in records if r.fields['name'] == 'load_671')
    load_sub = next(r for r in records if r.fields['name'] == 'load_sub')
    assert load_671.fields['EquipmentContainer'] == 'ieee_13_debug'
    assert load_sub.fields['EquipmentContainer'] == 'sub'


def test_table_form_zero_rows():
    records, _ = _parse('Table SynchronousMachine: name, bus, p, q\n')
    assert records == []


def test_table_form_ragged_blank_cells():
    records, _ = _parse(
        'Table EnergyConsumer: name, node, p (kW), q (kVAr)\n'
        '    load_634, 634, , \n'
    )
    rec = records[0]
    assert rec.fields == {'name': 'load_634', 'node': '634', 'p': None, 'q': None}


def test_table_form_compound_unit_and_slash_in_name():
    records, _ = _parse(
        'Table OverheadWireInfo: name, radius (in), rAC25 (ohm/kft)\n'
        '    ACSR_4/0, 0.563, 0.35227\n'
    )
    rec = records[0]
    assert rec.fields['name'] == 'ACSR_4/0'
    assert rec.units['rAC25'] == 'ohm/kft'


def test_comment_lines_ignored():
    records, _ = _parse(
        '# a leading comment\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
        '# a trailing comment\n'
    )
    assert len(records) == 1
    assert records[0].cim_class == 'BaseFrequency'


def test_trailing_same_line_comment_ignored():
    records, _ = _parse(
        'Object BaseFrequency: name=base_freq, frequency=60  # trailing comment\n'
    )
    assert records[0].fields == {'name': 'base_freq', 'frequency': '60'}


def test_blank_lines_between_statements():
    records, _ = _parse(
        '\n\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
        '\n\n\n'
        'Object BasePower: name=sbase, basePower (MVA)=100\n'
        '\n'
    )
    assert [r.cim_class for r in records] == ['BaseFrequency', 'BasePower']


def test_whitespace_only_line_inside_table_is_noise():
    records, _ = _parse(
        'Table ACLineSegment: name, bus1, bus2\n'
        '    line_1_2, bus_1, bus_2\n'
        '    \n'
        '\n'
    )
    assert len(records) == 1
    assert records[0].fields['name'] == 'line_1_2'


def test_indented_comment_line_inside_table_is_noise():
    # regression: VALUE used to match the indentation before '#', turning an
    # indented comment line into an all-blank row
    records, _ = _parse(
        'Table PhaseImpedanceData: row, column, r\n'
        '    1, 1, 0.3465\n'
        '\n'
        '    # a group comment\n'
        '    2, 1, 0.1560\n'
    )
    assert [r.fields['row'] for r in records] == ['1', '2']


def test_scientific_notation_and_negative_numbers():
    records, _ = _parse(
        'Table BatteryUnit: name, minP, maxP\n'
        '    batt, -100, 100\n'
    )
    assert records[0].fields == {'name': 'batt', 'minP': '-100', 'maxP': '100'}

    records2, _ = _parse(
        'Table ACLineSegment: name, r (pu)\n'
        '    line_1_2, 1.93800E-2\n'
    )
    assert records2[0].fields['r'] == '1.93800E-2'


def test_profile_statement():
    records, header = _parse(
        'Profile = cim18v17\n'
        '\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
    )
    assert header.profile == 'cim18v17'
    assert len(records) == 1


def test_authority_and_model_statements():
    records, header = _parse(
        'Profile = cim18v17\n'
        'Authority = https://github.com/PNNL-CIM-Tools/CIMTBL-Models\n'
        'Model = IEEE 13 node test feeder\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
    )
    assert header == parser.Header(profile='cim18v17',
                                   authority='https://github.com/PNNL-CIM-Tools/CIMTBL-Models',
                                   model='IEEE 13 node test feeder')
    assert len(records) == 1


def test_setting_declared_twice_raises():
    with pytest.raises(ValueError, match='Model is declared twice'):
        _parse('Model = a\nModel = b\n')


def test_import_model_mismatch_raises(tmp_path):
    (tmp_path / 'catalog.cimtbl').write_text('Model = catalog\n')
    (tmp_path / 'main.cimtbl').write_text('Model = ieee13\nImport catalog.cimtbl\n')
    with pytest.raises(ValueError, match='Model mismatch'):
        parser.parse_file(tmp_path / 'main.cimtbl')


def test_same_class_two_header_shapes():
    records, _ = _parse(
        'Table ACLineSegment: name, bus1, bus2, r (pu)\n'
        '    line_1_2, topo_1, topo_2, 1.93800E-2\n'
        '\n'
        'Table ACLineSegment: name, node1, node2, phases, length (ft)\n'
        '    650632, RG60, 362, ABCN, 2000\n'
    )
    assert len(records) == 2
    assert 'bus1' in records[0].fields
    assert 'node1' in records[1].fields


def test_malformed_object_missing_colon_raises():
    with pytest.raises(parser.CimtblParseError, match=r'^<test>:1:\d+: '):
        _parse('Object BaseFrequency name=x, frequency=60\n')


def test_malformed_unterminated_unit_raises():
    with pytest.raises(parser.CimtblParseError):
        _parse('Table BaseVoltage: name, nominalVoltage (V\n    base_115, 115000\n')


def test_parse_error_in_an_import_names_the_imported_file(tmp_path):
    # CIMHub's IEEE13_CYME: an empty header cell in the imported part
    (tmp_path / 'transformers.cimtbl').write_text(
        'Profile = cim18v17\n'
        'Table PowerTransformerEnd: mRID, ratedS (kVA), , x\n'
    )
    (tmp_path / 'master.cimtbl').write_text('Profile = cim18v17\nImport transformers.cimtbl\n')
    with pytest.raises(parser.CimtblParseError, match=r'transformers\.cimtbl:2:\d+: '):
        parser.parse_file(tmp_path / 'master.cimtbl')


def test_missing_import_names_the_importing_line(tmp_path):
    (tmp_path / 'master.cimtbl').write_text('Profile = cim18v17\n\nImport parts.cimtbl\n')
    with pytest.raises(parser.CimtblParseError, match=r"master\.cimtbl:3: Import 'parts.cimtbl'"):
        parser.parse_file(tmp_path / 'master.cimtbl')


@pytest.mark.parametrize('first_cell', ['Object_1', 'Objects', 'Profiles', 'Model', 'Tablet', 'Import'])
def test_a_row_cell_starting_with_a_keyword_is_a_value(first_cell):
    records, _ = _parse(f'Table IdentifiedObject: mRID, name\n    {first_cell}, Table 3\n')
    assert records[0].fields == {'mRID': first_cell, 'name': 'Table 3'}


# --- import splicing ---------------------------------------------------

def test_import_splices_records_from_file():
    records, header = parser.parse_file(SAMPLES_DIR / 'ieee13.cimtbl')
    wire_records = [r for r in records if r.cim_class == 'OverheadWireInfo']
    # wire_infos.cimtbl has two `Table OverheadWireInfo` blocks (3 rows total)
    assert len(wire_records) == 3
    assert wire_records[0].source_file.endswith('wire_infos.cimtbl')
    names = [r.fields['mRID'] for r in wire_records]
    assert names == ['ACSR_556_5', 'ACSR_4/0', 'CU_1/0']


def test_import_profile_mismatch_raises(tmp_path):
    (tmp_path / 'catalog.cimtbl').write_text(
        'Profile = cimhub_2023\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
    )
    (tmp_path / 'main.cimtbl').write_text(
        'Profile = cim18v17\n'
        'Import catalog.cimtbl\n'
    )
    with pytest.raises(ValueError, match='Profile mismatch'):
        parser.parse_file(tmp_path / 'main.cimtbl')


def test_import_profile_inherited_when_absent_locally(tmp_path):
    (tmp_path / 'catalog.cimtbl').write_text(
        'Profile = cim18v17\n'
        'Object BaseFrequency: name=base_freq, frequency=60\n'
    )
    (tmp_path / 'main.cimtbl').write_text('Import catalog.cimtbl\n')
    records, header = parser.parse_file(tmp_path / 'main.cimtbl')
    assert header.profile == 'cim18v17'
    assert len(records) == 1


# --- end to end: real sample files -----------------------------------------

def test_ieee13_parses_end_to_end():
    records, header = parser.parse_file(SAMPLES_DIR / 'ieee13.cimtbl')
    assert len(records) == 75
    assert header == parser.Header()

    line_1_2 = next(r for r in records if r.fields.get('mRID') == 'line_1_2')
    assert line_1_2.fields['r'] == '1.93800E-2'
    assert line_1_2.fields['x'] == '5.91700E-2'
    assert line_1_2.fields['bch'] == '0.05280'
    assert line_1_2.units['r'] == 'pu'

    load_634 = next(r for r in records if r.fields.get('mRID') == 'load_634')
    assert load_634.fields['p'] is None
    assert load_634.fields['q'] is None


def test_ieee14_parses_end_to_end():
    records, header = parser.parse_file(SAMPLES_DIR / 'ieee14.cimtbl')
    assert header == parser.Header()
    classes = [r.cim_class for r in records]
    assert classes == ['BaseFrequency', 'BasePower', 'BaseVoltage', 'ACLineSegment']
    base_voltage = next(r for r in records if r.cim_class == 'BaseVoltage')
    assert base_voltage.units['nominalVoltage'] == 'kV'


_KNOWN_SCHEMA_GAPS = {
    # Not real classes in the cimhub_2026 CIM profile at all - proposed,
    # not-yet-standardized CIM18 constructs (pending the C57.12 transformer
    # overhaul). Reflection can't legalize a class that doesn't exist.
    'TransformerAssembly', 'TransformerWinding',
    # TransformerTank/TransformerTankEnd: a genuine drift between the real
    # installed cimhub_2026 profile (reflected against here) and the stale
    # cimhub_2026.linkml.yaml snapshot the old LinkML schema trusted instead -
    # noLoadLoss/fullLoadLoss/percentMagnetizingCurrent/percentImpedance and
    # TransformerTankEnd.sequenceNumber (really endNumber) don't exist on the
    # live classes. Pending the same C57.12 overhaul.
    'TransformerTank', 'TransformerTankEnd',
    # Same drift: ShuntCompensator.nomQ (and ShuntCompensatorPhase.nomQ) are
    # in the stale LinkML snapshot but not on the live cimhub_2026 classes -
    # only nomU exists there now.
    'LinearShuntCompensator', 'LinearShuntCompensatorPhase',
}


def test_ieee13_validates_end_to_end_through_phase2():
    records, _ = parser.parse_file(SAMPLES_DIR / 'ieee13.cimtbl')
    checked = 0
    for record in records:
        if record.cim_class in _KNOWN_SCHEMA_GAPS:
            continue
        if record.fields.get('mRID') == 'load_646bc':
            continue
        validate.validate(record)
        checked += 1
    assert checked == 61


def test_grammar_builds_lalr_without_ambiguity():
    grammar_path = Path(parser.__file__).parent / 'grammar.lark'
    lark.Lark(grammar_path.read_text(), parser='lalr', propagate_positions=True)


def test_quoted_cells_take_comma_hash_and_parens_literally():
    records, _ = _parse(
        'Table WireInfo: mRID, name, description\n'
        '    "#2_ACSR_6/1", "DEFAULT (DC)", "a, b # not a comment"  # a real comment\n'
        '    plain, "  padded  ", "say ""hi"""\n'
    )
    assert records[0].fields == {'mRID': '#2_ACSR_6/1', 'name': 'DEFAULT (DC)', 'description': 'a, b # not a comment'}
    assert records[1].fields == {'mRID': 'plain', 'name': '  padded  ', 'description': 'say "hi"'}


def test_quoted_empty_string_is_not_unset():
    records, _ = _parse('Object WireInfo: mRID=w, name="", description=\n')
    assert records[0].fields == {'mRID': 'w', 'name': '', 'description': None}


def test_quoted_header_default():
    records, _ = _parse('Table WireInfo: mRID, name="DEFAULT (DC)"\n    w1\n')
    assert records[0].fields == {'mRID': 'w1', 'name': 'DEFAULT (DC)'}
