from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import signal

from .. import logger
from ..util import Error, Ok, Result
from .anchor import AnchorPosition, anchor_factors
from .box import BoundingBox
from .util import calculate_coverage_area


class MaskPlacementError(RuntimeError):
    pass


def _save_debug_mask_placement(
    mask: Image.Image,
    occupied_boxes: list[BoundingBox],
    canvas_box: BoundingBox,
    template_image: Image.Image,
    best_x: int,
    best_y: int,
    occupied_mask: np.ndarray,
) -> Image.Image:
    debug_template = template_image.convert("RGBA")
    width, height = debug_template.size
    debug_draw = ImageDraw.Draw(debug_template)

    for occupied_box in occupied_boxes:
        debug_draw.rectangle(
            [occupied_box.l, occupied_box.t, occupied_box.r, occupied_box.b],
            fill=(255, 0, 0, 255),
        )

    debug_draw.rectangle(
        [canvas_box.l, canvas_box.t, canvas_box.r, canvas_box.b],
        outline=(0, 255, 0, 255),
        width=3,
    )

    debug_template.alpha_composite(mask.convert("RGBA"), dest=(best_x, best_y))

    occupied_pixels = np.array(occupied_mask * 100, dtype=np.uint8)
    overlay = Image.new("RGBA", (width, height), (128, 0, 128, 255))
    overlay.putalpha(Image.fromarray(occupied_pixels))

    return Image.alpha_composite(debug_template, overlay)


def predict_mask_placement(
    mask: Image.Image,
    occupied_boxes: list[BoundingBox],
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    anchor_x: float,
    anchor_y: float,
    debug_template: Image.Image | None = None,
    anchor_position: AnchorPosition = "center",
) -> Result[tuple[int, int, float]]:
    logger.debug("Starting mask placement prediction.")

    anchor_factor_x, anchor_factor_y = anchor_factors(anchor_position)
    template_width, template_height = template_size

    box_margins = 10
    occupied_mask = np.zeros((template_height, template_width), dtype=np.float32)

    for occupied_box in occupied_boxes:
        left = max(0, int(occupied_box.l - box_margins))
        top = max(0, int(occupied_box.t - box_margins))
        right = min(template_width, int(occupied_box.r + box_margins))
        bottom = min(template_height, int(occupied_box.b + box_margins))
        occupied_mask[top:bottom, left:right] = 1.0

    border_margins = int((template_height + template_width) / 100.0)
    border_top = max(0, canvas_box.t + border_margins)
    border_bottom = max(0, min(canvas_box.b - border_margins, template_height))
    border_left = max(0, canvas_box.l + border_margins)
    border_right = max(0, min(canvas_box.r - border_margins, template_width))

    occupied_mask[:int(border_top), :] = 1.0
    occupied_mask[int(border_bottom):, :] = 1.0
    occupied_mask[:, :int(border_left)] = 1.0
    occupied_mask[:, int(border_right):] = 1.0

    mask_binary = make_binary_mask(mask).astype(np.float32)
    mask_height, mask_width = mask_binary.shape
    kernel = mask_binary[::-1, ::-1]

    is_valid_convolution = (
        (kernel.shape[0] >= occupied_mask.shape[0] and kernel.shape[1] >= occupied_mask.shape[1])
        or (occupied_mask.shape[0] >= kernel.shape[0] and occupied_mask.shape[1] >= kernel.shape[1])
    )

    if not is_valid_convolution:
        logger.error(
            f"Encountered invalid convolution: occupied_mask shape {occupied_mask.shape}, "
            f"mask shape {mask_binary.shape}, kernel shape {kernel.shape}"
        )
        return Error("Could not compute convolution for valid placement detection due to size mismatch")

    logger.debug(
        f"Computing convolution for valid placement detection: occupied_mask shape {occupied_mask.shape}, "
        f"mask shape {mask_binary.shape}, kernel shape {kernel.shape}"
    )
    overlap_map = signal.fftconvolve(occupied_mask, kernel, mode="valid")
    valid_mask = overlap_map < 0.5

    if not np.any(valid_mask):
        return Error("Could not find suitable placement for mask without overlapping occupied areas")

    ys, xs = np.nonzero(valid_mask)
    if len(xs) == 0:
        return Error("No valid placement found")

    current_anchor_x = xs + mask_width * anchor_factor_x
    current_anchor_y = ys + mask_height * anchor_factor_y
    squared_distances = (
        (current_anchor_x - anchor_x) ** 2
        + (current_anchor_y - anchor_y) ** 2
    )
    logger.debug(
        f"Using {anchor_position} anchor for placement target at "
        f"({anchor_x:.2f}, {anchor_y:.2f}) "
        f"within canvas {canvas_box.repr()}"
    )

    min_idx = int(np.argmin(squared_distances))
    min_distance = float(np.sqrt(squared_distances[min_idx]))
    max_possible_distance = np.sqrt(template_width**2 + template_height**2)
    score = float(min_distance / max_possible_distance) if max_possible_distance > 0 else 1.0

    best_x = int(xs[min_idx])
    best_y = int(ys[min_idx])

    if debug_template is not None:
        debug_placement = _save_debug_mask_placement(
            mask=mask,
            occupied_boxes=occupied_boxes,
            canvas_box=canvas_box,
            template_image=debug_template,
            best_x=best_x,
            best_y=best_y,
            occupied_mask=occupied_mask,
        )
        logger.write_image(
            debug_placement,
            "Debug visualization of mask placement with occupied areas (purple), occupied boxes (red), and canvas box (green)",
            "debug_mask_placement.png",
        )

    logger.debug("Finished mask placement prediction.")
    return Ok((best_x, best_y, score))


