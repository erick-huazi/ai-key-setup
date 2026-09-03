#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$SCRIPT_DIR/ai_key_setup.py"

find_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
                >/dev/null 2>&1 && {
                printf '%s\n' "$candidate"
                return 0
            }
        fi
    done
    return 1
}

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
    echo "错误：需要 Python 3.11 或更高版本。" >&2
    exit 1
fi

exec "$PYTHON" "$CORE" "$@"
