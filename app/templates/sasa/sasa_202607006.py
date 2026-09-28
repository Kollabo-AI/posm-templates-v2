# Uses the sunscreen template but adapted to the new handling of VIP price and 一口價 for 2026-07-15 demo

from __future__ import annotations
from decimal import Decimal, InvalidOperation

import os
import re
import math
import unicodedata
from functools import lru_cache as cache

from PIL import Image

from ...base import POSMImplementation
from ...schema import CreateParams, GenerationResult
from ...util import image_to_base64, load_image_from_url
from ...util.browser import (
    ALIBABA_PUHUITI_FONT_FACES,
    fabric_to_png,
    register_browser_font_faces,
)

from ... import logger
from ...types import FabricCanvas, FabricImage, FabricObjects, PosmBinding
from ...util import image_to_base64

from ...renderer import *
from .shared.icon import resolve_icon
from .shared.price import (
    PriceLineTextStyle,
    PriceSpanStyle,
    render_price_lines,
)
from .shared.discount import DiscountBallTextStyle, DiscountBall
from .shared.gift import _gwp_group_prepared
from .shared.product import PreparedProduct
from .shared.binding import (
    bind_promotion_text,
    conversion_binding_template,
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


FONT_FAMILY = "Alibaba PuHuiTi"
TEMPLATE_NAME = "sasa_202607006"
BRAND_NAME_FONT_SIZE = 52.09
BRAND_NAME_FONT_WEIGHT = 700
RECOMMENDED_PRICE_FONT_SIZE = 47.75
RECOMMENDED_PRICE_FONT_WEIGHT = 400
TNC_FONT_SIZE = 27.67
TNC_FONT_WEIGHT = 400
DISCOUNT_LABEL_FONT_SIZE = 60.78
DISCOUNT_LABEL_FONT_WEIGHT = 500
PRODUCT_NAME_FONT_SIZE = 52.09
PRODUCT_NAME_FONT_WEIGHT = 700
PRODUCT_NAME_LINE_GAP = 5
BRAND_NAME_ICON_GAP = 5
BRAND_NAME_LINE_GAP = 5
FAB_FONT_SIZE = 56.42
FAB_FONT_WEIGHT = 700
VIP_PRICE_FONT_SIZE = 123.72
VIP_CURRENCY_FONT_SIZE = 75
VIP_SUFFIX_FONT_SIZE = 55.89
VIP_PRICE_FONT_WEIGHT = 700
VIP_CURRENCY_FONT_WEIGHT = 700
VIP_SUFFIX_FONT_WEIGHT = 700
STAR_PRICE_FONT_SIZE = 123.72
STAR_CURRENCY_FONT_SIZE = 75
STAR_SUFFIX_FONT_SIZE = 55.89
STAR_PRICE_FONT_WEIGHT = 700
STAR_CURRENCY_FONT_WEIGHT = 700
STAR_SUFFIX_FONT_WEIGHT = 700
DISCOUNT_BALL_SIZE = 150
DISCOUNT_BALL_GAP_X = 150
DISCOUNT_BALL_GAP_Y = 0
DISCOUNT_BALL_INSET = 0.92
DISCOUNT_BALL_PRIMARY_FONT_SIZE = DISCOUNT_BALL_SIZE * 0.4
DISCOUNT_BALL_SECONDARY_FONT_SIZE = DISCOUNT_BALL_SIZE * 0.25
DISCOUNT_BALL_SUFFIX_FONT_SIZE = DISCOUNT_BALL_SIZE * 0.2
DISCOUNT_BALL_PRIMARY_FONT_WEIGHT = 700
DISCOUNT_BALL_SECONDARY_FONT_WEIGHT = 700
DISCOUNT_BALL_SUFFIX_FONT_WEIGHT = 500
DISCOUNT_BALL_LINE_GAP = 1
GWP_BALL_SIZE = 85
GWP_BALL_FONT_WEIGHT = 500
GWP_BALL_INSET = 0.92
GWP_TEXT_FONT_SIZE = 45
GWP_TEXT_HEIGHT_RATIO = 0.1
GWP_TEXT_FONT_WEIGHT = 700
GWP_TEXT_COLOR = "#333333"
GWP_TEXT_SPACING = 1
GWP_AREA_EXPANSION_RATIO = 0.02
ICON_WIDTH = 122
PRODUCT_ANCHOR_X = 756
PRODUCT_ANCHOR_Y = 657
DEFAULT_TEXT_COMPRESSION = 0.9
RETRY_TEXT_COMPRESSION = 0.75
BRAND_NAME_FIT_COMPRESSION = DEFAULT_TEXT_COMPRESSION
BRAND_NAME_WRAP_COMPRESSION = 0.75
BRAND_NAME_COMPRESSION_CLEARANCE = 1.0
CANVAS_SEARCH_TOLERANCE = 0.01


PINK = "#E7168A"
BLACK = "#000000"
WHITE = "#FFFFFF"

NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")


@cache
def get_template_path(template_path: str) -> str:
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template image not found at {template_path}")
    return template_path


@cache
def _get_template(template_path: str) -> Image.Image:
    return Image.open(get_template_path(template_path)).convert("RGBA")


def get_template(template_path: str, copy: bool = False) -> Image.Image:
    # Returns a copy of the template image to avoid modifying the cached version.
    if copy:
        return _get_template(template_path).copy()
    return _get_template(template_path)


@cache
def get_template_canvas() -> BoundingBox:
    return BoundingBox(l=100, t=184, w=816, h=761)


def _require_placement_box(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError(f"{type(element).__name__} has no placement box")
    return box


def _canvas_bounds(canvas: BoundingBox, template_size: tuple[int, int]) -> BoundingBox:
    w, h = template_size
    return BoundingBox(
        l=canvas.l + w * 0.02,
        t=canvas.t + h * 0.02,
        r=canvas.r - w * 0.02,
        b=canvas.b - h * 0.02,
    )


def _canvas_x(canvas: BoundingBox, value: float) -> float:
    default_canvas = get_template_canvas()
    return canvas.l + (value - default_canvas.l) * canvas.w / default_canvas.w


def _canvas_y(canvas: BoundingBox, value: float) -> float:
    default_canvas = get_template_canvas()
    return canvas.t + (value - default_canvas.t) * canvas.h / default_canvas.h


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
        fill=WHITE,
        text_fill=WHITE,
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
    elements: list[LayoutElement],
    template: Image.Image,
    layout_canvas: BoundingBox,
    output_canvas: BoundingBox,
) -> FabricCanvas:
    width, height = template.size
    objects: FabricObjects = []
    layout: LayoutElement = Group(elements=elements)
    if layout_canvas != output_canvas:
        layout = Boxed(layout, output_canvas, source_box=layout_canvas)
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


def _price_line_text_style(
    star_mode: bool = False,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
) -> PriceLineTextStyle:
    currency_font_size = STAR_CURRENCY_FONT_SIZE if star_mode else VIP_CURRENCY_FONT_SIZE
    currency_font_weight = STAR_CURRENCY_FONT_WEIGHT if star_mode else VIP_CURRENCY_FONT_WEIGHT
    price_font_size = STAR_PRICE_FONT_SIZE if star_mode else VIP_PRICE_FONT_SIZE
    price_font_weight = STAR_PRICE_FONT_WEIGHT if star_mode else VIP_PRICE_FONT_WEIGHT
    suffix_font_size = STAR_SUFFIX_FONT_SIZE if star_mode else VIP_SUFFIX_FONT_SIZE
    suffix_font_weight = STAR_SUFFIX_FONT_WEIGHT if star_mode else VIP_SUFFIX_FONT_WEIGHT

    def token_style(
        font_size: float,
        font_weight: int,
        vertical_align: VerticalAlign = "bottom",
    ) -> TextStyle:
        return _style(
            font_weight,
            PINK,
            vertical_align,
            text_compression,
        ).model_copy(update={"font_size": font_size})

    currency_style = token_style(currency_font_size, currency_font_weight)
    price_style = token_style(price_font_size, price_font_weight)
    suffix_style = token_style(suffix_font_size, suffix_font_weight)
    return PriceLineTextStyle(
        currency=PriceSpanStyle(
            text_style=currency_style,
            align_to_next="bottom",
        ),
        grouped_currency=PriceSpanStyle(
            text_style=currency_style,
            align_to_next="center",
        ),
        price=PriceSpanStyle(text_style=price_style),
        suffix=PriceSpanStyle(text_style=suffix_style),
        note=PriceSpanStyle(
            text_style=token_style(
                suffix_font_size,
                suffix_font_weight,
                "center",
            )
        ),
        hyphen=PriceSpanStyle(
            text_style=suffix_style if star_mode else price_style,
            gap_before=5,
            gap_after=5,
            align_to_previous=None if star_mode else "center",
            align_to_next="center" if star_mode else None,
        ),
        plus=PriceSpanStyle(
            text_style=suffix_style,
            align_to_next="center",
        ),
        star=PriceSpanStyle(
            text_style=suffix_style,
            align_to_previous="top",
        ),
        currency_gap_before_one=10,
        currency_gap_other=5,
        line_gap=1 if star_mode else 5,
    )


def _price_lines(
    price: str,
    canvas: BoundingBox,
    bottom_margin: float,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    star_mode: bool = False,
) -> LayoutElement:
    price_element = render_price_lines(
        price,
        BoundingBox(l=canvas.l, t=0, w=canvas.w, h=0),
        _price_line_text_style(
            star_mode=star_mode,
            text_compression=text_compression,
        ),
    )
    if isinstance(price_element, Empty):
        return price_element
    voffset = bottom_margin - _require_placement_box(price_element).b
    return Affine(
        element=price_element,
        translate_y=voffset
    )


def _fab(
    fab: str,
    canvas: BoundingBox,
    top_margin: float,
    bottom_margin: float,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    emit_bindings: bool = False,
    binding_template: str = TEMPLATE_NAME,
) -> list[LayoutElement]:
    if bottom_margin < top_margin:
        logger.warning(f"Bottom margin {bottom_margin} is less than top margin {top_margin}.")

    lines = [line.strip() for line in fab.splitlines() if line.strip()]
    elements: list[LayoutElement] = []
    current_top = 0.0

    for line_index, line in enumerate(lines):
        bullet = Text(
            text="•",
            box=BoundingBox(l=canvas.l, t=current_top, w=FAB_FONT_SIZE, h=FAB_FONT_SIZE),
            style=_style(FAB_FONT_WEIGHT, PINK, text_compression=text_compression),
        )
        text_left = _require_placement_box(bullet).r + 3
        elements.extend([
            bullet,
            Text(
                text=line,
                box=BoundingBox(l=text_left, t=current_top, r=canvas.r, h=FAB_FONT_SIZE),
                style=_style(FAB_FONT_WEIGHT, PINK, text_compression=text_compression),
                posm_binding=(
                    PosmBinding(
                        schemaVersion=1,
                        template=binding_template,
                        field="promotion_list.0.fab",
                        role="line",
                        index=line_index,
                    )
                    if emit_bindings
                    else None
                ),
            ),
        ])
        current_top += FAB_FONT_SIZE + 5

    if not elements:
        return []

    return [FitBetween(
        element=Group(elements=elements),
        top=top_margin,
        bottom=bottom_margin,
        vertical_align="center",
    )]


def _icon(icon_text: str | None, canvas: BoundingBox) -> list[LayoutElement]:
    icon = resolve_icon(icon_text)
    if not icon:
        return []
    scale = ICON_WIDTH / icon.width
    icon_box = BoundingBox(
        r=canvas.r - 11.47,
        t=canvas.t,
        w=ICON_WIDTH,
        h=icon.height * scale,
    )
    return [
        Named(
            element=ImageBox(
                image=icon,
                box=icon_box,
                style=ImageStyle(horizontal_align="right", vertical_align="top"),
            ),
            name="Promotion icon",
        )
    ]


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


def _brand_row_box(
    line_index: int,
    canvas: BoundingBox,
    icon_box: BoundingBox | None,
    line_gap: float = BRAND_NAME_LINE_GAP,
) -> BoundingBox:
    right = canvas.r
    if line_index == 0 and icon_box is not None:
        right = min(right, icon_box.l - BRAND_NAME_ICON_GAP)
    return BoundingBox(
        l=canvas.l,
        t=canvas.t + line_index * (BRAND_NAME_FONT_SIZE + line_gap),
        r=right,
        h=BRAND_NAME_FONT_SIZE,
    )


def _text_width(
    text: str,
    style: TextStyle,
    available_width: float | None = None,
) -> float:
    width = available_width or get_template_canvas().w
    measurement_width = max(
        width,
        len(text) * (style.font_size or BRAND_NAME_FONT_SIZE) * 2,
    )
    text_box = Text(
        text=text,
        box=BoundingBox(l=0, t=0, w=measurement_width, h=BRAND_NAME_FONT_SIZE),
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
        raise ValueError("A brand row must render at least one object")
    return min(compressions)


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


def _automatic_brand_name_lines(
    brand_name: str,
    brand_style: TextStyle,
    canvas: BoundingBox,
    icon_box: BoundingBox | None,
    line_gap: float = BRAND_NAME_LINE_GAP,
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
            "compression": BRAND_NAME_FIT_COMPRESSION,
            "minimum_compression": BRAND_NAME_FIT_COMPRESSION,
        }
    )
    lines: list[str] = []
    remaining = cleaned_brand_name
    while remaining:
        row_box = _brand_row_box(
            len(lines),
            canvas,
            icon_box,
            line_gap,
        )
        required_compression = _required_brand_compression(
            remaining,
            split_style,
            row_box.w,
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
    icon_box: BoundingBox | None,
    line_gap: float = BRAND_NAME_LINE_GAP,
) -> float:
    compressions = [brand_style.compression]
    for line_index, line in enumerate(brand_lines):
        row_box = _brand_row_box(line_index, canvas, icon_box, line_gap)
        measurement_box = BoundingBox(
            l=row_box.l,
            t=row_box.t,
            w=max(0.0, row_box.w - BRAND_NAME_COMPRESSION_CLEARANCE),
            h=row_box.h,
        )
        row = Text(
            text=line,
            box=measurement_box,
            style=brand_style,
        )
        compressions.append(_rendered_compression(row))
    return min(compressions)


def _align_brand_to_reference_bounds(
    brand_line: LayoutElement,
    reference_bounds: BoundingBox,
    left: float,
) -> LayoutElement:
    brand_bounds = _require_placement_box(brand_line)
    translate_x = left - brand_bounds.l
    if (
        brand_bounds.h <= 0
        or reference_bounds.h <= 0
        or (
            math.isclose(brand_bounds.t, reference_bounds.t, abs_tol=1e-6)
            and math.isclose(brand_bounds.b, reference_bounds.b, abs_tol=1e-6)
        )
    ):
        return Affine(element=brand_line, translate_x=translate_x)

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
            "compression": BRAND_NAME_FIT_COMPRESSION,
            "minimum_compression": BRAND_NAME_FIT_COMPRESSION,
        }
    )
    reference_font_size = reference_style.font_size or fit_box.h
    reference_width = max(
        fit_box.w,
        len(brand_name) * reference_font_size * 2,
    )
    reference_line = Text(
        text=brand_name,
        box=BoundingBox(
            l=fit_box.l,
            t=fit_box.t,
            w=reference_width,
            h=fit_box.h,
        ),
        style=reference_style,
    )
    reference_line_bounds = _require_placement_box(reference_line)
    reference_line = Affine(
        element=reference_line,
        translate_x=fit_box.l - reference_line_bounds.l,
    )
    reference_bounds = _require_placement_box(reference_line)

    fitted_line = Text(
        text=brand_name,
        box=fit_box,
        style=brand_style,
    )
    return _align_brand_to_reference_bounds(
        fitted_line,
        reference_bounds,
        fit_box.l,
    )


