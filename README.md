# ai-key-setup

安全保存 AI API Key，并以事务方式维护 Codex 自定义模型提供商。

[![Tests](https://github.com/erick-huazi/ai-key-setup/actions/workflows/test.yml/badge.svg)](https://github.com/erick-huazi/ai-key-setup/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## v3 核心能力

- Key 只进入环境存储，Codex 配置只记录环境变量名。
- 仅更新 `model`、`model_provider` 和目标提供商的托管字段。
- 保留插件、MCP、通知、项目授权，以及目标提供商的重试、请求头等扩展项。
- 联网预检、Codex 严格检查、持久化任一步失败，自动恢复原配置。
- 写入前检查并发修改，避免覆盖刚被用户或其他程序改过的配置。
- 阻止携带 Key 的跨域重定向，并限制验证接口响应体大小。
- 支持 `/models` 免费检查和 `/responses` 真实兼容性检查。
- 支持审计、删除环境变量、列出备份与精确回滚。
- Windows、macOS、Linux 共用同一套 Python 核心逻辑。

实现依据是 OpenAI 官方的 [Codex 配置参考](https://developers.openai.com/codex/config-reference/) 和 [Codex 认证说明](https://developers.openai.com/codex/auth/)。自定义提供商必须写在用户级配置中，`env_key` 的值必须是环境变量名称；`wire_api` 当前仅支持 `responses`。

## 安装

需要 Python 3.11 或更高版本。

### 克隆后运行

Windows PowerShell：

```powershell
git clone https://github.com/erick-huazi/ai-key-setup.git
cd ai-key-setup
Set-ExecutionPolicy -Scope Process Bypass
.\ai-key-setup.ps1 --version
```

macOS / Linux：

```bash
git clone https://github.com/erick-huazi/ai-key-setup.git
cd ai-key-setup
chmod +x ai-key-setup.sh
./ai-key-setup.sh --version
```

macOS / Linux 的用户范围会写入受限权限的 Shell 环境文件，适用于随后从该终端启动的 Codex CLI。由桌面图标直接启动的 GUI 应用不一定继承 Shell 环境，请按对应系统的应用环境配置处理。

### 安装为系统命令

```bash
python -m pip install "git+https://github.com/erick-huazi/ai-key-setup.git"
ai-key-setup --version
```

也可以使用 `pipx install git+https://github.com/erick-huazi/ai-key-setup.git` 创建隔离安装。

## 快速开始

默认预设是 Hyaloria、`kimi-k3` 和 `HYALORIA_API_KEY`。

```powershell
# 只预览，不读取 Key、不联网、不写文件
.\ai-key-setup.ps1 --dry-run

# 交互式隐藏输入 Key，并完成配置
.\ai-key-setup.ps1

# 综合检查当前配置
.\ai-key-setup.ps1 verify

# 检查是否存在明文凭据、认证冲突或缺失变量
.\ai-key-setup.ps1 audit
```

配置完成后，完全退出并重新打开 Codex 或 VS Code，使新的用户环境变量进入应用进程。

## 默认写入内容

```toml
model = "kimi-k3"
model_provider = "hyaloria"

[model_providers.hyaloria]
name = "Hyaloria"
base_url = "https://hyaloria.com/v1"
env_key = "HYALORIA_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

配置中不会出现真实 Key。`env_key` 必须类似 `HYALORIA_API_KEY`，不能填写 `sk-...`。

## 配置其他提供商

提供商必须兼容 OpenAI Responses API：

```powershell
.\ai-key-setup.ps1 codex `
  --provider-id my-gateway `
  --provider-name "My Gateway" `
  --base-url "https://api.example.com/v1" `
  --model "your-model-id" `
  --env-name "MY_GATEWAY_API_KEY"
```

`base-url` 应填写 API 根路径，通常以 `/v1` 结尾，不要包含 `/models` 或 `/responses`。远程地址默认必须使用 HTTPS；本机 `localhost` 可以使用 HTTP。

如果目标提供商已配置 Codex 的命令认证 `[model_providers.<id>.auth]`，工具默认停止，防止破坏已有认证。确认改成环境变量认证时添加：

```powershell
.\ai-key-setup.ps1 codex --replace-auth
```

若目标提供商的 `http_headers` 中存在静态 `Authorization`、`X-API-Key` 等认证头，工具会停止并要求先撤销、轮换并迁移到 `env_key` 或 `env_http_headers`，不会把明文凭据原样带入新配置。

## 验证模式

| 模式 | 行为 | 是否产生模型费用 |
|---|---|---|
| `models` | 请求 `/models` 并精确检查模型 ID，默认模式 | 通常不会 |
| `responses` | 向 `/responses` 发起一次最小真实调用 | 会产生极小费用 |
| `both` | 依次执行以上两项 | 会产生极小费用 |
| `none` | 完全不访问提供商 | 不会 |

示例：

```powershell
.\ai-key-setup.ps1 verify --verify-mode responses
.\ai-key-setup.ps1 codex --verify-mode both
.\ai-key-setup.ps1 codex --verify-mode none
```

旧参数 `--skip-network-check` 仍可使用，等同于 `--verify-mode none`。

## 命令

| 命令 | 用途 |
|---|---|
| `codex` | 配置 Codex；省略子命令时默认执行 |
| `set ENV_NAME` | 只安全保存一个环境变量 |
| `unset ENV_NAME` | 删除一个环境变量 |
| `verify` | 检查配置、环境变量、Codex 和提供商接口 |
| `audit` | 审计明文凭据、认证冲突和缺失变量 |
| `backups` | 列出最近的配置备份 |
| `rollback` | 恢复最近或指定的配置备份 |

常用选项：

```powershell
.\ai-key-setup.ps1 codex --dry-run
.\ai-key-setup.ps1 codex --no-key --verify-mode none
.\ai-key-setup.ps1 codex --replace-key
.\ai-key-setup.ps1 codex --key-from-env SOURCE_VARIABLE
.\ai-key-setup.ps1 codex --skip-codex-check
.\ai-key-setup.ps1 backups --limit 20
```

工具不接受把真实 Key 直接放进命令参数。自动化时先把 Key 放进已有环境变量，再使用 `--key-from-env`。

## 事务、备份与回滚

配置默认位于：

- Windows：`%USERPROFILE%\.codex\config.toml`
- macOS / Linux：`~/.codex/config.toml`

每次实际改写前，原文件会备份到 `~/.codex/backups/ai-key-setup/`。新候选配置写入后必须通过本机 Codex 严格检查，环境变量才会持久化；后续失败时恢复修改前的原始字节，包括 UTF-8 BOM。

```powershell
# 查看备份
.\ai-key-setup.ps1 backups

# 恢复最近备份
.\ai-key-setup.ps1 rollback

# 恢复指定备份
.\ai-key-setup.ps1 rollback --backup "C:\path\to\config.toml.TIMESTAMP.bak"
```

回滚只恢复 `config.toml`，不会改动环境变量。

## 密钥存储边界

- Windows 用户范围：当前用户注册表的 `Environment` 项。
- macOS / Linux 用户范围：`~/.config/ai-key-setup/env`，权限为 `600`，Bash、Zsh、sh、Dash、Ksh 的配置只加入加载入口；其他 Shell 会明确停止并提示手动设置。
- `--scope process`：仅供本次工具进程验证；进程退出后，后来启动的 Codex 不会继承该 Key。
- 备份是原配置的精确副本。如果旧配置本来含有明文 Key，备份也会保留它；修复后应轮换 Key，并妥善清理旧备份。

环境变量不是硬件保险箱，同一系统用户下的程序通常可以读取。若需要 OpenAI 官方登录凭据，Codex 支持系统凭据存储；本工具主要解决第三方 Responses 兼容提供商的 `env_key` 配置。

## 故障排除

### `Missing environment variable: sk-...`

这表示 `env_key` 被误填成了真实 Key。先轮换已经暴露的 Key，再运行：

```powershell
.\ai-key-setup.ps1 audit
.\ai-key-setup.ps1 codex --replace-key
```

### Key 有效但 `/models` 不可用

有些中转站只实现 `/responses`。可以改用：

```powershell
.\ai-key-setup.ps1 verify --verify-mode responses
```

该模式会产生一次极小的真实模型调用费用。

### PowerShell 禁止脚本运行

仅对当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 开发与测试

项目运行时只使用 Python 标准库：

```powershell
python -m py_compile ai_key_setup.py
python -m unittest discover -s tests -v
python -m pip install --no-deps -e .
ai-key-setup --version
```

## License

[MIT License](LICENSE)
