from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from ...native import Bundle, RendererError, contained_file


_ROOT = Path(__file__).resolve().parents[3]
_FONT_ROOT = Path(
    os.getenv("POSM_RENDERER_ASSETS", str(Bundle.configured().root / "assets"))
).resolve() / "fonts"
ALIBABA_PUHUITI_FONT_FACES = {
    "Alibaba PuHuiTi": {
        "normal": {
            "400": str(_FONT_ROOT / "AlibabaPuHuiTiR.otf"),
            "500": str(_FONT_ROOT / "AlibabaPuHuiTiM.otf"),
            "700": str(_FONT_ROOT / "AlibabaPuHuiTiB.otf"),
        }
    }
}


def register_browser_font_faces(_faces: dict[str, Any] | None = None) -> None:
    """Retained layout API; font registration is owned by the native renderer."""


def _as_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_json(item) for key, item in value.items()}
    return value


def fabric_to_png(canvas: Any) -> Image.Image:
    """Rasterize a Fabric primitive canvas through the bundled Skia renderer."""

    payload = _as_json(canvas)
    request_id = "layout-" + next(tempfile._get_candidate_names())
    scratch_root = _ROOT / ".native-layout"
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


__all__ = ["ALIBABA_PUHUITI_FONT_FACES", "fabric_to_png", "register_browser_font_faces"]
