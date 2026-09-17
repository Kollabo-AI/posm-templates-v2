#!/var/lang/bin/python3
"""Start the local AWS Workload Credentials Provider before Lambda runtime init."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


EXTENSIONS_API_VERSION = "2020-01-01"
PROVIDER_COMMAND = (
    "/opt/bin/aws-workload-credentials-provider",
    "sm",
    "start",
)
PROVIDER_PING_URL = "http://localhost:2773/ping"
PROVIDER_START_TIMEOUT_SECONDS = 20.0
PROVIDER_POLL_INTERVAL_SECONDS = 0.1

_provider: subprocess.Popen[bytes] | None = None


def _runtime_api_url(path: str) -> str:
    runtime_api = os.environ.get("AWS_LAMBDA_RUNTIME_API", "").strip()
    if not runtime_api:
        raise RuntimeError("AWS_LAMBDA_RUNTIME_API is required")
    return f"http://{runtime_api}/{EXTENSIONS_API_VERSION}/extension/{path}"


def register(extension_name: str) -> str:
    request = urllib.request.Request(
        _runtime_api_url("register"),
        data=b'{"events":["INVOKE","SHUTDOWN"]}',
        headers={"Lambda-Extension-Name": extension_name},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        extension_id = response.headers.get("Lambda-Extension-Identifier")
        response.read()
    if not extension_id:
        raise RuntimeError("Lambda extension registration returned no identifier")
    return extension_id


def next_event(extension_id: str) -> dict[str, Any]:
    request = urllib.request.Request(
        _runtime_api_url("event/next"),
        headers={"Lambda-Extension-Identifier": extension_id},
        method="GET",
    )
    with urllib.request.urlopen(request) as response:
        event = json.loads(response.read())
    if not isinstance(event, dict):
        raise RuntimeError("Lambda extension event must be an object")
    return event


def _provider_is_healthy() -> bool:
    try:
        with urllib.request.urlopen(PROVIDER_PING_URL, timeout=0.5) as response:
            return response.read() == b"healthy"
    except (urllib.error.URLError, OSError):
        return False


def start_provider() -> None:
    global _provider
    if _provider is not None and _provider.poll() is None:
        return
    print("[secrets-manager-provider-extension] starting provider", flush=True)
    _provider = subprocess.Popen(PROVIDER_COMMAND, cwd="/tmp")
    deadline = time.monotonic() + PROVIDER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        return_code = _provider.poll()
        if return_code is not None:
            raise RuntimeError(
                f"Workload Credentials Provider exited during startup: {return_code}"
            )
        if _provider_is_healthy():
            print(
                "[secrets-manager-provider-extension] provider is healthy",
                flush=True,
            )
            return
        time.sleep(PROVIDER_POLL_INTERVAL_SECONDS)
    raise TimeoutError("Workload Credentials Provider did not become healthy in 20s")


def stop_provider() -> None:
    global _provider
    if _provider is None or _provider.poll() is not None:
        return
    _provider.terminate()
    try:
        _provider.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _provider.kill()
        _provider.wait(timeout=2)


def _handle_termination(_signum: int, _frame: object) -> None:
    stop_provider()
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    extension_name = Path(sys.argv[0]).name
    start_provider()
    extension_id = register(extension_name)
    print(
        f"[secrets-manager-provider-extension] registered {extension_name}",
        flush=True,
    )
    while True:
        event_type = next_event(extension_id).get("eventType")
        if event_type == "INVOKE":
            start_provider()
        elif event_type == "SHUTDOWN":
            stop_provider()
            return


if __name__ == "__main__":
    main()
