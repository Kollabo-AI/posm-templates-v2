from __future__ import annotations

import base64
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from app.schema import GenerationResult
from app.types import FabricCanvas

def FabricGroup(**fields: object) -> dict:
    return {"type": "Group", **fields}

def FabricObject(**fields: object) -> dict:
    return fields
from worker import (
    ExpressJobClient,
    JobAlreadyHandled,
    TaskMessage,
    create_thumbnail_data_url,
    process_record,
    successful_callback,
)


class FakeResponse:
    def __init__(self, data: dict[str, object], status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._data

    @property
    def text(self) -> str:
        return json.dumps(self._data)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, get_response: FakeResponse | None = None) -> None:
        self.posts: list[dict[str, object]] = []
        self.get_response = get_response

    def get(self, *args: object, **kwargs: object) -> FakeResponse:
        return self.get_response or FakeResponse({
            "payload": {"template": "template"},
        })

    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.posts.append({"args": args, "kwargs": kwargs})
        return FakeResponse({"accepted": True})


def sqs_record(message_id: str = "message-1") -> dict[str, object]:
    return {
        "messageId": message_id,
        "body": json.dumps({
            "version": 1,
            "jobId": "job-1",
            "itemIndex": 2,
        }),
    }


def image_data_url(width: int = 1200, height: int = 600) -> str:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="JPEG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class FakeClient:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload or {"template": "template"}
        self.completed: list[dict[str, object]] = []
        self.already_handled = False

    def claim(self, task: TaskMessage) -> object:
        if self.already_handled:
            raise JobAlreadyHandled
        return self.payload

    def complete(self, task: TaskMessage, callback: dict[str, object]) -> None:
        self.completed.append(callback)


class WorkerTests(unittest.TestCase):
    def test_task_message_parses_versioned_body(self) -> None:
        task = TaskMessage.from_record(sqs_record())
        self.assertEqual(task.job_id, "job-1")
        self.assertEqual(task.item_index, 2)

    def test_task_message_rejects_unknown_version(self) -> None:
        record = sqs_record()
        record["body"] = json.dumps({"version": 2, "jobId": "job-1", "itemIndex": 0})
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            TaskMessage.from_record(record)

    def test_thumbnail_preserves_aspect_ratio(self) -> None:
        thumbnail_url = create_thumbnail_data_url(image_data_url())
        with Image.open(BytesIO(base64.b64decode(thumbnail_url.split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (640, 320))

    def test_express_client_uses_worker_secret_on_result_callback(self) -> None:
        session = FakeSession()
        client = ExpressJobClient(
            "https://api.example.test/api/internal",
            "worker-secret",
            session=session,
        )
        task = TaskMessage(version=1, job_id="job-1", item_index=2)

        self.assertEqual(client.claim(task), {"template": "template"})
        client.complete(task, {"successful": False, "error": "failed"})

        headers = session.posts[0]["kwargs"]["headers"]
        self.assertEqual(headers["X-Job-Worker-Secret"], "worker-secret")
        self.assertNotIn("X-Job-Claim-Token", headers)

    def test_express_client_logs_http_status_and_response_without_secret(self) -> None:
        session = FakeSession(FakeResponse({"error": "Worker authentication required"}, 401))
        client = ExpressJobClient(
            "https://api.example.test/api/internal",
            "do-not-log-this-secret",
            session=session,
        )
        task = TaskMessage(version=1, job_id="job-1", item_index=2)

        with self.assertLogs("worker", level="ERROR") as captured:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                client.claim(task)

        log_output = "\n".join(captured.output)
        self.assertIn("method=GET", log_output)
        self.assertIn("status=401", log_output)
        self.assertIn("Worker authentication required", log_output)
        self.assertNotIn("do-not-log-this-secret", log_output)

    def test_successful_render_posts_current_callback_contract(self) -> None:
        client = FakeClient()
        result_data = {
            "id": "render-1",
            "reference_jpg": image_data_url(),
            "fabric_model": {"objects": []},
            "message": None,
            "successful": True,
        }
        result = SimpleNamespace(
            id="render-1",
            successful=True,
            message=None,
            model_dump=lambda mode: dict(result_data),
        )

        process_record(sqs_record(), client, renderer=lambda payload: result)

        callback = client.completed[0]
        self.assertTrue(callback["successful"])
        self.assertIn("thumbnailUrl", callback["result"])
        self.assertNotIn("error", callback)

    def test_successful_callback_preserves_nested_fabric_names(self) -> None:
        result = GenerationResult(
            id="render-1",
            reference_jpg=image_data_url(),
            fabric_model=FabricCanvas(
                width=100,
                height=100,
                objects=[
                    FabricGroup(
                        name="Product image",
                        left=10,
                        top=20,
                        width=30,
                        height=40,
                        objects=[
                            FabricObject(
                                type="rect",
                                name="Product image shadow",
                                left=-15,
                                top=-20,
                                width=30,
                                height=40,
                            )
                        ],
                    )
                ],
            ),
            message=None,
            successful=True,
        )

        callback = successful_callback(result)

        self.assertEqual({"successful", "result"}, set(callback))
        self.assertTrue(callback["successful"])
        group = callback["result"]["fabric_model"]["objects"][0]
        self.assertEqual("Product image", group["name"])
        self.assertEqual("Product image shadow", group["objects"][0]["name"])
        self.assertIn("thumbnailUrl", callback["result"])

    def test_render_exception_posts_failure_and_acknowledges_after_callback(self) -> None:
        client = FakeClient()

        def fail(_: object) -> object:
            raise ValueError("invalid poster")

        process_record(sqs_record(), client, renderer=fail)
        self.assertEqual(client.completed, [{
            "successful": False,
            "error": "invalid poster",
        }])

    def test_already_handled_item_is_acknowledged_without_rendering(self) -> None:
        client = FakeClient()
        client.already_handled = True
        rendered = False

        def render(_: object) -> object:
            nonlocal rendered
            rendered = True
            return object()

        process_record(sqs_record(), client, renderer=render)
        self.assertFalse(rendered)
        self.assertEqual(client.completed, [])

if __name__ == "__main__":
    unittest.main()
