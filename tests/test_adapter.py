from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.native import Bundle, RendererError
from app.schema import CreateParams
from app.templates import get_pipeline


class AdapterTests(unittest.TestCase):
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
        with patch.object(Bundle, "configured", return_value=Bundle(Path.cwd(), "test")), \
             patch.object(Bundle, "invoke", invoke), \
             patch.object(pipeline, "maybe_load_image_from_url", side_effect=lambda _: Image.new("RGBA", (8, 12), "red")):
            result = pipeline.run(params)
        self.assertTrue(result.successful)
        request = calls[0]
        fields = request["params"]["promotion_list"][0]
        self.assertEqual(fields["brand_name"], "Test")
        self.assertEqual(fields["price_vip"], "MOP180")
        self.assertEqual(fields["price_recommended"], "MOP200")
        self.assertEqual(fields["gwp_text"], "Gift MOP99")
        self.assertEqual(fields["gwp_image"], request["params"]["product"][0])
        self.assertEqual(len(request["resources"]), 1)
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
        with patch.object(Bundle, "configured", return_value=Bundle(Path.cwd(), "test")), \
             patch.object(Bundle, "invoke", invoke), \
             patch.object(pipeline, "maybe_load_image_from_url", return_value=None):
            result = pipeline.run(params)
        self.assertTrue(result.successful)
        self.assertEqual(calls[0]["params"]["product"], [[]])
        fields = calls[0]["params"]["promotion_list"][0]
        self.assertEqual(fields["gwp_image"], [])
        self.assertEqual(fields["gwp_text"], "")
        self.assertEqual(fields["price_vip"], "MOP128")


if __name__ == "__main__":
    unittest.main()
