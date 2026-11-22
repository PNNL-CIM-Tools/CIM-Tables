# When something is wrong

Every row is checked against the CIM profile before it changes the model, and every error names the file and line
it came from. Loading stops at the first error: nothing is skipped and nothing is guessed.

## Examples

These are real messages from loading the [quick start](quickstart.ipynb)'s `tiny_feeder.cimtbl` with one mistake
each.

| Mistake | Message |
|---|---|
| Misspelled column | `tiny_feeder.cimtbl:19: EnergyConsumerRow has no attribute 'qq' - did you mean 'q'?` |
| Misspelled class | `tiny_feeder.cimtbl:7: unknown CIM class 'Feedr' (not in profile 'cim18v17')` |
| Wrong type | `tiny_feeder.cimtbl:16: 'length' expected a number, got 'five'` |
| Reference to an object not declared (yet) | `tiny_feeder.cimtbl:12: 'source': BaseVoltage 'bv_13kv' (BaseVoltage) not found in graph (declare it before rows that reference it)` |
| mRID used twice | `tiny_feeder.cimtbl:20: mRID 'ln_trunk' of EnergyConsumer is already used by ACLineSegment (an mRID must be unique within the model)` |
| Bad phases | `tiny_feeder.cimtbl:20: phases 'CQ' is not an ordered set of A/B/C/s1/s2/N` |
| Syntax | `tiny_feeder.cimtbl:14:1: Unexpected token Token('VALUE', 'Tabel ACLineSegment: mRID') ...` |

A problem in an imported file names that file, and a missing import, an import cycle or an imported setting that
disagrees is reported at the `Import` line.

## The exception types

All three are `ValueError`s, so `except ValueError` catches any of them.

| Exception | Raised for |
|---|---|
| `cimtables.CimtblParseError` | Syntax, a missing or circular `Import`, a setting declared twice or disagreeing. `file:line:col`. |
| `cimtables.CimtblValidationError` | An unknown class or column, a value of the wrong type, before anything touches the model. `file:line`. |
| `cimtables.CimtblBindError` | Anything found while building the object: an undeclared reference, a reused mRID, bad phases, a missing rating. `file:line`; the original error is its `__cause__`. Also a `TypeError`. |
