"""Minimal WKT parser for POINT and LINESTRING geometry types.

Parses the two geometry types that appear on power system assets (points for
equipment, linestrings for conductors) without any geospatial library
dependency. Coordinate values are kept as strings to match PositionPoint's
xPosition / yPosition / zPosition attributes exactly as the source provided them.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Coordinate(NamedTuple):
    x: str
    y: str
    z: str | None = None


class WktGeometry(NamedTuple):
    """Parsed POINT or LINESTRING geometry."""
    kind: str  # 'POINT' or 'LINESTRING'
    coordinates: list[Coordinate]


_POINT = re.compile(r'^\s*POINT\s*Z?\s*\(([^)]+)\)\s*$', re.IGNORECASE)
_LINESTRING = re.compile(r'^\s*LINESTRING\s*Z?\s*\(([^)]+)\)\s*$', re.IGNORECASE)


def _parse_coords(raw: str) -> list[Coordinate]:
    """Parse the coordinate list inside a WKT geometry's parentheses."""
    coords = []
    for point_str in raw.split(','):
        parts = point_str.split()
        if len(parts) < 2:
            raise ValueError(f"expected at least x y in WKT coordinate, got {point_str!r}")
        coords.append(Coordinate(x=parts[0], y=parts[1], z=parts[2] if len(parts) > 2 else None))
    return coords


def parse(wkt: str) -> WktGeometry:
    """Parse a WKT POINT or LINESTRING string into a WktGeometry.

    Raises ValueError for unsupported geometry types or malformed input.
    The 'Z' variant (3D coordinates) is accepted for both types.
    """
    m = _POINT.match(wkt)
    if m:
        coords = _parse_coords(m.group(1))
        if len(coords) != 1:
            raise ValueError(f"POINT must have exactly one coordinate, got {len(coords)}: {wkt!r}")
        return WktGeometry('POINT', coords)
    m = _LINESTRING.match(wkt)
    if m:
        coords = _parse_coords(m.group(1))
        if len(coords) < 2:
            raise ValueError(f"LINESTRING must have at least 2 coordinates, got {len(coords)}: {wkt!r}")
        return WktGeometry('LINESTRING', coords)
    kind = wkt.strip().split('(')[0].strip().upper()
    raise ValueError(
        f"wkt column supports POINT and LINESTRING geometry; got {kind!r}. "
        f"Use Location + PositionPoint rows directly for other types."
    )
