from __future__ import annotations

def score_product_scale(*, ph: float, pw: float, ch: float, cw: float) -> float:
    """Score product occupancy using the legacy canvas-relative scale."""

    if min(ph, pw, ch, cw) <= 0:
        return 0.0
    return float(max(0.0, min(ph / ch, pw / cw)))
