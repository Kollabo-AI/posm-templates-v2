"""Composable layout primitives and convenience constructors for poster rendering."""

from __future__ import annotations

import re
from math import cos, radians, sin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal, TypeAlias
import typing as ty

from PIL import Image, ImageDraw

from ..types import FabricObjects, PosmBinding
from .anchor import AnchorPosition, anchor_factors
from .text import split_text
from .base import LayoutElement

from .box import BoundingBox
from .. import logger

if ty.TYPE_CHECKING:
    from .textbox.bounds import TextRasterMask


TextAlign: TypeAlias = Literal["left", "center", "right"]  # We might want to add "justify" in the future. Let's see
HorizontalAlign: TypeAlias = Literal["left", "center", "right"]
VerticalAlign: TypeAlias = Literal["top", "center", "bottom"]
TextVerticalAlign: TypeAlias = Literal["top", "center", "bottom", "none"]
FontStyle: TypeAlias = Literal["normal", "italic", "oblique"]
LineCap: TypeAlias = Literal["butt", "round", "square"]
LineJoin: TypeAlias = Literal["bevel", "round", "miter"]


def _require_placement_box(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError(f"{type(element).__name__} has no placement box")
    return box


class TextStyle(BaseModel):
    """Visual and typographic settings for a text element."""

    font_family: str = "Alibaba PuHuiTi"
    font_size: float | None = None
    font_weight: int = 400
    font_style: FontStyle = "normal"
    fill: str = "#000000"
    border_color: str = "#000000"
    border_thickness: float = 0
    border_line_cap: LineCap = "butt"
    border_line_join: LineJoin = "miter"
    underline: bool = False
    text_align: TextAlign = "left"
    vertical_align: TextVerticalAlign = "center"
    tracking: float = -50.0
    compression: float = 0.9
    minimum_compression: float | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class ImageStyle(BaseModel):
    """Alignment settings for an image within its bounding box."""

    horizontal_align: HorizontalAlign = "center"
    vertical_align: VerticalAlign = "center"

    model_config = ConfigDict(frozen=True, extra="forbid")


class CircleStyle(BaseModel):
    """Fill, outline, and content-fit settings for a circle."""

    fill_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 0
    fit_inset: float = 0.95

    model_config = ConfigDict(frozen=True, extra="forbid")


class LineStyle(BaseModel):
    """Stroke settings for a line."""

    stroke: str = "#000000"
    stroke_width: float = Field(default=1, ge=0)
    stroke_dash_array: list[float] | None = None
    stroke_dash_offset: float = 0
    stroke_line_cap: LineCap = "butt"
    stroke_uniform: bool = False
    opacity: float = Field(default=1, ge=0, le=1)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("stroke_dash_array")
    @classmethod
    def validate_stroke_dash_array(cls, values: list[float] | None) -> list[float] | None:
        if values is not None and any(value < 0 for value in values):
            raise ValueError("stroke_dash_array values must be non-negative")
        return values


class TextSpan(BaseModel):
    """A styled span used to construct a text line."""

    text: str
    style: TextStyle
    name: str | None = None
    posm_binding: PosmBinding | None = None
    gap_after: float = 0
    align_to_previous: VerticalAlign | None = None
    align_to_next: VerticalAlign | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_relative_alignment(self) -> TextSpan:
        if self.align_to_previous is not None and self.align_to_next is not None:
            raise ValueError("TextSpan cannot align to both the previous and next spans")
        return self

    @property
    def font_size(self):
        return self.style.font_size


class Text(LayoutElement):
    """A text box that can be placed in a layout."""
    text: str
    box: BoundingBox
    style: TextStyle
    name: str | None = None
    posm_binding: PosmBinding | None = None

    def placement_boxes(self) -> list[BoundingBox]:
        from .textbox import text_placement_box
        return text_placement_box(self)

    def render(self) -> FabricObjects:
        from .textbox import render_text
        text_object = render_text(self)
        if self.posm_binding is not None:
            text_object = text_object.model_copy(
                update={"posmBinding": self.posm_binding},
                deep=True,
            )
        if self.name is not None:
            text_object = text_object.model_copy(update={"name": self.name})
        return [text_object]


class ImageBox(LayoutElement):
    """An image that can be placed in a layout."""
    image: Image.Image
    box: BoundingBox
    style: ImageStyle = Field(default_factory=ImageStyle)
    name: str | None = None

    def placement_boxes(self) -> list[BoundingBox]:
        from .helpers import _image_placement_box
        return [_image_placement_box(self)]

    def render(self) -> FabricObjects:
        from .helpers import _render_image
        return _render_image(self)


class Circle(LayoutElement):
    """A circle that can contain other layout elements."""
    element: LayoutElement | None = None
    box: BoundingBox
    style: CircleStyle = Field(default_factory=CircleStyle)
    name: str | None = None
    content_name: str | None = None

    @property
    def center(self) -> tuple[float, float]:
        return (self.box.cx, self.box.cy)

    @property
    def radius(self) -> float:
        return min(self.box.w, self.box.h) / 2

    def placement_boxes(self) -> list[BoundingBox]:
        stroke_offset = self.style.outline_width / 2
        x, y = self.center
        radius = self.radius
        return [BoundingBox(
            l=x - radius - stroke_offset,
            t=y - radius - stroke_offset,
            w=radius * 2 + self.style.outline_width,
            h=radius * 2 + self.style.outline_width,
        )]

    def render(self) -> FabricObjects:
        from .helpers import _render_circle
        return _render_circle(self)


class Line(LayoutElement):
    """A straight line between two points."""

    x1: float
    y1: float
    x2: float
    y2: float
    style: LineStyle = Field(default_factory=LineStyle)
    name: str | None = None

    def placement_boxes(self) -> list[BoundingBox]:
        from .helpers import _line_placement_box
        return [_line_placement_box(self)]

    def render(self) -> FabricObjects:
        from .helpers import _render_line
        return [_render_line(self)]


class Group(LayoutElement):
    """A group of layout elements that can be placed together."""
    elements: list[LayoutElement] = Field(default_factory=list)

    @field_validator("elements", mode="before")
    @classmethod
    def _flatten_elements(cls, elements: object) -> object:
        if not isinstance(elements, list):
            return elements

        flattened: list[object] = []

        def append_items(items: list[object]) -> None:
            for item in items:
                if isinstance(item, Group):
                    append_items(item.elements)  # type: ignore
                elif isinstance(item, Empty):
                    pass
                elif isinstance(item, list):
                    append_items(item)
                else:
                    flattened.append(item)

        append_items(elements)
        return flattened

    def render(self) -> FabricObjects:
        """Renders the group into a list of FabricObjects."""
        objects: FabricObjects = []
        for element in self.elements:
            objects.extend(element.render())
        return objects

    def placement_boxes(self) -> list[BoundingBox]:
        boxes: list[BoundingBox] = []
        for element in self.elements:
            boxes.extend(element.placement_boxes())
        return boxes


class Named(LayoutElement):
    """Attach an editor-facing name without changing layout or Fabric geometry."""

    element: LayoutElement
    name: str

    def placement_boxes(self) -> list[BoundingBox]:
        return self.element.placement_boxes()

    def render(self) -> FabricObjects:
        objects = self.element.render()
        object_count = len(objects)
        named_objects: FabricObjects = []
        for index, obj in enumerate(objects):
            if obj.name:
                named_objects.append(obj)
                continue
            object_name = (
                self.name
                if object_count == 1
                else f"{self.name} {index + 1}"
            )
            named_objects.append(obj.model_copy(update={"name": object_name}, deep=True))
        return named_objects


class SemanticTextBinding(LayoutElement):
    """Attach one semantic source field to matching rendered text objects.

    Layout helpers frequently expand one business value into several Fabric
    textboxes (wrapped rows, price tokens, or nested discount-ball content).
    This wrapper keeps that expansion geometry-neutral while assigning stable,
    contiguous renderer-owned binding indexes in rendered object order.

    ``name_prefixes`` is an internal layout selector for mixed semantic groups,
    such as the 07001 row that contains both brand and product name spans. The
    serialized Fabric ``name`` remains editor-facing metadata; consumers trust
    only the emitted ``posmBinding``.
    """

    element: LayoutElement
    template: str
    field: str
    role: str
    name_prefixes: tuple[str, ...] = ()

    def placement_boxes(self) -> list[BoundingBox]:
        return self.element.placement_boxes()

    def _matches_name(self, name: str | None) -> bool:
        if not self.name_prefixes:
            return True
        if name is None:
            return False
        return any(
            name == prefix or name.startswith(f"{prefix} ")
            for prefix in self.name_prefixes
        )

    def render(self) -> FabricObjects:
        next_index = 0

        def bind_object(obj):
            nonlocal next_index
            updated = obj
            nested = getattr(updated, "objects", None)
            if isinstance(nested, list):
                updated = updated.model_copy(
                    update={"objects": [bind_object(child) for child in nested]},
                    deep=True,
                )

            object_type = getattr(updated, "type", "")
            is_text = (
                isinstance(object_type, str)
                and object_type.lower() in {"text", "i-text", "textbox"}
            )
            if not is_text or not self._matches_name(getattr(updated, "name", None)):
                return updated

            # A nested semantic wrapper owns its binding. Do not overwrite it
            # merely because a broader mixed group is rendered around it.
            if getattr(updated, "posmBinding", None) is not None:
                return updated

            binding = PosmBinding(
                schemaVersion=1,
                template=self.template,
                field=self.field,
                role=self.role,
                index=next_index,
            )
            next_index += 1
            return updated.model_copy(update={"posmBinding": binding}, deep=True)

        return [bind_object(obj) for obj in self.element.render()]


def DiagonalLine(
    element: LayoutElement,
    style: LineStyle,
    line_name: str | None = None,
) -> LayoutElement:
    """Draw a diagonal line from the top-left to the bottom-right of an element's placement box."""
    box = element.placement_box()
    if box is None:
        return element
    return Group(elements=[
        element,
        Line(
            x1=box.l,
            y1=box.b,
            x2=box.r,
            y2=box.t,
            style=style,
            name=line_name,
        )
    ])


def FitBetween(
    element: LayoutElement,
    top: float,
    bottom: float,
    vertical_align: VerticalAlign = "center",
) -> LayoutElement:
    """Place an element between two vertical positions with the requested alignment."""
    box = element.placement_box()
    if box is None:
        return element
    if vertical_align == "top":
        translate_y = top - box.t
    elif vertical_align == "bottom":
        translate_y = bottom - box.b
    else:
        available_center = top + (bottom - top) / 2
        translate_y = available_center - box.cy
    return Affine(
        element=element,
        scale_x=1,
        scale_y=1,
        translate_x=0,
        translate_y=translate_y,
    )


def TextLine(
    spans: list[TextSpan],
    box: BoundingBox,
    tight: bool = False,
) -> LayoutElement:
    """Create a line of text whose spans share a common line height."""

    if not spans:
        return Empty()

    for index, span in enumerate(spans):
        if span.align_to_previous is not None and index == 0:
            raise ValueError("The first TextSpan cannot align to a previous span")
        if span.align_to_next is not None and index == len(spans) - 1:
            raise ValueError("The last TextSpan cannot align to a next span")
        if span.align_to_next is not None and spans[index + 1].align_to_previous is not None:
            raise ValueError("Two adjacent TextSpans cannot both have relative alignment")
        if span.align_to_next and span.align_to_previous:
            raise ValueError("A TextSpan cannot align to both the previous and next spans")

    lefts: list[float | None] = [None] * len(spans)
    lefts[0] = box.l

    boxes: list[LayoutElement | None] = [None] * len(spans)
    masks: list[TextRasterMask | None] = [None] * len(spans)
    placed: list[bool] = [False] * len(spans)
    while not all(placed):
        made_progress = False

        for i, span in enumerate(spans):
            if placed[i]:
                continue

            left = lefts[i]
            if left is None:
                continue

            logger.debug(f"Placing {span.text} (font size: {span.font_size})")
            provisional = False

            if span.align_to_previous is not None:
                if not placed[i - 1]:
                    continue
                previous_box = boxes[i - 1]
                assert previous_box is not None
                previous_placement_box = _require_placement_box(previous_box)
                new_style = span.style.model_copy(update={"vertical_align": span.align_to_previous})
                textbox = Text(
                    text=span.text,
                    box=BoundingBox(
                        l=left,
                        t=previous_placement_box.t,
                        r=box.r,
                        b=previous_placement_box.b,
                    ),
                    style=new_style,
                    name=span.name,
                    posm_binding=span.posm_binding,
                )
            elif span.align_to_next is not None:
                if not placed[i + 1]:
                    if boxes[i] is not None:
                        continue
                    provisional = True
                    textbox = Text(
                        text=span.text,
                        box=BoundingBox(
                            l=left,
                            t=box.t,
                            r=box.r,
                            b=box.b,
                        ),
                        style=span.style,
                        name=span.name,
                        posm_binding=span.posm_binding,
                    )
                else:
                    next_box = boxes[i + 1]
                    assert next_box is not None
                    next_placement_box = _require_placement_box(next_box)
                    new_style = span.style.model_copy(update={"vertical_align": span.align_to_next})
                    textbox = Text(
                        text=span.text,
                        box=BoundingBox(
                            l=left,
                            t=next_placement_box.t,
                            r=box.r,
                            b=next_placement_box.b,
                        ),
                        style=new_style,
                        name=span.name,
                        posm_binding=span.posm_binding,
                    )
            else:
                textbox = Text(
                    text=span.text,
                    box=BoundingBox(
                        l=left,
                        t=box.t,
                        r=box.r,
                        b=box.b,
                    ),
                    style=span.style,
                    name=span.name,
                    posm_binding=span.posm_binding,
                )

            placed_textbox: LayoutElement = textbox
            if tight:
                from .textbox import render_text_mask
                from .textbox.spacing import tight_horizontal_shift

                textbox_mask = render_text_mask(textbox)
                if i > 0:
                    previous_mask = masks[i - 1]
                    assert previous_mask is not None
                    shift = tight_horizontal_shift(previous_mask, textbox_mask)
                    if shift > 0:
                        placed_textbox = Affine(
                            element=placed_textbox,
                            translate_x=-shift,
                        )
                        textbox_mask = textbox_mask.translated(x=-shift)

                    gap = spans[i - 1].gap_after
                    if gap != 0:
                        placed_textbox = Affine(
                            element=placed_textbox,
                            translate_x=gap,
                        )
                        textbox_mask = textbox_mask.translated(x=gap)

                masks[i] = textbox_mask

            boxes[i] = placed_textbox
            placed[i] = not provisional

            if span.align_to_next is not None and not provisional:
                for downstream_index in range(i + 1, len(spans)):
                    boxes[downstream_index] = None
                    masks[downstream_index] = None
                    lefts[downstream_index] = None
                    placed[downstream_index] = False

            if i + 1 < len(spans):
                if tight:
                    current_mask = masks[i]
                    assert current_mask is not None
                    lefts[i + 1] = current_mask.box.r
                else:
                    textbox_box = _require_placement_box(textbox)
                    lefts[i + 1] = textbox_box.r + span.gap_after
            made_progress = True

        if not made_progress:
            raise RuntimeError("Could not place all TextSpans; check for circular alignment constraints")

    assert not any(box is None for box in boxes), "Not all TextSpans were placed successfully"
    return Group(elements=ty.cast(list[LayoutElement], boxes))


class Affine(LayoutElement):
    """A layout element transformed by independent scaling and translation.

    The scaling anchor is the top-left corner of the element's placement box. The translation is applied after scaling.
    """

    element: LayoutElement
    scale_x: float = 1.
    scale_y: float = 1.
    translate_x: float = 0.
    translate_y: float = 0.

    def placement_boxes(self) -> list[BoundingBox]:
        boxes: list[BoundingBox] = []
        for box in self.element.placement_boxes():
            left = box.l * self.scale_x + self.translate_x
            right = box.r * self.scale_x + self.translate_x
            top = box.t * self.scale_y + self.translate_y
            bottom = box.b * self.scale_y + self.translate_y
            boxes.append(BoundingBox(
                l=min(left, right),
                t=min(top, bottom),
                w=abs(right - left),
                h=abs(bottom - top),
            ))
        return boxes

    def render(self) -> FabricObjects:
        from .helpers import _transform_fabric_object
        return [
            _transform_fabric_object(obj, self.scale_x, self.scale_y, self.translate_x, self.translate_y)
            for obj in self.element.render()
        ]


def Boxed(
    element: LayoutElement,
    box: BoundingBox,
    source_box: BoundingBox | None = None,
) -> LayoutElement:
    """Scale and translate an element from its source bounds into a target box."""
    source = source_box if source_box is not None else element.placement_box()
    if source is None:
        return element
    scale_x = box.w / source.w if source.w > 0 else 1.0
    scale_y = box.h / source.h if source.h > 0 else 1.0
    return Affine(
        element=element,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=box.l - source.l * scale_x,
        translate_y=box.t - source.t * scale_y,
    )


class Rotate(LayoutElement):
    """Rotate an element clockwise around the top-left of its placement box."""

    element: LayoutElement
    angle: float

    def _rotate_point(self, x: float, y: float) -> tuple[float, float]:
        anchor = _require_placement_box(self.element)
        angle = radians(self.angle)
        cosine = cos(angle)
        sine = sin(angle)
        offset_x = x - anchor.l
        offset_y = y - anchor.t
        return (
            anchor.l + offset_x * cosine - offset_y * sine,
            anchor.t + offset_x * sine + offset_y * cosine,
        )

    def placement_boxes(self) -> list[BoundingBox]:
        boxes: list[BoundingBox] = []
        for box in self.element.placement_boxes():
            corners = [
                self._rotate_point(box.l, box.t),
                self._rotate_point(box.r, box.t),
                self._rotate_point(box.r, box.b),
                self._rotate_point(box.l, box.b),
            ]
            left = min(x for x, _ in corners)
            top = min(y for _, y in corners)
            right = max(x for x, _ in corners)
            bottom = max(y for _, y in corners)
            boxes.append(BoundingBox(l=left, t=top, r=right, b=bottom))
        return boxes

    def render(self) -> FabricObjects:
        objects: FabricObjects = []
        for obj in self.element.render():
            left, top = self._rotate_point(obj.left, obj.top)
            objects.append(obj.model_copy(
                update={
                    "left": left,
                    "top": top,
                    "angle": obj.angle + self.angle,
                },
                deep=True,
            ))
        return objects


def SmartVStack(elements: list[LayoutElement]) -> LayoutElement:
    """Resolve collisions by tightly repacking elements vertically, then uniformly scaling if needed."""
    # TODO implement this
    # Currently a No-Op
    logger.warning("SmartVStack is not yet implemented. This API might break in the future - use with caution.")
    return Group(elements=elements)


def Magnet(
    element: LayoutElement,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    anchor_x: float,
    anchor_y: float,
    avoid: list[BoundingBox],
    scale_factor: float = 1.0,
    fix_scale: bool = False,
    anchor_position: AnchorPosition = "center",
    allow_overlap_fallback: bool = True,
) -> LayoutElement:
    """Place an element's selected anchor near a target while avoiding occupied regions."""

    from .helpers import _render_local_mask
    from .place import place_mask

    anchor_factors(anchor_position)
    source_box = element.placement_box()
    if source_box is None:
        return element
    mask = _render_local_mask(element, source_box)
    placed_box = place_mask(
        mask=mask,
        canvas_box=canvas_box,
        template_size=template_size,
        occupied_boxes=avoid,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        scale_factor=scale_factor,
        fix_scale=fix_scale,
        anchor_position=anchor_position,
        allow_overlap_fallback=allow_overlap_fallback,
    )
    scale_x = placed_box.w / mask.width
    scale_y = placed_box.h / mask.height
    return Affine(
        element=element,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=placed_box.l - source_box.l * scale_x,
        translate_y=placed_box.t - source_box.t * scale_y,
    )


class Empty(LayoutElement):
    """The empty element. Use an explicit class inheritance so we can do smth like def f() -> Empty: ..."""

    def placement_boxes(self) -> list[BoundingBox]:
        return []

    def render(self) -> FabricObjects:
        return []


def TextRows(text: str, box: BoundingBox, style: TextStyle, spacing: float) -> Group:
    """Create uniformly compressed text rows starting at the supplied box."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    final_compression = 0.9
    for line in lines:
        tb = Text(
            text=line,
            box=BoundingBox(
                l=0,
                t=0,
                w=box.w,
                h=style.font_size or box.h,
            ),
            style=style,
        ).render()[0]
        compression = tb.scaleX / tb.scaleY
        final_compression = min(compression, final_compression)

    logger.debug(f"Compression factor: {final_compression:.3f}")
    models: list[LayoutElement] = []

    current_top = box.t
    for line in lines:
        logger.debug(f"Rendering product name line: {line}")
        tb = Text(
            text=line,
            box=BoundingBox(
                l=box.l,
                t=current_top,
                w=box.w,
                h=style.font_size or box.h,
            ),
            style=style.model_copy(update={"compression": final_compression}),
        )
        text_box = _require_placement_box(tb)
        current_top = text_box.b + spacing
        models.append(tb)

    return Group(elements=models)
