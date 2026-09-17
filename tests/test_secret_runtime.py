from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import lambda_entrypoint
import lambda_secrets_extension


ROOT = Path(__file__).resolve().parents[1]


def load_asm_exec():
    loader = importlib.machinery.SourceFileLoader(
        "poster_asm_exec_under_test", str(ROOT / "asm-exec")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load asm-exec")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeProcess:
    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: int) -> int:
        return 0


class SecretRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        lambda_secrets_extension._provider = None

    def test_executable_scripts_have_linux_shebangs_and_line_endings(self) -> None:
        expected = {
            "asm-exec": b"#!/usr/bin/env python3\n",
            "lambda_secrets_extension.py": b"#!/usr/bin/python3\n",
        }
        for filename, shebang in expected.items():
            with self.subTest(filename=filename):
                payload = (ROOT / filename).read_bytes()
                self.assertTrue(payload.startswith(shebang))
                self.assertNotIn(b"\r\n", payload)

    def test_entrypoint_replaces_arn_with_dynamic_reference_before_runtime(self) -> None:
        arn = "arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:poster-AbCdEf"
        environment = {"JOB_WORKER_SECRET_ARN": arn}
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(sys, "argv", ["lambda_entrypoint.py", "module.handler"]):
                with mock.patch.object(os, "execv") as execv:
                    lambda_entrypoint.main()

                self.assertEqual(
                    os.environ["JOB_WORKER_SECRET"],
                    f"{{{{resolve:secretsmanager:{arn}}}}}",
                )
                self.assertNotIn("JOB_WORKER_SECRET_ARN", os.environ)
        execv.assert_called_once_with(
            sys.executable,
            [
                sys.executable,
                "/var/task/asm-exec",
                "--",
                "/lambda-entrypoint.sh",
                "module.handler",
            ],
        )

    def test_entrypoint_rejects_non_secret_arn(self) -> None:
        with self.assertRaisesRegex(ValueError, "JOB_WORKER_SECRET_ARN"):
            lambda_entrypoint.secret_reference("arn:aws:s3:::example")

    def test_asm_exec_requires_full_secret_arn_and_never_uses_external_fallback(self) -> None:
        asm_exec = load_asm_exec()
        with self.assertRaisesRegex(ValueError, "full ARN"):
            asm_exec._parse_reference("poster-secret")
        source = (ROOT / "asm-exec").read_text(encoding="utf-8")
        self.assertNotIn("aws-mcp", source)
        self.assertNotIn("get-secret-value", source)

    def test_provider_starts_before_extension_registration(self) -> None:
        events: list[str] = []
        process = FakeProcess()
        with mock.patch.object(lambda_secrets_extension.signal, "signal"):
            with mock.patch.object(
                lambda_secrets_extension,
                "start_provider",
                side_effect=lambda: events.append("start"),
            ):
                with mock.patch.object(
                    lambda_secrets_extension,
                    "register",
                    side_effect=lambda _name: events.append("register") or "id",
                ):
                    with mock.patch.object(
                        lambda_secrets_extension,
                        "next_event",
                        side_effect=lambda _id: events.append("next")
                        or {"eventType": "SHUTDOWN"},
                    ):
                        with mock.patch.object(
                            lambda_secrets_extension,
                            "stop_provider",
                            side_effect=lambda: events.append("stop"),
                        ):
                            with mock.patch.object(
                                lambda_secrets_extension.subprocess,
                                "Popen",
                                return_value=process,
                            ):
                                lambda_secrets_extension.main()
        self.assertEqual(events, ["start", "register", "next", "stop"])


if __name__ == "__main__":
    unittest.main()
