"""The validation gate: RawRecord -> <Class>Row, by pure-Python reflection
on the live CIM profile (no LinkML, no curated schema).

Validation reflects directly on the live CIM dataclass named by the record
(resolved from whichever profile Profile= selected, via cim-graph's own
get_cim_profile()) - any class in the selected profile is automatically
valid, with no per-version curation file to maintain. The only columns a CIM
profile can never explain are this package's own connectivity/phases
shorthand (synthetic.py), added to every <Class>Row unless the class has a
real attribute of the same name.

A field is classified the same way graph_write.py already classifies one
when writing to a live object: `metadata['serialize'] is False` marks a pure
reverse/derived association cimgraph itself never constructs from
(Measurements, Terminals, ...) - excluded entirely, never a column. Of the
rest, a field whose type union contains a CIMUnit subclass (Voltage,
ActivePower, ...) is a physical-quantity slot and becomes a Qty; a
field whose type union contains another CIM dataclass is a reference/FK slot
and stays an unresolved mRID string - resolving an mRID into a live object is
binder.py's job (graph_write.resolve), never validate's; everything else is a plain scalar, coerced from the record's raw
string with _coerce.
"""

import dataclasses
import difflib
import functools
import importlib
import os
import typing
from enum import Enum
from typing import Any

from cimgraph.core.env_vars import get_cim_profile
from cimprofiles import profile_module
from cimprofiles.units.units import CIMUnit

from cimtables.units import Qty
from cimtables import graph_write, records, synthetic

_row_dataclasses: dict[tuple[str, str], type] = {}
_SKIP_FIELDS = {'mRID', 'identifier'}


class CimtblValidationError(ValueError):
    """Fail-fast, profile-anchored validation error, as `<file>:<line>: <message>`."""


class _FieldKind:
    SCALAR = 'scalar'
    QUANTITY = 'quantity'
    REFERENCE = 'reference'


def _qualify(path: str) -> str:
    """A profile's full module path, resolved the way cim-graph's
    get_cim_profile resolves CIMG_CIM_PROFILE, so a short name ('cim17v40')
    is network.cim's module and not the deprecated cimgraph.data_profile shim."""
    return path if path.startswith(('cimprofiles.', 'cimgraph.')) else profile_module(path)


def _cim_module(profile: str | None):
    """The CIM profile module for `profile`.

    The active profile (`profile` is None, or equals CIMG_CIM_PROFILE) is
    always resolved through cim-graph's get_cim_profile() - the same call that
    produces network.cim - so comma-spec merged profiles work and the module
    is the identical object. get_cim_profile() takes no argument and is
    @cache'd off the env var, so the cache is cleared when the env var has
    changed since it was filled (what cim-graph's own connections do).

    Only an explicit profile that differs from the env var (e.g. validating
    against 'cim17v40' while the env names another) is imported directly, as a
    single module name; that avoids mutating process-global state."""
    active = os.environ.get('CIMG_CIM_PROFILE')
    if profile is not None and profile != active:
        return importlib.import_module(_qualify(profile))
    if active is None:
        raise CimtblValidationError(
            'no CIM profile selected - set Profile= in the .cimtbl file or CIMG_CIM_PROFILE'
        )
    spec, module = get_cim_profile()
    if spec != active:
        get_cim_profile.cache_clear()
        _, module = get_cim_profile()
    return module


def _classify(hint: Any) -> tuple[str, type]:
    """A field's (_FieldKind, extra) from its resolved type hint - extra is
    the CIMUnit subclass for a quantity, the CIM dataclass for a reference,
    or the real scalar type (str/int/float/bool/an Enum subclass) otherwise."""
    args = typing.get_args(hint) or (hint,)
    real_args = [a for a in args if isinstance(a, type) and a is not type(None)]
    for arg in real_args:
        if issubclass(arg, CIMUnit):
            return _FieldKind.QUANTITY, arg
    for arg in real_args:
        if dataclasses.is_dataclass(arg):
            return _FieldKind.REFERENCE, arg
    return _FieldKind.SCALAR, (real_args[0] if real_args else str)


