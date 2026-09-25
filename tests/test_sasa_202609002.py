from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.schema import CreateParams
from app.template_contract import native_template
from app.templates import get_pipeline


class ResponsiveTemplateContractTests(unittest.TestCase):
    def payload(self, **updates: object) -> dict:
        value = {
            "template": "sasa_202609002",
            "width": 1500,
            "height": 1000,
            "promotion_list": [{"brand_name": "Brand"}],
            "product": [[]],
        }
        value.update(updates)
        return value

    def test_template_is_registered_and_dimensions_are_preserved(self) -> None:
        item = CreateParams(**self.payload())
        self.assertEqual(native_template(item.template), item.template)
        self.assertIsNotNone(get_pipeline(item.template))
        self.assertEqual((item.width, item.height), (1500, 1000))

    def test_dimensions_are_required_positive_strict_integers(self) -> None:
        for update in ({"width": None}, {"height": None}, {"width": 0}, {"height": -1}, {"width": True}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                CreateParams(**self.payload(**update))

    def test_background_only_is_valid_with_explicit_dimensions(self) -> None:
        item = CreateParams(**self.payload(promotion_list=[{}], product=[[]]))
        self.assertEqual(item.promotion_list, [{}])


if __name__ == "__main__":
    unittest.main()
