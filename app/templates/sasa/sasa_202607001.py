from __future__ import annotations

import math
import os
import re
import unicodedata
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image


from ... import logger
from ...types import FabricCanvas, FabricImage, FabricObjects, PosmBinding
from ...util import image_to_base64
from ...util.model import score_product_scale

from ...base import POSMImplementation
from ...native import Bundle
from ...schema import CreateParams, GenerationResult
from ...util import image_to_base64, load_image_from_url
from ...util.browser import (
    ALIBABA_PUHUITI_FONT_FACES,
    fabric_to_png,
    register_browser_font_faces,
)

from ...renderer import *
from ...renderer.place import MaskPlacementError
from ...renderer.text import split_text
from .shared.price import (
    PriceLineTextStyle,
    PriceSpanStyle,
    is_price_line,
    render_price_lines,
    split_price_lines,
)
from .shared.discount import DiscountBallTextStyle, DiscountBall
from .shared.gift import _gwp_group_prepared
from .shared.product import PreparedProduct
from .shared.icon import resolve_icon
from .shared.binding import (
    bind_promotion_text,
    conversion_binding_template,
    promotion_binding,
)
from .shared.preprocess import (
    gwp_image_references,
    hk_mo_price,
    normalize_currency_markers,
    optional_text,
    preprocess_vip_and_star_prices,
    recommended_price,
    required_text,
)


class TooLittleSpace(RuntimeError):
    def __init__(self, message: str, *args: Any) -> None:
        logger.warning(message)
        return super().__init__(message, *args)


class ProductTooSmall(TooLittleSpace):
    layout: FabricCanvas
    product_score: float

    def __init__(
        self,
        message: str,
        layout: FabricCanvas,
        product_score: float,
        *args: Any,
    ) -> None:
        self.layout = layout
        self.product_score = product_score
        super().__init__(message, *args)


class FallbackProductSizeUnavailable(TooLittleSpace):
    pass


class VipPriceOverlap(TooLittleSpace):
    pass


PINK = "#E7168A"
BLACK = "#000000"
YELLOW = "#F7EF53"

# FONT_FAMILY = "Source Han Sans CN"
FONT_FAMILY = "Alibaba PuHuiTi"

NAME_BOX_TOP = 440.57
BRAND_NAME_FONT_SIZE = 65.255
PRODUCT_NAME_FONT_SIZE = 65.405
NAME_ROW_HEIGHT = max(BRAND_NAME_FONT_SIZE, PRODUCT_NAME_FONT_SIZE)
NAME_HORIZONTAL_GAP = 12.875
NAME_LINE_GAP = 20.6 * 2 / 3
FALLBACK_NAME_LINE_GAP = 5.15 * 2 / 3
NAME_FONT_WEIGHT = 700

FAB_FONT_SIZE = 57.1032
FAB_FONT_WEIGHT = 700
FAB_LINE_GAP = 5.15
FAB_TOP_GAP = 20.6 * 2 / 3

RECOMMENDED_PRICE_FONT_SIZE = 49.44
RECOMMENDED_PRICE_FONT_WEIGHT = 700
RECOMMENDED_PRICE_TOP_GAP = 20.6
RECOMMENDED_PRICE_HORIZONTAL_GAP = 1.03

VIP_CURRENCY_FONT_SIZE = 144.2
VIP_PRICE_FONT_SIZE = 263.68
VIP_SUFFIX_FONT_SIZE = 109.18
VIP_NOTE_FONT_SIZE = 61.8
VIP_PRICE_FONT_WEIGHT = 700
VIP_CURRENCY_GAP_BEFORE_ONE = 24.102
VIP_CURRENCY_GAP_OTHER = 14.42
VIP_PRICE_GAP = 3.09
VIP_PRICE_SLASH_GAP = 5.15
VIP_PRICE_LINE_GAP = 5.15
DEFAULT_TEXT_COMPRESSION = 0.8
RETRY_TEXT_COMPRESSION = 0.75
BRAND_NAME_WRAP_COMPRESSION = 0.65
BRAND_NAME_COMPRESSION_CLEARANCE = 1.0
VIP_PRICE_MULTILINE_COMPRESSION = 0.7
VIP_PRICE_MIN_SCALE_X = 0.8
CANVAS_SEARCH_TOLERANCE = 0.01
VIP_PRICE_FALLBACK_MULTILINE_SCALE = 0.8
VIP_PRICE_FALLBACK_FONT_SCALE = 1.0
VIP_PRICE_LARGE_FONT_SCALE = 1.25
VIP_PRICE_LEFT = 131.6
PRICE_BOTTOM_MARGIN = 10.3
PRICE_NOTE_FIRST_LINE_GAP = -30.9

ONE_MOUTH_ANCHOR_LEFT = 126.45
ONE_MOUTH_BOTTOM_MARGIN = 30.9
ONE_MOUTH_LOGO_SIZE = 243.08
ONE_MOUTH_LOGO_MAX_SIZE = 412.0
ONE_MOUTH_PRICE_LEFT = 16.48
ONE_MOUTH_PRICE_TOP = 71.07
ONE_MOUTH_PRICE_BOTTOM = 202.91
ONE_MOUTH_PRICE_NOTE_BOTTOM_OFFSET = 30.9
ONE_MOUTH_PRICE_OUTLINE_WIDTH = 20.6
ONE_MOUTH_CURRENCY_GAP_BEFORE_ONE = 4.12
ONE_MOUTH_CURRENCY_GAP_OTHER = 0.0

PRODUCT_ANCHOR_X = 784.62
PRODUCT_ANCHOR_Y = 1065.78
PRODUCT_TARGET_SIZE = 0.05
MULTILINE_PRODUCT_MIN_SCORE = -0.2

TNC_BOX_LEFT = VIP_PRICE_LEFT
TNC_BOX_TOP = 1414.95
TNC_FONT_SIZE = 35.02
TNC_FONT_WEIGHT = 400
TNC_TEXT_COMPRESSION = 0.9
TNC_LINE_HEIGHT = 1.2

DISCOUNT_BALL_SIZE = 229
DISCOUNT_BALL_BOTTOM_MARGIN = 10.3
DISCOUNT_BALL_REGULAR_PRICE_EXTRA_BOTTOM_MARGIN = 30.9
DISCOUNT_BALL_MAX_ANCHOR_Y = 909.22
DISCOUNT_BALL_INSET = 0.96
DISCOUNT_BALL_PRIMARY_FONT_SIZE = 61.8
DISCOUNT_BALL_SECONDARY_FONT_SIZE = 30.9
DISCOUNT_BALL_SUFFIX_FONT_SIZE = 24.72
DISCOUNT_BALL_PRIMARY_FONT_WEIGHT = 900
DISCOUNT_BALL_SECONDARY_FONT_WEIGHT = 700
DISCOUNT_BALL_SUFFIX_FONT_WEIGHT = 700
DISCOUNT_BALL_LINE_GAP = 1.03
MASK_PLACEMENT_BOX_MARGIN = 10.0
PRICE_COMPRESSION_SEARCH_TOLERANCE = 0.001
PRICE_GEOMETRY_TOLERANCE = 0.01
PRICE_SCALE_SEARCH_TOLERANCE = 0.001
PRICE_RIGHT_CLEARANCE = 10.0

GWP_BALL_FONT_WEIGHT = 500
GWP_BALL_SIZE = 152.44
GWP_BALL_INSET = 0.92
GWP_BALL_TEXT_COLOR = YELLOW
GWP_TEXT_HEIGHT_RATIO = 0.1
GWP_TEXT_FONT_SIZE = 51.912
GWP_TEXT_FONT_WEIGHT = 700
GWP_TEXT_COLOR = "#333333"
GWP_TEXT_SPACING = 1.03
GWP_AREA_EXPANSION_RATIO = 0.02


