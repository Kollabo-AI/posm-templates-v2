from __future__ import annotations

from ..types import FabricCanvas, FabricObject, FabricGroup, FabricImage, FabricObjects
from ..util import image_to_base64
from .box import BoundingBox
from .primitives import *
from .. import logger
from ..util.browser import fabric_to_png

from copy import deepcopy

import numpy as np
import cv2
from PIL import Image
import math


def _render_image(image_element: ImageBox) -> FabricObjects:
    placement = _image_placement_box(image_element)
    image_width, image_height = image_element.image.size
    return [
        FabricImage(
            src=image_to_base64(image_element.image.convert("RGBA"), format="PNG"),
            name=image_element.name,
            left=placement.l,
            top=placement.t,
            width=image_width,
            height=image_height,
            scaleX=placement.w / image_width,
            scaleY=placement.h / image_height,
            fill="",
        )
    ]


def _image_placement_box(image_element: ImageBox) -> BoundingBox:
    contained_width, contained_height = _contained_size(
        image=image_element.image,
        box_width=image_element.box.w,
        box_height=image_element.box.h,
    )
    left = _aligned_left(
        box_left=image_element.box.l,
        box_width=image_element.box.w,
        box_right=image_element.box.r,
        width=contained_width,
        horizontal_align=image_element.style.horizontal_align,
    )
    top = _aligned_top(
        box_top=image_element.box.t,
        box_height=image_element.box.h,
        box_bottom=image_element.box.b,
        height=contained_height,
        vertical_align=image_element.style.vertical_align,
    )
    return BoundingBox(l=left, t=top, w=contained_width, h=contained_height)


def _contained_size(
    image: Image.Image,
    box_width: float,
    box_height: float,
) -> tuple[int, int]:
    image_width, image_height = image.size
    target_width = round(box_width)
    target_height = round(box_height)

    if image_width <= 0 or image_height <= 0:
        raise ValueError("ImageElement cannot render an image with zero width or height")
    if target_width <= 0 or target_height <= 0:
        raise ValueError("ImageElement cannot render into a box with zero width or height")

    scale = min(target_width / image_width, target_height / image_height)
    width = max(1, min(target_width, round(image_width * scale)))
    height = max(1, min(target_height, round(image_height * scale)))
    return width, height


def _aligned_left(
    box_left: float,
    box_width: float,
    box_right: float,
    width: int,
    horizontal_align: HorizontalAlign,
) -> int:
    if horizontal_align == "left":
        return round(box_left)
    if horizontal_align == "center":
        return round(box_left + (box_width - width) / 2)
    return round(box_right - width)


def _aligned_top(
    box_top: float,
    box_height: float,
    box_bottom: float,
    height: int,
    vertical_align: VerticalAlign,
) -> int:
    if vertical_align == "top":
        return round(box_top)
    if vertical_align == "center":
        return round(box_top + (box_height - height) / 2)
    return round(box_bottom - height)


def _render_circle(circle: Circle) -> FabricObjects:
    center = circle.center
    radius = circle.radius
    ellipse = FabricObject(
        type="ellipse",
        name=circle.name,
        left=center[0] - radius,
        top=center[1] - radius,
        width=radius * 2,
        height=radius * 2,
        rx=radius,
        ry=radius,
        fill=circle.style.fill_color,
        stroke=circle.style.outline_color if circle.style.outline_width > 0 else None,
        strokeWidth=circle.style.outline_width,
    )
    if not circle.element:
        return [ellipse]
    subcanvas = circle.element.render()
    group = _fit_models_on_circle(
        subcanvas,
        ellipse,
        circle.style.fit_inset,
        name=circle.content_name,
    )
    return [ellipse, group]


