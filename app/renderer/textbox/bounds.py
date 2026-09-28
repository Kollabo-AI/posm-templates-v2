from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageColor

from ...types import FabricCanvas, FabricTextbox
from ...util.browser import fabric_to_png
from ...util.browser.engine import get_browser_font_context_key
from ..box import BoundingBox


@dataclass(frozen=True)
class TextRasterMask:
    image: Image.Image
    box: BoundingBox

    def translated(self, x: float = 0, y: float = 0) -> TextRasterMask:
        return TextRasterMask(
            image=self.image,
            box=BoundingBox(
                l=self.box.l + x,
                t=self.box.t + y,
                w=self.box.w,
                h=self.box.h,
            ),
        )


def _render_mask(text_obj: FabricTextbox) -> TextRasterMask:
    cache_key = _text_raster_cache_key(text_obj)
    if cache_key is not None:
        return _render_cached_mask(cache_key).translated(
            x=text_obj.left,
            y=text_obj.top,
        )
    return _render_mask_uncached(text_obj)


@dataclass(frozen=True)
class _TextRasterCacheKey:
    text: str
    origin_x: str
    origin_y: str
    width: float
    height: float
    scale_x: float
    scale_y: float
    angle: float
    stroke_width: float
    stroke_dash_array: tuple[float, ...] | None
    stroke_line_cap: str
    stroke_dash_offset: float
    stroke_line_join: str
    stroke_uniform: bool
    stroke_miter_limit: float
    flip_x: bool
    flip_y: bool
    fill_rule: str
    paint_first: str
    skew_x: float
    skew_y: float
    font_size: float
    font_weight: str
    text_align: str
    split_by_grapheme: bool
    line_height: float
    font_family: str
    underline: bool
    overline: bool
    linethrough: bool
    font_style: str
    char_spacing: float
    direction: str
    min_width: float
    has_fill: bool
    has_stroke: bool
    browser_font_context: tuple[tuple[str, str, int, str], ...]


def _text_raster_cache_key(
    text_obj: FabricTextbox,
) -> _TextRasterCacheKey | None:
    if (
        text_obj.styles
        or text_obj.model_extra
        or text_obj.shadow is not None
        or text_obj.backgroundColor
        or text_obj.textBackgroundColor
        or text_obj.path is not None
        or not text_obj.visible
        or text_obj.opacity != 1
        or text_obj.globalCompositeOperation != "source-over"
    ):
        return None

    has_fill = _opaque_paint_presence(text_obj.fill)
    has_stroke = _opaque_paint_presence(text_obj.stroke)
    if has_fill is None or has_stroke is None:
        return None

    numeric_values = (
        text_obj.left,
        text_obj.top,
        text_obj.width,
        text_obj.height,
        text_obj.scaleX,
        text_obj.scaleY,
        text_obj.angle,
        text_obj.strokeWidth,
        text_obj.strokeDashOffset,
        text_obj.strokeMiterLimit,
        text_obj.skewX,
        text_obj.skewY,
        text_obj.fontSize,
        text_obj.lineHeight,
        text_obj.charSpacing,
        text_obj.minWidth,
    )
    if not all(math.isfinite(value) for value in numeric_values):
        return None
    if text_obj.strokeWidth < 0:
        return None

    has_stroke = has_stroke and text_obj.strokeWidth > 0
    stroke_width = text_obj.strokeWidth if has_stroke else 0
    stroke_dash_array = (
        tuple(text_obj.strokeDashArray)
        if has_stroke and text_obj.strokeDashArray is not None
        else None
    )
    if stroke_dash_array is not None and not all(
        math.isfinite(value) for value in stroke_dash_array
    ):
        return None

    is_single_line = _is_unwrapped_single_line(text_obj)
    return _TextRasterCacheKey(
        text=text_obj.text,
        origin_x=text_obj.originX,
        origin_y=text_obj.originY,
        width=text_obj.width,
        height=text_obj.height,
        scale_x=text_obj.scaleX,
        scale_y=text_obj.scaleY,
        angle=text_obj.angle,
        stroke_width=stroke_width,
        stroke_dash_array=stroke_dash_array,
        stroke_line_cap=text_obj.strokeLineCap if has_stroke else "butt",
        stroke_dash_offset=text_obj.strokeDashOffset if has_stroke else 0,
        stroke_line_join=text_obj.strokeLineJoin if has_stroke else "miter",
        stroke_uniform=text_obj.strokeUniform if has_stroke else False,
        stroke_miter_limit=text_obj.strokeMiterLimit if has_stroke else 4,
        flip_x=text_obj.flipX,
        flip_y=text_obj.flipY,
        fill_rule=text_obj.fillRule,
        paint_first=text_obj.paintFirst if has_fill and has_stroke else "fill",
        skew_x=text_obj.skewX,
        skew_y=text_obj.skewY,
        font_size=text_obj.fontSize,
        font_weight=text_obj.fontWeight,
        text_align=text_obj.textAlign,
        split_by_grapheme=(
            False if is_single_line else text_obj.splitByGrapheme
        ),
        line_height=1 if is_single_line else text_obj.lineHeight,
        font_family=text_obj.fontFamily,
        underline=text_obj.underline,
        overline=text_obj.overline,
        linethrough=text_obj.linethrough,
        font_style=text_obj.fontStyle,
        char_spacing=text_obj.charSpacing,
        direction=text_obj.direction,
        min_width=text_obj.width if is_single_line else text_obj.minWidth,
        has_fill=has_fill,
        has_stroke=has_stroke,
        browser_font_context=get_browser_font_context_key(),
    )


