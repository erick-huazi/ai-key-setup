#!/usr/bin/env bash
# ============================================================================
# ai-key-setup — 一键配置 AI CLI 工具 API Key
# ============================================================================
# 支持工具: Codex CLI, Hermes Agent, Claude Code, OpenCode, Aider
# 支持平台: Windows (Git Bash/WSL), macOS, Linux
# 作者: Hermes Agent
# 版本: 1.0.0
# ============================================================================

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# 图标
ICON_CHECK="✅"
ICON_CROSS="❌"
ICON_WARN="⚠️"
ICON_INFO="ℹ️"
ICON_KEY="🔑"
ICON_GEAR="⚙️"
ICON_ROCKET="🚀"

# ============================================================================
# 工具函数
# ============================================================================

log_info()    { echo -e "${BLUE}${ICON_INFO}  $*${NC}"; }
log_success() { echo -e "${GREEN}${ICON_CHECK} $*${NC}"; }
log_warn()    { echo -e "${YELLOW}${ICON_WARN} $*${NC}"; }
log_error()   { echo -e "${RED}${ICON_CROSS} $*${NC}"; }
log_step()    { echo -e "\n${CYAN}${BOLD}▸ $*${NC}"; }
log_key()     { echo -e "${YELLOW}${ICON_KEY} $*${NC}"; }

# 检测操作系统
detect_os() {
    case "$(uname -s)" in
        Linux*)     OS="linux";;
        Darwin*)    OS="macos";;
        CYGWIN*|MINGW*|MSYS*) OS="windows";;
        *)          OS="unknown";;
    esac
    echo "$OS"
}

# 检测 shell 配置文件
detect_shell_rc() {
    local shell_name
    shell_name="$(basename "$SHELL")"
    case "$shell_name" in
        zsh)  echo "$HOME/.zshrc" ;;
        bash)
            if [[ "$OS" == "macos" ]]; then
                echo "$HOME/.bash_profile"
            else
                echo "$HOME/.bashrc"
            fi
            ;;
        *)    echo "$HOME/.profile" ;;
    esac
}

# 安全地读取输入（带默认值）
prompt() {
    local var_name="$1"
    local prompt_text="$2"
    local default="${3:-}"
    local secret="${4:-false}"

    if [[ -n "${!var_name:-}" ]]; then
        # 已通过环境变量设置
        log_info "$prompt_text: 已设置（环境变量）"
        return 0
    fi

    local input
    if [[ "$secret" == "true" ]]; then
        read -rsp "$(echo -e "${YELLOW}${ICON_KEY} ${prompt_text}${NC} ")" input
        echo ""
    else
        if [[ -n "$default" ]]; then
            read -rp "$(echo -e "${YELLOW}${ICON_KEY} ${prompt_text} [${default}]${NC} ")" input
            input="${input:-$default}"
        else
            read -rp "$(echo -e "${YELLOW}${ICON_KEY} ${prompt_text}${NC} ")" input
        fi
    fi

    if [[ -n "$input" ]]; then
        printf -v "$var_name" '%s' "$input"
        return 0
    else
        return 1
    fi
}

# 确认提示
confirm() {
    local prompt_text="$1"
    local default="${2:-n}"

    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$(echo -e "${CYAN}? ${prompt_text} [Y/n]${NC} ")" yn
        yn="${yn:-y}"
    else
        read -rp "$(echo -e "${CYAN}? ${prompt_text} [y/N]${NC} ")" yn
        yn="${yn:-n}"
    fi

    [[ "$yn" =~ ^[Yy]$ ]]
}

# 检查命令是否存在
command_exists() {
    command -v "$1" &>/dev/null
}

# 备份文件
backup_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        local backup="${file}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$file" "$backup"
        log_info "已备份: $backup"
    fi
}

# 写入或更新环境变量到 shell rc
write_to_shell_rc() {
    local var_name="$1"
    local var_value="$2"
    local rc_file="$3"

    # 如果已存在，先删除旧行
    if grep -q "^export ${var_name}=" "$rc_file" 2>/dev/null; then
        # macOS sed 需要 -i ''
        if [[ "$OS" == "macos" ]]; then
            sed -i '' "/^export ${var_name}=/d" "$rc_file"
        else
            sed -i "/^export ${var_name}=/d" "$rc_file"
        fi
    fi

    echo "export ${var_name}=\"${var_value}\"" >> "$rc_file"
}

# ============================================================================
# 配置收集
# ============================================================================

