"""diff_models: compare two cim-graph models object by object, e.g. a
builder-built model against a reviewed .cimtbl golden (CIMHub R8, CIM-Builder
parity tests).

Objects are matched by (class name, mRID), or by identifier for an object
without an mRID (Identity-only classes), never by UUID alone: two models
built under different Authority/Model scopes give the same mRID different
UUIDs. (So an Identity-only object only matches across models under the same
scope; key it by an `identifier` column in .cimtbl to make it deterministic.)

Per matched object every serialized field is compared: CIMUnit values in SI
within rel_tol, other scalars exactly, a single association by its target's
key and a list association by the set of target keys. serialize=False
fields (reverse sides like BaseVoltage.ConductingEquipment) are skipped, as
the owning side already compares the same link.
"""

import dataclasses
import math

from cimgraph.models import GraphModel
from cimprofiles.units.units import CIMUnit


def _key(obj: object) -> str:
    mRID = getattr(obj, 'mRID', None)
    return mRID if mRID is not None else str(obj.identifier)


def diff_models(expected: GraphModel, actual: GraphModel, *, rel_tol: float = 1e-9) -> list[str]:
    """Differences as `Class key.attribute: expected X, got Y` (plus
    `Class key: missing` / `extra`), sorted; an empty list means equal."""
    objects = []
    for model in (expected, actual):
        by_key = {}
        for class_objects in model.graph.values():
            for obj in class_objects.values():
                by_key[(type(obj).__name__, _key(obj))] = obj
        objects.append(by_key)
    expected_objects, actual_objects = objects

    differences = [f'{cls} {key}: missing' for cls, key in expected_objects.keys() - actual_objects.keys()]
    differences += [f'{cls} {key}: extra' for cls, key in actual_objects.keys() - expected_objects.keys()]

    for (cls, key), expected_obj in expected_objects.items():
        actual_obj = actual_objects.get((cls, key))
        if actual_obj is None:
            continue
        for field in dataclasses.fields(expected_obj):
            if field.name in ('identifier', 'mRID') or field.metadata.get('serialize') is False:
                continue
            want, got = getattr(expected_obj, field.name), getattr(actual_obj, field.name)
            if field.metadata.get('inverse') is not None and (isinstance(want, list) or isinstance(got, list)):
                want = sorted(_key(target) for target in want or [])
                got = sorted(_key(target) for target in got or [])
            elif field.metadata.get('inverse') is not None:
                want = None if want is None else _key(want)
                got = None if got is None else _key(got)
            elif isinstance(want, CIMUnit) and isinstance(got, CIMUnit):
                if math.isclose(float(want), float(got), rel_tol=rel_tol):
                    continue
            if want != got:
                differences.append(f'{cls} {key}.{field.name}: expected {want!r}, got {got!r}')
    return sorted(differences)
