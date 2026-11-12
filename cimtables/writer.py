"""cim-graph model -> .cimtbl documents: the binder's inverse.

to_documents(network, name) returns a document.DocumentSet that loads back
(cimtables.load_model) into an equal model (diff.diff_models). Every object is
one row of its class's Table (an Object when the class has one instance),
keyed by its mRID (an Identity-only object by its identifier). Nothing is
written that the model doesn't hold: a blank cell is an unset attribute.

Terminals, two modes:
- 'folded' (default): an equipment's Terminals become its node/node1/node2
  and bus/bus1/bus2 columns, and its <Class>Phase children a phases (or
  phaseSide1/phaseSide2) column, plus sequenceNumbers where they aren't
  numbered 1..n, wherever binding that row would make exactly
  them (connectivity.phase_plan). A TransformerEnd takes its own Terminal as
  `node`. The folded objects get binding's generated mRIDs (`<mRID>_T<n>`,
  `<mRID>_<phase>`), so their mRIDs and UUIDs change; references to them
  follow. Their other attributes are written as Update rows. Anything that
  doesn't fold stays explicit.
- 'explicit': every Terminal and phase child is its own row, mRIDs kept.

Order: classes are sorted so a row's references are bound before it (Kahn's
algorithm over the references only one side can write; ties by class name),
rows by key. A reference to a row not yet written (a cycle, a later row of
the same class) and a list-valued side become `Update` rows at the end
of the master, after every object exists. Of an association both of whose
single-valued sides are serialized, only the later object's side is written.

Units: a configured unit per column (`units`: {'Class.attr' or 'attr': unit},
any class in the row class's MRO, `pu`/`percent` and `pu system` included;
read through graph_write.get_qty), else SI or a readable multiple of it (kV,
kW, MW, ohm/km, ...): the one that puts the column's median in [1, 1000),
else the one with the fewest digits. A quantity is written with the fewest
significant digits that read back within 1e-12 (13.2 kV, not
13.200000000000001); every other number exactly.

parts={part: [class names]} moves those classes' tables into `<part>.cimtbl`
files the master imports in order. A grouping in which parts (or a part and a
master class) reference each other raises: an Import can't be both before
and after another.
"""

import dataclasses
import json
import math
import statistics
from enum import Enum
from pathlib import Path

from cimgraph.models import GraphModel
from cimgraph.core import get_model_authority, get_model_name
from cimprofiles.units.units import CIMUnit

from cimtables import connectivity, document, graph_write, synthetic, validate

# Readable units a column may be written in instead of SI, by the SI unit's name
_READABLE_UNITS = {
    'V': ('kV',), 'A': ('kA',), 'W': ('kW', 'MW'), 'VA': ('kVA', 'MVA'), 'VAr': ('kvar', 'Mvar'),
    'Wh': ('kWh', 'MWh'), 'm': ('km',), 'ohmPerm': ('ohm/km',), 'SPerm': ('S/km', 'uS/km'),
}
# A quantity is written with the fewest significant digits that read back within this
# (float noise: 13.200000000000001 kV is written 13.2); diff_models compares at 1e-9
_REL_TOL = 1e-12
_SYNTHETIC_ORDER = ('node', 'node1', 'node2', 'bus', 'bus1', 'bus2', *synthetic.PHASE_COLUMNS)
# What binding a folded row sets on each Terminal it makes
_TERMINAL_FIELDS = frozenset({'ConductingEquipment', 'ConnectivityNode', 'TopologicalNode', 'sequenceNumber', 'phases'})


