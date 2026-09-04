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

EXISTING_CONFIG = r"""model = "old-model"
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
"""


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

    def test_preserves_target_extensions_and_nested_tables(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Old"
base_url = "https://old.example/v1"
env_key = "OLD_KEY"
request_max_retries = 2

[model_providers.hyaloria.env_http_headers]
"X-Trace-Id" = "TRACE_ID"

[plugins.demo]
enabled = true
"""
        updated = app.update_codex_config(existing, PROVIDER)
        parsed = app.tomllib.loads(updated)
        target = parsed["model_providers"]["hyaloria"]

        self.assertEqual(target["request_max_retries"], 2)
        self.assertEqual(target["env_http_headers"]["X-Trace-Id"], "TRACE_ID")
        self.assertTrue(parsed["plugins"]["demo"]["enabled"])
        self.assertEqual(updated, app.update_codex_config(updated, PROVIDER))

    def test_inserts_parent_before_existing_implicit_nested_table(self) -> None:
        existing = """model = "old"
model_provider = "openai"

[model_providers.hyaloria.env_http_headers]
"X-Trace-Id" = "TRACE_ID"
"""
        updated = app.update_codex_config(existing, PROVIDER)
        target = app.tomllib.loads(updated)["model_providers"]["hyaloria"]
        self.assertEqual(target["env_key"], "HYALORIA_API_KEY")
        self.assertEqual(target["env_http_headers"]["X-Trace-Id"], "TRACE_ID")
        self.assertLess(
            updated.index("[model_providers.hyaloria]"),
            updated.index("[model_providers.hyaloria.env_http_headers]"),
        )

    def test_rejects_inline_provider_table_instead_of_losing_fields(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"
model_providers = { hyaloria = { name = "Old", base_url = "https://old.example/v1", env_key = "OLD_KEY", custom = "keep" } }
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_rejects_dotted_provider_keys_with_nested_table(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"
model_providers.hyaloria.name = "Old"
model_providers.hyaloria.base_url = "https://old.example/v1"

[model_providers.hyaloria.env_http_headers]
"X-Trace-Id" = "TRACE_ID"
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_rejects_provider_array_table(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[[model_providers.hyaloria]]
name = "invalid"
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_table_text_inside_multiline_string_is_not_a_section(self) -> None:
        fake_key = "s" + "k-example-text-that-is-not-a-setting"
        existing = f'''instructions = """
[model_providers.hyaloria]
env_key = "{fake_key}"
model = "also-text-only"
# >>> ai-key-setup managed defaults >>>
# <<< ai-key-setup managed defaults <<<
"""
model = "old"
model_provider = "openai"

[plugins.demo]
enabled = true
'''
        updated = app.update_codex_config(existing, PROVIDER)
        parsed = app.tomllib.loads(updated)

        self.assertIn("[model_providers.hyaloria]", parsed["instructions"])
        self.assertIn('model = "also-text-only"', parsed["instructions"])
        self.assertIn("managed defaults", parsed["instructions"])
        self.assertTrue(parsed["plugins"]["demo"]["enabled"])

    def test_provider_multiline_text_cannot_spoof_managed_lines(self) -> None:
        existing = '''model = "old"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Old"
base_url = "https://old.example/v1"
env_key = "OLD_KEY"
description = """
name = "text only"
# >>> ai-key-setup managed provider >>>
# <<< ai-key-setup managed provider <<<
"""
request_max_retries = 3
'''
        updated = app.update_codex_config(existing, PROVIDER)
        target = app.tomllib.loads(updated)["model_providers"]["hyaloria"]
        self.assertIn('name = "text only"', target["description"])
        self.assertIn("managed provider", target["description"])
        self.assertEqual(target["request_max_retries"], 3)

    def test_quoted_provider_header_is_updated_in_place(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[model_providers."hyaloria"] # keep this comment
name = "Old"
base_url = "https://old.example/v1"
env_key = "OLD_KEY"
wire_api = "responses"
request_max_retries = 3
"""
        updated = app.update_codex_config(existing, PROVIDER)
        self.assertIn('[model_providers."hyaloria"] # keep this comment', updated)
        self.assertEqual(
            app.tomllib.loads(updated)["model_providers"]["hyaloria"][
                "request_max_retries"
            ],
            3,
        )

    def test_command_auth_requires_explicit_replacement(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Old"
base_url = "https://old.example/v1"

[model_providers.hyaloria.auth]
command = "credential-helper"
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

        updated = app.update_codex_config(existing, PROVIDER, replace_auth=True)
        target = app.tomllib.loads(updated)["model_providers"]["hyaloria"]
        self.assertNotIn("auth", target)
        self.assertEqual(target["env_key"], "HYALORIA_API_KEY")

    def test_static_authorization_header_is_rejected(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Old"
base_url = "https://old.example/v1"
env_key = "OLD_KEY"

[model_providers.hyaloria.http_headers]
Authorization = "Bearer plaintext-secret"
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_inline_bearer_token_is_removed(self) -> None:
        existing = """model = "old"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Old"
base_url = "https://old.example/v1"
experimental_bearer_token = "secret-value-that-must-go"
"""
        updated = app.update_codex_config(existing, PROVIDER)
        target = app.tomllib.loads(updated)["model_providers"]["hyaloria"]
        self.assertNotIn("experimental_bearer_token", target)
        self.assertNotIn("secret-value-that-must-go", updated)

    def test_multiline_root_model_assignment_is_replaced(self) -> None:
        existing = '''model = """
old-model
"""
model_provider = "openai"
notify = ["done"]
'''
        updated = app.update_codex_config(existing, PROVIDER)
        parsed = app.tomllib.loads(updated)
        self.assertEqual(parsed["model"], "kimi-k3")
        self.assertEqual(parsed["notify"], ["done"])

    def test_openai_reserved_section_is_removed(self) -> None:
        provider = app.ProviderConfig(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            "gpt-test",
            "OPENAI_API_KEY",
        )
        existing = """model = "old"
model_provider = "openai"

[model_providers.openai]
name = "Invalid override"
base_url = "https://example.com/v1"
env_key = "OPENAI_API_KEY"
"""
        updated = app.update_codex_config(existing, provider)
        parsed = app.tomllib.loads(updated)
        self.assertNotIn("openai", parsed.get("model_providers", {}))

    def test_rejects_secret_env_key_on_unrelated_provider(self) -> None:
        existing = """model = "old"
model_provider = "other"

[model_providers.other]
name = "Other"
base_url = "https://example.com/v1"
env_key = 'not-an-environment-name'
wire_api = "responses"
"""
        with self.assertRaises(app.SetupError):
            app.update_codex_config(existing, PROVIDER)

    def test_rejects_api_key_used_as_environment_name(self) -> None:
        mistaken_key = "sk" + "-this-is-a-secret"
        with self.assertRaises(app.SetupError):
            app.validate_env_name(mistaken_key)

    def test_rejects_protected_system_environment_name(self) -> None:
        with self.assertRaises(app.SetupError):
            app.validate_env_name("PATH")

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

    def test_rejects_non_http_base_url_even_when_insecure_is_allowed(self) -> None:
        provider = app.ProviderConfig(
            "gateway",
            "Gateway",
            "ftp://example.com/v1",
            "model",
            "GATEWAY_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=True)

    def test_rejects_endpoint_url_instead_of_api_root(self) -> None:
        provider = app.ProviderConfig(
            "gateway",
            "Gateway",
            "https://example.com/v1/responses",
            "model",
            "GATEWAY_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=False)

    def test_rejects_invalid_url_port(self) -> None:
        provider = app.ProviderConfig(
            "gateway",
            "Gateway",
            "https://example.com:not-a-port/v1",
            "model",
            "GATEWAY_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=False)

    def test_rejects_whitespace_inside_base_url(self) -> None:
        provider = app.ProviderConfig(
            "gateway",
            "Gateway",
            "https://example.com/v1\n/next",
            "model",
            "GATEWAY_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=False)

    def test_table_marker_name_does_not_confuse_header_parser(self) -> None:
        marker = "__ai_key_setup_table_marker_4b48f5b7__"
        self.assertEqual(
            app._parse_table_header_path(f"[model_providers.{marker}]\n"),
            ("model_providers", marker),
        )

    def test_rejects_amazon_bedrock_as_custom_http_provider(self) -> None:
        provider = app.ProviderConfig(
            "amazon-bedrock",
            "Amazon Bedrock",
            "https://example.com/v1",
            "model",
            "BEDROCK_API_KEY",
        )
        with self.assertRaises(app.SetupError):
            app.validate_provider(provider, allow_insecure_http=False)

    def test_builtin_openai_is_not_redefined(self) -> None:
        provider = app.ProviderConfig(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            "gpt-test",
            "OPENAI_API_KEY",
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

    def test_doctor_parser_ignores_unrelated_json_before_report(self) -> None:
        report = {"checks": {"config.load": {"status": "ok", "summary": "loaded"}}}
        output = '{"event":"startup"}\nwarning\n' + json.dumps(report)
        ok, summary = app.parse_doctor_config_check(output)
        self.assertTrue(ok)
        self.assertEqual(summary, "loaded")

    def test_codex_config_check_does_not_inherit_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(
                'model = "demo"\nmodel_provider = "openai"\n', encoding="utf-8"
            )
            completed = SimpleNamespace(returncode=0, stdout="")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "SOME_API_KEY": "secret-value",
                        "CODEX_ACCESS_TOKEN": "access-token",
                        "HTTPS_PROXY": "https://user:pass@example.test",
                    },
                    clear=False,
                ),
                mock.patch("ai_key_setup.shutil.which", return_value="codex"),
                mock.patch(
                    "ai_key_setup.subprocess.run", return_value=completed
                ) as runner,
            ):
                result = app.run_codex_strict_check(config)

        environment = runner.call_args.kwargs["env"]
        self.assertEqual(result.status, "passed")
        self.assertNotIn("SOME_API_KEY", environment)
        self.assertNotIn("CODEX_ACCESS_TOKEN", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertTrue(
            Path(runner.call_args.kwargs["cwd"]).name.startswith("ai-key-setup-check-")
        )


class FileSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def test_backup_atomic_write_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_text(
                'model = "before"\nmodel_provider = "openai"\n', encoding="utf-8"
            )
            backup = app.backup_file(config)
            self.assertIsNotNone(backup)

            app.atomic_write(config, 'model = "after"\nmodel_provider = "openai"\n')
            args = SimpleNamespace(config=str(config), backup=str(backup))
            with contextlib.redirect_stdout(io.StringIO()):
                result = app.rollback_codex(args)

            self.assertEqual(result, 0)
            self.assertIn('model = "before"', config.read_text(encoding="utf-8"))

    def test_relative_config_path_is_normalized_to_absolute_path(self) -> None:
        relative = Path("tmp") / "codex-home" / "config.toml"
        resolved = app.resolve_config_path(str(relative))
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, Path(os.path.abspath(relative)))

    @unittest.skipIf(os.name == "nt", "Symlink test runs on POSIX CI")
    def test_atomic_write_preserves_file_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "actual-config.toml"
            link = root / "config.toml"
            target.write_text("before", encoding="utf-8")
            link.symlink_to(target)

            app.atomic_write(link, "after")

            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "after")

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
                with (
                    contextlib.redirect_stdout(output),
                    mock.patch(
                        "ai_key_setup.run_codex_strict_check",
                        return_value=app.CheckResult(
                            "passed", "Codex 严格配置检查通过。"
                        ),
                    ),
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
                self.assertTrue(
                    list((config.parent / "backups" / "ai-key-setup").glob("*.bak"))
                )
            finally:
                os.environ.pop(source_name, None)
                os.environ.pop("HYALORIA_API_KEY", None)


class MetadataTests(unittest.TestCase):
    def test_package_version_matches_runtime_version(self) -> None:
        pyproject = Path(__file__).parents[1] / "pyproject.toml"
        metadata = app.tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], app.VERSION)


if __name__ == "__main__":
    unittest.main()