collect_config() {
    log_step "收集 API Key 配置"
    echo ""
    echo "请提供以下 API Key（按 Enter 跳过不需要的）："
    echo ""

    # OpenAI / Codex
    prompt OPENAI_API_KEY "OpenAI API Key (用于 Codex CLI):" "" true || true

    # Anthropic / Claude Code
    prompt ANTHROPIC_API_KEY "Anthropic API Key (用于 Claude Code):" "" true || true

    # Google / Gemini
    prompt GEMINI_API_KEY "Google Gemini API Key:" "" true || true

    # DeepSeek
    prompt DEEPSEEK_API_KEY "DeepSeek API Key:" "" true || true

    # Z.AI / GLM
    prompt ZAI_API_KEY "Z.AI API Key (GLM 模型):" "" true || true

    # GitHub (用于各种 MCP 工具)
    prompt GITHUB_TOKEN "GitHub Personal Access Token:" "" true || true

    # 自定义变量
    echo ""
    if confirm "是否添加自定义环境变量？" "n"; then
        while true; do
            local custom_name custom_value
            read -rp "$(echo -e "${YELLOW}${ICON_KEY} 变量名 (留空结束): ${NC}")" custom_name
            [[ -z "$custom_name" ]] && break
            read -rsp "$(echo -e "${YELLOW}${ICON_KEY} ${custom_name} 的值: ${NC}")" custom_value
            echo ""
            if [[ -n "$custom_value" ]]; then
                CUSTOM_VARS+=("$custom_name=$custom_value")
            fi
        done
    fi
}

# ============================================================================
# 配置写入
# ============================================================================

write_configs() {
    local rc_file
    rc_file="$(detect_shell_rc)"
    OS="$(detect_os)"

    log_step "写入配置到 $rc_file"
    backup_file "$rc_file"

    local written=0

    # 写入标准变量
    local var
    for var in OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY ZAI_API_KEY GITHUB_TOKEN; do
        if [[ -n "${!var:-}" ]]; then
            write_to_shell_rc "$var" "${!var}" "$rc_file"
            log_success "已设置 $var"
            ((written++))
        fi
    done

    # 写入自定义变量
    for pair in "${CUSTOM_VARS[@]:-}"; do
        if [[ -n "$pair" ]]; then
            local name="${pair%%=*}"
            local value="${pair#*=}"
            write_to_shell_rc "$name" "$value" "$rc_file"
            log_success "已设置 $name"
            ((written++))
        fi
    done

    if [[ $written -eq 0 ]]; then
        log_warn "没有写入任何配置"
        return 1
    fi

    log_success "共写入 $written 个环境变量到 $rc_file"
}

# ============================================================================
# 工具特定配置
# ============================================================================

