from __future__ import annotations

import base64
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from ..base import POSMImplementation
from ..native import Bundle, RendererError, contained_file, file_sha256
from ..preprocess import (gwp_image_references, hk_mo_price, normalize_currency_markers,
                          optional_text, preprocess_vip_and_star_prices, recommended_price, required_text)
from ..schema import CreateParams, GenerationResult
from ..types import FabricCanvas


class Pipeline(POSMImplementation):
    slots: int = 1

    @classmethod
    def get_expected_number_of_fields(cls) -> int:
        return cls.slots

    def get_template(self) -> Image.Image:
        bundle = Bundle.configured()
        bundle.verify()
        with Image.open(bundle.root / "assets" / "templates" / f"{self.name}.png") as image:
            return image.convert("RGBA")

    def get_schema(self) -> dict[str, str]:
        catalog = Path(__file__).resolve().parents[1] / "template_catalog.json"
        return json.loads(catalog.read_text(encoding="utf-8"))[self.name]["fields"]

    def process(self, params: CreateParams) -> GenerationResult:
        with tempfile.TemporaryDirectory(prefix="posm-native-") as directory:
            root = Path(directory).resolve()
            resources: dict[str, dict[str, Any]] = {}
            reference_ids: dict[str, str] = {}

            def prepare(reference: str) -> str:
                if reference in reference_ids:
                    return reference_ids[reference]
                image = self.maybe_load_image_from_url(reference)
                if image is None:
                    raise RendererError("IMAGE_LOAD_FAILED")
                with image:
                    bounds = image.getchannel("A").getbbox()
                    cropped = image.crop(bounds) if bounds else image.copy()
                    with cropped:
                        path = root / f"image-{len(resources)}.png"
                        cropped.save(path, format="PNG", compress_level=1)
                        digest = file_sha256(path)
                        image_id = f"image:{len(resources)}"
                        resources[image_id] = {"path": str(path), "sha256": digest,
                                               "width": cropped.width, "height": cropped.height}
                reference_ids[reference] = image_id
                return image_id

            fields_list: list[dict[str, str | list[str]]] = []
            products: list[list[str]] = []
            for fields, images in zip(params.promotion_list, params.product, strict=True):
                if not fields and self.name == "sasa_202609001":
                    fields_list.append({})
                    products.append([])
                    continue
                normalized = dict(fields)
                region = hk_mo_price(fields)
                for key in ("brand_name", "product_name"):
                    normalized[key] = required_text(fields, key)
                for key in ("price_recommended", "fab"):
                    normalized[key] = required_text(fields, key) if self.name == "sasa_202607001" else optional_text(fields, key) or ""
                normalized["price_recommended"] = recommended_price(str(normalized["price_recommended"]), region)
                vip, star = preprocess_vip_and_star_prices(optional_text(fields, "price_vip"), optional_text(fields, "star_price"), region)
                normalized["price_vip"], normalized["star_price"] = vip, star or ""
                normalized["gwp_text"] = normalize_currency_markers(optional_text(fields, "gwp_text"), region) or ""
                normalized["gwp_image"] = [prepare(ref) for ref in gwp_image_references(fields)]
                fields_list.append(normalized)
                products.append([prepare(ref) for ref in images])
            response = Bundle.configured().invoke({
                "protocol_version": 1, "request_id": self._get_run_id(), "method": "render",
                "params": {"template": self.name, "promotion_list": fields_list, "product": products},
                "resources": resources, "options": {"output_dir": str(root)}, "extensions": {},
            })
            canvas_path = contained_file(root, response["fabric"])
            preview_path = contained_file(root, response["preview"])
            if canvas_path.stat().st_size > 256 * 1024 * 1024 or preview_path.stat().st_size > 64 * 1024 * 1024:
                raise RendererError("RENDERER_OUTPUT_LIMIT")
            canvas = FabricCanvas.model_validate_json(canvas_path.read_bytes())
            if canvas.version != "6.6.5":
                raise RendererError("FABRIC_VERSION_MISMATCH")
            with Image.open(preview_path) as preview:
                if preview.size != (canvas.width, canvas.height):
                    raise RendererError("PREVIEW_DIMENSIONS_MISMATCH")
                buffer = io.BytesIO()
                preview.convert("RGB").save(buffer, format="JPEG")
            return GenerationResult(id=self._get_run_id(), fabric_model=canvas, successful=True, message=None,
                                    reference_jpg="data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))


class DoublePipeline(Pipeline):
    slots = 2