def _get_occupied_mask(
    occupied_boxes: list[BoundingBox],
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    box_margins: int,
    border_margins: int,
) -> np.ndarray:
    template_width, template_height = template_size
    occupied_mask = np.zeros((template_height, template_width), dtype=np.float32)

    for occupied_box in occupied_boxes:
        left = max(0, int(occupied_box.l - box_margins))
        top = max(0, int(occupied_box.t - box_margins))
        right = min(template_width, int(occupied_box.r + box_margins))
        bottom = min(template_height, int(occupied_box.b + box_margins))
        occupied_mask[top:bottom, left:right] = 1.0

    border_top = max(0, int(canvas_box.t + border_margins))
    border_bottom = min(template_height, int(canvas_box.b - border_margins))
    border_left = max(0, int(canvas_box.l + border_margins))
    border_right = min(template_width, int(canvas_box.r - border_margins))

    occupied_mask[:border_top, :] = 1.0
    occupied_mask[border_bottom:, :] = 1.0
    occupied_mask[:, :border_left] = 1.0
    occupied_mask[:, border_right:] = 1.0
    return occupied_mask


def _scale_mask_to_canvas(
    mask: Image.Image,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    scale_factor: float,
) -> Image.Image:
    border_margins = int(sum(template_size) / 100.0)
    max_width = max(1.0, canvas_box.w - border_margins * 2)
    max_height = max(1.0, canvas_box.h - border_margins * 2)
    scale_limit = min(max_width / mask.width, max_height / mask.height)
    return scale_image(mask, min(scale_factor, scale_limit))


