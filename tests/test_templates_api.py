from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from flask.testing import FlaskClient

from templates_api import (
    LAMBDA_REPOSITORY_ROOT,
    REPOSITORY_ROOT,
    LambdaTemplatesRuntime,
    _positive_integer,
    create_app,
    load_lambda_runtime,
)


@dataclass(frozen=True)
class FakeItem:
    source: dict[str, Any]
    template: str
    promotion_list: list[dict[str, Any]]
    product: list[list[str]]

    def model_dump(self, mode: str) -> dict[str, Any]:
        if mode != "json":
            raise ValueError(f"Unsupported mode: {mode}")
        return self.source


@dataclass(frozen=True)
class FakeResult:
    id: str
    successful: bool
    message: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reference_jpg": "data:image/jpeg;base64,ZmFrZQ=="
            if self.successful
            else "",
            "fabric_model": {"objects": []} if self.successful else None,
            "message": self.message,
            "successful": self.successful,
        }


class FakeRuntime:
    endpoint_version = "9.8.7"

    def __init__(
        self,
        results: list[FakeResult] | None = None,
        parse_error: Exception | None = None,
        render_error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.parse_error = parse_error
        self.render_error = render_error
        self.activation_count = 0
        self.rendered_items: list[FakeItem] = []

    def activate(self) -> None:
        self.activation_count += 1

    def parse_items(self, payload: Any) -> list[FakeItem]:
        if self.parse_error is not None:
            raise self.parse_error
        if not isinstance(payload, (dict, list)):
            raise ValueError("Input must be a list or a dictionary")

        entries = payload if isinstance(payload, list) else [payload]
        items: list[FakeItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("All items in the list must be dictionaries")
            items.append(
                FakeItem(
                    source=entry,
                    template=str(entry.get("template", "fake_template")),
                    promotion_list=entry.get("promotion_list", [{}]),
                    product=entry.get("product", [[]]),
                )
            )
        return items

    def render_item(self, item: FakeItem) -> FakeResult:
        if self.render_error is not None:
            raise self.render_error
        result_index = len(self.rendered_items)
        self.rendered_items.append(item)
        return self.results[result_index]

    def response_payload(self, results: list[FakeResult]) -> dict[str, Any]:
        return {"result": [result.as_payload() for result in results]}


def sample_payload(token: str = "one") -> dict[str, Any]:
    return {
        "template": "fake_template",
        "promotion_list": [{"token": token}],
        "product": [[f"{token}.png"]],
    }


class TemplatesApiTests(unittest.TestCase):
    def make_client(self, runtime: FakeRuntime) -> FlaskClient:
        app = create_app(runtime)
        app.config.update(TESTING=True)
        return app.test_client()

    def test_health_reports_renderer_version_and_cors(self) -> None:
        client = self.make_client(FakeRuntime())

        response = client.get("/health", headers={"Origin": "https://example.test"})

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok", "version": "9.8.7"}, response.get_json())
        self.assertEqual(
            "https://example.test", response.headers["Access-Control-Allow-Origin"]
        )

    def test_create_aliases_accept_one_object(self) -> None:
        for route in ("/create", "/api/create"):
            with self.subTest(route=route):
                runtime = FakeRuntime([FakeResult(id="success", successful=True)])
                client = self.make_client(runtime)

                with patch("templates_api._release_render_memory"):
                    response = client.post(route, json=sample_payload())

                self.assertEqual(200, response.status_code)
                self.assertTrue(response.get_json()["result"][0]["successful"])
                self.assertEqual(1, runtime.activation_count)
                self.assertEqual(
                    [sample_payload()],
                    [item.source for item in runtime.rendered_items],
                )

    def test_create_keeps_batch_order_and_returns_207_for_mixed_results(self) -> None:
        runtime = FakeRuntime(
            [
                FakeResult(id="first", successful=True),
                FakeResult(id="second", successful=False, message="failed"),
            ]
        )
        client = self.make_client(runtime)

        with patch("templates_api._release_render_memory"):
            response = client.post(
                "/create", json=[sample_payload("first"), sample_payload("second")]
            )

        self.assertEqual(207, response.status_code)
        self.assertEqual(
            ["first", "second"],
            [result["id"] for result in response.get_json()["result"]],
        )

    def test_create_returns_400_when_every_render_fails(self) -> None:
        runtime = FakeRuntime(
            [FakeResult(id="failure", successful=False, message="failed")]
        )
        client = self.make_client(runtime)

        with patch("templates_api._release_render_memory"):
            response = client.post("/create", json=sample_payload())

        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["result"][0]["successful"])

    def test_create_accepts_an_empty_batch(self) -> None:
        client = self.make_client(FakeRuntime())

        response = client.post("/create", json=[])

        self.assertEqual(200, response.status_code)
        self.assertEqual({"result": []}, response.get_json())

    def test_create_reports_bad_input_as_json(self) -> None:
        client = self.make_client(
            FakeRuntime(parse_error=ValueError("Input must be a dictionary"))
        )

        response = client.post("/create", json={"bad": True})

        self.assertEqual(400, response.status_code)
        self.assertEqual({"error": "Input must be a dictionary"}, response.get_json())

    def test_non_json_requests_keep_flask_http_error_behavior(self) -> None:
        client = self.make_client(FakeRuntime())

        response = client.post("/create", data="not json", content_type="text/plain")

        self.assertEqual(415, response.status_code)
        self.assertIsNone(response.get_json(silent=True))

    def test_unexpected_error_has_sanitized_error_envelope(self) -> None:
        client = self.make_client(FakeRuntime(render_error=RuntimeError("secret")))

        with patch("templates_api._release_render_memory"):
            response = client.post(
                "/create",
                json=sample_payload(),
                headers={"X-Request-ID": "request-123"},
            )

        payload = response.get_json()
        self.assertEqual(500, response.status_code)
        self.assertEqual("POSTER_GENERATION_FAILED", payload["error"])
        self.assertEqual("request-123", payload["requestId"])
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertTrue(payload["errorId"])

    def test_clean_routes_are_not_exposed(self) -> None:
        client = self.make_client(FakeRuntime())

        for route in ("/clean", "/api/clean"):
            with self.subTest(route=route):
                response = client.post(route)

                self.assertEqual(404, response.status_code)


class LambdaRuntimeIntegrationTests(unittest.TestCase):
    def test_loader_rejects_a_foreign_render_service_module(self) -> None:
        foreign_module = SimpleNamespace(__file__=str(REPOSITORY_ROOT.parent / "foreign.py"))

        with patch.dict(sys.modules, {"render_service": foreign_module}):
            with self.assertRaisesRegex(RuntimeError, "render_service"):
                load_lambda_runtime()

    def test_live_schema_validation_is_returned_by_the_http_adapter(self) -> None:
        original_cwd = Path.cwd()
        runtime = load_lambda_runtime()
        app = create_app(runtime)
        app.config.update(TESTING=True)

        try:
            response = app.test_client().post(
                "/create",
                json={
                    "template": "sasa_202607001",
                    "promotion_list": [{"brand_name": ["not", "a", "string"]}],
                    "product": [[]],
                },
            )

            self.assertEqual(400, response.status_code)
            self.assertIn("only 'gwp_image' accepts", response.get_json()["errors"][0])
        finally:
            os.chdir(original_cwd)

    def test_live_schema_and_one_item_render_adapter_are_imported(self) -> None:
        original_cwd = Path.cwd()
        runtime = load_lambda_runtime()
        render_calls: list[dict[str, Any]] = []

        try:
            runtime.activate()
            items = runtime.parse_items(
                {
                    "template": "sasa_202607001",
                    "promotion_list": [{}],
                    "product": [[]],
                }
            )
            fake_runtime: LambdaTemplatesRuntime = replace(
                runtime,
                render_payload_function=lambda payload: render_calls.append(payload)
                or FakeResult(id="fake", successful=True),
            )

            result = fake_runtime.render_item(items[0])

            self.assertEqual(LAMBDA_REPOSITORY_ROOT, Path.cwd().resolve())
            self.assertEqual(str(LAMBDA_REPOSITORY_ROOT), sys.path[0])
            for module_name in ("app", "render_service"):
                module_path = Path(sys.modules[module_name].__file__).resolve()
                self.assertIn(LAMBDA_REPOSITORY_ROOT, module_path.parents)
            self.assertEqual("fake", result.id)
            self.assertEqual(1, len(render_calls))
            self.assertEqual("sasa_202607001", render_calls[0]["template"])
            response = runtime.response_payload(
                [
                    {
                        "id": "failed",
                        "reference_jpg": "",
                        "fabric_model": None,
                        "message": "failed",
                        "successful": False,
                    }
                ]
            )
            self.assertEqual("failed", response["result"][0]["id"])
            self.assertFalse(response["result"][0]["successful"])
        finally:
            os.chdir(original_cwd)

    def test_server_integer_settings_are_validated(self) -> None:
        with patch.dict(os.environ, {"TEST_INTEGER": "8123"}):
            self.assertEqual(8123, _positive_integer("TEST_INTEGER", 1, 65535))

        for value in ("0", "65536", "not-an-integer"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"TEST_INTEGER": value}):
                    with self.assertRaises(ValueError):
                        _positive_integer("TEST_INTEGER", 1, 65535)


if __name__ == "__main__":
    unittest.main()
