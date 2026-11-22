# User guide

`.cimtbl` is CIM written as tables. This guide covers writing a model and loading it from Python. To change
`cimtables` itself, see the [developer docs](../developer/index.md).

<div class="grid cards" markdown>

-   __Getting started__

    ---

    [Installation](installation.md) and a [quick start](quickstart.ipynb) notebook that loads a small feeder and
    looks inside it.

-   __Writing a model__

    ---

    [The format](format.md): statements, cells, references and `Import`. Then
    [connectivity and phases](connectivity.md), [units and impedance bases](units.md), and
    [error messages](errors.md).

-   __Using it from Python__

    ---

    The [Python API](python-api.md): `load_model`, `load`, `to_documents`, `document` and `diff_models`.
    [CIM XML to .cimtbl](round_trip.ipynb) converts an existing CIM model and loads it back.

</div>

## The idea in four lines

- Every table is a CIM class: `Table ACLineSegment:`.
- Every column header is a CIM attribute or association name: `length`, `BaseVoltage`, `PerLengthImpedance`.
- Every row is one CIM object.
- Units are written once, on the column header (`length (ft)`, `p (kW)`, `r0 (pu)`), and converted for you.

There is no mapping from a native format to CIM, because the vocabulary *is* CIM. If the profile has a class or
attribute you can write it; if it doesn't, the error points at the line.