def _render_line(line: Line) -> FabricObject:
    width = abs(line.x2 - line.x1)
    height = abs(line.y2 - line.y1)
    return FabricObject(
        type="line",
        name=line.name,
        left=min(line.x1, line.x2),
        top=min(line.y1, line.y2),
        width=width,
        height=height,
        fill=None,
        stroke=line.style.stroke,
        strokeWidth=line.style.stroke_width,
        strokeDashArray=line.style.stroke_dash_array,
        strokeDashOffset=line.style.stroke_dash_offset,
        strokeLineCap=line.style.stroke_line_cap,
        strokeUniform=line.style.stroke_uniform,
        opacity=line.style.opacity,
        x1=-width / 2 if line.x1 <= line.x2 else width / 2,
        y1=-height / 2 if line.y1 <= line.y2 else height / 2,
        x2=width / 2 if line.x1 <= line.x2 else -width / 2,
        y2=height / 2 if line.y1 <= line.y2 else -height / 2,
    )


def _line_placement_box(line: Line) -> BoundingBox:
    half_stroke = line.style.stroke_width / 2
    delta_x = line.x2 - line.x1
    delta_y = line.y2 - line.y1
    length = math.hypot(delta_x, delta_y)

    if length == 0:
        return BoundingBox(
            l=line.x1 - half_stroke,
            t=line.y1 - half_stroke,
            w=line.style.stroke_width,
            h=line.style.stroke_width,
        )

    unit_x = abs(delta_x) / length
    unit_y = abs(delta_y) / length
    if line.style.stroke_line_cap == "butt":
        inset_x = half_stroke * unit_y
        inset_y = half_stroke * unit_x
    elif line.style.stroke_line_cap == "round":
        inset_x = half_stroke
        inset_y = half_stroke
    else:
        inset_x = half_stroke * (unit_x + unit_y)
        inset_y = inset_x

    return BoundingBox(
        l=min(line.x1, line.x2) - inset_x,
        t=min(line.y1, line.y2) - inset_y,
        r=max(line.x1, line.x2) + inset_x,
        b=max(line.y1, line.y2) + inset_y,
    )


def _fit_models_on_circle(
    elements: FabricObjects,
    discount_ball_object: FabricObject,
    inset: float = 0.95,
    name: str | None = None,
) -> FabricGroup:
    objects = deepcopy(elements)
    cw, ch = _render_size_for_objects([*objects, discount_ball_object])
    fabric = FabricCanvas(objects=objects, width=cw, height=ch)
    text_img = fabric_to_png(fabric)

    # Calculate visual center of the rendered stuff
    crop_box = text_img.getbbox(alpha_only=True)
    horizontal_scale = 0.9
    scaled_w = max(1, int(text_img.width * horizontal_scale))
    source_to_scaled_x = scaled_w / text_img.width
    if crop_box is None:
        logger.warning("Failed to get bounding box of rendered text. The text might be fully transparent.")
        cx, cy = text_img.width / 2, text_img.height / 2
        scale_factor = 1.0
    else:
        # Dynamically scale text to fit inside the discount ball.
        scaled_img = text_img.resize((scaled_w, text_img.height), Image.Resampling.LANCZOS)

        (cx, cy), radius = _find_smallest_enclosing_circle(scaled_img)
        ball_radius = (discount_ball_object.width * horizontal_scale) / 2
        scale_factor = (ball_radius * inset) / radius if radius > 0 else 1.0
        logger.info(f"Calculated visual center of discount ball text at ({cx:.2f}, {cy:.2f}) with enclosing circle radius {radius:.2f} and scale factor {scale_factor:.4f}")
        cx = cx / source_to_scaled_x

    # Anchor the rendered text visual center directly to the group center
    offset_x = -cx
    offset_y = -cy

    for obj in objects:
        obj.left = (obj.left + offset_x) * scale_factor
        obj.top = (obj.top + offset_y) * scale_factor
        obj.scaleX = (obj.scaleX or 1.0) * scale_factor
        obj.scaleY = (obj.scaleY or 1.0) * scale_factor

    group_left = discount_ball_object.left + (discount_ball_object.width * (1 - horizontal_scale) / 2)
    objs = FabricGroup(
        name=name,
        left=group_left,
        top=discount_ball_object.top,
        width=discount_ball_object.width,
        height=discount_ball_object.height,
        objects=objects,
        scaleX=horizontal_scale
    )

    return objs


