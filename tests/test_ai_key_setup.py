from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ai_key_setup as app


PROVIDER = app.ProviderConfig(
    provider_id="hyaloria",
    provider_name="Hyaloria",
    base_url="https://hyaloria.com/v1",
    model="kimi-k3",
    env_name="HYALORIA_API_KEY",
)

EXISTING_CONFIG = r'''model = "old-model"
model_provider = "hyaloria"
notify = ["C:\\Tools\\notify.exe", "turn-ended"]

[model_providers.hyaloria]
name = "Old Hyaloria"
base_url = "https://old.example/v1"
env_key = "key-placeholder-not-a-credential"
wire_api = "responses"

[plugins."browser@example"]
enabled = true

[mcp_servers.node]
command = "node"

[projects."C:\\work"]
trust_level = "trusted"
'''


class ConfigMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def test_updates_target_and_preserves_unrelated_sections(self) -> None:
        updated = app.update_codex_config(EXISTING_CONFIG, PROVIDER)
        parsed = app.tomllib.loads(updated)

        self.assertEqual(parsed["model"], "kimi-k3")
        self.assertEqual(parsed["model_provider"], "hyaloria")
        provider = parsed["model_providers"]["hyaloria"]
        self.assertEqual(provider["env_key"], "HYALORIA_API_KEY")
        self.assertEqual(provider["wire_api"], "responses")
        self.assertTrue(parsed["plugins"]["browser@example"]["enabled"])
        self.assertEqual(parsed["mcp_servers"]["node"]["command"], "node")
        self.assertEqual(parsed["projects"][r"C:\work"]["trust_level"], "trusted")
        self.assertNotIn("key-placeholder", updated)

    def test_update_is_idempotent(self) -> None:
        first = app.update_codex_config(EXISTING_CONFIG, PROVIDER)
        second = app.update_codex_config(first, PROVIDER)
        self.assertEqual(first, second)

    def test_crlf_config_stays_crlf_and_idempotent(self) -> None:
        existing = 'model = "old"\r\nmodel_provider = "openai"\r\nnotify = ["done"]\r\n'
        first = app.update_codex_config(existing, PROVIDER)
        second = app.update_codex_config(first, PROVIDER)
        self.assertEqual(first, second)
        self.assertNotIn("\n", first.replace("\r\n", ""))

    def test_rejects_secret_env_key_on_unrelated_provider(self) -> None:
        existing = '''model = "old"
model_provider = "other"

[model_providers.other]
name = "Other"
base_url = "https://example.com/v1"
env_key = 'not-an-environment-name'
wire_api = "responses"
'''
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_rejects_api_key_used_as_environment_name(self) -> None:
        mistaken_key = "sk" + "-this-is-a-secret"
        with self.assertRaises(app.SetupError):
            app.validate_env_name(mistaken_key)

    def test_rejects_credentials_embedded_in_base_url(self) -> None:
        provider = app.ProviderConfig(
            "gateway",
            "Gateway",
            "https://user:password@example.com/v1",
            "model",
            "GATEWAY_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=False)

    def test_builtin_openai_is_not_redefined(self) -> None:
        provider = app.ProviderConfig(
            "openai", "OpenAI", "https://api.openai.com/v1", "gpt-test", "OPENAI_API_KEY"
        )
        updated = app.update_codex_config("", provider)
        self.assertNotIn("[model_providers.openai]", updated)
        parsed = app.tomllib.loads(updated)
        self.assertEqual(parsed["model_provider"], "openai")

    def test_doctor_result_only_uses_config_check(self) -> None:
        report = {
            "overallStatus": "fail",
            "checks": {
                "config.load": {"status": "ok", "summary": "config loaded"},
                "terminal.env": {"status": "fail", "summary": "not a terminal"},
            },
        }
        output = "warning before JSON\n" + json.dumps(report) + "\n"
        ok, summary = app.parse_doctor_config_check(output)
        self.assertTrue(ok)
        self.assertEqual(summary, "config loaded")


class FileSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def test_backup_atomic_write_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_text('model = "before"\nmodel_provider = "openai"\n', encoding="utf-8")
            backup = app.backup_file(config)
            self.assertIsNotNone(backup)

            app.atomic_write(config, 'model = "after"\nmodel_provider = "openai"\n')
            args = SimpleNamespace(config=str(config), backup=str(backup))
            with contextlib.redirect_stdout(io.StringIO()):
                result = app.rollback_codex(args)

            self.assertEqual(result, 0)
            self.assertIn('model = "before"', config.read_text(encoding="utf-8"))

    def test_dry_run_does_not_write_or_require_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(EXISTING_CONFIG, encoding="utf-8")
            original = config.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = app.main(
                    [
                        "codex",
                        "--config",
                        str(config),
                        "--dry-run",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(config.read_bytes(), original)
            self.assertIn("不会读取 Key", output.getvalue())

    def test_invalid_toml_is_never_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text('model = "unterminated\n', encoding="utf-8")
            original = config.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                result = app.main(["codex", "--config", str(config), "--dry-run"])
            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), original)

    def test_process_scope_configuration_never_embeds_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(EXISTING_CONFIG, encoding="utf-8")
            source_name = "AI_KEY_SETUP_TEST_SOURCE"
            secret = "unit-test-secret-value"
            os.environ[source_name] = secret
            os.environ.pop("HYALORIA_API_KEY", None)
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output), mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    return_value=(True, "Codex 严格配置检查通过。"),
                ):
                    result = app.main(
                        [
                            "codex",
                            "--config",
                            str(config),
                            "--key-from-env",
                            source_name,
                            "--scope",
                            "process",
                            "--skip-network-check",
                        ]
                    )
                self.assertEqual(result, 0)
                content = config.read_text(encoding="utf-8")
                self.assertNotIn(secret, content)
                self.assertIn('env_key = "HYALORIA_API_KEY"', content)
                self.assertTrue(list((config.parent / "backups" / "ai-key-setup").glob("*.bak")))
            finally:
                os.environ.pop(source_name, None)
                os.environ.pop("HYALORIA_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
