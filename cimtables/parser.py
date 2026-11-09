"""The parser: .cimtbl text -> RawRecords (grammar.lark) plus the file's Header of settings."""

from dataclasses import dataclass, fields, replace
from pathlib import Path

import lark

from cimtables.records import ImportRecord, RawRecord

_GRAMMAR_PATH = Path(__file__).parent / 'grammar.lark'
_LARK_PARSER = lark.Lark(
    _GRAMMAR_PATH.read_text(encoding='utf-8'),
    parser='lalr',
    propagate_positions=True,
)


class CimtblParseError(ValueError):
    """A .cimtbl syntax error, as `<file>:<line>:<col>: <message>`; <file> is
    the file the error is in, an imported one included."""


def _cell_text(cell_node: lark.Tree) -> str | None:
    """A `cell` node is `value` (one VALUE token, possibly with incidental
    leading/trailing whitespace the grammar doesn't strip), `quoted` (a
    CSV-style "..." token) or `blank` (no token at all). A blank or
    empty-after-stripping value is None - a lone space between two
    commas is indistinguishable in intent from no character at all. A quoted
    cell is taken verbatim, `""` -> `"`, so `""` is the empty string, not unset."""
    if cell_node.data == 'blank':
        return None
    if cell_node.data == 'quoted':
        return str(cell_node.children[0])[1:-1].replace('""', '"')
    text = str(cell_node.children[0]).strip()
    return text or None


def _header_cell_name_unit_default(header_cell_node: lark.Tree) -> tuple[str, str | None, str | None]:
    """A header_cell is NAME unit? ("=" cell)? - unpack whichever of the two
    optional children are present by node type rather than position, since
    either, both, or neither may appear."""
    name = str(header_cell_node.children[0])
    unit: str | None = None
    default: str | None = None
    for child in header_cell_node.children[1:]:
        if child.data == 'unit':
            unit = str(child.children[0]).strip()
        else:
            default = _cell_text(child)
    return name, unit, default


@dataclass(frozen=True)
class Header:
    """A file's settings statements, each None if absent: `Profile =` (the CIM
    profile), `Authority =` and `Model =` (the model authority URI and
    model name that UUIDs are seeded under, see cimgraph.core.set_model_scope).
    Applied by cimtables.load / load_model, never by the parser."""
    profile: str | None = None
    authority: str | None = None
    model: str | None = None


_HEADER_STATEMENTS = {'profile_stmt': 'profile', 'authority_stmt': 'authority', 'model_stmt': 'model'}


class _RecordBuilder(lark.Transformer):
    """Parse tree -> flat list[RawRecord]. Table statements expand
    to one RawRecord per data row, sharing that table's cim_class/units;
    Object statements become exactly one RawRecord. Import and settings
    statements are handled by parse_text, not here."""

    def __init__(self, source_file: str):
        super().__init__()
        self.source_file = source_file

    def object_stmt(self, children: list) -> RawRecord:
        keyword_token, name_token, assignment_list = children
        fields: dict[str, str | None] = {}
        units: dict[str, str | None] = {}
        for assignment in assignment_list.children:
            name = str(assignment.children[0])
            has_unit = len(assignment.children) == 3
            unit = str(assignment.children[1].children[0]).strip() if has_unit else None
            cell_node = assignment.children[-1]
            fields[name] = _cell_text(cell_node)
            units[name] = unit
        return RawRecord(
            form='Object', cim_class=str(name_token), fields=fields, units=units,
            source_file=self.source_file, source_line=name_token.line,
        )

    def table_stmt(self, children: list) -> list[RawRecord]:
        keyword_token, name_token, header_list, *table_rows = children
        cim_class = str(name_token)

        columns: list[str] = []  # header order, for fields dict ordering
        row_columns: list[str] = []  # non-defaulted columns - always consume a row-cell position
        defaulted_columns: list[str] = []  # header order - a row may override in this order too
        units: dict[str, str | None] = {}
        constants: dict[str, str | None] = {}
        for header_cell in header_list.children:
            name, unit, default = _header_cell_name_unit_default(header_cell)
            columns.append(name)
            units[name] = unit
            has_default = len(header_cell.children) > 1 and header_cell.children[-1].data != 'unit'
            if has_default:
                constants[name] = default
                defaulted_columns.append(name)
            else:
                row_columns.append(name)

        # A row's cells fill row_columns first (positional, always present),
        # then any *extra* trailing cells fill defaulted_columns in header
        # order - letting a row override a subset of the header's defaults
        # (e.g. `load_sub`'s own EquipmentContainer cell in ieee13.cimtbl).
        records: list[RawRecord] = []
        for row in table_rows:
            cell_nodes = row.children
            if len(cell_nodes) > len(columns):
                raise CimtblParseError(
                    f'{self.source_file}:{row.meta.line}: {len(cell_nodes)} cells, but Table {cim_class} '
                    f'has {len(columns)} columns'
                )
            row_values = {
                col: _cell_text(cell_nodes[i])
                for i, col in enumerate(row_columns) if i < len(cell_nodes)
            }
            overrides = {
                col: _cell_text(cell_nodes[len(row_columns) + i])
                for i, col in enumerate(defaulted_columns) if len(row_columns) + i < len(cell_nodes)
            }
            fields = {
                col: row_values[col] if col in row_values
                else overrides[col] if col in overrides
                else constants.get(col)
                for col in columns
            }
            records.append(RawRecord(
                form='Table', cim_class=cim_class, fields=fields, units=dict(units),
                source_file=self.source_file, source_line=row.meta.line,
            ))
        return records

    def update_object_stmt(self, children: list) -> RawRecord:
        return replace(self.object_stmt(children), form='Update')

    def update_table_stmt(self, children: list) -> list[RawRecord]:
        update_token, table_token, *rest = children
        return [replace(record, form='Update') for record in self.table_stmt([update_token, *rest])]


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith('\n') else text + '\n'