def to_documents(network: GraphModel, name: str, *, terminals: str = 'folded',
                 units: dict[str, str] | str | Path | None = None,
                 parts: dict[str, list[str]] | None = None) -> document.DocumentSet:
    """The model as .cimtbl documents: a master `name` plus any `parts`.
    `units` is the {column: unit} mapping or a path to a JSON file holding
    it. Write them with document.write_documents."""
    if terminals not in ('folded', 'explicit'):
        raise ValueError(f"terminals must be 'folded' or 'explicit', got {terminals!r}")
    if isinstance(units, (str, Path)):
        units = json.loads(Path(units).read_text(encoding='utf-8'))
    units = units or {}
    cim = network.cim

    objects = sorted((obj for by_uuid in network.graph.values() for obj in by_uuid.values()),
                     key=lambda obj: (type(obj).__name__, _own_key(obj)))
    key = {obj: _own_key(obj) for obj in objects}
    owner_of, consumed, synthetic_cells = {}, {}, {}
    if terminals == 'folded':
        owner_of, consumed, synthetic_cells = _fold(cim, objects, key)
    rows = [obj for obj in objects if obj not in owner_of]

    class_order = _class_order(network, rows, owner_of, consumed, synthetic_cells, units, parts or {})
    by_class = {}
    for obj in rows:
        by_class.setdefault(type(obj).__name__, []).append(obj)
    position = {}
    for cls_name, _ in class_order:
        for obj in by_class[cls_name]:
            position[obj] = (len(position), '')
    for obj, owner in owner_of.items():
        position[obj] = (position[owner][0], key[obj])

    formative = {obj: dict(synthetic_cells.get(obj, {})) for obj in rows}
    updates = {obj: {} for obj in objects}
    list_updates = {}  # obj -> [(attr, target)]: one Update row each
    for obj in objects:
        _split_cells(obj, owner_of, consumed.get(obj, ()), position, units, formative, updates, list_updates)

    profile = cim.__name__.rsplit('.', 1)[-1]
    if validate._qualify(profile) != cim.__name__:
        raise ValueError(f"network.cim {cim.__name__!r} has no profile name a .cimtbl Profile = can give")
    master = document.Document(name, [], profile=profile, authority=get_model_authority(),
                               model=get_model_name() or None)
    part_documents = {}
    for cls_name, part in class_order:
        statement = _statement(network, cls_name, [(obj, formative[obj]) for obj in by_class[cls_name]], key, units)
        if part is None:
            master.statements.append(statement)
            continue
        if part not in part_documents:
            part_documents[part] = document.Document(part, [])
            master.statements.append(document.Import(part))
        part_documents[part].statements.append(statement)

    update_rows = {}
    for obj in sorted(objects, key=position.__getitem__):
        cells = [updates[obj]] if updates[obj] else []
        cells += [{attr: target} for attr, target in list_updates.get(obj, ())]
        if cells:
            update_rows.setdefault(type(obj).__name__, []).extend((obj, row_cells) for row_cells in cells)
    for cls_name, cls_rows in update_rows.items():
        master.statements.append(_statement(network, cls_name, cls_rows, key, units, update=True))
    return document.DocumentSet(master, list(part_documents.values()))


def _own_key(obj: object) -> str:
    """An object's row key: its mRID, else (Identity-only, or no mRID) its identifier."""
    mRID = getattr(obj, 'mRID', None)
    return mRID if mRID is not None else str(obj.identifier)


# --- folding ----------------------------------------------------------------

def _fold(cim, objects: list, key: dict) -> tuple[dict, dict, dict]:
    """Folded mode: ({folded object: the row object that makes it},
    {object: fields its row's binding sets}, {row object: {synthetic column:
    value}}). Renames the folded objects in `key`."""
    owner_of, consumed, synthetic_cells = {}, {}, {}
    taken = {mRID: obj for obj, mRID in key.items()}
    for obj in objects:
        if getattr(obj, 'mRID', None) is None:
            continue
        if isinstance(obj, cim.TransformerEnd):
            folded = _fold_end(cim, obj)
        elif isinstance(obj, cim.ConductingEquipment):
            folded = _fold_equipment(cim, obj)
        else:
            continue
        if folded is None:
            continue
        cells, row_consumed, generated = folded
        if any(made in owner_of or taken.get(mRID, made) is not made for made, mRID, _ in generated):
            continue  # already folded elsewhere, or a generated mRID is another object's: keep these explicit
        synthetic_cells[obj] = cells
        consumed[obj] = row_consumed
        for made, mRID, fields in generated:
            owner_of[made], consumed[made] = obj, fields
            del taken[key[made]]
            key[made] = taken[mRID] = mRID
    return owner_of, consumed, synthetic_cells


