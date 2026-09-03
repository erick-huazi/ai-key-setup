#!/usr/bin/env python3
"""Safely configure API keys and Codex custom model providers."""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None


VERSION = "2.0.0"
MANAGED_COMMENT = "# Managed by ai-key-setup. Secrets stay outside this file."
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk|key|token|ghp|github_pat)-?[A-Za-z0-9_-]{10,}\b"
)
BAD_ENV_KEY_RE = re.compile(
    r'''(?im)^\s*env_key\s*=\s*["'](?:sk|key|token|ghp|github_pat)[-_]'''
)
ACTIVE_SECRETS: list[str] = []


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    provider_name: str
    base_url: str
    model: str
    env_name: str

    @property
    def is_builtin_openai(self) -> bool:
        return self.provider_id == "openai"


def redact(value: object) -> str:
    text = str(value)
    for secret in ACTIVE_SECRETS:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return SECRET_VALUE_RE.sub("[REDACTED]", text)


def log(message: str) -> None:
    print(message)


def validate_env_name(name: str) -> None:
    if not ENV_NAME_RE.fullmatch(name):
        raise SetupError(
            "环境变量名无效。env-name 应类似 HYALORIA_API_KEY，不能填写 API Key。"
        )
    if re.match(r"(?i)^(?:sk|key|token|ghp|github_pat)[-_]", name):
        raise SetupError(
            "env-name 看起来像真实密钥。这里必须填写变量名，例如 HYALORIA_API_KEY。"
        )


def validate_provider(provider: ProviderConfig, allow_insecure_http: bool) -> None:
    validate_env_name(provider.env_name)
    if not PROVIDER_ID_RE.fullmatch(provider.provider_id):
        raise SetupError("provider-id 只能包含字母、数字、下划线和连字符。")
    if not provider.provider_name.strip():
        raise SetupError("提供商名称不能为空。")
    if not provider.model.strip():
        raise SetupError("模型名称不能为空。")
    if provider.provider_id in {"ollama", "lmstudio"}:
        raise SetupError("ollama 和 lmstudio 是 Codex 保留提供商，请使用 Codex 内置配置。")
    if provider.is_builtin_openai:
        return

    parsed = urllib.parse.urlparse(provider.base_url)
    if not parsed.scheme or not parsed.netloc:
        raise SetupError("base-url 必须是完整 URL。")
    if parsed.username or parsed.password:
        raise SetupError("base-url 不能包含用户名或密码。")
    if parsed.query or parsed.fragment:
        raise SetupError("base-url 不能包含查询参数或片段。")
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not is_local and not allow_insecure_http:
        raise SetupError(
            "远程 API 必须使用 HTTPS。确需 HTTP 时显式添加 --allow-insecure-http。"
        )


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_toml(text: str, source: str = "config.toml") -> dict[str, Any]:
    if tomllib is None:
        raise SetupError("需要 Python 3.11 或更高版本才能安全校验 TOML。")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(f"{source} 不是有效 TOML：{exc}") from exc


def remove_provider_section(text: str, provider_id: str) -> str:
    escaped = re.escape(provider_id)
    header = rf'model_providers\.(?:{escaped}|"{escaped}")'
    pattern = re.compile(
        rf"(?ms)^[ \t]*\[{header}\][ \t]*(?:\#[^\r\n]*)?\r?\n"
        rf".*?(?=^[ \t]*\[|\Z)"
    )
    return pattern.sub("", text)


def remove_root_assignment(preamble: str, key: str) -> str:
    pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[^\r\n]*(?:\r?\n|$)"
    )
    return pattern.sub("", preamble)


