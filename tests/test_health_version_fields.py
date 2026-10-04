"""/health 版本观测位护栏（E-07）：commit 与真值源同源，static_ver 语义不变。

背景（E-07）：/health 有 static_ver 但**无 commit**，部署机排查「线上跑的是哪次
提交」只能登机器翻 git log。本文件钉住观测位：
- `commit` 必须等于 `git rev-parse --short HEAD`（git 不可用时显式 "unknown"，
  缺失不冒充——不许编一个看起来正常的假 hash）；
- `static_ver` / `version` 语义回归（加 commit 时不得顺手改坏既有字段）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from fastapi.testclient import TestClient  # noqa: E402

# 与 test_static_cache_guard 同法：拦掉 TestClient 触发的 PG 探测
os.environ.pop("PGDATABASE_URL", None)


def _client():
    import web_server as ws
    orig = ws.get_store
    ws.get_store = lambda: type("S", (), {"close": lambda: None})()
    try:
        return TestClient(ws.app)
    finally:
        ws.get_store = orig


def test_health_commit_matches_git():
    """commit 观测位必须与 git 同源，且进程内缓存（两次取值一致）。"""
    import web_server as ws
    body = _client().get("/health").json()
    assert "commit" in body, "/health 缺 commit 字段：线上是哪次提交无从查证"
    try:
        expected = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, stderr=subprocess.DEVNULL, timeout=5,
        ).decode("utf-8", "replace").strip() or "unknown"
    except Exception:
        expected = "unknown"
    assert body["commit"] == expected, (
        f"/health.commit={body['commit']!r} 与 git rev-parse={expected!r} 不一致："
        "观测位与真值源漂移，排查会被带到沟里"
    )
    # 进程内缓存语义：再取一次必须同值（防退化成「每次请求都起子进程」）
    assert ws._git_commit() == body["commit"]


def test_health_static_ver_and_version_unchanged():
    """回归：加 commit 不得动到 static_ver / version 的既有语义。"""
    import web_server as ws
    body = _client().get("/health").json()
    assert body["static_ver"] == ws._static_ver("app.js"), \
        "static_ver 被改坏：必须始终等于 _static_ver('app.js')（页面 ?v= 同源）"
    assert body["version"] == ws.APP_VERSION, "version 被改坏：必须来自 pyproject 单源"