def _node_cells(terminals: list, container: object, single: bool) -> dict | None:
    """The node/bus columns that make these Terminals, or None if binding
    them would not give the same nodes (no node, or a node without a
    container that binding would put in the equipment's)."""
    cells = {}
    for terminal in terminals:
        if terminal.ConnectivityNode is None and terminal.TopologicalNode is None:
            return None
        for column, field in (('node', 'ConnectivityNode'), ('bus', 'TopologicalNode')):
            node = getattr(terminal, field)
            if node is None:
                continue
            if container is not None and node.ConnectivityNodeContainer is None:
                return None
            cells[column if single else f'{column}{terminal.sequenceNumber}'] = node
    return cells


def _fold_equipment(cim, equipment) -> tuple[dict, set, list] | None:
    """(synthetic cells, fields consumed on the row, [(folded object, its
    generated mRID, its consumed fields)]) for a ConductingEquipment whose
    Terminals and phase children its row can make, else None."""
    terminals = sorted(equipment.Terminals, key=lambda t: t.sequenceNumber or 0)
    if [t.sequenceNumber for t in terminals] not in ([1], [1, 2]):
        return None
    if any(t.TransformerEnd for t in terminals) or set(_SYNTHETIC_ORDER) & equipment.__dataclass_fields__.keys():
        return None  # an end's Terminal folds onto the end
    cells = _node_cells(terminals, getattr(equipment, 'EquipmentContainer', None), single=len(terminals) == 1)
    if cells is None:
        return None
    phases = _fold_phases(cim, equipment, terminals)
    if phases is None:
        return None
    phase_cells, children = phases
    generated = [(t, f'{equipment.mRID}_T{t.sequenceNumber}', _TERMINAL_FIELDS) for t in terminals]
    return {**cells, **phase_cells}, set(), generated + children


def _fold_phases(cim, equipment, terminals: list) -> tuple[dict, list] | None:
    """(phases cells, [(child, generated mRID, consumed fields)]) when the
    equipment's <Class>Phase children and Terminal phases are what a phases
    cell makes; ({}, []) when it has neither (balanced); else None."""
    codes = [t.phases.value if t.phases is not None else None for t in terminals]
    child_cls = connectivity.phase_cls(cim, equipment)
    children = []
    if child_cls is not None:
        parent_field = connectivity._parent_assoc_field(child_cls, equipment)
        children = getattr(equipment, child_cls.__dataclass_fields__[parent_field].metadata['inverse'].split('.')[1])
    if not children:
        return ({}, []) if codes == [None] * len(codes) else None

    hints = graph_write.type_hints(child_cls)
    has_sides = 'phaseSide1' in hints
    phase_fields = ('phaseSide1', 'phaseSide2') if has_sides else ('phase',)
    if any(getattr(child, field) is None for child in children for field in phase_fields):
        return None
    children = sorted(children, key=lambda child: connectivity.PHASE_ORDER.index(getattr(child, phase_fields[0]).value))
    sequence_numbers = None  # the sequenceNumbers cell, for children not numbered 1..n
    if 'sequenceNumber' in hints:
        numbers = [child.sequenceNumber for child in children]
        if None in numbers:
            return None
        if sorted(numbers) == list(range(1, len(numbers) + 1)):
            children = sorted(children, key=lambda child: child.sequenceNumber)  # 1..n: written order is the sequence
        else:
            sequence_numbers = ' '.join(str(number) for number in numbers)
    if has_sides:
        side1 = ''.join(child.phaseSide1.value for child in children)
        side2 = ''.join(child.phaseSide2.value for child in children)
        cells = {'phases': side1} if side1 == side2 else {'phaseSide1': side1, 'phaseSide2': side2}
    elif getattr(equipment, 'phaseConnection', None) == cim.PhaseShuntConnectionKind.D:
        if codes[0] is None:
            return None
        from_phases = [child.phase.value for child in children]
        rest = [t for t in connectivity.parse_phases(codes[0].replace('s12', 's1s2')) if t not in from_phases]
        cells = {'phases': ''.join(from_phases + rest)}
    else:
        cells = {'phases': ''.join(child.phase.value for child in children)}
    if sequence_numbers is not None:
        cells['sequenceNumbers'] = sequence_numbers

    try:
        want_codes, _, plan = connectivity.phase_plan(cim, equipment, len(terminals), **cells)
    except ValueError:
        return None  # binding would reject this shorthand, so the children stay explicit
    if want_codes != codes or len(plan) != len(children):
        return None
    generated = []
    for child, (mRID, fields) in zip(children, plan):
        if any(getattr(child, field) != value for field, value in fields.items()):
            return None
        generated.append((child, mRID, {parent_field, *fields}))
    return cells, generated