def _fallback_mask_placement(
    mask: Image.Image,
    scale_factor: float,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    anchor_x: float,
    anchor_y: float,
    anchor_position: AnchorPosition = "center",
) -> BoundingBox:
    anchor_factor_x, anchor_factor_y = anchor_factors(anchor_position)
    scaled_mask = _scale_mask_to_canvas(mask, canvas_box, template_size, scale_factor)
    template_width, template_height = template_size
    border_margins = int((template_height + template_width) / 100.0)
    occupied_mask = _get_occupied_mask(
        occupied_boxes=occupied_boxes,
        canvas_box=canvas_box,
        template_size=template_size,
        box_margins=10,
        border_margins=border_margins,
    )
    mask_binary = make_binary_mask(scaled_mask).astype(np.float32)
    kernel = mask_binary[::-1, ::-1]
    overlap_map = signal.fftconvolve(occupied_mask, kernel, mode="valid")

    top_min = max(0, int(canvas_box.t + border_margins))
    left_min = max(0, int(canvas_box.l + border_margins))
    top_max = min(template_height - scaled_mask.height, int(canvas_box.b - border_margins - scaled_mask.height))
    left_max = min(template_width - scaled_mask.width, int(canvas_box.r - border_margins - scaled_mask.width))

    if top_max < top_min or left_max < left_min:
        top_min = max(0, min(template_height - scaled_mask.height, int(canvas_box.t)))
        left_min = max(0, min(template_width - scaled_mask.width, int(canvas_box.l)))
        top_max = max(top_min, min(template_height - scaled_mask.height, int(canvas_box.b - scaled_mask.height)))
        left_max = max(left_min, min(template_width - scaled_mask.width, int(canvas_box.r - scaled_mask.width)))

    candidate_overlap = overlap_map[top_min:top_max + 1, left_min:left_max + 1]
    if candidate_overlap.size == 0:
        left = max(
            0,
            min(
                template_width - scaled_mask.width,
                int(anchor_x - scaled_mask.width * anchor_factor_x),
            ),
        )
        top = max(
            0,
            min(
                template_height - scaled_mask.height,
                int(anchor_y - scaled_mask.height * anchor_factor_y),
            ),
        )
        return BoundingBox(l=left, t=top, w=scaled_mask.width, h=scaled_mask.height)

    ys, xs = np.indices(candidate_overlap.shape)
    current_anchor_x = (
        xs + left_min + scaled_mask.width * anchor_factor_x
    )
    current_anchor_y = (
        ys + top_min + scaled_mask.height * anchor_factor_y
    )
    max_distance = np.sqrt(template_width**2 + template_height**2)
    distance_score = (
        (current_anchor_x - anchor_x) ** 2
        + (current_anchor_y - anchor_y) ** 2
    ) ** 0.5 / max_distance
    overlap_score = candidate_overlap / max(1.0, float(mask_binary.sum()))
    score = overlap_score * 10.0 + distance_score

    best_y, best_x = np.unravel_index(int(np.argmin(score)), score.shape)
    fallback_box = BoundingBox(
        l=int(best_x + left_min),
        t=int(best_y + top_min),
        w=scaled_mask.width,
        h=scaled_mask.height,
    )
    logger.warning(f"Using fallback mask placement at {fallback_box}.")
    return fallback_box


def composite_half_transparent(im1: Image.Image, im2: Image.Image) -> Image.Image:
    if im1.size != im2.size:
        raise ValueError("Images must be the same size for compositing")
    mask = np.array(im1.convert("L")).astype(np.float32) / 255.0
    im2_arr = np.array(im2.convert("RGBA")).astype(np.float32)
    composite = im2_arr.copy()
    mask_screen = mask * 0.5 + 0.5
    composite[:, :, :3] = im2_arr[:, :, :3] * mask_screen[:, :, None]
    composite[:, :, 3] = im2_arr[:, :, 3]
    return Image.fromarray(composite.astype(np.uint8))


def make_template_binary_mask(
    canvas_box: BoundingBox,
    occupied_boxes: list[BoundingBox],
    template_size: tuple[int, int],
) -> np.ndarray:
    template_width, template_height = template_size
    free_space_mask = np.zeros((template_height, template_width), dtype=np.float32)

    canvas_left = max(0, int(canvas_box.l))
    canvas_top = max(0, int(canvas_box.t))
    canvas_right = min(template_width, int(canvas_box.r))
    canvas_bottom = min(template_height, int(canvas_box.b))

    if canvas_right <= canvas_left or canvas_bottom <= canvas_top:
        raise RuntimeError("Canvas box has invalid dimensions or is outside template")

    free_space_mask[canvas_top:canvas_bottom, canvas_left:canvas_right] = 1.0

    box_margins = 5
    for occupied_box in occupied_boxes:
        left = max(0, int(occupied_box.l - box_margins))
        top = max(0, int(occupied_box.t - box_margins))
        right = min(template_width, int(occupied_box.r + box_margins))
        bottom = min(template_height, int(occupied_box.b + box_margins))
        free_space_mask[top:bottom, left:right] = 0.0

    return free_space_mask


def make_binary_mask(mask: Image.Image) -> np.ndarray:
    arr = np.array(mask)
    if len(arr.shape) == 3 and arr.shape[2] == 4:
        alpha = arr[:, :, 3] > 0
    elif len(arr.shape) == 2:
        alpha = arr > 0
    else:
        alpha = np.ones((mask.height, mask.width), dtype=bool)
    return alpha.astype(bool)


def scale_image(image: Image.Image, scale_factor: float) -> Image.Image:
    new_width = max(1, int(image.width * scale_factor))
    new_height = max(1, int(image.height * scale_factor))
    return image.resize((new_width, new_height), resample=Image.Resampling.LANCZOS)


