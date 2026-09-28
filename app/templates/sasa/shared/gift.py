from __future__ import annotations

from math import ceil, floor

import numpy as np
from PIL import Image

from .... import logger
from ....types import FabricObjects
from ....renderer import (
    Affine,
    BoundingBox,
    Circle,
    CircleStyle,
    Empty,
    FitBetween,
    Group,
    LayoutElement,
    Magnet,
    Named,
    Text,
    TextRows,
    TextStyle,
)
from ....renderer.place import place_mask
from ....renderer.text import split_text
from .product import PreparedProduct


_GWP_SLOT_OCCUPIED_MARGIN = 10
_GWP_BALL_MINIMUM_SIZE_RATIO = 0.9


class GWPGroupElement(LayoutElement):
    product: LayoutElement
    gwp_product: LayoutElement
    gwp_text: LayoutElement
    gwp_ball: LayoutElement

    def _components(self) -> tuple[LayoutElement, ...]:
        return self.product, self.gwp_product, self.gwp_text, self.gwp_ball

    def placement_boxes(self) -> list[BoundingBox]:
        return [
            box
            for component in self._components()
            for box in component.placement_boxes()
        ]

    def render(self) -> FabricObjects:
        objects: FabricObjects = []
        for component in self._components():
            objects.extend(component.render())
        return objects


def _boxes_overlap(first: BoundingBox, second: BoundingBox) -> bool:
    return (
        first.l < second.r
        and first.r > second.l
        and first.t < second.b
        and first.b > second.t
    )