@lru_cache
def _get_template(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def get_template(path: str, copy: bool = False) -> Image.Image:
    # Returns a copy of the template image to avoid modifying the cached version.
    if copy:
        return _get_template(path).copy()
    return _get_template(path)


@lru_cache
def get_template_canvas() -> BoundingBox:
    # Already applied a somewhat arbitrary inset
    return BoundingBox(l=100, t=441, w=877, h=1058)


def get_icon_box() -> BoundingBox:
    return BoundingBox(
        r=979,
        t=405,
        w=220,
        h=104,
    )


def _require_placement_box(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError(f"{type(element).__name__} has no placement box")
    return box


def _style(
    font_weight: int,
    fill: str,
    vertical_align: VerticalAlign = "center",
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
) -> TextStyle:
    return TextStyle(
        font_family=FONT_FAMILY,
        font_weight=font_weight,
        fill=fill,
        vertical_align=vertical_align,
        compression=text_compression,
        minimum_compression=text_compression,
    )


def _discount_ball_text_style(
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
) -> DiscountBallTextStyle:
    return DiscountBallTextStyle(
        font_family=FONT_FAMILY,
        fill=YELLOW,
        text_fill=YELLOW,
        primary_font_size=DISCOUNT_BALL_PRIMARY_FONT_SIZE,
        secondary_font_size=DISCOUNT_BALL_SECONDARY_FONT_SIZE,
        suffix_font_size=DISCOUNT_BALL_SUFFIX_FONT_SIZE,
        primary_font_weight=DISCOUNT_BALL_PRIMARY_FONT_WEIGHT,
        secondary_font_weight=DISCOUNT_BALL_SECONDARY_FONT_WEIGHT,
        suffix_font_weight=DISCOUNT_BALL_SUFFIX_FONT_WEIGHT,
        line_gap=DISCOUNT_BALL_LINE_GAP,
        compression=text_compression,
        minimum_compression=text_compression,
    )


def _models_to_canvas(
    template_path: str,
    elements: list[LayoutElement],
) -> FabricCanvas:
    template = get_template(template_path)
    width, height = template.size
    objects: FabricObjects = []
    layout: LayoutElement = Group(elements=elements)
    objects.extend(layout.render())
    return FabricCanvas(
        objects=objects,
        width=width,
        height=height,
        backgroundImage=FabricImage(
            src=image_to_base64(template, format="PNG"),
            left=0,
            top=0,
            width=width,
            height=height,
            scaleX=1,
            scaleY=1,
            fill="",
        ),
    )


def _render_price_lines(
    price: str,
    box: BoundingBox,
    style: PriceLineTextStyle,
    transform_line: Callable[[LayoutElement], LayoutElement] | None = None,
) -> LayoutElement:
    lines = split_price_lines(price)
    if len(lines) <= 1 or is_price_line(lines[0]):
        return render_price_lines(
            price,
            box,
            style,
            expand_to_fit_content=True,
            transform_line=transform_line,
        )

    first_line = render_price_lines(
        lines[0],
        box,
        style,
        expand_to_fit_content=True,
        transform_line=transform_line,
    )
    remaining_lines = render_price_lines(
        "\n".join(lines[1:]),
        BoundingBox(
            l=box.l,
            t=_require_placement_box(first_line).b + PRICE_NOTE_FIRST_LINE_GAP,
            w=box.w,
            h=box.h,
        ),
        style,
        expand_to_fit_content=True,
        transform_line=transform_line,
    )
    return Group(elements=[first_line, remaining_lines])


def _handle_one_mouth_price(
    price_str: str,
    avoid: list[BoundingBox],
    bottom_margin: float,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    price_text_compression: float,
    use_smaller_vip_price_font: bool = False,
) -> LayoutElement:
    lines = split_price_lines(price_str)
    if not lines:
        return Empty()

    price_style = _vip_price_text_style(
        price_text_compression,
        outlined=True,
        currency_gap_before_one=ONE_MOUTH_CURRENCY_GAP_BEFORE_ONE,
        currency_gap_other=ONE_MOUTH_CURRENCY_GAP_OTHER,
    )
    price_lines = _render_price_lines(
        price_str,
        BoundingBox(l=0, t=0, w=0, h=0),
        price_style,
    )
    price_box = _require_placement_box(price_lines)
    price_bottom = ONE_MOUTH_PRICE_BOTTOM
    if any(not is_price_line(line) for line in lines):
        price_bottom += ONE_MOUTH_PRICE_NOTE_BOTTOM_OFFSET
    price_area_height = price_bottom - ONE_MOUTH_PRICE_TOP
    group_scale = min(
        ONE_MOUTH_LOGO_MAX_SIZE / ONE_MOUTH_LOGO_SIZE,
        max(1.0, price_box.h / price_area_height),
    )
    price_area_scale = (
        VIP_PRICE_FALLBACK_FONT_SCALE
        if use_smaller_vip_price_font
        else VIP_PRICE_LARGE_FONT_SCALE
    )
    price_area_bottom = ONE_MOUTH_PRICE_TOP + price_area_height * price_area_scale
    price_lines = _fit_one_mouth_price_lines(
        price_lines,
        BoundingBox(
            l=ONE_MOUTH_PRICE_LEFT,
            t=ONE_MOUTH_PRICE_TOP,
            r=(canvas_box.r - ONE_MOUTH_ANCHOR_LEFT) / group_scale,
            b=price_area_bottom,
        ),
    )
    logo_image = Image.open(
        Bundle.configured().root / "assets" / "icons" / "sasa_202607001_onemouthprice.png"
    ).convert("RGBA")
    logo = ImageBox(
        image=logo_image,
        box=BoundingBox(
            l=0,
            t=0,
            w=ONE_MOUTH_LOGO_SIZE,
            h=ONE_MOUTH_LOGO_SIZE,
        ),
    )
    price_group = Group(elements=[logo, price_lines])
    price_group = _scale_from_top_left(
        price_group,
        scale_x=group_scale,
        scale_y=group_scale,
    )

    return Magnet(
        price_group,
        canvas_box,
        template_size,
        anchor_x=ONE_MOUTH_ANCHOR_LEFT,
        anchor_y=bottom_margin - ONE_MOUTH_BOTTOM_MARGIN,
        avoid=avoid,
        fix_scale=True,
        anchor_position="bottom-left",
    )


def _handle_pricing(
    price_str: str,
    avoid: list[BoundingBox],
    bottom_margin: float,
    is_one_mouth_price: bool,
    canvas: BoundingBox,
    template_size: tuple[int, int],
    text_compression: float,
    use_fallback_layout: bool = False,
    use_smaller_vip_price_font: bool = False,
    price_text_compression: float | None = None,
    regular_price_max_width: float | None = None,
) -> LayoutElement:
    lines = split_price_lines(price_str)
    if not lines:
        return Empty()

    if price_text_compression is None:
        price_text_compression = (
            VIP_PRICE_MULTILINE_COMPRESSION if len(lines) > 1 else text_compression
        )

    if is_one_mouth_price:
        return _handle_one_mouth_price(
            price_str,
            avoid,
            bottom_margin,
            canvas,
            template_size,
            price_text_compression,
            use_smaller_vip_price_font=use_smaller_vip_price_font,
        )

    is_multiline = len(lines) > 1
    price_font_scale = (
        VIP_PRICE_FALLBACK_FONT_SCALE
        if use_smaller_vip_price_font
        else VIP_PRICE_LARGE_FONT_SCALE
    )
    compact_scale = (
        VIP_PRICE_FALLBACK_MULTILINE_SCALE
        if is_multiline and use_smaller_vip_price_font
        else 1.0
    )
    max_width = (
        regular_price_max_width / compact_scale
        if regular_price_max_width is not None
        else canvas.r - VIP_PRICE_LEFT
    )

    def fit_line(line: LayoutElement) -> LayoutElement:
        return _fit_vip_price_line(
            line,
            max_width,
            use_fallback_layout=use_fallback_layout,
        )

    price_group = _render_price_lines(
        price_str,
        BoundingBox(l=0, t=0, w=max_width, h=0),
        _vip_price_text_style(
            price_text_compression,
            font_scale=price_font_scale,
        ),
        transform_line=fit_line,
    )
    if is_multiline and use_smaller_vip_price_font:
        price_group = _scale_from_top_left(
            price_group,
            scale_x=compact_scale,
            scale_y=compact_scale,
        )

    price_box = _require_placement_box(price_group)
    return Affine(
        element=price_group,
        translate_x=VIP_PRICE_LEFT - price_box.l,
        translate_y=bottom_margin - PRICE_BOTTOM_MARGIN - price_box.b,
    )


def _vip_price_style(
    font_size: float,
    vertical_align: VerticalAlign = "bottom",
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    outlined: bool = False,
    fill: str = PINK,
) -> TextStyle:
    style = _style(
        VIP_PRICE_FONT_WEIGHT,
        fill,
        vertical_align,
        text_compression,
    ).model_copy(
        update={
            "font_size": font_size,
        }
    )
    if not outlined:
        return style
    return style.model_copy(
        update={
            "border_color": YELLOW,
            "border_thickness": ONE_MOUTH_PRICE_OUTLINE_WIDTH,
            "border_line_cap": "round",
            "border_line_join": "round",
        }
    )


def _vip_price_text_style(
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    outlined: bool = False,
    currency_gap_before_one: float = VIP_CURRENCY_GAP_BEFORE_ONE,
    currency_gap_other: float = VIP_CURRENCY_GAP_OTHER,
    font_scale: float = 1.0,
) -> PriceLineTextStyle:
    price_style = _vip_price_style(
        VIP_PRICE_FONT_SIZE * font_scale,
        text_compression=text_compression,
        outlined=outlined,
    )
    suffix_style = _vip_price_style(
        VIP_SUFFIX_FONT_SIZE * font_scale,
        text_compression=text_compression,
        outlined=outlined,
    )
    return PriceLineTextStyle(
        currency=PriceSpanStyle(
            text_style=_vip_price_style(
                VIP_CURRENCY_FONT_SIZE * font_scale,
                text_compression=text_compression * 0.75 / 0.9,
                outlined=outlined,
            ),
        ),
        price=PriceSpanStyle(
            text_style=price_style,
            gap_after=VIP_PRICE_GAP,
        ),
        suffix=PriceSpanStyle(text_style=suffix_style),
        note=PriceSpanStyle(
            text_style=_vip_price_style(
                VIP_NOTE_FONT_SIZE * font_scale,
                "center",
                text_compression,
                outlined=outlined,
                fill=BLACK,
            )
        ),
        hyphen=PriceSpanStyle(
            text_style=_vip_price_style(
                VIP_PRICE_FONT_SIZE * font_scale,
                "center",
                text_compression,
                outlined=outlined,
            ),
            gap_before=5.15,
            gap_after=5.15,
            align_to_previous="center",
        ),
        plus=PriceSpanStyle(
            text_style=suffix_style,
            align_to_next="center",
        ),
        star=PriceSpanStyle(
            text_style=suffix_style,
            align_to_previous="top",
        ),
        slash=PriceSpanStyle(
            text_style=_vip_price_style(
                VIP_PRICE_FONT_SIZE * font_scale,
                text_compression=0.75,
                outlined=outlined,
            ),
            gap_before=VIP_PRICE_SLASH_GAP,
            gap_after=0,
        ),
        currency_gap_before_one=currency_gap_before_one,
        currency_gap_other=currency_gap_other,
        line_gap=VIP_PRICE_LINE_GAP,
    )


def _scale_from_top_left(
    element: LayoutElement,
    scale_x: float,
    scale_y: float = 1.0,
) -> LayoutElement:
    box = element.placement_box()
    if box is None:
        return element
    return Affine(
        element=element,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=box.l * (1 - scale_x),
        translate_y=box.t * (1 - scale_y),
    )


def _scale_from_bottom_left(
    element: LayoutElement,
    scale: float,
) -> LayoutElement:
    box = element.placement_box()
    if box is None:
        return element
    return Affine(
        element=element,
        scale_x=scale,
        scale_y=scale,
        translate_x=box.l * (1 - scale),
        translate_y=box.b * (1 - scale),
    )


def _align_bottom_left(
    element: LayoutElement,
    target_box: BoundingBox,
) -> LayoutElement:
    box = element.placement_box()
    if box is None:
        return element
    return Affine(
        element=element,
        translate_x=target_box.l - box.l,
        translate_y=target_box.b - box.b,
    )


def _fit_one_mouth_price_lines(
    price_lines: LayoutElement,
    target_box: BoundingBox,
) -> LayoutElement:
    price_box = price_lines.placement_box()
    if price_box is None or price_box.w == 0 or price_box.h == 0:
        return price_lines
    scale_y = target_box.h / price_box.h
    scale_x = min(
        scale_y,
        target_box.w / price_box.w,
    )
    return Affine(
        element=price_lines,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=target_box.l - price_box.l * scale_x,
        translate_y=target_box.cy - price_box.cy * scale_y,
    )


def _fit_vip_price_line(
    line: LayoutElement,
    max_width: float,
    use_fallback_layout: bool = False,
) -> LayoutElement:
    line_box = line.placement_box()
    if line_box is None:
        return line
    line_width = line_box.w
    if line_width <= max_width:
        return line

    scale_x = max_width / line_width
    if scale_x < VIP_PRICE_MIN_SCALE_X and not use_fallback_layout:
        raise TooLittleSpace(
            f"VIP price requires scaleX={scale_x:.3f} to fit {max_width:.1f}px, "
            f"below the allowed {VIP_PRICE_MIN_SCALE_X:.1f}"
        )
    return _scale_from_top_left(line, scale_x=scale_x)


def _text_width(
    text: str,
    style: TextStyle,
    available_width: float | None = None,
) -> float:
    width = available_width or get_template_canvas().w
    measurement_width = max(
        width,
        len(text) * (style.font_size or NAME_ROW_HEIGHT) * 2,
    )
    text_box = Text(
        text=text,
        box=BoundingBox(l=0, t=0, w=measurement_width, h=NAME_ROW_HEIGHT),
        style=style,
    )
    return _require_placement_box(text_box).w


def _rendered_compression(element: LayoutElement) -> float:
    rendered_objects = element.render()
    compressions = [
        rendered_object.scaleX / rendered_object.scaleY
        for rendered_object in rendered_objects
        if rendered_object.scaleY != 0
    ]
    if not compressions:
        raise ValueError("A name row must render at least one object")
    return min(compressions)


def _split_text_to_width(
    text: str,
    style: TextStyle,
    max_width: float,
) -> list[str]:
    if _text_width(text, style, max_width) <= max_width:
        return [text]

    previous_lines: list[str] | None = None
    for max_lines in range(2, len(text) + 1):
        lines = split_text(text, max_lines).splitlines()
        if lines == previous_lines:
            break
        if all(_text_width(line, style, max_width) <= max_width for line in lines):
            return lines
        previous_lines = lines

    lines: list[str] = []
    remaining = text.strip()
    while remaining:
        low = 1
        high = len(remaining)
        fitting_length = 1
        while low <= high:
            middle = (low + high) // 2
            if _text_width(remaining[:middle], style, max_width) <= max_width:
                fitting_length = middle
                low = middle + 1
            else:
                high = middle - 1

        lines.append(remaining[:fitting_length].strip())
        remaining = remaining[fitting_length:].strip()

    return lines


def _fab_text(fab: str) -> str:
    items = [
        re.sub(r"^[•·]\s*", "", line.strip())
        for line in fab.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        if line.strip()
    ]
    return "  ".join(f"• {item}" for item in items)


def _handle_fab_and_recommended_price(
    fab: str,
    recommended_price: str,
    top: float,
    canvas: BoundingBox,
    text_compression: float,
    binding_template: str | None = None,
) -> list[LayoutElement]:
    fab_style = _style(
        FAB_FONT_WEIGHT,
        PINK,
        text_compression=text_compression,
    ).model_copy(
        update={
            "font_size": FAB_FONT_SIZE,
            "text_align": "center",
        }
    )
    fab_lines = _split_text_to_width(_fab_text(fab), fab_style, canvas.w)

    elements: list[LayoutElement] = []
    for line_index, line in enumerate(fab_lines):
        elements.append(
            Text(
                text=line,
                box=BoundingBox(
                    l=canvas.l,
                    t=top + line_index * (FAB_FONT_SIZE + FAB_LINE_GAP),
                    w=canvas.w,
                    h=FAB_FONT_SIZE,
                ),
                style=fab_style,
                name="Features and benefits",
            )
        )

    fab_group = Group(elements=elements)
    fab_bottom = _require_placement_box(fab_group).b
    if binding_template is not None:
        elements = [
            bind_promotion_text(
                fab_group,
                template=binding_template,
                field="fab",
                role="formatted-line",
            )
        ]
    recommended_price_style = _style(
        RECOMMENDED_PRICE_FONT_WEIGHT,
        BLACK,
        text_compression=text_compression,
    ).model_copy(
        update={
            "font_size": RECOMMENDED_PRICE_FONT_SIZE,
        }
    )
    recommended_price_top = fab_bottom + RECOMMENDED_PRICE_TOP_GAP
    recommended_price_label = Text(
        text="建議價",
        box=BoundingBox(
            l=0,
            t=recommended_price_top,
            w=canvas.w,
            h=RECOMMENDED_PRICE_FONT_SIZE,
        ),
        style=recommended_price_style,
        name="Recommended price label",
    )
    recommended_price_value = Named(
        element=DiagonalLine(
            element=bind_promotion_text(
                Text(
                    text=recommended_price,
                    box=BoundingBox(
                        l=(
                            _require_placement_box(recommended_price_label).r
                            + RECOMMENDED_PRICE_HORIZONTAL_GAP
                        ),
                        t=recommended_price_top,
                        w=canvas.w,
                        h=RECOMMENDED_PRICE_FONT_SIZE,
                    ),
                    style=recommended_price_style,
                    name="Recommended price",
                ),
                template=binding_template,
                field="price_recommended",
                role="token",
            ),
            style=LineStyle(stroke=PINK, stroke_width=4.12),
            line_name="Recommended price strikethrough",
        ),
        name="Recommended price",
    )
    recommended_price_group = Group(
        elements=[recommended_price_label, recommended_price_value]
    )
    recommended_price_box = _require_placement_box(recommended_price_group)
    elements.append(
        Affine(
            element=recommended_price_group,
            translate_x=canvas.cx - recommended_price_box.cx,
        )
    )
    return elements


def _tnc_style() -> TextStyle:
    return _style(
        TNC_FONT_WEIGHT,
        BLACK,
        text_compression=TNC_TEXT_COMPRESSION,
    ).model_copy(update={"font_size": TNC_FONT_SIZE})


def _handle_tnc_line(
    line: str,
    box: BoundingBox,
    style: TextStyle,
    *,
    line_binding: PosmBinding | None = None,
    continuation_binding: PosmBinding | None = None,
) -> LayoutElement:
    if not line.startswith(("可以", "不可")):
        return Text(
            text=line,
            box=box,
            style=style,
            name="Terms and conditions",
            posm_binding=line_binding,
        )

    spans = [
        TextSpan(
            text=line[:2],
            style=style.model_copy(update={"underline": True}),
            name="Terms and conditions",
            posm_binding=line_binding,
        )
    ]
    if remainder := line[2:]:
        spans.append(
            TextSpan(
                text=remainder,
                style=style,
                name="Terms and conditions",
                posm_binding=continuation_binding,
            )
        )
    return TextLine(spans=spans, box=box, tight=True)


def _handle_tnc(
    tnc: str,
    canvas: BoundingBox,
    binding_template: str | None = None,
) -> LayoutElement:
    lines = [line.strip() for line in tnc.splitlines() if line.strip()]
    style = _tnc_style()
    elements: list[LayoutElement] = []
    binding_index = 0
    for line_index, line in enumerate(lines):
        box = BoundingBox(
            l=TNC_BOX_LEFT,
            t=TNC_BOX_TOP + line_index * TNC_FONT_SIZE * TNC_LINE_HEIGHT,
            r=canvas.r,
            h=TNC_FONT_SIZE,
        )
        has_continuation = line.startswith(("可以", "不可")) and bool(line[2:])
        elements.append(
            _handle_tnc_line(
                line,
                box,
                style,
                line_binding=promotion_binding(
                    template=binding_template,
                    field="tnc",
                    role="line",
                    index=binding_index,
                ),
                continuation_binding=promotion_binding(
                    template=binding_template,
                    field="tnc",
                    role="continuation",
                    index=binding_index + 1,
                ) if has_continuation else None,
            )
        )
        binding_index += 2 if has_continuation else 1
    return Group(elements=elements)


def _fallback_discount_ball_anchor_y(
    price_box: BoundingBox,
    is_one_mouth_price: bool,
) -> float:
    radius = DISCOUNT_BALL_SIZE / 2
    bottom_margin = DISCOUNT_BALL_BOTTOM_MARGIN
    if not is_one_mouth_price:
        bottom_margin += DISCOUNT_BALL_REGULAR_PRICE_EXTRA_BOTTOM_MARGIN
    return price_box.t - bottom_margin - radius


def _mask_placement_border_margin(template_size: tuple[int, int]) -> float:
    return int(sum(template_size) / 100.0)


def _discount_ball_left(
    canvas: BoundingBox,
    template_size: tuple[int, int],
) -> float:
    return float(
        max(0, int(canvas.l + _mask_placement_border_margin(template_size)))
    )


def _discount_ball_vertical_clearance(
    box: BoundingBox,
    ball_center_x: float,
    radius: float,
    template_size: tuple[int, int],
) -> float | None:
    expanded_left = max(0, int(box.l - MASK_PLACEMENT_BOX_MARGIN))
    expanded_right = min(
        template_size[0],
        int(box.r + MASK_PLACEMENT_BOX_MARGIN),
    )
    horizontal_distance = max(
        expanded_left - ball_center_x,
        ball_center_x - expanded_right,
        0.0,
    )
    if horizontal_distance >= radius:
        return None
    return math.sqrt(radius**2 - horizontal_distance**2)


def _left_discount_ball_anchor_y(
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
    template_size: tuple[int, int],
    preferred_anchor_y: float | None = None,
) -> float | None:
    border_margin = _mask_placement_border_margin(template_size)
    radius = DISCOUNT_BALL_SIZE / 2
    available_top = max(0, int(canvas.t + border_margin)) + radius
    available_bottom = min(
        template_size[1],
        int(canvas.b - border_margin),
    ) - radius
    if available_bottom < available_top:
        return None

    ball_left = _discount_ball_left(canvas, template_size)
    ball_center_x = ball_left + radius
    occupied_ranges: list[tuple[float, float]] = []
    for box in occupied_boxes:
        vertical_clearance = _discount_ball_vertical_clearance(
            box,
            ball_center_x,
            radius,
            template_size,
        )
        if vertical_clearance is None:
            continue
        expanded_top = max(0, int(box.t - MASK_PLACEMENT_BOX_MARGIN))
        expanded_bottom = min(
            template_size[1],
            int(box.b + MASK_PLACEMENT_BOX_MARGIN),
        )
        occupied_top = expanded_top - vertical_clearance
        occupied_bottom = expanded_bottom + vertical_clearance
        if occupied_bottom <= available_top or occupied_top >= available_bottom:
            continue
        occupied_ranges.append(
            (
                max(occupied_top, available_top),
                min(occupied_bottom, available_bottom),
            )
        )
    occupied_ranges.sort()

    available_spaces: list[tuple[float, float]] = []
    current_top = available_top
    for occupied_top, occupied_bottom in occupied_ranges:
        if occupied_bottom <= current_top:
            continue
        if occupied_top - current_top >= PRICE_GEOMETRY_TOLERANCE:
            available_spaces.append((current_top, occupied_top))
        current_top = max(current_top, occupied_bottom)

    if available_bottom - current_top >= PRICE_GEOMETRY_TOLERANCE:
        available_spaces.append((current_top, available_bottom))
    if not available_spaces:
        return None

    space_top, space_bottom = max(
        available_spaces,
        key=lambda space: (
            space[1] - space[0],
            0.0
            if preferred_anchor_y is None
            else -abs((space[0] + space[1]) / 2 - preferred_anchor_y),
        ),
    )
    return (space_top + space_bottom) / 2


def _discount_ball_anchor_y(
    price_box: BoundingBox,
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
    template_size: tuple[int, int],
    is_one_mouth_price: bool,
) -> float:
    fallback_anchor_y = _fallback_discount_ball_anchor_y(
        price_box,
        is_one_mouth_price,
    )
    anchor_y = _left_discount_ball_anchor_y(
        occupied_boxes,
        canvas,
        template_size,
        preferred_anchor_y=fallback_anchor_y,
    )
    if anchor_y is None:
        return fallback_anchor_y
    return anchor_y


def _fit_price_for_discount_ball(
    price: LayoutElement,
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
    template_size: tuple[int, int],
) -> tuple[LayoutElement, float]:
    if _left_discount_ball_anchor_y(
        occupied_boxes + price.placement_boxes(),
        canvas,
        template_size,
    ) is not None:
        return price, 1.0

    minimum_scale = PRICE_SCALE_SEARCH_TOLERANCE
    minimum_price = _scale_from_bottom_left(price, minimum_scale)
    if _left_discount_ball_anchor_y(
        occupied_boxes + minimum_price.placement_boxes(),
        canvas,
        template_size,
    ) is None:
        return price, 1.0

    lower_scale = minimum_scale
    upper_scale = 1.0
    fitted_price = minimum_price
    while upper_scale - lower_scale > PRICE_SCALE_SEARCH_TOLERANCE:
        candidate_scale = (lower_scale + upper_scale) / 2
        candidate = _scale_from_bottom_left(price, candidate_scale)
        if _left_discount_ball_anchor_y(
            occupied_boxes + candidate.placement_boxes(),
            canvas,
            template_size,
        ) is None:
            upper_scale = candidate_scale
        else:
            lower_scale = candidate_scale
            fitted_price = candidate

    logger.info(
        f"Scaled price to {lower_scale:.3f} so the discount ball fits on the left"
    )
    return fitted_price, lower_scale


def _price_right_limit(
    price_box: BoundingBox,
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
) -> float:
    right_limit = canvas.r
    for box in occupied_boxes:
        if box.t >= price_box.b or box.b <= price_box.t:
            continue
        if box.l >= price_box.r:
            right_limit = min(right_limit, box.l - PRICE_RIGHT_CLEARANCE)
        elif box.r > price_box.r:
            right_limit = min(right_limit, price_box.r)
    return max(price_box.r, right_limit)


def _uncompress_price_to_right(
    price: LayoutElement,
    build_price: Callable[[float, float | None], LayoutElement],
    current_compression: float,
    uniform_scale: float,
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
    is_one_mouth_price: bool,
) -> LayoutElement:
    if current_compression > DEFAULT_TEXT_COMPRESSION:
        return price

    price_box = price.placement_box()
    if price_box is None or price_box.w == 0:
        return price
    right_limit = _price_right_limit(price_box, occupied_boxes, canvas)
    if right_limit - price_box.r <= PRICE_COMPRESSION_SEARCH_TOLERANCE:
        return price

    def candidate(compression: float) -> LayoutElement:
        regular_price_max_width = (
            None
            if is_one_mouth_price
            else (right_limit - price_box.l) / uniform_scale
        )
        rebuilt = _scale_from_bottom_left(
            build_price(compression, regular_price_max_width),
            uniform_scale,
        )
        return _align_bottom_left(rebuilt, price_box)

    def fits(element: LayoutElement) -> bool:
        box = element.placement_box()
        return (
            box is not None
            and abs(box.l - price_box.l) <= PRICE_GEOMETRY_TOLERANCE
            and abs(box.t - price_box.t) <= PRICE_GEOMETRY_TOLERANCE
            and abs(box.b - price_box.b) <= PRICE_GEOMETRY_TOLERANCE
            and box.r <= right_limit + PRICE_GEOMETRY_TOLERANCE
        )

    def grows(element: LayoutElement) -> bool:
        box = element.placement_box()
        return (
            box is not None
            and box.r > price_box.r + PRICE_GEOMETRY_TOLERANCE
        )

    try:
        default_price = candidate(DEFAULT_TEXT_COMPRESSION)
    except TooLittleSpace:
        default_price = None
    if default_price is not None and fits(default_price):
        if grows(default_price):
            logger.info(
                f"Expanded price from {price_box.w:.1f}px to "
                f"{_require_placement_box(default_price).w:.1f}px using free "
                f"space on the right"
            )
        return default_price

    lower_compression = current_compression
    upper_compression = DEFAULT_TEXT_COMPRESSION
    fitted_price = price
    while (
        upper_compression - lower_compression
        > PRICE_COMPRESSION_SEARCH_TOLERANCE
    ):
        candidate_compression = (lower_compression + upper_compression) / 2
        try:
            candidate_price = candidate(candidate_compression)
        except TooLittleSpace:
            upper_compression = candidate_compression
            continue
        if fits(candidate_price):
            lower_compression = candidate_compression
            fitted_price = candidate_price
        else:
            upper_compression = candidate_compression

    if fitted_price is not price and grows(fitted_price):
        logger.info(
            f"Expanded price from {price_box.w:.1f}px to "
            f"{_require_placement_box(fitted_price).w:.1f}px using free space "
            f"on the right"
        )
    return fitted_price


def _handle_discount_ball(
    text: str,
    price_box: BoundingBox,
    occupied_boxes: list[BoundingBox],
    canvas: BoundingBox,
    template_size: tuple[int, int],
    text_compression: float,
    is_one_mouth_price: bool,
) -> LayoutElement:
    radius = DISCOUNT_BALL_SIZE / 2
    return DiscountBall(
        text=text,
        anchor_x=_discount_ball_left(canvas, template_size) + radius,
        anchor_y=_discount_ball_anchor_y(
            price_box,
            occupied_boxes,
            canvas,
            template_size,
            is_one_mouth_price,
        ),
        diameter=DISCOUNT_BALL_SIZE,
        canvas_box=canvas,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        fill_color=PINK,
        text_style=_discount_ball_text_style(text_compression),
        inset=DISCOUNT_BALL_INSET,
    )


def _name_row_box(
    line_index: int,
    canvas: BoundingBox,
    line_gap: float = NAME_LINE_GAP,
) -> BoundingBox:
    top = NAME_BOX_TOP + line_index * (NAME_ROW_HEIGHT + line_gap)
    return BoundingBox(
        l=canvas.l,
        t=top,
        w=canvas.w,
        h=NAME_ROW_HEIGHT,
    )


def _first_name_row_fit_box(
    canvas: BoundingBox,
    has_icon: bool,
) -> BoundingBox:
    row_box = _name_row_box(0, canvas)
    if not has_icon:
        return row_box

    space_gone = canvas.r - get_icon_box().l - 5
    available_width = row_box.w - 2 * space_gone
    return BoundingBox(
        l=row_box.cx - available_width / 2,
        t=row_box.t,
        w=available_width,
        h=row_box.h,
    )


_BRAND_OPENING_PUNCTUATION = frozenset("([{（【《〈「『“‘")
_BRAND_CLOSING_PUNCTUATION = frozenset(")]},.!?;:）】》〉」』”’，。！？；：")


def _is_cjk_brand_character(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
        or 0x2CEB0 <= codepoint <= 0x2EBEF
    )


def _is_safe_brand_break(text: str, position: int) -> bool:
    if position <= 0 or position >= len(text):
        return False
    left = text[position - 1]
    right = text[position]
    right_codepoint = ord(right)
    return not (
        left == "\u200d"
        or right == "\u200d"
        or unicodedata.combining(right)
        or 0xFE00 <= right_codepoint <= 0xFE0F
        or 0xE0100 <= right_codepoint <= 0xE01EF
        or 0x1F3FB <= right_codepoint <= 0x1F3FF
    )


def _brand_break_positions(text: str) -> list[int]:
    positions: list[int] = []
    for position in range(1, len(text)):
        if not _is_safe_brand_break(text, position):
            continue

        left = text[position - 1]
        right = text[position]
        if left.isspace():
            if not right.isspace():
                positions.append(position)
            continue
        if right.isspace():
            continue
        if right in _BRAND_CLOSING_PUNCTUATION or left in _BRAND_OPENING_PUNCTUATION:
            continue
        if (
            right in _BRAND_OPENING_PUNCTUATION
            or left in _BRAND_CLOSING_PUNCTUATION
            or _is_cjk_brand_character(left)
            or _is_cjk_brand_character(right)
        ):
            positions.append(position)
    return positions


def _safe_brand_break_positions(text: str) -> list[int]:
    return [
        position
        for position in range(1, len(text))
        if _is_safe_brand_break(text, position)
        and text[:position].strip()
        and text[position:].strip()
    ]


def _required_brand_compression(
    text: str,
    style: TextStyle,
    max_width: float,
) -> float:
    rendered_width = _text_width(text, style, max_width)
    if rendered_width <= 0:
        return style.compression
    return min(
        style.compression,
        style.compression * max_width / rendered_width,
    )


def _longest_fitting_brand_position(
    text: str,
    style: TextStyle,
    max_width: float,
    candidates: list[int],
) -> int | None:
    best_position: int | None = None
    low = 0
    high = len(candidates) - 1
    while low <= high:
        middle = (low + high) // 2
        position = candidates[middle]
        prefix = text[:position].strip()
        if _text_width(prefix, style, max_width) <= max_width:
            best_position = position
            low = middle + 1
        else:
            high = middle - 1
    return best_position


def _longest_fitting_brand_prefix(
    text: str,
    style: TextStyle,
    max_width: float,
) -> tuple[str, str] | None:
    candidates = [
        position
        for position in _brand_break_positions(text)
        if text[:position].strip() and text[position:].strip()
    ]
    split_position = _longest_fitting_brand_position(
        text,
        style,
        max_width,
        candidates,
    )
    if split_position is None:
        split_position = _longest_fitting_brand_position(
            text,
            style,
            max_width,
            _safe_brand_break_positions(text),
        )
    if split_position is None:
        return None

    return (
        text[:split_position].strip(),
        text[split_position:].strip(),
    )


def _wrapped_brand_row_box(
    line_index: int,
    canvas: BoundingBox,
    line_gap: float,
) -> BoundingBox:
    if line_index == 0:
        return _first_name_row_fit_box(canvas, True)
    return _name_row_box(line_index, canvas, line_gap)


def _automatic_brand_name_lines(
    brand_name: str,
    brand_style: TextStyle,
    canvas: BoundingBox,
    line_gap: float,
) -> list[str]:
    cleaned_brand_name = brand_name.strip()
    if (
        not cleaned_brand_name
        or "\n" in cleaned_brand_name
        or "\r" in cleaned_brand_name
    ):
        return [brand_name]

    split_style = brand_style.model_copy(
        update={
            "compression": DEFAULT_TEXT_COMPRESSION,
            "minimum_compression": DEFAULT_TEXT_COMPRESSION,
        }
    )
    first_row_box = _wrapped_brand_row_box(0, canvas, line_gap)
    first_row_compression = _required_brand_compression(
        cleaned_brand_name,
        split_style,
        first_row_box.w,
    )
    if first_row_compression >= BRAND_NAME_WRAP_COMPRESSION:
        return [cleaned_brand_name]

    second_row_box = _wrapped_brand_row_box(1, canvas, line_gap)
    second_row_compression = _required_brand_compression(
        cleaned_brand_name,
        split_style,
        second_row_box.w,
    )
    if second_row_compression >= BRAND_NAME_WRAP_COMPRESSION:
        return ["", cleaned_brand_name]

    lines: list[str] = []
    remaining = cleaned_brand_name
    while remaining:
        row_box = _wrapped_brand_row_box(len(lines), canvas, line_gap)
        required_compression = (
            first_row_compression
            if not lines
            else _required_brand_compression(
                remaining,
                split_style,
                row_box.w,
            )
        )
        if required_compression >= BRAND_NAME_WRAP_COMPRESSION:
            lines.append(remaining)
            break

        split = _longest_fitting_brand_prefix(
            remaining,
            split_style,
            row_box.w,
        )
        if split is None:
            lines.append(remaining)
            break

        line, remaining = split
        lines.append(line)
    return lines


def _unified_brand_compression(
    brand_lines: list[str],
    brand_style: TextStyle,
    canvas: BoundingBox,
    line_gap: float,
) -> float:
    compressions = [brand_style.compression]
    for line_index, line in enumerate(brand_lines):
        if not line:
            continue
        row_box = _wrapped_brand_row_box(line_index, canvas, line_gap)
        measurement_box = BoundingBox(
            l=row_box.l + BRAND_NAME_COMPRESSION_CLEARANCE / 2,
            t=row_box.t,
            w=max(0.0, row_box.w - BRAND_NAME_COMPRESSION_CLEARANCE),
            h=row_box.h,
        )
        row = TextLine(
            spans=[TextSpan(text=line, style=brand_style)],
            box=measurement_box,
            tight=True,
        )
        compressions.append(_rendered_compression(row))
    return min(compressions)


def _align_brand_to_reference_bounds(
    brand_line: LayoutElement,
    reference_bounds: BoundingBox,
    center_x: float,
) -> LayoutElement:
    brand_bounds = _require_placement_box(brand_line)
    translate_x = center_x - brand_bounds.cx
    if (
        brand_bounds.h <= 0
        or reference_bounds.h <= 0
        or (
            math.isclose(brand_bounds.t, reference_bounds.t, abs_tol=1e-6)
            and math.isclose(brand_bounds.b, reference_bounds.b, abs_tol=1e-6)
        )
    ):
        return Affine(
            element=brand_line,
            translate_x=translate_x,
        )

    scale_y = reference_bounds.h / brand_bounds.h
    return Affine(
        element=brand_line,
        scale_y=scale_y,
        translate_x=translate_x,
        translate_y=reference_bounds.t - brand_bounds.t * scale_y,
    )


def _fit_brand_row(
    brand_name: str,
    brand_style: TextStyle,
    fit_box: BoundingBox,
) -> LayoutElement:
    reference_style = brand_style.model_copy(
        update={
            "compression": DEFAULT_TEXT_COMPRESSION,
            "minimum_compression": DEFAULT_TEXT_COMPRESSION,
        }
    )
    reference_font_size = reference_style.font_size or fit_box.h
    reference_width = max(
        fit_box.w,
        len(brand_name) * reference_font_size * 2,
    )
    reference_box = BoundingBox(
        l=fit_box.cx - reference_width / 2,
        t=fit_box.t,
        w=reference_width,
        h=fit_box.h,
    )
    reference_line = TextLine(
        spans=[
            TextSpan(
                text=brand_name,
                style=reference_style,
                name="Brand name",
            )
        ],
        box=reference_box,
        tight=True,
    )
    reference_line_bounds = _require_placement_box(reference_line)
    reference_line = Affine(
        element=reference_line,
        translate_x=fit_box.cx - reference_line_bounds.cx,
    )
    reference_bounds = _require_placement_box(reference_line)

    fitted_line = TextLine(
        spans=[
            TextSpan(
                text=brand_name,
                style=brand_style,
                name="Brand name",
            )
        ],
        box=fit_box,
        tight=True,
    )
    return _align_brand_to_reference_bounds(
        fitted_line,
        reference_bounds,
        fit_box.cx,
    )


def _check_first_line_too_fat(
    product_line: str,
    brand_width: float,
    product_style: TextStyle,
    has_icon: bool,
    canvas: BoundingBox,
) -> bool:
    product_name_lines = product_line.split("\n")
    first_line_width = brand_width + NAME_HORIZONTAL_GAP + _text_width(
        product_name_lines[0],
        product_style,
        canvas.w,
    )
    return first_line_width > _first_name_row_fit_box(canvas, has_icon).w


def _automatically_split_product_name(
    product_name: str,
    brand_width: float,
    product_style: TextStyle,
    has_icon: bool,
    canvas: BoundingBox,
) -> list[str]:
    if not _check_first_line_too_fat(
        product_name,
        brand_width,
        product_style,
        has_icon,
        canvas,
    ):
        return [product_name]

    previous_lines: list[str] | None = None
    for max_lines in range(1, len(product_name) + 1):
        lines = split_text(product_name, max_lines).splitlines()
        if lines == previous_lines:
            break
        if all(
            _text_width(line, product_style, canvas.w)
            <= _name_row_box(line_index, canvas).w
            for line_index, line in enumerate(lines, start=1)
        ):
            return ["", *lines]
        previous_lines = lines

    return ["", product_name]


def _product_name_lines(
    product_name: str,
    brand_width: float,
    product_style: TextStyle,
    has_icon: bool = False,
    canvas: BoundingBox | None = None,
) -> list[str]:
    active_canvas = canvas or get_template_canvas()
    normalized_product_name = product_name.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in normalized_product_name:
        product_name_lines = normalized_product_name.split("\n")
        if _check_first_line_too_fat(
            product_name,
            brand_width,
            product_style,
            has_icon,
            active_canvas,
        ):
            return ["", *product_name_lines]
        return product_name_lines
    return _automatically_split_product_name(
        normalized_product_name,
        brand_width,
        product_style,
        has_icon,
        active_canvas,
    )


def _handle_brand_name_and_product_name(
    brand_name: str,
    product_name: str,
    has_icon: bool,
    canvas: BoundingBox,
    text_compression: float,
    line_gap: float = NAME_LINE_GAP,
    binding_template: str | None = None,
) -> tuple[list[LayoutElement], int, int, int]:
    brand_style = _style(
        NAME_FONT_WEIGHT,
        PINK,
        text_compression=text_compression,
    ).model_copy(update={"font_size": BRAND_NAME_FONT_SIZE})
    product_style = _style(
        NAME_FONT_WEIGHT,
        BLACK,
        text_compression=text_compression,
    ).model_copy(update={"font_size": PRODUCT_NAME_FONT_SIZE})
    brand_width = _text_width(brand_name, brand_style, canvas.w)
    product_lines = _product_name_lines(
        product_name,
        brand_width,
        product_style,
        has_icon,
        canvas,
    )
    product_name_line_count = sum(bool(line.strip()) for line in product_lines)

    first_product_line = product_lines[0] if product_lines else ""
    brand_lines = [brand_name]
    if has_icon and not first_product_line:
        brand_lines = _automatic_brand_name_lines(
            brand_name,
            brand_style,
            canvas,
            line_gap,
        )
    brand_name_row_count = len(brand_lines)
    brand_name_line_count = sum(bool(line.strip()) for line in brand_lines)
    product_row_offset = (
        brand_name_row_count - 1
        if not first_product_line
        else 0
    )

    final_product_compression = text_compression
    if first_product_line:
        first_row = TextLine(
            spans=[
                TextSpan(
                    text=brand_name,
                    style=brand_style,
                    gap_after=NAME_HORIZONTAL_GAP,
                    name="Brand name",
                ),
                TextSpan(
                    text=first_product_line,
                    style=product_style,
                    name="Product name",
                ),
            ],
            box=_name_row_box(0, canvas),
            tight=True,
        )
        final_product_compression = min(
            final_product_compression,
            _rendered_compression(first_row),
        )

    for line_index, line in enumerate(product_lines[1:], start=1):
        if not line:
            continue
        row_index = line_index + product_row_offset
        product_row = Text(
            text=line,
            box=_name_row_box(row_index, canvas, line_gap),
            style=product_style.model_copy(update={"text_align": "center"}),
        )
        final_product_compression = min(
            final_product_compression,
            _rendered_compression(product_row),
        )

    logger.debug(f"Product name compression factor: {final_product_compression:.3f}")
    product_style = product_style.model_copy(
        update={"compression": final_product_compression}
    )
    first_row_brand_style = brand_style
    if first_product_line:
        first_row_brand_style = brand_style.model_copy(
            update={"compression": final_product_compression}
        )

    elements: list[LayoutElement] = []
    if has_icon and not first_product_line:
        unified_brand_style = brand_style
        if brand_name_line_count > 1:
            unified_brand_compression = _unified_brand_compression(
                brand_lines,
                brand_style,
                canvas,
                line_gap,
            )
            logger.debug(
                f"Brand name compression factor: {unified_brand_compression:.3f}"
            )
            unified_brand_style = brand_style.model_copy(
                update={
                    "compression": unified_brand_compression,
                    "minimum_compression": unified_brand_compression,
                }
            )
        for line_index, line in enumerate(brand_lines):
            if not line:
                continue
            elements.append(
                _fit_brand_row(
                    line,
                    unified_brand_style,
                    _wrapped_brand_row_box(line_index, canvas, line_gap),
                )
            )
    else:
        spans = [
            TextSpan(
                text=brand_name,
                style=first_row_brand_style,
                gap_after=NAME_HORIZONTAL_GAP if first_product_line else 0,
                name="Brand name",
            )
        ]
        if first_product_line:
            spans.append(
                TextSpan(
                    text=first_product_line,
                    style=product_style,
                    name="Product name",
                )
            )

        first_row_box = (
            _name_row_box(0, canvas)
            if first_product_line
            else _first_name_row_fit_box(canvas, has_icon)
        )
        first_line = TextLine(
            spans=spans,
            box=first_row_box,
            tight=True,
        )
        first_line_box = _require_placement_box(first_line)
        elements.append(
            Affine(
                element=first_line,
                translate_x=first_row_box.cx - first_line_box.cx,
            )
        )

    last_name_row_index = brand_name_row_count - 1
    for line_index, line in enumerate(product_lines[1:], start=1):
        if not line:
            continue
        row_index = line_index + product_row_offset
        row_box = _name_row_box(row_index, canvas, line_gap)
        elements.append(
            Text(
                text=line,
                box=row_box,
                style=product_style.model_copy(update={"text_align": "center"}),
                name="Product name",
            )
        )
        last_name_row_index = max(last_name_row_index, row_index)

    semantic_elements = elements
    if binding_template is not None:
        bound_names = bind_promotion_text(
            Group(elements=elements),
            template=binding_template,
            field="brand_name",
            role="line",
            name_prefixes=("Brand name",),
        )
        bound_names = bind_promotion_text(
            bound_names,
            template=binding_template,
            field="product_name",
            role="line",
            name_prefixes=("Product name",),
        )
        semantic_elements = [bound_names]

    return (
        semantic_elements,
        last_name_row_index + 1,
        brand_name_line_count,
        product_name_line_count,
    )


def _product_score(
    product: LayoutElement,
    canvas: BoundingBox,
) -> float | None:
    product_box = product.placement_box()
    if product_box is None:
        return None
    product_score = score_product_scale(
        ph=product_box.h,
        pw=product_box.w,
        ch=canvas.h,
        cw=canvas.w,
    )
    logger.info(f"Product score: {product_score:.3f}")
    return product_score


def _icon(icon_text: str | None, canvas: BoundingBox) -> list[LayoutElement]:
    icon = resolve_icon(icon_text)
    if not icon:
        if icon_text:
            logger.warning(f"Icon text '{icon_text}' not recognized; skipping icon")
        return []
    box = get_icon_box()
    return [Named(element=ImageBox(image=icon, box=box), name="Promotion icon")]


def get_layout_inner(
    template_path: str,
    brand_name: str,
    product_name: str,
    recommended_price: str,
    vip_price: str,
    fab: str,
    product_images: list[Image.Image],
    tnc: str | None = None,
    discount_ball: str | None = None,
    one_mouth_price_mode: bool = False,
    icon_text: str | None = None,
    gwp_product_images: list[Image.Image] | None = None,
    gwp_text: str | None = None,
    fallback: bool = False,
    compare_fallback_product_size: bool = False,
    use_smaller_vip_price_font: bool = False,
    prepared_product: PreparedProduct | None = None,
    prepared_gwp_product: PreparedProduct | None = None,
    binding_template: str | None = None,
) -> FabricCanvas:
    """Build a layout attempt, relaxing fit constraints when fallback is True."""
    template = get_template(template_path)
    template_size = template.size
    template_canvas = get_template_canvas()
    elements: list[LayoutElement] = []
    placed_product: LayoutElement | None = None
    text_compression = RETRY_TEXT_COMPRESSION if fallback else DEFAULT_TEXT_COMPRESSION

    if icon_text is not None:
        icon = _icon(icon_text, template_canvas)
        elements.extend(icon)

    (
        brand_name_product_name,
        name_row_count,
        brand_name_line_count,
        product_name_line_count,
    ) = _handle_brand_name_and_product_name(
        brand_name,
        product_name,
        icon_text is not None,
        template_canvas,
        text_compression,
        FALLBACK_NAME_LINE_GAP if fallback else NAME_LINE_GAP,
        binding_template,
    )
    elements.extend(brand_name_product_name)

    name_bottom = _require_placement_box(
        Group(elements=brand_name_product_name)
    ).b
    elements.extend(
        _handle_fab_and_recommended_price(
            fab,
            recommended_price,
            name_bottom + FAB_TOP_GAP,
            template_canvas,
            text_compression,
            binding_template,
        )
    )
    bottom = _require_placement_box(Group(elements=elements)).b

    price_bottom_margin = TNC_BOX_TOP + TNC_FONT_SIZE
    if tnc:
        tnc_element = _handle_tnc(tnc, template_canvas, binding_template)
        tnc_box = _require_placement_box(tnc_element)
        if tnc_box.t < bottom:
            if not fallback:
                raise TooLittleSpace("Terms and conditions overlap the upper text block")
            logger.warning("Terms and conditions overlap the upper text block while fallback is enabled; ignoring this overlap...")
        elements.append(tnc_element)
        price_bottom_margin = tnc_box.t

    price_tag_index: int | None = None
    price_builder: Callable[[float, float | None], LayoutElement] | None = None
    price_text_compression: float | None = None
    price_uniform_scale = 1.0
    discount_ball_text = (
        discount_ball.strip()
        if discount_ball is not None and discount_ball.strip()
        else None
    )
    if vip_price.strip():
        price_lines = split_price_lines(vip_price)
        price_text_compression = (
            VIP_PRICE_MULTILINE_COMPRESSION
            if len(price_lines) > 1
            else text_compression
        )
        pricing_avoid_boxes = Group(elements=elements).placement_boxes()

        def build_price(
            compression: float,
            regular_price_max_width: float | None = None,
        ) -> LayoutElement:
            return _handle_pricing(
                vip_price,
                pricing_avoid_boxes,
                bottom_margin=price_bottom_margin,
                is_one_mouth_price=one_mouth_price_mode,
                canvas=template_canvas,
                template_size=template_size,
                text_compression=text_compression,
                use_fallback_layout=fallback,
                use_smaller_vip_price_font=use_smaller_vip_price_font,
                price_text_compression=compression,
                regular_price_max_width=regular_price_max_width,
            )

        price_builder = build_price
        price_tag = price_builder(price_text_compression, None)
        if discount_ball_text is not None:
            price_tag, price_uniform_scale = _fit_price_for_discount_ball(
                price_tag,
                pricing_avoid_boxes,
                template_canvas,
                template_size,
            )
        price_tag_box = _require_placement_box(price_tag)
        if price_tag_box.t < bottom:
            msg = f"Price block overlaps the upper text block, expected: {bottom:.1f}px, got: {price_tag_box.t:.1f}px"
            if (
                name_row_count > 1
                and not use_smaller_vip_price_font
            ):
                raise VipPriceOverlap(msg)
            if not fallback:
                raise TooLittleSpace(msg)
            logger.warning(f"{msg} while fallback is enabled; ignoring this overlap...")
        price_tag_index = len(elements)
        elements.append(price_tag)

    if discount_ball_text is not None:
        price_box = (
            elements[price_tag_index].placement_box()
            if price_tag_index is not None
            else None
        )
        if price_box is None or price_box.w == 0 or price_box.h == 0:
            logger.warning("Skipping discount ball because no price tag was placed")
        else:
            elements.append(
                bind_promotion_text(
                    _handle_discount_ball(
                        discount_ball_text,
                        price_box,
                        Group(elements=elements).placement_boxes(),
                        template_canvas,
                        template_size,
                        text_compression,
                        one_mouth_price_mode,
                    ),
                    template=binding_template,
                    field="discount_ball",
                    role="token",
                )
            )

    if prepared_product is None:
        prepared_product = PreparedProduct(product_images)
    if gwp_product_images and prepared_gwp_product is None:
        prepared_gwp_product = PreparedProduct(gwp_product_images)

    has_gwp_product = bool(prepared_gwp_product)
    has_gwp_text = bool(gwp_text and gwp_text.strip())

    if has_gwp_product or has_gwp_text:
        gwp_ball_text = "再送" if "送" in (discount_ball or "") else "送"
        logger.debug(f"Using GWP ball text: {gwp_ball_text}")
        top_box = [BoundingBox(l=template_canvas.l, t=template_canvas.t, r=template_canvas.r, b=bottom)]
        try:
            gwp_group = _gwp_group_prepared(
                prepared_product=prepared_product,
                prepared_gwp_product=prepared_gwp_product,
                gwp_text=gwp_text,
                gwp_ball_text=gwp_ball_text,
                gwp_ball_fill=PINK,
                gwp_ball_size=GWP_BALL_SIZE,
                template_canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=Group(elements=elements).placement_boxes() + top_box,
                product_anchor_x=PRODUCT_ANCHOR_X,
                product_anchor_y=PRODUCT_ANCHOR_Y,
                gwp_text_style=TextStyle(
                    font_family=FONT_FAMILY,
                    font_size=GWP_TEXT_FONT_SIZE,
                    font_weight=GWP_TEXT_FONT_WEIGHT,
                    fill=GWP_TEXT_COLOR,
                    text_align="center",
                    vertical_align="bottom",
                    compression=text_compression,
                    minimum_compression=text_compression,
                ),
                gwp_text_spacing=GWP_TEXT_SPACING,
                gwp_ball_text_style=TextStyle(
                    font_family=FONT_FAMILY,
                    font_weight=GWP_BALL_FONT_WEIGHT,
                    fill=GWP_BALL_TEXT_COLOR,
                    text_align="center",
                    vertical_align="center",
                    compression=text_compression,
                    minimum_compression=text_compression,
                ),
                gwp_ball_inset=GWP_BALL_INSET,
                gwp_expansion_ratio=GWP_AREA_EXPANSION_RATIO,
                gwp_text_height_ratio=GWP_TEXT_HEIGHT_RATIO,
                allow_overlap_fallback=False,
            )
        except MaskPlacementError as error:
            logger.error(
                f"Skipping GWP placement because no collision-free space is available: "
                f"{error}"
            )
        else:
            if not isinstance(gwp_group, Empty):
                if prepared_product:
                    placed_product = gwp_group.product
                elements.append(
                    bind_promotion_text(
                        gwp_group,
                        template=binding_template,
                        field="gwp_text",
                        role="line",
                        name_prefixes=("Gift description",),
                    )
                )
    elif prepared_product:
        try:
            product_element = prepared_product.place(
                canvas_box=template_canvas,
                template_size=template_size,
                occupied_boxes=Group(elements=elements).placement_boxes(),
                anchor_x=PRODUCT_ANCHOR_X,
                anchor_y=PRODUCT_ANCHOR_Y,
                target_product_size=PRODUCT_TARGET_SIZE,
                allow_overlap_fallback=False,
            )
        except MaskPlacementError as error:
            logger.error(
                f"Skipping product placement because no collision-free space is "
                f"available: {error}"
            )
        else:
            placed_product = product_element
            elements.append(product_element)
    else:
        logger.warning("No product images provided; skipping product placement")

    if (
        price_tag_index is not None
        and price_builder is not None
        and price_text_compression is not None
    ):
        price_tag = elements[price_tag_index]
        occupied_elements = (
            elements[:price_tag_index] + elements[price_tag_index + 1:]
        )
        elements[price_tag_index] = _uncompress_price_to_right(
            price_tag,
            price_builder,
            price_text_compression,
            price_uniform_scale,
            Group(elements=occupied_elements).placement_boxes(),
            template_canvas,
            one_mouth_price_mode,
        )

    if price_tag_index is not None:
        elements[price_tag_index] = bind_promotion_text(
            Named(
                element=elements[price_tag_index],
                name="VIP price",
            ),
            template=binding_template,
            field="star_price" if one_mouth_price_mode else "price_vip",
            role="token",
        )

    layout = _models_to_canvas(
        template_path,
        elements,
    )
    should_check_product_size = (
        compare_fallback_product_size
        or brand_name_line_count > 1
        or product_name_line_count > 1
        or name_row_count > 2
    ) and not fallback
    product_score = (
        _product_score(placed_product, template_canvas)
        if placed_product is not None
        else None
    )
    if should_check_product_size:
        if compare_fallback_product_size and product_score is None:
            raise FallbackProductSizeUnavailable(
                "Fallback product size could not be measured"
            )
        if (
            product_score is not None
            and product_score < MULTILINE_PRODUCT_MIN_SCORE
        ):
            raise ProductTooSmall(
                f"Product score {product_score:.3f} is below "
                f"{MULTILINE_PRODUCT_MIN_SCORE:.1f} with a multiline name block",
                layout=layout,
                product_score=product_score,
            )
    else:
        logger.info(f"Product score: {product_score:.3f}" if product_score is not None else "No product score available")
    return layout


def get_layout(
    template_path: str,
    brand_name: str,
    product_name: str,
    recommended_price: str,
    vip_price: str,
    fab: str,
    product_images: list[Image.Image],
    tnc: str | None = None,
    discount_ball: str | None = None,
    one_mouth_price_mode: bool = False,
    icon_text: str | None = None,
    gwp_product_images: list[Image.Image] | None = None,
    gwp_text: str | None = None,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    canvas_bounds: BoundingBox | None = None,
    binding_template: str | None = None,
) -> FabricCanvas:
    register_browser_font_faces(ALIBABA_PUHUITI_FONT_FACES)
    output_canvas = canvas_bounds or get_template_canvas()
    prepared_product = PreparedProduct(product_images)
    prepared_gwp_product = (
        PreparedProduct(gwp_product_images)
        if gwp_product_images
        else None
    )

    def attempt(
        fallback: bool,
        compare_fallback_product_size: bool = False,
        use_smaller_vip_price_font: bool = False,
    ) -> FabricCanvas:
        return get_layout_inner(
            template_path,
            brand_name,
            product_name,
            recommended_price,
            vip_price,
            fab,
            product_images,
            tnc=tnc,
            discount_ball=discount_ball,
            one_mouth_price_mode=one_mouth_price_mode,
            icon_text=icon_text,
            gwp_product_images=gwp_product_images,
            gwp_text=gwp_text,
            fallback=fallback,
            compare_fallback_product_size=compare_fallback_product_size,
            use_smaller_vip_price_font=use_smaller_vip_price_font,
            prepared_product=prepared_product,
            prepared_gwp_product=prepared_gwp_product,
            binding_template=binding_template,
        )

    def fallback_attempt(
        compare_fallback_product_size: bool = False,
    ) -> FabricCanvas:
        try:
            return attempt(
                fallback=True,
                compare_fallback_product_size=compare_fallback_product_size,
            )
        except VipPriceOverlap:
            return attempt(
                fallback=True,
                compare_fallback_product_size=compare_fallback_product_size,
                use_smaller_vip_price_font=True,
            )

    try:
        return attempt(False)
    except ProductTooSmall as original_error:
        logger.error(
            f"Layout failed with {text_compression:.2f} compression: "
            f"{original_error}"
        )
        try:
            return fallback_attempt(compare_fallback_product_size=True)
        except FallbackProductSizeUnavailable:
            logger.info(
                "Fallback product size could not be measured; using original layout."
            )
            return original_error.layout
        except ProductTooSmall as fallback_error:
            if fallback_error.product_score > original_error.product_score:
                logger.info(
                    f"Fallback improved product score from "
                    f"{original_error.product_score:.3f} to "
                    f"{fallback_error.product_score:.3f}; using fallback layout."
                )
                return fallback_error.layout
            logger.info(
                f"Fallback product score {fallback_error.product_score:.3f} did not "
                f"improve {original_error.product_score:.3f}; using original layout."
            )
            return original_error.layout
    except VipPriceOverlap as error:
        logger.error(
            f"Layout failed with {text_compression:.2f} compression: {error}"
        )
        return attempt(True, use_smaller_vip_price_font=True)
    except TooLittleSpace as e:
        logger.error(f"Layout failed with {text_compression:.2f} compression: {e}")
        return fallback_attempt()


@lru_cache(maxsize=1)
def get_template_path():
    path = str(Bundle.configured().root / "assets" / "templates" / "sasa_202607001.png")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Template image not found at {path}")
    return path


class Pipeline(POSMImplementation):
    def get_template_path(self) -> str:
        return get_template_path()

    def get_template(self) -> Image.Image:
        return Image.open(self.get_template_path()).convert("RGBA")

    @classmethod
    def get_expected_number_of_fields(cls) -> int:
        return 1

    def process(self, params: CreateParams) -> GenerationResult:
        fields = params.promotion_list[0]
        region = hk_mo_price(fields)
        binding_template = conversion_binding_template(self.name, region)
        template_path = self.get_template_path()

        product_images = [self.maybe_load_image_from_url(url) for url in params.product[0]]
        product_images = [img for img in product_images if img is not None]

        gwp_product_images = [
            self.maybe_load_image_from_url(reference)
            for reference in gwp_image_references(fields)
        ]
        gwp_product_images = [img for img in gwp_product_images if img is not None] or None

        vip_price, one_mouth_price = preprocess_vip_and_star_prices(
            optional_text(fields, "price_vip"),
            optional_text(fields, "star_price"),
            region,
        )

        # Full price normalization is limited to the rendered price fields. GWP
        # monetary copy receives marker replacement only; discount-ball and
        # other promotion copy pass through literally.
        fabric_model = get_layout(
            template_path=template_path,
            brand_name=required_text(fields, "brand_name"),
            product_name=required_text(fields, "product_name"),
            recommended_price=recommended_price(
                required_text(fields, "price_recommended"),
                region,
            ),
            vip_price=one_mouth_price or vip_price,
            fab=required_text(fields, "fab"),
            product_images=product_images,
            tnc=optional_text(fields, "tnc"),
            discount_ball=optional_text(fields, "discount_ball"),
            one_mouth_price_mode=one_mouth_price is not None,
            icon_text=optional_text(fields, "icon_text"),
            gwp_product_images=gwp_product_images,
            gwp_text=normalize_currency_markers(
                optional_text(fields, "gwp_text"),
                region,
            ),
            binding_template=binding_template,
        )
        reference_image = fabric_to_png(fabric_model).convert("RGB")

        return GenerationResult(
            id=self._get_run_id(),
            reference_jpg=image_to_base64(reference_image, format="JPEG"),
            fabric_model=fabric_model,
            message=None,
            successful=True,
        )

    def get_schema(self) -> dict[str, str]:
        return {
            "hk_mo_price": "MO",
            "brand_name": "Cyber Colors",
            "product_name": "亮澤保濕卸妝潔面凝膠 150ml",
            "price_vip": "MOP132/件",
            "price_non_vip": "",
            "price_recommended": "MOP198/件",
            "star_price": "**價",
            "fab": "溶解彩妝，清除角質\r\n滋潤亮澤肌膚",
            "discount_ball": "67折#",
            "tnc": "可以與VIP優惠同時使用",
            "icon_text": "獨家發售",
            "sell_price": "",
            "gwp_text": "買即送洗面乳30ml (總值$99)",
            "gwp_image": "https://example.com/gwp_image.png",
        }
