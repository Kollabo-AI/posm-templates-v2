from __future__ import annotations

import abc
import io
import math
import uuid
from datetime import datetime

from PIL import Image

from . import logger
from .schema import CreateParams, GenerationResult
from .util.image import load_image_from_url


class POSMImplementation(abc.ABC):
    def __init__(self, name: str) -> None:
        self._name = name
        self._runid: str | None = None
        self._dirty = False

    @property
    def name(self) -> str:
        return self._name

    @abc.abstractmethod
    def get_template(self) -> Image.Image: ...

    @classmethod
    @abc.abstractmethod
    def get_expected_number_of_fields(cls) -> int: ...

    @abc.abstractmethod
    def get_schema(self) -> dict[str, str]: ...

    @abc.abstractmethod
    def process(self, params: CreateParams) -> GenerationResult: ...

    def _get_run_id(self) -> str:
        if self._runid is None:
            self._runid = f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
            self._dirty = True
        return self._runid

    def cleanup(self) -> None:
        self._runid = None
        self._dirty = False

    def maybe_load_image_from_url(self, reference: str) -> Image.Image | None:
        try:
            with load_image_from_url(reference) as source:
                image = source.convert("RGBA")
            scale = min(1.0, 2000 / image.width, 2000 / image.height)
            size = (max(1, math.floor(image.width * scale)), max(1, math.floor(image.height * scale)))
            while True:
                resized = image.resize(size, Image.Resampling.LANCZOS) if size != image.size else image.copy()
                buffer = io.BytesIO()
                resized.save(buffer, format="PNG", compress_level=1)
                if buffer.tell() <= 5 * 1024 * 1024:
                    image.close()
                    return resized
                resized.close()
                ratio = math.sqrt(5 * 1024 * 1024 / buffer.tell())
                size = (max(1, math.floor(size[0] * ratio)), max(1, math.floor(size[1] * ratio)))
        except Exception:
            logger.exception("IMAGE_LOAD_FAILED")
            return None

    def run(self, params: CreateParams) -> GenerationResult:
        if self._dirty:
            self.cleanup()
        run_id = self._get_run_id()
        try:
            unexpected = {key for fields in params.promotion_list for key in fields} - self.get_schema().keys()
            if unexpected:
                logger.warning("Unexpected promotion fields: %s", sorted(unexpected))
            return self.process(params)
        except Exception as error:
            logger.exception("Native template generation failed")
            return GenerationResult(id=run_id, reference_jpg="", fabric_model=None,
                                    message=f"Pipeline processing failed due to error: {error}", successful=False)
        finally:
            self.cleanup()
