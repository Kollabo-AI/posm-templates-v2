from __future__ import annotations

import ctypes
import gc
import logging
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from flask import Flask, Response, current_app, jsonify, request
from flask.typing import ResponseReturnValue
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException


REPOSITORY_ROOT = Path(__file__).resolve().parent
LAMBDA_REPOSITORY_ROOT = REPOSITORY_ROOT


class TemplatesRuntime(Protocol):
    endpoint_version: str

    def activate(self) -> None: ...

    def parse_items(self, payload: Any) -> list[Any]: ...

    def render_item(self, item: Any) -> Any: ...

    def response_payload(self, results: list[Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LambdaTemplatesRuntime:
    repository_root: Path
    create_payload_model: type[Any]
    create_response_model: type[Any]
    render_payload_function: Callable[[Any], Any]
    endpoint_version: str

    def activate(self) -> None:
        if Path.cwd().resolve() != self.repository_root:
            os.chdir(self.repository_root)

    def parse_items(self, payload: Any) -> list[Any]:
        return list(self.create_payload_model(payload).get_items())

    def render_item(self, item: Any) -> Any:
        return self.render_payload_function(item.model_dump(mode="json"))

    def response_payload(self, results: list[Any]) -> dict[str, Any]:
        response = self.create_response_model(result=results)
        return response.model_dump(mode="json")


def _module_belongs_to(module: Any, repository_root: Path) -> bool:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False

    resolved_file = Path(module_file).resolve()
    return resolved_file == repository_root or repository_root in resolved_file.parents


def load_lambda_runtime() -> LambdaTemplatesRuntime:
    schema_path = LAMBDA_REPOSITORY_ROOT / "app" / "schema.py"
    render_service_path = LAMBDA_REPOSITORY_ROOT / "render_service.py"
    if not schema_path.is_file() or not render_service_path.is_file():
        raise RuntimeError(
            "The templates runtime is unavailable. "
            "Reinstall the service package."
        )

    for module_name in ("app", "render_service"):
        loaded_module = sys.modules.get(module_name)
        if loaded_module is not None and not _module_belongs_to(
            loaded_module, LAMBDA_REPOSITORY_ROOT
        ):
            raise RuntimeError(
                f"A different top-level `{module_name}` module is already loaded; "
                "start the templates API in a fresh Python process."
            )

    lambda_path = str(LAMBDA_REPOSITORY_ROOT)
    sys.path[:] = [entry for entry in sys.path if entry != lambda_path]
    sys.path.insert(0, lambda_path)

    from app.__version__ import ENDPOINT_VERSION  # type: ignore[import]
    from app.schema import CreatePayload, CreateResponse  # type: ignore[import]
    from render_service import render_payload  # type: ignore[import]

    return LambdaTemplatesRuntime(
        repository_root=LAMBDA_REPOSITORY_ROOT,
        create_payload_model=CreatePayload,
        create_response_model=CreateResponse,
        render_payload_function=render_payload,
        endpoint_version=ENDPOINT_VERSION,
    )


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _release_render_memory(log: logging.Logger) -> None:
    try:
        collected = gc.collect()
        log.info("Garbage collection completed successfully (count=%s)", collected)
    except Exception as error:
        log.error("GC failed: %s", error)

    if sys.platform != "linux":
        return

    try:
        malloc_trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if malloc_trim is None:
            return
        malloc_trim(0)
        log.info("Memory trim completed successfully")
    except Exception as error:
        log.error("Memory trim failed: %s", error)


def create_app(runtime: TemplatesRuntime | None = None) -> Flask:
    app = Flask(__name__)
    _configure_logging(app)

    os.environ.setdefault("POSM_LOG_DIR", str(REPOSITORY_ROOT / "logs"))
    renderer = runtime or load_lambda_runtime()
    render_lock = threading.Lock()

    CORS(app, resources={r"/*": {"origins": "*"}})
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
    limiter.init_app(app)

    @app.before_request
    def _log_request() -> None:
        if request.path not in {"/create", "/api/create"}:
            app.logger.info("%s %s", request.method, request.path)

    @app.errorhandler(Exception)
    def _handle_unexpected_error(error: Exception) -> ResponseReturnValue:
        if isinstance(error, HTTPException):
            return error

        error_id = str(uuid.uuid4())
        request_id = request.headers.get("X-Request-ID", "")[:200]
        app.logger.exception(
            "Unhandled request error error_id=%s request_id=%s method=%s path=%s",
            error_id,
            request_id or "none",
            request.method,
            request.path,
        )
        return (
            jsonify(
                {
                    "error": "POSTER_GENERATION_FAILED",
                    "message": "Poster generation encountered an unexpected error",
                    "errorId": error_id,
                    "requestId": request_id or None,
                }
            ),
            500,
        )

    @app.get("/health")
    def health_check() -> tuple[Response, int]:
        return jsonify({"status": "ok", "version": renderer.endpoint_version}), 200

    @app.post("/create")
    @app.post("/api/create")
    @limiter.limit("300 per minute")
    def create() -> tuple[Response, int]:
        json_data = request.get_json()

        with render_lock:
            renderer.activate()
            try:
                items = renderer.parse_items(json_data)
            except ValidationError as error:
                messages = [entry["msg"] for entry in error.errors()]
                current_app.logger.warning(
                    "/create rejected (validation): %s", messages
                )
                return jsonify({"errors": messages}), 400
            except ValueError as error:
                current_app.logger.warning("/create rejected (bad input): %s", error)
                return jsonify({"error": str(error)}), 400

            current_app.logger.info("/create: %s item(s) to generate", len(items))
            generations: list[Any] = []

            for index, item in enumerate(items, start=1):
                product_count = sum(len(products) for products in item.product)
                current_app.logger.info(
                    "item %s/%s: template=%r, promotions=%s, products=%s",
                    index,
                    len(items),
                    item.template,
                    len(item.promotion_list),
                    product_count,
                )

                try:
                    output = renderer.render_item(item)
                finally:
                    _release_render_memory(current_app.logger)
                generations.append(output)

                if output.successful:
                    current_app.logger.info("item %s: OK (id=%s)", index, output.id)
                else:
                    current_app.logger.error(
                        "item %s: FAILED (id=%s): %s",
                        index,
                        output.id,
                        output.message,
                    )

            response_payload = renderer.response_payload(generations)

        all_successful = all(result.successful for result in generations)
        any_successful = any(result.successful for result in generations)
        status_code = 200 if all_successful else 207 if any_successful else 400
        succeeded = sum(result.successful for result in generations)
        current_app.logger.info(
            "/create done: %s/%s succeeded, status=%s",
            succeeded,
            len(generations),
            status_code,
        )
        return jsonify(response_payload), status_code

    return app


def _positive_integer(name: str, default: int, maximum: int | None = None) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from error

    if value < 1 or (maximum is not None and value > maximum):
        expected = f"between 1 and {maximum}" if maximum is not None else "positive"
        raise ValueError(f"{name} must be {expected}, got {value}")
    return value


def main() -> None:
    from waitress import serve

    host = os.getenv("POSM_TEMPLATES_HOST", "0.0.0.0")
    port = _positive_integer("POSM_TEMPLATES_PORT", 8123, maximum=65535)
    threads = _positive_integer("POSM_TEMPLATES_THREADS", 4)
    app = create_app()

    print(f"POSM templates API listening on http://{host}:{port}")
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
