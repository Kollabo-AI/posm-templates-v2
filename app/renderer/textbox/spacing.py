from __future__ import annotations

import numpy as np

from .bounds import TextRasterMask


def tight_horizontal_shift(left: TextRasterMask, right: TextRasterMask) -> float:
    left_pixels = np.asarray(left.image) > 0
    right_pixels = np.asarray(right.image) > 0

    occupied_left_rows = np.flatnonzero(left_pixels.any(axis=1))
    occupied_right_rows = np.flatnonzero(right_pixels.any(axis=1))
    if occupied_left_rows.size == 0 or occupied_right_rows.size == 0:
        return 0

    left_edges = left_pixels.shape[1] - 1 - np.argmax(
        left_pixels[:, ::-1],
        axis=1,
    )
    right_edges = np.argmax(right_pixels, axis=1)
    right_tops = right.box.t + occupied_right_rows
    minimum_clearance: float | None = None

    for left_row in occupied_left_rows:
        left_top = left.box.t + left_row
        vertically_overlapping = (
            (right_tops < left_top + 1)
            & (right_tops + 1 > left_top)
        )
        if not vertically_overlapping.any():
            continue

        overlapping_right_rows = occupied_right_rows[vertically_overlapping]
        clearance = float(
            (
                right.box.l
                + right_edges[overlapping_right_rows]
                - left.box.l
                - left_edges[left_row]
                - 1
            ).min()
        )
        if minimum_clearance is None or clearance < minimum_clearance:
            minimum_clearance = clearance

    if minimum_clearance is None:
        return 0
    return max(0.0, minimum_clearance)


def tight_vertical_shift(top: TextRasterMask, bottom: TextRasterMask) -> float:
    top_pixels = np.asarray(top.image) > 0
    bottom_pixels = np.asarray(bottom.image) > 0

    occupied_top_columns = np.flatnonzero(top_pixels.any(axis=0))
    occupied_bottom_columns = np.flatnonzero(bottom_pixels.any(axis=0))
    if occupied_top_columns.size == 0 or occupied_bottom_columns.size == 0:
        return 0

    top_edges = top_pixels.shape[0] - 1 - np.argmax(
        top_pixels[::-1, :],
        axis=0,
    )
    bottom_edges = np.argmax(bottom_pixels, axis=0)
    bottom_lefts = bottom.box.l + occupied_bottom_columns
    minimum_clearance: float | None = None

    for top_column in occupied_top_columns:
        top_left = top.box.l + top_column
        horizontally_overlapping = (
            (bottom_lefts < top_left + 1)
            & (bottom_lefts + 1 > top_left)
        )
        if not horizontally_overlapping.any():
            continue

        overlapping_bottom_columns = occupied_bottom_columns[horizontally_overlapping]
        clearance = float(
            (
                bottom.box.t
                + bottom_edges[overlapping_bottom_columns]
                - top.box.t
                - top_edges[top_column]
                - 1
            ).min()
        )
        if minimum_clearance is None or clearance < minimum_clearance:
            minimum_clearance = clearance

    if minimum_clearance is None:
        return 0
    return max(0.0, minimum_clearance)
