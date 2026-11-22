# Python API

Everything below is importable from `cimtables` unless noted. Once loaded, a model is ordinary
[cim-graph](https://pnnl-cim-tools.github.io/CIM-Graph/): query it, change it, and save it with cim-graph's
`write_xml` or `write_json_ld`.

## Loading

### `load_model(path, model_cls=None)`

Build a new in-memory model from a `.cimtbl` file and everything it imports.

```python
import cimtables

network = cimtables.load_model('ieee13.cimtbl')
```

With `model_cls=None`, the file decides:

| The file has | You get | Its container |
|---|---|---|
| one `Feeder` | `FeederModel` | that Feeder |
| otherwise, one `Substation` | `NodeBreakerModel` | that Substation |
| otherwise | `NodeBreakerModel` | none |

Pass a class to say what you expect instead:

| `model_cls` | Its container |
|---|---|
| `FeederModel` | the file's one `Feeder`; any other number of Feeders raises |
| `NodeBreakerModel` | the file's one `Substation`, else none; Feeders are allowed |
| `BusBranchModel` | the file's one `GeographicalRegion`, else none |

```python
from cimgraph.models import BusBranchModel

network = cimtables.load_model('ieee14.cimtbl', model_cls=BusBranchModel)
```

`load_model` activates the file's `Profile =` and sets cim-graph's model scope from its `Authority =` and
`Model =`. Both settings are process-wide in cim-graph.

### `load(path, network)` and `load_text(text, network)`

Add a file's objects to a model you already have, and return them in file order. The file's `Profile =`, if it
has one, must be the network's profile. `load_text` takes the text itself, which is handy in tests and
notebooks; an `Import` in it raises, since there is no file to resolve it against.

```python
from cimgraph.models import FeederModel

network = FeederModel(container=feeder, connection=None, distributed=False)
cimtables.load('catalog.cimtbl', network)
cimtables.load_text('Object BaseVoltage: mRID=bv_480, nominalVoltage (V)=480', network)
```

## Writing `.cimtbl`

### `to_documents(network, name, *, terminals='folded', units=None, parts=None)`

Turn any cim-graph model (one read from CIM XML, say) into `.cimtbl` documents. Loading them back gives an equal
model. Write the result with `document.write_documents`:

```python
from cimtables.document import write_documents

documents = cimtables.to_documents(network, 'ieee13', parts={'catalog': ['WireInfo', 'TapChangerInfo']})
master = write_documents(documents, 'out/')     # out/ieee13.cimtbl, which imports out/catalog.cimtbl
```

- **`terminals='folded'`** (the default) writes Terminals and per-phase objects as `node1`, `node2` and `phases`
  columns. That renames them to the generated names (`<mRID>_T1`, `<mRID>_A`), so their UUIDs change too.
  `terminals='explicit'` keeps every Terminal as its own row with its own mRID.
- **`units`** fixes a column's unit, by `'Class.attribute'` or `'attribute'`:
  `{'ACLineSegment.length': 'ft', 'PowerTransformerEnd.x': 'percent'}`, or a path to a JSON file with the same.
  Otherwise each column gets a readable unit (kV, kW, ohm/km, ...).
- **`parts`** moves the named classes into their own imported files.

See the [CIM XML to .cimtbl](round_trip.ipynb) notebook for a full example.

### `cimtables.document`

Build `.cimtbl` text from Python with stable, aligned formatting. Model converters use this to write `.cimtbl` a
person can then read and edit. Cells are text; turning typed values into text is the caller's job.

```python
from cimtables.document import Column, Document, ObjectStatement, Row, Table, format_document

doc = Document('feeder', [
    ObjectStatement('BaseVoltage', [(Column('mRID'), 'bv_12kv'), (Column('nominalVoltage', 'kV'), '12.47')]),
    Table('EnergyConsumer',
          [Column('mRID'), Column('node'), Column('p', 'kW'), Column('BaseVoltage'),
           Column('EquipmentContainer', default='fdr')],
          [Row(['ld_1', 'n1', '1155', 'bv_12kv']),
           Row(['ld_2', 'n2', '170', 'bv_12kv'], comment='lateral')]),
], profile='cim18v17', model='feeder')
print(format_document(doc))
```

```text
Profile = cim18v17
Model = feeder

Object BaseVoltage: mRID=bv_12kv, nominalVoltage (kV)=12.47

Table EnergyConsumer: mRID, node, p (kW), BaseVoltage, EquipmentContainer=fdr
    ld_1, n1, 1155, bv_12kv
    # lateral
    ld_2, n2, 170,  bv_12kv
```

Cells that need quoting are quoted for you. `write_documents(DocumentSet(master, parts), directory)` writes a
master and the files it imports, and refuses to write anything if an import is missing, a part is never imported,
or the directory already holds another `.cimtbl` file.

## Comparing models

### `diff_models(expected, actual, rel_tol=1e-9)`

Compare two models object by object and return a list of differences (empty if they are equal). Objects are
matched by class and mRID, not by UUID, so models built under different `Authority`/`Model` scopes still compare.
Quantities are compared in SI within `rel_tol`.

```python
differences = cimtables.diff_models(from_xml, from_cimtbl)
# [] when equal; otherwise sorted lines such as 'EnergyConsumer ld_675: missing'
# or 'ACLineSegment 650632.length: expected <value>, got <value>'
```

## Lower-level pieces

| | |
|---|---|
| `parse_file(path)` / `parse_text(text)` | Text to raw records and the file's settings, without validating or loading anything. |
| `bind_row(network, row)` | Bind one validated row. This is what CIM-Builder's `ObjectBuilder.from_table` calls. |
| `cimtables.graph_write` | `set_attr`, `set_assc`, `resolve` and `get_qty`: write and read values the same way the loader does, units and pu bases included. |

These are described in the [developer docs](../developer/index.md).