def update_codex_config(existing: str, provider: ProviderConfig) -> str:
    if existing.strip():
        parse_toml(existing, "现有 config.toml")
    newline = "\r\n" if "\r\n" in existing else "\n"
    text = remove_provider_section(existing, provider.provider_id)
    first_table = re.search(r"(?m)^[ \t]*\[", text)
    split_at = first_table.start() if first_table else len(text)
    preamble = text[:split_at]
    tables = text[split_at:]

    managed_block = re.compile(
        rf"(?m)^[ \t]*{re.escape(MANAGED_COMMENT)}[ \t]*\r?\n"
        rf"(?:[ \t]*model[ \t]*=[^\r\n]*(?:\r?\n|$))?"
        rf"(?:[ \t]*model_provider[ \t]*=[^\r\n]*(?:\r?\n|$))?"
        rf"(?:\r?\n)?"
    )
    preamble = managed_block.sub("", preamble)
    preamble = remove_root_assignment(preamble, "model")
    preamble = remove_root_assignment(preamble, "model_provider")

    lines = preamble.splitlines(keepends=True)
    insert_at = 0
    while insert_at < len(lines):
        stripped = lines[insert_at].strip()
        if stripped and not stripped.startswith("#"):
            break
        insert_at += 1

    managed = [
        MANAGED_COMMENT + newline,
        f"model = {toml_string(provider.model)}" + newline,
        f"model_provider = {toml_string(provider.provider_id)}" + newline,
        newline,
    ]
    lines[insert_at:insert_at] = managed
    preamble = "".join(lines).rstrip("\r\n")
    tables = tables.strip("\r\n")

    parts = [preamble]
    if tables:
        parts.append(tables)

    if not provider.is_builtin_openai:
        provider_block = newline.join(
            [
                f"[model_providers.{provider.provider_id}]",
                f"name = {toml_string(provider.provider_name)}",
                f"base_url = {toml_string(provider.base_url.rstrip('/'))}",
                f"env_key = {toml_string(provider.env_name)}",
                'wire_api = "responses"',
                "requires_openai_auth = false",
            ]
        )
        parts.append(provider_block)

    result = (newline + newline).join(part for part in parts if part) + newline
    if BAD_ENV_KEY_RE.search(result):
        raise SetupError(
            "配置中仍有 env_key 被填写为真实密钥。请先修复所有此类字段。"
        )

    parsed = parse_toml(result)
    if parsed.get("model") != provider.model:
        raise SetupError("写入后的 model 校验失败。")
    if parsed.get("model_provider") != provider.provider_id:
        raise SetupError("写入后的 model_provider 校验失败。")

    providers = parsed.get("model_providers", {})
    if not isinstance(providers, dict):
        raise SetupError("model_providers 必须是 TOML 表。")
    for configured_id, configured in providers.items():
        if not isinstance(configured, dict) or "env_key" not in configured:
            continue
        configured_env = configured["env_key"]
        if not isinstance(configured_env, str):
            raise SetupError(f"model_providers.{configured_id}.env_key 必须是字符串变量名。")
        try:
            validate_env_name(configured_env)
        except SetupError as exc:
            raise SetupError(
                f"model_providers.{configured_id}.env_key 必须是环境变量名，不能是真实 Key。"
            ) from exc

    if not provider.is_builtin_openai:
        configured = providers.get(provider.provider_id, {})
        if configured.get("env_key") != provider.env_name:
            raise SetupError("写入后的 env_key 校验失败。")
        if configured.get("wire_api") != "responses":
            raise SetupError("Codex 自定义提供商必须使用 Responses API。")
    return result


def get_windows_user_env(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value else None
    except (FileNotFoundError, OSError):
        return None


def posix_env_file() -> Path:
    return Path.home() / ".config" / "ai-key-setup" / "env"


def get_posix_persisted_env(name: str) -> str | None:
    path = posix_env_file()
    if not path.exists():
        return None
    pattern = re.compile(rf"^export\s+{re.escape(name)}=(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            try:
                values = shlex.split(match.group(1))
                return values[0] if values else ""
            except ValueError:
                return None
    return None


def get_persisted_env(name: str) -> str | None:
    return (
        os.environ.get(name)
        or get_windows_user_env(name)
        or (get_posix_persisted_env(name) if os.name != "nt" else None)
    )


def broadcast_windows_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            0x001A,
            0,
            "Environment",
            0x0002,
            5000,
            ctypes.byref(result),
        )
    except (AttributeError, OSError):
        pass


def backup_file(path: Path, backup_dir: Path | None = None) -> Path | None:
    if not path.exists():
        return None
    destination_dir = backup_dir or path.parent / "backups" / "ai-key-setup"
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = destination_dir / f"{path.name}.{timestamp}.bak"
    counter = 1
    while backup.exists():
        backup = destination_dir / f"{path.name}.{timestamp}.{counter}.bak"
        counter += 1
    shutil.copy2(path, backup)
    return backup


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def update_posix_env_store(name: str, value: str) -> tuple[Path, Path]:
    path = posix_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"(?m)^export\s+{re.escape(name)}=.*(?:\n|$)")
    cleaned = pattern.sub("", existing).rstrip()
    line = f"export {name}={shlex.quote(value)}"
    content = (cleaned + "\n" if cleaned else "") + line + "\n"
    atomic_write(path, content)
    os.chmod(path, 0o600)

    shell_name = Path(os.environ.get("SHELL", "sh")).name
    if shell_name == "zsh":
        rc_path = Path.home() / ".zshrc"
    elif shell_name == "bash":
        rc_path = Path.home() / ".bashrc"
    else:
        rc_path = Path.home() / ".profile"

    source_line = '[ -f "$HOME/.config/ai-key-setup/env" ] && . "$HOME/.config/ai-key-setup/env"'
    rc_text = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    if source_line not in rc_text:
        rc_backup = backup_file(rc_path) if rc_path.exists() else None
        separator = "" if not rc_text or rc_text.endswith("\n") else "\n"
        block = (
            "# >>> ai-key-setup >>>\n"
            + source_line
            + "\n# <<< ai-key-setup <<<\n"
        )
        atomic_write(rc_path, rc_text + separator + block)
        if rc_backup:
            log(f"Shell 配置备份：{rc_backup}")
    return path, rc_path