@functools.cache
def _real_fields(cim_cls: type) -> dict[str, tuple[str, type]]:
    """{field_name: (kind, extra)} for every column a .cimtbl row may set on
    this CIM class - every dataclass field except pure reverse/derived
    associations (serialize=False) and identity bookkeeping (mRID/identifier,
    handled separately). Cached per class, like graph_write.type_hints; do
    not mutate the result."""
    hints = graph_write.type_hints(cim_cls)
    classified = {}
    for field in dataclasses.fields(cim_cls):
        if field.name in _SKIP_FIELDS or field.metadata.get('serialize') is False:
            continue
        classified[field.name] = _classify(hints[field.name])
    return classified


def reference_slots(row_cls_name: str, *, profile: str | None = None) -> set[str]:
    """Names of the class's FK slots - slots whose range is itself a CIM
    class (BaseVoltage, PerLengthImpedance, LoadResponse, ...) rather than a
    scalar type. These stay unresolved mRID strings in a <Class>Row and must
    never reach the CIM class's own constructor.

    Public: bind_row (binder.py) also uses this to tell
    an FK column apart from a plain scalar, with no per-class FK list to
    maintain."""
    cim_cls_name = row_cls_name.removesuffix('Row')
    cim_module = _cim_module(profile)
    cim_cls = getattr(cim_module, cim_cls_name)
    return {
        name for name, (kind, _) in _real_fields(cim_cls).items()
        if kind == _FieldKind.REFERENCE
    }


def reference_target_class(row_cls_name: str, slot_name: str, *, profile: str | None = None) -> str:
    """The CIM class name a reference slot resolves against (e.g.
    'BaseVoltage', 'LoadResponseCharacteristic', 'EquipmentContainer') - what
    bind_row needs to call graph_write.resolve() generically, with no
    per-class FK table to maintain."""
    cim_cls_name = row_cls_name.removesuffix('Row')
    cim_module = _cim_module(profile)
    cim_cls = getattr(cim_module, cim_cls_name)
    _, target_cls = _real_fields(cim_cls)[slot_name]
    return target_cls.__name__


def _coerce(raw: str, py_type: type) -> Any:
    """record.fields values are always raw strings off the parser -
    this is the str->real-type coercion a generated LinkML class used to do
    for us."""
    if py_type is bool:
        token = raw.strip().lower()
        if token not in ('true', 'false', '1', '0'):
            raise ValueError(f"expected a boolean (true/false/1/0), got {raw!r}")
        return token in ('true', '1')
    if py_type in (int, float):
        return py_type(raw)
    if isinstance(py_type, type) and issubclass(py_type, Enum):
        return py_type(raw)
    return raw


def _row_dataclass(row_cls_name: str, profile: str) -> type:
    """The plain <Class>Row dataclass, built once per (profile,
    class) from the live CIM dataclass's own fields plus the DSL's synthetic
    columns - reflects the real profile, so it can't drift from it."""
    key = (profile, row_cls_name)
    if key in _row_dataclasses:
        return _row_dataclasses[key]

    cim_cls_name = row_cls_name.removesuffix('Row')
    cim_module = _cim_module(profile)
    cim_cls = getattr(cim_module, cim_cls_name, None)
    if cim_cls is None:
        raise CimtblValidationError(
            f"unknown CIM class {cim_cls_name!r} (not in profile {profile!r})"
        )

    fields: list[tuple[str, Any, dataclasses.Field]] = [
        ('mRID', str | None, dataclasses.field()),
        ('identifier', str | None, dataclasses.field(default=None)),
    ]
    real_fields = _real_fields(cim_cls)
    for field_name, (kind, extra) in real_fields.items():
        if kind == _FieldKind.QUANTITY:
            field_type = Qty
        elif kind == _FieldKind.REFERENCE:
            field_type = str
        else:
            field_type = extra
        fields.append((field_name, field_type | None, dataclasses.field(default=None)))
    for col_name, col_type in synthetic.SYNTHETIC_COLUMNS.items():
        # A real CIM attribute wins over the DSL shorthand of the same name
        # (Terminal.phases is a PhaseCode, not the phase-children string)
        if col_name in real_fields:
            continue
        fields.append((col_name, col_type | None, dataclasses.field(default=None)))
    fields.append(('source_file', str, dataclasses.field(default='')))
    fields.append(('source_line', int, dataclasses.field(default=0)))

    row_cls = dataclasses.make_dataclass(row_cls_name, fields)
    _row_dataclasses[key] = row_cls
    return row_cls


