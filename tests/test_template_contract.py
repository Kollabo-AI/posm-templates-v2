import unittest
from app.schema import CreateParams
from app.template_contract import LEGACY_TEMPLATES, native_template, normalize_bindings
from app.templates import get_pipeline
from app.types import FabricCanvas


class TemplateContractTests(unittest.TestCase):
    def test_09001_normalizes_each_slots_price_and_bullet_bindings(self):
        canvas = FabricCanvas(width=100, height=100, objects=[
            {"type": "textbox", "left": 12, "posmBinding": {
                "field": f"promotion_list.{slot}.{field}", "role": "formatted-line", "index": 8}}
            for slot in range(2) for field in ("price_vip", "price_recommended", "fab")
        ])
        normalize_bindings(canvas, "sasa_202609001")
        for node in canvas.objects:
            self.assertEqual(node["left"], 12)
            self.assertEqual(node["posmBinding"]["index"], 0)
            self.assertEqual(node["posmBinding"]["role"],
                             "formatted-line" if node["posmBinding"]["field"].endswith("fab") else "token")

    def test_legacy_names_keep_identity_and_one_slot_contract(self):
        for name in LEGACY_TEMPLATES:
            with self.subTest(template=name):
                item = CreateParams(template=name, promotion_list=[{}], product=[[]])
                self.assertEqual(item.template, name)
                self.assertEqual(get_pipeline(name).name, name)
                self.assertEqual(native_template(name), "sasa_202607006")
                self.assertIn("brand_name", get_pipeline(name).get_schema())

    def test_bindings_match_existing_platform_roles_and_keep_geometry(self):
        objects = [
            {"type": "textbox", "left": 17, "text": "Brand", "posmBinding": {
                "field": "promotion_list.0.brand_name", "role": "formatted-line", "index": 9}},
            {"type": "group", "objects": [
                {"type": "textbox", "text": "Terms", "posmBinding": {
                    "field": "promotion_list.0.tnc", "role": "formatted-line", "index": 4}},
                {"type": "textbox", "text": "Feature", "posmBinding": {
                    "field": "promotion_list.0.fab", "role": "formatted-line", "index": 5}}]},
        ]
        for name in ("sasa_202607001", "sasa_202604002"):
            canvas = FabricCanvas(width=100, height=100, objects=objects)
            normalize_bindings(canvas, name)
            brand = canvas.objects[0]
            self.assertEqual(brand["left"], 17)
            self.assertEqual(brand["posmBinding"], {
                "field": "promotion_list.0.brand_name", "role": "line", "index": 0, "template": name})
            nested = canvas.objects[1]["objects"]
            self.assertEqual(nested[0]["posmBinding"]["role"], "line")
            self.assertEqual(nested[1]["posmBinding"]["role"], "formatted-line" if name.endswith("001") else "line")