def _render_size_for_objects(objects: FabricObjects) -> tuple[int, int]:
    width = 1
    height = 1
    for obj in objects:
        scale_x = obj.scaleX or 1.0
        scale_y = obj.scaleY or 1.0
        width = max(width, math.ceil(obj.left + obj.width * scale_x + 1))
        height = max(height, math.ceil(obj.top + obj.height * scale_y + 1))
    return width, height


def _find_smallest_enclosing_circle(pil_img: Image.Image) -> tuple[tuple[float, float], float]:
    if pil_img.mode != "RGBA":
        raise ValueError("The image does not have an alpha (transparency) channel. Ensure it is in 'RGBA' mode.")
    alpha_channel = np.asarray(pil_img.getchannel("A"))
    _, mask = cv2.threshold(alpha_channel, 0, 255, cv2.THRESH_BINARY)
    points = cv2.findNonZero(mask)
    if points is None:
        raise ValueError("The image is completely transparent. No circle can be found.")
    (x, y), radius = cv2.minEnclosingCircle(points)
    return (x, y), radius


def _transform_fabric_object(
    obj: FabricObject,
    scale_x: float,
    scale_y: float,
    translate_x: float,
    translate_y: float,
) -> FabricObject:
    return obj.model_copy(
        update={
            "left": obj.left * scale_x + translate_x,
            "top": obj.top * scale_y + translate_y,
            "scaleX": obj.scaleX * scale_x,
            "scaleY": obj.scaleY * scale_y,
        },
        deep=True,
    )


def _render_local_mask(element: LayoutElement, source_box: BoundingBox) -> Image.Image:
    if source_box.w <= 0 or source_box.h <= 0:
        raise ValueError(f"Cannot place element with empty placement box: {source_box}")

    from ..util.browser import fabric_to_png

    local_element = Affine(
        element=element,
        scale_x=1.0,
        scale_y=1.0,
        translate_x=-source_box.l,
        translate_y=-source_box.t,
    )
    canvas = FabricCanvas(
        objects=local_element.render(),
        width=max(1, math.ceil(source_box.r - source_box.l)),
        height=max(1, math.ceil(source_box.b - source_box.t)),
    )
    return fabric_to_png(canvas).convert("RGBA")


def _boxes_overlap(left: BoundingBox, right: BoundingBox) -> bool:
    return left.l < right.r and right.l < left.r and left.t < right.b and right.t < left.b


def _pack_vstack(
    ordered_elements: list[tuple[int, LayoutElement, BoundingBox]],
    scale: float,
    stack_top: float,
) -> tuple[list[tuple[int, LayoutElement]], float]:
    placed: list[tuple[int, LayoutElement]] = []
    occupied_boxes: list[BoundingBox] = []
    previous_top = stack_top
    stack_bottom = stack_top

    for index, element, element_box in ordered_elements:
        scaled_boxes = [
            BoundingBox(
                l=element_box.l + (box.l - element_box.l) * scale,
                t=(box.t - element_box.t) * scale,
                w=box.w * scale,
                h=box.h * scale,
            )
            for box in element.placement_boxes()
            if box.w > 0 and box.h > 0
        ]
        element_top = previous_top
        for box in scaled_boxes:
            for occupied_box in occupied_boxes:
                if box.l < occupied_box.r and occupied_box.l < box.r:
                    element_top = max(element_top, occupied_box.b - box.t)

        translate_x = element_box.l * (1 - scale)
        translate_y = element_top - element_box.t * scale
        transformed = Affine(
            element=element,
            scale_x=scale,
            scale_y=scale,
            translate_x=translate_x,
            translate_y=translate_y,
        )
        transformed_boxes = transformed.placement_boxes()
        placed.append((index, transformed))
        occupied_boxes.extend(transformed_boxes)
        previous_top = element_top
        if transformed_boxes:
            stack_bottom = max(stack_bottom, max(box.b for box in transformed_boxes))

    return placed, stack_bottom
