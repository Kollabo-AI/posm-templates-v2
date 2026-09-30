from __future__ import annotations
from PIL import Image, ImageFilter
import numpy as np
from math import radians, tan
from pydantic import BaseModel, ConfigDict, model_validator
from PIL.Image import Resampling
import hashlib
from PIL import Image
from typing import TypedDict

import os
import tempfile

from ....util.model import score_product_scale
from ....renderer.util import validate_box
from ....util import image_to_base64, load_image_from_url
from ....types import FabricGroup, FabricObject, FabricObjects
from ....renderer import *
from .... import logger


def generate_collage_layout(images: list[EditableImage]) -> ProductShot:
    """
    Given a list of PIL images, calculates a layout of bounding boxes (ltwh)
    using the heuristic image-combination strategy.
    """
    path_image_lookup: dict[str, EditableImage] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, img in enumerate(images):
            path = os.path.join(tmpdir, f"product_{i}.png")
            img.get_image().save(path)
            path_image_lookup[path] = img

        logger.info("Generating collage layout for product shot using heuristic strategy")
        boxes = process_multiple_images(
            image_paths=[os.path.join(tmpdir, f"product_{i}.png") for i in range(len(images))],
            output_dir=tmpdir,
        )

    if not boxes:
        logger.error("Heuristic collage layout generation failed due to unknown error.")
        raise RuntimeError("Heuristic collage layout generation failed due to unknown error.")

    l_offset: int = 0
    t_offset: int = 0

    for _, box in boxes:
        if box['l'] < 0:
            l_offset = max(l_offset, -box['l'])
        if box['t'] < 0:
            t_offset = max(t_offset, -box['t'])

    logger.debug(f"Applying offsets to generated layout: l_offset={l_offset}, t_offset={t_offset} to ensure all boxes have non-negative coordinates")

    new_w = max(box['l'] + box['w'] + l_offset for _, box in boxes) + 1
    new_h = max(box['t'] + box['h'] + t_offset for _, box in boxes) + 1

    positions: list[Coordinates] = []
    new_images: list[EditableImage] = []
    for k, box in boxes:
        new_images.append(path_image_lookup[k].set_size(int(box['w']), int(box['h'])))
        positions.append(Coordinates(
            x=int(box['l']) + l_offset,
            y=int(box['t']) + t_offset,
        ))

    shot = ProductShot(
        images=new_images,
        positions=positions,
        width=int(new_w),
        height=int(new_h),
    )

    return shot


