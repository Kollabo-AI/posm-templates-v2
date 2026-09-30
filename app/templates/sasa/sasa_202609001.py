from __future__ import annotations

import re
import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFilter

from ...base import POSMImplementation
from ...renderer.runtime import Bundle
from ...schema import CreateParams, GenerationResult
from ...renderer import (
    Affine, BoundingBox, Boxed, Circle, CircleStyle, DiagonalLine, Empty,
    Group, ImageBox, LayoutElement, LineStyle, Named, SemanticTextBinding,
    Text, TextLine, TextSpan, TextStyle,
)
from ...renderer.text import split_text
from .shared.discount import DiscountBallTextStyle, _discount_ball_text
from .shared.icon import resolve_icon
from .shared.preprocess import (
    gwp_image_references, hk_mo_price, normalize_currency_markers, optional_text,
    preprocess_vip_and_star_prices, recommended_price, required_text,
)
from .shared.price import PriceLineTextStyle, PriceSpanStyle, is_price_line, render_price_lines, split_price_lines
from .shared.product import PreparedProduct
from ...types import FabricCanvas, FabricImage
from ...util import image_to_base64
from ...renderer.raster import rasterize_fabric


TEMPLATE = "sasa_202609001"


def asset_root() -> Path:
    return Bundle.configured().root / "assets"
PINK = "#E7168A"
YELLOW = "#F7EF53"
BLACK = "#000000"
FONT_FAMILY = "Alibaba PuHuiTi"
TEMPLATE_SIZE = (1640, 1188)
MEASUREMENT_SCALE = TEMPLATE_SIZE[0]/3612


def _px(measured: float) -> float:
    """Convert original calibration pixels to native layout pixels."""
    return measured*MEASUREMENT_SCALE


LEFT_PANEL = BoundingBox(l=_px(306), t=_px(825), w=_px(1443), h=_px(1467))
RIGHT_PANEL = BoundingBox(l=_px(1847), t=_px(825), w=_px(1443), h=_px(1467))
CONTENT = BoundingBox(l=_px(346), t=_px(893), r=_px(1709), b=_px(2267))
ICON_BOX = BoundingBox(r=_px(1707), t=_px(825), w=_px(306), h=_px(160))
NAME_FONT_SIZE = _px(103)
NAME_LINE_GAP = _px(14)
FAB_FONT_SIZE = _px(90.5)
FAB_LINE_GAP = _px(4)
RECOMMENDED_FONT_SIZE = _px(78)
TNC_FONT_SIZE = _px(55)
PRICE_FONT_SIZE = _px(520)
PRICE_NOTE_FONT_SIZE = _px(132)
DISCOUNT_DIAMETER = _px(300)
ONE_MOUTH_SIZE = _px(680)
NAME_MIN_HORIZONTAL_SCALE = 0.58
GIFT_FONT_SIZE = _px(70)
GIFT_IMAGE_SIZE = _px(350)
GIFT_WIDTH = _px(560)

Promotion = dict[str, str | list[str]]