def _closest_attribute_error(valid: list[str], bad_column: str, source_file: str, source_line: int, row_cls_name: str) -> CimtblValidationError:
    matches = difflib.get_close_matches(bad_column, valid, n=1)
    suggestion = f" - did you mean {matches[0]!r}?" if matches else ''
    return CimtblValidationError(
        f"{source_file}:{source_line}: {row_cls_name} has no attribute {bad_column!r}{suggestion}"
    )


def validate(record: records.RawRecord, *, profile: str | None = None) -> object:
    """RawRecord -> validated <Class>Row, or raises CimtblValidationError."""
    profile = profile or os.environ.get('CIMG_CIM_PROFILE')
    row_cls_name = f'{record.cim_class}Row'
    cim_cls_name = record.cim_class
    cim_module = _cim_module(profile)
    cim_cls = getattr(cim_module, cim_cls_name, None)
    if cim_cls is None:
        raise CimtblValidationError(
            f"{record.source_file}:{record.source_line}: "
            f"unknown CIM class {record.cim_class!r} (not in profile {profile!r})"
        )

    real_fields = _real_fields(cim_cls)
    # The row key: mRID on an IdentifiedObject, else (CIM 18 Identity-only classes,
    # e.g. PhaseImpedanceData) an optional identifier seeding a deterministic UUID
    has_mrid = 'mRID' in cim_cls.__dataclass_fields__
    key_column = 'mRID' if has_mrid else 'identifier'
    valid_columns = {*real_fields, *synthetic.SYNTHETIC_COLUMNS, key_column}

    if not has_mrid and 'mRID' in record.fields:
        raise CimtblValidationError(
            f"{record.source_file}:{record.source_line}: {record.cim_class} is not an "
            f"IdentifiedObject (no mRID) - key it with an identifier column instead"
        )
    for bad_column in record.fields:
        if bad_column not in valid_columns:
            raise _closest_attribute_error(
                sorted(valid_columns), bad_column, record.source_file, record.source_line, row_cls_name
            )

    # mRID is the row's key (unique within the model, CIM 18); name is optional data
    mRID = record.fields.get('mRID')
    if mRID is None and has_mrid:
        raise CimtblValidationError(
            f"{record.source_file}:{record.source_line}: {record.cim_class} row has no mRID"
        )

    row_cls = _row_dataclass(row_cls_name, profile)
    row_kwargs: dict[str, Any] = {
        'mRID': mRID,
        'identifier': record.fields.get('identifier'),
        'source_file': record.source_file,
        'source_line': record.source_line,
    }

    for col_name in synthetic.SYNTHETIC_COLUMNS:
        row_kwargs[col_name] = record.fields.get(col_name)

    for field_name, (kind, extra) in real_fields.items():
        raw = record.fields.get(field_name)
        if raw is None:
            row_kwargs[field_name] = None
        elif kind == _FieldKind.REFERENCE:
            row_kwargs[field_name] = raw
        elif kind == _FieldKind.QUANTITY:
            # `(pu system)` / `(percent system)`: a relative unit on the system
            # base instead of the class's own rating (PSS/E's SBASE impedances)
            unit, _, base = (record.units.get(field_name) or '').partition(' ')
            if base and (unit not in ('pu', 'percent') or base.strip() != 'system'):
                raise CimtblValidationError(
                    f"{record.source_file}:{record.source_line}: {field_name!r} unit "
                    f"{record.units[field_name]!r}: only pu/percent take a base, and only 'system'"
                )
            try:
                row_kwargs[field_name] = Qty(float(raw), unit, base='system' if base else None)
            except ValueError as exc:
                raise CimtblValidationError(
                    f"{record.source_file}:{record.source_line}: {field_name!r} expected a number, got {raw!r}"
                ) from exc
        else:
            try:
                row_kwargs[field_name] = _coerce(raw, extra)
            except ValueError as exc:
                raise CimtblValidationError(
                    f"{record.source_file}:{record.source_line}: {field_name!r}: {exc}"
                ) from exc

    return row_cls(**row_kwargs)
