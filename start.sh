#!/bin/bash
# 创业者工作台 — 启动脚本
# 优先用项目 .venv，缺失时回退到 SHANGZHU_FALLBACK_PY 环境变量指定的解释器
# 端口: 8081
# 用法: ./start.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 优先使用项目自带 .venv（uv sync 生成，含全部依赖）。
# 备用解释器通过环境变量 SHANGZHU_FALLBACK_PY 指定——不把个人绝对路径写死在仓库里，
# 否则克隆到其他机器时会指向一个不存在的路径（一键克隆的可移植性要求）。
VENV_PY="$SCRIPT_DIR/.venv/bin/python3"
if [ ! -x "$VENV_PY" ] && [ -n "${SHANGZHU_FALLBACK_PY:-}" ] && [ -x "${SHANGZHU_FALLBACK_PY:-}" ]; then
    VENV_PY="$SHANGZHU_FALLBACK_PY"
fi
LOG="$SCRIPT_DIR/logs/shangzhu.log"
PORT="${PORT:-8081}"

# ── 本分支是**英文版**：部署级默认语言 = en ──────────────────────────────────
# 语言是部署级 profile（不在运行时切，见 docs/PLAN_I18N_EN.md §5.3）。
# 为什么设在这里、而不是写在 web_server.py 或改 i18n.DEFAULT_LOCALE：
# - 写进 web_server.py 会污染测试——多个测试（test_task_api / test_real_dialogs /
#   test_phase4_workbench 等）会 import web_server，一旦它在 import 时把
#   SHANGZHU_LOCALE 设成 en，整个 pytest 进程的中文断言全部跑成英文。
# - 改代码里的 DEFAULT_LOCALE 会同时改掉 t() 的回退目标与上千条中文回归断言
#   （那些断言正是"引擎行为未变"的 oracle，不能顺手废掉）。
# start.sh 只由人工启动执行、测试从不运行它，是唯一干净的位置。
# 想跑中文版：SHANGZHU_LOCALE=zh ./start.sh
export SHANGZHU_LOCALE="${SHANGZHU_LOCALE:-en}"

mkdir -p logs output

if [ ! -x "$VENV_PY" ]; then
    echo "[start] 错误：未找到可用的 venv Python: $VENV_PY" >&2
    echo "[start] 请先运行 ./setup.sh 初始化环境（会自动 uv sync 生成 .venv）" >&2
    exit 1
fi

# 快速导入冒烟
if ! "$VENV_PY" -c "from web_server import app; assert app is not None" 2>/tmp/shangzhu_start_import.err; then
    echo "[start] 错误：web_server 导入失败，详见 /tmp/shangzhu_start_import.err"
    cat /tmp/shangzhu_start_import.err >&2 || true
    exit 1
fi

echo "[shangzhu] 启动中... (端口 $PORT, 日志: $LOG)"
exec "$VENV_PY" -c "
import uvicorn
from web_server import app
uvicorn.run(app, host='127.0.0.1', port=$PORT, log_level='warning')
" >> "$LOG" 2>&1
