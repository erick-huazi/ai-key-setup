# ai-key-setup

安全配置 AI API Key，并以增量方式维护 Codex 自定义模型提供商。

[![Tests](https://github.com/erick-huazi/ai-key-setup/actions/workflows/test.yml/badge.svg)](https://github.com/erick-huazi/ai-key-setup/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## v2 解决的问题

- Windows 可直接在 PowerShell 运行，不再依赖 Git Bash 或 WSL。
- API Key 只保存到用户环境，不写入 `~/.codex/config.toml`。
- 只更新 Codex 的 `model`、`model_provider` 和目标提供商段。
- 保留原有插件、MCP、通知、项目授权和其他配置。
- 修改前自动备份，支持一键回滚。
- 支持模拟运行、TOML 校验、Codex 严格检查和模型接口验证。
- 默认预设为 Hyaloria `kimi-k3`，也支持任意兼容 Codex Responses API 的中转站。

## Windows 快速开始

需要 Git 和 Python 3.11 或更高版本。从 PowerShell 执行：

```powershell
git clone https://github.com/erick-huazi/ai-key-setup.git
cd ai-key-setup
Set-ExecutionPolicy -Scope Process Bypass

# 先预览，不读取 Key，也不修改文件
.\ai-key-setup.ps1 codex --dry-run

# 配置 Hyaloria + kimi-k3，Key 输入时不可见
.\ai-key-setup.ps1 codex

# 检查配置、环境变量和模型列表
.\ai-key-setup.ps1 verify
```

配置完成后，请完全退出并重新打开 Codex 或 VS Code，让新的用户环境变量进入应用进程。

## macOS / Linux 快速开始

```bash
git clone https://github.com/erick-huazi/ai-key-setup.git
cd ai-key-setup
chmod +x ai-key-setup.sh

./ai-key-setup.sh codex --dry-run
./ai-key-setup.sh codex
./ai-key-setup.sh verify
```

脚本将密钥保存在 `~/.config/ai-key-setup/env`，权限设为 `600`，并在当前 Shell 配置中加入加载入口。配置完成后按终端提示重新加载。

## 默认配置

不带额外参数执行 `codex` 时使用：

| 项目 | 默认值 |
|---|---|
| 提供商 ID | `hyaloria` |
| API Base URL | `https://hyaloria.com/v1` |
| 模型 | `kimi-k3` |
| Key 环境变量 | `HYALORIA_API_KEY` |
| Codex 接口 | `responses` |

写入 Codex 的只是环境变量名称：

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

`env_key` 必须是变量名，例如 `HYALORIA_API_KEY`，绝不能填写以 `sk-` 开头的真实 Key。

## 配置其他提供商

自定义中转站必须支持 Codex 使用的 OpenAI-compatible Responses API，通常是 `/v1/responses`，并建议提供 `/v1/models`：

```powershell
.\ai-key-setup.ps1 codex `
  --provider-id my-gateway `
  --provider-name "My Gateway" `
  --base-url "https://api.example.com/v1" `
  --model "your-model-id" `
  --env-name "MY_GATEWAY_API_KEY"
```

使用 Codex 内置 OpenAI 提供商时，不会创建保留的 `[model_providers.openai]` 段：

```powershell
.\ai-key-setup.ps1 codex --provider-id openai --model "your-openai-model" --env-name OPENAI_API_KEY
```

## 只设置环境变量

为 Claude Code、Hermes、Aider 或其他工具保存 Key，但不修改它们的配置文件：

```powershell
.\ai-key-setup.ps1 set ANTHROPIC_API_KEY
.\ai-key-setup.ps1 set OPENAI_API_KEY
```

已有环境变量需要替换时添加 `--replace-key`。自动化环境可使用 `--key-from-env SOURCE_VARIABLE`，工具会从另一个已有环境变量读取，不接受把真实 Key 直接放进命令参数。

## 常用命令

| 命令 | 用途 |
|---|---|
| `codex --dry-run` | 只显示计划，不读取 Key、不写文件 |
| `codex --no-key` | 只更新 Codex 配置，不设置 Key |
| `codex --replace-key` | 替换已保存的 Key |
| `codex --skip-network-check` | 不请求提供商的 `/models` 接口 |
| `codex --scope process` | Key 仅在当前进程有效，适合测试 |
| `verify` | 校验当前 Codex 配置、环境变量和模型接口 |
| `rollback` | 恢复最近一次 Codex 配置备份 |
| `--version` | 显示版本 |

完整参数：

```powershell
.\ai-key-setup.ps1 codex --help
.\ai-key-setup.ps1 set --help
.\ai-key-setup.ps1 verify --help
```

## 备份与回滚

Codex 配置默认位于：

- Windows：`%USERPROFILE%\.codex\config.toml`
- macOS / Linux：`~/.codex/config.toml`

原文件默认备份到 `~/.codex/backups/ai-key-setup/`。恢复最近备份：

```powershell
.\ai-key-setup.ps1 rollback
```

恢复指定备份：

```powershell
.\ai-key-setup.ps1 rollback --backup "C:\path\to\config.toml.TIMESTAMP.bak"
```

回滚只恢复 `config.toml`，不会删除或还原已经保存的用户环境变量。

## 安全说明

- 默认隐藏输入，不打印 API Key。
- 输出和异常会主动遮盖疑似密钥。
- `.env` 不会被程序自动读取；仓库中的 `.env.template` 仅是变量名参考。
- Windows 用户环境变量保存在当前用户的注册表环境项中，当前 Windows 用户可以读取它。
- 如果 Key 曾出现在聊天、截图、配置或 Git 历史中，请立即在服务商后台撤销并重新生成。
- 不要把 Key 作为 `--env-name` 的值，也不要把真实 Key 写进命令行或提交到 Git。

## 故障排除

### Missing environment variable: `sk-...`

这表示 Codex 配置里的 `env_key` 被错误填成了真实 Key。重新运行：

```powershell
.\ai-key-setup.ps1 codex
```

脚本会将该字段修复为 `HYALORIA_API_KEY`，同时保留其他 Codex 配置。随后重启 Codex。

### PowerShell 禁止运行脚本

仅对当前窗口临时放行，不需要管理员权限：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### API Key 有效但找不到模型

先向中转站确认实际模型 ID。若服务可调用但没有实现 `/models`，配置时添加 `--skip-network-check`，再在 Codex 中做一次真实对话测试。

## 开发与测试

项目只使用 Python 标准库：

```powershell
python -m unittest discover -s tests -v
python -m py_compile ai_key_setup.py
```

## License

[MIT License](LICENSE)
