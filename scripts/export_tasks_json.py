#!/usr/bin/env python3
"""把 SQLite 里的任务导出成 JSON（薄壳，实现见 scripts/db_tool.py export）。

为什么要有它：从 PG 换到 SQLite 是**单向门**——数据进了 SQLite 就没有
「再换回去」的自动通道。这个脚本是那道门的回退路径：任何时候想把数据
搬到别处（另一个后端、中文仓、或者单纯人工核对），先跑它拿到一份
与后端无关的 JSON。

用法：
    .venv/bin/python3 scripts/export_tasks_json.py                  # 默认库
    .venv/bin/python3 scripts/export_tasks_json.py -o /tmp/all.json # 指定输出
    SHANGZHU_DB_PATH=/path/to/x.db .venv/bin/python3 scripts/export_tasks_json.py

导出的是**全量**（含已软删的任务），不是只导存活的——软删也是数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_tool  # noqa: E402


if __name__ == "__main__":
    sys.exit(db_tool.main(["export"] + sys.argv[1:]))