def _require_placement_box(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError(f"{type(element).__name__} has no placement box")
    return box


def _largest_aspect_size(canvas: BoundingBox, width_ratio: float, height_ratio: float) -> tuple[int, int]:
    """
    Given a canvas and a desired aspect ratio (width_ratio:height_ratio), returns the largest width and height
    that fits within the canvas while maintaining the aspect ratio.
    """
    scale = min(canvas.w / width_ratio, canvas.h / height_ratio)
    return max(1, round(width_ratio * scale)), max(1, round(height_ratio * scale))


def _place_gwp_slots(
    ratio: tuple[float, float],
    canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    allow_overlap_fallback: bool,
) -> tuple[BoundingBox, BoundingBox]:
    width_ratio, height_ratio = ratio
    mask_size = _largest_aspect_size(canvas, width_ratio, height_ratio)
    mask = Image.new("RGBA", mask_size, (255, 255, 255, 255))
    pair_box = place_mask(
        mask=mask,
        canvas_box=canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        anchor_x=product_anchor_x,
        anchor_y=product_anchor_y,
        allow_overlap_fallback=allow_overlap_fallback,
    )

    is_vertical = height_ratio > width_ratio

    if is_vertical:
        side = min(pair_box.w, pair_box.h / 2)
        product_square = BoundingBox(l=pair_box.l, t=pair_box.t, w=side, h=side)
        gift_square = BoundingBox(l=pair_box.l, b=pair_box.b, w=side, h=side)
    else:
        side = min(pair_box.w / 2, pair_box.h)
        product_square = BoundingBox(l=pair_box.l, t=pair_box.t, w=side, h=side)
        gift_square = BoundingBox(r=pair_box.r, t=pair_box.t, w=side, h=side)
    return product_square, gift_square


def _place_gwp_slot(
    canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    allow_overlap_fallback: bool,
) -> BoundingBox:
    mask_size = _largest_aspect_size(canvas, 1, 1)
    mask = Image.new("RGBA", mask_size, (255, 255, 255, 255))
    slot_box = place_mask(
        mask=mask,
        canvas_box=canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        anchor_x=product_anchor_x,
        anchor_y=product_anchor_y,
        allow_overlap_fallback=allow_overlap_fallback,
    )
    side = min(slot_box.w, slot_box.h)
    return BoundingBox(l=slot_box.l, t=slot_box.t, w=side, h=side)


def _available_gwp_region(
    canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
) -> tuple[np.ndarray, int, int] | None:
    template_width, template_height = template_size
    border_margin = int((template_width + template_height) / 100.0)
    canvas_left = max(0, ceil(canvas.l + border_margin))
    canvas_top = max(0, ceil(canvas.t + border_margin))
    canvas_right = min(template_width, floor(canvas.r - border_margin))
    canvas_bottom = min(template_height, floor(canvas.b - border_margin))

    if canvas_right <= canvas_left or canvas_bottom <= canvas_top:
        return None

    available = np.ones(
        (canvas_bottom - canvas_top, canvas_right - canvas_left),
        dtype=bool,
    )
    for occupied_box in occupied_boxes:
        left = max(canvas_left, floor(occupied_box.l - _GWP_SLOT_OCCUPIED_MARGIN))
        top = max(canvas_top, floor(occupied_box.t - _GWP_SLOT_OCCUPIED_MARGIN))
        right = min(canvas_right, ceil(occupied_box.r + _GWP_SLOT_OCCUPIED_MARGIN))
        bottom = min(canvas_bottom, ceil(occupied_box.b + _GWP_SLOT_OCCUPIED_MARGIN))
        if right <= left or bottom <= top:
            continue
        available[
            top - canvas_top:bottom - canvas_top,
            left - canvas_left:right - canvas_left,
        ] = False
    return available, canvas_left, canvas_top


def _blocked_summed_area(available_region: np.ndarray) -> np.ndarray:
    blocked = np.logical_not(available_region).astype(np.int32)
    summed_area = np.zeros(
        (blocked.shape[0] + 1, blocked.shape[1] + 1),
        dtype=np.int64,
    )
    summed_area[1:, 1:] = blocked.cumsum(axis=0, dtype=np.int64).cumsum(
        axis=1,
        dtype=np.int64,
    )
    return summed_area


def _valid_square_positions(summed_area: np.ndarray, side: int) -> np.ndarray:
    region_height = summed_area.shape[0] - 1
    region_width = summed_area.shape[1] - 1
    if side < 1 or side > region_height or side > region_width:
        return np.zeros((0, 0), dtype=bool)

    blocked_counts = (
        summed_area[side:, side:]
        - summed_area[:-side, side:]
        - summed_area[side:, :-side]
        + summed_area[:-side, :-side]
    )
    return blocked_counts == 0


def _ordered_first_square_positions(
    valid_positions: np.ndarray,
    side: int,
) -> np.ndarray:
    height, width = valid_positions.shape
    southeast = np.logical_or.accumulate(
        np.logical_or.accumulate(valid_positions[::-1, ::-1], axis=0),
        axis=1,
    )[::-1, ::-1]
    has_second = np.zeros_like(valid_positions)
    if side < width:
        has_second[:, :width - side] |= southeast[:, side:]
    if side < height:
        has_second[:height - side, :] |= southeast[side:, :]
    return valid_positions & has_second


def _select_ordered_square_pair(
    valid_positions: np.ndarray,
    side: int,
    region_left: int,
    region_top: int,
    product_anchor_x: float,
    product_anchor_y: float,
) -> tuple[BoundingBox, BoundingBox] | None:
    first_positions = _ordered_first_square_positions(valid_positions, side)
    first_ys, first_xs = np.nonzero(first_positions)
    if first_xs.size == 0:
        return None

    first_center_xs = region_left + first_xs + side / 2
    first_center_ys = region_top + first_ys + side / 2
    first_scores = (
        (first_center_xs - product_anchor_x) ** 2
        + (first_center_ys - product_anchor_y) ** 2
    )
    first_index = int(np.argmin(first_scores))
    first_x = int(first_xs[first_index])
    first_y = int(first_ys[first_index])

    height, width = valid_positions.shape
    second_positions = np.zeros_like(valid_positions)
    if first_x + side < width:
        second_positions[first_y:, first_x + side:] = valid_positions[
            first_y:,
            first_x + side:,
        ]
    if first_y + side < height:
        second_positions[first_y + side:, first_x:] |= valid_positions[
            first_y + side:,
            first_x:,
        ]

    second_ys, second_xs = np.nonzero(second_positions)
    if second_xs.size == 0:
        return None
    second_scores = (
        (second_xs.astype(np.float64) - first_x) ** 2
        + (second_ys.astype(np.float64) - first_y) ** 2
    )
    second_index = int(np.argmin(second_scores))
    second_x = int(second_xs[second_index])
    second_y = int(second_ys[second_index])

    product_square = BoundingBox(
        l=region_left + first_x,
        t=region_top + first_y,
        w=side,
        h=side,
    )
    gift_square = BoundingBox(
        l=region_left + second_x,
        t=region_top + second_y,
        w=side,
        h=side,
    )
    return product_square, gift_square


def _find_ordered_square_pair(
    available_region: np.ndarray,
    region_left: int,
    region_top: int,
    product_anchor_x: float,
    product_anchor_y: float,
    minimum_side: int = 1,
) -> tuple[BoundingBox, BoundingBox] | None:
    if available_region.ndim != 2 or available_region.size == 0:
        return None

    summed_area = _blocked_summed_area(available_region)
    low = max(1, minimum_side)
    high = min(available_region.shape)
    best_side = 0

    while low <= high:
        side = (low + high) // 2
        valid_positions = _valid_square_positions(summed_area, side)
        if np.any(_ordered_first_square_positions(valid_positions, side)):
            best_side = side
            low = side + 1
        else:
            high = side - 1

    if best_side == 0:
        return None
    return _select_ordered_square_pair(
        valid_positions=_valid_square_positions(summed_area, best_side),
        side=best_side,
        region_left=region_left,
        region_top=region_top,
        product_anchor_x=product_anchor_x,
        product_anchor_y=product_anchor_y,
    )


def _select_square(
    valid_positions: np.ndarray,
    side: int,
    region_left: int,
    region_top: int,
    anchor_x: float,
    anchor_y: float,
) -> BoundingBox | None:
    ys, xs = np.nonzero(valid_positions)
    if xs.size == 0:
        return None

    center_xs = region_left + xs + side / 2
    center_ys = region_top + ys + side / 2
    scores = (center_xs - anchor_x) ** 2 + (center_ys - anchor_y) ** 2
    index = int(np.argmin(scores))
    return BoundingBox(
        l=region_left + int(xs[index]),
        t=region_top + int(ys[index]),
        w=side,
        h=side,
    )


def _find_largest_square(
    available_region: np.ndarray,
    region_left: int,
    region_top: int,
    anchor_x: float,
    anchor_y: float,
    minimum_side: int = 1,
) -> BoundingBox | None:
    if available_region.ndim != 2 or available_region.size == 0:
        return None

    summed_area = _blocked_summed_area(available_region)
    low = max(1, minimum_side)
    high = min(available_region.shape)
    best_side = 0

    while low <= high:
        side = (low + high) // 2
        if np.any(_valid_square_positions(summed_area, side)):
            best_side = side
            low = side + 1
        else:
            high = side - 1

    if best_side == 0:
        return None
    return _select_square(
        valid_positions=_valid_square_positions(summed_area, best_side),
        side=best_side,
        region_left=region_left,
        region_top=region_top,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
    )


def _find_irregular_gwp_slots(
    canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    minimum_side: int,
) -> tuple[BoundingBox, BoundingBox] | None:
    available_region = _available_gwp_region(
        canvas=canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
    )
    if available_region is None:
        return None
    available, region_left, region_top = available_region
    return _find_ordered_square_pair(
        available_region=available,
        region_left=region_left,
        region_top=region_top,
        product_anchor_x=product_anchor_x,
        product_anchor_y=product_anchor_y,
        minimum_side=minimum_side,
    )


def _find_irregular_gwp_slot(
    canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    minimum_side: int,
) -> BoundingBox | None:
    available_region = _available_gwp_region(
        canvas=canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
    )
    if available_region is None:
        return None
    available, region_left, region_top = available_region
    return _find_largest_square(
        available_region=available,
        region_left=region_left,
        region_top=region_top,
        anchor_x=product_anchor_x,
        anchor_y=product_anchor_y,
        minimum_side=minimum_side,
    )


def _expanded_square(square: BoundingBox, template_size: tuple[int, int], gwp_expansion_ratio: float) -> BoundingBox:
    if gwp_expansion_ratio <= 0:
        logger.warning(f"GWP expansion ratio is {gwp_expansion_ratio}, which is not positive. No expansion will be applied.")
        return square
    if gwp_expansion_ratio > 1:
        logger.warning(f"GWP expansion ratio is {gwp_expansion_ratio}, which is greater than 1. This may cause the expanded square to exceed the template size.")
        return BoundingBox(
            l=0,
            t=0,
            r=template_size[0],
            b=template_size[1],
        )
    template_width, template_height = template_size
    expansion_x = template_width * gwp_expansion_ratio
    expansion_y = template_height * gwp_expansion_ratio
    return BoundingBox(
        l=square.l - expansion_x,
        t=square.t - expansion_y,
        r=square.r + expansion_x,
        b=square.b + expansion_y,
    )


def _gwp_text_element(
    gwp_text: str,
    gift_square: BoundingBox,
    gwp_text_height_ratio: float,
    text_style: TextStyle,
    spacing: float,
    split_single_line: bool = True,
) -> LayoutElement:
    if not gwp_text:
        return Empty()
    lines = [line.strip() for line in gwp_text.splitlines() if line.strip()]
    if split_single_line and len(lines) == 1:
        lines = split_text(lines[0], 2).splitlines()

    line_height = text_style.font_size or gift_square.h * gwp_text_height_ratio
    text_rows = TextRows(
        text="\n".join(lines),
        box=BoundingBox(l=gift_square.l, t=0, w=gift_square.w, h=line_height),
        style=text_style,
        spacing=spacing,
    )
    return Named(
        element=FitBetween(
            text_rows,
            top=gift_square.t,
            bottom=gift_square.b,
            vertical_align="bottom",
        ),
        name="Gift description",
    )


def _gwp_text_compression(element: LayoutElement) -> float:
    if isinstance(element, Text):
        return element.style.compression
    if isinstance(element, (Affine, Named)):
        return _gwp_text_compression(element.element)
    if isinstance(element, Group):
        return min(
            (_gwp_text_compression(row) for row in element.elements),
            default=1.0,
        )
    return 1.0


def _redo_gwp_text_element(
    gwp_text: str,
    gift_square: BoundingBox,
    gwp_text_height_ratio: float,
    text_style: TextStyle,
    spacing: float,
    provisional_text: LayoutElement,
    occupied_boxes: list[BoundingBox],
) -> LayoutElement:
    lines = [line.strip() for line in gwp_text.splitlines() if line.strip()]
    if len(lines) != 1:
        return provisional_text

    one_line_text = _gwp_text_element(
        gwp_text=gwp_text,
        gift_square=gift_square,
        gwp_text_height_ratio=gwp_text_height_ratio,
        text_style=text_style,
        spacing=spacing,
        split_single_line=False,
    )
    minimum_compression = (
        text_style.minimum_compression
        if text_style.minimum_compression is not None
        else text_style.compression
    )
    if _gwp_text_compression(one_line_text) < minimum_compression - 1e-6:
        logger.debug("GWP text does not fit on one line at the allowed compression; using two lines.")
        return provisional_text

    placement_boxes = one_line_text.placement_boxes()
    if any(not gift_square.contains(box) for box in placement_boxes):
        logger.debug("GWP text does not fit inside the original text area; using two lines.")
        return provisional_text
    if any(
        _boxes_overlap(text_box, occupied_box)
        for text_box in placement_boxes
        for occupied_box in occupied_boxes
    ):
        logger.debug("GWP text overlaps placed content on one line; using two lines.")
        return provisional_text
    return one_line_text


def _gwp_ball(
    text: str,
    fill: str,
    anchor_x: float,
    anchor_y: float,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    ball_size: float,
    text_style: TextStyle,
    inset: float,
    allow_overlap_fallback: bool,
) -> LayoutElement:
    diameter = round(ball_size)
    ball_text = Text(
        text=text,
        box=BoundingBox(l=0, t=0, w=diameter * 5, h=diameter),
        style=text_style,
    )
    ball = Circle(
        element=Named(element=ball_text, name="Gift badge text"),
        box=BoundingBox(l=0, t=0, w=diameter, h=diameter),
        style=CircleStyle(fill_color=fill, outline_width=0, fit_inset=inset),
        name="Gift badge background",
        content_name="Gift badge text",
    )
    return Magnet(
        element=ball,
        canvas_box=template_canvas,
        template_size=template_size,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        avoid=occupied_boxes,
        allow_overlap_fallback=allow_overlap_fallback,
    )


def _place_gwp_candidate(
    *,
    product_box: BoundingBox,
    gift_box: BoundingBox,
    prepared_product: PreparedProduct,
    prepared_gwp_product: PreparedProduct,
    gwp_text: str,
    gwp_ball_text: str,
    gwp_ball_fill: str,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    gwp_expansion_ratio: float,
    gwp_text_height_ratio: float,
    gwp_text_style: TextStyle,
    gwp_text_spacing: float,
    gwp_ball_size: float,
    gwp_ball_text_style: TextStyle,
    gwp_ball_inset: float,
    allow_overlap_fallback: bool,
) -> GWPGroupElement | Empty:
    template_width, template_height = template_size
    gwp_text_element = _gwp_text_element(
        gwp_text,
        gift_box,
        gwp_text_height_ratio,
        gwp_text_style,
        gwp_text_spacing,
    )

    product_area = _expanded_square(
        product_box,
        template_size,
        gwp_expansion_ratio,
    ).black_out(template_width, template_height)
    gwp_area = _expanded_square(
        gift_box,
        template_size,
        gwp_expansion_ratio,
    ).black_out(template_width, template_height)

    product = prepared_product.place(
        canvas_box=template_canvas,
        template_size=template_size,
        occupied_boxes=[
            *occupied_boxes,
            *product_area,
        ],
        anchor_x=round(product_box.cx),
        anchor_y=round(product_box.cy),
        allow_overlap_fallback=allow_overlap_fallback,
        name="Product image",
    )

    gwp_product = prepared_gwp_product.place(
        canvas_box=template_canvas,
        template_size=template_size,
        occupied_boxes=[
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *product.placement_boxes(),
            *gwp_area,
        ],
        anchor_x=round(gift_box.r),
        anchor_y=round(gift_box.cy),
        allow_overlap_fallback=allow_overlap_fallback,
        name="Gift product image",
    )
    placed_gift_box = _require_placement_box(gwp_product)
    ball = _gwp_ball(
        text=gwp_ball_text,
        anchor_x=placed_gift_box.l,
        anchor_y=placed_gift_box.cy,
        template_canvas=template_canvas,
        template_size=template_size,
        occupied_boxes=[
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
            *gwp_area,
        ],
        fill=gwp_ball_fill,
        ball_size=gwp_ball_size,
        text_style=gwp_ball_text_style,
        inset=gwp_ball_inset,
        allow_overlap_fallback=allow_overlap_fallback,
    )
    placed_ball_box = _require_placement_box(ball)
    redo_ball = False
    minimum_ball_size = gwp_ball_size * _GWP_BALL_MINIMUM_SIZE_RATIO
    if min(placed_ball_box.w, placed_ball_box.h) < minimum_ball_size:
        logger.warning(
            f"GWP ball placement is smaller than expected (expected {gwp_ball_size}, "
            f"got {min(placed_ball_box.w, placed_ball_box.h)}). Redoing ball placement."
        )
        redo_ball = True
    if any(
        _boxes_overlap(placed_ball_box, occupied)
        for occupied in [
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
            *gwp_area,
        ]
    ):
        logger.warning("GWP ball placement overlaps with occupied boxes. Redoing ball placement.")
        redo_ball = True

    product = prepared_product.place(
        canvas_box=template_canvas,
        template_size=template_size,
        occupied_boxes=[
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *ball.placement_boxes(),
            *gwp_product.placement_boxes(),
        ],
        anchor_x=round(product_box.cx),
        anchor_y=round(product_box.cy),
        allow_overlap_fallback=allow_overlap_fallback,
        name="Product image",
    )

    if redo_ball:
        redone_ball = _gwp_ball(
            text=gwp_ball_text,
            anchor_x=placed_gift_box.l,
            anchor_y=placed_gift_box.cy,
            template_canvas=template_canvas,
            template_size=template_size,
            occupied_boxes=[
                *occupied_boxes,
                *gwp_text_element.placement_boxes(),
                *product.placement_boxes(),
                *gwp_product.placement_boxes(),
            ],
            fill=gwp_ball_fill,
            ball_size=gwp_ball_size,
            text_style=gwp_ball_text_style,
            inset=gwp_ball_inset,
            allow_overlap_fallback=allow_overlap_fallback,
        )
        redone_ball_box = _require_placement_box(redone_ball)
        redone_ball_size = min(redone_ball_box.w, redone_ball_box.h)
        if redone_ball_size < minimum_ball_size:
            logger.warning(
                f"Redone GWP ball is smaller than the required size "
                f"({redone_ball_size} < {minimum_ball_size}). Trying ball-first fallback."
            )
            ball = _gwp_ball(
                text=gwp_ball_text,
                anchor_x=redone_ball_box.cx,
                anchor_y=redone_ball_box.cy,
                template_canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=[
                    *occupied_boxes,
                    *gwp_text_element.placement_boxes(),
                    *product.placement_boxes(),
                ],
                fill=gwp_ball_fill,
                ball_size=gwp_ball_size,
                text_style=gwp_ball_text_style,
                inset=gwp_ball_inset,
                allow_overlap_fallback=allow_overlap_fallback,
            )
            placed_ball_box = _require_placement_box(ball)
            gwp_product = prepared_gwp_product.place(
                canvas_box=template_canvas,
                template_size=template_size,
                occupied_boxes=[
                    *occupied_boxes,
                    *gwp_text_element.placement_boxes(),
                    *product.placement_boxes(),
                    *ball.placement_boxes(),
                ],
                anchor_x=placed_ball_box.r,
                anchor_y=placed_ball_box.cy,
                allow_overlap_fallback=allow_overlap_fallback,
                anchor_position="center-left",
                name="Gift product image",
            )
            placed_gift_box = _require_placement_box(gwp_product)
            logger.info(
                f"Placed GWP ball first at {placed_ball_box}, then GWP product at "
                f"{placed_gift_box}."
            )
        else:
            ball = redone_ball
            placed_ball_box = redone_ball_box
            logger.info(f"Redoing GWP ball placement. New placement: {placed_ball_box}.")

    placed_ball_size = min(placed_ball_box.w, placed_ball_box.h)
    minimum_ball_size = gwp_ball_size * _GWP_BALL_MINIMUM_SIZE_RATIO
    if placed_ball_size < minimum_ball_size:
        logger.warning(
            f"GWP ball is smaller than 90% of its intended size "
            f"(intended diameter {gwp_ball_size}, actual diameter {placed_ball_size})."
        )

    gwp_text_element = _redo_gwp_text_element(
        gwp_text=gwp_text,
        gift_square=gift_box,
        gwp_text_height_ratio=gwp_text_height_ratio,
        text_style=gwp_text_style,
        spacing=gwp_text_spacing,
        provisional_text=gwp_text_element,
        occupied_boxes=[
            *occupied_boxes,
            *ball.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
        ],
    )

    return GWPGroupElement(
        product=product,
        gwp_product=gwp_product,
        gwp_text=gwp_text_element,
        gwp_ball=ball
    )


def _place_partial_gwp_candidate(
    *,
    product_box: BoundingBox | None,
    gift_box: BoundingBox,
    prepared_product: PreparedProduct | None,
    prepared_gwp_product: PreparedProduct | None,
    gwp_text: str,
    gwp_ball_text: str,
    gwp_ball_fill: str,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    gwp_expansion_ratio: float,
    gwp_text_height_ratio: float,
    gwp_text_style: TextStyle,
    gwp_text_spacing: float,
    gwp_ball_size: float,
    gwp_ball_text_style: TextStyle,
    gwp_ball_inset: float,
    allow_overlap_fallback: bool,
) -> GWPGroupElement:
    """Place a GWP composition when either product image role is absent."""

    template_width, template_height = template_size
    has_product = bool(prepared_product)
    has_gwp_product = bool(prepared_gwp_product)
    if has_product and product_box is None:
        raise ValueError("A product slot is required when product images are present")

    gwp_text_element = _gwp_text_element(
        gwp_text,
        gift_box,
        gwp_text_height_ratio,
        gwp_text_style,
        gwp_text_spacing,
    )
    gwp_area = _expanded_square(
        gift_box,
        template_size,
        gwp_expansion_ratio,
    ).black_out(template_width, template_height)

    product: LayoutElement = Empty()
    if has_product:
        assert prepared_product is not None and product_box is not None
        product_area = _expanded_square(
            product_box,
            template_size,
            gwp_expansion_ratio,
        ).black_out(template_width, template_height)
        product = prepared_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=[*occupied_boxes, *product_area],
            anchor_x=round(product_box.cx),
            anchor_y=round(product_box.cy),
            allow_overlap_fallback=allow_overlap_fallback,
            name="Product image",
        )

    gwp_product: LayoutElement = Empty()
    if has_gwp_product:
        assert prepared_gwp_product is not None
        gwp_product = prepared_gwp_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=[
                *occupied_boxes,
                *gwp_text_element.placement_boxes(),
                *product.placement_boxes(),
                *gwp_area,
            ],
            anchor_x=round(gift_box.r),
            anchor_y=round(gift_box.cy),
            allow_overlap_fallback=allow_overlap_fallback,
            name="Gift product image",
        )
        placed_gift_box = _require_placement_box(gwp_product)
        ball_anchor_x = placed_gift_box.l
        ball_anchor_y = placed_gift_box.cy
    else:
        ball_anchor_x = gift_box.cx
        ball_anchor_y = gift_box.cy

    ball = _gwp_ball(
        text=gwp_ball_text,
        anchor_x=ball_anchor_x,
        anchor_y=ball_anchor_y,
        template_canvas=template_canvas,
        template_size=template_size,
        occupied_boxes=[
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
            *gwp_area,
        ],
        fill=gwp_ball_fill,
        ball_size=gwp_ball_size,
        text_style=gwp_ball_text_style,
        inset=gwp_ball_inset,
        allow_overlap_fallback=allow_overlap_fallback,
    )
    placed_ball_box = _require_placement_box(ball)
    minimum_ball_size = gwp_ball_size * _GWP_BALL_MINIMUM_SIZE_RATIO
    redo_ball = False
    if min(placed_ball_box.w, placed_ball_box.h) < minimum_ball_size:
        logger.warning(
            f"GWP ball placement is smaller than expected (expected {gwp_ball_size}, "
            f"got {min(placed_ball_box.w, placed_ball_box.h)}). Redoing ball placement."
        )
        redo_ball = True
    if any(
        _boxes_overlap(placed_ball_box, occupied)
        for occupied in [
            *occupied_boxes,
            *gwp_text_element.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
            *gwp_area,
        ]
    ):
        logger.warning("GWP ball placement overlaps with occupied boxes. Redoing ball placement.")
        redo_ball = True

    if has_product:
        assert prepared_product is not None and product_box is not None
        product = prepared_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=[
                *occupied_boxes,
                *gwp_text_element.placement_boxes(),
                *ball.placement_boxes(),
                *gwp_product.placement_boxes(),
            ],
            anchor_x=round(product_box.cx),
            anchor_y=round(product_box.cy),
            allow_overlap_fallback=allow_overlap_fallback,
            name="Product image",
        )

    if redo_ball:
        redone_ball = _gwp_ball(
            text=gwp_ball_text,
            anchor_x=ball_anchor_x,
            anchor_y=ball_anchor_y,
            template_canvas=template_canvas,
            template_size=template_size,
            occupied_boxes=[
                *occupied_boxes,
                *gwp_text_element.placement_boxes(),
                *product.placement_boxes(),
                *gwp_product.placement_boxes(),
            ],
            fill=gwp_ball_fill,
            ball_size=gwp_ball_size,
            text_style=gwp_ball_text_style,
            inset=gwp_ball_inset,
            allow_overlap_fallback=allow_overlap_fallback,
        )
        redone_ball_box = _require_placement_box(redone_ball)
        redone_ball_size = min(redone_ball_box.w, redone_ball_box.h)
        if redone_ball_size < minimum_ball_size and has_gwp_product:
            logger.warning(
                f"Redone GWP ball is smaller than the required size "
                f"({redone_ball_size} < {minimum_ball_size}). Trying ball-first fallback."
            )
            ball = _gwp_ball(
                text=gwp_ball_text,
                anchor_x=redone_ball_box.cx,
                anchor_y=redone_ball_box.cy,
                template_canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=[
                    *occupied_boxes,
                    *gwp_text_element.placement_boxes(),
                    *product.placement_boxes(),
                ],
                fill=gwp_ball_fill,
                ball_size=gwp_ball_size,
                text_style=gwp_ball_text_style,
                inset=gwp_ball_inset,
                allow_overlap_fallback=allow_overlap_fallback,
            )
            placed_ball_box = _require_placement_box(ball)
            assert prepared_gwp_product is not None
            gwp_product = prepared_gwp_product.place(
                canvas_box=template_canvas,
                template_size=template_size,
                occupied_boxes=[
                    *occupied_boxes,
                    *gwp_text_element.placement_boxes(),
                    *product.placement_boxes(),
                    *ball.placement_boxes(),
                ],
                anchor_x=placed_ball_box.r,
                anchor_y=placed_ball_box.cy,
                allow_overlap_fallback=allow_overlap_fallback,
                anchor_position="center-left",
                name="Gift product image",
            )
            logger.info(
                f"Placed GWP ball first at {placed_ball_box}, then GWP product at "
                f"{_require_placement_box(gwp_product)}."
            )
        else:
            ball = redone_ball
            placed_ball_box = redone_ball_box
            logger.info(f"Redoing GWP ball placement. New placement: {placed_ball_box}.")

    placed_ball_size = min(placed_ball_box.w, placed_ball_box.h)
    if placed_ball_size < minimum_ball_size:
        logger.warning(
            f"GWP ball is smaller than 90% of its intended size "
            f"(intended diameter {gwp_ball_size}, actual diameter {placed_ball_size})."
        )

    gwp_text_element = _redo_gwp_text_element(
        gwp_text=gwp_text,
        gift_square=gift_box,
        gwp_text_height_ratio=gwp_text_height_ratio,
        text_style=gwp_text_style,
        spacing=gwp_text_spacing,
        provisional_text=gwp_text_element,
        occupied_boxes=[
            *occupied_boxes,
            *ball.placement_boxes(),
            *product.placement_boxes(),
            *gwp_product.placement_boxes(),
        ],
    )

    return GWPGroupElement(
        product=product,
        gwp_product=gwp_product,
        gwp_text=gwp_text_element,
        gwp_ball=ball,
    )