def preprocess_product_ids(
    product_ids: list[ImageRef],
    apply_shadow: bool = True,
) -> ProductShot:
    """
    Pre-processes product images before placement:
      - If there is >1 product, combines them heuristically into one image.
      - If apply_shadow=True, applies a floor shadow to each (possibly combined) image.
    Returns a product shot.
    """
    if not product_ids:
        logger.warning("No product IDs provided for preprocessing.")
        return ProductShot.empty()

    images_edt: list[EditableImage] = []

    for pid in product_ids:
        image = pid.get_image()

        bbox = image.getbbox()
        if bbox:
            l = bbox[0]
            t = bbox[1]
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            crop_box = BoundingBox(
                l=float(l),
                t=float(t),
                w=float(w),
                h=float(h),
            )
        else:
            crop_box = BoundingBox(
                l=0.0,
                t=0.0,
                w=float(pid.width),
                h=float(pid.height),
            )

        editable_image = EditableImage(
            source=pid,
            crop_box=crop_box,
            width=int(crop_box.w) + 1,
            height=int(crop_box.h) + 1,
        )
        images_edt.append(editable_image)

    if len(images_edt) == 0:
        return ProductShot.empty()

    combine_image_coords: list[Coordinates]
    combine_images: list[EditableImage]
    if len(images_edt) == 1:
        logger.debug("Only one image, skipping combination and using the single image as the product shot.")
        combine_image_coords = [Coordinates(x=0, y=0)]
        combine_images = [images_edt[0]]
    else:
        logger.debug(f"Combining {len(images_edt)} images into one product shot using heuristic collage layout generation.")
        temp_product_shot = generate_collage_layout(images_edt)
        if len(temp_product_shot.images) != len(images_edt):
            logger.error(f"Heuristic collage layout generation failed: expected {len(images_edt)} images in the product shot, but got {len(temp_product_shot.images)}.")
            raise RuntimeError(
                f"Heuristic collage layout generation failed: expected {len(images_edt)} images in the product shot, but got {len(temp_product_shot.images)}."
            )
        combine_image_coords = temp_product_shot.positions
        combine_images = temp_product_shot.images
        logger.debug(
            "Combined reconstructed product shot from heuristic layout generation (%sx%s)",
            temp_product_shot.width,
            temp_product_shot.height,
        )

    imgs: list[EditableImage] = []
    shadows: list[EditableImage] = []
    img_coords: list[Coordinates] = []
    shadow_coords: list[Coordinates] = []

    for j, (img, base_coords) in enumerate(zip(combine_images, combine_image_coords)):
        if apply_shadow:
            logger.info(f"Applying floor shadow for {j}.")
            shadow, (off_x, off_y) = add_shadow(img.get_image(), shadow_type="drop", return_shadow_only=True)
            shadow_image = ImageRef.from_image(shadow)
            shadow_edt = EditableImage(
                source=shadow_image,
                crop_box=BoundingBox(
                    l=0,
                    t=0,
                    w=shadow.width,
                    h=shadow.height,
                ),
                width=shadow.width,
                height=shadow.height,
            )
            shadow_coords.append(Coordinates(x=base_coords.x - off_x, y=base_coords.y - off_y))
            shadows.append(shadow_edt)
        img_coords.append(base_coords)
        imgs.append(img)

    all_images = shadows + imgs
    all_coords = shadow_coords + img_coords

    x_offset = 0
    y_offset = 0
    for coords in all_coords:
        if coords.x < 0:
            x_offset = max(x_offset, -coords.x)
        if coords.y < 0:
            y_offset = max(y_offset, -coords.y)
    all_coords = [Coordinates(x=c.x + x_offset, y=c.y + y_offset) for c in all_coords]

    return ProductShot(
        images=all_images,
        positions=all_coords,
        width=max(c.x + img.width for c, img in zip(all_coords, all_images)),
        height=max(c.y + img.height for c, img in zip(all_coords, all_images)),
    )


class Box(TypedDict):
    l: int
    t: int
    w: int
    h: int


def _remove_white_background(_img):
    """
    Placeholder for optional background removal.
    """
    raise NotImplementedError("Background removal is currently disabled.")