def persist_env(name: str, value: str, scope: str) -> None:
    validate_env_name(name)
    os.environ[name] = value
    if scope == "process":
        return
    if os.name == "nt":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        broadcast_windows_environment_change()
        log(f"已写入 Windows 用户环境变量：{name}")
    else:
        env_path, rc_path = update_posix_env_store(name, value)
        log(f"已写入受限权限密钥文件：{env_path}")
        log(f"Shell 将从以下文件加载：{rc_path}")


def acquire_key(
    env_name: str,
    key_from_env: str | None,
    replace_key: bool,
    no_key: bool,
    dry_run: bool,
) -> str | None:
    if no_key or dry_run:
        return None
    existing = get_persisted_env(env_name)
    if existing and not replace_key:
        ACTIVE_SECRETS.append(existing)
        log(f"检测到已有环境变量 {env_name}，保留现有值。")
        return existing
    if key_from_env:
        validate_env_name(key_from_env)
        value = os.environ.get(key_from_env, "").strip()
        if not value:
            raise SetupError(f"来源环境变量 {key_from_env} 为空。")
    else:
        value = getpass.getpass(f"请输入 {env_name} 的 API Key：").strip()
    if not value:
        raise SetupError("API Key 不能为空。")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise SetupError("API Key 不能包含换行符或空字符。")
    ACTIVE_SECRETS.append(value)
    return value