def _place_image_only_gwp_candidate(
    *,
    product_box: BoundingBox | None,
    gift_box: BoundingBox,
    prepared_product: PreparedProduct | None,
    prepared_gwp_product: PreparedProduct | None,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    gwp_expansion_ratio: float,
    allow_overlap_fallback: bool,
) -> GWPGroupElement:
    """Place whichever product-image roles are present, product first."""

    template_width, template_height = template_size
    product: LayoutElement = Empty()
    if prepared_product:
        if product_box is None:
            raise ValueError("A product slot is required when product images are present")
        product_area = _expanded_square(
            product_box,
            template_size,
            gwp_expansion_ratio,
        ).black_out(template_width, template_height)
        product = prepared_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=[*occupied_boxes, *product_area],
            anchor_x=round(product_box.cx),
            anchor_y=round(product_box.cy),
            allow_overlap_fallback=allow_overlap_fallback,
            name="Product image",
        )

    gwp_product: LayoutElement = Empty()
    if prepared_gwp_product:
        gift_area = _expanded_square(
            gift_box,
            template_size,
            gwp_expansion_ratio,
        ).black_out(template_width, template_height)
        gwp_product = prepared_gwp_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=[
                *occupied_boxes,
                *product.placement_boxes(),
                *gift_area,
            ],
            anchor_x=round(gift_box.cx),
            anchor_y=round(gift_box.cy),
            allow_overlap_fallback=allow_overlap_fallback,
            name="Gift product image",
        )

    return GWPGroupElement(
        product=product,
        gwp_product=gwp_product,
        gwp_text=Empty(),
        gwp_ball=Empty(),
    )


