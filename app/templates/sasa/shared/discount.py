from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ....types import FabricCanvas, FabricObject, FabricGroup, FabricImage, FabricObjects, FabricTextbox
from ....util import image_to_base64
from ....renderer import BoundingBox
from ....renderer import *
from ....renderer.helpers import _render_local_mask
from ....renderer.place import place_mask
from ....renderer.textbox.spacing import tight_vertical_shift
from ....renderer.textbox.bounds import TextRasterMask
from ....renderer.text import split_text
from .... import logger
from ....util.browser import fabric_to_png

from copy import deepcopy

import numpy as np
import cv2
from PIL import Image, ImageDraw
import math
import re


class DiscountBallTextStyle(BaseModel):
    """Typography and spacing used to render discount-ball text."""

    font_family: str
    fill: str
    text_fill: str
    primary_font_size: float
    secondary_font_size: float
    suffix_font_size: float
    primary_font_weight: int
    secondary_font_weight: int
    suffix_font_weight: int
    line_gap: float
    compression: float = 0.9
    minimum_compression: float | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


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


def _discount_text_style(
    style: DiscountBallTextStyle,
    font_size: float | None,
    font_weight: int,
    compression: float,
    vertical_align: VerticalAlign = "bottom",
) -> TextStyle:
    return TextStyle(
        font_family=style.font_family,
        font_size=font_size,
        font_weight=font_weight,
        fill=style.fill,
        vertical_align=vertical_align,
        compression=compression,
        minimum_compression=style.minimum_compression,
    )


def _format_discount_text_basic(
    text: str,
    style: DiscountBallTextStyle,
    current_top: float = 0.0,
) -> LayoutElement:
    match = re.fullmatch(r"(\d{1,2})折(\#|\*)?", text)
    if match is None:
        raise ValueError(f"Invalid basic discount text: '{text}'")

    spans: list[TextSpan] = []

    def append_text(
        value: str,
        font_size: float,
        font_weight: int,
        gap_after: float = 0.0,
        vertical_align: VerticalAlign = "bottom",
        align_to_previous: VerticalAlign | None = None,
        tracking: float = -50.0,
    ) -> None:
        spans.append(TextSpan(
            text=value,
            style=TextStyle(
                font_family=style.font_family,
                font_size=font_size,
                font_weight=font_weight,
                fill=style.text_fill,
                vertical_align=vertical_align,
                tracking=tracking,
                compression=style.compression,
                minimum_compression=style.minimum_compression,
            ),
            gap_after=gap_after,
            align_to_previous=align_to_previous
        ))

    discount, suffix = match.groups()
    append_text(
        discount,
        style.primary_font_size,
        style.primary_font_weight,
        gap_after=style.primary_font_size * 0.03,
        tracking=0,
    )
    append_text("折", style.secondary_font_size, style.secondary_font_weight)
    if suffix:
        append_text(
            suffix,
            style.suffix_font_size,
            style.suffix_font_weight,
            vertical_align="top",
            align_to_previous="top",
        )

    line_width = sum((span.font_size or 1) * max(1, len(span.text)) * 5 for span in spans)
    return TextLine(
        spans,
        box=BoundingBox(
            l=0,
            t=current_top,
            w=line_width,
            h=max((span.font_size or 1) for span in spans),
        ),
    )


def _format_discount_text_buy_x_get_y_free(
    text: str,
    style: DiscountBallTextStyle,
) -> LayoutElement:
    # Split the text into "買X" and "送Y"
    buy_part, get_part = text.split("送", 1)

    base_width = style.primary_font_size * max(5, len(buy_part.strip()), len(get_part.strip()))

    # Format the "買X" part
    buy_line = Text(
        text=buy_part.strip(),
        box=BoundingBox(l=0, t=0, w=base_width, h=style.primary_font_size),
        style=TextStyle(
            font_family=style.font_family,
            font_size=style.primary_font_size,
            font_weight=style.secondary_font_weight,
            fill=style.text_fill,
            text_align="center",
            tracking=0,
            compression=style.compression,
            minimum_compression=style.minimum_compression,
        )
    )

    # Format the "送Y" part
    buy_line_box = _require_placement_box(buy_line)
    get_line = Text(
        text="送" + get_part.strip(),
        box=BoundingBox(l=0, t=buy_line_box.b + style.line_gap, w=base_width, h=style.primary_font_size),
        style=TextStyle(
            font_family=style.font_family,
            font_size=style.primary_font_size,
            font_weight=style.secondary_font_weight,
            fill=style.text_fill,
            text_align="center",
            tracking=0,
            compression=style.compression,
            minimum_compression=style.minimum_compression,
        )
    )

    # Combine both lines into a group
    return Group(elements=[buy_line, get_line])


