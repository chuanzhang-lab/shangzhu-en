"""switchTask 调用点必须接住 promise —— 浮空调用 = 异常无痕（E-05 护栏）。

背景：app.js 曾有 3 处 `switchTask(id);` 裸调用（promise 被丢弃），切换失败
只在 console 留一个 unhandledrejection，用户界面毫无反应、服务端也无痕。
E-05 统一模板后每个调用点都带 `.catch(e => reportClientError(...))` 或 await。
本护栏静态扫描：带 switchTask( 的调用行不接住即红，防止新代码再犯。
"""
import os
import re

APP_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "web_static", "app.js",
)


def test_no_unawaited_switchtask():
    with open(APP_JS, encoding="utf-8") as f:
        lines = f.read().splitlines()

    offenders = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue  # 注释里提及调用形态不算
        if "switchTask(" not in line:
            continue
        if re.search(r"function\s+switchTask\s*\(", line):
            continue  # 定义行
        if re.search(r"switchTask\(.*?\)\s*\.catch", line) or re.search(r"await\s+switchTask\(", line):
            continue
        offenders.append(f"  app.js:{i}: {stripped[:100]}")

    assert not offenders, (
        "switchTask 调用未接住 promise（应 .catch(reportClientError) 或 await）:\n"
        + "\n".join(offenders)
    )