def _gwp_group_prepared(
    prepared_product: PreparedProduct | None,
    prepared_gwp_product: PreparedProduct | None,
    gwp_text: str | None,
    gwp_ball_text: str,
    gwp_ball_fill: str,
    gwp_ball_size: float,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    gwp_text_style: TextStyle,
    gwp_text_spacing: float,
    gwp_ball_text_style: TextStyle,
    gwp_ball_inset: float,
    gwp_expansion_ratio: float,
    gwp_text_height_ratio: float,
    allow_overlap_fallback: bool = True,
) -> GWPGroupElement | Empty:
    normalized_gwp_text = (gwp_text or "").strip()
    has_product = bool(prepared_product)
    has_gwp_product = bool(prepared_gwp_product)
    has_gwp_text = bool(normalized_gwp_text)
    if not (has_product or has_gwp_product or has_gwp_text):
        return Empty()

    minimum_side = max(1, ceil(gwp_ball_size))
    # P/G/T/GT share one slot; PG/PT/PGT reserve ordered product/gift slots.
    use_two_slots = has_product and (has_gwp_product or has_gwp_text)

    if use_two_slots:
        slot_pair = _find_irregular_gwp_slots(
            canvas=template_canvas,
            template_size=template_size,
            occupied_boxes=occupied_boxes,
            product_anchor_x=product_anchor_x,
            product_anchor_y=product_anchor_y,
            minimum_side=minimum_side,
        )
        if slot_pair is None:
            logger.warning(
                "Could not find two constrained GWP squares. Falling back to the established placement."
            )
            slot_pair = _place_gwp_slots(
                ratio=(1, 2),
                canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=occupied_boxes,
                product_anchor_x=product_anchor_x,
                product_anchor_y=product_anchor_y,
                allow_overlap_fallback=allow_overlap_fallback,
            )
        product_box, gift_box = slot_pair
    else:
        slot_box = _find_irregular_gwp_slot(
            canvas=template_canvas,
            template_size=template_size,
            occupied_boxes=occupied_boxes,
            product_anchor_x=product_anchor_x,
            product_anchor_y=product_anchor_y,
            minimum_side=minimum_side,
        )
        if slot_box is None:
            logger.warning(
                "Could not find a constrained GWP square. Falling back to the established placement."
            )
            slot_box = _place_gwp_slot(
                canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=occupied_boxes,
                product_anchor_x=product_anchor_x,
                product_anchor_y=product_anchor_y,
                allow_overlap_fallback=allow_overlap_fallback,
            )
        product_box = slot_box if has_product else None
        gift_box = slot_box

    if not has_gwp_text:
        return _place_image_only_gwp_candidate(
            product_box=product_box,
            gift_box=gift_box,
            prepared_product=prepared_product,
            prepared_gwp_product=prepared_gwp_product,
            template_canvas=template_canvas,
            template_size=template_size,
            occupied_boxes=occupied_boxes,
            gwp_expansion_ratio=gwp_expansion_ratio,
            allow_overlap_fallback=allow_overlap_fallback,
        )

    candidate_kwargs = dict(
        product_box=product_box,
        gift_box=gift_box,
        prepared_product=prepared_product,
        prepared_gwp_product=prepared_gwp_product,
        gwp_text=normalized_gwp_text,
        gwp_ball_text=gwp_ball_text,
        gwp_ball_fill=gwp_ball_fill,
        template_canvas=template_canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        gwp_expansion_ratio=gwp_expansion_ratio,
        gwp_text_height_ratio=gwp_text_height_ratio,
        gwp_text_style=gwp_text_style,
        gwp_text_spacing=gwp_text_spacing,
        gwp_ball_size=gwp_ball_size,
        gwp_ball_text_style=gwp_ball_text_style,
        gwp_ball_inset=gwp_ball_inset,
        allow_overlap_fallback=allow_overlap_fallback,
    )
    if has_product and has_gwp_product:
        assert product_box is not None
        return _place_gwp_candidate(**candidate_kwargs)

    return _place_partial_gwp_candidate(**candidate_kwargs)