def _fold_end(cim, end) -> tuple[dict, set, list] | None:
    """The `node`/`bus` (and `phases`) cells that make a TransformerEnd's
    Terminal on its transformer, as for _fold_equipment, else None."""
    owner_field = connectivity.end_owner_field(cim, end)
    transformer = getattr(end, owner_field) if owner_field is not None else None
    terminal = end.Terminal
    if transformer is None or terminal is None or end.endNumber is None:
        return None
    if terminal.ConductingEquipment is not transformer or terminal.sequenceNumber != end.endNumber:
        return None
    cells = _node_cells([terminal], transformer.EquipmentContainer, single=True)
    if cells is None:
        return None
    if 'phase' in graph_write.type_hints(type(end)):  # AutoTransformerEnd: binding copies its phase
        if terminal.phases != end.phase:
            return None
    elif terminal.phases is not None:
        cells['phases'] = terminal.phases.value.replace('s12', 's1s2')
    return cells, {'Terminal'}, [(terminal, f'{transformer.mRID}_T{end.endNumber}', _TERMINAL_FIELDS)]


# --- order ------------------------------------------------------------------

def _forced_side(field: dataclasses.Field, cim) -> bool:
    """A single-valued reference that only this side can write: its inverse
    is unserialized or list-valued."""
    cls_name, attr = field.metadata['inverse'].split('.')
    inverse = getattr(cim, cls_name).__dataclass_fields__.get(attr)
    return inverse is None or inverse.metadata.get('serialize') is False or 'list' in str(inverse.type)


def _class_order(network: GraphModel, rows: list, owner_of: dict, consumed: dict, synthetic_cells: dict,
                 units: dict, parts: dict) -> list[tuple[str, str | None]]:
    """[(class name, part or None)] in writing order."""
    cim = network.cim
    has_base_power = cim.BasePower in network.graph if hasattr(cim, 'BasePower') else False
    deps = {}
    for obj in rows:
        cls_deps = deps.setdefault(type(obj).__name__, set())
        targets = list(synthetic_cells.get(obj, {}).values())
        for field in dataclasses.fields(obj):
            value = getattr(obj, field.name)
            if value is None or field.name in consumed.get(obj, ()) or field.metadata.get('serialize') is False:
                continue
            if field.metadata.get('inverse') is not None and not isinstance(value, list) and _forced_side(field, cim):
                targets.append(value)
            elif isinstance(value, CIMUnit) and has_base_power and _column_unit(type(obj), field.name, units)[0] in ('pu', 'percent'):
                cls_deps.add('BasePower')
        for target in targets:
            if not isinstance(target, str):
                cls_deps.add(type(owner_of.get(target, target)).__name__)
    for cls_deps in deps.values():
        cls_deps &= deps.keys()

    part_of = {}
    for part, cls_names in parts.items():
        for cls_name in cls_names:
            if cls_name in part_of:
                raise ValueError(f"class {cls_name!r} is in parts {part_of[cls_name]!r} and {part!r}")
            part_of[cls_name] = part
    unit_of = {cls_name: ('part', part_of[cls_name]) if cls_name in part_of else ('class', cls_name) for cls_name in deps}
    unit_deps = {}
    for cls_name, cls_deps in deps.items():
        unit_deps.setdefault(unit_of[cls_name], set()).update(unit_of[d] for d in cls_deps if unit_of[d] != unit_of[cls_name])
    looping = sorted(unit for unit in unit_deps if unit[0] == 'part' and unit in _reachable(unit, unit_deps))
    if looping:
        edges = sorted(f'{a} -> {b}' for a, cls_deps in deps.items() for b in cls_deps
                       if unit_of[a] != unit_of[b] and {unit_of[a], unit_of[b]} & set(looping))
        raise ValueError(f"parts {[name for _, name in looping]} would import each other (a part may only reference "
                         f"what is written before it): {', '.join(edges)}")

    order = []
    for unit in _order(list(unit_deps), unit_deps):
        members = [cls_name for cls_name in deps if unit_of[cls_name] == unit]
        part = unit[1] if unit[0] == 'part' else None
        order += [(cls_name, part) for cls_name in _order(members, deps)]
    return order


