"""cimtables: the .cimtbl DSL - parser, profile-reflective validator, and
graph-write/binder core for cim-graph (design: docs/CIMTBL_DESIGN.md). It
never imports cimbuilder, which depends on it.

`load()` is the package's own minimal, ObjectBuilder-free entry point: parse
a .cimtbl file, validate every record against the network's active CIM
profile, and bind each one onto the network via bind_row - the same function
a hand-written builder's from_table() delegates to, with no builder
subclassing required at all. A consumer who only wants ".cimtbl in, live
cim-graph objects out" never needs anything else from this package."""

import os
from importlib.metadata import version

from cimgraph.core import get_cim_profile, get_model_authority, get_model_name, set_model_scope
from cimgraph.models import BusBranchModel, FeederModel, GraphModel, NodeBreakerModel

from cimtables import connectivity, document, graph_write, impedance, synthetic, validate
from cimtables.binder import CimtblBindError, bind_row
from cimtables.diff import diff_models
from cimtables.parser import CimtblParseError, Header, parse_file, parse_text
from cimtables.records import ImportRecord, RawRecord
from cimtables.units import Qty
from cimtables.validate import CimtblValidationError

__version__ = version('cimtables')

__all__ = [
    'bind_row', 'connectivity', 'document', 'graph_write', 'impedance', 'synthetic', 'validate',
    'Header', 'parse_file', 'parse_text', 'ImportRecord', 'RawRecord', 'Qty',
    'CimtblParseError', 'CimtblValidationError', 'CimtblBindError', 'diff_models', 'load', 'load_text', 'load_model',
]


def load(path, network):
    """Parse, validate, and bind every record in a .cimtbl file onto an
    already-constructed network; returns the live cim-graph objects in file
    order. No ObjectBuilder subclass involved - every row goes through the
    same bind_row() a builder's from_table delegates to.

    The file's Authority=/Model= set the model scope its UUIDs are seeded
    under; its Profile= (if any) must be the network's profile."""
    records, header = parse_file(path)
    return _bind_records(network, records, header, str(path))


def load_text(text: str, network, source_file: str = '<text>'):
    """load() for .cimtbl source already in memory, e.g. a snippet in a
    script or test; returns the bound objects in order. Import statements
    need a file to resolve against, so they raise here - use load()."""
    records, header = parse_text(text, source_file=source_file)
    for record in records:
        if isinstance(record, ImportRecord):
            raise ValueError(f"{source_file}:{record.source_line}: Import {record.path!r} needs a file; use load(path, network)")
    return _bind_records(network, records, header, source_file)


def load_model(path, model_cls: type[GraphModel] | None = None) -> GraphModel:
    """A .cimtbl master file -> a new in-memory GraphModel holding its objects.

    The file's Profile= (if any) selects the CIM profile (CIMG_CIM_PROFILE),
    and its Authority=/Model= the model scope (see load). The model has no
    database connection, and no container while rows are bound, so no
    EquipmentContainer is set that the file doesn't declare. Its container is
    then the file's one Feeder (a FeederModel), else its one Substation (a
    NodeBreakerModel), else None (a NodeBreakerModel, e.g. a lone padmount).

    model_cls=BusBranchModel (PSS/E RAW, MATPOWER) builds that instead, its
    container the file's one GeographicalRegion, else None."""
    if model_cls not in (None, BusBranchModel):
        raise ValueError(f"model_cls must be None (chosen from the file) or BusBranchModel, not {model_cls.__name__}")
    records, header = parse_file(path)
    if header.profile is not None:
        os.environ['CIMG_CIM_PROFILE'] = header.profile
    get_cim_profile.cache_clear()
    _set_model_scope(header)
    network = (model_cls or NodeBreakerModel)(container=None, connection=None, distributed=False)
    for record in records:
        bind_row(network, validate.validate(record), update=record.form == 'Update')
    if model_cls is BusBranchModel:
        regions = list(network.graph.get(network.cim.GeographicalRegion, {}).values())
        network.container = regions[0] if len(regions) == 1 else None
        return network

    feeders = list(network.graph.get(network.cim.Feeder, {}).values())
    substations = list(network.graph.get(network.cim.Substation, {}).values())
    if len(feeders) == 1:
        return FeederModel(container=feeders[0], connection=None, distributed=False, graph=network.graph)
    if len(substations) == 1:
        network.container = substations[0]
    return network


def _bind_records(network, records: list[RawRecord], header: Header, source: str) -> list:
    """Validate and bind records onto network under the file's model scope,
    after checking the file's Profile= (if any) is network.cim's: rows are
    validated against the file's profile but bound with network.cim's classes."""
    if header.profile is not None and validate._cim_module(header.profile) is not network.cim:
        raise CimtblValidationError(
            f"{source}: Profile = {header.profile}, but the network's profile is {network.cim.__name__}"
        )
    _set_model_scope(header)
    return [bind_row(network, validate.validate(r, profile=header.profile), update=r.form == 'Update') for r in records]


def _set_model_scope(header: Header) -> None:
    """Each of Authority=/Model= the file declares replaces that half of the
    current model scope (CIMG_MODEL_AUTHORITY / CIMG_MODEL_NAME); undeclared
    ones are left as they are. The scope is process-global, like the profile."""
    if header.authority is None and header.model is None:
        return
    set_model_scope(header.authority if header.authority is not None else get_model_authority(),
                    header.model if header.model is not None else get_model_name())