def _bounds(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError("Expected visible layout content")
    return box


def _occupied_mask(elements: list[LayoutElement]) -> Image.Image:
    objects = [obj for element in elements for obj in element.render()]
    canvas = FabricCanvas(objects=objects, width=TEMPLATE_SIZE[0], height=TEMPLATE_SIZE[1])
    return rasterize_fabric(canvas).getchannel("A").filter(ImageFilter.MaxFilter(21))


def _style(
    size: float, fill: str = BLACK, *, weight: int = 700,
    align: Literal["left", "center", "right"] = "center", compression: float = 0.8,
) -> TextStyle:
    return TextStyle(
        font_family=FONT_FAMILY, font_size=size, font_weight=weight, fill=fill,
        text_align=align, vertical_align="top", compression=compression,
        tracking=-35,
    )


def _text(value: str, size: float, fill: str, name: str, width: float) -> Text:
    return Text(
        text=value, box=BoundingBox(l=0, t=0, w=width, h=size),
        style=_style(size, fill), name=name,
    )


def _move(element: LayoutElement, left: float, top: float) -> LayoutElement:
    box = _bounds(element)
    return Affine(element=element, translate_x=left-box.l, translate_y=top-box.t)


def _fit_width(element: LayoutElement, width: float) -> LayoutElement:
    source = _bounds(element)
    return Boxed(
        element, source_box=source,
        box=BoundingBox(l=source.l, t=source.t, w=min(width, source.w), h=source.h),
    )


def _center(element: LayoutElement, top: float, left: float = CONTENT.l,
            right: float = CONTENT.r) -> LayoutElement:
    element = _fit_width(element, right-left)
    return _move(element, (left+right-_bounds(element).w)/2, top)


def _name_row(element: LayoutElement, top: float, right: float) -> LayoutElement:
    half_width = min(CONTENT.cx-CONTENT.l, right-CONTENT.cx)
    return _center(element, top, CONTENT.cx-half_width, CONTENT.cx+half_width)


def _wrap(value: str, size: float, width: float) -> list[str]:
    result: list[str] = []
    for paragraph in value.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for count in range(1, len(paragraph)+1):
            lines = split_text(paragraph, count).splitlines()
            if all(_bounds(_text(line, size, BLACK, "Measure", _px(20000))).w <= width
                   for line in lines):
                result.extend(lines)
                break
        else:
            result.append(paragraph)
    return result


def _bind(element: LayoutElement, index: int, field: str) -> LayoutElement:
    return SemanticTextBinding(
        element=element, template=TEMPLATE, field=f"promotion_list.{index}.{field}",
        role="formatted-line",
    )


def _product_rows(value: str, star: bool) -> list[str]:
    paragraphs = [line.strip() for line in value.splitlines() if line.strip()]
    max_rows = 1 if star else 2
    if len(paragraphs) > max_rows:
        if star:
            return [" ".join(paragraphs)]

        def width(lines: list[str]) -> float:
            return _bounds(_text(" ".join(lines), NAME_FONT_SIZE, BLACK, "Measure", _px(20000))).w

        cut = min(range(1, len(paragraphs)),
                  key=lambda index: max(width(paragraphs[:index]), width(paragraphs[index:])))
        return [" ".join(paragraphs[:cut]), " ".join(paragraphs[cut:])]
    rows = [line for paragraph in paragraphs
            for line in ([paragraph] if paragraph.startswith(("(", "（"))
                         else _wrap(paragraph, NAME_FONT_SIZE, CONTENT.w/NAME_MIN_HORIZONTAL_SCALE))]
    return split_text(" ".join(paragraphs), max_rows).splitlines() if len(rows) > max_rows else rows


def _identity(fields: Promotion, has_icon: bool, star: bool = False) -> tuple[LayoutElement, float]:
    brand = required_text(fields, "brand_name")
    product = required_text(fields, "product_name")
    brand_element = _text(brand, NAME_FONT_SIZE, PINK, "Brand name", _px(20000))
    explicit_lines = [line.strip() for line in product.splitlines() if line.strip()]
    first = _text(explicit_lines[0], NAME_FONT_SIZE, BLACK, "Product name", _px(20000))
    first_right = ICON_BOX.l-_px(24) if has_icon else CONTENT.r
    first_width = first_right-CONTENT.l
    rows: list[LayoutElement] = []
    top = CONTENT.t
    if len(explicit_lines) > 1 and _bounds(brand_element).w + _bounds(first).w + _px(20) <= first_width/0.92:
        row_height = max(_bounds(brand_element).h, _bounds(first).h)
        row = Group(elements=[_move(brand_element, 0, (row_height-_bounds(brand_element).h)/2),
                              _move(first, _bounds(brand_element).w+_px(20), 0)])
        rows.append(_name_row(row, top, first_right))
        top = _bounds(rows[-1]).b + NAME_LINE_GAP
        product = "\n".join(explicit_lines[1:])
    else:
        rows.append(_name_row(brand_element, top, first_right))
        top += NAME_FONT_SIZE+_px(8)
    if has_icon:
        top = max(top, ICON_BOX.b+_px(12))
    for line in _product_rows(product, star):
        size = _px(72) if line.startswith(("(", "（")) else NAME_FONT_SIZE
        row = _text(line, size, BLACK, "Product name", _px(20000))
        right = ICON_BOX.l-_px(24) if has_icon and top < ICON_BOX.b+_px(10) else CONTENT.r
        rows.append(_name_row(row, top, right))
        top = _bounds(rows[-1]).b + NAME_LINE_GAP
    return Group(elements=rows), top-NAME_LINE_GAP


def _copy(fields: Promotion, index: int, has_icon: bool, star: bool = False) -> LayoutElement:
    identity, bottom = _identity(fields, has_icon, star)
    identity = SemanticTextBinding(
        element=identity, template=TEMPLATE, field=f"promotion_list.{index}.brand_name",
        role="formatted-line", name_prefixes=("Brand name",),
    )
    identity = SemanticTextBinding(
        element=identity, template=TEMPLATE, field=f"promotion_list.{index}.product_name",
        role="formatted-line", name_prefixes=("Product name",),
    )
    elements: list[LayoutElement] = [identity]
    fab = optional_text(fields, "fab")
    if fab:
        bullets = [re.sub(r"^[•·]\s*", "", line.strip()) for line in fab.splitlines() if line.strip()]
        value = "  ".join(f"• {line}" for line in bullets)
        lines = ([value] if _bounds(_text(value, FAB_FONT_SIZE, PINK, "Measure", _px(20000))).w <= CONTENT.w
                 else [line for bullet in bullets for line in _wrap(f"• {bullet}", FAB_FONT_SIZE, CONTENT.w)])
        top = bottom+_px(16)
        rows: list[LayoutElement] = []
        for line in lines:
            row = _center(_text(line, FAB_FONT_SIZE, PINK, "Features and benefits", _px(20000)), top)
            rows.append(row)
            top = _bounds(row).b+FAB_LINE_GAP
        elements.append(_bind(Group(elements=rows), index, "fab"))
        bottom = _bounds(rows[-1]).b
    recommended = optional_text(fields, "price_recommended")
    if recommended:
        value = recommended_price(recommended, hk_mo_price(fields))
        label = _text("建議價", RECOMMENDED_FONT_SIZE, BLACK, "Recommended price label", _px(20000))
        price = _move(_text(value, RECOMMENDED_FONT_SIZE, BLACK, "Recommended price", _px(20000)),
                      _bounds(label).r+_px(2), 0)
        price = DiagonalLine(
            element=_bind(price, index, "price_recommended"),
            style=LineStyle(stroke=PINK, stroke_width=_px(6)),
            line_name="Recommended price strikethrough",
        )
        elements.append(_center(Group(elements=[label, price]), bottom+_px(16)))
    return Group(elements=elements)


def _terms(value: str | None, index: int) -> LayoutElement:
    if not value:
        return Empty()
    lines = _wrap(value, TNC_FONT_SIZE, CONTENT.w)
    rows: list[LayoutElement] = []
    for line_index, line in enumerate(lines):
        style = _style(TNC_FONT_SIZE, weight=400, align="left", compression=0.9)
        spans = [TextSpan(text=line, style=style, name="Terms and conditions")]
        if line.startswith(("可以", "不可")):
            spans = [TextSpan(text=line[:2], style=style.model_copy(update={"underline": True}),
                              name="Terms and conditions")]
            if line[2:]:
                spans.append(TextSpan(text=line[2:], style=style, name="Terms and conditions"))
        row = TextLine(spans, BoundingBox(l=0, t=line_index*_px(62), w=CONTENT.w, h=_px(62)), tight=True)
        rows.append(row)
    group = Group(elements=rows)
    return _bind(_move(group, CONTENT.l, CONTENT.b-_bounds(group).h), index, "tnc")


def _wide_price_range(value: str) -> bool:
    match = re.search(r"(\d[\d,]*)\s*-\s*(?:\$|MOP)?\s*(\d[\d,]*)", value)
    return bool(match and all(len(part.replace(",", "")) >= 4 for part in match.groups()))


def _price_style(star: bool, has_note: bool = False, mop: bool = False,
                 wide_range: bool = False) -> PriceLineTextStyle:
    def span(size: float, fill: str = PINK, outline: bool = False) -> PriceSpanStyle:
        style = _style(size, fill, align="left").model_copy(update={
            "vertical_align": "bottom", "tracking": -55,
            "border_color": YELLOW, "border_thickness": _px(22) if outline else 0,
            "border_line_join": "round",
        })
        return PriceSpanStyle(text_style=style, gap_before=0, gap_after=_px(5))
    size = _px(463) if star else (_px(490) if has_note else PRICE_FONT_SIZE)
    if wide_range:
        size *= 0.72
    suffix = span(size*0.41)
    currency = span(size*0.54, outline=star)
    if mop:
        currency = currency.model_copy(update={"text_style": currency.text_style.model_copy(
            update={"compression": 0.6})})
    return PriceLineTextStyle(
        currency=currency, price=span(size, outline=star),
        suffix=suffix, note=span(PRICE_NOTE_FONT_SIZE if not star else _px(92), BLACK),
        hyphen=span(size*0.65).model_copy(update={"align_to_previous": "center"}),
        slash=span(size*0.85),
        star=suffix.model_copy(update={"align_to_previous": "top"}),
        plus=suffix.model_copy(update={"align_to_next": "center"}),
        currency_gap_before_one=_px(4 if star else 35), currency_gap_other=0, line_gap=_px(12),
    )


def _price(value: str | None, star: bool, bottom: float, max_width: float,
           index: int) -> LayoutElement:
    if not value:
        return Empty()
    def position_line(line: LayoutElement) -> LayoutElement:
        line = _fit_width(line, max_width)
        return _move(line, 0, _bounds(line).t)

    lines = render_price_lines(
        value, BoundingBox(l=0, t=0, w=max_width, h=0),
        _price_style(star, any(not is_price_line(line) for line in split_price_lines(value)),
                     "MOP" in value, _wide_price_range(value)),
        expand_to_fit_content=True,
        transform_line=position_line,
    )
    lines = _bind(lines, index, "star_price" if star else "price_vip")
    if not star:
        return _move(lines, CONTENT.l+_px(12), bottom-_bounds(lines).h)
    logo_box = BoundingBox(l=CONTENT.l, t=bottom-ONE_MOUTH_SIZE,
                           w=ONE_MOUTH_SIZE, h=ONE_MOUTH_SIZE)
    logo = ImageBox(
        image=_asset("icons/sasa_202607001_onemouthprice.png"),
        box=logo_box,
        name="One-mouth price background",
    )
    source_box = _bounds(lines)
    line_group = lines.element
    if isinstance(line_group, SemanticTextBinding):
        line_group = line_group.element
    rows = line_group.elements if isinstance(line_group, Group) else []
    if len(rows) == 2:
        rows = [
            _move(rows[0], 0, _px(80)),
            rows[1],
        ]
        lines = lines.model_copy(update={"element": Group(elements=rows)})
        source_box = _bounds(lines)
        scale_x = 1.0
        scale_y = 0.8
        first_row = _bounds(rows[0])
        lines = Affine(
            element=lines,
            scale_x=scale_x,
            scale_y=scale_y,
            translate_x=logo_box.l+_px(35)-source_box.l*scale_x,
            translate_y=logo_box.t+_px(240)-first_row.t*scale_y,
        )
    else:
        max_width = logo_box.w-_px(24)
        max_height = logo_box.h-_px(112)
        scale = min(1.0, max_width/source_box.w, max_height/source_box.h)
        fitted_width = source_box.w*scale
        fitted_height = source_box.h*scale
        lines = Affine(
            element=lines,
            scale_x=scale,
            scale_y=scale,
            translate_x=logo_box.cx-fitted_width/2-source_box.l*scale,
            translate_y=logo_box.t+_px(100)+(max_height-fitted_height)/2-source_box.t*scale,
        )
    return Group(elements=[logo, lines])


def _discount(value: str, top: float, left: float, index: int, diameter: float) -> LayoutElement:
    style = DiscountBallTextStyle(
        font_family=FONT_FAMILY, fill=YELLOW, text_fill=YELLOW,
        primary_font_size=_px(235), secondary_font_size=_px(116), suffix_font_size=_px(65),
        primary_font_weight=900, secondary_font_weight=700, suffix_font_weight=700,
        line_gap=_px(4), compression=0.8,
    )
    content = _discount_ball_text(value, style)
    if match := re.fullmatch(r"(.+?)\s*再(\d{1,2})折([#*]?)", value, re.DOTALL):
        prefix, number, suffix = match.groups()
        heading = _text(prefix.strip(), _px(70), YELLOW, "Discount ball text", _px(1000))
        spans = [TextSpan(text=text, style=_style(_px(size), YELLOW, weight=weight).model_copy(
            update={"vertical_align": "bottom"}), name="Discount ball text")
            for text, size, weight in [("再", 110, 700), (number, 235, 900), ("折"+suffix, 110, 700)]]
        number_line = TextLine(spans, BoundingBox(l=0, t=0, w=_px(1000), h=_px(235)), tight=True)
        width = max(_bounds(heading).w, _bounds(number_line).w)
        content = Group(elements=[
            _move(heading, (width-_bounds(heading).w)/2, 0),
            _move(number_line, (width-_bounds(number_line).w)/2, _bounds(heading).h+_px(8)),
        ])
    box = _bounds(content)
    scale = min(1.0, diameter*0.94/math.hypot(box.w, box.h))
    content = Boxed(content, source_box=box, box=BoundingBox(
        l=left+(diameter-box.w*scale)/2,
        t=top+(diameter-box.h*scale)/2, w=box.w*scale, h=box.h*scale,
    ))
    return Group(elements=[
        Circle(box=BoundingBox(l=left, t=top, w=diameter, h=diameter),
               style=CircleStyle(fill_color=PINK), name="Discount ball background"),
        _bind(Named(element=content, name="Discount ball text"), index, "discount_ball"),
    ])


def _products(images: list[Image.Image], box: BoundingBox,
              avoid: list[BoundingBox], name: str, *, top_right: bool = False,
              occupied_mask: Image.Image | None = None) -> LayoutElement:
    if not images:
        return Empty()
    border = int(sum(TEMPLATE_SIZE)/100)
    search_box = BoundingBox(l=box.l-border, t=box.t-border,
                             r=box.r+border, b=box.b+border)
    result = PreparedProduct(images).place(
        canvas_box=search_box, template_size=TEMPLATE_SIZE,
        occupied_boxes=[] if occupied_mask is not None else avoid,
        occupied_mask=occupied_mask,
        anchor_x=box.r if top_right else box.cx, anchor_y=box.t if top_right else box.cy,
        anchor_position="top-right" if top_right else "center", target_product_size=10.0,
        allow_overlap_fallback=False, name=name,
    )
    if not result:
        raise ValueError(f"No space for {name.lower()} in the Highlight panel")
    return result


def _gift_caption(value: str | None, bottom: float, index: int,
                  left: float = CONTENT.l) -> LayoutElement:
    if not value:
        return Empty()
    line = _text(" ".join(value.split()), GIFT_FONT_SIZE, "#333333", "Gift description", _px(20000))
    line = _fit_width(line, CONTENT.r-left)
    return _bind(_move(line, CONTENT.r-_bounds(line).w, bottom-_bounds(line).h), index, "gwp_text")


def _gift(images: list[Image.Image], bottom: float) -> LayoutElement:
    if not images:
        return Empty()
    left = CONTENT.r-GIFT_IMAGE_SIZE
    elements: list[LayoutElement] = []
    image_box = BoundingBox(l=left, b=bottom, w=GIFT_IMAGE_SIZE, h=GIFT_IMAGE_SIZE)
    elements.append(_products(images, image_box, [], "Gift image", top_right=True))
    elements.append(Circle(
        element=_text("送", _px(150), YELLOW, "Gift badge text", _px(200)),
        box=BoundingBox(l=CONTENT.r-GIFT_WIDTH, t=image_box.t+_px(40), w=_px(190), h=_px(190)),
        style=CircleStyle(fill_color=PINK, fit_inset=0.8), name="Gift badge",
    ))
    return Group(elements=elements)


def panel_layout(fields: Promotion, images: list[Image.Image], gifts: list[Image.Image],
                 index: int) -> LayoutElement:
    vip, star = preprocess_vip_and_star_prices(
        optional_text(fields, "price_vip"), optional_text(fields, "star_price"), hk_mo_price(fields),
    )
    icon = resolve_icon(optional_text(fields, "icon_text"))
    copy = _copy(fields, index, icon is not None, bool(star))
    terms = _terms(optional_text(fields, "tnc"), index)
    bottom = (_bounds(terms).t if terms else CONTENT.b)-_px(16)
    gift_text = normalize_currency_markers(optional_text(fields, "gwp_text"), hk_mo_price(fields))
    caption = _gift_caption(gift_text, bottom if terms else CONTENT.b, index)
    if caption:
        caption_left = _bounds(terms).r+_px(24) if terms else CONTENT.r
        if CONTENT.r-caption_left >= CONTENT.w*0.45:
            caption = _gift_caption(gift_text, CONTENT.b, index, caption_left)
            bottom = min(_bounds(terms).t, _bounds(caption).t)-_px(18)
        else:
            bottom = _bounds(caption).t-_px(18)
    price_value = star or vip
    price_width = CONTENT.w-GIFT_WIDTH-_px(24) if gifts else CONTENT.w-_px(100 if star else 12)
    if star and "/" not in split_price_lines(star)[0]:
        price_width = min(price_width, ONE_MOUTH_SIZE-_px(80))
    elif len(images) > 2 and not gifts and not _wide_price_range(price_value or ""):
        price_width = min(price_width, CONTENT.w*0.55)
    price = _price(price_value, bool(star), bottom, price_width, index)
    elements: list[LayoutElement] = [copy, terms, caption]
    if icon is not None:
        elements.append(ImageBox(image=icon, box=ICON_BOX, name="Exclusive icon"))
    discount = optional_text(fields, "discount_ball")
    if discount:
        base_diameter = DISCOUNT_DIAMETER+_px(30) if "再" in discount else DISCOUNT_DIAMETER
        price_top = _bounds(price).t if price else bottom
        left = CONTENT.l if star else CONTENT.l+_px(24)
        top = None
        diameter = base_diameter
        for factor in (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58):
            diameter = base_diameter*factor
            preferred = price_top-diameter+_px(44 if star else -28)
            if not star:
                preferred = min(preferred, (_bounds(copy).b+price_top-diameter)/2)
            try:
                top, left = _discount_top(preferred, left, copy, price, bool(star), diameter)
                break
            except ValueError:
                continue
        if top is None:
            raise ValueError("Copy and price leave no room for the measured discount disc")
        ball = _discount(discount, top, left, index, diameter)
        elements.append(ball)
    elements.append(price)
    product_top = _bounds(copy).b+_px(18)
    elements.append(_gift(gifts, bottom))
    product_box = BoundingBox(
        l=CONTENT.l, t=product_top, r=CONTENT.r, b=bottom,
    )
    occupied = [box for element in elements for box in element.placement_boxes()]
    occupied_mask = _occupied_mask(elements)
    elements.append(_products(
        images, product_box, occupied, "Product image", top_right=True,
        occupied_mask=occupied_mask,
    ))
    return Group(elements=elements)


def _discount_top(preferred: float, left: float, copy: LayoutElement,
                  price: LayoutElement, star: bool, diameter: float) -> tuple[float, float]:
    radius = diameter/2
    occupied = copy.placement_boxes() + ([] if star else price.placement_boxes())
    horizontal_positions = [left, CONTENT.r-diameter-_px(8), CONTENT.cx-diameter/2]
    for candidate_left in dict.fromkeys(horizontal_positions):
        if candidate_left < CONTENT.l or candidate_left+diameter > CONTENT.r:
            continue
        for step in range(0, 500, 2):
            distance = _px(step)
            for top in (preferred-distance, preferred+distance):
                if top < CONTENT.t+_px(180) or top+diameter > CONTENT.b:
                    continue
                if star and top+diameter > _bounds(price).t+_px(56):
                    continue
                cx, cy = candidate_left+radius, top+radius
                if all(math.hypot(cx-min(max(cx, box.l-_px(8)), box.r+_px(8)),
                                  cy-min(max(cy, box.t-_px(8)), box.b+_px(8))) >= radius
                       for box in occupied):
                    return top, candidate_left
    raise ValueError("Copy and price leave no room for the measured discount disc")


@lru_cache(maxsize=2)
def _asset(relative: str) -> Image.Image:
    with Image.open(asset_root() / relative) as image:
        return image.convert("RGBA")


class Pipeline(POSMImplementation):
    def get_template(self) -> Image.Image:
        background = _asset("templates/sasa_202609001.png")
        if background.size != TEMPLATE_SIZE:
            raise ValueError(f"Highlight background must be {TEMPLATE_SIZE}, got {background.size}")
        return background.copy()

    @classmethod
    def get_expected_number_of_fields(cls) -> int:
        return 2

    def _load_images(self, references: list[str]) -> list[Image.Image]:
        images: list[Image.Image] = []
        for reference in references:
            image = self.maybe_load_image_from_url(reference)
            if image is None:
                raise ValueError(f"Could not load a supplied image: {reference}")
            images.append(image)
        return images

    def process(self, params: CreateParams) -> GenerationResult:
        if len(params.promotion_list) != 2 or len(params.product) != 2:
            raise ValueError("Highlight double requires two promotions and two product lists")
        panels: list[LayoutElement] = []
        for index, (fields, references, destination) in enumerate(zip(
            params.promotion_list, params.product, (LEFT_PANEL, RIGHT_PANEL), strict=True,
        )):
            if not fields and not references:
                continue
            content = panel_layout(fields, self._load_images(references),
                                   self._load_images(gwp_image_references(fields)), index)
            panels.append(Boxed(content, source_box=LEFT_PANEL, box=destination))
        background = self.get_template()
        canvas = FabricCanvas(
            width=background.width, height=background.height,
            objects=Group(elements=panels).render(),
            backgroundImage=FabricImage(
                src=image_to_base64(background, format="PNG"), left=0, top=0,
                width=background.width, height=background.height, strokeWidth=0,
            ),
        )
        preview = rasterize_fabric(canvas).convert("RGB")
        return GenerationResult(
            id=self._get_run_id(), reference_jpg=image_to_base64(preview, format="JPEG"),
            fabric_model=canvas, message=None, successful=True,
        )

    def get_schema(self) -> dict[str, str]:
        return {
            "hk_mo_price": "HK or MO (per panel)", "brand_name": "UNOVE",
            "product_name": "深度損傷頭髮修護洗頭水 500G", "price_recommended": "$135/件",
            "price_vip": "$99/件", "star_price": "$128", "price_non_vip": "",
            "sell_price": "", "fab": "有助清爽頭皮\n軟化髮絲", "discount_ball": "74折",
            "tnc": "可以與VIP優惠同時使用", "icon_text": "獨家代理",
            "gwp_text": "贈品說明", "gwp_image": "Gift image reference or list of references",
        }
