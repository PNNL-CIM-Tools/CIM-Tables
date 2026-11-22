# The grammar

`.cimtbl` is parsed by one fixed [Lark](https://lark-parser.readthedocs.io/) grammar, `cimtables/grammar.lark`,
with Lark's LALR parser (`parser.py`). The grammar knows statement shapes, cells, units and comments. It knows
**no CIM**: class and attribute names are just `NAME` tokens, and checking them is
[validation's](binding.md#validate-reflection-on-the-profile) job. A new CIM profile never changes the grammar,
and no CIM-specific rule should ever be added to it.

## The whole grammar

The file as it is in the repository:

```text title="cimtables/grammar.lark"
--8<-- "cimtables/grammar.lark"
```

## How it reads a file

### Lines are the structure

Every statement starts at the beginning of a line and ends at a line break. A table's rows are "lines that
aren't a new statement"; their indentation is ignored by the grammar and is purely for people.

Comments are `%ignore`d without swallowing their newline, so a comment-only line lexes as a bare `_NL`, exactly
like a blank line. Each statement and row therefore ends in `_NL+`, which absorbs any run of blank and comment
lines, and `start: _NL* statement*` tolerates them at the top of the file too.

### Statements

| Rule | Text | Becomes |
|---|---|---|
| `profile_stmt`, `authority_stmt`, `model_stmt` | `Profile = cim18v17` | a field of the file's `Header` |
| `import_stmt` | `Import catalog.cimtbl` | an `ImportRecord`, resolved by `parse_file` |
| `object_stmt` | `Object Feeder: mRID=fdr` | one `RawRecord(form='Object')` |
| `table_stmt` | `Table Terminal: mRID, ...` + rows | one `RawRecord(form='Table')` per row |
| `update_object_stmt`, `update_table_stmt` | `Update ...` | the same, with `form='Update'` |

### Keywords only where a statement follows

`Object`, `Table`, `Update`, `Import`, `Profile`, `Authority` and `Model` are terminals with a higher priority
(`.3`) than `NAME` (`.2`), and each carries a lookahead for the shape of its statement:

```text
TABLE.3: /Table(?=[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*:)/
```

So `Table` is a keyword only when a class name and a colon follow it. A row whose first cell happens to be
`Table 3`, `Object_1` or `Profiles` is lexed as a `VALUE`, not as syntax.
`document.py` relies on the same shapes, in reverse: it quotes any cell that starts like a statement.

### Cells

```text
cell: VALUE -> value
    | QUOTED -> quoted
    | -> blank
```

- `VALUE` never starts with whitespace and can't contain `,` `(` `)` `#` or a newline. Surrounding whitespace is
  ignored, so cells come out trimmed.
- `QUOTED` is CSV-style: `,` `#` `(` `)` are literal inside, and `""` is one embedded quote.
- An empty alternative gives `blank`, so `a,,c` has an unset middle cell. `parser._cell_text` turns `blank` into
  `None`, and a quoted `""` into the empty string `''`: unset and empty are different.

### Units and header defaults

```text
header_cell: NAME unit? ("=" cell)?
unit: "(" UNIT_TEXT ")"
```

`length (ft)` is a `NAME` plus a `unit`; `EquipmentContainer=fdr` is a `NAME` with a default cell. The unit text
is kept verbatim (`pu system` included) for validation to interpret.

## From parse tree to records

`parser._RecordBuilder` is a Lark `Transformer` that turns each statement's subtree into `RawRecord`s:

- **`object_stmt`** maps each `name (unit)=cell` to `fields[name]` and `units[name]`.
- **`table_stmt`** splits the header into positional columns and defaulted columns. A row's cells fill the
  positional columns in order; extra trailing cells override the defaulted ones, in header order; a row with more
  cells than the header raises `CimtblParseError`. Every row's record carries the header's units and its own
  source line.
- **`update_*`** build the same records and set `form='Update'`.

Settings and `Import`s are handled around the transformer, in `parse_text`: a setting declared twice raises, and
an `Import` is returned in place as an `ImportRecord`. `parse_text` never touches the filesystem; `parse_file`
resolves the imports, as described in [Loading a file](loading.md#parse_file-and-import).

A `RawRecord` is still all text:

```python
RawRecord(form='Table', cim_class='ACLineSegment',
          fields={'mRID': 'ln_trunk', 'node1': 'sourcebus', 'length': '2000', 'EquipmentContainer': 'fdr', ...},
          units={'length': 'ft', ...},
          source_file='tiny_feeder.cimtbl', source_line=15)
```

## Errors

A Lark `UnexpectedInput` is re-raised as `CimtblParseError` (a `ValueError`) formatted `<file>:<line>:<col>:`,
naming the imported file the error is in. An exception raised inside a transformer method reaches the caller as
itself, not wrapped in Lark's `VisitError`.

## Changing the grammar

The grammar is deliberately small. Before adding to it, check whether the feature can be a column instead: the
connectivity, phase and `wkt` shorthand are all columns (`synthetic.py`), not syntax. If it does need syntax:

1. Keep it profile-agnostic.
2. Give any new keyword a lookahead for its statement shape, as the existing ones have.
3. Update `document.py` so it writes the new form and quotes cells that would read as it, and test
   `parse_text(format_document(doc))` round trips.
4. Update the design record's [The format](../CIMTBL_DESIGN.md#the-format) section.
