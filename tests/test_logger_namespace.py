"""日志命名空间护栏（环境无关，无需服务启动 / 无网络）。

守护的约定：**所有 src 下的 logger 必须挂在 "web.*" 命名空间**。

web_server 把 StreamHandler 与 RotatingFileHandler 装在 logging.getLogger("web") 上。
任何用 logging.getLogger(__name__) 的模块都会得到一个与 "web" 无血缘的孤立 logger，
继承不到 handler → 只走 logging.lastResort 打一行到 stderr。

后果是**日志里查无此项**：典型如 LLM 返回 401（占位 api_key），前端表现为
「点 AI 解读一片空白」，而日志文件干净得像没发生过故障 —— 排查时被完全隐形。

这是已经发生过的真实故障（llm_advisor / op_executor），用 AST 静态扫描钉死不再复发。
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def _getlogger_args(src_root):
    """收集所有 logging.getLogger(...) 的字面量参数 → [(file, lineno, arg)]。"""
    found = []
    for dirpath, _dirs, files in os.walk(src_root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            except SyntaxError:  # 解析失败不该让护栏测试崩，交由编译类测试处理
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_getlogger = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "getLogger"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "logging"
                )
                if not is_getlogger or not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((os.path.relpath(path, ROOT), node.lineno, arg.value))
                else:
                    # 非常量参数（如变量拼接）无法静态判定 → 报出来人工确认
                    found.append((os.path.relpath(path, ROOT), node.lineno, None))
    return found


def test_all_src_loggers_live_under_web_namespace():
    """src 下每个 logger 名都必须以 'web.' 开头，否则其日志不会落盘。"""
    offenders = [
        (f, ln, name)
        for f, ln, name in _getlogger_args(SRC)
        if name is None or not name.startswith("web.")
    ]
    assert not offenders, (
        "以下 logger 不在 'web.*' 命名空间，日志将无法写入 logs/web_server.log：\n"
        + "\n".join(f"  {f}:{ln} -> getLogger({name!r})" for f, ln, name in offenders)
    )


def test_web_namespace_loggers_actually_exist():
    """扫描结果不能为空集 —— 否则上一个断言可能因「什么都没扫到」而假通过。"""
    if "web_server" not in sys.modules:
        sys.path.insert(0, ROOT)
    try:
        import web_server  # noqa: F401  触发日志配置
    except Exception as e:  # pragma: no cover - 环境问题另由其他测试覆盖
        pytest.skip(f"web_server 不可导入: {e}")

    names = {name for _f, _ln, name in _getlogger_args(SRC) if name}
    # 至少应包含已知的核心 logger，证明扫描确实读到了东西而不是空跑
    assert "web" in names or any(n.startswith("web.") for n in names), names


def test_llm_and_op_loggers_inherit_web_handlers():
    """修过的两个模块必须能看到 file handler（regression guard）。"""
    import logging

    sys.path.insert(0, os.path.join(ROOT, "src"))
    try:
        import web_server  # noqa: F401
        import llm_advisor  # noqa: F401
        import op_executor  # noqa: F401
    except Exception as e:  # pragma: no cover
        pytest.skip(f"依赖不可导入: {e}")

    for name in ("web.llm_advisor", "web.op_executor"):
        lg = logging.getLogger(name)
        handlers = []
        node = lg
        while node:
            handlers.extend(node.handlers)
            node = node.parent
        assert handlers, f"{name} 未继承到任何 handler，其日志不会落盘"
