from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.renderer.runtime import Bundle, RendererError
from app.schema import CreateParams
from app.templates import get_pipeline

BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "renderer" / "windows-x86_64"


class AdapterTests(unittest.TestCase):
    @staticmethod
    def _objects(request: dict) -> list[dict]:
        def walk(nodes: list[dict]):
            for node in nodes:
                yield node
                yield from walk(node.get("objects", []))

        return list(walk(request["params"]["objects"]))

    def _text(self, request: dict, name: str) -> str | None:
        for node in self._objects(request):
            if node.get("name") == name:
                return node.get("text")
        return None

    def test_normalized_input_and_gift_resources_reach_native_boundary(self) -> None:
        calls: list[dict] = []

        def invoke(bundle: Bundle, request: dict) -> dict:
            calls.append(request)
            root = Path(request["options"]["output_dir"])
            Image.new("RGB", (4, 4), "white").save(root / "preview.png")
            (root / "fabric.json").write_text(json.dumps({"version": "6.6.5", "width": 4, "height": 4, "objects": []}))
            for resource in request["resources"].values():
                self.assertTrue(Path(resource["path"]).is_file())
                self.assertLessEqual(resource["width"], 2000)
            return {"preview": "preview.png", "fabric": "fabric.json"}

        params = CreateParams(template="sasa_202607001", promotion_list=[{
            "hk_mo_price": "MO", "brand_name": " Test ", "product_name": "Product",
            "price_recommended": "$200", "price_vip": "180", "fab": "Feature",
            "gwp_image": ["shared-image"], "gwp_text": "Gift $99",
        }], product=[["shared-image"]])
        pipeline = get_pipeline(params.template)
        with patch.object(Bundle, "configured", return_value=Bundle(BUNDLE_ROOT, "test")), \
             patch.object(Bundle, "invoke", invoke), \
             patch.object(pipeline, "maybe_load_image_from_url", side_effect=lambda _: Image.new("RGBA", (8, 12), "red")):
            result = pipeline.run(params)
        self.assertTrue(result.successful)
        request = calls[-1]
        self.assertEqual(self._text(request, "Brand name"), "Test")
        self.assertEqual(self._text(request, "VIP price 2"), "180")
        self.assertEqual(self._text(request, "Recommended price"), "MOP200")
        self.assertEqual(self._text(request, "Gift description"), "Gift MOP99")
        image_nodes = [node for node in self._objects(request) if node.get("type") == "image"]
        self.assertTrue(image_nodes)
        self.assertTrue(all(node["src"].startswith("data:image/") for node in image_nodes))
        self.assertEqual(request["resources"], {})
        self.assertFalse(Path(request["options"]["output_dir"]).exists())

    def test_native_failure_keeps_generation_result_failure_contract(self) -> None:
        params = CreateParams(template="sasa_202609001", promotion_list=[{}, {}], product=[[], []])
        with patch.object(Bundle, "configured", side_effect=RendererError("RENDERER_FILE_CHECKSUM")):
            result = get_pipeline(params.template).run(params)
        self.assertFalse(result.successful)
        self.assertEqual(result.reference_jpg, "")
        self.assertIsNone(result.fabric_model)
        self.assertIn("RENDERER_FILE_CHECKSUM", result.message or "")

    def test_07006_filters_failed_images_and_preserves_empty_gift_caption(self) -> None:
        calls: list[dict] = []

        def invoke(bundle: Bundle, request: dict) -> dict:
            calls.append(request)
            root = Path(request["options"]["output_dir"])
            Image.new("RGB", (4, 4), "white").save(root / "preview.png")
            (root / "fabric.json").write_text(json.dumps({"version": "6.6.5", "width": 4, "height": 4, "objects": []}))
            return {"preview": "preview.png", "fabric": "fabric.json"}

        params = CreateParams(template="sasa_202607006", promotion_list=[{
            "hk_mo_price": "MO", "brand_name": "Brand", "product_name": "Product",
            "price_recommended": "$195", "price_vip": "128", "fab": "Feature",
            "gwp_image": ["bad-image"], "gwp_text": "",
        }], product=[["bad-image"]])
        pipeline = get_pipeline(params.template)
        with patch.object(Bundle, "configured", return_value=Bundle(BUNDLE_ROOT, "test")), \
             patch.object(Bundle, "invoke", invoke), \
             patch.object(pipeline, "maybe_load_image_from_url", return_value=None):
            result = pipeline.run(params)
        self.assertTrue(result.successful)
        request = calls[-1]
        self.assertEqual(self._text(request, "VIP price 2"), "128")
        self.assertIsNone(self._text(request, "Gift description"))
        self.assertFalse([node for node in self._objects(request) if node.get("type") == "image"])


if __name__ == "__main__":
    unittest.main()
