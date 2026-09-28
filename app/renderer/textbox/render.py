# This module take cares of translating a TextElement into a FabricTextbox, and calculating the bounding box of the rendered text.

from __future__ import annotations

import re

from .bounds import TextRasterMask, _render_bounds, _render_mask

from ... import logger
from ...types import FabricTextbox
from ..box import BoundingBox
from ..primitives import Text, TextAlign, TextStyle, TextVerticalAlign


_NUMERIC_GROUPING_COMMA_PATTERN = re.compile(r"(?<=\d),(?=\d)")


def render_text(text_element: Text) -> FabricTextbox:
    if not text_element.text:
        raise ValueError("TextElement cannot render empty text")

    text_obj = _make_textbox(
        text=text_element.text,
        box=text_element.box,
        style=text_element.style,
        font_size=text_element.style.font_size or text_element.box.h,
    )
    text_obj = _compress_to_width(
        text_obj=text_obj,
        box=text_element.box,
        compression=text_element.style.compression,
        minimum_compression=text_element.style.minimum_compression,
    )
    return _align_to_box(
        text_obj=text_obj,
        box=text_element.box,
        text_align=text_element.style.text_align,
        vertical_align=text_element.style.vertical_align,
    )


def text_placement_box(text_element: Text) -> list[BoundingBox]:
    bounds = _render_bounds(render_text(text_element))
    return [BoundingBox(l=bounds.l, t=bounds.t, w=bounds.w, h=bounds.h)]


def render_text_mask(text_element: Text) -> TextRasterMask:
    return _render_mask(render_text(text_element))


def _compress_to_width(
    text_obj: FabricTextbox,
    box: BoundingBox,
    compression: float,
    minimum_compression: float | None,
) -> FabricTextbox:
    scale_x = text_obj.scaleX * compression
    text_obj = text_obj.model_copy(update={"scaleX": scale_x})

    bounds = _render_bounds(text_obj)
    if bounds.w > box.w:
        scale_x *= box.w / bounds.w
        text_obj = text_obj.model_copy(update={"scaleX": scale_x})

    if minimum_compression is not None and scale_x < minimum_compression - 1e-6:
        logger.warning(
            f"Text '{text_obj.text}' requires {scale_x:.3f} compression, "
            f"below the allowed {minimum_compression:.3f}"
        )

    if scale_x < 0.75:
        logger.warning(f"Text '{text_obj.text}' is compressed to {scale_x:.2f}x, which may be too small to read")
    return text_obj


def _align_to_box(
    text_obj: FabricTextbox,
    box: BoundingBox,
    text_align: TextAlign,
    vertical_align: TextVerticalAlign,
) -> FabricTextbox:
    for _ in range(2):
        bounds = _render_bounds(text_obj)
        vertical_bounds = _vertical_alignment_bounds(
            text_obj,
            bounds,
            vertical_align,
        )

        if text_align == "left":
            desired_left = box.l
        elif text_align == "center":
            desired_left = box.cx - bounds.w / 2
        else:
            desired_left = box.r - bounds.w

        if vertical_align == "top":
            desired_top = box.t
        elif vertical_align == "center":
            desired_top = box.cy - vertical_bounds.h / 2
        elif vertical_align == "bottom":
            desired_top = box.b - vertical_bounds.h
        else:
            desired_top = vertical_bounds.t

        text_obj = text_obj.model_copy(
            update={
                "left": text_obj.left + desired_left - bounds.l,
                "top": text_obj.top + desired_top - vertical_bounds.t,
            }
        )
    return text_obj


def _vertical_alignment_bounds(
    text_obj: FabricTextbox,
    bounds: BoundingBox,
    vertical_align: TextVerticalAlign,
) -> BoundingBox:
    if vertical_align != "bottom":
        return bounds

    alignment_text = _NUMERIC_GROUPING_COMMA_PATTERN.sub("", text_obj.text)
    if alignment_text == text_obj.text or not alignment_text:
        return bounds
    return _render_bounds(text_obj.model_copy(update={"text": alignment_text}))


def _make_textbox(
    text: str,
    box: BoundingBox,
    style: TextStyle,
    font_size: float,
) -> FabricTextbox:
    has_border = style.border_thickness > 0
    return FabricTextbox(
        text=text,
        left=box.l,
        top=box.t,
        width=_draft_text_width(text=text, box_width=box.w, font_size=font_size),
        height=box.h,
        fill=style.fill,
        fontSize=font_size,
        fontFamily=style.font_family,
        fontWeight=str(style.font_weight),
        fontStyle=style.font_style,
        underline=style.underline,
        textAlign=style.text_align,
        charSpacing=style.tracking,
        stroke=style.border_color if has_border else None,
        strokeWidth=style.border_thickness if has_border else 0,
        strokeLineCap=style.border_line_cap if has_border else "butt",
        strokeLineJoin=style.border_line_join if has_border else "miter",
        paintFirst="stroke" if has_border else "fill",
    )


def _draft_text_width(text: str, box_width: float, font_size: float) -> float:
    longest_line = max(text.splitlines() or [text], key=len)
    return max(box_width, len(longest_line) * font_size)