def _reachable(start, deps: dict) -> set:
    seen, stack = set(), [start]
    while stack:
        for nxt in deps.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _order(nodes: list, deps: dict) -> list:
    """nodes, each after the nodes it depends on; ties by sort order. A cycle
    is broken at the node with the fewest unmet dependencies (its references
    to the rest become Update rows)."""
    remaining, order = set(nodes), []
    while remaining:
        unmet = {node: (deps.get(node, set()) & remaining) - {node} for node in remaining}
        pick = min(remaining, key=lambda node: (len(unmet[node]), node))
        order.append(pick)
        remaining.remove(pick)
    return order


# --- cells ------------------------------------------------------------------

def _column_unit(cls: type, attr: str, units: dict) -> tuple[str | None, str | None]:
    """The configured (unit, base) for cls.attr: `Class.attr` for the class
    or any base, else `attr`; (None, None) when not configured."""
    for base_cls in cls.__mro__:
        if f'{base_cls.__name__}.{attr}' in units:
            unit, _, base = units[f'{base_cls.__name__}.{attr}'].partition(' ')
            return unit, base or None
    if attr in units:
        unit, _, base = units[attr].partition(' ')
        return unit, base or None
    return None, None


def _split_cells(obj, owner_of: dict, consumed, position: dict, units: dict,
                 formative: dict, updates: dict, list_updates: dict) -> None:
    """Put each attribute of obj in its row (formative) or an Update."""
    in_row = obj not in owner_of
    deferred_reference = False
    relative = []
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if (value is None or value == [] or field.name in validate.KEY_FIELDS or field.name in consumed
                or field.metadata.get('serialize') is False):
            continue
        cells = formative[obj] if in_row else updates[obj]
        if field.metadata.get('inverse') is None:
            cells[field.name] = value
            if in_row and isinstance(value, CIMUnit) and _column_unit(type(obj), field.name, units)[0] in ('pu', 'percent'):
                relative.append(field.name)
            continue
        targets = value if isinstance(value, list) else [value]
        missing = [target for target in targets if target not in position]
        if missing:
            raise ValueError(f"{type(obj).__name__} {_own_key(obj)}.{field.name} references a "
                             f"{type(missing[0]).__name__} that is not in the model")
        cls_name, attr = field.metadata['inverse'].split('.')
        inverse = type(targets[0]).__dataclass_fields__.get(attr)
        inverse_written = inverse is not None and inverse.metadata.get('serialize') is not False
        if isinstance(value, list):
            if inverse_written and ('list' not in str(inverse.type) or inverse.metadata['inverse'] > field.metadata['inverse']):
                continue  # the other side writes it
            list_updates.setdefault(obj, []).extend((field.name, target) for target in value)
        elif inverse_written and 'list' not in str(inverse.type) and getattr(value, attr) is obj and position[value] > position[obj]:
            continue  # both sides are single and serialized: the later object writes it
        elif not in_row or position[value] >= position[obj]:
            updates[obj][field.name] = value
            deferred_reference = True
        else:
            formative[obj][field.name] = value
    if deferred_reference:  # a pu/percent cell needs its base references bound first
        for attr in relative:
            updates[obj][attr] = formative[obj].pop(attr)


