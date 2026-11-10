"""Tests for the wkt synthetic column: parser, binder, and document round-trip."""

import importlib

import pytest
from cimgraph.core.env_vars import get_cim_profile
from cimgraph.models import FeederModel

from cimtables import graph_write, parser, validate
from cimtables.binder import bind_row
from cimtables.document import Column, Document, DocumentSet, ObjectStatement, Row, Table, format_document
from cimtables.wkt import Coordinate, WktGeometry, parse as parse_wkt


# ---------------------------------------------------------------------------
# wkt.parse unit tests
# ---------------------------------------------------------------------------

def test_parse_point():
    geom = parse_wkt('POINT (-72.3 44.6)')
    assert geom == WktGeometry('POINT', [Coordinate('-72.3', '44.6')])


def test_parse_point_z():
    geom = parse_wkt('POINT Z (-72.3 44.6 100.0)')
    assert geom == WktGeometry('POINT', [Coordinate('-72.3', '44.6', '100.0')])


def test_parse_linestring():
    geom = parse_wkt('LINESTRING (-72.3 44.6, -72.4 44.7, -72.5 44.8)')
    assert geom.kind == 'LINESTRING'
    assert len(geom.coordinates) == 3
    assert geom.coordinates[0] == Coordinate('-72.3', '44.6')
    assert geom.coordinates[2] == Coordinate('-72.5', '44.8')


def test_parse_linestring_z():
    geom = parse_wkt('LINESTRING Z (-72.3 44.6 10.0, -72.4 44.7 10.0)')
    assert geom.coordinates[0].z == '10.0'


def test_parse_case_insensitive():
    geom = parse_wkt('point (-72.3 44.6)')
    assert geom.kind == 'POINT'


def test_parse_unsupported_type_raises():
    with pytest.raises(ValueError, match='POLYGON'):
        parse_wkt('POLYGON ((0 0, 1 0, 1 1, 0 0))')


def test_parse_point_multiple_coords_raises():
    with pytest.raises(ValueError, match='exactly one'):
        parse_wkt('POINT (-72.3 44.6, -72.4 44.7)')


def test_parse_linestring_one_coord_raises():
    with pytest.raises(ValueError, match='at least 2'):
        parse_wkt('LINESTRING (-72.3 44.6)')


# ---------------------------------------------------------------------------
# document format: wkt cells are auto-quoted (contain parens and commas)
# ---------------------------------------------------------------------------

def test_wkt_cell_is_quoted_in_document():
    doc = Document('test', [
        Table('ACLineSegment', [Column('mRID'), Column('wkt')], [
            Row(['line1', 'LINESTRING (-72.3 44.6, -72.4 44.7)']),
        ])
    ])
    text = format_document(doc)
    assert '"LINESTRING (-72.3 44.6, -72.4 44.7)"' in text


def test_point_wkt_cell_is_quoted_in_document():
    doc = Document('test', [
        ObjectStatement('ACLineSegment', [
            (Column('mRID'), 'line1'),
            (Column('wkt'), 'POINT (-72.3 44.6)'),
        ])
    ])
    text = format_document(doc)
    assert '"POINT (-72.3 44.6)"' in text


# ---------------------------------------------------------------------------
# binder: wkt column creates Location + PositionPoints
# ---------------------------------------------------------------------------

@pytest.fixture
def cim(monkeypatch):
    monkeypatch.setenv('CIMG_CIM_PROFILE', 'cim18v17')
    get_cim_profile.cache_clear()
    yield importlib.import_module('cimprofiles.cim18.cim18v17')
    get_cim_profile.cache_clear()


@pytest.fixture
def network(cim):
    return FeederModel(connection=None, container=cim.Feeder(mRID='f1'), distributed=False)


def _bind(network, text):
    records, _ = parser.parse_text(text, source_file='<test>')
    return [bind_row(network, validate.validate(r)) for r in records]


def test_point_wkt_creates_location_and_one_position_point(network, cim):
    _bind(network, (
        'Table ACLineSegment: mRID, node1, node2, wkt\n'
        '    line1, 650, 632, "POINT (-72.3 44.6)"\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line1')
    assert line.Location is not None
    assert line.Location.mRID == 'line1_loc'
    assert len(line.Location.PositionPoints) == 1
    pp = line.Location.PositionPoints[0]
    assert pp.xPosition == '-72.3'
    assert pp.yPosition == '44.6'
    assert pp.sequenceNumber == 0


def test_linestring_wkt_creates_one_position_point_per_vertex(network, cim):
    _bind(network, (
        'Table ACLineSegment: mRID, node1, node2, wkt\n'
        '    line2, 650, 632, "LINESTRING (-72.3 44.6, -72.4 44.7, -72.5 44.8)"\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line2')
    pps = sorted(line.Location.PositionPoints, key=lambda p: p.sequenceNumber)
    assert len(pps) == 3
    assert [(p.xPosition, p.yPosition) for p in pps] == [
        ('-72.3', '44.6'), ('-72.4', '44.7'), ('-72.5', '44.8')
    ]


def test_wkt_location_reverse_side_is_wired(network, cim):
    _bind(network, (
        'Table ACLineSegment: mRID, node1, node2, wkt\n'
        '    line3, 650, 632, "POINT (-72.3 44.6)"\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line3')
    loc = line.Location
    assert any(r is line for r in loc.PowerSystemResources)


def test_wkt_z_coordinates_set_zposition(network, cim):
    _bind(network, (
        'Table ACLineSegment: mRID, node1, node2, wkt\n'
        '    line4, 650, 632, "POINT Z (-72.3 44.6 100.5)"\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line4')
    pp = line.Location.PositionPoints[0]
    assert pp.zPosition == '100.5'


def test_no_wkt_column_leaves_location_unset(network, cim):
    _bind(network, (
        'Table ACLineSegment: mRID, node1, node2\n'
        '    line5, 650, 632\n'
    ))
    line = graph_write.resolve(network, cim.ACLineSegment, 'line5')
    assert line.Location is None


def test_wkt_on_non_psr_raises(network, cim):
    # BaseVoltage has no Location slot (it's not a PowerSystemResource)
    with pytest.raises(Exception, match='Location slot'):
        _bind(network, 'Object BaseVoltage: mRID=bv1, nominalVoltage (V)=12470, wkt="POINT (0 0)"\n')


def test_wkt_unsupported_type_raises_with_file_line(network, cim):
    with pytest.raises(Exception, match='POLYGON'):
        _bind(network, (
            'Table ACLineSegment: mRID, node1, node2, wkt\n'
            '    line6, 650, 632, "POLYGON ((0 0, 1 0, 1 1, 0 0))"\n'
        ))
