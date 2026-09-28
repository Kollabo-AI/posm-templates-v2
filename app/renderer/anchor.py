from __future__ import annotations

from typing import Literal, TypeAlias


AnchorPosition: TypeAlias = Literal[
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]

_ANCHOR_FACTORS: dict[AnchorPosition, tuple[float, float]] = {
    "top-left": (0.0, 0.0),
    "top-center": (0.5, 0.0),
    "top-right": (1.0, 0.0),
    "center-left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "center-right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0),
    "bottom-center": (0.5, 1.0),
    "bottom-right": (1.0, 1.0),
}


def anchor_factors(anchor_position: AnchorPosition) -> tuple[float, float]:
    try:
        return _ANCHOR_FACTORS[anchor_position]
    except KeyError as error:
        valid_positions = ", ".join(_ANCHOR_FACTORS)
        raise ValueError(
            f"Unknown anchor position '{anchor_position}'. "
            f"Expected one of: {valid_positions}"
        ) from error
