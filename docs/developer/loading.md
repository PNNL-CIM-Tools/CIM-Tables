# Loading a file

`cimtables.load_model(path)` is the whole pipeline: parse the file and its imports, apply its settings, validate
and bind every row into a new in-memory cim-graph model, then choose the model class and container. It is about
twenty lines in `cimtables/__init__.py`.

## `load_model`

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    actor Caller
    participant init as cimtables.load_model
    participant parser as parser.parse_file
    participant core as cimgraph.core
    participant validate as validate.validate
    participant binder as binder.bind_row
    participant model as NodeBreakerModel

    Caller ->>+ init: load_model(path, model_cls)
    init ->> init: check model_cls is None, FeederModel,<br/>NodeBreakerModel or BusBranchModel
    init ->>+ parser: parse_file(path)
    parser -->>- init: records, Header(profile, authority, model)
    opt the file declares Profile =
        init ->> init: os.environ['CIMG_CIM_PROFILE'] = profile
    end
    init ->> core: get_cim_profile.cache_clear()
    init ->> core: set_model_scope(authority, model)
    init ->>+ model: NodeBreakerModel(container=None, connection=None)<br/>(BusBranchModel if asked for)
    model -->>- init: network
    loop every record, in file order
        init ->>+ validate: validate(record)
        validate -->>- init: <Class>Row
        init ->>+ binder: bind_row(network, row, update=record.form == 'Update')
        binder -->>- init: live object (now in network.graph)
    end
    alt BusBranchModel
        init ->> init: container = the one GeographicalRegion, else None
    else one Feeder, and model_cls is not NodeBreakerModel
        init ->> init: FeederModel(container=feeder, graph=network.graph)
    else
        init ->> init: container = the one Substation, else None
    end
    init -->>- Caller: model
```

Three things to notice:

- **The model has no container while rows bind.** `bind_row` defaults an `EquipmentContainer` from
  `network.container`, so a container set too early would leak into equipment the file didn't put in it. The
  container is attached only after every row is bound.
- **`model_cls=FeederModel` asserts.** If the file doesn't have exactly one `Feeder`, `load_model` raises
  instead of returning another class. `NodeBreakerModel` never becomes a `FeederModel`, and Feeders are allowed in
  it (a transmission model with feeders under it).
- **Settings are process-global.** The profile is an environment variable and the model scope is cim-graph state,
  as they are everywhere in cim-graph. Tests that load files with different scopes restore them (see
  `tests/test_load_model.py`'s `model_scope` fixture).

`load(path, network)` and `load_text(text, network)` are the same loop onto a network the caller made. They
don't touch the profile: a file whose `Profile =` isn't `network.cim` raises `CimtblValidationError`, because
its rows would validate against one profile and bind with another's classes.

## `parse_file` and `Import`

`Import` is textual composition: the imported file's records are spliced in where the `Import` stood, as if its
text were pasted there. `parse_text` never reads a file; it returns each `Import` in place as an `ImportRecord`,
and `parse_file` resolves them recursively.

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    participant pf as parser.parse_file
    participant inner as parser._parse_file
    participant pt as parser.parse_text
    participant lark as Lark (LALR)

    pf ->>+ inner: _parse_file(path, importers=())
    inner ->>+ pt: parse_text(path.read_text())
    pt ->>+ lark: parse(text)
    lark -->>- pt: tree
    pt ->> pt: settings → Header (each at most once)<br/>Import → ImportRecord<br/>Object/Table/Update → RawRecords
    pt -->>- inner: records, header
    loop every ImportRecord
        inner ->> inner: path relative to the importing file<br/>missing file or a cycle → CimtblParseError
        inner ->>+ inner: _parse_file(import_path, chain + path)
        inner -->>- inner: imported records, imported header
        inner ->> inner: splice records in place<br/>merge header (fill unset, a mismatch raises)
    end
    inner -->>- pf: records, header
```

- **Cycles** are detected on the chain of importing files: `a` → `b` → `a` raises at the `Import` line. A file
  imported from two branches (`a` → `b` → `cat`, `a` → `c` → `cat`) is not a cycle; its rows bind twice, and the
  second time each one [restates](binding.md#restating-an-object) the object.
- **Settings merge.** An imported file's `Profile`/`Authority`/`Model` fills one the importer left unset; a
  different value raises at the `Import` line.
- **The parser applies nothing.** It only reads the settings; `load` and `load_model` apply them.

## Identity: where UUIDs come from

`bind_row` creates every object as `cim_cls(mRID=row.mRID)`. cim-graph seeds the UUID from the mRID under the
current model scope, which `load_model` has just set from `Authority =` and `Model =`:

```mermaid
%%{init: {"themeVariables": {"actorLineColor": "#a1a1aa", "activationBkgColor": "#d578261f", "activationBorderColor": "#d57826"}}}%%
sequenceDiagram
    participant binder as binder.bind_row
    participant cls as cim.ACLineSegment
    participant core as cimgraph.core (model scope)
    participant gw as graph_write.add_to_graph
    participant model as GraphModel

    binder ->>+ cls: ACLineSegment(mRID='ln_trunk')
    cls ->> core: seeded UUID from (authority, model, 'ln_trunk')
    cls -->>- binder: obj (identifier = that UUID)
    Note over binder: set attributes, references, Terminals
    binder ->>+ gw: add_to_graph(network, obj)
    gw ->>+ model: add_to_graph(obj)
    model ->> model: index the mRID<br/>(already used by another object → raises)
    model -->>- gw: 
    gw -->>- binder: 
```

The same file therefore gives the same UUIDs on every load, two models can each have a bus `671`, and renaming
an object (`name`) never changes its UUID. `write_xml` writes the scope into the model header and cim-graph's XML
reader restores it.