def _brand_name_group(
    brand_name: str,
    canvas: BoundingBox,
    icon_box: BoundingBox | None,
    text_compression: float,
    line_gap: float = BRAND_NAME_LINE_GAP,
) -> Group:
    brand_style = _style(
        BRAND_NAME_FONT_WEIGHT,
        PINK,
        text_compression=text_compression,
    ).model_copy(update={"font_size": BRAND_NAME_FONT_SIZE})
    brand_lines = _automatic_brand_name_lines(
        brand_name,
        brand_style,
        canvas,
        icon_box,
        line_gap,
    )
    fitted_style = brand_style
    if len(brand_lines) > 1:
        unified_compression = _unified_brand_compression(
            brand_lines,
            brand_style,
            canvas,
            icon_box,
            line_gap,
        )
        logger.debug(f"Brand name compression factor: {unified_compression:.3f}")
        fitted_style = brand_style.model_copy(
            update={
                "compression": unified_compression,
                "minimum_compression": unified_compression,
            }
        )
    return Group(
        elements=[
            _fit_brand_row(
                line,
                fitted_style,
                _brand_row_box(line_index, canvas, icon_box, line_gap),
            )
            for line_index, line in enumerate(brand_lines)
        ]
    )


def _product_name_box(
    brand_name_group: LayoutElement,
    canvas: BoundingBox,
) -> BoundingBox:
    return BoundingBox(
        l=canvas.l,
        t=(
            _require_placement_box(brand_name_group).b
            + 5
            + PRODUCT_NAME_LINE_GAP
        ),
        w=canvas.w,
        h=PRODUCT_NAME_FONT_SIZE,
    )


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
    star_price: str | None = None,
    icon_text: str | None = None,
    gwp_product_images: list[Image.Image] | None = None,
    gwp_text: str | None = None,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    canvas_bounds: BoundingBox | None = None,
    output_canvas_bounds: BoundingBox | None = None,
    prepared_product: PreparedProduct | None = None,
    prepared_gwp_product: PreparedProduct | None = None,
    emit_fab_bindings: bool = False,
    binding_template: str | None = None,
) -> FabricCanvas:
    template = get_template(template_path)
    template_canvas = canvas_bounds or get_template_canvas()
    output_canvas = output_canvas_bounds or template_canvas
    canvas_scale = template_canvas.w / output_canvas.w
    template_size = (
        max(template.width, math.ceil(template.width * canvas_scale)),
        max(template.height, math.ceil(template.height * canvas_scale)),
    )
    canvas = _canvas_bounds(template_canvas, template_size)
    elements: list[LayoutElement] = []

    icon_elements = _icon(icon_text, template_canvas)
    elements.extend(icon_elements)
    icon_box = (
        _require_placement_box(Group(elements=icon_elements))
        if icon_elements
        else None
    )

    brand_name_group = _brand_name_group(
        brand_name,
        canvas,
        icon_box,
        text_compression,
    )
    elements.append(
        bind_promotion_text(
            Named(element=brand_name_group, name="Brand name"),
            template=binding_template,
            field="brand_name",
            role="line",
        )
    )

    product_name_box = _product_name_box(brand_name_group, canvas)
    product_name_group = TextRows(
        text=product_name,
        box=product_name_box,
        style=_style(PRODUCT_NAME_FONT_WEIGHT, BLACK, text_compression=text_compression),
        spacing=PRODUCT_NAME_LINE_GAP,
    )
    elements.append(
        bind_promotion_text(
            Named(element=product_name_group, name="Product name"),
            template=binding_template,
            field="product_name",
            role="line",
        )
    )
    current_top = _require_placement_box(product_name_group).b + 10

    recommended_price_box_1 = Text(
        text="建議價",
        box=BoundingBox(l=canvas.l, t=current_top, w=canvas.w, h=RECOMMENDED_PRICE_FONT_SIZE),
        style=_style(RECOMMENDED_PRICE_FONT_WEIGHT, BLACK, text_compression=text_compression),
        name="Recommended price label",
    )
    recommended_price_box_2 = Named(
        element=DiagonalLine(
            element=bind_promotion_text(
                Text(
                    text=recommended_price,
                    box=BoundingBox(
                        l=_require_placement_box(recommended_price_box_1).r + 1,
                        t=current_top,
                        w=canvas.w,
                        h=RECOMMENDED_PRICE_FONT_SIZE,
                    ),
                    style=_style(
                        RECOMMENDED_PRICE_FONT_WEIGHT,
                        BLACK,
                        text_compression=text_compression,
                    ),
                    name="Recommended price",
                ),
                template=binding_template,
                field="price_recommended",
                role="token",
            ),
            style=LineStyle(stroke=PINK, stroke_width=4),
            line_name="Recommended price strikethrough",
        ),
        name="Recommended price",
    )
    elements.append(recommended_price_box_1)
    elements.append(recommended_price_box_2)
    fab_top_margin = _require_placement_box(recommended_price_box_1).b + 5
    current_bottom = canvas.b

    if tnc:
        tnc_box = bind_promotion_text(
            Text(
                text=tnc,
                box=BoundingBox(l=canvas.l, b=canvas.b, w=canvas.w, h=TNC_FONT_SIZE),
                style=_style(TNC_FONT_WEIGHT, BLACK, text_compression=text_compression),
                name="Terms and conditions",
            ),
            template=binding_template,
            field="tnc",
            role="line",
        )
        elements.append(tnc_box)
        current_bottom = _require_placement_box(tnc_box).t - 5

    if vip_price and star_price is not None:
        logger.warning("Both VIP price and star price are provided; using star price and ignoring VIP price.")

    price_element = (
        _price_lines(
            star_price,
            canvas,
            current_bottom,
            text_compression,
            star_mode=True,
        )
        if star_price is not None
        else _price_lines(vip_price, canvas, current_bottom, text_compression)
    )
    price_name = "Star price" if star_price is not None else "VIP price"
    price_element = bind_promotion_text(
        Named(element=price_element, name=price_name),
        template=binding_template,
        field="star_price" if star_price is not None else "price_vip",
        role="token",
    )
    elements.append(price_element)
    if price_element:
        current_bottom = _require_placement_box(price_element).t - 5
    price_box = (
        _require_placement_box(price_element)
        if price_element
        else BoundingBox(
            l=canvas.l,
            t=current_bottom,
            w=0,
            h=0,
        )
    )

    if star_price is not None and star_price.strip():
        star_price_label = Text(
            text="一口價",
            box=BoundingBox(
                l=canvas.l,
                b=current_bottom,
                w=canvas.w,
                h=STAR_SUFFIX_FONT_SIZE,
            ),
            style=_style(
                STAR_SUFFIX_FONT_WEIGHT,
                BLACK,
                "center",
                text_compression,
            ),
            name="Star price label",
        )
        elements.append(star_price_label)
        current_bottom = _require_placement_box(star_price_label).t - 5

    elements.extend(
        Named(element=element, name="Features and benefits")
        for element in _fab(
            fab,
            canvas,
            fab_top_margin,
            current_bottom,
            text_compression,
            emit_fab_bindings,
            binding_template or TEMPLATE_NAME,
        )
    )

    if discount_ball and discount_ball.strip():
        ball = bind_promotion_text(
            DiscountBall(
                text=discount_ball,
                anchor_x=price_box.r + DISCOUNT_BALL_GAP_X,
                anchor_y=price_box.b - DISCOUNT_BALL_GAP_Y,
                diameter=DISCOUNT_BALL_SIZE,
                fill_color=PINK,
                text_style=_discount_ball_text_style(text_compression),
                inset=DISCOUNT_BALL_INSET,
                canvas_box=canvas,
                template_size=template_size,
                occupied_boxes=Group(elements=elements).placement_boxes(),
            ),
            template=binding_template,
            field="discount_ball",
            role="token",
        )
        elements.append(ball)

    if prepared_product is None:
        prepared_product = PreparedProduct(product_images)
    if gwp_product_images and prepared_gwp_product is None:
        prepared_gwp_product = PreparedProduct(gwp_product_images)

    has_gwp_product = bool(prepared_gwp_product)
    has_gwp_text = bool(gwp_text and gwp_text.strip())

    product_element: LayoutElement | None = None
    if has_gwp_product or has_gwp_text:
        gwp_ball_text = "再送" if "送" in (discount_ball or "") else "送"
        logger.debug(f"Using GWP ball text: {gwp_ball_text}")
        product_element = bind_promotion_text(
            _gwp_group_prepared(
                prepared_product=prepared_product,
                prepared_gwp_product=prepared_gwp_product,
                gwp_text=gwp_text,
                gwp_ball_text=gwp_ball_text,
                template_canvas=template_canvas,
                template_size=template_size,
                occupied_boxes=Group(elements=elements).placement_boxes(),
                product_anchor_x=_canvas_x(template_canvas, PRODUCT_ANCHOR_X),
                product_anchor_y=_canvas_y(template_canvas, PRODUCT_ANCHOR_Y),
                gwp_ball_size=GWP_BALL_SIZE,
                gwp_ball_fill=PINK,
                gwp_text_style=_style(
                    GWP_TEXT_FONT_WEIGHT,
                    GWP_TEXT_COLOR,
                    text_compression=text_compression,
                ).model_copy(update={"font_size": GWP_TEXT_FONT_SIZE}),
                gwp_text_spacing=GWP_TEXT_SPACING,
                gwp_ball_text_style=TextStyle(
                    font_family=FONT_FAMILY,
                    font_weight=700,
                    fill=WHITE,
                    text_align="center",
                    vertical_align="bottom",
                    compression=text_compression,
                    minimum_compression=text_compression,
                ),
                gwp_ball_inset=GWP_BALL_INSET,
                gwp_expansion_ratio=GWP_AREA_EXPANSION_RATIO,
                gwp_text_height_ratio=GWP_TEXT_HEIGHT_RATIO,
            ),
            template=binding_template,
            field="gwp_text",
            role="line",
            name_prefixes=("Gift description",),
        )
    elif prepared_product:
        product_element = prepared_product.place(
            canvas_box=template_canvas,
            template_size=template_size,
            occupied_boxes=Group(elements=elements).placement_boxes(),
            anchor_x=_canvas_x(template_canvas, PRODUCT_ANCHOR_X),
            anchor_y=_canvas_y(template_canvas, PRODUCT_ANCHOR_Y),
        )

    if product_element is not None:
        elements.append(product_element)

    return _models_to_canvas(elements, template, template_canvas, output_canvas)


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
    star_price: str | None = None,
    icon_text: str | None = None,
    gwp_product_images: list[Image.Image] | None = None,
    gwp_text: str | None = None,
    text_compression: float = DEFAULT_TEXT_COMPRESSION,
    canvas_bounds: BoundingBox | None = None,
    emit_fab_bindings: bool = False,
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

    def attempt(compression: float, canvas_scale: float) -> FabricCanvas:
        layout_canvas = BoundingBox(
            l=output_canvas.l * canvas_scale,
            t=output_canvas.t * canvas_scale,
            w=output_canvas.w * canvas_scale,
            h=output_canvas.h * canvas_scale,
        )
        return get_layout_inner(
            template_path=template_path,
            brand_name=brand_name,
            product_name=product_name,
            recommended_price=recommended_price,
            vip_price=vip_price,
            fab=fab,
            product_images=product_images,
            tnc=tnc,
            discount_ball=discount_ball,
            star_price=star_price,
            icon_text=icon_text,
            gwp_product_images=gwp_product_images,
            gwp_text=gwp_text,
            text_compression=compression,
            canvas_bounds=layout_canvas,
            output_canvas_bounds=output_canvas,
            prepared_product=prepared_product,
            prepared_gwp_product=prepared_gwp_product,
            emit_fab_bindings=emit_fab_bindings,
            binding_template=binding_template,
        )

    return attempt(text_compression, 1.0)


