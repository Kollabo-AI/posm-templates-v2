from __future__ import annotations

import base64
import binascii
import hashlib
import io
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from ..util import RENDERER_MAX_IMAGE_BYTES, RENDERER_MAX_IMAGE_DIMENSION
from .runtime import Bundle, RendererError, contained_file


_ROOT = Path(__file__).resolve().parents[2]
RENDERER_MAX_RESOURCE_COUNT = 32


def _as_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_json(item) for key, item in value.items()}
    return value


def _externalize_images(canvas: dict[str, Any], directory: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    resources: dict[str, dict[str, Any]] = {}
    labels: dict[str, str] = {}
    resource_ids: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            source = value.get("src")
            if isinstance(source, str) and source.startswith("data:image/"):
                try:
                    _, encoded = source.split(",", 1)
                    image_bytes = base64.b64decode(encoded, validate=True)
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        width, height = image.size
                except (ValueError, OSError, binascii.Error) as error:
                    raise RendererError("IMAGE_INVALID") from error
                digest = hashlib.sha256(image_bytes).hexdigest()
                resource_id = resource_ids.get(digest)
                if resource_id is None:
                    resource_id = f"image:{len(resources)}"
                    path = directory / f"resource-{len(resources)}.bin"
                    path.write_bytes(image_bytes)
                    resources[resource_id] = {
                        "path": str(path.resolve()),
                        "sha256": digest,
                        "width": width,
                        "height": height,
                    }
                    resource_ids[digest] = resource_id
                    labels[resource_id] = str(value.get("name") or "")
                value["src"] = resource_id
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(canvas)
    return resources, labels


def _validate_resources(resources: dict[str, dict[str, Any]], labels: dict[str, str]) -> None:
    if len(resources) > RENDERER_MAX_RESOURCE_COUNT:
        raise RendererError(
            f"IMAGE_LIMIT: {len(resources)} images in one poster, above the renderer limit of {RENDERER_MAX_RESOURCE_COUNT}"
        )
    for resource_id, record in resources.items():
        label = labels.get(resource_id, "")
        subject = f"{resource_id} '{label}'" if label else resource_id
        width, height = record["width"], record["height"]
        if width <= 0 or height <= 0 or width > RENDERER_MAX_IMAGE_DIMENSION or height > RENDERER_MAX_IMAGE_DIMENSION:
            raise RendererError(
                f"IMAGE_LIMIT: {subject} is {width}x{height}, "
                f"outside the renderer limit of {RENDERER_MAX_IMAGE_DIMENSION} px per side"
            )
        size = Path(record["path"]).stat().st_size
        if size > RENDERER_MAX_IMAGE_BYTES:
            raise RendererError(
                f"IMAGE_LIMIT: {subject} is {size / (1024 * 1024):.2f} MiB, "
                f"above the renderer limit of {RENDERER_MAX_IMAGE_BYTES // (1024 * 1024)} MiB"
            )


def rasterize_fabric(canvas: Any) -> Image.Image:
    """Rasterize a Fabric primitive canvas through the bundled Skia renderer."""

    request_id = f"layout-{uuid.uuid4().hex}"
    scratch_root = _ROOT / ".renderer-layout"
    scratch_root.mkdir(exist_ok=True)
    directory = scratch_root / f"run-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        payload = _as_json(canvas)
        resources, labels = _externalize_images(payload, directory)
        _validate_resources(resources, labels)
        request = {
            "protocol_version": 1,
            "request_id": request_id,
            "method": "render_primitives",
            "params": payload,
            "resources": resources,
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
