from __future__ import annotations

import codecs
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_key_setup as app


ORIGINAL = b'model = "before"\nmodel_provider = "openai"\nnotify = ["done"]\n'
SOURCE_ENV = "AI_KEY_SETUP_TRANSACTION_SOURCE"
SECRET = "transaction-test-secret-value"


class ConfigureTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()
        os.environ[SOURCE_ENV] = SECRET
        os.environ.pop("HYALORIA_API_KEY", None)

    def tearDown(self) -> None:
        os.environ.pop(SOURCE_ENV, None)
        os.environ.pop("HYALORIA_API_KEY", None)
        app.ACTIVE_SECRETS.clear()

    def command(self, config: Path, *extra: str) -> list[str]:
        return [
            "codex",
            "--config",
            str(config),
            "--key-from-env",
            SOURCE_ENV,
            "--scope",
            "process",
            *extra,
        ]

    def test_network_failure_happens_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            with (
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_network_verification",
                    side_effect=app.SetupError("network failed"),
                ),
                mock.patch("ai_key_setup.persist_env") as persist,
            ):
                result = app.main(self.command(config))

            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), ORIGINAL)
            self.assertFalse(persist.called)
            self.assertFalse((config.parent / "backups").exists())

    def test_strict_failure_restores_original_and_never_persists_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    return_value=app.CheckResult("failed", "invalid config"),
                ),
                mock.patch("ai_key_setup.persist_env") as persist,
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), ORIGINAL)
            self.assertFalse(persist.called)
            self.assertTrue(
                list((config.parent / "backups" / "ai-key-setup").glob("*.bak"))
            )

    def test_strict_failure_removes_new_candidate_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    return_value=app.CheckResult("failed", "invalid config"),
                ),
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 1)
            self.assertFalse(config.exists())

    def test_environment_failure_restores_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    return_value=app.CheckResult("passed", "ok"),
                ),
                mock.patch(
                    "ai_key_setup.persist_env",
                    side_effect=app.SetupError("registry failed"),
                ),
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), ORIGINAL)

    def test_keyboard_interrupt_restores_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check", side_effect=KeyboardInterrupt
                ),
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 130)
            self.assertEqual(config.read_bytes(), ORIGINAL)

    def test_success_preserves_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(codecs.BOM_UTF8 + ORIGINAL)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    return_value=app.CheckResult("passed", "ok"),
                ),
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 0)
            self.assertTrue(config.read_bytes().startswith(codecs.BOM_UTF8))
            self.assertNotIn(SECRET.encode(), config.read_bytes())

    def test_concurrent_edit_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            concurrent = b'model = "concurrent"\nmodel_provider = "openai"\n'

            def edit_during_network(*_args: object) -> None:
                config.write_bytes(concurrent)

            with (
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_network_verification",
                    side_effect=edit_during_network,
                ),
                mock.patch("ai_key_setup.persist_env") as persist,
            ):
                result = app.main(self.command(config))

            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), concurrent)
            self.assertFalse(persist.called)

    def test_edit_after_candidate_write_is_not_overwritten_by_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            concurrent = b'model = "late-edit"\nmodel_provider = "openai"\n'

            def edit_during_strict(*_args: object) -> app.CheckResult:
                config.write_bytes(concurrent)
                return app.CheckResult("failed", "invalid config")

            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch(
                    "ai_key_setup.run_codex_strict_check",
                    side_effect=edit_during_strict,
                ),
            ):
                result = app.main(self.command(config, "--verify-mode", "none"))

            self.assertEqual(result, 1)
            self.assertEqual(config.read_bytes(), concurrent)

    def test_options_without_subcommand_default_to_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_bytes(ORIGINAL)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = app.main(["--config", str(config), "--dry-run"])

            self.assertEqual(result, 0)
            self.assertIn("模拟运行", output.getvalue())
            self.assertEqual(config.read_bytes(), ORIGINAL)


class MaintenanceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        app.ACTIVE_SECRETS.clear()

    def tearDown(self) -> None:
        os.environ.pop("AI_KEY_SETUP_UNSET_TEST", None)
        app.ACTIVE_SECRETS.clear()

    def test_unset_process_scope(self) -> None:
        os.environ["AI_KEY_SETUP_UNSET_TEST"] = "value"
        with contextlib.redirect_stdout(io.StringIO()):
            result = app.main(
                ["unset", "AI_KEY_SETUP_UNSET_TEST", "--scope", "process"]
            )
        self.assertEqual(result, 0)
        self.assertNotIn("AI_KEY_SETUP_UNSET_TEST", os.environ)

    def test_unset_process_scope_ignores_saved_user_value(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "ai_key_setup.get_saved_user_env", return_value="saved-user-value"
            ),
            contextlib.redirect_stdout(output),
        ):
            result = app.main(
                ["unset", "AI_KEY_SETUP_UNSET_TEST", "--scope", "process"]
            )
        self.assertEqual(result, 0)
        self.assertIn("原本不存在", output.getvalue())

    def test_user_scope_prefers_saved_value_over_stale_process_value(self) -> None:
        name = "AI_KEY_SETUP_PRECEDENCE_TEST"
        with (
            mock.patch.dict(os.environ, {name: "stale-process-value"}),
            mock.patch(
                "ai_key_setup.get_saved_user_env", return_value="new-saved-value"
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            value = app.acquire_key(name, None, False, False, False, "user")
        self.assertEqual(value, "new-saved-value")

    def test_audit_reports_bad_env_key_without_printing_it(self) -> None:
        secret = "sk" + "-audit-secret-value-1234567890"
        config_text = f'''model = "demo"
model_provider = "gateway"

[model_providers.gateway]
name = "Gateway"
base_url = "https://example.com/v1"
env_key = "{secret}"
wire_api = "responses"
'''
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(config_text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = app.main(["audit", "--config", str(config)])

        self.assertEqual(result, 2)
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("不是合法环境变量名", output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows registry transaction test")
    def test_windows_persistence_failure_restores_process_state(self) -> None:
        name = "AI_KEY_SETUP_WINDOWS_ROLLBACK_TEST"
        os.environ.pop(name, None)
        with (
            mock.patch(
                "ai_key_setup.get_windows_user_env_state",
                return_value=(True, "old-persisted-value", 1),
            ),
            mock.patch(
                "ai_key_setup.set_windows_user_env",
                side_effect=[OSError("write failed"), None],
            ) as setter,
            mock.patch("ai_key_setup.broadcast_windows_environment_change"),
        ):
            with self.assertRaises(app.SetupError):
                app.persist_env(name, "new-value", "user")
        self.assertNotIn(name, os.environ)
        self.assertEqual(setter.call_count, 2)

    @unittest.skipIf(os.name == "nt", "POSIX environment store test")
    def test_posix_user_store_has_restricted_permissions_and_can_unset(self) -> None:
        name = "AI_KEY_SETUP_POSIX_TEST"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with (
                mock.patch("ai_key_setup.Path.home", return_value=home),
                mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}, clear=False),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                app.persist_env(name, "local-secret", "user")
                env_file = home / ".config" / "ai-key-setup" / "env"
                self.assertTrue(env_file.exists())
                self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
                self.assertIn(f"export {name}=", env_file.read_text(encoding="utf-8"))
                self.assertIn(
                    "ai-key-setup",
                    (home / ".bashrc").read_text(encoding="utf-8"),
                )
                app.unset_env(name, "user")
                self.assertEqual(env_file.read_text(encoding="utf-8"), "")
        os.environ.pop(name, None)


if __name__ == "__main__":
    unittest.main()
