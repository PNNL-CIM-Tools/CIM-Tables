# Installation

```bash
pip install --pre cim-tables
```

`cim-tables` is imported as `cimtables`. It needs Python 3.10 or newer, and `--pre` is required for now:
`cim-tables` is an alpha release and depends on pre-release versions of
[cim-graph](https://github.com/PNNL-CIM-Tools/CIM-Graph) and
[cim-profiles](https://github.com/PNNL-CIM-Tools/CIM-Profiles).

With [uv](https://docs.astral.sh/uv/):

```bash
uv add --prerelease allow cim-tables
```

## Check it works

```python
import cimtables
print(cimtables.__version__)
```

## Which CIM profile

A `.cimtbl` file names its profile on the first line, `Profile = cim18v17`, and `load_model` activates it. The
CIM 18 profile `cim18v17` is the one that is tested. Other profiles from cim-profiles work in principle, since
nothing in `cimtables` is profile-specific, but they are not yet covered by tests.

## Example models

[CIMTBL-Models](https://github.com/PNNL-CIM-Tools/CIMTBL-Models) holds grid models written in `.cimtbl`: several
versions of the IEEE 13-bus feeder, small examples (a padmount transformer, a regulator, an open-wye/open-delta
bank, a feeder boundary), and an IEEE 13 model tiered under a transmission system.