def _is_unwrapped_single_line(text_obj: FabricTextbox) -> bool:
    if len(text_obj.text.splitlines()) != 1 or "\t" in text_obj.text:
        return False
    character_spacing = (
        max(0, len(text_obj.text) - 1)
        * text_obj.fontSize
        * max(0, text_obj.charSpacing)
        / 1000
    )
    estimated_width = len(text_obj.text) * text_obj.fontSize + character_spacing
    return text_obj.width >= max(text_obj.minWidth, estimated_width)


def _opaque_paint_presence(paint: object) -> bool | None:
    if paint is None or paint == "":
        return False
    if not isinstance(paint, str):
        return None
    try:
        alpha = ImageColor.getcolor(paint, "RGBA")[3]  # type: ignore
    except ValueError:
        return None
    if alpha == 255:
        return True
    if alpha == 0:
        return False
    return None


@lru_cache(maxsize=128)
def _render_cached_mask(cache_key: _TextRasterCacheKey) -> TextRasterMask:
    text_obj = FabricTextbox(
        text=cache_key.text,
        left=0,
        top=0,
        originX=cache_key.origin_x,
        originY=cache_key.origin_y,
        width=cache_key.width,
        height=cache_key.height,
        fill="black" if cache_key.has_fill else None,
        scaleX=cache_key.scale_x,
        scaleY=cache_key.scale_y,
        angle=cache_key.angle,
        stroke="black" if cache_key.has_stroke else None,
        strokeWidth=cache_key.stroke_width,
        strokeDashArray=(
            list(cache_key.stroke_dash_array)
            if cache_key.stroke_dash_array is not None
            else None
        ),
        strokeLineCap=cache_key.stroke_line_cap,
        strokeDashOffset=cache_key.stroke_dash_offset,
        strokeLineJoin=cache_key.stroke_line_join,
        strokeUniform=cache_key.stroke_uniform,
        strokeMiterLimit=cache_key.stroke_miter_limit,
        flipX=cache_key.flip_x,
        flipY=cache_key.flip_y,
        fillRule=cache_key.fill_rule,
        paintFirst=cache_key.paint_first,
        skewX=cache_key.skew_x,
        skewY=cache_key.skew_y,
        fontSize=cache_key.font_size,
        fontWeight=cache_key.font_weight,
        textAlign=cache_key.text_align,
        splitByGrapheme=cache_key.split_by_grapheme,
        lineHeight=cache_key.line_height,
        fontFamily=cache_key.font_family,
        underline=cache_key.underline,
        overline=cache_key.overline,
        linethrough=cache_key.linethrough,
        fontStyle=cache_key.font_style,
        charSpacing=cache_key.char_spacing,
        direction=cache_key.direction,
        minWidth=cache_key.min_width,
    )
    return _render_mask_uncached(text_obj)


def get_text_raster_cache_snapshot() -> dict[str, int | None]:
    """Return observable statistics for the warm-process text mask cache."""
    info = _render_cached_mask.cache_info()
    return {
        "items": info.currsize,
        "max_items": info.maxsize,
        "hits": info.hits,
        "misses": info.misses,
    }


def _render_mask_uncached(text_obj: FabricTextbox) -> TextRasterMask:
    text_width = abs(text_obj.width * text_obj.scaleX)
    line_count = max(1, len(text_obj.text.splitlines()))
    content_height = max(
        text_obj.height,
        text_obj.fontSize * text_obj.lineHeight * line_count,
    )
    text_height = abs(content_height * text_obj.scaleY)
    padding = math.ceil(
        max(128, text_obj.fontSize * 2, text_obj.strokeWidth + 8)
    )

    local_text_obj = text_obj.model_copy(update={"left": padding, "top": padding})
    width = max(1, math.ceil(text_width + padding * 2))
    height = max(1, math.ceil(text_height + padding * 2))
    canvas = FabricCanvas(objects=[local_text_obj], width=width, height=height)
    image = fabric_to_png(canvas)
    alpha = _alpha_channel(image)
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("Rendered text did not produce any visible pixels")
    left, top, right, bottom = bbox
    return TextRasterMask(
        image=alpha.crop(bbox),
        box=BoundingBox(
            l=text_obj.left + left - padding,
            t=text_obj.top + top - padding,
            w=right - left,
            h=bottom - top,
        ),
    )


def _render_bounds(text_obj: FabricTextbox) -> BoundingBox:
    return _render_mask(text_obj).box


def _alpha_channel(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        return image.getchannel("A")
    return image.convert("RGBA").getchannel("A")
