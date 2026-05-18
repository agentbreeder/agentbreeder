"""Reusable Pydantic field types + validators.

Field aliases:
    TopKField, HopsField, SeedEntityLimitField, WeightField — pre-configured
    constrained types matching the audit's W1-04 bounds.

Validator factory:
    make_weights_sum_validator(name_a, name_b, tolerance=1e-6) — returns a
    model_validator that enforces ``getattr(self, name_a) + getattr(self, name_b)
    sums to 1.0 within tolerance``.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, model_validator

# --- Field-type aliases ------------------------------------------------------
TopKField = Annotated[int, Field(ge=1, le=1000)]
HopsField = Annotated[int | None, Field(default=None, ge=0, le=10)]
SeedEntityLimitField = Annotated[int, Field(ge=1, le=50)]
WeightField = Annotated[float, Field(ge=0.0, le=1.0)]


# --- Cross-field validator factory ------------------------------------------
def make_weights_sum_validator(
    name_a: str,
    name_b: str,
    *,
    tolerance: float = 1e-6,
):
    """Return a Pydantic model_validator(mode='after') that enforces sum-to-1.0.

    Use as::

        class Foo(BaseModel):
            vector_weight: WeightField = 0.7
            text_weight: WeightField = 0.3
            _check_weights = make_weights_sum_validator("vector_weight", "text_weight")
    """

    @model_validator(mode="after")
    def _validate(self: Any) -> Any:
        a = getattr(self, name_a)
        b = getattr(self, name_b)
        total = a + b
        if abs(total - 1.0) > tolerance:
            raise ValueError(f"{name_a} + {name_b} must sum to 1.0 (got {total:.6f})")
        return self

    return _validate
