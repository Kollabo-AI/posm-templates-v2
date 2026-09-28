from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator
from PIL import Image, ImageDraw, ImageColor

from app import logger
from .util import calculate_coverage_area


class BoundingBox(BaseModel):
    l: float
    t: float
    w: float
    h: float

    model_config = ConfigDict(frozen=True, extra="forbid")

    def __init__(
        self,
        *,
        l: float | None = None,
        t: float | None = None,
        r: float | None = None,
        b: float | None = None,
        w: float | None = None,
        h: float | None = None,
    ) -> None:
        l, w = self._ensure_system_lwr(l=l, w=w, r=r)
        t, h = self._ensure_system_thb(t=t, h=h, b=b)
        super().__init__(l=l, t=t, w=w, h=h)

    @property
    def cx(self) -> float:
        return self.l + self.w / 2

    @property
    def cy(self) -> float:
        return self.t + self.h / 2

    @property
    def b(self) -> float:
        return self.t + self.h

    @property
    def r(self) -> float:
        return self.l + self.w

    @property
    def area(self) -> float:
        return self.w * self.h

    @model_validator(mode="after")
    def post_init_checks(self) -> BoundingBox:
        if self.w < 0:
            raise ValueError(f"Box width must be >= 0, got: {self.w}")
        if self.h < 0:
            raise ValueError(f"Box height must be >= 0, got: {self.h}")
        return self

    def contains(self, other: BoundingBox) -> bool:
        return self.l <= other.l and self.t <= other.t and self.r >= other.r and self.b >= other.b

    def repr(self) -> str:
        classname = self.__class__.__name__
        return f"<{classname}(l={self.l}, t={self.t}, w={self.w}, h={self.h})>"

    def __repr__(self) -> str:
        return self.repr()

    def draw(
        self,
        template: Image.Image,
        hex_color: str,
        alpha: float,
        skip_show: bool = False,
    ) -> Image.Image:
        if not 0 <= alpha <= 1:
            raise ValueError("Alpha value must be between 0 and 1")
        base_img = template.copy()
        overlay = Image.new("RGBA", base_img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        rgb = ImageColor.getrgb(hex_color)
        alpha_int = int(alpha * 255)
        draw.rectangle(
            [self.l, self.t, self.r, self.b],
            fill=rgb + (alpha_int,)
        )
        combined_img = Image.alpha_composite(base_img, overlay)
        if not skip_show:
            combined_img.show()
        return combined_img

    @staticmethod
    def draw_many(
        boxes: list[BoundingBox],
        template: Image.Image,
        hex_color: str,
        alpha: float,
        skip_show: bool = False,
    ) -> Image.Image:
        if not boxes:
            raise ValueError("No boxes to draw")

        if not 0 <= alpha <= 1:
            raise ValueError("Alpha value must be between 0 and 1")

        base_img = template.copy()
        overlay = Image.new("RGBA", base_img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        rgb = ImageColor.getrgb(hex_color)
        alpha_int = int(alpha * 255)

        for box in boxes:
            draw.rectangle(
                [box.l, box.t, box.r, box.b],
                fill=rgb + (alpha_int,)
            )

        combined_img = Image.alpha_composite(base_img, overlay)

        if not skip_show:
            combined_img.show()

        return combined_img

    @staticmethod
    def merge(boxes: list[BoundingBox]) -> BoundingBox:
        """Merges multiple boxes into a single bounding box that contains all of them."""
        if not boxes:
            raise ValueError("No boxes to merge")
        boxes = [box for box in boxes if box.w > 0 and box.h > 0]
        left = min(box.l for box in boxes)
        top = min(box.t for box in boxes)
        right = max(box.r for box in boxes)
        bottom = max(box.b for box in boxes)
        return BoundingBox(
            l=left,
            t=top,
            w=right - left,
            h=bottom - top,
        )

    @staticmethod
    def total_area(boxes: list[BoundingBox], image_width: int, image_height: int) -> float:
        b = [(box.l, box.t, box.w, box.h) for box in boxes]
        area = calculate_coverage_area(b, image_width, image_height)
        return area

    @staticmethod
    def _ensure_system_lwr(l: float | None = None, w: float | None = None, r: float | None = None) -> tuple[float, float]:
        if l is not None and w is not None:
            if r is not None and abs((l + w) - r) > 1e-6:
                logger.warning(f"Provided l={l}, w={w}, r={r} are inconsistent. Using l and w to compute r.")
            r = l + w
        elif l is not None and r is not None:
            if w is not None and abs((r - l) - w) > 1e-6:
                logger.warning(f"Provided l={l}, w={w}, r={r} are inconsistent. Using l and r to compute w.")
            w = r - l
        elif w is not None and r is not None:
            if l is not None and abs((r - w) - l) > 1e-6:
                logger.warning(f"Provided l={l}, w={w}, r={r} are inconsistent. Using w and r to compute l.")
            l = r - w
        else:
            raise ValueError("At least two of l, w, r must be provided")
        assert l is not None and w is not None
        return l, w

    @staticmethod
    def _ensure_system_thb(t: float | None = None, h: float | None = None, b: float | None = None) -> tuple[float, float]:
        if t is not None and h is not None:
            if b is not None and abs((t + h) - b) > 1e-6:
                logger.warning(f"Provided t={t}, h={h}, b={b} are inconsistent. Using t and h to compute b.")
            b = t + h
        elif t is not None and b is not None:
            if h is not None and abs((b - t) - h) > 1e-6:
                logger.warning(f"Provided t={t}, h={h}, b={b} are inconsistent. Using t and b to compute h.")
            h = b - t
        elif h is not None and b is not None:
            if t is not None and abs((b - h) - t) > 1e-6:
                logger.warning(f"Provided t={t}, h={h}, b={b} are inconsistent. Using h and b to compute t.")
            t = b - h
        else:
            raise ValueError("At least two of t, h, b must be provided")
        assert t is not None and h is not None
        return t, h

    def black_out(self, template_w: int, template_h: int) -> list[BoundingBox]:
        """Returns a list of bounding boxes that represent the areas outside this bounding box, effectively blacking out the rest of the image."""
        boxes: list[BoundingBox] = []
        template_box = BoundingBox(l=0, t=0, w=template_w, h=template_h)

        left = max(0.0, self.l)
        top = max(0.0, self.t)
        right = min(float(template_w), self.r)
        bottom = min(float(template_h), self.b)
        if right <= left or bottom <= top:
            logger.warning(f"Bounding box {self.repr()} is outside the template. The whole template will be blacked out.")
            return [template_box]

        if top > 0:
            boxes.append(BoundingBox(l=0, t=0, w=template_w, h=top))
        if bottom < template_h:
            boxes.append(BoundingBox(l=0, t=bottom, w=template_w, h=template_h - bottom))
        if left > 0:
            boxes.append(BoundingBox(l=0, t=top, w=left, h=bottom - top))
        if right < template_w:
            boxes.append(BoundingBox(l=right, t=top, w=template_w - right, h=bottom - top))

        return boxes