def _get_content_bbox(img):
    """
    Get the bounding box of the non-transparent contents of an RGBA image.
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    return bbox if bbox else (0, 0, img.width, img.height)


def combine_images_heuristically(
    image_paths: list[str],
    output_path: str,
    target_height: int = 800,
) -> tuple[str, list[tuple[str, Box]]] | None:
    """
    Places all products horizontally side by side with minimal margin.
    Returns the output path on success, or None on failure.
    """
    logger.debug("\n[HEURISTIC FALLBACK] Placing products side by side...")
    processed_images = []

    for p in image_paths:
        try:
            img = Image.open(p).convert("RGBA")
            try:
                img = _remove_white_background(img)
            except NotImplementedError:
                logger.debug("[HEURISTIC FALLBACK] Background removal not implemented, using original image.")
            bbox = _get_content_bbox(img)
            img = img.crop(bbox)
            processed_images.append({
                "path": p,
                "filename": os.path.basename(p),
                "image": img,
                "w": img.width,
                "h": img.height,
            })
        except Exception as e:
            logger.debug(f"[HEURISTIC FALLBACK] Error processing {p}: {e}")
            return None

    if not processed_images:
        return None

    scaled = []
    for item in processed_images:
        scale = target_height / item["h"]
        new_w = max(1, int(item["w"] * scale))
        resized = item["image"].resize((new_w, target_height), Image.Resampling.LANCZOS)
        scaled.append({"path": item["path"], "filename": item["filename"], "image": resized, "w": new_w})

    padding = int(target_height * 0.025)

    placements = []
    x = 0
    for s in scaled:
        shadowed_img, (off_x, off_y) = add_shadow(s["image"], shadow_type="drop")

        placements.append({
            "path": s["path"],
            "filename": s["filename"],
            "img": shadowed_img,
            "x": x - off_x,
            "y": 0 - off_y,
            "orig_x": x,
            "orig_y": 0,
            "body_w": s["w"],
        })

        logger.debug(f"[HEURISTIC] [{s['filename']}] Product body at X={x}, W={s['w']}. Shadowed img pasted at X={x - off_x}")
        x += s["w"] + padding

    min_x = min(p["x"] for p in placements)
    min_y = min(p["y"] for p in placements)
    max_x = max(p["x"] + p["img"].width for p in placements)
    max_y = max(p["y"] + p["img"].height for p in placements)

    canvas_w = max_x - min_x
    canvas_h = max_y - min_y
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 0))

    final_boxes = []
    for p in placements:
        paste_x = p["x"] - min_x
        paste_y = p["y"] - min_y
        canvas.paste(p["img"], (paste_x, paste_y), p["img"])

        final_boxes.append((
            p["path"],
            {
                "l": p["orig_x"] - min_x,
                "t": p["orig_y"] - min_y,
                "w": p["body_w"],
                "h": target_height,
            }
        ))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    canvas.save(output_path)
    logger.debug(f"[HEURISTIC FALLBACK] Saved to: {output_path}")
    return output_path, final_boxes


def process_multiple_images(
    image_paths: list[str],
    output_dir: str,
) -> list[tuple[str, Box]]:
    """
    Combines images using the heuristic layout and returns product placements.
    """
    for p in image_paths:
        if not os.path.exists(p):
            logger.error(f"Error: Image not found at {p}")
            return []

    base_names = [os.path.splitext(os.path.basename(p))[0] for p in image_paths]
    base_filename = "combined_image_" + "_".join(base_names)

    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"{base_filename}.png")
    counter = 1
    while os.path.exists(output_file):
        output_file = os.path.join(output_dir, f"{base_filename}({counter}).png")
        counter += 1

    logger.debug("Using heuristic layout.")
    combined = combine_images_heuristically(image_paths, output_file)
    if combined is None:
        logger.error("Failed to combine images heuristically.")
        return []
    return combined[1]


class Coordinates(BaseModel):
    """This should denote absolute coordinates on a canvas in pixel space"""
    x: int
    y: int

    model_config = ConfigDict(frozen=True)


class ImageRef(BaseModel):
    data_or_url: str
    width: int
    height: int

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def check_dims(self) -> ImageRef:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"ImageRef must have positive dimensions, got width={self.width}, height={self.height}")

        try:
            image = load_image_from_url(self.data_or_url)
        except Exception as e:
            raise ValueError(f"Cannot load image data from {self.get_short_url()}: {e}") from e

        if image.width != self.width or image.height != self.height:
            raise ValueError(
                f"Image dimensions do not match for {self.get_short_url()}: "
                f"expected ({self.width}, {self.height}), got ({image.width}, {image.height})"
            )

        if not self.data_or_url.startswith("data:image/"):
            rgba_image = image if image.mode == "RGBA" else image.convert("RGBA")
            object.__setattr__(self, "data_or_url", image_to_base64(rgba_image))
        return self

    def get_base64_string(self) -> str:
        return self.data_or_url

    def get_image(self) -> Image.Image:
        image = load_image_from_url(self.data_or_url)
        return image if image.mode == "RGBA" else image.convert("RGBA")

    @classmethod
    def from_image(cls, img: Image.Image) -> ImageRef:
        return cls(
            data_or_url=image_to_base64(img),
            width=img.width,
            height=img.height,
        )

    @classmethod
    def from_url(cls, url: str) -> ImageRef:
        img = load_image_from_url(url)
        return cls.from_image(img)

    @classmethod
    def from_path(cls, path: str) -> ImageRef:
        img = load_image_from_url(path)
        return cls.from_image(img)

    @property
    def hash(self) -> str:
        return hashlib.md5(self.data_or_url.encode()).hexdigest()

    def get_short_url(self, max_length: int = 30) -> str:
        return self.data_or_url if len(self.data_or_url) <= max_length else self.data_or_url[:max_length - 3] + "..."

    def repr(self) -> str:
        return f"<{self.__class__.__name__}(url='{self.get_short_url()}', {self.width}x{self.height})>"

    def __repr__(self) -> str:
        return self.repr()

    def __str__(self) -> str:
        return self.repr()


class EditableImage(BaseModel):
    """An image produced by cropping and scaling a source image."""
    source: ImageRef
    crop_box: BoundingBox
    width: int
    height: int

    model_config = ConfigDict(frozen=True)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def set_crop(self, new_crop_box: BoundingBox) -> EditableImage:
        return EditableImage(
            source=self.source,
            crop_box=new_crop_box,
            width=self.width,
            height=self.height,
        )

    def set_size(self, new_width: int, new_height: int) -> EditableImage:
        return EditableImage(
            source=self.source,
            crop_box=self.crop_box,
            width=new_width,
            height=new_height,
        )

    def get_original(self) -> Image.Image:
        return self.source.get_image()

    def get_image(self) -> Image.Image:
        original = self.get_original()
        if (
            self.crop_box.l == 0
            and self.crop_box.t == 0
            and self.crop_box.r == original.width
            and self.crop_box.b == original.height
        ):
            cropped = original
        else:
            cropped = original.crop(
                (self.crop_box.l, self.crop_box.t, self.crop_box.r, self.crop_box.b)
            )
        if cropped.size == self.size:
            return cropped
        return cropped.resize(self.size, resample=Resampling.LANCZOS)

    @staticmethod
    def from_image(image: Image.Image) -> EditableImage:
        source = ImageRef.from_image(image)
        crop_box = BoundingBox(
            l=0,
            t=0,
            w=image.width,
            h=image.height,
        )
        return EditableImage(
            source=source,
            crop_box=crop_box,
            width=image.width,
            height=image.height,
        )


class ProductShot(BaseModel):
    """A product shot made from one or more editable image layers."""
    images: list[EditableImage]
    positions: list[Coordinates]
    width: int
    height: int

    model_config = ConfigDict(frozen=True)

    @staticmethod
    def empty() -> ProductShot:
        return ProductShot(
            images=[],
            positions=[],
            width=1,
            height=1,
        )

    @model_validator(mode="after")
    def validate_images_and_positions(self) -> ProductShot:
        if len(self.images) != len(self.positions):
            raise ValueError("The number of images and positions in a ProductShot must be the same")
        for i, (img, pos) in enumerate(zip(self.images, self.positions)):
            if pos.x < 0 or pos.y < 0:
                raise ValueError(f"Positions in a ProductShot must have non-negative coordinates, got position {i}: ({pos.x}, {pos.y})")
            if img.size[0] < 0 or img.size[1] < 0:
                raise ValueError(f"Images in a ProductShot must have positive dimensions, got image {i}: size {img.size}")
            validate_box(
                pos.x,
                pos.y,
                img.size[0],
                img.size[1],
                self.width,
                self.height,
            )
        return self

    def scale(self, factor: float) -> ProductShot:
        new_width = int(self.width * factor)
        new_height = int(self.height * factor)
        new_images: list[EditableImage] = []
        new_coords: list[Coordinates] = []
        for image, coords in zip(self.images, self.positions):
            new_images.append(image.set_size(int(image.width * factor), int(image.height * factor)))
            new_coords.append(Coordinates(x=int(coords.x * factor), y=int(coords.y * factor)))
        return ProductShot(
            images=new_images,
            positions=new_coords,
            width=new_width,
            height=new_height,
        )

    def get_image(self) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        for image, coords in zip(self.images, self.positions):
            canvas.alpha_composite(image.get_image(), dest=(coords.x, coords.y))
        return canvas

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def __len__(self) -> int:
        return len(self.images)


class ProductPlacementInput(BaseModel):
    products: ProductShot
    canvas_box: BoundingBox
    promotion_fields: object | None = None

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


def _get_product_scales(
    product_size: tuple[int, int],
    canvas_box: BoundingBox,
    target_score: float = 0.2,
) -> float:
    """
    Predicts the initial product scale from the canvas and prepared dimensions.

    Args:
        product_size: Width and height of the prepared product shot.
        canvas_box (BoundingBox): The bounding box of the canvas area.
        target_score: The target size of the product. 0 is about right, 1 is too big, -1 is too small.

    Returns:
        float: A predicted scale for the product shot.
    """
    product_width, product_height = product_size
    product_shot_aspect = product_width / product_height
    canvas_box_aspect = canvas_box.w / canvas_box.h
    ub = 0.99
    ph_ch_ratio_upper_bound = ub
    pw_cw_ratio_upper_bound = ub

    implied_scale_h_ub = ph_ch_ratio_upper_bound * canvas_box.h / product_height
    implied_scale_w_ub = pw_cw_ratio_upper_bound * canvas_box.w / product_width
    max_scale = min(implied_scale_h_ub, implied_scale_w_ub)

    lb = 0.01
    ph_ch_ratio_lower_bound = lb
    pw_cw_ratio_lower_bound = lb
    implied_scale_h_lb = ph_ch_ratio_lower_bound * canvas_box.h / product_height
    implied_scale_w_lb = pw_cw_ratio_lower_bound * canvas_box.w / product_width
    min_scale = min(implied_scale_h_lb, implied_scale_w_lb)

    if max_scale < min_scale:
        min_scale, max_scale = max_scale, min_scale

    logger.debug(f"Initial product scale bounds: min {min_scale}, max {max_scale}")

    # Binary search for the scale that gives us the target score based on the regression model
    num_iters = 10
    for _ in range(num_iters):
        mid_scale = (min_scale + max_scale) / 2.0
        score = score_product_scale(
            ph=product_height * mid_scale,
            pw=product_width * mid_scale,
            ch=canvas_box.h,
            cw=canvas_box.w
        )
        if score > target_score:
            max_scale = mid_scale
        else:
            min_scale = mid_scale

    implied_scale = (min_scale + max_scale) / 2.0

    logger.debug(f"Predicted product scale: {implied_scale}")
    logger.debug(f"- ratio_bound: {ph_ch_ratio_upper_bound}, {pw_cw_ratio_upper_bound}")
    logger.debug(f"- product aspect: {product_shot_aspect}, canvas aspect: {canvas_box_aspect}")
    return implied_scale


def _get_final_product_boxes(
    product_count: int,
    placed_box: BoundingBox,
    scaled_product_shot: ProductShot,
) -> list[BoundingBox]:
    if product_count <= 0:
        return []

    product_start_index = len(scaled_product_shot.images) - product_count
    product_boxes: list[BoundingBox] = []

    for image, position in zip(
        scaled_product_shot.images[product_start_index:],
        scaled_product_shot.positions[product_start_index:],
    ):
        product_boxes.append(BoundingBox(
            l=placed_box.l + position.x,
            t=placed_box.t + position.y,
            w=image.width,
            h=image.height,
        ))

    return product_boxes


def create_drop_shadow(
    img: Image.Image,
    shadow_opacity: float = 0.35,
    size_scale: float = 1.05,
    blur_scale: float = 0.03,
    offset_scale: float = 0.005,
    shadow_colour: tuple[int, int, int] = (0x2e, 0x2e, 0x30),
    return_shadow_only: bool = False,
) -> tuple[Image.Image, tuple[int, int]]:
    """
    Create a drop shadow directly beneath the object, simulating it floating.
    Always returns (image, (offset_x, offset_y)) where offset is the position
    of the original product body relative to the top-left of the returned image.
    """
    img = img.convert("RGBA")

    avg_dim = (img.width + img.height) / 2
    dynamic_blur = avg_dim * blur_scale
    dynamic_offset = avg_dim * offset_scale

    sigma = int(max(6, dynamic_blur))
    offset = int(max(5, dynamic_offset))

    shadow_alpha = img.split()[3]

    new_shadow_width = int(shadow_alpha.width * size_scale)
    new_shadow_height = int(shadow_alpha.height * size_scale)
    shadow_alpha = shadow_alpha.resize((new_shadow_width, new_shadow_height), Image.Resampling.LANCZOS)

    right_offset_px = int(img.width * 0.05)
    extra_w = max(0, new_shadow_width - img.width)
    extra_h = max(0, new_shadow_height - img.height)

    margin_x = int(extra_w + offset + right_offset_px + sigma * 2)
    margin_y = int(extra_h + offset + sigma * 2)

    padded_width = img.width + margin_x * 2
    padded_height = img.height + margin_y * 2

    shadow_layer = Image.new("RGBA", (padded_width, padded_height), (0, 0, 0, 0))
    shadow_x = margin_x + (img.width - new_shadow_width) // 2 + offset + right_offset_px
    shadow_y = margin_y + offset

    shadow_base = Image.new("RGBA", (new_shadow_width, new_shadow_height), shadow_colour + (0,))
    shadow_base.putalpha(shadow_alpha)
    shadow_layer.paste(shadow_base, (shadow_x, shadow_y), shadow_base)

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(sigma / 2.0))

    if shadow_opacity != 1.0:
        r, g, b, a = shadow_layer.split()
        a = a.point(lambda p: int(p * shadow_opacity))
        shadow_layer = Image.merge("RGBA", (r, g, b, a))

    if return_shadow_only:
        return shadow_layer, (margin_x, margin_y)

    final_canvas = Image.new("RGBA", (padded_width, padded_height), (0, 0, 0, 0))
    final_canvas.paste(shadow_layer, (0, 0), shadow_layer)
    final_canvas.paste(img, (margin_x, margin_y), img)

    return final_canvas, (margin_x, margin_y)


def add_shadow(
    img: Image.Image,
    shadow_type: str = "floor",
    **kwargs
) -> tuple[Image.Image, tuple[int, int]]:
    """
    Apply a shadow to an image.
    shadow_type can be 'floor' or 'drop'.
    """
    if shadow_type == "drop":
        return create_drop_shadow(img, **kwargs)
    elif shadow_type == "floor":
        return create_floor_shadow(img, **kwargs)
    else:
        raise ValueError(f"Unknown shadow type: {shadow_type}")


def create_floor_shadow(
    img: Image.Image,
    shear_angle: float = 25,
    vertical_scale: float = 0.65,
    shadow_opacity: float = 1,
    blur_radius: float = 30,
    fade: bool = True,
    padding: int = 0,
    return_shadow_only: bool = False,
) -> tuple[Image.Image, tuple[int, int]]:
    """
    Create a realistic floor shadow that falls BEHIND the object (top-right).
    Always returns (image, (offset_x, offset_y)).
    """
    img = img.convert("RGBA")

    if padding > 0:
        padded_img = Image.new("RGBA", (img.width + padding * 2, img.height + padding * 2), (0, 0, 0, 0))
        padded_img.paste(img, (padding, padding))
        img = padded_img

    w, h = img.size

    alpha = np.array(img.split()[3])
    rows_with_content = np.where(np.any(alpha > 0, axis=1))[0]
    if len(rows_with_content) == 0:
        return img, (0, 0)

    object_top = rows_with_content[0]
    object_bottom = rows_with_content[-1]
    object_height = object_bottom - object_top + 1

    alpha_img = img.split()[3]
    shadow = Image.new("RGBA", alpha_img.size, (0, 0, 0, 255))
    shadow.putalpha(alpha_img)

    if shadow_opacity != 1.0:
        r, g, b, a = shadow.split()
        a = a.point(lambda p: int(p * shadow_opacity))
        shadow = Image.merge("RGBA", (r, g, b, a))

    shadow = shadow.crop((0, object_top, w, object_bottom + 1))

    new_h = max(1, int(object_height * vertical_scale))
    shadow = shadow.resize((w, new_h), Image.Resampling.LANCZOS)

    blur_pad = int(blur_radius * 2) if blur_radius > 0 else 0

    shear_px = abs(int(new_h * tan(radians(shear_angle))))
    sheared_w = w + shear_px + (blur_pad * 2)
    sheared_h = new_h + (blur_pad * 2)

    padded_shadow = Image.new("RGBA", (sheared_w, sheared_h), (0, 0, 0, 0))
    shift_dir_offset = blur_pad if shear_angle >= 0 else shear_px + blur_pad
    padded_shadow.paste(shadow, (shift_dir_offset, blur_pad))

    shear_factor = tan(radians(shear_angle))
    y_anchor = new_h + blur_pad

    sheared_shadow = padded_shadow.transform(
        (sheared_w, sheared_h),
        Image.Transform.AFFINE,
        (1, shear_factor, -y_anchor * shear_factor, 0, 1, 0),
        resample=Image.Resampling.BICUBIC
    )

    sheared_shadow = sheared_shadow.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    if fade:
        s_np = np.array(sheared_shadow).astype(float)
        gradient = np.linspace(0.35, 1.0, sheared_h).reshape(-1, 1)
        s_np[..., 3] *= gradient
        sheared_shadow = Image.fromarray(s_np.astype(np.uint8))

    gap = int(object_height * 0.02)
    shadow_y = object_bottom - new_h + 1 - gap - blur_pad

    canvas_left = min(0, -blur_pad)
    canvas_right = max(w, sheared_w - blur_pad)
    canvas_top = min(0, shadow_y)
    canvas_bottom = max(h, shadow_y + sheared_h)

    canvas_w = canvas_right - canvas_left
    canvas_h = canvas_bottom - canvas_top

    if return_shadow_only:
        return sheared_shadow, (-blur_pad + (-canvas_left), shadow_y + (-canvas_top))

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    x_offset = -canvas_left
    y_offset = -canvas_top

    canvas.paste(sheared_shadow, (-blur_pad + x_offset, shadow_y + y_offset), sheared_shadow)
    canvas.paste(img, (0 + x_offset, 0 + y_offset), img)

    return canvas, (x_offset, y_offset)


def _product_layer_fabric_bounds(fabric_object: FabricObject) -> BoundingBox:
    scale_x = abs(fabric_object.scaleX)
    scale_y = abs(fabric_object.scaleY)
    stroke_scale_x = 1 if fabric_object.strokeUniform else scale_x
    stroke_scale_y = 1 if fabric_object.strokeUniform else scale_y
    return BoundingBox(
        l=fabric_object.left,
        t=fabric_object.top,
        w=fabric_object.width * scale_x + fabric_object.strokeWidth * stroke_scale_x,
        h=fabric_object.height * scale_y + fabric_object.strokeWidth * stroke_scale_y,
    )


class _ProductImageGroup(LayoutElement):
    shadow: ImageBox
    product: ImageBox
    name: str

    def placement_boxes(self) -> list[BoundingBox]:
        return self.shadow.placement_boxes() + self.product.placement_boxes()

    def render(self) -> FabricObjects:
        objects = self.shadow.render() + self.product.render()
        group_box = BoundingBox.merge([
            _product_layer_fabric_bounds(fabric_object)
            for fabric_object in objects
        ])

        object_names = [f"{self.name} shadow", self.name]
        grouped_objects: FabricObjects = [
            fabric_object.model_copy(
                update={
                    "left": fabric_object.left - group_box.l,
                    "top": fabric_object.top - group_box.t,
                    "name": object_names[index],
                },
                deep=True,
            )
            for index, fabric_object in enumerate(objects)
        ]
        return [
            FabricGroup(
                name=self.name,
                left=group_box.l,
                top=group_box.t,
                width=group_box.w,
                height=group_box.h,
                fill="",
                strokeWidth=0,
                objectCaching=False,
                objects=grouped_objects,
            )
        ]


def _product_image_elements(
    product_shot: ProductShot,
    product_count: int,
    name: str,
) -> list[LayoutElement]:
    layers: list[ImageBox] = []
    for image, position in zip(product_shot.images, product_shot.positions):
        product_image = image.get_image()
        layers.append(
            ImageBox(
                image=product_image,
                box=BoundingBox(
                    l=position.x,
                    t=position.y,
                    w=product_image.width,
                    h=product_image.height,
                ),
            )
        )

    if len(layers) != product_count * 2:
        return [
            layer.model_copy(
                update={
                    "name": name if len(layers) == 1 else f"{name} {index + 1}"
                }
            )
            for index, layer in enumerate(layers)
        ]

    grouped_elements: list[LayoutElement] = []
    for index in range(product_count):
        group_name = name if product_count == 1 else f"{name} {index + 1}"
        grouped_elements.append(
            _ProductImageGroup(
                shadow=layers[index],
                product=layers[product_count + index],
                name=group_name,
            )
        )
    return grouped_elements


class PreparedProduct:
    """Prepare product images once and place the result multiple times."""

    __slots__ = ("_image_refs", "_product_shot")

    def __init__(self, product_images: list[Image.Image]) -> None:
        self._image_refs: tuple[ImageRef, ...] = tuple(
            ImageRef.from_image(image.convert("RGBA"))
            for image in product_images
        )
        self._product_shot: ProductShot | None = None

    def __bool__(self) -> bool:
        return bool(self._image_refs)

    def _prepare(self) -> ProductShot:
        if self._product_shot is None:
            self._product_shot = preprocess_product_ids(list(self._image_refs))
        return self._product_shot

    def place(
        self,
        canvas_box: BoundingBox,
        template_size: tuple[int, int],
        occupied_boxes: list[BoundingBox],
        anchor_x: float,
        anchor_y: float,
        target_product_size: float = 0.2,
        allow_overlap_fallback: bool = True,
        anchor_position: AnchorPosition = "center",
        name: str = "Product image",
        occupied_mask: Image.Image | np.ndarray | None = None,
    ) -> LayoutElement:
        product_shot = self._prepare()

        if len(product_shot) == 0:
            logger.warning("No product images provided, skipping product placement.")
            return Empty()

        logger.debug(f"Preprocessed product shot with size {product_shot.size}.")

        scale_factor = _get_product_scales(
            product_shot.size,
            canvas_box,
            target_score=target_product_size,
        )
        logger.debug(f"Initial product scale factor: {scale_factor:.2f}")

        elements = _product_image_elements(
            product_shot,
            len(self._image_refs),
            name,
        )

        return Magnet(
            element=Group(elements=elements),
            canvas_box=canvas_box,
            template_size=template_size,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            avoid=occupied_boxes,
            scale_factor=scale_factor,
            allow_overlap_fallback=allow_overlap_fallback,
            anchor_position=anchor_position,
            occupied_mask=occupied_mask,
        )


def Product(
    product_images: list[Image.Image] | PreparedProduct,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    anchor_x: float,
    anchor_y: float,
    target_product_size: float = 0.2,
    allow_overlap_fallback: bool = True,
    anchor_position: AnchorPosition = "center",
    occupied_mask: Image.Image | np.ndarray | None = None,
) -> LayoutElement:
    """Arrange product images as a collage and place it near an anchor."""
    prepared_product = (
        product_images
        if isinstance(product_images, PreparedProduct)
        else PreparedProduct(product_images)
    )
    return prepared_product.place(
        canvas_box=canvas_box,
        template_size=template_size,
        occupied_boxes=occupied_boxes,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        target_product_size=target_product_size,
        allow_overlap_fallback=allow_overlap_fallback,
        anchor_position=anchor_position,
        occupied_mask=occupied_mask,
    )