configure_codex() {
    log_step "配置 Codex CLI"

    local codex_dir="$HOME/.codex"
    local codex_config="$codex_dir/config.toml"

    if [[ ! -d "$codex_dir" ]]; then
        mkdir -p "$codex_dir"
        log_info "创建目录: $codex_dir"
    fi

    backup_file "$codex_config"

    # 检测模型提供商
    local model_provider="openai"
    local model="gpt-4o"
    local base_url=""

    if [[ -n "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
        model_provider="deepseek"
        model="deepseek-chat"
        base_url="https://api.deepseek.com/v1"
        log_info "检测到 DeepSeek Key，配置为默认提供商"
    elif [[ -n "${ZAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
        model_provider="zai"
        model="glm-4.6"
        base_url="https://open.bigmodel.cn/api/paas/v4"
        log_info "检测到 Z.AI Key，配置为默认提供商"
    fi

    cat > "$codex_config" << EOF
# Codex CLI 配置文件
# 由 ai-key-setup 生成于 $(date)

# 模型提供商: openai | deepseek | zai | azure | ollama
model_provider = "${model_provider}"

# 默认模型
model = "${model}"

# API 基础 URL（如使用第三方服务）
$(if [[ -n "$base_url" ]]; then echo "base_url = \"${base_url}\""; fi)

# 审批模式: suggest | auto-edit | full-auto
approval_mode = "suggest"

# 禁用遥测
disable_telemetry = true

# 环境变量引用（从 shell 环境读取）
[env]
$(if [[ -n "${OPENAI_API_KEY:-}" ]]; then echo "OPENAI_API_KEY = \"${OPENAI_API_KEY}\""; fi)
$(if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then echo "DEEPSEEK_API_KEY = \"${DEEPSEEK_API_KEY}\""; fi)
$(if [[ -n "${ZAI_API_KEY:-}" ]]; then echo "ZAI_API_KEY = \"${ZAI_API_KEY}\""; fi)

# 多提供商配置
[model_providers]

[model_providers.openai]
name = "OpenAI"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"

$(if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then cat << 'EOT'
[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"
env_key = "DEEPSEEK_API_KEY"
EOT
fi)

$(if [[ -n "${ZAI_API_KEY:-}" ]]; then cat << 'EOT'
[model_providers.zai]
name = "Z.AI (GLM)"
base_url = "https://open.bigmodel.cn/api/paas/v4"
env_key = "ZAI_API_KEY"
EOT
fi)
EOF

    log_success "Codex 配置已写入: $codex_config"
}

configure_claude_code() {
    log_step "配置 Claude Code"

    local claude_dir="$HOME/.claude"
    local claude_settings="$claude_dir/settings.json"

    if [[ ! -d "$claude_dir" ]]; then
        mkdir -p "$claude_dir"
        log_info "创建目录: $claude_dir"
    fi

    backup_file "$claude_settings"

    # 构建权限配置
    local permissions_json='{"allow":["Bash(git *)","Bash(npm *)","Bash(python *)","Bash(pip *)","Read","Edit","WebSearch"],"ask":["Write","Bash(rm *)"],"deny":["Read(.env*)"]}'

    cat > "$claude_settings" << EOF
{
  "permissions": ${permissions_json},
  "alwaysThinkingEnabled": true,
  "promptSuggestionEnabled": true,
  "autoCompactEnabled": true,
  "model": "claude-sonnet-4-6"
}
EOF

    log_success "Claude Code 配置已写入: $claude_settings"

    # 创建 CLAUDE.md 模板
    if [[ ! -f "$claude_dir/CLAUDE.md" ]]; then
        cat > "$claude_dir/CLAUDE.md" << 'EOF'
# 全局规则

## 代码风格
- 使用中文回复，技术术语附英文
- 代码添加中文注释
- 遵循项目现有代码风格

## 安全规则
- 不提交 .env 文件
- 不硬编码 API Key
- 敏感操作先询问
EOF
        log_success "Claude Code 全局规则已创建: $claude_dir/CLAUDE.md"
    fi
}

configure_hermes() {
    log_step "配置 Hermes Agent"

    local hermes_dir="$HOME/.hermes"
    local hermes_config="$hermes_dir/config.yaml"

    if [[ ! -d "$hermes_dir" ]]; then
        mkdir -p "$hermes_dir"
        log_info "创建目录: $hermes_dir"
    fi

    backup_file "$hermes_config"

    # 检测可用模型
    local default_model="openai/gpt-4o"
    if [[ -n "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
        default_model="deepseek/deepseek-chat"
    elif [[ -n "${ZAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
        default_model="zai/glm-4.6"
    elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        default_model="anthropic/claude-sonnet-4-6"
    fi

    cat > "$hermes_config" << EOF
# Hermes Agent 配置
# 由 ai-key-setup 生成于 $(date)

model:
  provider: "$(echo "$default_model" | cut -d/ -f1)"
  model: "$(echo "$default_model" | cut -d/ -f2-)"

# 网关配置
gateway:
  enabled: false
  port: 8080

# 日志
logging:
  level: info
  file: ~/.hermes/logs/hermes.log

# 工具配置
tools:
  terminal:
    timeout: 300
  web:
    enabled: true

# 环境变量（从 shell 继承，此处可覆盖）
env:
$(if [[ -n "${OPENAI_API_KEY:-}" ]]; then echo "  OPENAI_API_KEY: \"${OPENAI_API_KEY}\""; fi)
$(if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then echo "  ANTHROPIC_API_KEY: \"${ANTHROPIC_API_KEY}\""; fi)
$(if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then echo "  DEEPSEEK_API_KEY: \"${DEEPSEEK_API_KEY}\""; fi)
$(if [[ -n "${ZAI_API_KEY:-}" ]]; then echo "  ZAI_API_KEY: \"${ZAI_API_KEY}\""; fi)
$(if [[ -n "${GEMINI_API_KEY:-}" ]]; then echo "  GEMINI_API_KEY: \"${GEMINI_API_KEY}\""; fi)
$(if [[ -n "${GITHUB_TOKEN:-}" ]]; then echo "  GITHUB_TOKEN: \"${GITHUB_TOKEN}\""; fi)
EOF

    log_success "Hermes 配置已写入: $hermes_config"
}

configure_opencode() {
    log_step "配置 OpenCode"

    local opencode_dir="$HOME/.config/opencode"
    local opencode_config="$opencode_dir/config.json"

    if [[ ! -d "$opencode_dir" ]]; then
        mkdir -p "$opencode_dir"
        log_info "创建目录: $opencode_dir"
    fi

    backup_file "$opencode_config"

    cat > "$opencode_config" << EOF
{
  "model": "openai/gpt-4o",
  "theme": "system",
  "autoupdate": true
}
EOF

    log_success "OpenCode 配置已写入: $opencode_config"
}

configure_aider() {
    log_step "配置 Aider"

    local aider_dir="$HOME/.aider"
    local aider_config="$aider_dir/aider.conf.yml"

    if [[ ! -d "$aider_dir" ]]; then
        mkdir -p "$aider_dir"
        log_info "创建目录: $aider_dir"
    fi

    backup_file "$aider_config"

    # 检测默认模型
    local default_model="gpt-4o"
    if [[ -n "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
        default_model="deepseek/deepseek-chat"
    elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        default_model="sonnet"
    fi

    cat > "$aider_config" << EOF
# Aider 配置
# 由 ai-key-setup 生成于 $(date)

# 默认模型
model: ${default_model}

# API Key 从环境变量读取
# OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY 等

# 编辑器
editor: code

# 自动提交
auto-commits: true

# 黑暗模式
dark-mode: true

# 显示 diff
show-diffs: true
EOF

    log_success "Aider 配置已写入: $aider_config"
}

# ============================================================================
# 验证
# ============================================================================

verify_setup() {
    log_step "验证配置"

    local all_ok=true

    # 检查环境变量
    echo ""
    log_info "环境变量检查:"
    for var in OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY ZAI_API_KEY GITHUB_TOKEN; do
        if [[ -n "${!var:-}" ]]; then
            local masked="${!var:0:8}..."
            log_success "$var = $masked"
        else
            log_warn "$var 未设置"
        fi
    done

    # 检查工具是否安装
    echo ""
    log_info "工具安装检查:"

    if command_exists codex; then
        log_success "Codex CLI: $(codex --version 2>&1 | head -1)"
    else
        log_warn "Codex CLI 未安装 (npm install -g @openai/codex)"
    fi

    if command_exists claude; then
        log_success "Claude Code: $(claude --version 2>&1 | head -1)"
    else
        log_warn "Claude Code 未安装 (npm install -g @anthropic-ai/claude-code)"
    fi

    if command_exists hermes; then
        log_success "Hermes Agent: $(hermes --version 2>&1 | head -1 || echo 'installed')"
    else
        log_warn "Hermes Agent 未安装或未在 PATH 中"
    fi

    if command_exists opencode; then
        log_success "OpenCode: $(opencode --version 2>&1 | head -1)"
    else
        log_warn "OpenCode 未安装"
    fi

    if command_exists aider; then
        log_success "Aider: $(aider --version 2>&1 | head -1)"
    else
        log_warn "Aider 未安装 (pip install aider-chat)"
    fi

    # 检查配置文件
    echo ""
    log_info "配置文件检查:"
    for f in "$HOME/.codex/config.toml" "$HOME/.claude/settings.json" "$HOME/.hermes/config.yaml" "$HOME/.config/opencode/config.json" "$HOME/.aider/aider.conf.yml"; do
        if [[ -f "$f" ]]; then
            log_success "存在: $f"
        else
            log_warn "缺失: $f"
        fi
    done
}

# ============================================================================
# .env 文件支持
# ============================================================================

load_env_file() {
    local env_file="$1"
    if [[ -f "$env_file" ]]; then
        log_info "加载 .env 文件: $env_file"
        # 导出所有变量
        set -a
        # shellcheck source=/dev/null
        source "$env_file"
        set +a
        return 0
    else
        log_error ".env 文件不存在: $env_file"
        return 1
    fi
}

save_env_file() {
    local env_file="$1"
    log_info "保存到 .env 文件: $env_file"

    cat > "$env_file" << EOF
# AI CLI API Keys
# 由 ai-key-setup 生成于 $(date)
# 警告: 请勿提交到 Git 仓库

$(if [[ -n "${OPENAI_API_KEY:-}" ]]; then echo "OPENAI_API_KEY=\"${OPENAI_API_KEY}\""; fi)
$(if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then echo "ANTHROPIC_API_KEY=\"${ANTHROPIC_API_KEY}\""; fi)
$(if [[ -n "${GEMINI_API_KEY:-}" ]]; then echo "GEMINI_API_KEY=\"${GEMINI_API_KEY}\""; fi)
$(if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then echo "DEEPSEEK_API_KEY=\"${DEEPSEEK_API_KEY}\""; fi)
$(if [[ -n "${ZAI_API_KEY:-}" ]]; then echo "ZAI_API_KEY=\"${ZAI_API_KEY}\""; fi)
$(if [[ -n "${GITHUB_TOKEN:-}" ]]; then echo "GITHUB_TOKEN=\"${GITHUB_TOKEN}\""; fi)

# 自定义变量
$(for pair in "${CUSTOM_VARS[@]:-}"; do
    if [[ -n "$pair" ]]; then
        echo "${pair%%=*}=\"${pair#*=}\""
    fi
done)
EOF

    chmod 600 "$env_file"
    log_success "已保存（权限 600）: $env_file"
}

# ============================================================================
# 主函数
# ============================================================================

show_banner() {
    echo -e "${CYAN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║   🔑  AI CLI Key Setup — 一键配置所有 AI 编程工具            ║"
    echo "║                                                              ║"
    echo "║   支持: Codex CLI · Claude Code · Hermes · OpenCode · Aider  ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

show_usage() {
    cat << EOF
用法: $(basename "$0") [选项]

选项:
  -h, --help          显示帮助
  -i, --interactive   交互式配置（默认）
  -e, --env FILE      从 .env 文件加载
  -s, --save FILE     保存配置到 .env 文件
  -t, --tools LIST    只配置指定工具（逗号分隔: codex,claude,hermes,opencode,aider）
  -v, --verify        仅验证现有配置
  --dry-run           模拟运行，不写入文件

示例:
  $(basename "$0")                    # 交互式配置
  $(basename "$0") -e .env            # 从 .env 加载并配置
  $(basename "$0") -t codex,claude    # 只配置 Codex 和 Claude Code
  $(basename "$0") -v                 # 验证配置

EOF
}

main() {
    local interactive=true
    local env_file=""
    local save_env=""
    local tools="codex,claude,hermes,opencode,aider"
    local verify_only=false
    local dry_run=false

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_usage
                exit 0
                ;;
            -i|--interactive)
                interactive=true
                shift
                ;;
            -e|--env)
                env_file="$2"
                shift 2
                ;;
            -s|--save)
                save_env="$2"
                shift 2
                ;;
            -t|--tools)
                tools="$2"
                shift 2
                ;;
            -v|--verify)
                verify_only=true
                shift
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            *)
                log_error "未知选项: $1"
                show_usage
                exit 1
                ;;
        esac
    done

    show_banner

    # 仅验证模式
    if [[ "$verify_only" == "true" ]]; then
        verify_setup
        exit 0
    fi

    # 加载 .env 文件
    if [[ -n "$env_file" ]]; then
        load_env_file "$env_file"
    fi

    # 交互式收集
    if [[ "$interactive" == "true" && -z "$env_file" ]]; then
        collect_config
    fi

    # 保存到 .env
    if [[ -n "$save_env" ]]; then
        save_env_file "$save_env"
    fi

    # 写入 shell rc
    if [[ "$dry_run" == "false" ]]; then
        write_configs
    else
        log_warn "Dry-run 模式，跳过写入 shell rc"
    fi

    # 配置各工具
    local tool_list
    IFS=',' read -ra tool_list <<< "$tools"

    for tool in "${tool_list[@]}"; do
        tool="$(echo "$tool" | xargs)"  # trim whitespace
        case "$tool" in
            codex)
                if [[ "$dry_run" == "false" ]]; then configure_codex; fi
                ;;
            claude|claude-code)
                if [[ "$dry_run" == "false" ]]; then configure_claude_code; fi
                ;;
            hermes)
                if [[ "$dry_run" == "false" ]]; then configure_hermes; fi
                ;;
            opencode)
                if [[ "$dry_run" == "false" ]]; then configure_opencode; fi
                ;;
            aider)
                if [[ "$dry_run" == "false" ]]; then configure_aider; fi
                ;;
            *)
                log_warn "未知工具: $tool"
                ;;
        esac
    done

    # 验证
    echo ""
    verify_setup

    # 完成
    echo ""
    log_step "配置完成！"
    echo ""
    echo "下一步:"
    echo "  1. 重新加载 shell 配置: source $(detect_shell_rc)"
    echo "  2. 验证各工具是否正常工作"
    echo ""
    echo "快速测试命令:"
    echo "  codex --version"
    echo "  claude --version"
    echo "  hermes --version"
    echo ""
    log_success "Happy coding! 🚀"
}

# 声明数组
declare -a CUSTOM_VARS=()

# 运行
main "$@"
