# 🔑 ai-key-setup

**一键配置所有 AI 编程 CLI 工具的 API Key**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)](https://github.com/tashfeenahmed/ai-key-setup)
[![Shell](https://img.shields.io/badge/shell-Bash-green)](https://www.gnu.org/software/bash/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

支持工具: **Codex CLI** · **Claude Code** · **Hermes Agent** · **OpenCode** · **Aider**

---

## 🎯 解决什么问题？

每次安装新的 AI 编程工具，都要手动配置 API Key：
- 找到配置文件位置
- 记住环境变量名
- 担心 `.env` 文件被提交到 Git
- 不同工具的配置格式各不相同

**`ai-key-setup` 一键解决所有问题！**

---

## 🚀 快速开始

### 30 秒上手（交互式）

```bash
# 下载脚本
curl -fsSL https://raw.githubusercontent.com/tashfeenahmed/ai-key-setup/main/ai-key-setup.sh -o ai-key-setup.sh

# 运行
bash ai-key-setup.sh
```

脚本会逐个提示你输入 API Key，自动完成所有配置。

### 使用 .env 文件（推荐）

```bash
# 1. 下载模板
curl -fsSL https://raw.githubusercontent.com/tashfeenahmed/ai-key-setup/main/.env.template -o .env

# 2. 编辑 .env，填入你的 API Key
nano .env  # 或用你喜欢的编辑器

# 3. 一键配置
bash ai-key-setup.sh -e .env
```

### 只配置特定工具

```bash
# 只配置 Codex 和 Claude Code
bash ai-key-setup.sh -t codex,claude

# 只配置 Hermes
bash ai-key-setup.sh -t hermes
```

### 验证现有配置

```bash
bash ai-key-setup.sh -v
```

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🔐 **安全输入** | API Key 输入时隐藏显示，不回显到终端 |
| 💾 **.env 支持** | 从文件加载或保存到文件，权限自动设为 600 |
| 🛠️ **多工具** | 一键配置 Codex, Claude Code, Hermes, OpenCode, Aider |
| 🖥️ **多平台** | Windows (Git Bash/WSL), macOS, Linux |
| ✅ **自动验证** | 配置完成后自动验证工具安装和配置状态 |
| 📦 **自动备份** | 修改前自动备份原配置文件 |
| 🎨 **智能默认** | 根据已有 Key 自动选择最佳模型提供商 |
| 🔍 **模拟运行** | `--dry-run` 预览配置变更，不实际写入 |

---

## 📋 各工具配置详情

### Codex CLI

- **配置文件**: `~/.codex/config.toml`
- **支持提供商**: OpenAI, DeepSeek, Z.AI (GLM), Azure, Ollama
- **智能检测**: 自动检测可用 Key 并设置默认提供商

### Claude Code

- **配置文件**: `~/.claude/settings.json`
- **全局规则**: `~/.claude/CLAUDE.md`
- **预配置权限**: git, npm, python 等常用命令

### Hermes Agent

- **配置文件**: `~/.hermes/config.yaml`
- **智能模型选择**: 根据可用 Key 自动选择最佳模型

### OpenCode

- **配置文件**: `~/.config/opencode/config.json`

### Aider

- **配置文件**: `~/.aider/aider.conf.yml`
- **自动检测默认模型**

---

## 📖 完整命令参考

```bash
# 交互式配置（默认）
bash ai-key-setup.sh

# 从 .env 文件加载
bash ai-key-setup.sh -e .env

# 保存配置到 .env 文件
bash ai-key-setup.sh -s .env

# 只配置指定工具（逗号分隔）
bash ai-key-setup.sh -t codex,claude,hermes

# 模拟运行（不写入文件）
bash ai-key-setup.sh --dry-run

# 验证配置
bash ai-key-setup.sh -v

# 显示帮助
bash ai-key-setup.sh -h

# 组合使用
bash ai-key-setup.sh -e .env -t codex,claude --dry-run
```

---

## 🔧 配置后操作

配置完成后，执行以下命令使环境变量生效：

```bash
# Bash
source ~/.bashrc

# Zsh
source ~/.zshrc

# macOS Bash
source ~/.bash_profile
```

然后验证各工具：

```bash
codex --version
claude --version
hermes --version
aider --version
```

---

## 🔑 获取 API Key

| 服务 | 获取地址 | 用途 |
|------|----------|------|
| **OpenAI** | https://platform.openai.com/api-keys | Codex CLI |
| **Anthropic** | https://console.anthropic.com/ | Claude Code |
| **Google Gemini** | https://aistudio.google.com/app/apikey | Gemini 模型 |
| **DeepSeek** | https://platform.deepseek.com/api_keys | DeepSeek 模型 |
| **Z.AI (GLM)** | https://open.bigmodel.cn/ | 智谱 GLM 模型 |
| **GitHub** | https://github.com/settings/tokens | GitHub MCP 工具 |

---

## 🛡️ 安全提示

1. **永远不要**将 `.env` 文件提交到 Git 仓库
2. 脚本会自动设置 `.env` 文件权限为 `600`（仅所有者可读写）
3. 建议定期轮换 API Key
4. 使用 `--dry-run` 先预览配置变更
5. 配置文件修改前自动备份到 `*.backup.YYYYMMDD_HHMMSS`

---

## 🐛 故障排除

### 权限问题

```bash
chmod +x ai-key-setup.sh
```

### 环境变量未生效

```bash
# 检查是否写入成功
cat ~/.bashrc | grep API_KEY

# 手动加载
source ~/.bashrc
```

### 工具未找到

```bash
# Codex CLI
npm install -g @openai/codex

# Claude Code
npm install -g @anthropic-ai/claude-code

# Aider
pip install aider-chat
```

### Windows 用户

确保使用 **Git Bash** 或 **WSL** 运行脚本，PowerShell 和 CMD 不支持。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feat/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feat/amazing-feature`)
5. 打开 Pull Request

---

## 📄 License

本项目采用 [MIT License](LICENSE)。

---

## ⭐ Star History

如果这个项目对你有帮助，请给它一个 Star！

[![Star History Chart](https://api.star-history.com/svg?repos=tashfeenahmed/ai-key-setup&type=Date)](https://star-history.com/#tashfeenahmed/ai-key-setup&Date)

---

<div align="center">

**Made with ❤️ for the AI coding community**

</div>