def parse_text(text: str, source_file: str = '<text>') -> tuple[list[RawRecord | ImportRecord], Header]:
    """Parse `.cimtbl` source already in memory. Returns (records, header)
    - `header` holds the file's own Profile/Authority/Model statements.
    Import statements are NOT resolved here (no filesystem access at this
    layer): each comes back in place as an ImportRecord. Use parse_file for a
    real file that may contain them."""
    try:
        tree = _LARK_PARSER.parse(_ensure_trailing_newline(text))
    except lark.exceptions.UnexpectedInput as exc:
        raise CimtblParseError(f'{source_file}:{exc.line}:{exc.column}: {exc}') from exc

    header = Header()
    builder = _RecordBuilder(source_file)
    records: list[RawRecord | ImportRecord] = []
    for statement in tree.children:
        node = statement.children[0]
        if node.data in _HEADER_STATEMENTS:
            setting = _HEADER_STATEMENTS[node.data]
            if getattr(header, setting) is not None:
                raise CimtblParseError(f'{source_file}:{node.meta.line}: {node.children[0]} is declared twice')
            header = replace(header, **{setting: str(node.children[1]).strip()})
            continue
        if node.data == 'import_stmt':
            records.append(ImportRecord(str(node.children[1]), source_file, node.meta.line))
            continue
        try:
            built = builder.transform(node)
        except lark.exceptions.VisitError as exc:  # lark wraps what a transformer method raises
            raise exc.orig_exc from None
        if isinstance(built, list):
            records.extend(built)
        else:
            records.append(built)
    return records, header


def _merge_header(header: Header, imported: Header, import_path: Path, where: str) -> Header:
    """An Import's settings fill the ones the importing file (or an earlier
    Import) left unset; one that disagrees with an already-set value raises,
    at `where` (the Import statement's `file:line: Import 'x'`)."""
    for setting in fields(Header):
        value, imported_value = getattr(header, setting.name), getattr(imported, setting.name)
        if value is None:
            header = replace(header, **{setting.name: imported_value})
        elif imported_value is not None and imported_value != value:
            raise CimtblParseError(
                f"{where}: {setting.name.capitalize()} mismatch: {import_path} declares "
                f"{imported_value!r}, but {value!r} is already set"
            )
    return header


def parse_file(path: str | Path) -> tuple[list[RawRecord], Header]:
    """The public parser entry point: a .cimtbl file -> the fully-composed,
    ordered RawRecord list validate.py consumes, plus the Header of
    Profile/Authority/Model settings the file/Import chain declared.

    Import statements are resolved here by textual composition: each
    imported file is recursively parsed (relative to the importing file's
    directory) and its records spliced in at that position. Import records
    never reach the returned list themselves. Each setting is declared at
    most once per file/Import chain - an imported file declaring a
    *different* value than one already set earlier in the chain is a
    fail-fast error, not a silent override. An Import cycle raises. The
    parser applies none of the settings.
    """
    return _parse_file(Path(path), importers=())


def _parse_file(path: Path, importers: tuple[Path, ...]) -> tuple[list[RawRecord], Header]:
    """parse_file for `path`, imported (directly or not) by `importers`, the
    resolved paths of the Import chain outermost first. An Import of a file
    already on the chain is a cycle and raises; the same file imported twice
    from different branches is not (its duplicate mRIDs raise at bind)."""
    text = path.read_text(encoding='utf-8')
    raw_records, header = parse_text(text, source_file=str(path))
    chain = (*importers, path.resolve())

    records: list[RawRecord] = []
    for record in raw_records:
        if isinstance(record, ImportRecord):
            import_path = path.parent / record.path
            where = f"{path}:{record.source_line}: Import {record.path!r}"
            if not import_path.is_file():
                raise CimtblParseError(f"{where}: {import_path} not found")
            resolved = import_path.resolve()
            if resolved in chain:
                cycle = chain[chain.index(resolved):] + (resolved,)
                raise CimtblParseError(f"{where}: Import cycle {' -> '.join(p.name for p in cycle)}")
            imported_records, imported_header = _parse_file(import_path, chain)
            records.extend(imported_records)
            header = _merge_header(header, imported_header, import_path, where)
            continue
        records.append(record)

    return records, header
