"""Qty: the value + unit carrier for a `(unit)` column.

validate.py puts one in every unit-bearing <Class>Row field; the conversion
to a CIMUnit (pu/percent through impedance.z_base) is graph_write.set_attr's.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Qty:
    """Value + unit carrier. Mirrors the .cimtbl header 'r (ohm)'.

    base: which base a RELATIVE unit (pu/percent) is against.
      None -> chosen by class shape (impedance.z_base: the rating pair, else
      the system base); 'system' -> always the system base, from a
      `(pu system)` / `(percent system)` header suffix.
    """

    value: float
    unit: str
    base: str | None = None

    def is_relative(self) -> bool:
        return self.unit in ('pu', 'percent')
