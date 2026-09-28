from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.schema import CreateParams
from app.template_contract import native_template
from app.templates import get_pipeline


class DeprecatedResponsiveTemplateTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "template": "sasa_202609002",
            "width": 1500,
            "height": 1000,
            "promotion_list": [{"brand_name": "Brand"}],
            "product": [[]],
        }

    def test_template_is_rejected_at_the_public_contract(self) -> None:
        with self.assertRaises(ValueError):
            native_template("sasa_202609002")
        with self.assertRaises(ValueError):
            get_pipeline("sasa_202609002")

    def test_create_params_rejects_deprecated_template(self) -> None:
        with self.assertRaises(ValidationError):
            CreateParams(**self.payload())


if __name__ == "__main__":
    unittest.main()
