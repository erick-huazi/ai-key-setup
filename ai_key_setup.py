#!/usr/bin/env python3
"""Safely configure API keys and Codex custom model providers."""

from __future__ import annotations

import argparse
import codecs
import ctypes
import getpass
import http.client
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
from typing import Any, Literal

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None


VERSION = "3.0.0"
MANAGED_COMMENT = "# Managed by ai-key-setup. Secrets stay outside this file."
ROOT_MANAGED_BEGIN = "# >>> ai-key-setup managed defaults >>>"
ROOT_MANAGED_END = "# <<< ai-key-setup managed defaults <<<"
PROVIDER_MANAGED_BEGIN = "# >>> ai-key-setup managed provider >>>"
PROVIDER_MANAGED_END = "# <<< ai-key-setup managed provider <<<"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk|key|token|ghp|github_pat)-?[A-Za-z0-9_-]{10,}\b"
)
UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ACTIVE_SECRETS: list[str] = []
MAX_HTTP_RESPONSE_BYTES = 1_048_576
RESERVED_PROVIDER_IDS = {"openai", "ollama", "lmstudio"}
NON_CUSTOM_PROVIDER_IDS = RESERVED_PROVIDER_IDS | {"amazon-bedrock"}
PROTECTED_ENV_NAMES = {
    "PATH",
    "PATHEXT",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "SHELL",
    "CODEX_HOME",
    "PYTHONHOME",
    "PYTHONPATH",
}
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}
SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:api_key|key|token|secret|password|passwd|credentials?)(?:$|_)"
)
MANAGED_PROVIDER_KEYS = {
    "name",
    "base_url",
    "env_key",
    "wire_api",
    "requires_openai_auth",
    "experimental_bearer_token",
}


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


@dataclass(frozen=True)
class CheckResult:
    status: Literal["passed", "skipped", "failed"]
    message: str

    @property
    def successful(self) -> bool:
        return self.status in {"passed", "skipped"}


@dataclass(frozen=True)
class TableSection:
    path: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    existed: bool
    data: bytes
    mode: int | None


def redact(value: object) -> str:
    text = str(value)
    for secret in ACTIVE_SECRETS:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = SECRET_VALUE_RE.sub("[REDACTED]", text)
    return UNSAFE_CONTROL_RE.sub("?", text)


def log(message: str) -> None:
    print(redact(message))


def has_static_credential_header(configured: dict[str, Any]) -> bool:
    headers = configured.get("http_headers")
    return isinstance(headers, dict) and any(
        str(name).lower() in SENSITIVE_HEADER_NAMES and bool(value)
        for name, value in headers.items()
    )


def validate_env_name(name: str) -> None:
    if len(name) > 128:
        raise SetupError("环境变量名过长。")
    if not ENV_NAME_RE.fullmatch(name):
        raise SetupError(
            "环境变量名无效。env-name 应类似 HYALORIA_API_KEY，不能填写 API Key。"
        )
    if re.match(r"(?i)^(?:sk|key|token|ghp|github_pat)[-_]", name):
        raise SetupError(
            "env-name 看起来像真实密钥。这里必须填写变量名，例如 HYALORIA_API_KEY。"
        )
    if name.upper() in PROTECTED_ENV_NAMES:
        raise SetupError(f"拒绝覆盖系统关键环境变量：{name}")


def validate_provider(provider: ProviderConfig, allow_insecure_http: bool) -> None:
    validate_env_name(provider.env_name)
    if len(provider.provider_id) > 64:
        raise SetupError("provider-id 不能超过 64 个字符。")
    if not PROVIDER_ID_RE.fullmatch(provider.provider_id):
        raise SetupError("provider-id 只能包含字母、数字、下划线和连字符。")
    if not provider.provider_name.strip():
        raise SetupError("提供商名称不能为空。")
    if len(provider.provider_name) > 200:
        raise SetupError("提供商名称不能超过 200 个字符。")
    if not provider.model.strip():
        raise SetupError("模型名称不能为空。")
    if len(provider.model) > 256:
        raise SetupError("模型名称不能超过 256 个字符。")
    for label, value in (
        ("提供商名称", provider.provider_name),
        ("模型名称", provider.model),
    ):
        if any(ord(character) < 32 for character in value):
            raise SetupError(f"{label}不能包含控制字符。")
    if provider.provider_id in NON_CUSTOM_PROVIDER_IDS - {"openai"}:
        raise SetupError(
            f"{provider.provider_id} 是 Codex 内置提供商，请使用对应的内置配置。"
        )
    if provider.is_builtin_openai:
        return

    if provider.base_url != provider.base_url.strip():
        raise SetupError("base-url 首尾不能包含空白字符。")
    if any(
        character.isspace() or ord(character) < 32 for character in provider.base_url
    ):
        raise SetupError("base-url 不能包含空白或控制字符。")
    if len(provider.base_url) > 2048:
        raise SetupError("base-url 不能超过 2048 个字符。")
    try:
        parsed = urllib.parse.urlparse(provider.base_url)
    except ValueError as exc:
        raise SetupError("base-url 格式无效。") from exc
    if not parsed.scheme or not parsed.netloc:
        raise SetupError("base-url 必须是完整 URL。")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SetupError("base-url 只支持 HTTPS，或显式允许的 HTTP。")
    if parsed.username or parsed.password:
        raise SetupError("base-url 不能包含用户名或密码。")
    if not parsed.hostname:
        raise SetupError("base-url 缺少有效主机名。")
    try:
        parsed.port
    except ValueError as exc:
        raise SetupError("base-url 端口无效。") from exc
    if parsed.query or parsed.fragment:
        raise SetupError("base-url 不能包含查询参数或片段。")
    normalized_path = parsed.path.rstrip("/").lower()
    if normalized_path.endswith(("/models", "/responses")):
        raise SetupError(
            "base-url 应填写 API 根路径（通常以 /v1 结尾），不要包含接口名。"
        )
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


