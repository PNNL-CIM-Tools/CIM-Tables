"""Qty carrier + %Z / pu / ohm normalization helpers (design: CIMTBL_DESIGN.md §4.3).

Qty itself is needed starting Phase 2 (it's a field type on the generated
<Class>Row dataclasses, §12.2); the to_cimunit/z_base conversion helpers are
Phase 5b.
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