def resolve_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if not configured and os.name == "nt":
        configured = get_windows_user_env("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def resolve_config_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else resolve_codex_home() / "config.toml"


def run_codex_strict_check(
    env_name: str, key: str | None, config_path: Path
) -> tuple[bool, str]:
    executable = shutil.which("codex")
    if not executable:
        return False, "未找到 codex 命令，已跳过严格配置检查。"
    if config_path.name.lower() != "config.toml":
        return False, "自定义配置文件名不是 config.toml，已跳过 Codex 严格检查。"
    environment = os.environ.copy()
    if key:
        environment[env_name] = key
    environment["CODEX_HOME"] = str(config_path.parent)
    try:
        result = subprocess.run(
            [executable, "--strict-config", "features", "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Codex 严格检查未完成：{redact(exc)}"
    if result.returncode == 0:
        return True, "Codex 严格配置检查通过。"

    unsupported = (
        "--strict-config" in result.stdout
        and "not supported" in result.stdout
        and "codex features" in result.stdout
    )
    if unsupported:
        return run_codex_doctor_config_check(executable, environment)

    summary = redact(result.stdout.strip())[-500:]
    return False, f"Codex 严格配置检查失败：{summary}"


def parse_doctor_config_check(output: str) -> tuple[bool, str]:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        return False, redact(output.strip())[-500:]
    try:
        report = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return False, redact(output.strip())[-500:]
    check = report.get("checks", {}).get("config.load", {})
    status = check.get("status")
    summary = str(check.get("summary") or "Codex 未返回配置检查结果。")
    return status == "ok", redact(summary)


def run_codex_doctor_config_check(
    executable: str, environment: dict[str, str]
) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [executable, "--strict-config", "doctor", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Codex 严格检查未完成：{redact(exc)}"
    ok, summary = parse_doctor_config_check(result.stdout)
    if ok:
        return True, "Codex 严格配置检查通过。"
    return False, f"Codex 严格配置检查失败：{summary}"


def verify_models_endpoint(provider: ProviderConfig, key: str) -> None:
    if provider.is_builtin_openai:
        base_url = "https://api.openai.com/v1"
    else:
        base_url = provider.base_url.rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"ai-key-setup/{VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace")
        raise SetupError(f"API 验证失败（HTTP {exc.code}）：{redact(body)}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SetupError(f"API 验证失败：{redact(exc)}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SetupError("API 验证失败：/models 返回格式不是预期的 JSON 列表。")
    model_ids = {
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if provider.model not in model_ids:
        raise SetupError(
            f"API Key 有效，但 /models 未返回模型 {provider.model}。"
        )
    log(f"API 验证通过：找到模型 {provider.model}。")


def configure_codex(args: argparse.Namespace) -> int:
    provider = ProviderConfig(
        provider_id=args.provider_id,
        provider_name=args.provider_name,
        base_url=args.base_url,
        model=args.model,
        env_name=args.env_name,
    )
    validate_provider(provider, args.allow_insecure_http)
    config_path = resolve_config_path(args.config)
    existing = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
    updated = update_codex_config(existing, provider)
    changed = updated != existing

    if args.dry_run:
        log("模拟运行，不会读取 Key，也不会写入文件。")
        log(f"配置文件：{config_path}")
        log(f"模型：{provider.model}")
        log(f"提供商：{provider.provider_id}")
        log(f"环境变量名：{provider.env_name}")
        log(f"配置需要修改：{'是' if changed else '否'}")
        return 0

    key = acquire_key(
        provider.env_name,
        args.key_from_env,
        args.replace_key,
        args.no_key,
        args.dry_run,
    )
    if key:
        persist_env(provider.env_name, key, args.scope)

    backup = None
    if changed:
        backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None
        backup = backup_file(config_path, backup_dir)
        atomic_write(config_path, updated)
        log(f"Codex 配置已增量更新：{config_path}")
        if backup:
            log(f"原配置备份：{backup}")
    else:
        log("Codex 配置已经是目标状态，无需改写。")

    strict_ok, strict_message = run_codex_strict_check(
        provider.env_name, key, config_path
    )
    log(strict_message)
    if not args.skip_network_check and key:
        verify_models_endpoint(provider, key)
    elif not key:
        log("未提供 Key，已跳过网络验证。")

    if os.name == "nt" and args.scope == "user":
        log("请完全退出并重新打开 Codex/VS Code，使新的用户环境变量生效。")
    elif os.name != "nt" and args.scope == "user":
        log(f'请执行：source {shlex.quote(str(posix_env_file()))}')
    return 0 if strict_ok or "跳过" in strict_message else 1


def set_key(args: argparse.Namespace) -> int:
    validate_env_name(args.env_name)
    if args.dry_run:
        log(f"模拟运行：将设置 {args.env_name}，不会读取或写入 Key。")
        return 0
    key = acquire_key(
        args.env_name,
        args.key_from_env,
        args.replace_key,
        False,
        False,
    )
    assert key is not None
    persist_env(args.env_name, key, args.scope)
    log(f"环境变量 {args.env_name} 配置完成。")
    return 0


def load_provider_from_config(config_path: Path) -> ProviderConfig:
    if not config_path.exists():
        raise SetupError(f"Codex 配置不存在：{config_path}")
    text = config_path.read_text(encoding="utf-8-sig")
    data = parse_toml(text, str(config_path))
    provider_id = data.get("model_provider")
    model = data.get("model")
    if not isinstance(provider_id, str) or not isinstance(model, str):
        raise SetupError("配置中缺少顶层 model 或 model_provider。")
    if provider_id == "openai":
        return ProviderConfig("openai", "OpenAI", "https://api.openai.com/v1", model, "OPENAI_API_KEY")
    provider = data.get("model_providers", {}).get(provider_id)
    if not isinstance(provider, dict):
        raise SetupError(f"配置中找不到 model_providers.{provider_id}。")
    env_name = provider.get("env_key")
    base_url = provider.get("base_url")
    if not isinstance(env_name, str) or not isinstance(base_url, str):
        raise SetupError("当前提供商缺少 env_key 或 base_url。")
    validate_env_name(env_name)
    if provider.get("wire_api", "responses") != "responses":
        raise SetupError("Codex 自定义提供商的 wire_api 必须是 responses。")
    return ProviderConfig(
        provider_id,
        str(provider.get("name") or provider_id),
        base_url,
        model,
        env_name,
    )


def verify_codex(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    provider = load_provider_from_config(config_path)
    key = get_persisted_env(provider.env_name)
    log(f"配置文件：{config_path}")
    log(f"模型：{provider.model}")
    log(f"提供商：{provider.provider_id}")
    log(f"环境变量名：{provider.env_name}")
    log(f"环境变量存在：{'是' if key else '否'}")
    if not key:
        raise SetupError(f"缺少环境变量 {provider.env_name}。")
    ACTIVE_SECRETS.append(key)
    strict_ok, strict_message = run_codex_strict_check(
        provider.env_name, key, config_path
    )
    log(strict_message)
    if not args.skip_network_check:
        validate_provider(provider, args.allow_insecure_http)
        verify_models_endpoint(provider, key)
    return 0 if strict_ok or "跳过" in strict_message else 1


def rollback_codex(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    if args.backup:
        backup = Path(args.backup).expanduser()
    else:
        backup_dir = config_path.parent / "backups" / "ai-key-setup"
        candidates = sorted(
            backup_dir.glob(f"{config_path.name}.*.bak"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise SetupError(f"没有找到可恢复备份：{backup_dir}")
        backup = candidates[0]
    if not backup.exists():
        raise SetupError(f"备份不存在：{backup}")
    content = backup.read_text(encoding="utf-8-sig")
    parse_toml(content, str(backup))
    current_backup = backup_file(config_path)
    atomic_write(config_path, content)
    log(f"已恢复：{backup}")
    if current_backup:
        log(f"恢复前配置另存为：{current_backup}")
    return 0


def add_shared_key_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--key-from-env", help="从已有环境变量读取 Key，不通过命令行传值")
    parser.add_argument("--replace-key", action="store_true", help="替换已经保存的 Key")
    parser.add_argument(
        "--scope",
        choices=("user", "process"),
        default="user",
        help="user 持久保存；process 仅用于当前进程和测试",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不读取 Key、不写文件")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-key-setup",
        description="安全配置 AI API Key，并增量维护 Codex 自定义提供商。",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)

    codex = commands.add_parser("codex", help="配置 Codex；默认使用 Hyaloria/Kimi K3")
    codex.add_argument("--provider-id", default="hyaloria")
    codex.add_argument("--provider-name", default="Hyaloria")
    codex.add_argument("--base-url", default="https://hyaloria.com/v1")
    codex.add_argument("--model", default="kimi-k3")
    codex.add_argument("--env-name", default="HYALORIA_API_KEY")
    codex.add_argument("--config", help="覆盖 Codex config.toml 路径")
    codex.add_argument("--backup-dir", help="覆盖备份目录")
    codex.add_argument("--no-key", action="store_true", help="只写配置，不设置 Key")
    codex.add_argument("--skip-network-check", action="store_true")
    codex.add_argument("--allow-insecure-http", action="store_true")
    add_shared_key_options(codex)
    codex.set_defaults(func=configure_codex)

    key = commands.add_parser("set", help="只安全设置一个环境变量，适用于其他 AI CLI")
    key.add_argument("env_name")
    add_shared_key_options(key)
    key.set_defaults(func=set_key)

    verify = commands.add_parser("verify", help="检查现有 Codex 配置、环境变量和模型列表")
    verify.add_argument("--config", help="覆盖 Codex config.toml 路径")
    verify.add_argument("--skip-network-check", action="store_true")
    verify.add_argument("--allow-insecure-http", action="store_true")
    verify.set_defaults(func=verify_codex)

    rollback = commands.add_parser("rollback", help="恢复最近一次 Codex 配置备份")
    rollback.add_argument("--config", help="覆盖 Codex config.toml 路径")
    rollback.add_argument("--backup", help="指定备份文件；默认使用最近备份")
    rollback.set_defaults(func=rollback_codex)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        if sys.version_info < (3, 11):
            raise SetupError("需要 Python 3.11 或更高版本。")
        parser = build_parser()
        args = parser.parse_args(argv)
        return int(args.func(args))
    except SetupError as exc:
        print(f"错误：{redact(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:  # Defensive redaction for unexpected platform errors.
        print(f"未预期错误：{redact(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
