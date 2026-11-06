""".cimtbl documents as data, and their deterministic text (the parser's
inverse, so the format's rules live in one package and `parse_text(
format_document(d))` is tested here). An importer builds a Document of cell
*text*; turning typed values into text (repr(float), true/false, enum names)
is the importer's job. Layout and quoting are this module's.

Layout: an optional `#` comment, then the Profile/Authority/Model settings,
then the statements in order, one blank line between each and a trailing
newline. Table rows are indented 4 spaces; each cell but the last is padded
after its comma to the column's widest cell, so columns line up. Every row
writes every cell (a trailing None is a trailing empty cell).

Quoting: a cell is quoted iff it contains `,` `(` `)` `#` `"`, starts or ends
with whitespace, is the empty string, or starts like a statement (`Table X:`,
`Profile =`, `Import `), which the parser would otherwise read as one. Inner
`"` are doubled. None is a blank cell (unset); a newline in a cell raises.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

_ROW_INDENT = '    '
_NEEDS_QUOTES = re.compile(r'[,()#"]|^\s|\s$|^$')
_STATEMENT_START = re.compile(r'(Profile|Authority|Model)[ \t]*=|Import[ \t]|(Object|Table)[ \t]+[A-Za-z_]\w*[ \t]*:')


class Column(NamedTuple):
    """A header column: `name (unit)=default`. unit may carry the base word
    (`pu system`); a default is the cell text rows without that cell take."""
    name: str
    unit: str | None = None
    default: str | None = None


class Row(NamedTuple):
    """One cell per column without a default, in header order, optionally
    followed by cells overriding the defaulted columns, in header order."""
    cells: list[str | None]
    comment: str | None = None


class Import(NamedTuple):
    """`Import <name>.cimtbl`: name is another Document's name in the set."""
    name: str


@dataclass
class Table:
    cim_class: str
    columns: list[Column]
    rows: list[Row]
    comment: str | None = None


@dataclass
class ObjectStatement:
    """`Object <cim_class>: name (unit)=value, ...`; a Column's default is unused."""
    cim_class: str
    cells: list[tuple[Column, str | None]]
    comment: str | None = None


@dataclass
class Document:
    """One .cimtbl file, `<name>.cimtbl`. A setting left None is not written."""
    name: str
    statements: list[Table | ObjectStatement | Import]
    profile: str | None = None
    authority: str | None = None
    model: str | None = None
    comment: str | None = None


@dataclass
class DocumentSet:
    master: Document
    parts: list[Document]


def _cell(text: str | None) -> str:
    """Cell text as written: None blank, quoted where the parser needs it."""
    if text is None:
        return ''
    if '\n' in text or '\r' in text:
        raise ValueError(f'a .cimtbl cell cannot hold a newline: {text!r}')
    if _NEEDS_QUOTES.search(text) or _STATEMENT_START.match(text):
        return '"' + text.replace('"', '""') + '"'
    return text


def _comment(text: str | None, indent: str = '') -> list[str]:
    """`# ` lines for a (possibly multi-line) comment; none for None."""
    if text is None:
        return []
    return [f'{indent}# {line}'.rstrip() for line in text.split('\n')]


def format_document(document: Document) -> str:
    """The Document's .cimtbl text; the same Document always gives the same bytes."""
    blocks: list[list[str]] = []
    if document.comment is not None:
        blocks.append(_comment(document.comment))
    settings = [('Profile', document.profile), ('Authority', document.authority), ('Model', document.model)]
    setting_lines = [f'{keyword} = {value}' for keyword, value in settings if value is not None]
    if setting_lines:
        blocks.append(setting_lines)

    for statement in document.statements:
        if isinstance(statement, Import):
            blocks.append([f'Import {statement.name}.cimtbl'])
            continue
        lines = _comment(statement.comment)
        if isinstance(statement, ObjectStatement):
            assignments = [
                f"{column.name}{f' ({column.unit})' if column.unit else ''}={_cell(value)}"
                for column, value in statement.cells
            ]
            blocks.append(lines + [f"Object {statement.cim_class}: {', '.join(assignments)}"])
            continue

        header = []
        for column in statement.columns:
            text = column.name + (f' ({column.unit})' if column.unit else '')
            header.append(text if column.default is None else f'{text}={_cell(column.default)}')
        lines.append(f"Table {statement.cim_class}: {', '.join(header)}")

        required = sum(column.default is None for column in statement.columns)
        rows = [[_cell(text) for text in row.cells] for row in statement.rows]
        widths: dict[int, int] = {}
        for row_number, (row, cells) in enumerate(zip(statement.rows, rows), start=1):
            if not required <= len(cells) <= len(statement.columns):
                raise ValueError(
                    f'{document.name}: Table {statement.cim_class} row {row_number} has {len(cells)} cells, '
                    f'expected {required} (+ up to {len(statement.columns) - required} default overrides)'
                )
            if all(text is None for text in row.cells):
                raise ValueError(f'{document.name}: Table {statement.cim_class} row {row_number} is all blank')
            for position, text in enumerate(cells):
                widths[position] = max(widths.get(position, 0), len(text))
        for row, cells in zip(statement.rows, rows):
            lines.extend(_comment(row.comment, _ROW_INDENT))
            padded = [(text + ',').ljust(widths[position] + 2) for position, text in enumerate(cells[:-1])]
            lines.append((_ROW_INDENT + ''.join(padded) + cells[-1]).rstrip())
        blocks.append(lines)

    return '\n\n'.join('\n'.join(block) for block in blocks) + '\n'


def write_documents(documents: DocumentSet, directory) -> Path:
    """Write the master and every part as `<name>.cimtbl` into directory
    (created if missing); returns the master's path. Nothing is written if
    a name repeats, an Import names a document not in the set, a part is
    never imported, or directory already holds a .cimtbl file not in the set
    (a stale part from an earlier run would sit there unimported)."""
    directory = Path(directory)
    all_documents = [documents.master, *documents.parts]
    names = [document.name for document in all_documents]
    if len(set(names)) != len(names):
        raise ValueError(f'document names repeat: {sorted(n for n in set(names) if names.count(n) > 1)}')

    imported = set()
    for document in all_documents:
        for statement in document.statements:
            if not isinstance(statement, Import):
                continue
            if statement.name not in names:
                raise ValueError(f'{document.name} imports {statement.name!r}, which is not in the document set')
            imported.add(statement.name)
    never_imported = [part.name for part in documents.parts if part.name not in imported]
    if never_imported:
        raise ValueError(f'parts never imported: {never_imported}')

    stale = sorted(p.name for p in directory.glob('*.cimtbl') if p.stem not in names)
    if stale:
        raise ValueError(f'{directory} holds .cimtbl files not in the document set: {stale}')

    texts = {document.name: format_document(document) for document in all_documents}
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (directory / f'{name}.cimtbl').write_text(text)
    return directory / f'{documents.master.name}.cimtbl'