def detect_newline(text: str) -> str:
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match else "\n"


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _advance_multiline_state(
    line: str, state: Literal["basic", "literal"] | None
) -> Literal["basic", "literal"] | None:
    """Track TOML multiline strings so table-like text inside them is ignored."""
    index = 0
    while index < len(line):
        if state == "basic":
            if line.startswith('"""', index) and not _is_escaped(line, index):
                state = None
                index += 3
            else:
                index += 1
            continue
        if state == "literal":
            if line.startswith("'''", index):
                state = None
                index += 3
            else:
                index += 1
            continue

        character = line[index]
        if character == "#":
            break
        if line.startswith('"""', index):
            state = "basic"
            index += 3
            continue
        if line.startswith("'''", index):
            state = "literal"
            index += 3
            continue
        if character == '"':
            index += 1
            while index < len(line):
                if line[index] == "\\":
                    index += 2
                elif line[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            continue
        if character == "'":
            closing = line.find("'", index + 1)
            index = len(line) if closing < 0 else closing + 1
            continue
        index += 1
    return state


def _find_marker_path(
    value: object, marker: str, path: tuple[str, ...] = ()
) -> tuple[str, ...] | None:
    if isinstance(value, dict):
        if value.get(marker) is True:
            return path
        for key, child in value.items():
            found = _find_marker_path(child, marker, path + (str(key),))
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_marker_path(child, marker, path)
            if found is not None:
                return found
    return None


def _parse_table_header_path(line: str) -> tuple[str, ...] | None:
    marker = "__ai_key_setup_table_marker_4b48f5b7__"
    if not line.lstrip().startswith("["):
        return None
    try:
        parsed = tomllib.loads(line.rstrip("\r\n") + f"\n{marker} = true\n")
    except tomllib.TOMLDecodeError:
        return None
    return _find_marker_path(parsed, marker)


def scan_table_sections(text: str) -> list[TableSection]:
    lines = text.splitlines(keepends=True)
    headers: list[tuple[tuple[str, ...], int]] = []
    state: Literal["basic", "literal"] | None = None
    for index, line in enumerate(lines):
        if state is None:
            path = _parse_table_header_path(line)
            if path is not None:
                headers.append((path, index))
        state = _advance_multiline_state(line, state)
    return [
        TableSection(
            path=path,
            start=start,
            end=headers[position + 1][1] if position + 1 < len(headers) else len(lines),
        )
        for position, (path, start) in enumerate(headers)
    ]


def root_declares_provider_inline_or_dotted(text: str, provider_id: str) -> bool:
    root_key = r'(?:model_providers|"model_providers"|\'model_providers\')'
    provider_key = (
        rf'(?:{re.escape(provider_id)}|"{re.escape(provider_id)}"|'
        rf"'{re.escape(provider_id)}')"
    )
    pattern = re.compile(
        rf"^[ \t]*{root_key}[ \t]*(?:=|\.[ \t]*{provider_key}[ \t]*(?:=|\.))"
    )
    state: Literal["basic", "literal"] | None = None
    for line in text.splitlines(keepends=True):
        if state is None:
            if _parse_table_header_path(line) is not None:
                break
            if pattern.match(line):
                return True
        state = _advance_multiline_state(line, state)
    return False


def _remove_managed_blocks(lines: list[str], begin: str, end: str) -> list[str]:
    result: list[str] = []
    index = 0
    state: Literal["basic", "literal"] | None = None
    while index < len(lines):
        if state is not None or lines[index].strip() != begin:
            result.append(lines[index])
            state = _advance_multiline_state(lines[index], state)
            index += 1
            continue
        index += 1
        while index < len(lines) and lines[index].strip() != end:
            index += 1
        if index >= len(lines):
            raise SetupError(f"检测到不完整的托管标记：{begin}")
        index += 1
        if index < len(lines) and not lines[index].strip():
            index += 1
    return result


def _remove_exact_line_outside_multiline(lines: list[str], target: str) -> list[str]:
    result: list[str] = []
    state: Literal["basic", "literal"] | None = None
    for line in lines:
        if state is None and line.strip() == target:
            continue
        result.append(line)
        state = _advance_multiline_state(line, state)
    return result


def _assignment_key(line: str, keys: set[str]) -> str | None:
    for key in keys:
        pattern = rf"^[ \t]*(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')[ \t]*="
        if re.match(pattern, line):
            return key
    return None


def _assignment_end(lines: list[str], start: int) -> int:
    for end in range(start + 1, len(lines) + 1):
        candidate = "".join(lines[start:end])
        try:
            tomllib.loads(candidate)
            return end
        except tomllib.TOMLDecodeError:
            continue
    raise SetupError("无法安全识别现有 TOML 赋值范围，未修改配置。")


def _remove_assignments(lines: list[str], keys: set[str]) -> list[str]:
    result: list[str] = []
    index = 0
    state: Literal["basic", "literal"] | None = None
    while index < len(lines):
        if state is not None or _assignment_key(lines[index], keys) is None:
            result.append(lines[index])
            state = _advance_multiline_state(lines[index], state)
            index += 1
            continue
        index = _assignment_end(lines, index)
    return result


def _trim_blank_edges(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _ensure_line_ending(line: str, newline: str) -> str:
    return line if line.endswith(("\n", "\r")) else line + newline


def _mutate_root_defaults(
    lines: list[str], provider: ProviderConfig, newline: str
) -> list[str]:
    sections = scan_table_sections("".join(lines))
    preamble_end = sections[0].start if sections else len(lines)
    preamble = lines[:preamble_end]
    tables = lines[preamble_end:]

    preamble = _remove_managed_blocks(preamble, ROOT_MANAGED_BEGIN, ROOT_MANAGED_END)
    preamble = _remove_exact_line_outside_multiline(preamble, MANAGED_COMMENT)
    preamble = _remove_assignments(preamble, {"model", "model_provider"})

    insert_at = 0
    while insert_at < len(preamble):
        stripped = preamble[insert_at].strip()
        if stripped and not stripped.startswith("#"):
            break
        insert_at += 1
    managed = [
        ROOT_MANAGED_BEGIN + newline,
        f"model = {toml_string(provider.model)}" + newline,
        f"model_provider = {toml_string(provider.provider_id)}" + newline,
        ROOT_MANAGED_END + newline,
        newline,
    ]
    preamble[insert_at:insert_at] = managed
    return preamble + tables


def _remove_matching_sections(lines: list[str], predicate: Any) -> list[str]:
    sections = scan_table_sections("".join(lines))
    for section in reversed(sections):
        if predicate(section.path):
            del lines[section.start : section.end]
    return lines


def _provider_section_lines(
    provider: ProviderConfig,
    newline: str,
    body: list[str] | None = None,
    header_line: str | None = None,
) -> list[str]:
    header = header_line or f"[model_providers.{provider.provider_id}]"
    result = [
        _ensure_line_ending(header.rstrip("\r\n"), newline),
        PROVIDER_MANAGED_BEGIN + newline,
        f"name = {toml_string(provider.provider_name)}" + newline,
        f"base_url = {toml_string(provider.base_url.rstrip('/'))}" + newline,
        f"env_key = {toml_string(provider.env_name)}" + newline,
        'wire_api = "responses"' + newline,
        "requires_openai_auth = false" + newline,
        PROVIDER_MANAGED_END + newline,
    ]
    preserved = _trim_blank_edges(body or [])
    if preserved:
        result.append(newline)
        result.extend(_ensure_line_ending(line, newline) for line in preserved)
    result.append(newline)
    return result


def _mutate_provider_section(
    lines: list[str],
    provider: ProviderConfig,
    newline: str,
    replace_auth: bool,
) -> list[str]:
    target = ("model_providers", provider.provider_id)
    if provider.is_builtin_openai:
        return _remove_matching_sections(
            lines, lambda path: path[: len(target)] == target
        )

    if replace_auth:
        auth_prefix = target + ("auth",)
        lines = _remove_matching_sections(
            lines, lambda path: path[: len(auth_prefix)] == auth_prefix
        )

    sections = scan_table_sections("".join(lines))
    exact = next((section for section in sections if section.path == target), None)
    if exact is not None:
        body = lines[exact.start + 1 : exact.end]
        body = _remove_managed_blocks(
            body, PROVIDER_MANAGED_BEGIN, PROVIDER_MANAGED_END
        )
        keys = set(MANAGED_PROVIDER_KEYS)
        if replace_auth:
            keys.add("auth")
        body = _remove_assignments(body, keys)
        replacement = _provider_section_lines(
            provider, newline, body=body, header_line=lines[exact.start]
        )
        lines[exact.start : exact.end] = replacement
        return lines

    descendant = next(
        (section for section in sections if section.path[: len(target)] == target),
        None,
    )
    insert_at = descendant.start if descendant else len(lines)
    before = "".join(lines[:insert_at]).rstrip("\r\n")
    after = "".join(lines[insert_at:]).lstrip("\r\n")
    block = "".join(_provider_section_lines(provider, newline)).rstrip("\r\n")
    combined = before
    if combined:
        combined += newline + newline
    combined += block
    if after:
        combined += newline + newline + after
    else:
        combined += newline
    return combined.splitlines(keepends=True)


def update_codex_config(
    existing: str, provider: ProviderConfig, replace_auth: bool = False
) -> str:
    parsed_existing: dict[str, Any] = {}
    if existing.strip():
        parsed_existing = parse_toml(existing, "现有 config.toml")
    newline = detect_newline(existing)

    existing_providers = parsed_existing.get("model_providers", {})
    if not isinstance(existing_providers, dict):
        raise SetupError("现有 model_providers 必须是 TOML 表。")
    existing_target = existing_providers.get(provider.provider_id, {})
    if existing_target and not isinstance(existing_target, dict):
        raise SetupError(
            f"model_providers.{provider.provider_id} 必须是普通 TOML 表，不能是数组表。"
        )
    target_path = ("model_providers", provider.provider_id)
    existing_sections = scan_table_sections(existing)
    has_target_table = any(
        section.path[: len(target_path)] == target_path for section in existing_sections
    )
    if existing_target and (
        not has_target_table
        or root_declares_provider_inline_or_dotted(existing, provider.provider_id)
    ):
        raise SetupError(
            f"model_providers.{provider.provider_id} 使用内联表或点号键；"
            "为避免丢失自定义字段，本版不会自动改写这种格式。请先改成标准表段。"
        )
    if (
        not provider.is_builtin_openai
        and isinstance(existing_target, dict)
        and "auth" in existing_target
        and not replace_auth
    ):
        raise SetupError(
            f"model_providers.{provider.provider_id}.auth 使用命令认证，"
            "不能与 env_key 并用。确认替换时添加 --replace-auth。"
        )
    if isinstance(existing_target, dict) and has_static_credential_header(
        existing_target
    ):
        raise SetupError(
            f"model_providers.{provider.provider_id}.http_headers 含静态认证头。"
            "请先撤销并迁移到 env_key 或 env_http_headers。"
        )

    lines = existing.splitlines(keepends=True)
    lines = _mutate_root_defaults(lines, provider, newline)
    lines = _mutate_provider_section(lines, provider, newline, replace_auth)
    result = "".join(lines).rstrip("\r\n") + newline
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
            raise SetupError(
                f"model_providers.{configured_id}.env_key 必须是字符串变量名。"
            )
        try:
            validate_env_name(configured_env)
        except SetupError as exc:
            raise SetupError(
                f"model_providers.{configured_id}.env_key 必须是环境变量名，不能是真实 Key。"
            ) from exc

    if not provider.is_builtin_openai:
        configured = providers.get(provider.provider_id, {})
        if not isinstance(configured, dict):
            raise SetupError(f"model_providers.{provider.provider_id} 必须是 TOML 表。")
        if configured.get("env_key") != provider.env_name:
            raise SetupError("写入后的 env_key 校验失败。")
        if configured.get("wire_api") != "responses":
            raise SetupError("Codex 自定义提供商必须使用 Responses API。")
        if "auth" in configured or "experimental_bearer_token" in configured:
            raise SetupError("目标提供商仍含有与 env_key 冲突的认证配置。")
    elif "openai" in providers:
        raise SetupError(
            "openai 是 Codex 保留提供商，不能在 model_providers 中重定义。"
        )
    return result


def get_windows_user_env_state(name: str) -> tuple[bool, str | None, int | None]:
    if os.name != "nt":
        return False, None, None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, value_type = winreg.QueryValueEx(key, name)
            return True, str(value), value_type
    except FileNotFoundError:
        return False, None, None


def get_windows_user_env(name: str) -> str | None:
    present, value, _ = get_windows_user_env_state(name)
    return value if present and value else None


def set_windows_user_env(name: str, value: str, value_type: int | None = None) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, name, 0, value_type or winreg.REG_SZ, value)


def delete_windows_user_env(name: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def validate_file_symlink(path: Path, label: str) -> Path:
    if path.is_symlink():
        try:
            path.resolve(strict=True)
        except OSError as exc:
            raise SetupError(f"{label}符号链接无效：{path}") from exc
    return path


def posix_env_file() -> Path:
    path = Path.home() / ".config" / "ai-key-setup" / "env"
    return validate_file_symlink(path, "环境文件")


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


def get_saved_user_env(name: str) -> str | None:
    if os.name == "nt":
        return get_windows_user_env(name)
    return get_posix_persisted_env(name)


def get_persisted_env(name: str) -> str | None:
    return get_saved_user_env(name) or os.environ.get(name)


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


def snapshot_file(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(path, False, b"", None)
    if not path.is_file():
        raise SetupError(f"预期文件路径，但实际不是普通文件：{path}")
    return FileSnapshot(path, True, path.read_bytes(), path.stat().st_mode)


def atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    target = path.resolve(strict=True) if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    preserved_mode = target.stat().st_mode if target.exists() else mode
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if preserved_mode is not None:
            os.chmod(temporary, preserved_mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def restore_file_snapshot(snapshot: FileSnapshot) -> None:
    if snapshot.existed:
        atomic_write_bytes(snapshot.path, snapshot.data, snapshot.mode)
    elif snapshot.path.exists():
        snapshot.path.unlink()


def read_utf8_document(path: Path) -> tuple[str, bytes, bool]:
    if not path.exists():
        return "", b"", False
    if not path.is_file():
        raise SetupError(f"预期文件路径，但实际不是普通文件：{path}")
    data = path.read_bytes()
    had_bom = data.startswith(codecs.BOM_UTF8)
    try:
        return data.decode("utf-8-sig"), data, had_bom
    except UnicodeDecodeError as exc:
        raise SetupError(f"文件不是有效 UTF-8：{path}") from exc


def encode_utf8_document(text: str, had_bom: bool) -> bytes:
    data = text.encode("utf-8")
    return codecs.BOM_UTF8 + data if had_bom else data


def assert_file_unchanged(path: Path, existed: bool, expected: bytes) -> None:
    if path.exists() != existed:
        raise SetupError(f"配置在操作期间被其他程序改动，已停止：{path}")
    if existed and path.read_bytes() != expected:
        raise SetupError(f"配置在操作期间被其他程序改动，已停止：{path}")


def resolve_shell_rc_path() -> Path:
    shell_name = Path(os.environ.get("SHELL", "sh")).name
    if shell_name == "zsh":
        path = Path.home() / ".zshrc"
    elif shell_name == "bash":
        path = Path.home() / ".bashrc"
    elif shell_name in {"sh", "dash", "ksh"}:
        path = Path.home() / ".profile"
    else:
        raise SetupError(
            f"暂不支持自动写入 {shell_name}。请使用 --scope process，或手动设置环境变量。"
        )
    return validate_file_symlink(path, "Shell 配置")


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

    rc_path = resolve_shell_rc_path()

    source_line = (
        '[ -f "$HOME/.config/ai-key-setup/env" ] && . "$HOME/.config/ai-key-setup/env"'
    )
    rc_text = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    has_source_line = any(line.strip() == source_line for line in rc_text.splitlines())
    if not has_source_line:
        rc_backup = backup_file(rc_path) if rc_path.exists() else None
        separator = "" if not rc_text or rc_text.endswith("\n") else "\n"
        block = "# >>> ai-key-setup >>>\n" + source_line + "\n# <<< ai-key-setup <<<\n"
        atomic_write(rc_path, rc_text + separator + block)
        if rc_backup:
            log(f"Shell 配置备份：{rc_backup}")
    return path, rc_path


def remove_posix_env_store(name: str) -> Path:
    path = posix_env_file()
    if not path.exists():
        return path
    existing = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?m)^export\s+{re.escape(name)}=.*(?:\n|$)")
    content = pattern.sub("", existing).strip("\n")
    if content:
        atomic_write(path, content + "\n")
        os.chmod(path, 0o600)
    else:
        atomic_write(path, "")
        os.chmod(path, 0o600)
    return path


def _restore_process_env(name: str, present: bool, value: str | None) -> None:
    if present and value is not None:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def persist_env(name: str, value: str, scope: str) -> None:
    validate_env_name(name)
    if scope not in {"user", "process"}:
        raise SetupError("环境变量范围必须是 user 或 process。")
    process_present = name in os.environ
    process_value = os.environ.get(name)
    user_scope = scope == "user"
    windows_state = (
        get_windows_user_env_state(name) if os.name == "nt" and user_scope else None
    )
    env_snapshot = (
        snapshot_file(posix_env_file()) if os.name != "nt" and user_scope else None
    )
    rc_snapshot = (
        snapshot_file(resolve_shell_rc_path())
        if os.name != "nt" and user_scope
        else None
    )
    try:
        os.environ[name] = value
        if scope == "process":
            return
        if os.name == "nt":
            set_windows_user_env(name, value)
            broadcast_windows_environment_change()
            log(f"已写入 Windows 用户环境变量：{name}")
        else:
            env_path, rc_path = update_posix_env_store(name, value)
            log(f"已写入受限权限密钥文件：{env_path}")
            log(f"Shell 将从以下文件加载：{rc_path}")
    except BaseException as exc:
        _restore_process_env(name, process_present, process_value)
        try:
            if os.name == "nt" and windows_state is not None:
                present, old_value, value_type = windows_state
                if present and old_value is not None:
                    set_windows_user_env(name, old_value, value_type)
                else:
                    delete_windows_user_env(name)
                broadcast_windows_environment_change()
            elif env_snapshot is not None and rc_snapshot is not None:
                restore_file_snapshot(env_snapshot)
                restore_file_snapshot(rc_snapshot)
        except Exception as restore_exc:
            raise SetupError(
                f"环境变量写入失败，且自动恢复失败：{redact(restore_exc)}"
            ) from exc
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise SetupError(f"环境变量写入失败，已恢复原状态：{redact(exc)}") from exc


def unset_env(name: str, scope: str) -> None:
    validate_env_name(name)
    if scope not in {"user", "process"}:
        raise SetupError("环境变量范围必须是 user 或 process。")
    process_present = name in os.environ
    process_value = os.environ.get(name)
    user_scope = scope == "user"
    windows_state = (
        get_windows_user_env_state(name) if os.name == "nt" and user_scope else None
    )
    env_snapshot = (
        snapshot_file(posix_env_file()) if os.name != "nt" and user_scope else None
    )
    try:
        os.environ.pop(name, None)
        if scope == "user":
            if os.name == "nt":
                delete_windows_user_env(name)
                broadcast_windows_environment_change()
            else:
                remove_posix_env_store(name)
    except BaseException as exc:
        _restore_process_env(name, process_present, process_value)
        try:
            if os.name == "nt" and windows_state is not None:
                present, old_value, value_type = windows_state
                if present and old_value is not None:
                    set_windows_user_env(name, old_value, value_type)
            elif env_snapshot is not None:
                restore_file_snapshot(env_snapshot)
        except Exception as restore_exc:
            raise SetupError(
                f"环境变量删除失败，且自动恢复失败：{redact(restore_exc)}"
            ) from exc
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise SetupError(f"环境变量删除失败，已恢复原状态：{redact(exc)}") from exc


def acquire_key(
    env_name: str,
    key_from_env: str | None,
    replace_key: bool,
    no_key: bool,
    dry_run: bool,
    scope: str,
) -> str | None:
    if no_key or dry_run:
        return None
    if scope not in {"user", "process"}:
        raise SetupError("环境变量范围必须是 user 或 process。")
    if scope == "user":
        existing = get_saved_user_env(env_name) or os.environ.get(env_name)
    else:
        existing = os.environ.get(env_name) or get_saved_user_env(env_name)
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
        try:
            value = getpass.getpass(f"请输入 {env_name} 的 API Key：").strip()
        except EOFError as exc:
            raise SetupError(
                "当前环境无法安全读取隐藏输入，请先设置来源变量并使用 --key-from-env。"
            ) from exc
    if not value:
        raise SetupError("API Key 不能为空。")
    if len(value) > 16_384:
        raise SetupError("API Key 长度异常，拒绝保存。")
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
    path = Path(value).expanduser() if value else resolve_codex_home() / "config.toml"
    if not path.is_absolute():
        path = Path(os.path.abspath(path))
    return validate_file_symlink(path, "Codex 配置")


def sanitized_subprocess_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not SENSITIVE_ENV_NAME_RE.search(name)
        and name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }


def run_codex_strict_check(config_path: Path) -> CheckResult:
    executable = shutil.which("codex")
    if not executable:
        return CheckResult("skipped", "未找到 codex 命令，已跳过严格配置检查。")
    if config_path.name.lower() != "config.toml":
        return CheckResult(
            "skipped", "自定义配置文件名不是 config.toml，已跳过 Codex 严格检查。"
        )
    environment = sanitized_subprocess_environment()
    environment["CODEX_HOME"] = str(config_path.parent)
    try:
        with tempfile.TemporaryDirectory(prefix="ai-key-setup-check-") as check_dir:
            result = subprocess.run(
                [executable, "--strict-config", "features", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=environment,
                cwd=check_dir,
                check=False,
            )

            unsupported = (
                "--strict-config" in result.stdout
                and "not supported" in result.stdout
                and "codex features" in result.stdout
            )
            if unsupported:
                return run_codex_doctor_config_check(executable, environment, check_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("failed", f"Codex 严格检查未完成：{redact(exc)}")
    if result.returncode == 0:
        return CheckResult("passed", "Codex 严格配置检查通过。")

    summary = redact(result.stdout.strip())[-500:]
    return CheckResult("failed", f"Codex 严格配置检查失败：{summary}")


def parse_doctor_config_check(output: str) -> tuple[bool, str]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", output):
        try:
            report, _ = decoder.raw_decode(output[match.start() :])
        except json.JSONDecodeError:
            continue
        if not isinstance(report, dict):
            continue
        checks = report.get("checks")
        if not isinstance(checks, dict) or "config.load" not in checks:
            continue
        check = checks.get("config.load")
        if not isinstance(check, dict):
            return False, "Codex 未返回有效的配置检查结果。"
        status = check.get("status")
        summary = str(check.get("summary") or "Codex 未返回配置检查结果。")
        return status == "ok", redact(summary)
    return False, redact(output.strip())[-500:]


def run_codex_doctor_config_check(
    executable: str, environment: dict[str, str], cwd: str
) -> CheckResult:
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
            cwd=cwd,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("failed", f"Codex 严格检查未完成：{redact(exc)}")
    ok, summary = parse_doctor_config_check(result.stdout)
    if ok:
        return CheckResult("passed", "Codex 严格配置检查通过。")
    return CheckResult("failed", f"Codex 严格配置检查失败：{summary}")


def provider_base_url(provider: ProviderConfig) -> str:
    if provider.is_builtin_openai:
        return "https://api.openai.com/v1"
    return provider.base_url.rstrip("/")


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        try:
            origin_changed = _url_origin(req.full_url) != _url_origin(resolved)
        except ValueError as exc:
            raise SetupError("API 返回了无效的重定向地址，已停止请求。") from exc
        if origin_changed:
            raise SetupError("API 验证被重定向到其他域名，已阻止发送密钥。")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _read_limited(response: Any, limit: int = MAX_HTTP_RESPONSE_BYTES) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise SetupError(f"API 验证响应超过 {limit // 1024} KiB，已停止读取。")
    return data


def _http_error_detail(code: int) -> str:
    if code in {401, 403}:
        return "认证被拒绝，请检查 Key、权限和账户状态"
    if code in {404, 405}:
        return "接口不存在或不支持该请求方法"
    if code == 429:
        return "请求被限流或账户额度不足"
    return "服务端返回错误"


def request_json(
    method: str,
    url: str,
    key: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> object:
    data = None
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": f"ai-key-setup/{VERSION}",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        headers=headers,
        data=data,
        method=method,
    )
    opener = urllib.request.build_opener(SameOriginRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = _read_limited(response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2048).decode("utf-8", errors="replace").strip()
        except OSError:
            body = ""
        detail = _http_error_detail(exc.code)
        suffix = f"：{redact(body)[:500]}" if body else ""
        raise SetupError(f"API 验证失败（HTTP {exc.code}，{detail}）{suffix}") from exc
    except SetupError:
        raise
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        raise SetupError(f"API 验证失败：{redact(exc)}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupError("API 验证失败：响应不是有效 UTF-8 JSON。") from exc


def verify_models_endpoint(provider: ProviderConfig, key: str) -> None:
    payload = request_json("GET", f"{provider_base_url(provider)}/models", key)

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SetupError("API 验证失败：/models 返回格式不是预期的 JSON 列表。")
    model_ids = {
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if provider.model not in model_ids:
        raise SetupError(f"API Key 有效，但 /models 未返回模型 {provider.model}。")
    log(f"API 验证通过：找到模型 {provider.model}。")


def verify_responses_endpoint(provider: ProviderConfig, key: str) -> None:
    payload = request_json(
        "POST",
        f"{provider_base_url(provider)}/responses",
        key,
        {
            "model": provider.model,
            "input": "Reply with OK.",
            "max_output_tokens": 16,
            "stream": False,
        },
        timeout=60,
    )
    if not isinstance(payload, dict):
        raise SetupError("Responses API 验证失败：返回格式不是 JSON 对象。")
    if payload.get("error"):
        raise SetupError("Responses API 验证失败：服务返回了错误对象。")
    if (
        not isinstance(payload.get("id"), str)
        or not isinstance(payload.get("output"), list)
        or payload.get("object") not in {None, "response"}
    ):
        raise SetupError("Responses API 验证失败：返回内容不符合 Responses 格式。")
    log(f"Responses API 验证通过：模型 {provider.model} 可调用。")


def run_network_verification(mode: str, provider: ProviderConfig, key: str) -> None:
    if mode in {"models", "both"}:
        verify_models_endpoint(provider, key)
    if mode in {"responses", "both"}:
        verify_responses_endpoint(provider, key)


def selected_verify_mode(args: argparse.Namespace) -> str:
    if getattr(args, "skip_network_check", False):
        return "none"
    return str(getattr(args, "verify_mode", "models"))


def restore_config_after_failure(
    path: Path,
    original_existed: bool,
    original_bytes: bytes,
    candidate_bytes: bytes,
) -> None:
    if not path.exists() or path.read_bytes() != candidate_bytes:
        raise SetupError("配置写入后又被其他程序改动，未自动覆盖；请使用备份手动核对。")
    if original_existed:
        atomic_write_bytes(path, original_bytes)
    else:
        path.unlink()
    log("后续校验未完成，Codex 配置已自动恢复原状态。")


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
    original_existed = config_path.exists()
    existing, original_bytes, had_bom = read_utf8_document(config_path)
    updated = update_codex_config(existing, provider, args.replace_auth)
    candidate_bytes = encode_utf8_document(updated, had_bom)
    changed = candidate_bytes != original_bytes
    verify_mode = selected_verify_mode(args)

    if args.dry_run:
        log("模拟运行，不会读取 Key，也不会写入文件。")
        log(f"配置文件：{config_path}")
        log(f"模型：{provider.model}")
        log(f"提供商：{provider.provider_id}")
        log(f"环境变量名：{provider.env_name}")
        log(f"联网验证模式：{verify_mode}")
        log(f"配置需要修改：{'是' if changed else '否'}")
        return 0

    key = acquire_key(
        provider.env_name,
        args.key_from_env,
        args.replace_key,
        args.no_key,
        args.dry_run,
        args.scope,
    )
    if key and verify_mode != "none":
        if verify_mode in {"responses", "both"}:
            log("Responses 实测会产生一次极小的模型调用费用。")
        run_network_verification(verify_mode, provider, key)
    elif not key and verify_mode != "none":
        log("未提供 Key，已跳过联网验证。")

    backup: Path | None = None
    wrote_candidate = False
    try:
        if changed:
            assert_file_unchanged(config_path, original_existed, original_bytes)
            backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None
            backup = backup_file(config_path, backup_dir)
            atomic_write_bytes(config_path, candidate_bytes)
            wrote_candidate = True

        if args.skip_codex_check:
            strict_result = CheckResult("skipped", "已按参数跳过 Codex 严格配置检查。")
        else:
            strict_result = run_codex_strict_check(config_path)
        log(strict_result.message)
        if not strict_result.successful:
            raise SetupError(strict_result.message)

        if key:
            persist_env(provider.env_name, key, args.scope)
    except BaseException as exc:
        if wrote_candidate:
            try:
                restore_config_after_failure(
                    config_path,
                    original_existed,
                    original_bytes,
                    candidate_bytes,
                )
            except Exception as restore_exc:
                raise SetupError(
                    f"操作失败，且配置自动恢复失败：{redact(restore_exc)}"
                ) from exc
        raise

    if changed:
        log(f"Codex 配置已事务式更新：{config_path}")
        if backup:
            log(f"原配置备份：{backup}")
    else:
        log("Codex 配置已经是目标状态，无需改写。")

    if key and os.name == "nt" and args.scope == "user":
        log("请完全退出并重新打开 Codex/VS Code，使新的用户环境变量生效。")
    elif key and os.name != "nt" and args.scope == "user":
        log(f"请执行：source {shlex.quote(str(posix_env_file()))}")
    elif key and args.scope == "process":
        log("注意：process 范围仅供本次验证，后续启动的 Codex 不会继承该 Key。")
    return 0


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
        args.scope,
    )
    assert key is not None
    persist_env(args.env_name, key, args.scope)
    log(f"环境变量 {args.env_name} 配置完成。")
    return 0


def unset_key(args: argparse.Namespace) -> int:
    validate_env_name(args.env_name)
    existing = (
        get_saved_user_env(args.env_name)
        if args.scope == "user"
        else os.environ.get(args.env_name)
    )
    existed = bool(existing)
    if args.dry_run:
        log(f"模拟运行：将删除 {args.env_name}（范围：{args.scope}），不会修改系统。")
        return 0
    unset_env(args.env_name, args.scope)
    if existed:
        log(f"环境变量 {args.env_name} 已删除（范围：{args.scope}）。")
    else:
        log(f"环境变量 {args.env_name} 原本不存在，无需删除。")
    if os.name == "nt" and args.scope == "user":
        log("请完全退出并重新打开 Codex/VS Code，使删除结果生效。")
    return 0


def load_provider_from_config(config_path: Path) -> ProviderConfig:
    if not config_path.exists():
        raise SetupError(f"Codex 配置不存在：{config_path}")
    text, _, _ = read_utf8_document(config_path)
    data = parse_toml(text, str(config_path))
    provider_id = data.get("model_provider")
    model = data.get("model")
    if not isinstance(provider_id, str) or not isinstance(model, str):
        raise SetupError("配置中缺少顶层 model 或 model_provider。")
    if provider_id == "openai":
        return ProviderConfig(
            "openai", "OpenAI", "https://api.openai.com/v1", model, "OPENAI_API_KEY"
        )
    providers = data.get("model_providers", {})
    if not isinstance(providers, dict):
        raise SetupError("model_providers 必须是 TOML 表。")
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        raise SetupError(f"配置中找不到 model_providers.{provider_id}。")
    env_name = provider.get("env_key")
    base_url = provider.get("base_url")
    if not isinstance(env_name, str) or not isinstance(base_url, str):
        raise SetupError("当前提供商缺少 env_key 或 base_url。")
    validate_env_name(env_name)
    if provider.get("wire_api", "responses") != "responses":
        raise SetupError("Codex 自定义提供商的 wire_api 必须是 responses。")
    if "auth" in provider or "experimental_bearer_token" in provider:
        raise SetupError("当前提供商同时存在冲突的认证方式。")
    if has_static_credential_header(provider):
        raise SetupError("当前提供商的 http_headers 中存在静态认证头。")
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
    if args.skip_codex_check:
        strict_result = CheckResult("skipped", "已按参数跳过 Codex 严格配置检查。")
    else:
        strict_result = run_codex_strict_check(config_path)
    log(strict_result.message)
    if not strict_result.successful:
        raise SetupError(strict_result.message)
    verify_mode = selected_verify_mode(args)
    if verify_mode != "none":
        validate_provider(provider, args.allow_insecure_http)
        if verify_mode in {"responses", "both"}:
            log("Responses 实测会产生一次极小的模型调用费用。")
        run_network_verification(verify_mode, provider, key)
    return 0


def audit_codex(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    text, _, _ = read_utf8_document(config_path)
    if not text:
        raise SetupError(f"Codex 配置不存在或为空：{config_path}")
    data = parse_toml(text, str(config_path))
    findings: list[str] = []
    active_provider = data.get("model_provider")
    if not isinstance(data.get("model"), str) or not data.get("model"):
        findings.append("顶层 model 缺失或无效。")
    if not isinstance(active_provider, str) or not active_provider:
        findings.append("顶层 model_provider 缺失或无效。")
    providers = data.get("model_providers", {})
    if not isinstance(providers, dict):
        findings.append("model_providers 不是有效的 TOML 表。")
        providers = {}

    for reserved in RESERVED_PROVIDER_IDS:
        if reserved in providers:
            findings.append(f"保留提供商 {reserved} 被自定义重定义。")

    for provider_id, configured in providers.items():
        if not isinstance(configured, dict):
            findings.append(f"model_providers.{provider_id} 不是有效表。")
            continue
        if root_declares_provider_inline_or_dotted(text, str(provider_id)):
            findings.append(
                f"model_providers.{provider_id} 使用内联表或点号键，无法安全增量维护。"
            )
        env_name = configured.get("env_key")
        if "experimental_bearer_token" in configured:
            findings.append(f"model_providers.{provider_id} 含明文 bearer token 配置。")
        if "auth" in configured and env_name is not None:
            findings.append(f"model_providers.{provider_id} 同时配置 auth 与 env_key。")
        if configured.get("wire_api", "responses") != "responses":
            findings.append(f"model_providers.{provider_id}.wire_api 不是 responses。")
        base_url = configured.get("base_url")
        if provider_id not in NON_CUSTOM_PROVIDER_IDS:
            if not isinstance(base_url, str):
                findings.append(f"model_providers.{provider_id}.base_url 缺失或无效。")
            else:
                try:
                    validate_provider(
                        ProviderConfig(
                            str(provider_id),
                            str(configured.get("name") or provider_id),
                            base_url,
                            "audit-model",
                            "AI_KEY_SETUP_AUDIT_KEY",
                        ),
                        allow_insecure_http=False,
                    )
                except SetupError as exc:
                    findings.append(
                        f"model_providers.{provider_id} 不符合安全规则：{redact(exc)}"
                    )
        if has_static_credential_header(configured):
            findings.append(
                f"model_providers.{provider_id}.http_headers 可能含静态凭据。"
            )
        if env_name is not None:
            if not isinstance(env_name, str):
                findings.append(f"model_providers.{provider_id}.env_key 不是字符串。")
            else:
                try:
                    validate_env_name(env_name)
                except SetupError:
                    findings.append(
                        f"model_providers.{provider_id}.env_key 不是合法环境变量名。"
                    )
                else:
                    exists = bool(get_persisted_env(env_name))
                    label = "存在" if exists else "缺失"
                    log(f"凭据状态：{provider_id} -> {env_name}（{label}）")
                    if provider_id == active_provider and not exists:
                        findings.append(f"当前提供商缺少环境变量 {env_name}。")

    if (
        isinstance(active_provider, str)
        and active_provider not in NON_CUSTOM_PROVIDER_IDS
        and active_provider not in providers
    ):
        findings.append(f"当前提供商 {active_provider} 没有对应的配置表。")

    if SECRET_VALUE_RE.search(text):
        findings.append("config.toml 中检测到疑似明文密钥，请人工核对并轮换。")

    backup_dir = config_path.parent / "backups" / "ai-key-setup"
    backup_count = len(list(backup_dir.glob(f"{config_path.name}.*.bak")))
    log(f"配置文件：{config_path}")
    log(f"当前提供商：{active_provider or '未设置'}")
    log(f"可用备份：{backup_count} 个")
    if findings:
        for finding in findings:
            log(f"[风险] {finding}")
        log(f"审计完成：发现 {len(findings)} 项需要处理。")
        return 2
    log("审计完成：未发现凭据或配置结构风险。")
    return 0


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
    content, backup_bytes, _ = read_utf8_document(backup)
    parse_toml(content, str(backup))
    current_existed = config_path.exists()
    current_bytes = config_path.read_bytes() if current_existed else b""
    current_backup = backup_file(config_path)
    assert_file_unchanged(config_path, current_existed, current_bytes)
    atomic_write_bytes(config_path, backup_bytes)
    log(f"已恢复：{backup}")
    if current_backup:
        log(f"恢复前配置另存为：{current_backup}")
    return 0


def list_backups(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    backup_dir = config_path.parent / "backups" / "ai-key-setup"
    candidates = sorted(
        backup_dir.glob(f"{config_path.name}.*.bak"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        log(f"没有找到备份：{backup_dir}")
        return 0
    log(f"备份目录：{backup_dir}")
    for candidate in candidates[: args.limit]:
        timestamp = datetime.fromtimestamp(
            candidate.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
        log(f"{timestamp}  {candidate.name}")
    return 0


def add_shared_key_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--key-from-env", help="从已有环境变量读取 Key，不通过命令行传值"
    )
    parser.add_argument("--replace-key", action="store_true", help="替换已经保存的 Key")
    parser.add_argument(
        "--scope",
        choices=("user", "process"),
        default="user",
        help="user 持久保存；process 仅用于本次工具进程和测试",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只显示计划，不读取 Key、不写文件"
    )


def add_verification_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verify-mode",
        choices=("models", "responses", "both", "none"),
        default="models",
        help="models 免费查模型；responses 实测调用；both 两者；none 不联网",
    )
    parser.add_argument(
        "--skip-network-check",
        action="store_true",
        help="兼容旧版，等同于 --verify-mode none",
    )
    parser.add_argument(
        "--skip-codex-check",
        action="store_true",
        help="跳过本机 Codex 严格配置检查",
    )


def backup_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit 必须是整数") from exc
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit 必须在 1 到 100 之间")
    return parsed


def configure_windows_console_encoding() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


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
    codex.add_argument(
        "--replace-auth",
        action="store_true",
        help="移除目标提供商原有的命令认证，改用 env_key",
    )
    codex.add_argument("--allow-insecure-http", action="store_true")
    add_verification_options(codex)
    add_shared_key_options(codex)
    codex.set_defaults(func=configure_codex)

    key = commands.add_parser("set", help="只安全设置一个环境变量，适用于其他 AI CLI")
    key.add_argument("env_name")
    add_shared_key_options(key)
    key.set_defaults(func=set_key)

    unset = commands.add_parser("unset", help="安全删除一个环境变量")
    unset.add_argument("env_name")
    unset.add_argument(
        "--scope",
        choices=("user", "process"),
        default="user",
        help="user 删除持久值；process 仅清理本次工具进程",
    )
    unset.add_argument("--dry-run", action="store_true")
    unset.set_defaults(func=unset_key)

    verify = commands.add_parser(
        "verify", help="检查现有 Codex 配置、环境变量和模型列表"
    )
    verify.add_argument("--config", help="覆盖 Codex config.toml 路径")
    verify.add_argument("--allow-insecure-http", action="store_true")
    add_verification_options(verify)
    verify.set_defaults(func=verify_codex)

    audit = commands.add_parser("audit", help="审计明文凭据、认证冲突和缺失变量")
    audit.add_argument("--config", help="覆盖 Codex config.toml 路径")
    audit.set_defaults(func=audit_codex)

    rollback = commands.add_parser("rollback", help="恢复最近一次 Codex 配置备份")
    rollback.add_argument("--config", help="覆盖 Codex config.toml 路径")
    rollback.add_argument("--backup", help="指定备份文件；默认使用最近备份")
    rollback.set_defaults(func=rollback_codex)

    backups = commands.add_parser("backups", help="列出 Codex 配置备份")
    backups.add_argument("--config", help="覆盖 Codex config.toml 路径")
    backups.add_argument("--limit", type=backup_limit, default=10)
    backups.set_defaults(func=list_backups)
    return parser


def main(argv: list[str] | None = None) -> int:
    ACTIVE_SECRETS.clear()
    configure_windows_console_encoding()
    try:
        if sys.version_info < (3, 11):
            raise SetupError("需要 Python 3.11 或更高版本。")
        parser = build_parser()
        actual_argv = list(sys.argv[1:] if argv is None else argv)
        commands = {"codex", "set", "unset", "verify", "audit", "rollback", "backups"}
        global_options = {"-h", "--help", "--version"}
        if not actual_argv:
            actual_argv = ["codex"]
        elif actual_argv[0] not in commands | global_options and actual_argv[
            0
        ].startswith("-"):
            actual_argv.insert(0, "codex")
        args = parser.parse_args(actual_argv)
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
    finally:
        ACTIVE_SECRETS.clear()


if __name__ == "__main__":
    raise SystemExit(main())
