"""Reflection validation gate tests.

validate.validate() turns a RawRecord into a validated <Class>Row. Covers:
unknown class, unknown column with closest-match suggestion, Qty binding for
physical-quantity slots, FK pass-through as unresolved mRID strings, and correct native Python typing (int/bool/float)
for plain scalar slots.
"""

import pytest

from cimtables.units import Qty
from cimtables import records, validate


def _record(cim_class, fields, units=None, form='Table', line=1):
    return records.RawRecord(
        form=form, cim_class=cim_class, fields=fields, units=units or {},
        source_file='<test>', source_line=line,
    )


def test_unknown_class_raises_with_source_location():
    with pytest.raises(validate.CimtblValidationError, match=r'<test>:7: unknown CIM class .Nonexistent.'):
        validate.validate(_record('Nonexistent', {'mRID': 'x'}, line=7))


def test_unknown_column_suggests_closest_match():
    with pytest.raises(validate.CimtblValidationError, match=r"no attribute 'frequancy' - did you mean 'frequency'"):
        validate.validate(_record('BaseFrequency', {'mRID': 'x', 'frequancy': '60'}))


def test_object_form_scalar_fields():
    row = validate.validate(_record('BaseFrequency', {'mRID': 'base_freq', 'frequency': '60'}, form='Object'))
    assert row.mRID == 'base_freq'
    # frequency is a physical-quantity slot - always a Qty, even with
    # no unit on the .cimtbl column (blank unit string, not a bare float).
    assert row.frequency == Qty(60.0, '')


def test_quantity_slot_binds_qty_with_unit():
    row = validate.validate(
        _record('BasePower', {'mRID': 'sbase', 'basePower': '100'}, units={'basePower': 'MVA'}, form='Object')
    )
    assert row.basePower == Qty(100.0, 'MVA')


def test_quantity_slot_blank_cell_is_none():
    row = validate.validate(
        _record('EnergyConsumer', {
            'mRID': 'load_634', 'node': '634', 'p': None, 'q': None,
            'phaseConnection': 'Y', 'BaseVoltage': 'base_480', 'LoadResponse': 'zip_constantPQ',
        }, units={'p': 'kW', 'q': 'kVAr'})
    )
    assert row.p is None
    assert row.q is None


def test_fk_slot_passes_through_as_unresolved_string():
    row = validate.validate(
        _record('EnergyConsumerPhase', {
            'mRID': 'load_634a', 'phase': 'A', 'p': '160', 'q': '110', 'EnergyConsumer': 'load_634',
        }, units={'p': 'kW', 'q': 'kW'})
    )
    assert row.EnergyConsumer == 'load_634'
    assert isinstance(row.EnergyConsumer, str)


def test_integer_slot_is_native_int_not_str():
    row = validate.validate(
        _record('ACLineSegment', {
            'mRID': 'line_1_2', 'bus1': 'topo_bus_1', 'bus2': 'topo_bus_2', 'circuitNumber': '1',
            'r': '1.93800E-2', 'x': '5.91700E-2', 'bch': '0.05280', 'BaseVoltage': 'base_115',
        }, units={'r': 'pu', 'x': 'pu', 'bch': 'pu'})
    )
    assert row.circuitNumber == 1
    assert isinstance(row.circuitNumber, int)


def test_boolean_slot_is_native_bool_not_str():
    row = validate.validate(
        _record('Fuse', {
            'mRID': 'fuse_1', 'node1': '633', 'node2': 'XF1', 'ratedCurrent': '100.0',
            'open': 'false', 'normalOpen': 'false',
        }, units={'ratedCurrent': 'A'})
    )
    assert row.open is False
    assert row.normalOpen is False
    assert isinstance(row.open, bool)
    # regression guard: str(False) == 'False' is truthy - a stringified bool
    # would silently break every downstream `if row.open:` check.
    assert not row.open


def test_boolean_slot_rejects_non_boolean_text():
    # 'yes'/'open'/typos used to silently become False
    for raw in ('yes', 'open', 'ture'):
        with pytest.raises(validate.CimtblValidationError, match=r"'open'.*expected a boolean"):
            validate.validate(_record('Fuse', {'mRID': 'fuse_1', 'open': raw}))


def test_two_header_shapes_of_same_class_both_validate():
    by_sequence = validate.validate(
        _record('ACLineSegment', {
            'mRID': 'line_1_2', 'bus1': 'topo_bus_1', 'bus2': 'topo_bus_2', 'circuitNumber': '1',
            'r': '1.93800E-2', 'x': '5.91700E-2', 'bch': '0.05280', 'BaseVoltage': 'base_115',
        }, units={'r': 'pu', 'x': 'pu', 'bch': 'pu'})
    )
    by_per_length = validate.validate(
        _record('ACLineSegment', {
            'mRID': '650632', 'node1': 'RG60', 'node2': '362', 'phases': 'ABCN',
            'length': '2000', 'PerLengthImpedance': 'mtx601', 'BaseVoltage': 'base_4160',
        }, units={'length': 'ft'})
    )
    assert by_sequence.bus1 == 'topo_bus_1'
    assert by_sequence.node1 is None
    assert by_per_length.node1 == 'RG60'
    assert by_per_length.bus1 is None


def test_row_dataclass_is_cached_per_class():
    cls_a = validate._row_dataclass('BaseFrequencyRow', 'cim18v17')
    cls_b = validate._row_dataclass('BaseFrequencyRow', 'cim18v17')
    assert cls_a is cls_b


def test_real_attribute_shadows_synthetic_column_of_same_name():
    # Terminal.phases is a real PhaseCode attribute, not the DSL's
    # phase-children shorthand string
    row = validate.validate(_record('Terminal', {'mRID': 't1', 'phases': 'AB'}))
    assert row.phases.value == 'AB'
