"""RawRecord: the parser's output, validate.py's input."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class RawRecord:
    """One Object/Table row (formative: defines the object) or Update row
    (additive: more attributes on an object defined earlier), straight off the grammar. Every cell is still a
    str (or None for a blank cell) - no CIM knowledge, no coercion.
    validate.py is the only consumer."""

    form: Literal['Object', 'Table', 'Update']
    cim_class: str
    fields: dict[str, str | None]
    units: dict[str, str | None]
    source_file: str
    source_line: int
