from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.native import Bundle, RendererError
from app.schema import CreateParams
from render_service import render_payload


class IntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / "renderer"
        self.binary.write_bytes(b"fixture executable")
        manifest = {"schema_version": 1, "protocol_version": 1, "fabric_version": "6.6.5", "development": False,
                    "executable": "renderer", "files": {"renderer": hashlib.sha256(self.binary.read_bytes()).hexdigest()}}
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.bundle = Bundle(self.root, hashlib.sha256(self.manifest.read_bytes()).hexdigest())

    def test_binary_tampering_is_rejected_before_execution(self) -> None:
        self.binary.write_bytes(b"modified executable")
        with patch("app.native.subprocess.run") as execute:
            with self.assertRaisesRegex(RendererError, "RENDERER_FILE_CHECKSUM"):
                self.bundle.invoke({"request_id": "test"})
            execute.assert_not_called()

    def test_manifest_tampering_is_rejected(self) -> None:
        self.manifest.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RendererError, "RENDERER_MANIFEST_CHECKSUM"):
            self.bundle.verify()

    def test_resource_cannot_escape_bundle(self) -> None:
        from app.native import contained_file
        with self.assertRaisesRegex(RendererError, "RENDERER_FILE_INVALID"):
            contained_file(self.root, "../outside")


class ContractTests(unittest.TestCase):
    def test_single_panel_is_padded_on_the_right(self) -> None:
        item = CreateParams(template="sasa_202609001", promotion_list=[{"brand_name": "A"}], product=[[]])
        self.assertEqual(item.promotion_list, [{"brand_name": "A"}, {}])
        self.assertEqual(item.product, [[], []])

    def test_right_only_panel_keeps_its_index(self) -> None:
        item = CreateParams(template="sasa_202609001", promotion_list=[{}, {"brand_name": "B"}], product=[[], []])
        self.assertEqual(item.promotion_list[1]["brand_name"], "B")
        self.assertEqual(item.promotion_list[0], {})

    def test_empty_panel_rejects_product_images(self) -> None:
        with self.assertRaises(ValueError):
            CreateParams(template="sasa_202609001", promotion_list=[{}, {}], product=[["image"], []])

    def test_service_boundary_rejects_multiple_items(self) -> None:
        item = {"template": "sasa_202607001", "promotion_list": [{"brand_name": "A"}], "product": [[]]}
        with self.assertRaisesRegex(ValueError, "exactly one render item"):
            render_payload([item, item])

    def test_unported_template_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CreateParams(template="sasa_202607007", promotion_list=[{}], product=[[]])

    def test_07006_requires_exactly_one_promotion_and_product_list(self) -> None:
        item = CreateParams(template="sasa_202607006", promotion_list=[{}], product=[[]])
        self.assertEqual(len(item.promotion_list), 1)
        with self.assertRaises(ValueError):
            CreateParams(template="sasa_202607006", promotion_list=[{}, {}], product=[[], []])


if __name__ == "__main__":
    unittest.main()