def GWPGroup(
    product_images: list[Image.Image] | None,
    gwp_product_images: list[Image.Image] | None,
    gwp_text: str | None,
    gwp_ball_text: str,
    gwp_ball_fill: str,
    gwp_ball_size: float,
    template_canvas: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    product_anchor_x: float,
    product_anchor_y: float,
    gwp_text_style: TextStyle,
    gwp_text_spacing: float,
    gwp_ball_text_style: TextStyle,
    gwp_ball_inset: float,
    gwp_expansion_ratio: float,
    gwp_text_height_ratio: float,
    allow_overlap_fallback: bool = True,
) -> LayoutElement:
    """Place product and GWP fields according to their independent presence."""

    prepared_product = PreparedProduct(product_images or [])
    prepared_gwp_product = PreparedProduct(gwp_product_images or [])
    normalized_gwp_text = (gwp_text or "").strip()
    if not (prepared_gwp_product or normalized_gwp_text):
        if prepared_product:
            return prepared_product.place(
                canvas_box=template_canvas,
                template_size=template_size,
                occupied_boxes=occupied_boxes,
                anchor_x=product_anchor_x,
                anchor_y=product_anchor_y,
                allow_overlap_fallback=allow_overlap_fallback,
                name="Product image",
            )
        return Empty()

    return _gwp_group_prepared(
        prepared_product=prepared_product,
        prepared_gwp_product=prepared_gwp_product,
        gwp_text=normalized_gwp_text,
        gwp_ball_text=gwp_ball_text,
        gwp_ball_fill=gwp_ball_fill,
        gwp_ball_size=gwp_ball_size,
        template_canvas=template_canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        product_anchor_x=product_anchor_x,
        product_anchor_y=product_anchor_y,
        gwp_text_style=gwp_text_style,
        gwp_text_spacing=gwp_text_spacing,
        gwp_ball_text_style=gwp_ball_text_style,
        gwp_ball_inset=gwp_ball_inset,
        gwp_expansion_ratio=gwp_expansion_ratio,
        gwp_text_height_ratio=gwp_text_height_ratio,
        allow_overlap_fallback=allow_overlap_fallback,
    )