def _number(value: float) -> str:
    """A float's exact text (repr, without a trailing .0)."""
    text = repr(float(value))
    return text[:-2] if text.endswith('.0') else text


def _quantity(value: float) -> str:
    """A quantity's text: the fewest significant digits within _REL_TOL."""
    for digits in range(1, 17):
        rounded = float(f'{value:.{digits}g}')
        if math.isclose(rounded, value, rel_tol=_REL_TOL):
            return _number(rounded)
    return _number(value)


def _digits(text: str) -> int:
    return len(text.replace('.', ''))


def _auto_unit(values: list[CIMUnit]) -> str | None:
    """The unit (None: SI) that puts the column's median in [1, 1000) (12.47
    kV, 10 kW, 152.4 m), else the one with the fewest digits (0.3 ohm/km over
    0.0003 ohm/m); SI on a tie. The readable units are all multiples of SI."""
    candidates = [(None, 1.0)] + [(unit, type(values[0])(1.0, unit).value)
                                  for unit in _READABLE_UNITS.get(values[0].unit.name, ())]
    median = statistics.median(abs(v.value) for v in values)
    for unit, factor in candidates:
        if 1 <= median / factor < 1000:
            return unit
    return min(candidates, key=lambda c: sum(_digits(_quantity(v.value / c[1])) for v in values))[0]


def _text(value: object, key: dict) -> str:
    """A scalar or reference cell's text, as validate._coerce reads it back."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if dataclasses.is_dataclass(value):
        if getattr(value, 'mRID', None) is None:
            raise ValueError(f"a {type(value).__name__} without an mRID can't be referenced from .cimtbl")
        return key[value]
    raise TypeError(f"no .cimtbl text for {value!r}")


def _statement(network: GraphModel, cls_name: str, rows: list[tuple[object, dict]], key: dict, units: dict,
               *, update: bool = False) -> document.Table | document.ObjectStatement:
    """One Table (or Object, for a single row) of cls_name rows."""
    cls = getattr(network.cim, cls_name)
    present = {name for _, cells in rows for name in cells}
    key_column = 'mRID' if 'mRID' in cls.__dataclass_fields__ else 'identifier'
    names = [key_column, *(['name'] if 'name' in present else []), *(c for c in _SYNTHETIC_ORDER if c in present)]
    names += [f.name for f in dataclasses.fields(cls) if f.name in present and f.name not in names]

    columns = []
    for name in names:
        values = [cells[name] for _, cells in rows if name in cells]
        unit, base = _column_unit(cls, name, units)
        if values and isinstance(values[0], CIMUnit) and unit is None:
            unit = _auto_unit(values)
        columns.append((name, unit, base))

    table_rows = []
    for obj, cells in rows:
        row = [key[obj]]
        for name, unit, base in columns[1:]:
            value = cells.get(name)
            if not isinstance(value, CIMUnit):
                row.append(None if value is None else _text(value, key))
            elif unit is None:
                row.append(_quantity(value.value))
            else:
                row.append(_quantity(graph_write.get_qty(obj, name, unit, base=base, system_base=network.base_power)))
        table_rows.append(row)

    header = [document.Column(name, f'{unit} {base}' if base else unit) for name, unit, base in columns]
    if len(table_rows) == 1:
        return document.ObjectStatement(cls_name, list(zip(header, table_rows[0])), update=update)
    return document.Table(cls_name, header, [document.Row(row) for row in table_rows], update=update)