def _format_discount_text_generic_discount(
    text: str,
    style: DiscountBallTextStyle,
) -> LayoutElement:
    match = re.fullmatch(r"(.+?)(\d{1,2})折([#*])?", text)
    if not match:
        raise ValueError(f"Invalid generic discount text: '{text}'")
    top, bottom, suffix = match.groups()
    top_element = Text(
        text=top,
        box=BoundingBox(
            l=0,
            t=0,
            w=style.primary_font_size * max(1, len(top)) * 5,
            h=style.secondary_font_size,
        ),
        style=TextStyle(
            font_family=style.font_family,
            font_size=style.secondary_font_size,
            font_weight=style.secondary_font_weight,
            fill=style.text_fill,
            text_align="center",
            compression=style.compression,
            minimum_compression=style.minimum_compression,
        ),
    )
    bottom_element = _format_discount_text_basic(
        f"{bottom}折{suffix or ''}",
        style,
        current_top=0,
    )

    top_box = _require_placement_box(top_element)
    bottom_box = _require_placement_box(bottom_element)
    aligned_bottom = Affine(
        element=bottom_element,
        translate_x=top_box.cx - bottom_box.cx,
        translate_y=top_box.b - bottom_box.t,
    )

    aligned_bottom_box = _require_placement_box(aligned_bottom)
    top_mask = TextRasterMask(
        image=_render_local_mask(top_element, top_box).getchannel("A"),
        box=top_box,
    )
    bottom_mask = TextRasterMask(
        image=_render_local_mask(aligned_bottom, aligned_bottom_box).getchannel("A"),
        box=aligned_bottom_box,
    )
    shift = tight_vertical_shift(top_mask, bottom_mask)
    placed_bottom = Affine(
        element=aligned_bottom,
        translate_y=style.line_gap - shift,
    )
    return Group(elements=[top_element, placed_bottom])


def _format_discount_text(
    ball_text: str,
    style: DiscountBallTextStyle,
) -> LayoutElement:
    """Handles generic discount text formatting"""
    base_width = len(ball_text) * style.primary_font_size
    if "或以上" in ball_text:
        split_idx = ball_text.find("或以上") + 3
        top_text = ball_text[:split_idx]
        bottom_text = ball_text[split_idx:]
        top_font_size = style.secondary_font_size
        bottom_font_size = style.primary_font_size
        top_font_weight = style.secondary_font_weight
        bottom_font_weight = style.primary_font_weight
        top_tracking = TextStyle().tracking
        bottom_tracking = 0.0
    else:
        logger.warning(f"Unknown discount ball text format: '{ball_text}'")
        lines = split_text(ball_text, 2).split("\n")
        if len(lines) != 2:
            font_size = style.primary_font_size
            return Text(
                text=ball_text,
                box=BoundingBox(l=0, t=0, w=base_width, h=font_size),
                style=TextStyle(
                    font_family=style.font_family,
                    font_size=font_size,
                    font_weight=style.primary_font_weight,
                    fill=style.text_fill,
                    text_align="center",
                    tracking=0,
                    compression=style.compression,
                    minimum_compression=style.minimum_compression,
                )
            )
        top_text, bottom_text = lines
        top_font_size = style.secondary_font_size
        bottom_font_size = style.secondary_font_size
        top_font_weight = style.secondary_font_weight
        bottom_font_weight = style.secondary_font_weight
        top_tracking = TextStyle().tracking
        bottom_tracking = TextStyle().tracking

    line1 = Text(
        text=top_text,
        box=BoundingBox(l=0, t=0, w=base_width, h=top_font_size),
        style=TextStyle(
            font_family=style.font_family,
            font_size=top_font_size,
            font_weight=top_font_weight,
            fill=style.text_fill,
            text_align="center",
            tracking=top_tracking,
            compression=style.compression,
            minimum_compression=style.minimum_compression,
        )
    )
    line1_box = _require_placement_box(line1)
    line2 = Text(
        text=bottom_text,
        box=BoundingBox(l=0, t=line1_box.b + style.line_gap, w=base_width, h=bottom_font_size),
        style=TextStyle(
            font_family=style.font_family,
            font_size=bottom_font_size,
            font_weight=bottom_font_weight,
            fill=style.text_fill,
            text_align="center",
            tracking=bottom_tracking,
            compression=style.compression,
            minimum_compression=style.minimum_compression,
        )
    )
    return Group(elements=[line1, line2])


def _discount_ball_text(
    text: str,
    style: DiscountBallTextStyle,
) -> LayoutElement:
    ball_text = text.strip()

    if re.fullmatch(r"\d{1,2}折[#*]?", ball_text):
        return _format_discount_text_basic(ball_text, style)

    if re.match(r"買\d送\d", ball_text):
        return _format_discount_text_buy_x_get_y_free(ball_text, style)

    if re.fullmatch(r".+?\d{1,2}折[#*]?", ball_text):
        return _format_discount_text_generic_discount(ball_text, style)

    return _format_discount_text(ball_text, style)


def DiscountBall(
    text: str,
    anchor_x: float,
    anchor_y: float,
    diameter: int,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    fill_color: str,
    text_style: DiscountBallTextStyle,
    inset: float,
) -> LayoutElement:
    """Create and place a circular discount callout containing formatted text."""

    mask = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter - 1, diameter - 1), fill=(255, 255, 255, 255))
    placed_box = place_mask(
        mask=mask,
        canvas_box=canvas_box,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        scale_factor=1.0,
        fix_scale=True,
    )
    if min(placed_box.w, placed_box.h) < diameter - 1:
        logger.warning(
            f"Discount ball requires a {diameter}px diameter, but only "
            f"{min(placed_box.w, placed_box.h):.1f}px fits"
        )
    if any(_boxes_overlap(placed_box, occupied) for occupied in occupied_boxes):
        logger.warning(
            f"Discount ball at ({anchor_x:.1f}, {anchor_y:.1f}) overlaps with existing elements"
        )
    return Circle(
        element=Named(
            element=_discount_ball_text(text, text_style),
            name="Discount ball text",
        ),
        box=placed_box,
        style=CircleStyle(
            fill_color=fill_color,
            outline_color="#000000",
            outline_width=0,
            fit_inset=inset,
        ),
        name="Discount ball background",
        content_name="Discount ball text",
    )
