from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from .runtime import Bundle, RendererError, contained_file


_ROOT = Path(__file__).resolve().parents[2]


def _as_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_json(item) for key, item in value.items()}
    return value


def rasterize_fabric(canvas: Any) -> Image.Image:
    """Rasterize a Fabric primitive canvas through the bundled Skia renderer."""

    payload = _as_json(canvas)
    request_id = f"layout-{uuid.uuid4().hex}"
    scratch_root = _ROOT / ".renderer-layout"
    scratch_root.mkdir(exist_ok=True)
    directory = scratch_root / f"run-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        request = {
            "protocol_version": 1,
            "request_id": request_id,
            "method": "render_primitives",
            "params": payload,
            "resources": {},
            "options": {"output_dir": str(directory)},
            "extensions": {},
        }
        result = Bundle.configured().invoke(request)
        preview = contained_file(directory, result["preview"])
        with Image.open(preview) as image:
            return image.convert("RGBA")
    except RendererError:
        raise
    except Exception as error:
        raise RendererError("PRIMITIVE_RENDER_FAILED") from error
    finally:
        shutil.rmtree(directory, ignore_errors=True)


__all__ = ["rasterize_fabric"]