def _place_mask_scale_decay(
    mask: Image.Image,
    scale_factor: float,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    anchor_x: float,
    anchor_y: float,
    fix_scale: bool = False,
    debug_template: Image.Image | None = None,
    anchor_position: AnchorPosition = "center",
    max_anchor_loss: float | None = 0.1,
    use_deferred_anchor_loss: bool = False,
) -> Result[BoundingBox]:
    logger.debug("Starting mask placement with scale decay.")

    alpha = 1.0
    alpha_decay = 0.95
    alpha_decay_base = 0.1 if not fix_scale else 1.0

    if not occupied_boxes:
        logger.warning("No occupied boxes provided in mask placement with scale decay")

    template_width, template_height = template_size
    coverage = calculate_coverage_area(
        [(box.l, box.t, box.w, box.h) for box in occupied_boxes],
        template_width,
        template_height,
    ) / (template_width * template_height)

    if not (0.0 <= coverage <= 1.0):
        raise RuntimeError(f"Invalid coverage ratio {coverage:.4f} in mask placement with scale decay")

    if coverage >= 0.99:
        return Error(f"Too much occupied coverage ({coverage:.4f}) for reliable mask placement")

    deferred_placement: BoundingBox | None = None
    while alpha >= alpha_decay_base:
        scaled_mask = scale_image(mask, scale_factor * alpha)
        logger.debug(f"Scale decay attempt: {alpha=:.2f}, size={scaled_mask.size}")

        result = predict_mask_placement(
            scaled_mask,
            occupied_boxes,
            canvas_box,
            template_size,
            anchor_x,
            anchor_y,
            debug_template=debug_template,
            anchor_position=anchor_position,
        )

        if result.is_error():
            msg = result.unwrap_error()
            logger.debug(f"Placement detection failed with alpha={alpha:.4f}: {msg}")
            alpha *= alpha_decay
            continue

        x, y, loss = result.unwrap()
        placement = BoundingBox(
            l=x,
            t=y,
            w=scaled_mask.width,
            h=scaled_mask.height,
        )
        if max_anchor_loss is not None and loss > max_anchor_loss:
            if use_deferred_anchor_loss and deferred_placement is None:
                deferred_placement = placement
            msg = f"Predicted placement too far from target anchor (loss={loss:.4f})"
            logger.debug(f"Placement detection failed with alpha={alpha:.4f}: {msg}")
            alpha *= alpha_decay
            continue

        logger.debug(f"Successful placement with scale decay at alpha={alpha:.4f}")
        return Ok(placement)

    if deferred_placement is not None:
        logger.info(
            "Using collision-free mask placement outside the preferred anchor range."
        )
        return Ok(deferred_placement)

    return Error("Could not find valid mask placement after scaling attempts")


def place_mask(
    mask: Image.Image,
    canvas_box: BoundingBox,
    template_size: tuple[int, int],
    occupied_boxes: list[BoundingBox],
    anchor_x: float,
    anchor_y: float,
    scale_factor: float = 1.0,
    fix_scale: bool = False,
    debug_template: Image.Image | None = None,
    anchor_position: AnchorPosition = "center",
    allow_overlap_fallback: bool = True,
) -> BoundingBox:
    """Place a mask by its selected anchor while avoiding occupied boxes."""
    logger.debug(f"Preprocessed mask with size {mask.size}.")

    prediction = _place_mask_scale_decay(
        mask,
        scale_factor,
        canvas_box,
        template_size,
        occupied_boxes,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        fix_scale=fix_scale,
        debug_template=debug_template,
        anchor_position=anchor_position,
        use_deferred_anchor_loss=not allow_overlap_fallback,
    )

    if prediction.is_error():
        placement_error = prediction.unwrap_error()
        if not allow_overlap_fallback:
            raise MaskPlacementError(
                "Could not place mask without overlapping occupied areas: "
                f"{placement_error}"
            )
        logger.warning(
            f"Mask placement failed with error: {placement_error}. "
            "Using fallback placement."
        )
        return _fallback_mask_placement(
            mask=mask,
            scale_factor=scale_factor,
            canvas_box=canvas_box,
            template_size=template_size,
            occupied_boxes=occupied_boxes,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            anchor_position=anchor_position,
        )

    return prediction.unwrap()