class Pipeline(POSMImplementation):
    @property
    def template_path(self) -> str:
        return "resources/templates/sasa_202604002.png"

    def get_template(self) -> Image.Image:
        return get_template(self.template_path)

    @classmethod
    def get_expected_number_of_fields(cls) -> int:
        return 1

    def process(self, params: CreateParams) -> GenerationResult:
        fields = params.promotion_list[0]
        region = hk_mo_price(fields)
        binding_template = conversion_binding_template(self.name, region)

        product_images = [self.maybe_load_image_from_url(url) for url in params.product[0]]
        product_images = [img for img in product_images if img is not None]

        gwp_product_images = [
            self.maybe_load_image_from_url(reference)
            for reference in gwp_image_references(fields)
        ]
        gwp_product_images = [img for img in gwp_product_images if img is not None] or None

        vip_price, star_price = preprocess_vip_and_star_prices(
            optional_text(fields, "price_vip"),
            optional_text(fields, "star_price"),
            region,
        )

        # Full price normalization is limited to the three rendered price fields.
        # GWP monetary copy receives marker replacement only; discount-ball and
        # other promotion copy pass through literally.
        fabric_model = get_layout(
            template_path=self.template_path,
            brand_name=required_text(fields, "brand_name"),
            product_name=required_text(fields, "product_name"),
            recommended_price=recommended_price(
                required_text(fields, "price_recommended"),
                region,
            ),
            vip_price=vip_price,
            fab=required_text(fields, "fab"),
            product_images=product_images,
            tnc=optional_text(fields, "tnc"),
            discount_ball=optional_text(fields, "discount_ball"),
            star_price=star_price,
            icon_text=optional_text(fields, "icon_text"),
            gwp_product_images=gwp_product_images,
            gwp_text=normalize_currency_markers(
                optional_text(fields, "gwp_text"),
                region,
            ),
            emit_fab_bindings=binding_template is not None,
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
            "tnc": "#以上折扣以VIP價計算",
            "icon_text": "獨家發售",
            "sell_price": "",
            "gwp_text": "買即送洗面乳30ml (總值$99)",
            "gwp_image": "https://example.com/gwp_image.png",
        }
