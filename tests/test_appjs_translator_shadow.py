"""app.js 翻译函数遮蔽护栏（静态扫描，不执行 JS）。

背景（2026-10-01 真实故障）：app.js 做 i18n 迁移后全局翻译函数叫 `t`，而
任务管理代码里任务对象参数也叫 `t`——`tasks.forEach(t => ...)` / `taskMenu(t, div)`
等处参数遮蔽翻译函数，`t('ui.rename_title')` 变成「拿任务对象当函数调」→
TypeError → 被 catch 吞掉 → 前端弹「任务列表加载失败」，且改名/删除/新建失败
提示全部静默损坏。一处修了别处还在，同类共 5 处。

本测试静态扫描 app.js：凡是函数参数（或 let/const 声明）名为 `t` 的作用域里
出现 `t('...')` 调用即报错。新增任务相关代码时参数请用 `task`，别用 `t`。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, "src", "web_static", "app.js")

_FUNC_RE = re.compile(r"^\s*(?:async\s+)?function\s+\w+\s*\(([^)]*)\)")
_CALL_RE = re.compile(r"\bt\(\s*['\"]")


def _function_blocks(lines):
    """产出 (start_idx, param_names, body_line_idxs)。按花括号配对截块。"""
    for i, line in enumerate(lines):
        m = _FUNC_RE.match(line)
        if not m:
            continue
        params = [p.strip() for p in m.group(1).split(",") if p.strip()]
        depth, started, body = 0, False, []
        for j in range(i, len(lines)):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
            body.append(j)
            if started and depth <= 0:
                break
        yield i, params, body


def test_no_translator_shadowing_in_app_js():
    with open(APP_JS, encoding="utf-8") as f:
        lines = f.read().split("\n")
    offenders = []
    for start, params, body in _function_blocks(lines):
        if "t" not in params:
            continue
        calls = [n + 1 for n in body if _CALL_RE.search(lines[n])]
        if calls:
            offenders.append({"function": lines[start].strip()[:60], "t('…') 调用行": calls})
    assert not offenders, (
        "app.js 存在翻译函数遮蔽：参数名 t 遮蔽了全局 i18n 翻译函数 t，"
        f"作用域内的 t('…') 会拿任务对象当函数调（TypeError）。{offenders}。"
        "修复：把任务对象参数改名为 task。"
    )
